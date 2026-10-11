"""乐谱工作室：MusicXML 五线谱可视化 + MIDI 播放 + 音频可视化 + 报告 UI
- 五线谱从 MusicXML 加载显示，不生成 PNG
- MIDI 模拟播放，可导出 MusicXML / MIDI / WAV
- 读取音频后现算 Mel / 波形 / STFT / 色度 / 能量包络（不保存 PNG）
- 分析报告写入 md，并在界面标签页显示
"""
import os
import sys
import time
import json
import re
import uuid
import threading
import tempfile
import warnings
import wave
from array import array
from datetime import datetime
from typing import Any, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- FluidSynth DLL 兼容（fluidsynth.py 包写死了不存在的 C:\tools\fluidsynth\bin）---
_ROOT = os.path.dirname(os.path.abspath(__file__))
FLUIDSYNTH_BIN = os.path.join(_ROOT, 'fluidsynth', 'bin')
os.environ['PATH'] = FLUIDSYNTH_BIN + os.pathsep + os.environ['PATH']
if sys.platform == 'darwin':
    # macOS: brew 安装的 libfluidsynth 不在默认 dyld 搜索路径里
    _dyld = os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', '')
    for _p in ('/opt/homebrew/lib', '/usr/local/lib'):
        if os.path.isdir(_p) and _p not in _dyld:
            _dyld = (_dyld + os.pathsep if _dyld else '') + _p
    os.environ['DYLD_FALLBACK_LIBRARY_PATH'] = _dyld
_orig_add_dll_directory = getattr(os, 'add_dll_directory', None)
if _orig_add_dll_directory is not None:
    def _safe_add_dll_directory(path):
        try:
            return _orig_add_dll_directory(path)
        except (FileNotFoundError, OSError):
            return None
    os.add_dll_directory = _safe_add_dll_directory

warnings.filterwarnings('ignore', category=DeprecationWarning)

import numpy as np
import sounddevice as sd
import pretty_midi


# ---- sounddevice 播放保活（防 SIGSEGV）-----------------------------------
# sd.play() 会立刻替换 sd._last_callback：旧流对象引用计数归零 →
# 其 CFFI 回调闭包被同步释放；而 CoreAudio 渲染线程可能还有一次在飞的
# IOProc，踩到已释放闭包 → 段错误（崩溃栈特征：libffi_closure_SYSV →
# AdaptingOutputOnlyProcess → AudioIOProc）。
# 修复：替换前先把旧流摘进保活队列，延迟若干次播放后再允许 GC。
_sd_retired_streams = []


def _sd_play_safe(data, sr):
    old = getattr(sd, '_last_callback', None)
    if old is not None and getattr(old, 'stream', None) is not None:
        _sd_retired_streams.append(old.stream)
        # 只保活最近 6 个旧流；更早的流早已无在飞回调，正常释放
        del _sd_retired_streams[:-6]
    sd.play(data, sr)


from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QElapsedTimer, QObject, QEvent
from PyQt5.QtGui import (QPainter, QFont, QKeySequence, QTextCursor, QColor,
                         QPen, QBrush, QCursor, QPixmap, QIcon)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QDockWidget, QGraphicsScene, QGraphicsView, QTabWidget, QLabel,
    QPushButton, QMessageBox, QFileDialog, QAction, QTextBrowser,
    QComboBox, QTextEdit, QLineEdit, QSplitter, QPlainTextEdit,
    QSizePolicy, QShortcut, QListWidget, QListWidgetItem, QAbstractItemView,
    QSlider, QFrame, QScrollArea, QActionGroup,
)
import i18n

from composition_skill_ui import SkillLibraryDialog
from studio_daw import (PianoRollView,
                        ArrangementView, TransportExtras)

from music21 import stream, note as m21note, chord as m21chord
from music21 import key, meter, tempo, instrument, clef as m21clef, metadata as m21meta
from music21 import converter as m21converter

from music_score.models import (
    Score, Staff, Measure, Note, Pitch,
    ClefType, NoteName, Accidental, NoteDuration, TimeSignature, KeySignature
)
from music_score.renderer import ScoreRenderer, STAFF_HEIGHT, STAFF_LINE_SPACING
from music_score.interaction import (
    staff_position_to_pitch, snap_to_nearest_line, insert_index_for_x,
)
from music_score.editor_core import EditController, pitch_label
from music_score.editor_ui import ScoreEditPalette, ScorePalettePanel, SuggestionBar
from music_score.exporter import model_to_m21
from music_score.assist_engine import blank_score, INSPIRATION_PROFILES


class _AssistJudgeBridge(QObject):
    """后台听审线程 → UI 线程。"""
    finished = pyqtSignal(int, int, str)  # gen_id, best_idx, reason

import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# 中文字体：按平台选第一个存在的（macOS 没有"微软雅黑"，用苹方避免 Qt 字体别名填充开销）
if sys.platform == 'darwin':
    _UI_FONT_FAMILY = 'PingFang SC'
else:
    _UI_FONT_FAMILY = 'Microsoft YaHei'
plt.rcParams['font.sans-serif'] = [_UI_FONT_FAMILY, 'PingFang SC',
                                   'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

SOUNDFONT_CANDIDATES = [
    # 当前默认：Timbres of Heaven 4.0（最细腻/最丰满，419MB，4 库综合对比胜出）
    os.path.join('soundfonts', 'Timbres_of_Heaven.sf2'),
    # 历史/回退默认：GeneralUser GS（免费 GM，混音口碑佳，轻量）
    os.path.join('soundfonts', 'GeneralUser-GS.sf2'),
    # 历史默认 / 回退：MuseScore_General.sf3（旧 headless 产物音色）
    os.path.join('soundfonts', 'MuseScore_General.sf3'),
    # 其它免费高质量 GM 库（可选）
    os.path.join('soundfonts', 'FluidR3_GM.sf2'),
    # 历史占位（通常不存在，自动跳过）
    os.path.join('soundfonts', 'MuseScore_General.sf2'),
    '/System/Library/Components/CoreAudio.component/Contents/Resources/gs_instruments.dls',  # macOS 自带 GM
]
def _pick_soundfont():
    for p in SOUNDFONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None
SOUNDFONT = _pick_soundfont() or os.path.join('soundfonts', 'Timbres_of_Heaven.sf2')
# 默认打开：巴赫经典；原创管弦乐章另存
BACH_MUSICXML_PATH = os.path.join('scores', 'bach_wtc1_prelude_c.xml')
BACH846_XML = os.path.join('scores', 'bach_bwv846_prelude_fugue.xml')
BACH846_MIDI = os.path.join('scores', 'bach_bwv846_prelude_fugue.mid')
BACH846_WAV = os.path.join('scores', 'bach_bwv846_prelude_fugue.wav')

# ---------------------------------------------------------------------------
# Markdown -> HTML（供应用内帮助/报告视图与外部文档打开共用）
# 优先使用标准 markdown 库（已内置 fenced_code/tables/sane_lists 扩展），
# 库缺失时回退到内置弱解析器，保证任何环境都能渲染而非显示原始 markdown。
# ---------------------------------------------------------------------------
_MD_CSS = """
body{font-family:'Microsoft YaHei','PingFang SC',Segoe UI,sans-serif;
  font-size:13px;line-height:1.6;color:#1f2430;margin:12px 16px;}
h1{font-size:22px;border-bottom:2px solid #4b3aa8;padding-bottom:6px;margin:14px 0 10px;}
h2{font-size:18px;color:#4b3aa8;margin:16px 0 8px;}
h3{font-size:15px;color:#2c2c3a;margin:12px 0 6px;}
p{margin:6px 0;}
code{background:#f1f2f6;border-radius:4px;padding:1px 5px;
  font-family:Consolas,'Courier New',monospace;font-size:12px;color:#c0341d;}
pre{background:#f6f7fb;border:1px solid #e2e4ec;border-radius:6px;
  padding:10px 12px;overflow:auto;margin:8px 0;}
pre code{background:transparent;padding:0;color:#1f2430;display:block;white-space:pre;}
blockquote{background:#f3f1fb;border-left:4px solid #8a7be0;
  margin:8px 0;padding:6px 12px;color:#4a4760;}
table{border-collapse:collapse;margin:10px 0;font-size:12px;}
th,td{border:1px solid #cfd3e0;padding:5px 9px;text-align:left;vertical-align:top;}
th{background:#eeeafb;font-weight:600;}
ul,ol{margin:6px 0 6px 22px;padding:0;}
li{margin:2px 0;}
hr{border:none;border-top:1px solid #d8dae6;margin:14px 0;}
a{color:#2f6fde;}
"""

def render_markdown_to_html(md: str, title: str = '') -> str:
    """把 markdown 文本转换成带样式的完整 HTML 文档字符串。

    优先使用标准 markdown 库（已内置 fenced_code/tables/sane_lists 扩展）；
    库缺失时回退到内置弱解析器。两者都会先清理 Qt 富文本不支持的
    <details>/<summary> 标签与 HTML 注释，避免其内部 markdown（如 **加粗**）
    被当成原始文本显示。
    """
    # 清理 Qt 富文本不支持的标签：<details>/<summary> 与 HTML 注释。
    # 否则 Qt 富文本解析器遇到这些未知块级标签会"降级"为纯文本，
    # 导致内部 **加粗** 等 markdown 标记原样暴露（表现即"后半部分未渲染"）。
    _clean = re.sub(r'</?(?:details|summary)[^>]*>', '', md or '',
                    flags=re.IGNORECASE)
    _clean = re.sub(r'<!--.*?-->', '', _clean, flags=re.DOTALL)
    body = None
    try:
        import markdown as _md
        body = _md.markdown(
            _clean, extensions=['fenced_code', 'tables', 'sane_lists'])
    except Exception:
        body = None
    if body is None:
        # 回退：复用 StudioWindow 内置弱解析器（仅当 markdown 库不可用）
        try:
            body = StudioWindow._markdown_to_html(_clean)
            # _markdown_to_html 已含 <html> 外壳，避免重复包裹
            if body.lstrip().lower().startswith('<html'):
                return body
        except Exception:
            body = f'<pre>{_clean}</pre>'
    head_title = f'<title>{title}</title>' if title else ''
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        + head_title + '<style>' + _MD_CSS + '</style></head><body>'
        + body + '</body></html>'
    )

def _ensure_html_for_md(md_path: str):
    """把 .md 文件转成同目录同名 .html 并返回其路径；失败返回 None。"""
    try:
        with open(md_path, encoding='utf-8') as f:
            md = f.read()
        html = render_markdown_to_html(
            md, title=os.path.basename(md_path))
        html_path = os.path.splitext(md_path)[0] + '.html'
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        return html_path
    except Exception:
        return None

BACH846_REPORT = os.path.join('scores', 'bach_bwv846_prelude_fugue_report.md')
BACH_MIDI_PATH = os.path.join('scores', 'bach_wtc1_prelude_c.mid')
BACH_WAV_PATH = os.path.join('scores', 'bach_wtc1_prelude_c.wav')
BACH_REPORT_PATH = os.path.join('scores', 'bach_wtc1_prelude_c_report.md')
PIRATE_MUSICXML_PATH = os.path.join('scores', 'pirate_storm_allegro.xml')
PIRATE_MIDI_PATH = os.path.join('scores', 'pirate_storm_allegro.mid')
PIRATE_WAV_PATH = os.path.join('scores', 'pirate_storm_allegro.wav')
PIRATE_REPORT_PATH = os.path.join('scores', 'pirate_storm_allegro_report.md')
REPORT_PATH = os.path.join('scores', 'g_major_allegro_report.md')
MUSICXML_PATH = os.path.join('scores', 'g_major_allegro_movement.xml')
MIDI_PATH = os.path.join('scores', 'g_major_allegro_movement.mid')
WAV_PATH = os.path.join('scores', 'g_major_allegro_movement.wav')
LLM_MUSICXML_PATH = os.path.join('scores', 'llm_compose.xml')
LLM_MIDI_PATH = os.path.join('scores', 'llm_compose.mid')
LLM_WAV_PATH = os.path.join('scores', 'llm_compose.wav')
LLM_REPORT_PATH = os.path.join('scores', 'llm_compose_report.md')
CHAT_DIR = os.path.join('scores', 'chats')
CHAT_INDEX_PATH = os.path.join(CHAT_DIR, 'index.json')
SCORE_WIDTH = 1200
SAMPLE_RATE = 44100
NOTE_PC_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# 混音台轨道配色（参考主流 DAW 的轨道色）
STAFF_COLORS = [
    '#e05561', '#5b8ff9', '#5ad8a6', '#f6bd16', '#e8684a',
    '#6dc8ec', '#9270ca', '#ff9d4d', '#269a99', '#ff99c3',
]

# ---- 深色 DAW 主题（FL Studio / Logic Pro 风格）---------------------------
# 可视化图的深色配色
_VIZ_FIG_BG = '#101114'    # matplotlib figure 底
_VIZ_AX_BG = '#17181c'     # 坐标区底
_VIZ_FG = '#c9cdd4'        # 主文字/刻度
_VIZ_DIM = '#8a8f98'       # 次要文字

_APP_STYLESHEET = """
    /* 全局：深色底盘 + 浅灰文字，FL Studio 式近黑背景 */
    QWidget { background-color:#1a1c21; color:#d7dae0; font-size:13px; }
    QToolTip { background-color:#26282e; color:#e8eaee; border:1px solid #3a3d45; padding:4px 6px; }
    QSplitter::handle { background-color:#101114; }

    /* 菜单栏 / 状态栏 */
    QMenuBar { background-color:#15161a; color:#c9cdd4; border-bottom:1px solid #2c2f36; }
    QMenuBar::item { background:transparent; padding:4px 12px; }
    QMenuBar::item:selected { background-color:#2a2d34; border-radius:4px; }
    QMenu { background-color:#202227; color:#d7dae0; border:1px solid #34373f; }
    QMenu::item { padding:5px 24px; }
    QMenu::item:selected { background-color:#ff8c1a; color:#15161a; }
    QStatusBar { background-color:#15161a; color:#9aa0a8; border-top:1px solid #2c2f36; }

    /* 走带条：比窗口更深的横条 */
    #transportBar { background-color:#121317; border-bottom:1px solid #2c2f36; }
    #transportBar QPushButton { background-color:#26282e; color:#cfd3da; border:1px solid #34373f; border-radius:4px; padding:3px 9px; font-size:12px; }
    #transportBar QPushButton:hover { background-color:#31343c; border-color:#3f434d; }
    #transportBar QPushButton:pressed { background-color:#3a3d45; }
    #transportBar QPushButton:disabled { color:#5d626b; background-color:#1d1f24; border-color:#2a2d33; }
    #transportBar QPushButton#playBtn { background-color:#30d158; color:#06230f; border:1px solid #30d158; font-size:15px; font-weight:bold; }
    #transportBar QPushButton#playBtn:hover { background-color:#45e06c; border-color:#45e06c; }
    #transportBar QPushButton#playBtn:disabled { background-color:#26282e; color:#5d626b; border-color:#34373f; }
    #transportBar QLabel { color:#9aa0a8; font-size:12px; background:transparent; }
    #lcdLabel { background-color:#050607; color:#32e16a; font-family:'Menlo','Courier New',monospace; font-size:14px; font-weight:bold; border:1px solid #1e2025; border-radius:4px; padding:3px 10px; }
    #zoomLabel { color:#9aa0a8; background:transparent; }

    /* 乐谱视图：深色边框包围纸面（纸面由 scene 背景绘制） */
    #scoreView { background-color:#0d0e11; border:none; }

    /* 底部停靠区标签页 */
    QTabWidget::pane { border:1px solid #2c2f36; background-color:#15161a; }
    QTabBar::tab { background-color:#1d1f24; color:#8a8f98; padding:5px 14px;
                   border-top-left-radius:6px; border-top-right-radius:6px; margin-right:2px; }
    QTabBar::tab:selected { background-color:#26282e; color:#ff8c1a; }
    QTabBar::tab:hover:!selected { color:#c9cdd4; }

    /* DAW 停靠面板（右侧检查器 / 对话） */
    QDockWidget::title { background-color:#1a1c21; color:#c9cdd4; padding:6px 8px;
                         border-bottom:1px solid #2c2f36; }
    #panelTitle { color:#ff8c1a; font-size:12px; font-weight:bold; }
    #panelHint { color:#8a8f98; font-size:11px; }
    QGroupBox { color:#c9cdd4; border:1px solid #2c2f36; border-radius:6px;
                margin-top:10px; padding-top:8px; }
    QGroupBox::title { subcontrol-origin:margin; left:8px; padding:0 4px; color:#ff8c1a; }
    /* 树控件样式保留：作曲库对话框的 taxonomy / 风格页仍在用 */
    QTreeWidget { background-color:#1a1c21; color:#d7dae0; border:1px solid #2c2f36; }
    QTreeWidget::item:selected { background-color:#ff8c1a; color:#15161a; }
    QTreeWidget::item:hover { background-color:#26282e; }

    /* 混音台通道条 */
    #mixerStrip { background-color:#202227; border:1px solid #33363d; border-radius:6px; }
    #mixerStrip QLabel { color:#c7cbd3; font-size:11px; background:transparent; }
    #mixerStrip QPushButton { background-color:#2a2d34; color:#cfd3da; border:1px solid #3a3d45; border-radius:3px; font-size:11px; font-weight:bold; }
    #mixerStrip QPushButton#muteBtn:checked { background-color:#ff453a; color:#2b0603; border-color:#ff453a; }
    #mixerStrip QPushButton#soloBtn:checked { background-color:#ff9f0a; color:#2b1a03; border-color:#ff9f0a; }

    /* 右侧聊天面板：保留布局，深色化 */
    #chatPanel { background-color:#15161a; }
    #chatPanel QLabel { color:#9aa0a8; background:transparent; }
    #chatPanel QPushButton { background-color:#26282e; color:#cfd3da; border:1px solid #34373f; border-radius:4px; padding:4px 12px; }
    #chatPanel QPushButton:hover { background-color:#31343c; }
    #chatPanel QPushButton:pressed { background-color:#3a3d45; }
    #chatPanel QPushButton#chatSendBtn { background-color:#ff8c1a; color:#1a1206; font-weight:bold; border:1px solid #ff8c1a; }
    #chatPanel QPushButton#chatSendBtn:hover { background-color:#ffa53d; border-color:#ffa53d; }
    #chatPanel QPushButton#chatSendBtn:pressed { background-color:#e07b00; }

    /* 下拉框 */
    QComboBox { background-color:#202227; color:#d7dae0; border:1px solid #34373f; border-radius:4px; padding:2px 6px; min-height:20px; }
    QComboBox:hover { border-color:#3f434d; }
    QComboBox::drop-down { border:none; background:transparent; width:18px; }
    QComboBox QAbstractItemView { background-color:#202227; color:#d7dae0; border:1px solid #34373f;
                                  selection-background-color:#ff8c1a; selection-color:#15161a; outline:none; }

    /* 文本框 / 输入框 */
    QPlainTextEdit, QTextEdit, QTextBrowser, QLineEdit {
        background-color:#101114; color:#d7dae0; border:1px solid #2c2f36; border-radius:6px; padding:4px 6px;
        selection-background-color:#ff8c1a; selection-color:#15161a; }
    QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus { border-color:#ff8c1a; }

    /* 列表（/style 弹层等） */
    QListWidget { background-color:#202227; color:#d7dae0; border:1px solid #34373f; }
    QListWidget::item { padding:4px 8px; }
    QListWidget::item:selected { background-color:#ff8c1a; color:#15161a; }

    /* 滑杆：细轨 + 圆柄，Logic 风 */
    QSlider::groove:horizontal { height:4px; background-color:#2c2f36; border-radius:2px; }
    QSlider::handle:horizontal { width:14px; height:14px; margin:-6px 0; border-radius:7px;
                                 background-color:#c9cdd4; border:1px solid #101114; }
    QSlider::handle:horizontal:hover { background-color:#ff8c1a; }
    QSlider::groove:vertical { width:4px; background-color:#2c2f36; border-radius:2px; }
    QSlider::handle:vertical { width:14px; height:14px; margin:0 -6px; border-radius:7px;
                               background-color:#c9cdd4; border:1px solid #101114; }
    QSlider::handle:vertical:hover { background-color:#ff8c1a; }

    /* 滚动条：细暗条 */
    QScrollBar:vertical { background-color:#15161a; width:10px; margin:0; }
    QScrollBar::handle:vertical { background-color:#2f323a; min-height:24px; border-radius:5px; margin:2px; }
    QScrollBar::handle:vertical:hover { background-color:#3f434d; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
    QScrollBar:horizontal { background-color:#15161a; height:10px; margin:0; }
    QScrollBar::handle:horizontal { background-color:#2f323a; min-width:24px; border-radius:5px; margin:2px; }
    QScrollBar::handle:horizontal:hover { background-color:#3f434d; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }
    QScrollBar::add-page, QScrollBar::sub-page { background:transparent; }

    /* 编辑模式：顶部音符输入条 + 左侧符号面板 + 建议条（对齐 MuseScore） */
    #editPalette { background-color:#121317; border-bottom:1px solid #2c2f36; }
    #editPalette QLabel#noteInputCaption { color:#6a7078; font-size:10px; font-weight:bold;
        background-color:#0e0f12; padding-left:4px; }
    #scorePalettePanel QLineEdit#paletteSearch { background-color:#202227; color:#cfd3da;
        border:1px solid #34373f; border-left:none; border-right:none; padding:2px 8px;
        font-size:12px; }
    #scorePalettePanel QCheckBox { color:#9aa0a8; font-size:11px; background:transparent; }
    #scorePalettePanel QCheckBox::indicator { width:13px; height:13px; border:1px solid #3f434d;
        border-radius:3px; background-color:#202227; }
    #scorePalettePanel QCheckBox::indicator:checked { background-color:#ff8c1a; border-color:#ff8c1a; }
    #editPalette QPushButton { background-color:#26282e; color:#cfd3da;
        border:1px solid #34373f; border-radius:4px; padding:2px 8px; font-size:14px;
        font-family:'Noto Music','Apple Symbols','Segoe UI Symbol',PingFang SC,sans-serif; }
    #editPalette QPushButton:hover { background-color:#31343c; border-color:#3f434d; }
    #editPalette QPushButton:checked { background-color:#ff8c1a; color:#15161a;
        border-color:#ff8c1a; font-weight:bold; }
    #editPalette QLabel { color:#9aa0a8; font-size:12px; background:transparent; }
    #editPalette QCheckBox { color:#9aa0a8; font-size:12px; background:transparent; }
    #editPalette QCheckBox::indicator { width:13px; height:13px; border:1px solid #3f434d;
        border-radius:3px; background-color:#202227; }
    #editPalette QCheckBox::indicator:checked { background-color:#ff8c1a;
        border-color:#ff8c1a; }
    #scorePalettePanel { background-color:#15161a; border-right:1px solid #2c2f36; }
    #scorePalettePanel QLabel#msPanelHeader { color:#cfd3da; font-size:12px; font-weight:bold;
        background-color:#121317; border-bottom:1px solid #2c2f36; }
    #scorePalettePanel QToolButton#paletteSectionHdr { color:#ff8c1a; font-size:12px;
        font-weight:bold; background:transparent; border:none; text-align:left; padding:4px 6px; }
    #scorePalettePanel QPushButton { background-color:#26282e; color:#cfd3da;
        border:1px solid #34373f; border-radius:4px; padding:2px 4px; font-size:13px;
        font-family:'Noto Music','Apple Symbols','Segoe UI Symbol',PingFang SC,sans-serif; }
    #scorePalettePanel QPushButton:hover { background-color:#31343c; border-color:#ff8c1a; color:#ff8c1a; }
    #scorePalettePanel QLabel#panelHint { color:#6a7078; font-size:11px; background:transparent; }
    #suggestionBar { background-color:#15161a; border-bottom:1px solid #2c2f36; }
    #suggestionBar QLabel { color:#8a8f98; font-size:12px; background:transparent; }
    #suggestionBar QLabel#sugTitle { color:#ff8c1a; font-weight:bold; }
    #suggestionBar QPushButton[sugChip="true"] { background-color:#202227; color:#d7dae0;
        border:1px solid #3a3d45; border-radius:11px; padding:2px 11px; font-size:12px; }
    #suggestionBar QPushButton[sugChip="true"]:hover { border-color:#ff8c1a; color:#ff8c1a; }
    #transportBar QPushButton#editBtn { padding:3px 12px; }
    #transportBar QPushButton#editBtn:checked { background-color:#ff8c1a; color:#15161a;
        border-color:#ff8c1a; font-weight:bold; }
"""

PART_LABELS = {    'Flute': '长笛',
    'Oboe': '双簧管',
    'Horn': '圆号',
    'Trumpet': '小号',
    'Violin': '小提琴',
    'Viola': '中提琴',
    'Violoncello': '大提琴',
    'Cello': '大提琴',
    'Timpani': '定音鼓',
    'String Ensemble': '弦乐合奏',
}

DUR_MAP = {
    'whole': NoteDuration.WHOLE,    'half': NoteDuration.HALF,
    'quarter': NoteDuration.QUARTER,
    'eighth': NoteDuration.EIGHTH,
    '16th': NoteDuration.SIXTEENTH,
    '32nd': NoteDuration.THIRTY_SECOND,
}
DUR_NAME_ZH = {
    NoteDuration.WHOLE: '全音符', NoteDuration.HALF: '二分音符',
    NoteDuration.QUARTER: '四分音符', NoteDuration.EIGHTH: '八分音符',
    NoteDuration.SIXTEENTH: '十六分音符', NoteDuration.THIRTY_SECOND: '三十二分音符',
}


def _ql_to_note_duration(ql: float):
    """把 quarterLength 映射为 (NoteDuration, dots)。"""
    target = float(ql) / 4.0  # 模型以全音符为 1
    best = (NoteDuration.QUARTER, 0)
    best_diff = 1e9
    for dur in NoteDuration:
        base = dur.value
        for dots in range(0, 4):
            val = base + sum(base * (0.5 ** (i + 1)) for i in range(dots))
            diff = abs(val - target)
            if diff < best_diff - 1e-12:
                best_diff = diff
                best = (dur, dots)
    return best


_M21_TYPE_TO_DUR = dict(DUR_MAP, breve=NoteDuration.WHOLE, longa=NoteDuration.WHOLE,
                        **{'64th': NoteDuration.THIRTY_SECOND,
                           '128th': NoteDuration.THIRTY_SECOND})
_TUPLET_RATIOS = ((3, 2), (5, 4), (6, 4), (7, 4), (7, 8), (2, 3), (9, 8))

# music21 类名（含父类）→ 模型记号名
_M21_MARK_NAMES = {
    'Staccatissimo': 'staccatissimo', 'Spiccato': 'staccatissimo',
    'Staccato': 'staccato', 'StrongAccent': 'marcato', 'Accent': 'accent',
    'Tenuto': 'tenuto', 'BreathMark': 'breath', 'Caesura': 'breath',
    'Fermata': 'fermata', 'Trill': 'trill', 'InvertedMordent': 'inverted_mordent',
    'Mordent': 'mordent', 'Turn': 'turn', 'ArpeggioMark': 'arpeggio',
}


def _el_marks(el) -> list:
    """music21 元素的演奏法 / 装饰音 / 琶音 → 模型 marks。"""
    out = []
    for coll in ('articulations', 'expressions'):
        for a in getattr(el, coll, None) or []:
            for cls in type(a).__mro__:
                name = _M21_MARK_NAMES.get(cls.__name__)
                if name:
                    if name not in out:
                        out.append(name)
                    break
    return out


def _ql_to_note_duration_ex(ql: float):
    """quarterLength → (NoteDuration, dots, tuplet|None)；普通时值配不上时试连音比例。"""
    dur, dots = _ql_to_note_duration(ql)
    exact = dur.value * 4.0 * (2 - 0.5 ** dots)
    if abs(exact - ql) < 1e-6:
        return dur, dots, None
    for actual, normal in _TUPLET_RATIOS:
        d2, k2 = _ql_to_note_duration(ql * actual / normal)
        if abs(d2.value * 4.0 * (2 - 0.5 ** k2) * normal / actual - ql) < 1e-6:
            return d2, k2, (actual, normal)
    return dur, dots, None


def _el_note_duration(el):
    """按元素自身的记谱时值（类型 + 附点 + 连音）取模型时值；失败退回按长度推算。"""
    d = el.duration
    dur = _M21_TYPE_TO_DUR.get(getattr(d, 'type', None))
    if dur is None:
        return _ql_to_note_duration_ex(float(el.quarterLength))
    tup = None
    tups = getattr(d, 'tuplets', None) or ()
    if tups:
        t = tups[0]
        tup = (int(t.numberNotesActual), int(t.numberNotesNormal))
    return dur, int(getattr(d, 'dots', 0) or 0), tup


def _group_tuplets(notes):
    """给连续的同比例连音音符标出组首/组尾（按"normal 个基本时值"成组）。"""
    i = 0
    while i < len(notes):
        n = notes[i]
        if not n.tuplet or n.grace:
            i += 1
            continue
        actual, normal = n.tuplet
        span = n.duration.value * normal          # 一组实际占的（全音符为 1）时值
        acc = 0.0
        j = i
        while j < len(notes) and notes[j].tuplet == n.tuplet:
            if notes[j].grace:
                j += 1
                continue
            acc += notes[j].duration_value()
            if acc >= span - 1e-9:
                break
            j += 1
        last = min(j, len(notes) - 1)
        notes[i].tuplet_start = True
        notes[last].tuplet_end = True
        i = last + 1


def _note_offsets(notes):
    """每个模型音符在小节内的起点（quarterLength）。"""
    out, t = [], 0.0
    for n in notes:
        out.append(t)
        t += n.duration_value() * 4.0
    return out


def _elements_to_linear_notes(elements, measure_start: float, ql_per_measure: float):
    """把一小节内可能重叠的多声部按时间轴切片，合并为线性音符序列。

    巴赫等键盘谱常在同一谱表写两条 Voice；若直接累加时值会变成 8 拍。
    单声部（无重叠）走快速路径，保留元素原有的记谱时值（连音、附点）与延音线；
    多声部按时间切片合并成和弦。倚音（零时值）插在同起点的主音之前。
    返回 (notes, el_to_idx)：el_to_idx 把 id(music21 元素) 映射到
    note 序列内的索引列表，供圆滑线/渐强渐弱回读定位。
    """
    segs = []   # (local_start, local_end, pitches|None, el)
    graces = []  # (local_start, el)
    for el in elements:
        if type(el).__name__ in ('ChordSymbol', 'NoChord') or getattr(el, 'isHarmony', False):
            continue
        local = float(el.offset) - measure_start
        dur = float(el.quarterLength)
        is_grace = bool(getattr(el.duration, 'isGrace', False))
        if is_grace and not getattr(el, 'isRest', False):
            if -1e-9 <= local <= ql_per_measure + 1e-9:
                graces.append((max(local, 0.0), el))
            continue
        if local >= ql_per_measure - 1e-9 or dur <= 0:
            continue
        if local < -1e-9:
            dur += local
            local = 0.0
        end = min(local + dur, ql_per_measure)
        if end - local <= 1e-9:
            continue
        if getattr(el, 'isRest', False):
            segs.append((local, end, None, el))
        elif getattr(el, 'isChord', False):
            segs.append((local, end, [_convert_pitch(p) for p in el.pitches], el))
        else:
            segs.append((local, end, [_convert_pitch(el.pitch)], el))
    segs.sort(key=lambda s: (s[0], s[1]))

    notes = []
    el_to_idx = {}
    grace_i = 0
    graces.sort(key=lambda g: g[0])

    def _emit_graces(upto):
        nonlocal grace_i
        while grace_i < len(graces) and graces[grace_i][0] <= upto + 1e-9:
            el = graces[grace_i][1]
            ps = ([_convert_pitch(p) for p in el.pitches] if getattr(el, 'isChord', False)
                  else [_convert_pitch(el.pitch)])
            ps = sorted({p.to_midi(): p for p in ps}.values(), key=lambda p: p.to_midi(),
                        reverse=True)
            dur, dots, _t = _el_note_duration(el)
            if dur in (NoteDuration.WHOLE, NoteDuration.HALF, NoteDuration.QUARTER):
                dur = NoteDuration.EIGHTH
            notes.append(Note(ps[0], dur, dots=dots, chord_tones=ps[1:], grace=True,
                              marks=_el_marks(el)))
            el_to_idx.setdefault(id(el), []).append(len(notes) - 1)
            grace_i += 1

    def _rest(span):
        dur, dots, tup = _ql_to_note_duration_ex(span)
        notes.append(Note(Pitch(NoteName.C, 4), dur, is_rest=True, dots=dots, tuplet=tup))

    def _tie_flags(el):
        t = getattr(el, 'tie', None)
        typ = getattr(t, 'type', None) if t is not None else None
        return typ in ('start', 'continue'), typ in ('stop', 'continue')

    single_voice = all(segs[k][0] >= segs[k - 1][1] - 1e-9 for k in range(1, len(segs)))
    if single_voice:
        cursor = 0.0
        for local, end, ps, el in segs:
            if local > cursor + 1e-9:
                _emit_graces(cursor)
                _rest(local - cursor)
            _emit_graces(local)
            clipped = abs((end - local) - float(el.quarterLength)) > 1e-6
            if clipped:
                dur, dots, tup = _ql_to_note_duration_ex(end - local)
            else:
                dur, dots, tup = _el_note_duration(el)
            if ps is None:
                n = Note(Pitch(NoteName.C, 4), dur, is_rest=True, dots=dots, tuplet=tup,
                         marks=['fermata'] if 'fermata' in _el_marks(el) else [])
            else:
                uniq = sorted({p.to_midi(): p for p in ps}.values(),
                              key=lambda p: p.to_midi(), reverse=True)
                ts, te = _tie_flags(el)
                n = Note(uniq[0], dur, dots=dots, chord_tones=uniq[1:], tuplet=tup,
                         tie_start=ts, tie_end=te, marks=_el_marks(el),
                         lyric=(getattr(el, 'lyric', None) or None))
            notes.append(n)
            el_to_idx.setdefault(id(el), []).append(len(notes) - 1)
            cursor = end
        if cursor < ql_per_measure - 1e-9 and segs:
            _emit_graces(cursor)
            _rest(ql_per_measure - cursor)
        elif not segs:
            _emit_graces(0.0)
            _rest(ql_per_measure)
        _emit_graces(ql_per_measure)
        _group_tuplets(notes)
        return notes, el_to_idx

    bounds = {0.0, float(ql_per_measure)}
    for s, e, _p, _el in segs:
        bounds.add(round(s, 6))
        bounds.add(round(e, 6))
    bounds = sorted(bounds)

    seen = set()
    prev_contrib = None   # 上一切片的发声元素集合（判断切片间延音线）
    for t0, t1 in zip(bounds, bounds[1:]):
        span = t1 - t0
        if span <= 1e-9:
            continue
        _emit_graces(t0)
        pitches, marks, contrib, lyric = [], [], [], None
        for s, e, ps, el in segs:
            if ps is None:
                continue
            if s <= t0 + 1e-9 and e > t0 + 1e-9:
                pitches.extend(ps)
                contrib.append(el)
                if id(el) not in seen:
                    seen.add(id(el))
                    marks.extend(m for m in _el_marks(el) if m not in marks)
                    lyric = lyric or getattr(el, 'lyric', None) or None
        dur, dots, tup = _ql_to_note_duration_ex(span)
        if pitches:
            ordered = sorted({p.to_midi(): p for p in pitches}.values(),
                             key=lambda p: p.to_midi(), reverse=True)
            n = Note(ordered[0], dur, dots=dots, chord_tones=ordered[1:], tuplet=tup,
                     marks=marks, lyric=lyric)
            cur = {id(e) for e in contrib}
            if prev_contrib is not None and cur == prev_contrib and notes:
                notes[-1].tie_start = True
                n.tie_end = True
            elif all(_tie_flags(e)[1] for e in contrib) and all(
                    abs(float(e.offset) - measure_start - t0) < 1e-6 for e in contrib):
                n.tie_end = True
            if all(_tie_flags(e)[0] for e in contrib) and all(
                    abs(min(float(e.offset) - measure_start + float(e.quarterLength),
                            ql_per_measure) - t1) < 1e-6 for e in contrib):
                n.tie_start = True
            notes.append(n)
            prev_contrib = cur
        else:
            notes.append(Note(Pitch(NoteName.C, 4), dur, is_rest=True, dots=dots, tuplet=tup))
            prev_contrib = None
        for el in contrib:
            el_to_idx.setdefault(id(el), []).append(len(notes) - 1)
    _emit_graces(ql_per_measure)
    _group_tuplets(notes)
    return notes, el_to_idx


# ============================================================
# 乐谱构建（music21）— 原创 G 大调快板乐章
# ============================================================

def _make_part(inst_obj, name, midi_program, clef_obj, part_id):
    part = stream.Part(id=part_id)
    inst_obj.instrumentName = name
    inst_obj.partName = name
    if midi_program is not None:
        inst_obj.midiProgram = midi_program
    if hasattr(inst_obj, 'transposition'):
        inst_obj.transposition = None
    part.insert(0, inst_obj)
    part.insert(0, clef_obj)
    return part


def _add_events(part, events):
    """events: list of (pitch_or_list_or_None, duration_type)"""
    for pitch, dur in events:
        if pitch is None:
            part.append(m21note.Rest(type=dur))
        elif isinstance(pitch, (list, tuple)):
            part.append(m21chord.Chord(list(pitch), type=dur))
        else:
            part.append(m21note.Note(pitch, type=dur))


def build_music21_score():
    """原创管弦乐章：《晨光快板》G 大调，4/4，♩=126，共 32 小节。

    曲式：呈示(1–16) — 展开(17–24) — 再现与终止(25–32)
    编制：长笛、双簧管、圆号、小号、小提琴、中提琴、大提琴、定音鼓
    """
    score = stream.Score(id='g_major_allegro_movement')
    md = m21meta.Metadata()
    md.title = '晨光快板'
    md.composer = 'Score Studio（原创）'
    md.movementName = 'Allegro'
    score.insert(0, md)
    score.insert(0, key.KeySignature(1))
    score.insert(0, meter.TimeSignature('4/4'))
    score.insert(0, tempo.MetronomeMark(number=126))

    flute = _make_part(instrument.Flute(), 'Flute', 73, m21clef.TrebleClef(), 'flute')
    oboe = _make_part(instrument.Oboe(), 'Oboe', 68, m21clef.TrebleClef(), 'oboe')
    horn = _make_part(instrument.Horn(), 'Horn', 60, m21clef.TrebleClef(), 'horn')
    trumpet = _make_part(instrument.Trumpet(), 'Trumpet', 56, m21clef.TrebleClef(), 'trumpet')
    violin = _make_part(instrument.Violin(), 'Violin', 40, m21clef.TrebleClef(), 'violin')
    viola = _make_part(instrument.Viola(), 'Viola', 41, m21clef.AltoClef(), 'viola')
    cello = _make_part(instrument.Violoncello(), 'Cello', 42, m21clef.BassClef(), 'cello')
    timp = _make_part(instrument.Timpani(), 'Timpani', 47, m21clef.BassClef(), 'timpani')

    _add_events(violin, [
        # 1–8
        ('G4', 'eighth'),
        ('D5', 'eighth'),
        ('B4', 'eighth'),
        ('G5', 'eighth'),
        ('F#5', 'quarter'),
        ('D5', 'quarter'),
        ('E5', 'eighth'),
        ('B4', 'eighth'),
        ('G4', 'eighth'),
        ('E5', 'eighth'),
        ('D5', 'quarter'),
        ('A4', 'quarter'),
        ('B4', 'eighth'),
        ('G5', 'eighth'),
        ('D5', 'eighth'),
        ('B4', 'eighth'),
        ('C5', 'quarter'),
        ('E5', 'quarter'),
        ('A5', 'quarter'),
        ('D5', 'eighth'),
        ('F#5', 'eighth'),
        ('G5', 'half'),
        ('E5', 'eighth'),
        ('C5', 'eighth'),
        ('A4', 'eighth'),
        ('E5', 'eighth'),
        ('D5', 'eighth'),
        ('B4', 'eighth'),
        ('G4', 'quarter'),
        ('C5', 'eighth'),
        ('G5', 'eighth'),
        ('E5', 'eighth'),
        ('C5', 'eighth'),
        ('B4', 'quarter'),
        ('D5', 'quarter'),
        ('A4', 'eighth'),
        ('E5', 'eighth'),
        ('C5', 'eighth'),
        ('A5', 'eighth'),
        ('G5', 'half'),
        ('D5', 'eighth'),
        ('G4', 'eighth'),
        ('B4', 'eighth'),
        ('D5', 'eighth'),
        ('G5', 'half'),
        # 9–16
        ('D5', 'eighth'),
        ('A5', 'eighth'),
        ('F#5', 'eighth'),
        ('D5', 'eighth'),
        ('G5', 'quarter'),
        ('B4', 'quarter'),
        ('E5', 'eighth'),
        ('C5', 'eighth'),
        ('G5', 'eighth'),
        ('E5', 'eighth'),
        ('D5', 'quarter'),
        ('F#5', 'quarter'),
        ('B5', 'quarter'),
        ('G4', 'eighth'),
        ('D5', 'eighth'),
        ('E5', 'half'),
        ('C5', 'eighth'),
        ('A4', 'eighth'),
        ('F#5', 'eighth'),
        ('D5', 'eighth'),
        ('B4', 'half'),
        ('G5', 'eighth'),
        ('D5', 'eighth'),
        ('A4', 'eighth'),
        ('D5', 'eighth'),
        ('B4', 'eighth'),
        ('G5', 'eighth'),
        ('D5', 'quarter'),
        ('E5', 'eighth'),
        ('B5', 'eighth'),
        ('G5', 'eighth'),
        ('E5', 'eighth'),
        ('F#5', 'half'),
        ('G5', 'eighth'),
        ('D6', 'eighth'),
        ('B5', 'eighth'),
        ('G5', 'eighth'),
        ('D5', 'half'),
        ('A5', 'eighth'),
        ('E5', 'eighth'),
        ('C5', 'eighth'),
        ('A4', 'eighth'),
        ('G4', 'half'),
        # 17–24
        ('E5', 'eighth'),
        ('B4', 'eighth'),
        ('G5', 'eighth'),
        ('E5', 'eighth'),
        ('C5', 'quarter'),
        ('A4', 'quarter'),
        ('D5', 'eighth'),
        ('A5', 'eighth'),
        ('F#5', 'eighth'),
        ('D5', 'eighth'),
        ('G5', 'quarter'),
        ('B4', 'quarter'),
        ('C5', 'eighth'),
        ('G4', 'eighth'),
        ('E5', 'eighth'),
        ('C5', 'eighth'),
        ('A4', 'eighth'),
        ('E5', 'eighth'),
        ('C5', 'quarter'),
        ('B4', 'eighth'),
        ('G5', 'eighth'),
        ('D5', 'eighth'),
        ('B4', 'eighth'),
        ('A4', 'half'),
        ('G5', 'eighth'),
        ('E5', 'eighth'),
        ('C5', 'eighth'),
        ('G5', 'eighth'),
        ('A5', 'quarter'),
        ('F#5', 'quarter'),
        ('D5', 'eighth'),
        ('B4', 'eighth'),
        ('G4', 'eighth'),
        ('D5', 'eighth'),
        ('E5', 'half'),
        ('A4', 'eighth'),
        ('F#5', 'eighth'),
        ('D5', 'eighth'),
        ('A5', 'eighth'),
        ('G5', 'half'),
        ('E5', 'quarter'),
        ('C5', 'eighth'),
        ('A4', 'eighth'),
        ('B4', 'half'),
        # 25–32
        ('G4', 'eighth'),
        ('D5', 'eighth'),
        ('B4', 'eighth'),
        ('G5', 'eighth'),
        ('A5', 'quarter'),
        ('F#5', 'quarter'),
        ('E5', 'eighth'),
        ('B5', 'eighth'),
        ('G5', 'eighth'),
        ('E5', 'eighth'),
        ('D5', 'quarter'),
        ('B4', 'quarter'),
        ('C5', 'eighth'),
        ('G5', 'eighth'),
        ('E5', 'eighth'),
        ('C6', 'eighth'),
        ('B5', 'half'),
        ('A5', 'eighth'),
        ('E5', 'eighth'),
        ('C5', 'eighth'),
        ('A4', 'eighth'),
        ('D5', 'eighth'),
        ('G5', 'eighth'),
        ('B4', 'quarter'),
        ('E5', 'eighth'),
        ('C5', 'eighth'),
        ('A5', 'eighth'),
        ('F#5', 'eighth'),
        ('G5', 'half'),
        ('D5', 'eighth'),
        ('B4', 'eighth'),
        ('G5', 'eighth'),
        ('D5', 'eighth'),
        ('E5', 'quarter'),
        ('C5', 'quarter'),
        ('B4', 'eighth'),
        ('G4', 'eighth'),
        ('D5', 'eighth'),
        ('B4', 'eighth'),
        ('A4', 'quarter'),
        ('G4', 'quarter'),
        ('D5', 'eighth'),
        ('B4', 'eighth'),
        ('G5', 'eighth'),
        ('D5', 'eighth'),
        ('G4', 'half'),
    ])

    _add_events(flute, [
        # 1–8
        (None, 'quarter'), ('G5', 'eighth'), ('D6', 'eighth'),
        ('B5', 'eighth'), ('G5', 'eighth'), ('E5', 'quarter'),
        ('A5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'), ('A5', 'eighth'),
        ('G5', 'half'),
        ('D6', 'quarter'), ('A5', 'eighth'), ('F#5', 'eighth'), ('G5', 'half'),
        ('E5', 'eighth'), ('C6', 'eighth'), ('A5', 'eighth'), ('E5', 'eighth'),
        ('D5', 'half'),
        ('B5', 'eighth'), ('G5', 'eighth'), ('D5', 'eighth'), ('B5', 'eighth'),
        ('C6', 'quarter'), ('A5', 'quarter'),
        ('G5', 'eighth'), ('D6', 'eighth'), ('B5', 'eighth'), ('G5', 'eighth'),
        ('A5', 'half'),
        ('F#5', 'eighth'), ('D5', 'eighth'), ('A5', 'eighth'), ('F#5', 'eighth'),
        ('G5', 'half'),
        ('E6', 'quarter'), ('C6', 'eighth'), ('A5', 'eighth'), ('G5', 'half'),
        # 9–16
        ('A5', 'eighth'), ('E6', 'eighth'), ('C6', 'eighth'), ('A5', 'eighth'),
        ('G5', 'quarter'), ('D5', 'quarter'),
        ('B5', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'), ('B5', 'eighth'),
        ('A5', 'half'),
        ('D6', 'quarter'), ('G5', 'eighth'), ('B5', 'eighth'), ('C6', 'half'),
        ('A5', 'eighth'), ('F#5', 'eighth'), ('D5', 'eighth'), ('A5', 'eighth'),
        ('G5', 'half'),
        ('E6', 'eighth'), ('C6', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'),
        ('D5', 'eighth'), ('A5', 'eighth'), ('F#5', 'quarter'),
        ('G5', 'eighth'), ('D6', 'eighth'), ('B5', 'eighth'), ('G5', 'eighth'),
        ('D5', 'half'),
        ('C6', 'eighth'), ('A5', 'eighth'), ('F#5', 'eighth'), ('D5', 'eighth'),
        ('G5', 'half'),
        ('B5', 'quarter'), ('G5', 'eighth'), ('E5', 'eighth'), ('D5', 'half'),
        # 17–24
        ('E5', 'eighth'), ('B5', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'),
        ('C5', 'quarter'), ('A5', 'quarter'),
        ('D5', 'eighth'), ('A5', 'eighth'), ('F#5', 'eighth'), ('D6', 'eighth'),
        ('B5', 'half'),
        ('C6', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'),
        ('A4', 'eighth'), ('E5', 'eighth'), ('C5', 'quarter'),
        ('B5', 'eighth'), ('G5', 'eighth'), ('D5', 'eighth'), ('B4', 'eighth'),
        ('A5', 'half'),
        ('G5', 'eighth'), ('E6', 'eighth'), ('C6', 'eighth'), ('G5', 'eighth'),
        ('A5', 'quarter'), ('F#5', 'quarter'),
        ('D5', 'eighth'), ('B5', 'eighth'), ('G5', 'eighth'), ('D5', 'eighth'),
        ('E5', 'half'),
        ('A5', 'eighth'), ('F#5', 'eighth'), ('D5', 'eighth'), ('A5', 'eighth'),
        ('G5', 'half'),
        ('E5', 'quarter'), ('C6', 'eighth'), ('A5', 'eighth'), ('G5', 'half'),
        # 25–32
        ('G5', 'eighth'), ('D6', 'eighth'), ('B5', 'eighth'), ('G5', 'eighth'),
        ('A5', 'quarter'), ('F#5', 'quarter'),
        ('E5', 'eighth'), ('B5', 'eighth'), ('G5', 'eighth'), ('E6', 'eighth'),
        ('D6', 'quarter'), ('B5', 'quarter'),
        ('C6', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'), ('C6', 'eighth'),
        ('B5', 'half'),
        ('A5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'), ('A5', 'eighth'),
        ('D5', 'eighth'), ('G5', 'eighth'), ('B5', 'quarter'),
        ('E5', 'eighth'), ('C6', 'eighth'), ('A5', 'eighth'), ('F#5', 'eighth'),
        ('G5', 'half'),
        ('D5', 'eighth'), ('B5', 'eighth'), ('G5', 'eighth'), ('D5', 'eighth'),
        ('E5', 'quarter'), ('C5', 'quarter'),
        ('B5', 'eighth'), ('G5', 'eighth'), ('D5', 'eighth'), ('B4', 'eighth'),
        ('G5', 'half'),
        ('D6', 'quarter'), ('B5', 'eighth'), ('G5', 'eighth'), ('G5', 'half'),
    ])

    _add_events(oboe, [
        ('D5', 'half'), ('B4', 'half'),
        ('A4', 'half'), ('G4', 'half'),
        ('E5', 'half'), ('D5', 'half'),
        ('C5', 'half'), ('B4', 'half'),
        ('C5', 'half'), ('D5', 'half'),
        ('E5', 'half'), ('D5', 'half'),
        ('C5', 'whole'),
        ('B4', 'whole'),
        ('A4', 'half'), ('B4', 'half'),
        ('C5', 'half'), ('D5', 'half'),
        ('E5', 'half'), ('D5', 'half'),
        ('C5', 'whole'),
        ('B4', 'half'), ('A4', 'half'),
        ('G4', 'half'), ('A4', 'half'),
        ('B4', 'half'), ('C5', 'half'),
        ('D5', 'whole'),
        ('E5', 'half'), ('D5', 'half'),
        ('C5', 'half'), ('B4', 'half'),
        ('A4', 'half'), ('G4', 'half'),
        ('F#4', 'half'), ('G4', 'half'),
        ('A4', 'half'), ('B4', 'half'),
        ('C5', 'half'), ('B4', 'half'),
        ('A4', 'whole'),
        ('F#4', 'whole'),
        ('D5', 'half'), ('B4', 'half'),
        ('A4', 'half'), ('G4', 'half'),
        ('E5', 'half'), ('D5', 'half'),
        ('C5', 'half'), ('B4', 'half'),
        ('A4', 'half'), ('B4', 'half'),
        ('C5', 'half'), ('D5', 'half'),
        ('B4', 'half'), ('A4', 'half'),
        ('G4', 'whole'),
    ])

    horn_chords = (
        [['G3', 'B3', 'D4']] * 2
        + [['C4', 'E4', 'G4'], ['D4', 'F#4', 'A4'],
           ['E3', 'G3', 'B3'], ['A3', 'C4', 'E4'],
           ['D4', 'F#4', 'A4'], ['G3', 'B3', 'D4']]
        + [['D4', 'F#4', 'A4']] * 2
        + [['G3', 'B3', 'D4'], ['A3', 'C4', 'E4'],
           ['B3', 'D4', 'F#4'], ['C4', 'E4', 'G4'],
           ['D4', 'F#4', 'A4'], ['D4', 'F#4', 'A4']]
        + [['A3', 'C4', 'E4'], ['D4', 'F#4', 'A4'],
           ['E3', 'G3', 'B3'], ['G3', 'B3', 'D4'],
           ['C4', 'E4', 'G4'], ['A3', 'C4', 'E4'],
           ['D4', 'F#4', 'A4'], ['D4', 'F#4', 'A4']]
        + [['G3', 'B3', 'D4']] * 2
        + [['C4', 'E4', 'G4'], ['D4', 'F#4', 'A4'],
           ['E3', 'G3', 'B3'], ['A3', 'C4', 'E4'],
           ['D4', 'F#4', 'A4'], ['G3', 'B3', 'D4']]
    )
    _add_events(horn, [(ch, 'whole') for ch in horn_chords])

    _add_events(trumpet, [
        (None, 'whole'), (None, 'whole'), (None, 'whole'), (None, 'whole'),
        (None, 'whole'), (None, 'whole'),
        (['D5', 'A4'], 'half'), (['D5', 'A4'], 'half'),
        (['G4', 'B4', 'D5'], 'whole'),
        (None, 'whole'), (None, 'whole'), (None, 'whole'), (None, 'whole'),
        (None, 'whole'), (None, 'whole'),
        (['A4', 'D5'], 'half'), (['A4', 'D5'], 'half'),
        (['D5', 'F#5'], 'whole'),
        (None, 'whole'), (None, 'whole'), (None, 'whole'), (None, 'whole'),
        (None, 'whole'), (None, 'whole'),
        (['D5', 'A4'], 'half'), (None, 'half'),
        (None, 'whole'),
        (None, 'whole'), (None, 'whole'), (None, 'whole'), (None, 'whole'),
        (None, 'whole'), (None, 'whole'),
        (['D5', 'A4'], 'half'), (['D5', 'G4'], 'half'),
        (['G4', 'B4', 'D5'], 'whole'),
    ])

    _add_events(viola, [
        ('B3', 'half'), ('D4', 'half'),
        ('D4', 'half'), ('C4', 'half'),
        ('E4', 'half'), ('G4', 'half'),
        ('F#4', 'half'), ('D4', 'half'),
        ('G3', 'half'), ('B3', 'half'),
        ('C4', 'half'), ('E4', 'half'),
        ('D4', 'whole'),
        ('B3', 'whole'),
        ('F#4', 'half'), ('A4', 'half'),
        ('G4', 'half'), ('E4', 'half'),
        ('D4', 'half'), ('B3', 'half'),
        ('C4', 'whole'),
        ('D4', 'half'), ('C4', 'half'),
        ('B3', 'half'), ('A3', 'half'),
        ('A3', 'half'), ('F#4', 'half'),
        ('G4', 'whole'),
        ('C4', 'half'), ('B3', 'half'),
        ('A3', 'half'), ('G3', 'half'),
        ('F#3', 'half'), ('G3', 'half'),
        ('A3', 'half'), ('B3', 'half'),
        ('C4', 'half'), ('D4', 'half'),
        ('E4', 'half'), ('D4', 'half'),
        ('C4', 'whole'),
        ('A3', 'whole'),
        ('B3', 'half'), ('D4', 'half'),
        ('D4', 'half'), ('C4', 'half'),
        ('E4', 'half'), ('D4', 'half'),
        ('C4', 'half'), ('B3', 'half'),
        ('A3', 'half'), ('B3', 'half'),
        ('C4', 'half'), ('D4', 'half'),
        ('B3', 'half'), ('A3', 'half'),
        ('G3', 'whole'),
    ])

    _add_events(cello, [
        ('G2', 'half'), ('D3', 'half'),
        ('G2', 'half'), ('F#2', 'half'),
        ('C3', 'half'), ('G2', 'half'),
        ('D3', 'half'), ('A2', 'half'),
        ('E2', 'half'), ('B2', 'half'),
        ('A2', 'half'), ('E3', 'half'),
        ('D3', 'half'), ('C3', 'half'),
        ('G2', 'whole'),
        ('D3', 'half'), ('A2', 'half'),
        ('G2', 'half'), ('E2', 'half'),
        ('B2', 'half'), ('G2', 'half'),
        ('A2', 'whole'),
        ('B2', 'half'), ('C3', 'half'),
        ('D3', 'half'), ('C3', 'half'),
        ('A2', 'half'), ('D3', 'half'),
        ('D3', 'whole'),
        ('A2', 'half'), ('E3', 'half'),
        ('C3', 'half'), ('A2', 'half'),
        ('E2', 'half'), ('B2', 'half'),
        ('G2', 'half'), ('D3', 'half'),
        ('C3', 'half'), ('G2', 'half'),
        ('A2', 'half'), ('E2', 'half'),
        ('B2', 'half'), ('A2', 'half'),
        ('D2', 'whole'),
        ('G2', 'half'), ('D3', 'half'),
        ('G2', 'half'), ('F#2', 'half'),
        ('C3', 'half'), ('G2', 'half'),
        ('D3', 'half'), ('A2', 'half'),
        ('E2', 'half'), ('B2', 'half'),
        ('A2', 'half'), ('D3', 'half'),
        ('D3', 'half'), ('B2', 'half'),
        ('G2', 'whole'),
    ])

    _add_events(timp, [
        ('G2', 'half'), (None, 'half'),
        (None, 'whole'),
        (None, 'whole'),
        ('D3', 'half'), (None, 'half'),
        (None, 'whole'),
        (None, 'whole'),
        ('D3', 'quarter'), ('D3', 'quarter'), (None, 'half'),
        ('G2', 'whole'),
        ('D3', 'half'), (None, 'half'),
        (None, 'whole'),
        (None, 'whole'),
        (None, 'whole'),
        (None, 'whole'),
        (None, 'whole'),
        ('D3', 'half'), ('D3', 'half'),
        ('D3', 'whole'),
        (None, 'whole'),
        (None, 'whole'),
        (None, 'whole'),
        (None, 'whole'),
        (None, 'whole'),
        (None, 'whole'),
        ('D3', 'half'), (None, 'half'),
        ('D2', 'whole'),
        ('G2', 'half'), (None, 'half'),
        (None, 'whole'),
        (None, 'whole'),
        ('D3', 'half'), (None, 'half'),
        (None, 'whole'),
        (None, 'whole'),
        ('D3', 'quarter'), ('D3', 'quarter'), (None, 'half'),
        ('G2', 'whole'),
    ])

    for p in (flute, oboe, horn, trumpet, violin, viola, cello, timp):
        score.insert(0, p)
    return score

def build_pirate_allegro_score():
    """原创管弦乐章：《怒海快板》A 小调，6/8，♩=180，共 32 小节。

    海盗风：号角动机、琶音大跳、定音鼓主属敲击；旋律原创，非任何电影原曲。
    曲式：呈示(1–16) — 展开(17–24) — 再现与终止(25–32)
    """
    from music21 import duration as m21duration
    score = stream.Score(id='pirate_storm_allegro')
    md = m21meta.Metadata()
    md.title = '怒海快板'
    md.composer = 'Score Studio（原创）'
    md.movementName = 'Allegro pirata'
    score.insert(0, md)
    score.insert(0, key.KeySignature(0))  # A 小调
    score.insert(0, meter.TimeSignature('6/8'))
    mm = tempo.MetronomeMark(number=180)
    score.insert(0, mm)

    flute = _make_part(instrument.Flute(), 'Flute', 73, m21clef.TrebleClef(), 'flute')
    oboe = _make_part(instrument.Oboe(), 'Oboe', 68, m21clef.TrebleClef(), 'oboe')
    horn = _make_part(instrument.Horn(), 'Horn', 60, m21clef.TrebleClef(), 'horn')
    trumpet = _make_part(instrument.Trumpet(), 'Trumpet', 56, m21clef.TrebleClef(), 'trumpet')
    violin = _make_part(instrument.Violin(), 'Violin', 40, m21clef.TrebleClef(), 'violin')
    viola = _make_part(instrument.Viola(), 'Viola', 41, m21clef.AltoClef(), 'viola')
    cello = _make_part(instrument.Violoncello(), 'Cello', 42, m21clef.BassClef(), 'cello')
    timp = _make_part(instrument.Timpani(), 'Timpani', 47, m21clef.BassClef(), 'timpani')

    _add_events(violin, [
        # 1–8
        ('A4', 'eighth'), ('C5', 'eighth'), ('E5', 'eighth'), ('A5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'),
        ('B4', 'eighth'), ('D5', 'eighth'), ('F5', 'eighth'), ('A5', 'eighth'), ('F5', 'eighth'), ('D5', 'eighth'),
        ('E5', 'quarter'), ('C5', 'eighth'), ('A4', 'quarter'), ('E4', 'eighth'),
        ('F5', 'eighth'), ('D5', 'eighth'), ('A4', 'eighth'), ('F5', 'eighth'), ('E5', 'quarter'),
        ('A5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'), ('A4', 'eighth'), ('C5', 'eighth'), ('E5', 'eighth'),
        ('G5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'), ('G4', 'eighth'), ('C5', 'eighth'), ('E5', 'eighth'),
        ('F5', 'quarter'), ('D5', 'eighth'), ('B4', 'quarter'), ('G4', 'eighth'),
        ('A4', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'), ('A5', 'quarter'), ('E5', 'eighth'),
        # 9–16
        ('C6', 'eighth'), ('A5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'), ('E5', 'eighth'), ('A5', 'eighth'),
        ('B5', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'), ('B4', 'eighth'), ('E5', 'eighth'), ('G5', 'eighth'),
        ('A5', 'quarter'), ('F5', 'eighth'), ('D5', 'quarter'), ('A4', 'eighth'),
        ('E5', 'eighth'), ('C5', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'), ('A5', 'quarter'),
        ('F5', 'eighth'), ('A5', 'eighth'), ('D6', 'eighth'), ('A5', 'eighth'), ('F5', 'eighth'), ('D5', 'eighth'),
        ('E5', 'eighth'), ('G5', 'eighth'), ('C6', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'),
        ('D5', 'quarter'), ('B4', 'eighth'), ('G4', 'quarter'), ('D5', 'eighth'),
        ('A4', 'half'), ('E5', 'eighth'), ('A5', 'eighth'),
        # 17–24
        ('C5', 'eighth'), ('E5', 'eighth'), ('G5', 'eighth'), ('C6', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'),
        ('D5', 'eighth'), ('F5', 'eighth'), ('A5', 'eighth'), ('D6', 'eighth'), ('A5', 'eighth'), ('F5', 'eighth'),
        ('E5', 'eighth'), ('G#5', 'eighth'), ('B5', 'eighth'), ('E6', 'eighth'), ('B5', 'eighth'), ('G#5', 'eighth'),
        ('A5', 'quarter'), ('E5', 'eighth'), ('C5', 'quarter'), ('A4', 'eighth'),
        ('F5', 'eighth'), ('D5', 'eighth'), ('B4', 'eighth'), ('F4', 'eighth'), ('B4', 'eighth'), ('D5', 'eighth'),
        ('E5', 'eighth'), ('C5', 'eighth'), ('A4', 'eighth'), ('E4', 'eighth'), ('A4', 'eighth'), ('C5', 'eighth'),
        ('D5', 'quarter'), ('F5', 'eighth'), ('A5', 'quarter'), ('F5', 'eighth'),
        ('E5', 'eighth'), ('B4', 'eighth'), ('G#4', 'eighth'), ('E5', 'quarter'), ('B4', 'eighth'),
        # 25–32
        ('A4', 'eighth'), ('C5', 'eighth'), ('E5', 'eighth'), ('A5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'),
        ('B4', 'eighth'), ('D5', 'eighth'), ('F5', 'eighth'), ('A5', 'eighth'), ('F5', 'eighth'), ('D5', 'eighth'),
        ('C5', 'eighth'), ('E5', 'eighth'), ('G5', 'eighth'), ('C6', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'),
        ('A5', 'quarter'), ('E5', 'eighth'), ('C6', 'quarter'), ('A5', 'eighth'),
        ('F5', 'eighth'), ('D5', 'eighth'), ('A5', 'eighth'), ('F5', 'eighth'), ('E5', 'quarter'),
        ('D5', 'eighth'), ('B4', 'eighth'), ('F5', 'eighth'), ('D5', 'eighth'), ('C5', 'quarter'),
        ('B4', 'eighth'), ('E5', 'eighth'), ('G#5', 'eighth'), ('B5', 'eighth'), ('E5', 'quarter'),
        ('A5', 'half'), ('A4', 'quarter'),
    ])
    _add_events(flute, [
        # 1–8
        (None, 'eighth'), ('E5', 'eighth'), ('A5', 'eighth'), ('C6', 'eighth'), ('A5', 'eighth'), ('E5', 'eighth'),
        (None, 'eighth'), ('F5', 'eighth'), ('A5', 'eighth'), ('D6', 'eighth'), ('A5', 'eighth'), ('F5', 'eighth'),
        ('C6', 'quarter'), ('A5', 'eighth'), ('E5', 'quarter'), ('C5', 'eighth'),
        ('D6', 'eighth'), ('A5', 'eighth'), ('F5', 'eighth'), ('D6', 'eighth'), ('C6', 'quarter'),
        ('E6', 'eighth'), ('C6', 'eighth'), ('A5', 'eighth'), ('E5', 'eighth'), ('A5', 'eighth'), ('C6', 'eighth'),
        ('E6', 'eighth'), ('C6', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'), ('G5', 'eighth'), ('C6', 'eighth'),
        ('D6', 'quarter'), ('B5', 'eighth'), ('G5', 'quarter'), ('D5', 'eighth'),
        ('E5', 'eighth'), ('A5', 'eighth'), ('C6', 'eighth'), ('E6', 'quarter'), ('C6', 'eighth'),
        # 9–16
        ('A5', 'eighth'), ('C6', 'eighth'), ('E6', 'eighth'), ('A5', 'eighth'), ('C6', 'eighth'), ('E6', 'eighth'),
        ('G5', 'eighth'), ('B5', 'eighth'), ('E6', 'eighth'), ('G5', 'eighth'), ('B5', 'eighth'), ('E6', 'eighth'),
        ('F6', 'quarter'), ('D6', 'eighth'), ('A5', 'quarter'), ('F5', 'eighth'),
        ('E6', 'eighth'), ('C6', 'eighth'), ('A5', 'eighth'), ('E6', 'eighth'), ('C6', 'quarter'),
        ('D6', 'eighth'), ('F6', 'eighth'), ('A5', 'eighth'), ('D6', 'eighth'), ('F5', 'eighth'), ('A5', 'eighth'),
        ('C6', 'eighth'), ('E6', 'eighth'), ('G5', 'eighth'), ('C6', 'eighth'), ('E5', 'eighth'), ('G5', 'eighth'),
        ('B5', 'quarter'), ('G5', 'eighth'), ('D5', 'quarter'), ('B4', 'eighth'),
        ('A5', 'half'), ('C6', 'eighth'), ('E6', 'eighth'),
        # 17–24
        ('G5', 'eighth'), ('C6', 'eighth'), ('E6', 'eighth'), ('G6', 'eighth'), ('E6', 'eighth'), ('C6', 'eighth'),
        ('A5', 'eighth'), ('D6', 'eighth'), ('F6', 'eighth'), ('A6', 'eighth'), ('F6', 'eighth'), ('D6', 'eighth'),
        ('B5', 'eighth'), ('E6', 'eighth'), ('G#6', 'eighth'), ('B5', 'eighth'), ('E6', 'eighth'), ('G#5', 'eighth'),
        ('A6', 'quarter'), ('E6', 'eighth'), ('C6', 'quarter'), ('A5', 'eighth'),
        ('D6', 'eighth'), ('B5', 'eighth'), ('F5', 'eighth'), ('D6', 'eighth'), ('B5', 'quarter'),
        ('C6', 'eighth'), ('A5', 'eighth'), ('E5', 'eighth'), ('C6', 'eighth'), ('A5', 'quarter'),
        ('F6', 'quarter'), ('D6', 'eighth'), ('A5', 'quarter'), ('F5', 'eighth'),
        ('E6', 'eighth'), ('B5', 'eighth'), ('G#5', 'eighth'), ('E6', 'quarter'), ('B5', 'eighth'),
        # 25–32
        (None, 'eighth'), ('E5', 'eighth'), ('A5', 'eighth'), ('C6', 'eighth'), ('A5', 'eighth'), ('E5', 'eighth'),
        (None, 'eighth'), ('F5', 'eighth'), ('A5', 'eighth'), ('D6', 'eighth'), ('A5', 'eighth'), ('F5', 'eighth'),
        ('G5', 'eighth'), ('C6', 'eighth'), ('E6', 'eighth'), ('G6', 'eighth'), ('E6', 'eighth'), ('C6', 'eighth'),
        ('A6', 'quarter'), ('E6', 'eighth'), ('C6', 'quarter'), ('A5', 'eighth'),
        ('F6', 'eighth'), ('D6', 'eighth'), ('A5', 'eighth'), ('F6', 'eighth'), ('E6', 'quarter'),
        ('D6', 'eighth'), ('B5', 'eighth'), ('F5', 'eighth'), ('D6', 'eighth'), ('C6', 'quarter'),
        ('B5', 'eighth'), ('E6', 'eighth'), ('G#5', 'eighth'), ('B5', 'eighth'), ('E6', 'quarter'),
        ('A6', 'half'), ('A5', 'quarter'),
    ])
    _add_events(oboe, [
        # 1–8
        ('E5', 'quarter'), ('A4', 'eighth'), ('C5', 'quarter'), ('E5', 'eighth'),
        ('F5', 'quarter'), ('A4', 'eighth'), ('D5', 'quarter'), ('F5', 'eighth'),
        ('A4', 'half'), ('C5', 'eighth'), ('E5', 'eighth'),
        ('D5', 'half'), ('F5', 'eighth'), ('A4', 'eighth'),
        ('C5', 'quarter'), ('E5', 'eighth'), ('A5', 'quarter'), ('E5', 'eighth'),
        ('C5', 'quarter'), ('E5', 'eighth'), ('G5', 'quarter'), ('E5', 'eighth'),
        ('B4', 'half'), ('D5', 'eighth'), ('F5', 'eighth'),
        ('C5', 'half'), ('E5', 'eighth'), ('A4', 'eighth'),
        # 9–16
        ('E5', 'quarter'), ('A5', 'eighth'), ('C5', 'quarter'), ('E5', 'eighth'),
        ('E5', 'quarter'), ('G5', 'eighth'), ('B4', 'quarter'), ('E5', 'eighth'),
        ('D5', 'half'), ('F5', 'eighth'), ('A4', 'eighth'),
        ('C5', 'half'), ('E5', 'eighth'), ('A5', 'eighth'),
        ('D5', 'quarter'), ('F5', 'eighth'), ('A5', 'quarter'), ('F5', 'eighth'),
        ('C5', 'quarter'), ('E5', 'eighth'), ('G5', 'quarter'), ('E5', 'eighth'),
        ('B4', 'half'), ('D5', 'eighth'), ('G4', 'eighth'),
        ('A4', 'half'), ('C5', 'quarter'),
        # 17–24
        ('E5', 'quarter'), ('G5', 'eighth'), ('C6', 'quarter'), ('G5', 'eighth'),
        ('F5', 'quarter'), ('A5', 'eighth'), ('D6', 'quarter'), ('A5', 'eighth'),
        ('G#5', 'quarter'), ('B5', 'eighth'), ('E5', 'quarter'), ('G#5', 'eighth'),
        ('A5', 'half'), ('E5', 'quarter'),
        ('B4', 'half'), ('D5', 'quarter'),
        ('A4', 'half'), ('C5', 'quarter'),
        ('D5', 'quarter'), ('F5', 'eighth'), ('A5', 'quarter'), ('F5', 'eighth'),
        ('E5', 'half'), ('G#4', 'quarter'),
        # 25–32
        ('E5', 'quarter'), ('A4', 'eighth'), ('C5', 'quarter'), ('E5', 'eighth'),
        ('F5', 'quarter'), ('A4', 'eighth'), ('D5', 'quarter'), ('F5', 'eighth'),
        ('G5', 'quarter'), ('C5', 'eighth'), ('E5', 'quarter'), ('G5', 'eighth'),
        ('A5', 'half'), ('E5', 'quarter'),
        ('F5', 'half'), ('D5', 'quarter'),
        ('D5', 'half'), ('B4', 'quarter'),
        ('E5', 'half'), ('G#4', 'quarter'),
        ('A4', 'half'), ('A5', 'quarter'),
    ])
    _add_events(horn, [
        # 1–8
        ('A3', 'quarter'), ('E4', 'eighth'), ('A3', 'quarter'), ('E4', 'eighth'),
        ('A3', 'quarter'), ('F4', 'eighth'), ('A3', 'quarter'), ('F4', 'eighth'),
        ('A3', 'half'), ('C4', 'quarter'),
        ('D4', 'half'), ('A3', 'quarter'),
        ('E4', 'quarter'), ('A3', 'eighth'), ('E4', 'quarter'), ('C4', 'eighth'),
        ('E4', 'quarter'), ('C4', 'eighth'), ('G3', 'quarter'), ('C4', 'eighth'),
        ('B3', 'half'), ('D4', 'quarter'),
        ('A3', 'half'), ('E4', 'quarter'),
        # 9–16
        ('A3', 'quarter'), ('E4', 'eighth'), ('A4', 'quarter'), ('E4', 'eighth'),
        ('G3', 'quarter'), ('E4', 'eighth'), ('G3', 'quarter'), ('E4', 'eighth'),
        ('F3', 'half'), ('A3', 'quarter'),
        ('E3', 'half'), ('A3', 'quarter'),
        ('D4', 'quarter'), ('A3', 'eighth'), ('F3', 'quarter'), ('A3', 'eighth'),
        ('C4', 'quarter'), ('G3', 'eighth'), ('E3', 'quarter'), ('G3', 'eighth'),
        ('G3', 'half'), ('B3', 'quarter'),
        ('A3', 'half'), ('E3', 'quarter'),
        # 17–24
        ('C4', 'quarter'), ('G3', 'eighth'), ('C4', 'quarter'), ('E4', 'eighth'),
        ('D4', 'quarter'), ('A3', 'eighth'), ('D4', 'quarter'), ('F4', 'eighth'),
        ('E4', 'quarter'), ('B3', 'eighth'), ('E4', 'quarter'), ('G#3', 'eighth'),
        ('A3', 'half'), ('E4', 'quarter'),
        ('B3', 'half'), ('F3', 'quarter'),
        ('A3', 'half'), ('E3', 'quarter'),
        ('D4', 'half'), ('A3', 'quarter'),
        ('E4', 'half'), ('B3', 'quarter'),
        # 25–32
        ('A3', 'quarter'), ('E4', 'eighth'), ('A3', 'quarter'), ('E4', 'eighth'),
        ('A3', 'quarter'), ('F4', 'eighth'), ('A3', 'quarter'), ('F4', 'eighth'),
        ('C4', 'quarter'), ('G3', 'eighth'), ('C4', 'quarter'), ('E4', 'eighth'),
        ('A3', 'half'), ('E4', 'quarter'),
        ('D4', 'half'), ('A3', 'quarter'),
        ('B3', 'half'), ('F3', 'quarter'),
        ('E4', 'half'), ('G#3', 'quarter'),
        ('A3', 'half'), ('A3', 'quarter'),
    ])
    _add_events(trumpet, [
        # 1–8
        (None, 'quarter'), ('A4', 'eighth'), ('E5', 'eighth'), ('A4', 'eighth'), (None, 'eighth'),
        (None, 'quarter'), ('A4', 'eighth'), ('F5', 'eighth'), ('A4', 'eighth'), (None, 'eighth'),
        ('E5', 'half'), (None, 'quarter'),
        ('F5', 'half'), (None, 'quarter'),
        (None, 'eighth'), ('A4', 'eighth'), ('C5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'), ('A4', 'eighth'),
        (None, 'eighth'), ('G4', 'eighth'), ('C5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'), ('G4', 'eighth'),
        ('D5', 'half'), (None, 'quarter'),
        ('E5', 'half'), (None, 'quarter'),
        # 9–16
        ('A4', 'eighth'), ('E5', 'eighth'), ('A5', 'eighth'), ('E5', 'eighth'), ('A4', 'quarter'),
        ('G4', 'eighth'), ('E5', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'), ('G4', 'quarter'),
        ('F5', 'half'), (None, 'quarter'),
        ('E5', 'half'), (None, 'quarter'),
        ('D5', 'eighth'), ('A4', 'eighth'), ('F5', 'eighth'), ('D5', 'eighth'), ('A4', 'quarter'),
        ('C5', 'eighth'), ('G4', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'), ('G4', 'quarter'),
        ('B4', 'half'), (None, 'quarter'),
        ('A4', 'half'), (None, 'quarter'),
        # 17–24
        (None, 'eighth'), ('C5', 'eighth'), ('E5', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'),
        (None, 'eighth'), ('D5', 'eighth'), ('F5', 'eighth'), ('A5', 'eighth'), ('F5', 'eighth'), ('D5', 'eighth'),
        (None, 'eighth'), ('E5', 'eighth'), ('G#5', 'eighth'), ('B5', 'eighth'), ('G#5', 'eighth'), ('E5', 'eighth'),
        ('A5', 'half'), (None, 'quarter'),
        ('F5', 'half'), (None, 'quarter'),
        ('E5', 'half'), (None, 'quarter'),
        ('D5', 'eighth'), ('F5', 'eighth'), ('A5', 'eighth'), ('F5', 'eighth'), ('D5', 'quarter'),
        ('E5', 'half'), (None, 'quarter'),
        # 25–32
        (None, 'quarter'), ('A4', 'eighth'), ('E5', 'eighth'), ('A4', 'eighth'), (None, 'eighth'),
        (None, 'quarter'), ('A4', 'eighth'), ('F5', 'eighth'), ('A4', 'eighth'), (None, 'eighth'),
        (None, 'eighth'), ('C5', 'eighth'), ('E5', 'eighth'), ('G5', 'eighth'), ('E5', 'eighth'), ('C5', 'eighth'),
        ('A5', 'half'), (None, 'quarter'),
        ('F5', 'half'), (None, 'quarter'),
        ('D5', 'half'), (None, 'quarter'),
        ('E5', 'eighth'), ('G#5', 'eighth'), ('B4', 'eighth'), ('E5', 'eighth'), (None, 'quarter'),
        ('A4', 'half'), ('A5', 'quarter'),
    ])
    _add_events(viola, [
        # 1–8
        ('A3', 'eighth'), ('E4', 'eighth'), ('A3', 'eighth'), ('C4', 'eighth'), ('E4', 'eighth'), ('A3', 'eighth'),
        ('A3', 'eighth'), ('F4', 'eighth'), ('A3', 'eighth'), ('D4', 'eighth'), ('F4', 'eighth'), ('A3', 'eighth'),
        ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'), ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'),
        ('A3', 'eighth'), ('F4', 'eighth'), ('D4', 'eighth'), ('A3', 'eighth'), ('F4', 'eighth'), ('D4', 'eighth'),
        ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'), ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'),
        ('G3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'), ('G3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'),
        ('G3', 'eighth'), ('D4', 'eighth'), ('B3', 'eighth'), ('G3', 'eighth'), ('D4', 'eighth'), ('B3', 'eighth'),
        ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'), ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'),
        # 9–16
        ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'), ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'),
        ('G3', 'eighth'), ('E4', 'eighth'), ('B3', 'eighth'), ('G3', 'eighth'), ('E4', 'eighth'), ('B3', 'eighth'),
        ('F3', 'eighth'), ('D4', 'eighth'), ('A3', 'eighth'), ('F3', 'eighth'), ('D4', 'eighth'), ('A3', 'eighth'),
        ('E3', 'eighth'), ('C4', 'eighth'), ('A3', 'eighth'), ('E3', 'eighth'), ('C4', 'eighth'), ('A3', 'eighth'),
        ('D3', 'eighth'), ('A3', 'eighth'), ('F4', 'eighth'), ('D3', 'eighth'), ('A3', 'eighth'), ('F4', 'eighth'),
        ('C3', 'eighth'), ('G3', 'eighth'), ('E4', 'eighth'), ('C3', 'eighth'), ('G3', 'eighth'), ('E4', 'eighth'),
        ('G3', 'eighth'), ('D4', 'eighth'), ('B3', 'eighth'), ('G3', 'eighth'), ('D4', 'eighth'), ('B3', 'eighth'),
        ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'), ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'),
        # 17–24
        ('C4', 'eighth'), ('G3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'), ('G3', 'eighth'), ('E4', 'eighth'),
        ('D4', 'eighth'), ('A3', 'eighth'), ('F4', 'eighth'), ('D4', 'eighth'), ('A3', 'eighth'), ('F4', 'eighth'),
        ('E4', 'eighth'), ('B3', 'eighth'), ('G#3', 'eighth'), ('E4', 'eighth'), ('B3', 'eighth'), ('G#3', 'eighth'),
        ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'), ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'),
        ('B3', 'eighth'), ('F4', 'eighth'), ('D4', 'eighth'), ('B3', 'eighth'), ('F4', 'eighth'), ('D4', 'eighth'),
        ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'), ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'),
        ('D4', 'eighth'), ('A3', 'eighth'), ('F4', 'eighth'), ('D4', 'eighth'), ('A3', 'eighth'), ('F4', 'eighth'),
        ('E4', 'eighth'), ('B3', 'eighth'), ('G#4', 'eighth'), ('E4', 'eighth'), ('B3', 'eighth'), ('G#3', 'eighth'),
        # 25–32
        ('A3', 'eighth'), ('E4', 'eighth'), ('A3', 'eighth'), ('C4', 'eighth'), ('E4', 'eighth'), ('A3', 'eighth'),
        ('A3', 'eighth'), ('F4', 'eighth'), ('A3', 'eighth'), ('D4', 'eighth'), ('F4', 'eighth'), ('A3', 'eighth'),
        ('C4', 'eighth'), ('G3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'), ('G3', 'eighth'), ('E4', 'eighth'),
        ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'), ('A3', 'eighth'), ('E4', 'eighth'), ('C4', 'eighth'),
        ('F3', 'eighth'), ('D4', 'eighth'), ('A3', 'eighth'), ('F3', 'eighth'), ('D4', 'eighth'), ('A3', 'eighth'),
        ('D3', 'eighth'), ('B3', 'eighth'), ('F4', 'eighth'), ('D3', 'eighth'), ('B3', 'eighth'), ('F4', 'eighth'),
        ('E3', 'eighth'), ('B3', 'eighth'), ('G#3', 'eighth'), ('E3', 'eighth'), ('B3', 'eighth'), ('G#3', 'eighth'),
        ('A3', 'half'), ('A3', 'quarter'),
    ])
    _add_events(cello, [
        # 1–8
        ('A2', 'quarter'), ('E3', 'eighth'), ('A2', 'quarter'), ('E3', 'eighth'),
        ('A2', 'quarter'), ('F3', 'eighth'), ('A2', 'quarter'), ('F3', 'eighth'),
        ('A2', 'half'), ('C3', 'quarter'),
        ('D3', 'half'), ('A2', 'quarter'),
        ('A2', 'quarter'), ('E3', 'eighth'), ('A2', 'quarter'), ('C3', 'eighth'),
        ('C3', 'quarter'), ('G2', 'eighth'), ('C3', 'quarter'), ('E3', 'eighth'),
        ('G2', 'half'), ('B2', 'quarter'),
        ('A2', 'half'), ('E2', 'quarter'),
        # 9–16
        ('A2', 'quarter'), ('E3', 'eighth'), ('A2', 'quarter'), ('E3', 'eighth'),
        ('E2', 'quarter'), ('B2', 'eighth'), ('E3', 'quarter'), ('B2', 'eighth'),
        ('F2', 'half'), ('D3', 'quarter'),
        ('E2', 'half'), ('C3', 'quarter'),
        ('D2', 'quarter'), ('A2', 'eighth'), ('D3', 'quarter'), ('A2', 'eighth'),
        ('C2', 'quarter'), ('G2', 'eighth'), ('C3', 'quarter'), ('G2', 'eighth'),
        ('G2', 'half'), ('D3', 'quarter'),
        ('A2', 'half'), ('E2', 'quarter'),
        # 17–24
        ('C3', 'quarter'), ('G2', 'eighth'), ('C3', 'quarter'), ('G2', 'eighth'),
        ('D3', 'quarter'), ('A2', 'eighth'), ('D3', 'quarter'), ('A2', 'eighth'),
        ('E3', 'quarter'), ('B2', 'eighth'), ('E2', 'quarter'), ('B2', 'eighth'),
        ('A2', 'half'), ('E3', 'quarter'),
        ('B2', 'half'), ('F2', 'quarter'),
        ('A2', 'half'), ('E2', 'quarter'),
        ('D3', 'half'), ('A2', 'quarter'),
        ('E2', 'half'), ('B2', 'quarter'),
        # 25–32
        ('A2', 'quarter'), ('E3', 'eighth'), ('A2', 'quarter'), ('E3', 'eighth'),
        ('A2', 'quarter'), ('F3', 'eighth'), ('A2', 'quarter'), ('F3', 'eighth'),
        ('C3', 'quarter'), ('G2', 'eighth'), ('C3', 'quarter'), ('E3', 'eighth'),
        ('A2', 'half'), ('E3', 'quarter'),
        ('D3', 'half'), ('A2', 'quarter'),
        ('B2', 'half'), ('G2', 'quarter'),
        ('E2', 'half'), ('G#2', 'quarter'),
        ('A2', 'half'), ('A2', 'quarter'),
    ])
    _add_events(timp, [
        # 1–8
        ('A2', 'quarter'), (None, 'eighth'), ('A2', 'quarter'), (None, 'eighth'),
        (None, 'half'), (None, 'quarter'),
        ('A2', 'half'), (None, 'quarter'),
        ('E3', 'half'), (None, 'quarter'),
        ('A2', 'quarter'), (None, 'eighth'), ('A2', 'eighth'), ('E3', 'eighth'), (None, 'eighth'),
        (None, 'half'), (None, 'quarter'),
        ('E3', 'half'), (None, 'quarter'),
        ('A2', 'half'), (None, 'quarter'),
        # 9–16
        ('A2', 'quarter'), (None, 'eighth'), ('A2', 'quarter'), (None, 'eighth'),
        (None, 'half'), (None, 'quarter'),
        ('D3', 'half'), (None, 'quarter'),
        ('E3', 'half'), (None, 'quarter'),
        (None, 'half'), (None, 'quarter'),
        (None, 'half'), (None, 'quarter'),
        ('E3', 'quarter'), ('E3', 'eighth'), (None, 'quarter'), (None, 'eighth'),
        ('A2', 'half'), (None, 'quarter'),
        # 17–24
        ('C3', 'half'), (None, 'quarter'),
        ('D3', 'half'), (None, 'quarter'),
        ('E3', 'quarter'), ('E3', 'eighth'), ('E3', 'quarter'), (None, 'eighth'),
        ('A2', 'half'), (None, 'quarter'),
        (None, 'half'), (None, 'quarter'),
        (None, 'half'), (None, 'quarter'),
        ('D3', 'half'), (None, 'quarter'),
        ('E3', 'half'), (None, 'quarter'),
        # 25–32
        ('A2', 'quarter'), (None, 'eighth'), ('A2', 'quarter'), (None, 'eighth'),
        (None, 'half'), (None, 'quarter'),
        ('C3', 'half'), (None, 'quarter'),
        ('A2', 'half'), (None, 'quarter'),
        ('D3', 'half'), (None, 'quarter'),
        ('B2', 'half'), (None, 'quarter'),
        ('E3', 'quarter'), ('E3', 'eighth'), ('E3', 'eighth'), (None, 'quarter'),
        ('A2', 'half'), ('A2', 'quarter'),
    ])

    for p in (flute, oboe, horn, trumpet, violin, viola, cello, timp):
        score.insert(0, p)
    return score


def export_musicxml(m21_score, path=MUSICXML_PATH):
    """导出可编辑 MusicXML（可用 MuseScore / Finale / music21 打开）。"""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    # 把调号/拍号/速度写入各声部，避免 music21 导出后丢失
    flat = m21_score.flatten()
    ks = flat.getElementsByClass(key.KeySignature)
    ts = flat.getElementsByClass(meter.TimeSignature)
    mm = flat.getElementsByClass(tempo.MetronomeMark)
    sharps = int(ks[0].sharps) if len(ks) else 0   # 无调号 → C 大调，不是 G 大调
    numer = ts[0].numerator if len(ts) else 4
    denom = ts[0].denominator if len(ts) else 4
    bpm = 126
    for _mm in mm:
        try:
            if _mm.number:
                bpm = int(round(float(_mm.number)))
                break
            # 只有文字速度标记（如 Andante）时按拍时值换算
            qpm = _mm.getQuarterBPM()
            if qpm and 20 < float(qpm) < 400:
                bpm = int(round(float(qpm)))
                break
        except Exception:
            continue
    for part in m21_score.parts:
        if not len(part.getElementsByClass(key.KeySignature)):
            part.insert(0, key.KeySignature(sharps))
        if not len(part.getElementsByClass(meter.TimeSignature)):
            part.insert(0, meter.TimeSignature(f'{numer}/{denom}'))
        if not len(part.getElementsByClass(tempo.MetronomeMark)):
            part.insert(0, tempo.MetronomeMark(number=bpm))
    m21_score.write('musicxml', fp=path)
    return path


def _articulation_onsets(m21_score):
    """([(part_idx, onset_ql)] staccato, [(part_idx, onset_ql)] accent) — MIDI 后处理用。"""
    stacc: list = []
    accent: list = []
    try:
        from music21 import articulations as m21art
        parts = list(m21_score.parts) if hasattr(m21_score, 'parts') else [m21_score]
        for pi, part in enumerate(parts):
            for n in part.flatten().notes:
                arts = getattr(n, 'articulations', [])
                if any(isinstance(a, (m21art.Staccato, m21art.Staccatissimo)) for a in arts):
                    stacc.append((pi, round(float(n.offset), 4)))
                if any(isinstance(a, m21art.Accent) for a in arts):
                    accent.append((pi, round(float(n.offset), 4)))
    except Exception:
        pass
    return stacc, accent


def _apply_velocities_to_midi(path, m21_score):
    """music21 导出 MIDI 会按 Dynamic 上下文重算 velocity（忽略每音显式值，
    pp→31 之类会被压得听不见）。导出后按谱内每音的 velocity 重写，
    恢复 DYNAMIC_VELOCITIES 力度映射与标记间渐变。"""
    try:
        import pretty_midi
        pm = pretty_midi.PrettyMIDI(path)
        parts = list(m21_score.parts) if hasattr(m21_score, 'parts') else [m21_score]
        for pi, part in enumerate(parts):
            if pi >= len(pm.instruments):
                break
            for n in part.recurse().notes:
                vel = getattr(getattr(n, 'volume', None), 'velocity', None)
                if not vel:
                    continue
                pitches = [p.midi for p in n.pitches] if hasattr(n, 'pitches') \
                    else [n.pitch.midi]
                try:
                    t0 = pm.tick_to_time(
                        int(round(float(n.getOffsetInHierarchy(m21_score))
                                  * pm.resolution)))
                except Exception:
                    continue
                for pn in pm.instruments[pi].notes:
                    if abs(pn.start - t0) < 0.02 and pn.pitch in pitches:
                        pn.velocity = max(1, min(127, int(vel)))
        pm.write(path)
    except Exception:
        pass


def _apply_articulations_to_midi(path, m21_score):
    """music21 导出 MIDI 不处理 staccato/accent：本地截短/加重标记音符。

    staccato → 时值截到 ~55%；accent → velocity 抬 25%（cap 127）。"""
    stacc, accent = _articulation_onsets(m21_score)
    if not stacc and not accent:
        return
    try:
        import pretty_midi
        pm = pretty_midi.PrettyMIDI(path)
        def _note_at(pi, ql):
            if pi >= len(pm.instruments):
                return None
            try:
                t0 = pm.tick_to_time(int(round(ql * pm.resolution)))
            except Exception:
                return None
            for n in pm.instruments[pi].notes:
                if abs(n.start - t0) < 0.02:
                    return n
            return None
        for pi, ql in stacc:
            n = _note_at(pi, ql)
            if n is not None:
                dur = n.end - n.start
                if dur > 1e-4:
                    n.end = n.start + dur * 0.55
        for pi, ql in accent:
            n = _note_at(pi, ql)
            if n is not None:
                n.velocity = min(127, int(round(n.velocity * 1.25)) + 3)
        pm.write(path)
    except Exception:
        pass


def export_midi(m21_score, path=MIDI_PATH, groove=None):
    """从总谱导出标准 MIDI（力度重写 + 断奏截短/重音加重 + 可选 Groove 后处理）。"""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    m21_score.write('midi', fp=path)
    _apply_velocities_to_midi(path, m21_score)
    _apply_articulations_to_midi(path, m21_score)
    if groove and groove.get('on'):
        try:
            from compose_groove import apply_groove_to_midi
            apply_groove_to_midi(
                path,
                swing=float(groove.get('swing', 0.0)),
                humanize=float(groove.get('humanize', 0.35)),
                seed=groove.get('seed', 'groove'))
        except Exception:
            pass
    return path


def export_wav(audio, sample_rate=SAMPLE_RATE, path=WAV_PATH):
    """把合成波形写成 16-bit PCM WAV。"""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1.0:
        audio = audio / peak * 0.99
    pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm.tobytes())
    return path


def _fftconvolve(x, ir):
    """numpy 版快速卷积（避免依赖 scipy）。"""
    n = len(x) + len(ir) - 1
    nfft = 1 << (n - 1).bit_length()
    X = np.fft.rfft(x, nfft)
    H = np.fft.rfft(ir, nfft)
    return np.fft.irfft(X * H, nfft)[:n]   # 线性卷积长度恰为 n


# 钢琴专属音色库（仅当 MIDI 只含钢琴族乐器时优先使用）
PIANO_SOUNDFONTS = [
    os.path.join('soundfonts', 'SalamanderGrandPiano.sf2'),
]

# 全局渲染锁：FluidSynth 每次渲染都要解析整个 SoundFont（Salamander 1.1GB），
# 多线程并发渲染会导致内存峰值翻倍（曾致 macOS 内存压力下进程被杀）且
# libfluidsynth 并发加载同一 SF2 可能段错误。所有渲染必须串行。
_RENDER_LOCK = threading.Lock()


def _atomic_json_dump(path: str, data: Any, **dump_kw) -> None:
    """Write JSON via tempfile + os.replace so crash mid-write cannot truncate."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    kw = {'ensure_ascii': False, 'indent': 2}
    kw.update(dump_kw)
    fd, tmp = tempfile.mkstemp(
        prefix='.tmp_', suffix='.json',
        dir=os.path.dirname(os.path.abspath(path)) or '.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, **kw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

def _is_piano_midi(pm) -> bool:
    """MIDI 全部为钢琴族（program 0-7）且无打击轨时返回 True。"""
    try:
        if not pm.instruments:
            return False
        for inst in pm.instruments:
            if inst.is_drum or inst.program > 7:
                return False
        return True
    except Exception:
        return False


def _apply_reverb(audio, sr=SAMPLE_RATE, wet=0.22, tail=1.6, seed=42):
    """轻量卷积混响：给 FluidSynth 干声加空间感，显著降低'MIDI 味'。"""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return audio
    try:
        peak_in = float(np.max(np.abs(audio)))
        if peak_in <= 1e-9:
            return audio
        ir_len = max(int(sr * tail), 64)
        rng = np.random.default_rng(seed)
        t = np.arange(ir_len, dtype=np.float32) / float(sr)
        # 指数衰减噪声脉冲响应（模拟房间反射尾音）
        ir = (rng.standard_normal(ir_len).astype(np.float32)
              * np.exp(-t * 3.2).astype(np.float32))
        # 一点点预延迟，避免直接声被糊住
        pre = max(int(sr * 0.012), 1)
        ir[:pre] = 0.0
        ir /= (float(np.max(np.abs(ir))) + 1e-9)
        wet_sig = _fftconvolve(audio, ir)[: len(audio)].astype(np.float32)
        wet_peak = float(np.max(np.abs(wet_sig)))
        if wet_peak > 1e-9:
            wet_sig = wet_sig / wet_peak * peak_in
        out = audio * (1.0 - wet) + wet_sig * wet
        out_peak = float(np.max(np.abs(out)))
        if out_peak > 0.999:
            out = out / out_peak * 0.999
        return out.astype(np.float32)
    except Exception:
        return audio


def render_midi_to_audio(midi_path, sf2_path=SOUNDFONT, sample_rate=SAMPLE_RATE):
    """用 MIDI + SoundFont 做模拟播放波形（含轻混响）；失败则按候选音色库逐个回退，仍失败返回 None。
    纯钢琴曲目自动优先使用 Salamander Grand Piano 采样音色。"""
    if not os.path.exists(midi_path):
        return None
    try:
        pm = pretty_midi.PrettyMIDI(midi_path)
    except Exception:
        pm = None
    # 候选排序：纯钢琴曲目 → 钢琴采样优先；否则 GM 全乐器音色优先
    gm_cands = [sf2_path] + [p for p in SOUNDFONT_CANDIDATES if p != sf2_path]
    piano_cands = [p for p in PIANO_SOUNDFONTS if os.path.exists(p)]
    if pm is not None and _is_piano_midi(pm) and piano_cands:
        candidates = piano_cands + gm_cands
    else:
        candidates = gm_cands
    for sf_path in candidates:
        if not sf_path or not os.path.exists(sf_path):
            continue
        audio = None
        try:
            with _RENDER_LOCK:
                if pm is None:
                    pm = pretty_midi.PrettyMIDI(midi_path)
                audio = pm.fluidsynth(sf2_path=sf_path, fs=sample_rate)
            if audio is None or len(audio) == 0:
                audio = None
        except Exception:
            audio = None
        # python fluidsynth 不可用（本环境常态）→ 回退到系统 FluidSynth 二进制
        if audio is None:
            try:
                from render_enhance import _find_binary, _render_via_binary
                binp = _find_binary()
                if binp:
                    with _RENDER_LOCK:
                        res = _render_via_binary(midi_path, sf_path, sample_rate, binp)
                    if res is not None:
                        audio, _sr = res
            except Exception:
                audio = None
        if audio is None or len(audio) == 0:
            continue
        audio = _apply_reverb(np.asarray(audio, dtype=np.float32), sample_rate)
        return np.asarray(audio, dtype=np.float32)
    return None


def write_score_bundle(m21_score, audio=None, sample_rate=SAMPLE_RATE,
                       musicxml_path=None, midi_path=None, wav_path=None):
    """写出 MusicXML + MIDI，若有音频再写 WAV。返回写入路径字典。"""
    musicxml_path = musicxml_path or MUSICXML_PATH
    midi_path = midi_path or MIDI_PATH
    wav_path = wav_path or WAV_PATH
    paths = {
        'musicxml': export_musicxml(m21_score, musicxml_path),
        'midi': export_midi(m21_score, midi_path),
    }
    if audio is not None:
        paths['wav'] = export_wav(audio, sample_rate, wav_path)
    return paths


def ensure_bach_bundle(force=False):
    """从 music21 语料库导出巴赫《平均律》第一册 C 大调前奏曲（BWV 846）。"""
    os.makedirs('scores', exist_ok=True)
    if (not force and os.path.exists(BACH_MUSICXML_PATH) and os.path.exists(BACH_MIDI_PATH)
            and os.path.getsize(BACH_MUSICXML_PATH) > 100):
        return BACH_MUSICXML_PATH
    from music21 import corpus
    sc = corpus.parse('bach/bwv846.mxl')
    if sc.metadata is None:
        sc.metadata = m21meta.Metadata()
    sc.metadata.title = '平均律钢琴曲集 I · 前奏曲 C 大调'
    sc.metadata.composer = 'J. S. Bach'
    sc.metadata.movementName = 'BWV 846 Prelude'
    # 去掉语料里可能无效的速度标记，统一为 ♩=72
    for mm in list(sc.recurse().getElementsByClass(tempo.MetronomeMark)):
        sc.remove(mm, recurse=True)
    sc.insert(0, tempo.MetronomeMark(number=72))
    if not list(sc.recurse().getElementsByClass(key.KeySignature)):
        sc.insert(0, key.KeySignature(0))
    if not list(sc.recurse().getElementsByClass(meter.TimeSignature)):
        sc.insert(0, meter.TimeSignature('4/4'))
    sc.write('musicxml', fp=BACH_MUSICXML_PATH)
    sc.write('midi', fp=BACH_MIDI_PATH)
    return BACH_MUSICXML_PATH


# ============================================================
# music21 -> 项目模型 转换
# ============================================================

def _convert_pitch(p) -> Pitch:
    name = NoteName[p.step]
    acc = {0: Accidental.NATURAL, 1: Accidental.SHARP, -1: Accidental.FLAT,
           2: Accidental.DOUBLE_SHARP, -2: Accidental.DOUBLE_FLAT}.get(int(p.alter), Accidental.NATURAL)
    return Pitch(name, p.octave, acc)


def _clef_type_of(c) -> Optional[ClefType]:
    """music21 谱号对象 → ClefType（按 sign/line/octaveChange 判断）。"""
    if c is None:
        return None
    sign = getattr(c, 'sign', None)
    line = getattr(c, 'line', None)
    octave = int(getattr(c, 'octaveChange', 0) or 0)
    if sign == 'F':
        return ClefType.BASS_8VB if octave == -1 else ClefType.BASS
    if sign == 'C':
        return ClefType.TENOR if line == 4 else ClefType.ALTO
    if sign == 'G':
        return ClefType.TREBLE_8VB if octave == -1 else ClefType.TREBLE
    return ClefType.TREBLE


def _convert_clef(part) -> ClefType:
    try:
        c = part.flatten().getElementsByClass(m21clef.Clef)[0]
        return _clef_type_of(c) or ClefType.TREBLE
    except Exception:
        pass
    return ClefType.TREBLE


def _part_name(part, idx) -> str:
    try:
        for inst in part.flatten().getElementsByClass(instrument.Instrument):
            raw = (inst.instrumentName or '').strip()
            if raw and raw != 'Instrument':
                zh = PART_LABELS.get(raw, raw)
                return zh if zh == raw else f'{zh} ({raw})'
    except Exception:
        pass
    return f'声部 {idx + 1}'


def _part_measure_grid(part, base_ts=None):
    """小节网格 [(start_ql, length_ql, ts_obj|None)]。

    优先用真实 Measure 对象（converter.parse 的谱都有，能正确处理曲中换拍号）；
    扁平流（spec_to_music21 产物）按 TimeSignature 元素偏移推导；再退化为恒定 4/4。
    base_ts：声部内没有 offset-0 拍号对象时的起始拍号（用乐谱全局首个拍号）。
    """
    measures = []
    try:
        if isinstance(part, stream.Part):
            measures = list(part.getElementsByClass(stream.Measure))
    except Exception:
        pass
    if measures:
        grid = []
        for i, m in enumerate(measures):
            start = float(m.offset)
            try:
                length = float(m.barDuration.quarterLength)
            except Exception:
                length = 4.0
            if length <= 0:
                length = 4.0
            # 弱起/不完整小节：实际长度以下一小节起点为准，避免吞掉下一小节的音
            if i + 1 < len(measures):
                nxt = float(measures[i + 1].offset) - start
                if 1e-9 < nxt < length - 1e-9:
                    length = nxt
            grid.append((start, length, m.timeSignature))
        return grid
    elements = list(part.flatten().notesAndRests)
    if not elements:
        return []
    end = max(float(e.offset) + float(e.quarterLength) for e in elements)
    ts_items = sorted(
        [(float(t.offset), t) for t in part.flatten().getElementsByClass(meter.TimeSignature)],
        key=lambda x: x[0])
    if not ts_items or ts_items[0][0] > 1e-9:
        ts_items.insert(0, (0.0, base_ts or meter.TimeSignature('4/4')))
    grid = []
    t = 0.0
    while t < end - 1e-9:
        cur = ts_items[0][1]
        for off, tsx in ts_items:
            if off <= t + 1e-9:
                cur = tsx
        try:
            length = float(cur.barDuration.quarterLength)
        except Exception:
            length = 4.0
        if length <= 0:
            length = 4.0
        grid.append((t, length, cur))
        t += length
    return grid


def _metronome_display(mm) -> str:
    text = (getattr(mm, 'text', '') or '').strip()
    num = getattr(mm, 'number', None)
    try:
        num = int(round(float(num))) if num is not None else None
    except (TypeError, ValueError):
        num = None
    if text and num:
        return f'{text} ♩={num}'
    if num:
        return f'♩={num}'
    return text


_TEMPO_WORD_RE = re.compile(
    r'^(grave|largo|larghetto|lento|adagio|adagietto|andante|andantino|moderato|'
    r'allegretto|allegro|vivace|vivacissimo|presto|prestissimo|tempo|a tempo|'
    r'rit|ritard|ritardando|rall|rallentando|accel|accelerando|rubato|meno|più|piu|'
    r'l\'istesso|swing|ballad|medium|up|slow|fast)\b', re.IGNORECASE)


def _is_tempo_words(txt: str) -> bool:
    return bool(_TEMPO_WORD_RE.match((txt or '').strip())) or '♩' in (txt or '')


def _barline_style(b, side: str) -> Optional[str]:
    """music21 Barline/Repeat → 模型小节线样式。"""
    if b is None:
        return None
    if type(b).__name__ == 'Repeat':
        d = getattr(b, 'direction', None)
        if d == 'start' and side == 'left':
            return 'repeat_start'
        if d == 'end' and side == 'right':
            return 'repeat_end'
        return None
    typ = getattr(b, 'type', None)
    return {'double': 'double', 'light-light': 'double', 'final': 'final',
            'light-heavy': 'final', 'dashed': 'dashed'}.get(typ)


def m21_to_model(m21_score, title, composer=''):
    flat = m21_score.flatten()
    ts_list = flat.getElementsByClass(meter.TimeSignature)
    ks_list = flat.getElementsByClass(key.KeySignature)
    mm_list = flat.getElementsByClass(tempo.MetronomeMark)
    ts = ts_list[0] if len(ts_list) else meter.TimeSignature('4/4')
    # 全曲调号只看开头的；曲中换调由各小节处理（无开头调号 = C 大调）
    ks0 = [k for k in ks_list if float(k.offset) < 1e-9]
    ks = ks0[0] if ks0 else key.KeySignature(0)
    bpm = 90
    if len(mm_list):
        n = getattr(mm_list[0], 'number', None)
        if n is not None:
            try:
                bpm = float(n)
            except (TypeError, ValueError):
                bpm = 90

    def _ts_model(t):
        sym = getattr(t, 'symbol', '') or ''
        return TimeSignature(t.numerator, t.denominator,
                             symbol=sym if sym in ('common', 'cut') else None)

    model = Score(title=title, composer=composer, tempo=int(round(bpm)),
                  time_signature=_ts_model(ts),
                  key_signature=KeySignature(int(getattr(ks, 'sharps', 0) or 0)))
    model.staves = []

    from music21 import dynamics as m21dynamics
    from music21 import expressions as m21expressions
    from music21 import spanner as m21spanner
    from music21 import harmony as m21harmony

    parts = list(m21_score.parts) if hasattr(m21_score, 'parts') and len(m21_score.parts) else [m21_score]
    for part_idx, part in enumerate(parts):
        staff = Staff(clef=_convert_clef(part), name=_part_name(part, part_idx))
        grid = _part_measure_grid(part, ts)
        flatpart = part.flatten()
        elements = list(flatpart.notesAndRests)
        try:
            mobjs = list(part.getElementsByClass(stream.Measure)) \
                if isinstance(part, stream.Part) else []
        except Exception:
            mobjs = []
        if len(mobjs) != len(grid):
            mobjs = []

        def _cell_of(offset):
            for gi, (start, length, _ts) in enumerate(grid):
                if start - 1e-9 <= offset < start + length - 1e-9:
                    return gi
            return None

        # 元素按网格分小节
        groups = {}
        for el in elements:
            gi = _cell_of(float(el.offset))
            if gi is not None:
                groups.setdefault(gi, []).append(el)

        def _by_measure(cls, value_fn):
            """{gi: [(local_offset, value)]}，按偏移排序。"""
            out = {}
            for obj in flatpart.getElementsByClass(cls):
                off = float(obj.offset)
                gi = _cell_of(off)
                if gi is None:
                    continue
                v = value_fn(obj)
                if v is not None and v != '':
                    out.setdefault(gi, []).append((off - grid[gi][0], v))
            for v in out.values():
                v.sort(key=lambda x: x[0])
            return out

        dyn_by_measure = _by_measure(m21dynamics.Dynamic, lambda d: str(d.value))
        chord_sym_by_measure = _by_measure(
            m21harmony.ChordSymbol,
            lambda c: (getattr(c, 'figure', None) or '').strip() or None)
        rehearsal_by_measure = _by_measure(
            m21expressions.RehearsalMark,
            lambda r: (str(getattr(r, 'content', '') or '')).strip() or None)
        clef_by_measure = _by_measure(m21clef.Clef, _clef_type_of)
        key_by_measure = _by_measure(
            key.KeySignature, lambda k: int(getattr(k, 'sharps', 0) or 0))

        # 文字：与 MetronomeMark 同位置或像速度术语的 → 速度标记；其余 → 文字指示
        mm_bpm_by_off = {}
        tempo_txt_by_off, text_by_measure = {}, {}
        for mm in flatpart.getElementsByClass(tempo.MetronomeMark):
            off = round(float(mm.offset), 6)
            n = getattr(mm, 'number', None)
            try:
                n = int(round(float(n))) if n is not None else None
            except (TypeError, ValueError):
                n = None
            mm_bpm_by_off[off] = n
            if not getattr(mm, 'textImplicit', True):
                mtxt = (getattr(mm, 'text', None) or '').strip()
                if mtxt:
                    tempo_txt_by_off[off] = mtxt
        tes = []
        for te in flatpart.getElementsByClass(m21expressions.TextExpression):
            txt = (getattr(te, 'content', '') or '').strip()
            if txt:
                tes.append((round(float(te.offset), 6), txt))
        tes.sort(key=lambda x: not _is_tempo_words(x[1]))   # 速度术语优先认领速度位
        for off, txt in tes:
            if _is_tempo_words(txt) or (off in mm_bpm_by_off and off not in tempo_txt_by_off):
                tempo_txt_by_off.setdefault(off, txt)
            else:
                gi = _cell_of(off)
                if gi is not None and gi not in text_by_measure:
                    text_by_measure[gi] = txt
        tm_by_measure, bpm_by_measure = {}, {}
        if part_idx == 0:
            for off in sorted(set(tempo_txt_by_off) | set(mm_bpm_by_off)):
                text = tempo_txt_by_off.get(off)
                b = mm_bpm_by_off.get(off)
                if off < 1e-9 and not text:
                    continue  # 全局基础速度（纯数值），不画谱面
                if not text and b is None:
                    continue
                if text and b is not None:
                    disp = f'{text} ♩={b}'
                elif text:
                    disp = text
                else:
                    disp = f'♩={b}'
                gi = _cell_of(off)
                if gi is not None:
                    tm_by_measure.setdefault(gi, disp)
                    if b is not None:
                        bpm_by_measure.setdefault(gi, b)

        # 房子号：RepeatBracket 覆盖的小节
        volta_by_mobj = {}
        try:
            for rb in part.spannerBundle.getByClass(m21spanner.RepeatBracket):
                label = str(getattr(rb, 'number', '') or '').strip()
                for mo in rb.getSpannedElements():
                    volta_by_mobj[id(mo)] = label or '1'
        except Exception:
            pass

        el_map = {}  # id(el) -> (measure_idx, note_idx)
        prev_ts_obj = None
        cur_clef = staff.clef
        cur_key = model.key_signature.sharps
        pickup = False
        for gi, (start, length, ts_obj) in enumerate(grid):
            m = Measure(number=gi + 1)
            mo = mobjs[gi] if mobjs else None
            # 曲中换拍号：本小节拍号对象与前一小节不同（首小节不算）
            if gi > 0 and ts_obj is not None and ts_obj is not prev_ts_obj:
                try:
                    m.time_signature = _ts_model(ts_obj)
                except Exception:
                    pass
            if ts_obj is not None:
                prev_ts_obj = ts_obj
            try:
                full = float(ts_obj.barDuration.quarterLength) if ts_obj is not None else length
            except Exception:
                full = length
            if gi == 0 and mo is not None and float(getattr(mo, 'paddingLeft', 0) or 0) > 0:
                m.implicit = True
            elif length < full - 1e-6:
                m.implicit = True
            if gi == 0 and m.implicit:
                pickup = True
            if pickup:
                m.number = gi
            # 换谱号 / 换调（小节首）
            for loc, ct in clef_by_measure.get(gi, []):
                if loc < 1e-6 and ct is not None and ct != cur_clef:
                    m.clef = ct
                    cur_clef = ct
            for loc, sh in key_by_measure.get(gi, []):
                if loc < 1e-6 and gi > 0 and sh != cur_key:
                    m.key_signature = KeySignature(sh)
                    cur_key = sh
            if mo is not None:
                m.barline_left = _barline_style(getattr(mo, 'leftBarline', None), 'left')
                m.barline_right = _barline_style(getattr(mo, 'rightBarline', None), 'right')
                m.volta = volta_by_mobj.get(id(mo))
            notes, el_to_idx = _elements_to_linear_notes(groups.get(gi, []), start, length)
            m.notes = notes
            offs = _note_offsets(notes)

            def _note_at(loc):
                """loc 处开始（或之后第一个）的非倚音音符。"""
                for k, (o, nn) in enumerate(zip(offs, notes)):
                    if nn.grace:
                        continue
                    if o >= loc - 1e-6:
                        return nn
                return None

            for k, (loc, dv) in enumerate(dyn_by_measure.get(gi, [])):
                if k == 0 and loc < 1e-6:
                    m.dynamic = dv
                    continue
                tgt = _note_at(loc)
                if tgt is not None and not tgt.dynamic:
                    tgt.dynamic = dv
            for loc, fig in chord_sym_by_measure.get(gi, []):
                tgt = _note_at(loc)
                if tgt is not None and not tgt.chord_symbol:
                    tgt.chord_symbol = fig
            rh = rehearsal_by_measure.get(gi)
            if rh:
                m.rehearsal = rh[0][1]
            m.text = text_by_measure.get(gi)
            m.tempo_mark = tm_by_measure.get(gi)
            m.tempo_bpm = bpm_by_measure.get(gi)
            for el, idxs in el_to_idx.items():
                if idxs:
                    el_map[el] = (gi, idxs[0], idxs[-1])
            staff.measures.append(m)

        def _note_ref(el, last=False):
            ref = el_map.get(id(el))
            if ref is None or ref[0] >= len(staff.measures):
                return None
            notes = staff.measures[ref[0]].notes
            idx = ref[2] if last else ref[1]
            return notes[idx] if idx < len(notes) else None

        # 圆滑线 / 渐强渐弱：spanner 起止音符 → 对应模型音符标记
        try:
            for sp in flatpart.getElementsByClass(m21spanner.Slur):
                spans = list(sp)
                if len(spans) < 2:
                    continue
                a, b = _note_ref(spans[0]), _note_ref(spans[-1], last=True)
                if b is not None and b.is_rest:
                    # 圆滑线不挂休止符：终点回退到之前最近的发声音符
                    seq = [n for mm in staff.measures for n in mm.notes]
                    k = next((i for i, n in enumerate(seq) if n is b), None)
                    b = None
                    while k is not None and k > 0:
                        k -= 1
                        if seq[k] is a:
                            break
                        if not seq[k].is_rest:
                            b = seq[k]
                            break
                if a is not None and b is not None and a is not b and not a.is_rest:
                    a.slur_start = True
                    b.slur_end = True
        except Exception:
            pass
        try:
            for cls, kind in ((m21dynamics.Crescendo, 'crescendo'),
                              (m21dynamics.Diminuendo, 'diminuendo')):
                for sp in flatpart.getElementsByClass(cls):
                    spans = list(sp)
                    if not spans:
                        continue
                    a, b = _note_ref(spans[0]), _note_ref(spans[-1], last=True)
                    if a is not None and b is not None:
                        a.hairpin_start = kind
                        b.hairpin_end = True
        except Exception:
            pass
        model.staves.append(staff)
    # 钢琴大谱表：RH/LH 或两行 Piano → 花括号分组
    if len(model.staves) == 2:
        names = [(s.name or '').lower() for s in model.staves]
        if any('piano' in n or n in ('rh', 'lh') for n in names):
            model.staff_groups = [
                {'parts': [0, 1], 'symbol': 'brace', 'name': 'Piano'}]
    return model


# ============================================================
# Mel 频谱（纯 numpy 实现）
# ============================================================

def _hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + np.asarray(f) / 700.0)


def _mel_to_hz(m):
    return 700.0 * (10.0 ** (np.asarray(m) / 2595.0) - 1.0)


def _mel_filterbank(sr, n_fft, n_mels, fmin, fmax):
    n_freq = n_fft // 2 + 1
    fft_freqs = np.linspace(0, sr / 2, n_freq)
    mel_pts = np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2)
    hz_pts = _mel_to_hz(mel_pts)
    fb = np.zeros((n_mels, n_freq))
    for m in range(n_mels):
        lo, c, hi = hz_pts[m], hz_pts[m + 1], hz_pts[m + 2]
        up = (fft_freqs - lo) / max(c - lo, 1e-9)
        down = (hi - fft_freqs) / max(hi - c, 1e-9)
        fb[m] = np.maximum(0, np.minimum(up, down))
    return fb


def log_mel_spectrogram(audio, sr, n_fft=2048, hop=512, n_mels=64,
                        fmin=30.0, fmax=8000.0):
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.asarray(audio, dtype=np.float64)
    win = np.hanning(n_fft)
    n_frames = max(1, 1 + (len(audio) - n_fft) // hop)
    spec = np.zeros((n_fft // 2 + 1, n_frames))
    for i in range(n_frames):
        seg = audio[i * hop: i * hop + n_fft]
        if len(seg) < n_fft:
            seg = np.pad(seg, (0, n_fft - len(seg)))
        spec[:, i] = np.abs(np.fft.rfft(seg * win)) ** 2
    fb = _mel_filterbank(sr, n_fft, n_mels, fmin, min(fmax, sr / 2))
    mel = fb @ spec
    return 10.0 * np.log10(mel + 1e-10)


def stft_spectrogram_db(audio, sr, n_fft=2048, hop=512):
    """线性频率 STFT 功率谱（dB）。"""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.asarray(audio, dtype=np.float64)
    win = np.hanning(n_fft)
    n_frames = max(1, 1 + (len(audio) - n_fft) // hop)
    spec = np.zeros((n_fft // 2 + 1, n_frames))
    for i in range(n_frames):
        seg = audio[i * hop: i * hop + n_fft]
        if len(seg) < n_fft:
            seg = np.pad(seg, (0, n_fft - len(seg)))
        spec[:, i] = np.abs(np.fft.rfft(seg * win)) ** 2
    return 10.0 * np.log10(spec + 1e-10)


def chromagram(audio, sr, n_fft=2048, hop=512):
    """12 音级色度图（按帧归一化）。"""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.asarray(audio, dtype=np.float64)
    win = np.hanning(n_fft)
    n_frames = max(1, 1 + (len(audio) - n_fft) // hop)
    chroma = np.zeros((12, n_frames))
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    for i in range(n_frames):
        seg = audio[i * hop: i * hop + n_fft]
        if len(seg) < n_fft:
            seg = np.pad(seg, (0, n_fft - len(seg)))
        mag = np.abs(np.fft.rfft(seg * win))
        for k, f in enumerate(freqs):
            if f < 50 or f > min(5000, sr / 2):
                continue
            midi = 69 + 12 * np.log2(f / 440.0)
            pc = int(round(midi)) % 12
            chroma[pc, i] += mag[k]
        s = chroma[:, i].sum()
        if s > 0:
            chroma[:, i] /= s
    return chroma


def rms_envelope(audio, sr, hop=512, win=2048):
    """短时 RMS 包络。"""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.asarray(audio, dtype=np.float64)
    n_frames = max(1, 1 + (len(audio) - win) // hop)
    env = np.zeros(n_frames)
    for i in range(n_frames):
        seg = audio[i * hop: i * hop + win]
        if len(seg) < win:
            seg = np.pad(seg, (0, win - len(seg)))
        env[i] = np.sqrt(np.mean(seg ** 2))
    times = (np.arange(n_frames) * hop + win / 2) / float(sr)
    return times, env


def load_score_from_musicxml(path, fallback_title='Untitled'):
    """从 MusicXML 解析并转换为项目 Score 模型（五线谱显示的唯一来源）。"""
    parsed = m21converter.parse(path)
    if not isinstance(parsed, stream.Score):
        score = stream.Score()
        score.insert(0, parsed)
        parsed = score
    title = fallback_title
    try:
        md = parsed.metadata
        if md:
            for cand in (
                getattr(md, 'bestTitle', None),
                md.title,
                getattr(md, 'movementName', None),
            ):
                if cand and str(cand).strip():
                    title = str(cand).strip()
                    break
    except Exception:
        pass
    composer = ''
    try:
        if parsed.metadata and parsed.metadata.composer:
            composer = str(parsed.metadata.composer)
    except Exception:
        pass
    model = m21_to_model(parsed, title=title, composer=composer or 'Score Studio')
    flat = parsed.flatten()
    # MusicXML 往返可能丢失调号 / 速度：按本乐章默认恢复
    if len(flat.getElementsByClass(key.KeySignature)) == 0:
        model.key_signature = KeySignature(1)
        try:
            parsed.insert(0, key.KeySignature(1))
        except Exception:
            pass
    if len(flat.getElementsByClass(tempo.MetronomeMark)) == 0:
        restored = None
        midi_guess = os.path.splitext(path)[0] + '.mid'
        for cand in (midi_guess, MIDI_PATH):
            if not os.path.exists(cand):
                continue
            try:
                pm = pretty_midi.PrettyMIDI(cand)
                _times, tempos = pm.get_tempo_changes()
                if len(tempos):
                    restored = int(round(float(tempos[0])))
                    break
            except Exception:
                pass
        model.tempo = restored or 126
        try:
            parsed.insert(0, tempo.MetronomeMark(number=model.tempo))
        except Exception:
            pass
    return parsed, model


def _staff_preset(name: str) -> str:
    """按声部名（乐器名，中/英）映射到软件合成器音色 preset。"""
    low = (name or '').lower()
    first = low.split()[0] if low.split() else ''
    if '圆号' in name or '法国号' in name or 'horn' in low:
        return 'horn'
    if '长笛' in name or 'flute' in low:
        return 'flute'
    if '双簧管' in name or 'oboe' in low:
        return 'oboe'
    if '小号' in name or 'trumpet' in low:
        return 'trumpet'
    if '中提' in name or 'viola' in low:
        return 'viola'
    if '大提' in name or 'cello' in low:
        return 'cello'
    if '定音' in name or 'timp' in low:
        return 'timpani'
    if '小提' in name or 'violin' in low:
        return 'violin'
    if '低音提' in name or 'contrabass' in low or 'doublebass' in low:
        return 'contrabass'
    if ('弦' in name or 'string' in low) and '低音' not in name:
        return 'strings'
    if first in ('soprano', 'alto', 'tenor', 'bass', 'voice') \
            or 'choir' in low or '合唱' in name or '人声' in name:
        return 'choir'
    return 'piano'


def synthesize_software(score, sr=44100):
    """不依赖 FluidSynth 的离线合成，保证播放和 Mel 频谱可用。"""
    from music_score.audio_engine import AudioEngine
    engine = AudioEngine()
    presets = [_staff_preset(staff.name) for staff in score.staves]
    spb = 60.0 / max(int(score.tempo), 1)
    events = []
    end = 0.0
    for staff, preset in zip(score.staves, presets):
        t = 0.0
        for measure in staff.measures:
            for note in measure.notes:
                dur = note.duration_value() * 4.0 * spb
                if not note.is_rest:
                    for pitch in [note.pitch] + list(note.chord_tones):
                        events.append((t, max(dur, 0.05), pitch.to_midi(), preset))
                t += dur
        end = max(end, t)
    total = int((end + 0.6) * sr) + 1
    mix = np.zeros(total, dtype=np.float32)
    for t, dur, midi, preset in events:
        cfg = engine.ORCHESTRA_PRESETS[preset]
        freq = 440.0 * (2.0 ** ((midi - 69) / 12.0))
        wave_buf = engine._generate_wave(freq, dur, cfg, 84)
        i0 = int(round(t * sr))
        i1 = min(i0 + len(wave_buf), total)
        if i1 > i0:
            mix[i0:i1] += wave_buf[:i1 - i0]
    peak = float(np.max(np.abs(mix))) if total else 0.0
    if peak > 1e-8:
        mix = mix / peak * 0.9
    return mix


def load_audio_file(path: str):
    """加载本地音频，返回 (mono float32, sample_rate)。优先 WAV，其它格式尝试 soundfile。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.wav', '.wave'):
        with wave.open(path, 'rb') as wf:
            sr = wf.getframerate()
            n_ch = wf.getnchannels()
            width = wf.getsampwidth()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
        if width == 1:
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif width == 2:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif width == 3:
            # 24-bit PCM：按小端三字节拆开
            buf = array('B', raw)
            if len(buf) % 3:
                raise ValueError('24-bit WAV 数据长度不是 3 的倍数')
            ints = []
            for i in range(0, len(buf), 3):
                v = buf[i] | (buf[i + 1] << 8) | (buf[i + 2] << 16)
                if v & 0x800000:
                    v -= 0x1000000
                ints.append(v)
            data = np.asarray(ints, dtype=np.float32) / 8388608.0
        elif width == 4:
            # 可能是 32-bit PCM 或 float；先按 int32，峰值过大再试 float32
            as_i = np.frombuffer(raw, dtype=np.int32).astype(np.float32)
            if np.max(np.abs(as_i)) > 8:
                data = as_i / 2147483648.0
            else:
                data = np.frombuffer(raw, dtype=np.float32).copy()
        else:
            raise ValueError(f'不支持的 WAV 采样宽度: {width} 字节')
        if n_ch > 1:
            data = data.reshape(-1, n_ch).mean(axis=1)
        return data.astype(np.float32), int(sr)

    try:
        import soundfile as sf
    except ImportError as e:
        raise ValueError(
            f'无法打开 {ext}：当前仅内置 WAV 支持。'
            f'请安装 soundfile（pip install soundfile），或先把文件转成 WAV。'
        ) from e
    data, sr = sf.read(path, always_2d=True, dtype='float32')
    mono = data.mean(axis=1)
    return mono.astype(np.float32), int(sr)


# ============================================================
# 作曲原则分析
# ============================================================

NOTE_NAMES_SHARP = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
ORDER_SHARPS = ['F', 'C', 'G', 'D', 'A', 'E', 'B']
ORDER_FLATS = ['B', 'E', 'A', 'D', 'G', 'C', 'F']

# (调名, 主音音级)。升号数决定大调主音。
MAJOR_KEYS = {
    0: ('C 大调', 0), 1: ('G 大调', 7), 2: ('D 大调', 2), 3: ('A 大调', 9),
    4: ('E 大调', 4), 5: ('B 大调', 11), 6: ('F# 大调', 6), 7: ('C# 大调', 1),
    -1: ('F 大调', 5), -2: ('Bb 大调', 10), -3: ('Eb 大调', 3), -4: ('Ab 大调', 8),
    -5: ('Db 大调', 1), -6: ('Gb 大调', 6), -7: ('Cb 大调', 11),
}
# 常见乐器的大致音域（MIDI）。按声部名称匹配。
INSTRUMENT_RANGES = [
    # 与 notation._RANGE_BY_NAME 保持一致（单一事实源在 notation.part_range，
    # 此表仅作 notation 不可用时的兜底）——两表不一致会导致「音域 ⚠️」与
    # 「可演奏性通过/超限」对同一乐器给出互相矛盾的结论
    ('长笛', 60, 96), ('flute', 60, 96),
    ('双簧管', 58, 91), ('oboe', 58, 91),
    ('圆号', 34, 77), ('horn', 34, 77),
    ('小号', 55, 88), ('trumpet', 55, 88),
    ('小提琴', 55, 98), ('violin', 55, 98),
    ('中提琴', 48, 84), ('viola', 48, 84),
    ('大提琴', 36, 72), ('cello', 36, 72),
    ('定音鼓', 36, 60), ('timpani', 36, 60),
    ('弦乐', 48, 84), ('string', 48, 84),
    ('钢琴', 21, 108), ('piano', 21, 108), ('pianoforte', 21, 108),
]

_CLEF_STR = {ClefType.TREBLE: 'treble', ClefType.BASS: 'bass', ClefType.ALTO: 'alto',
             ClefType.TENOR: 'tenor', ClefType.TREBLE_8VB: 'tenor',
             ClefType.BASS_8VB: 'bass'}


def _diatonic_pc(sharps):
    """返回该调号下自然音阶的音级集合（pitch class）。"""
    offset = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
    result = set()
    sharp_set = set(ORDER_SHARPS[:max(sharps, 0)])
    flat_set = set(ORDER_FLATS[:max(-sharps, 0)])
    for n, pc in offset.items():
        if n in sharp_set:
            pc = (pc + 1) % 12
        elif n in flat_set:
            pc = (pc - 1) % 12
        result.add(pc)
    return result


def _kc_key(sharps, mode):
    """转调段 → (显示调名, 主音 pc)。mode='minor' 显示对应关系小调。"""
    name, tonic = MAJOR_KEYS.get(int(sharps), (f'{sharps} 个升降号', 0))
    if mode == 'minor':
        tonic = (tonic - 3) % 12
        m = re.match(r'^([A-G])(b|#)?', name)
        if m:
            letters = 'CDEFGAB'
            letter = letters[(letters.index(m.group(1)) - 2) % 7]
            # 升降号按主音音级重算（降 E 大调的关系小调是 C 小调，不是 Cb）
            acc = {0: '', 1: '#', 11: 'b'}.get(
                (tonic - (0, 2, 4, 5, 7, 9, 11)[letters.index(letter)]) % 12, '')
            name = letter + acc + ' 小调'
    return name, tonic


def _minor_allowed_pcs(tonic_pc):
    """自然小调音级 + 和声小调导音（属和弦/终止必需）。"""
    return ({(tonic_pc + iv) % 12 for iv in (0, 2, 3, 5, 7, 8, 10)}
            | {(tonic_pc - 1) % 12})


def _staff_voices(staff):
    """提取声部事件: [(onset_quarterLength, [midi...], duration_quarterLength)]"""
    events = []
    t = 0.0
    for m in staff.measures:
        for n in m.notes:
            d = n.duration_value() * 4.0  # duration_value 以全音符为 1
            if n.is_rest:
                t += d
                continue
            midis = sorted([p.to_midi() for p in [n.pitch] + n.chord_tones], reverse=True)
            events.append((t, midis, d))
            t += d
    return events


def _sounding(events, t):
    return [m for on, ms, d in events if on <= t < on + d for m in ms]


def _pitch_label(midi: int) -> str:
    return f"{NOTE_NAMES_SHARP[midi % 12]}{midi // 12 - 1}"


def _voice_crossings(score, voices) -> List[str]:
    """纵向相邻声部交叉检测（与内核 voice-leading resolver 同源）。

    管弦乐编制按乐谱次序排列（木管→铜管→打击→弦乐），相邻乐谱声部未必纵向
    相邻（如大管在大提琴之上、大号在低音提琴之上）。按乐谱次序报交叉会误报大量
    「合法越界」，故此处按声部「实际音高跨度中心」重排为真正的纵向次序，只检查
    纵向上相邻的声部对。交叉超过大二度（2 半音）才提示，避免擦边误报。

    返回交叉条目列表（每条含「同类交叉共 N 处」汇总），供调用方截断展示。
    """
    crossings: List[str] = []
    onsets = sorted({0.0} | {on for v in voices for on, _, _ in v})
    try:
        from notation import part_range as _pr
    except Exception:
        _pr = None

    def _center(i: int) -> float:
        # 用声部实际音高的跨度中心（而非乐器名义音域中心）决定纵向叠放——
        # 与内核 resolver 一致：大管名义音域偏低但常在中高音区取音，按名义中心会
        # 把它误排到低音号之下，导致「合法越界」被误报为交叉。
        ms = [m for _, ms_, _ in voices[i] for m in ms_]
        if ms:
            return (min(ms) + max(ms)) / 2.0
        nm = score.staves[i].name or ''
        clef = _CLEF_STR.get(score.staves[i].clef, 'treble')
        if _pr is not None:
            lo, hi = _pr(nm, clef)
        else:
            lo, hi = 40, 90
        return (lo + hi) / 2.0

    n_staff = len(voices)
    # 纵向顺序：音域中心高 → 低
    vorder = sorted(range(n_staff), key=lambda i: -_center(i))
    for pos in range(len(vorder) - 1):
        i = vorder[pos]          # 上方声部
        j = vorder[pos + 1]      # 下方声部
        ni = (score.staves[i].name or '')
        nj = (score.staves[j].name or '')
        ni_l, nj_l = ni.lower(), nj.lower()
        # 定音鼓与相邻声部音区重叠常见，不报交叉
        if any(k in ni_l or k in nj_l for k in ('定音', 'timp')):
            continue
        if any(k in ni_l for k in ('piano', '钢琴')) and any(k in nj_l for k in ('piano', '钢琴')):
            continue
        # 简化：木管之间、木管与小提琴/小号不报交叉（同族高音乐器本就常互叠）
        windish = ('flute', 'oboe', '长笛', '双簧管', 'violin', '小提琴', 'trumpet', '小号')
        if any(k in ni_l or k in ni for k in windish) and any(k in nj_l or k in nj for k in windish):
            continue
        # 圆号在管弦乐里居中填充和声，与弦乐声部互相穿插属于常规配器
        hornish, stringish = ('horn', '圆号'), ('violin', 'viola', 'cello', '提琴')
        if (any(k in ni_l for k in hornish) and any(k in nj_l for k in stringish)) or \
                (any(k in nj_l for k in hornish) and any(k in ni_l for k in stringish)):
            continue
        n_cross = 0
        first_msg = None
        for t in onsets:
            high_notes = _sounding(voices[i], t)
            low_notes = _sounding(voices[j], t)
            if high_notes and low_notes and min(high_notes) + 2 < max(low_notes):
                # 交叉超过大二度才提示，避免擦边
                n_cross += 1
                if first_msg is None:
                    first_msg = (
                        f'纵向上方声部 {i + 1}（{ni}）与下方声部 {j + 1}（{nj}）'
                        f'在第 {t:.1f} 拍交叉：'
                        f'{_pitch_label(min(high_notes))} 低于 {_pitch_label(max(low_notes))}'
                    )
        if n_cross:
            crossings.append(
                first_msg + ('' if n_cross == 1
                             else f'（同类交叉共 {n_cross} 处，此处为第 1 例）'))
    return crossings


def _spelled_name(p) -> str:
    """按模型音高的实际拼写返回音名（如 Db / C#）——按音级类回名会把 Db 标成 C#。"""
    try:
        letter = 'CDEFGAB'[int(p.name.value)]
        acc = {0: '', 1: '#', -1: 'b', 2: 'x', -2: 'bb'}[int(p.accidental.value)]
        return f'{letter}{acc}'
    except Exception:
        return NOTE_NAMES_SHARP[p.to_midi() % 12]


def _key_info(sharps: int):
    return MAJOR_KEYS.get(sharps, (f'{sharps} 个升降号', 0))


def _score_tonality(score: Score):
    """返回 (调名, 主音pc, 允许音级集合)。识别《怒海快板》等小调乐章。"""
    title = score.title or ''
    if '怒海' in title:
        # A 自然小调 + 和声小调导音 G#
        allowed = {9, 11, 0, 2, 4, 5, 7, 8}
        return 'A 小调', 9, allowed
    key_name, tonic = _key_info(score.key_signature.sharps)
    return key_name, tonic, _diatonic_pc(score.key_signature.sharps)


def _staff_role_index(staves, keywords):
    for i, s in enumerate(staves):
        name = (s.name or '').lower()
        if any(k.lower() in name for k in keywords):
            return i
    return None


def _is_ensemble_score(score: Score) -> bool:
    """Brass/strings/timp mixed or 3+ non-piano parts → treat as orchestral texture.

    纯人声 SATB / 钢琴大谱表不算管弦（众赞歌需检平行八度）。
    """
    names = ' '.join((s.name or '').lower() for s in score.staves)
    # 纯合唱开谱
    vocal_keys = ('soprano', 'alto', 'tenor', 'bass', '女高', '女低', '男高', '男低', 'voice')
    n_vocal = sum(
        1 for s in score.staves
        if any(k in (s.name or '').lower() for k in vocal_keys)
    )
    if n_vocal >= 3 and n_vocal == len(score.staves):
        return False
    # 纯钢琴
    if score.staves and all(
            any(k in (s.name or '').lower() for k in ('piano', '钢琴', 'rh', 'lh'))
            for s in score.staves):
        return False
    if len(score.staves) >= 5:
        return True
    ensemble_hits = sum(
        1 for k in (
            'trumpet', '小号', 'horn', '圆号', 'trombone', '长号',
            'timp', '定音', 'violin', '小提琴', 'cello', '大提',
            'viola', '中提', 'string', '弦', 'flute', '长笛',
            'oboe', '双簧', 'clarinet', '单簧', 'bassoon', '大管',
            'orchestra', '管弦',
        )
        if k in names
    )
    non_piano = sum(
        1 for s in score.staves
        if not any(k in (s.name or '').lower() for k in ('piano', '钢琴', 'rh', 'lh'))
        and not any(k in (s.name or '').lower() for k in vocal_keys)
    )
    return ensemble_hits >= 2 or non_piano >= 3


def _should_check_parallel_octaves(score: Score, spec=None) -> bool:
    """众赞歌/四部和声/开谱 SATB 需要报平行八度；管弦与普通钢琴织体不报。

    管弦与夜曲/圆舞里旋律↔低音八度加强极常见，故默认不检八度；
    众赞歌与钢琴四部叠写（RH/LH 带 chord_tones）则必须检出。
    """
    if _is_ensemble_score(score):
        return False
    names = ' '.join((s.name or '').lower() for s in score.staves)
    if any(k in names for k in (
            'soprano', 'alto', 'tenor', 'bass',
            '女高', '女低', '男高', '男低')):
        return True
    blob = ' '.join([
        str((spec or {}).get('genre_id') or ''),
        str((spec or {}).get('genre') or ''),
        str((spec or {}).get('style') or ''),
        str(getattr(score, 'title', '') or ''),
        str(getattr(score, 'composer', '') or ''),
    ]).lower()
    if any(k in blob for k in (
            'chorale', '众赞', 'hymn', 'sacred', '四部', 'satb', '圣咏')):
        return True
    # 钢琴大谱表且大量叠写（四部和声缩谱）
    if len(score.staves) == 2:
        chordal = total = 0
        for s in score.staves:
            for m in s.measures:
                for n in m.notes:
                    if n.is_rest:
                        continue
                    total += 1
                    if n.chord_tones:
                        chordal += 1
        if total and chordal / total >= 0.35:
            return True
    return False


def _split_staff_voice_streams(events, max_voices: int = 2):
    """把和弦化谱表拆成高→低的单声部流（用于四部叠写的平行检测）。"""
    max_n = max((len(ms) for _, ms, _ in events), default=1)
    n = max(1, min(int(max_voices), max_n))
    streams = [[] for _ in range(n)]
    for on, ms, d in events:
        ordered = sorted(ms, reverse=True)
        for i in range(n):
            if i < len(ordered):
                streams[i].append((on, [ordered[i]], d))
    return streams


def _voices_for_parallel_check(score: Score):
    """返回 (voice_streams, labels, staff_of_voice)。

    钢琴 RH/LH 叠写时拆成最多 4 条逻辑声部；开谱多行则一行一声部。
    staff_of_voice 用于「同谱表内八度加强不报平行八度」。
    """
    streams, labels, staff_of = [], [], []
    pianoish = (
        len(score.staves) == 2
        and any(
            any(k in (s.name or '').lower()
                for k in ('piano', '钢琴', 'rh', 'lh'))
            for s in score.staves)
    )
    # 无 Piano 名但两行高低音谱且大量 chord_tones，也按叠写处理
    if not pianoish and len(score.staves) == 2:
        ct = sum(
            1 for s in score.staves for m in s.measures
            for n in m.notes if (not n.is_rest and n.chord_tones))
        if ct >= 2:
            pianoish = True

    for si, staff in enumerate(score.staves):
        ev = _staff_voices(staff)
        if pianoish:
            parts = _split_staff_voice_streams(ev, max_voices=2)
        else:
            parts = [ev]
        for j, p in enumerate(parts):
            if not p:
                continue
            streams.append(p)
            labels.append(f'{si + 1}' if len(parts) == 1 else f'{si + 1}.{j + 1}')
            staff_of.append(si)
    if not streams:
        streams = [_staff_voices(s) for s in score.staves]
        labels = [str(i + 1) for i in range(len(streams))]
        staff_of = list(range(len(streams)))
    return streams, labels, staff_of


def _detect_parallels_for_score(score: Score, spec=None):
    """平行五/八度检测入口：按织体决定是否检八度、是否拆钢琴叠写声部。"""
    orchestral = _is_ensemble_score(score)
    check_oct = (not orchestral) and _should_check_parallel_octaves(score, spec)
    streams, labels, staff_of = _voices_for_parallel_check(score)

    if orchestral:
        # 管弦：只查外声部，且不报八度
        avgs = []
        staff_voices = [_staff_voices(s) for s in score.staves]
        for v in staff_voices:
            midis = [m for _, ms, _ in v for m in ms]
            avgs.append(sum(midis) / len(midis) if midis else 0.0)
        hi_i, lo_i = _pick_outer_voice_indices(score, avgs)
        if hi_i is not None and lo_i is not None and hi_i != lo_i:
            return _detect_parallel_fifths_octaves(
                [staff_voices[hi_i], staff_voices[lo_i]],
                labels=[str(hi_i + 1), str(lo_i + 1)],
                include_octaves=False,
            )
        return _detect_parallel_fifths_octaves(
            staff_voices, include_octaves=False)

    # 室内/钢琴/众赞：查所有逻辑声部对；同谱表对不报八度（避免 RH 八度加强误报）
    problems = []
    seen = set()
    for i in range(len(streams)):
        for j in range(i + 1, len(streams)):
            same_staff = staff_of[i] == staff_of[j]
            oct_ok = check_oct and not same_staff
            for text in _detect_parallel_fifths_octaves(
                    [streams[i], streams[j]],
                    labels=[labels[i], labels[j]],
                    include_octaves=oct_ok):
                if text not in seen:
                    seen.add(text)
                    problems.append(text)
    return problems


def _pick_outer_voice_indices(score: Score, avgs_all):
    """管弦乐取小提琴/小号 vs 大提琴/低音弦；排除定音鼓。"""
    staves = score.staves
    timp_i = {
        i for i, s in enumerate(staves)
        if any(k in (s.name or '').lower() for k in ('定音', 'timp'))
    }
    hi = _staff_role_index(staves, ('小提琴', 'violin', '小号', 'trumpet'))
    if hi is None:
        hi = _staff_role_index(staves, ('长笛', 'flute'))
    lo = _staff_role_index(staves, ('大提琴', 'cello', '低音提琴', 'bass'))
    if lo is None:
        # bare "Strings" / 弦乐 often doubles bass in LLM scores
        for i, s in enumerate(staves):
            n = (s.name or '').lower()
            if i in timp_i:
                continue
            if n in ('strings', 'string', '弦乐', '弦乐组') or n.endswith('strings'):
                if avgs_all[i] and avgs_all[i] < 55:
                    lo = i
                    break
    candidates = [i for i in range(len(staves)) if i not in timp_i and avgs_all[i] > 0]
    if hi is None and candidates:
        hi = max(candidates, key=lambda i: avgs_all[i])
    if lo is None and candidates:
        lo = min(candidates, key=lambda i: avgs_all[i])
    return hi, lo


def _match_voices(before, after):
    """按音高就近配对，避免把和弦里互不相干的音当成一条声部。"""
    remaining = list(after)
    pairs = []
    for src in sorted(before):
        if not remaining:
            break
        dst = min(remaining, key=lambda n: (abs(n - src), n))
        remaining.remove(dst)
        pairs.append((src, dst))
    return pairs


def _detect_parallel_fifths_octaves(voices, labels=None, include_octaves=True):
    """两个声部同时换音，且换音前后音程同为纯五度或纯八度。"""
    problems = []
    seen = set()
    labels = labels or [str(i + 1) for i in range(len(voices))]
    for i in range(len(voices)):
        for j in range(i + 1, len(voices)):
            onsets = sorted({0.0}
                            | {on for on, _, _ in voices[i]}
                            | {on for on, _, _ in voices[j]})
            for t0, t1 in zip(onsets, onsets[1:]):
                notes_i0 = _sounding(voices[i], t0)
                notes_j0 = _sounding(voices[j], t0)
                new_i = [m for on, ms, _ in voices[i] if on == t1 for m in ms]
                new_j = [m for on, ms, _ in voices[j] if on == t1 for m in ms]
                if not new_i or not new_j:
                    continue
                for a0, a1 in _match_voices(notes_i0, new_i):
                    for b0, b1 in _match_voices(notes_j0, new_j):
                        if a1 == a0 or b1 == b0:
                            continue
                        # 同向才算平行
                        if (a1 - a0) * (b1 - b0) <= 0:
                            continue
                        ic0, ic1 = (a0 - b0) % 12, (a1 - b1) % 12
                        kind = None
                        if ic0 == ic1 == 7:
                            kind = '平行五度'
                        elif include_octaves and ic0 == ic1 == 0:
                            kind = '平行八度'
                        if kind is None:
                            continue
                        text = (f"声部{labels[i]}/{labels[j]} 在第 {t0:.1f}→{t1:.1f} 拍 {kind}"
                                f"（{_pitch_label(a0)}->{_pitch_label(a1)} 对 "
                                f"{_pitch_label(b0)}->{_pitch_label(b1)}）")
                        if text not in seen:
                            seen.add(text)
                            problems.append(text)
    return problems


def _full_measure_count(staff) -> int:
    """小节数，不计开头的弱起小节（第 0 小节）。"""
    ms = staff.measures
    return len(ms) - (1 if ms and getattr(ms[0], 'implicit', False) else 0)


def analyze_composition(score: Score, spec=None):
    """返回检查项列表[(类别, 状态, 描述)]。

    传入 spec 时可感知段落级转调（spec.key_changes）：各调性段按新调评判
    调号一致性与终止式——否则 B 段新调的导音会被误报成「变化音」、
    新调主音终止会被误判成「下中音」。
    """
    checks = []
    beats = score.time_signature.beats_per_measure()
    key_name, tonic_pc, allowed = _score_tonality(score)
    # 全局小调：和声小调导音计入允许集合（终止/属和弦必需）
    if spec and str(spec.get('mode') or '') == 'minor' and '小调' not in key_name:
        allowed = set(allowed) | {(tonic_pc - 1) % 12}
    orchestral = _is_ensemble_score(score)

    # 段落级转调：把全曲切成调性段
    kc_list = []
    for kc in ((spec or {}).get('key_changes') or []):
        try:
            kc_list.append((int(kc[0]), int(kc[1]), str(kc[2])))
        except (TypeError, ValueError, IndexError):
            continue
    kc_list.sort()
    seg_defs = [{'start': 1, 'name': key_name, 'tonic': tonic_pc,
                 'allowed': set(allowed), 'minor': '小调' in key_name}]
    # 无转调时也用 spec 的 mode 修正首段调名（调号只给大调名，a 小调会被
    # 误当成 C 大调：终止主音 A 被判成「下中音」）
    if spec:
        base_mode = str(spec.get('mode') or 'major')
        base_name, base_tonic = _kc_key(int(spec.get('key_sharps') or 0), base_mode)
        base_allowed = _minor_allowed_pcs(base_tonic) if base_mode == 'minor' \
            else set(allowed)
        seg_defs[0] = {'start': 1, 'name': base_name, 'tonic': base_tonic,
                       'allowed': set(base_allowed), 'minor': base_mode == 'minor'}
    for kb, sh_, md_ in kc_list:
        nm2, tc2 = _kc_key(sh_, md_)
        al2 = _minor_allowed_pcs(tc2) if md_ == 'minor' else _diatonic_pc(sh_)
        seg_defs.append({'start': kb, 'name': nm2, 'tonic': tc2,
                         'allowed': al2, 'minor': md_ == 'minor'})

    def _seg_at(bar):
        cur = seg_defs[0]
        for s in seg_defs[1:]:
            if bar >= s['start']:
                cur = s
        return cur

    # 1. 调号一致性（合并为一条；小调导音已计入允许集合；转调段按新调评判；
    #    变化音按谱面实际拼写显示——按音级类回名会把 Db 误标成 C#）
    if len(seg_defs) > 1:
        out_by_seg = {}
        for staff in score.staves:
            for m in staff.measures:
                seg = _seg_at(m.number)
                for n in m.notes:
                    if n.is_rest:
                        continue
                    for p in [n.pitch] + n.chord_tones:
                        pc = p.to_midi() % 12
                        if pc not in seg['allowed']:
                            out_by_seg.setdefault(seg['name'], {})\
                                .setdefault(pc, _spelled_name(p))
        if out_by_seg:
            names = '；'.join(
                f'{k} 段出现变化音 '
                + ', '.join(nm for _pc, nm in sorted(v.items()))
                for k, v in sorted(out_by_seg.items()))
            checks.append(('调号一致性', '⚠️ 注意',
                           f'{names}（转调段调外音/经过音）'))
        else:
            checks.append(('调号一致性', '✅ 通过',
                           '各调性段（含转调）音高均在对应自然/和声音阶内'))
    else:
        # 必须按调内允许集合过滤：漏掉过滤会把所有用到的自然音都当成「变化音」
        # （曾经就是这样——单首无转调的大调曲也会报「C 大调下出现变化音
        #   C, D, E, F, G, A, B」，且下面的 ✅ 分支永远不可达）
        allowed0 = seg_defs[0]['allowed']
        used_all = {}
        for staff in score.staves:
            for m in staff.measures:
                for n in m.notes:
                    if n.is_rest:
                        continue
                    for p in [n.pitch] + n.chord_tones:
                        pc = p.to_midi() % 12
                        if pc in allowed0:
                            continue
                        used_all.setdefault(pc, _spelled_name(p))
        outside = sorted(used_all.items())
        seg_name = seg_defs[0]['name']
        if outside:
            names = ', '.join(nm for _pc, nm in outside)
            why = '经过音/离调'
            applied = []
            for b in (((spec or {}).get('grammar') or {}).get('beats') or []):
                ch = str(b.get('chord') or '')
                if '/' in ch and ch not in applied:
                    applied.append(ch)
            if applied:
                why = f"离调和弦，含 {'、'.join(applied[:4])} 等副属和弦"
            checks.append(('调号一致性', '⚠️ 注意',
                           f'{seg_name}下出现变化音 {names}（{why}）'))
        else:
            checks.append(('调号一致性', '✅ 通过',
                           f'全部音高均在 {seg_name} 自然/和声音阶内'))

    # 2. 小节时值完整性（曲中换拍：按每小节实际拍号——变化小节带标记，
    #    其余沿用当前拍号，无标记用全局拍号）
    rhythm_bad = []
    for si, staff in enumerate(score.staves):
        cur_expected = beats
        for m in staff.measures:
            total = sum(n.duration_value() for n in m.notes) * 4.0
            m_ts = getattr(m, 'time_signature', None)
            if m_ts is not None:
                cur_expected = m_ts.beats_per_measure()
            expected = cur_expected
            if getattr(m, 'implicit', False) and 0 < total < expected - 1e-6:
                kind = '弱起小节' if m is staff.measures[0] else '补足小节（与弱起合成一整小节）'
                checks.append(('节奏完整性', '✅ 通过',
                               f'声部 {si + 1} 第 {m.number} 小节为{kind}（{total:g} 拍）'))
            elif abs(total - expected) < 1e-6:
                checks.append(('节奏完整性', '✅ 通过',
                               f'声部 {si + 1} 第 {m.number} 小节时值 = {total:.0f} 拍（{expected:.0f} 拍）'))
            else:
                msg = (f'声部 {si + 1} 第 {m.number} 小节时值 = {total:.0f} 拍，'
                       f'应为 {expected:.0f} 拍')
                rhythm_bad.append(msg)
                checks.append(('节奏完整性', '❌ 问题', msg))

    # 3. 平行五度 / 八度
    # 管弦：只查外声部五度（八度加强不报）。
    # 众赞歌/钢琴四部叠写：查逻辑声部对，跨谱表平行八度计入错误。
    parallels = _detect_parallels_for_score(score, spec)
    check_oct = _should_check_parallel_octaves(score, spec)
    if parallels:
        for p in parallels[:8]:
            if orchestral:
                checks.append(('声部进行', '⚠️ 注意', p + '（管弦织体常见，可接受）'))
            else:
                checks.append(('声部进行', '❌ 问题', p))
        if len(parallels) > 8:
            checks.append(('声部进行', '⚠️ 注意',
                           f'另有 {len(parallels) - 8} 处类似平行进行未逐条列出'))
    else:
        if check_oct:
            tip = '未发现平行五度/八度（众赞歌·四部和声口径，含跨谱表八度）'
        else:
            tip = '外声部未发现平行五度；平行八度（旋律/低音加强）不计入错误'
        checks.append(('声部进行', '✅ 通过', tip))

    # 3b. 第一类二声部对位硬约束（钢琴大谱表 · 1 音/小节）
    if spec and (spec.get('species') or (spec.get('_species_hint'))
                 or ((spec.get('plan') or {}).get('species') if isinstance(
                     spec.get('plan'), dict) else False)):
        try:
            from music_score.two_part_species import (
                validate_two_part_species, extract_bar_midis_from_spec,
            )
            up, lo = [], []
            if len(score.staves) >= 2:
                for m in score.staves[0].measures:
                    n = next((x for x in m.notes if not x.is_rest), None)
                    up.append(n.pitch.to_midi() if n else None)
                for m in score.staves[1].measures:
                    n = next((x for x in m.notes if not x.is_rest), None)
                    lo.append(n.pitch.to_midi() if n else None)
            if any(x is None for x in up + lo):
                eu, el = extract_bar_midis_from_spec(spec)
                if not up or any(x is None for x in up):
                    up = eu
                if not lo or any(x is None for x in lo):
                    lo = el
            species_bad = []
            if len(score.staves) >= 2:
                for si, staff in enumerate(score.staves[:2]):
                    for m in staff.measures:
                        sounding = [n for n in m.notes if not n.is_rest]
                        if len(sounding) != 1:
                            species_bad.append(
                                f'声部 {si + 1} 第 {m.number} 小节应恰好 1 个音，'
                                f'实际 {len(sounding)}')
            if up and lo and None not in up and None not in lo:
                try:
                    tp = int(spec.get('key_sharps', 0) or 0)
                    # 与 two_part_species 生成器一致：调号 → 大调主音 PC
                    _maj = {0: 0, 1: 7, 2: 2, 3: 9, 4: 4, 5: 11, 6: 6, 7: 1,
                            -1: 5, -2: 10, -3: 3, -4: 8, -5: 1, -6: 6, -7: 11}
                    tonic = int(_maj.get(tp, 0))
                    if str(spec.get('mode') or 'major').lower().startswith('min'):
                        tonic = (tonic - 3) % 12
                    if 'tonic_pc' in (spec or {}):
                        tonic = int(spec['tonic_pc']) % 12
                except Exception:
                    tonic = 0
                species_bad.extend(
                    validate_two_part_species(
                        up, lo, tonic_pc=tonic,
                        mode=str(spec.get('mode') or 'major')))
            if species_bad:
                for msg in species_bad[:10]:
                    checks.append(('二声部对位', '❌ 问题', msg))
            else:
                checks.append((
                    '二声部对位', '✅ 通过',
                    '开头Ⅰ · 禁同时跳进 · 少平行3/6 · 反向为主 · '
                    '中间3/6/5/8 · 同音域收束'))
        except Exception as e:
            checks.append(('二声部对位', '⚠️ 注意', f'校验跳过：{e}'))

    # 4. 声部交叉：按真实音域（音域中心）确定纵向叠放顺序，只检查「纵向上相邻」
    #    的功能声部——管弦乐编制按乐谱次序排列（木管→铜管→打击→弦乐），相邻乐谱
    #    声部未必纵向相邻（如大管在大提琴之上、大号在低音提琴之上），按乐谱次序报
    #    交叉会误报大量「合法越界」。改成按音域中心排序后检查纵向相邻声部。
    voices = [_staff_voices(s) for s in score.staves]
    crossings = _voice_crossings(score, voices)
    if crossings:
        for c in crossings[:3]:
            checks.append(('声部交叉', '⚠️ 注意', c))
    else:
        checks.append(('声部交叉', '✅ 通过', '相邻功能声部没有明显交叉'))

    # 5. 音域（与 notation.playability_issues 共用同一张乐器音域表，
    #    避免「音域 ⚠️」与「可演奏性 MAJOR」对同一乐器给出不同结论）
    try:
        from notation import part_range as _part_range, has_named_range as _has_named_range
    except Exception:
        _part_range = None
        _has_named_range = None
    for si, staff in enumerate(score.staves):
        midis = [m for _, ms, _ in _staff_voices(staff) for m in ms]
        if not midis:
            continue
        lo, hi = min(midis), max(midis)
        name = staff.name or f'声部 {si + 1}'
        lo_lim, hi_lim = 40, 90
        name_l = name.lower()
        if _part_range is not None:
            lo_lim, hi_lim = _part_range(name, _CLEF_STR.get(staff.clef, 'treble'))
            # 低音谱表的 "Strings"/弦乐垫：常按大提琴以下音区写作
            if (staff.clef == ClefType.BASS
                    and any(k in name_l for k in ('string', '弦'))
                    and not _has_named_range(name)):
                lo_lim, hi_lim = 28, 72
        else:
            # low "Strings" / bass-clef string pad → cello-like range
            if staff.clef == ClefType.BASS and any(k in name_l for k in ('string', '弦')):
                lo_lim, hi_lim = 28, 72
            else:
                for key, a, b in INSTRUMENT_RANGES:
                    if key.lower() in name_l:
                        lo_lim, hi_lim = a, b
                        break
        ok = lo_lim <= lo and hi <= hi_lim
        checks.append(('音域', '✅ 通过' if ok else '⚠️ 注意',
                       f'{name}: {_pitch_label(lo)}–{_pitch_label(hi)}（MIDI {lo}–{hi}）'))

    # 6. 旋律进行 — 器乐允许大跳；仅在几乎全是无序大跳时提示
    for si, staff in enumerate(score.staves):
        name = staff.name or ''
        if not any(k in name for k in ('小提琴', 'Violin', '长笛', 'Flute', '双簧管', 'Oboe')):
            continue
        mel = []
        for m in staff.measures:
            for n in m.notes:
                if n.is_rest or n.chord_tones:
                    continue
                mel.append(n.pitch.to_midi())
        if len(mel) <= 1:
            continue
        intervals = [abs(b - a) for a, b in zip(mel, mel[1:])]
        leaps = sum(1 for d in intervals if d > 2)
        wide = sum(1 for d in intervals if d >= 7)
        ratio = leaps / max(len(intervals), 1)
        if ratio >= 0.9 and wide >= len(intervals) * 0.5:
            checks.append(('旋律进行', '⚠️ 注意',
                           f'声部 {si + 1}（{name}）: 跳进占比 {ratio:.0%}，其中大跳(≥五度)较多'))
        else:
            checks.append(('旋律进行', '✅ 通过',
                           f'声部 {si + 1}（{name}）: {len(mel)} 音，跳进 {leaps} 处，大跳 {wide} 处'))

    # 7. 终止式
    final_pcs = set()
    final_melody_midi = None
    for staff in score.staves:
        if not staff.measures:
            continue
        last = staff.measures[-1]
        melodic = staff.name and any(k in staff.name for k in ('小提琴', 'Violin'))
        for n in last.notes:
            if n.is_rest:
                continue
            for p in [n.pitch] + n.chord_tones:
                midi = p.to_midi()
                final_pcs.add(midi % 12)
                if melodic:
                    final_melody_midi = midi
    if final_melody_midi is None:
        for staff in score.staves:
            for n in reversed(staff.measures[-1].notes if staff.measures else []):
                if not n.is_rest:
                    final_melody_midi = n.pitch.to_midi()
                    break
            if final_melody_midi is not None:
                break

    # 小调主三和弦：主、小三、五；转调时按**末段调性**评判终止
    last_seg = seg_defs[-1] if len(seg_defs) > 1 else None
    end_seg = last_seg or seg_defs[0]   # seg_defs[0] 已按 spec 的 mode 修正调名
    end_key = end_seg['name']
    end_tonic = end_seg['tonic']
    end_is_minor = end_seg['minor']
    tonic_triad = {end_tonic,
                   (end_tonic + (3 if end_is_minor else 4)) % 12,
                   (end_tonic + 7) % 12}

    if final_melody_midi is not None and final_pcs:
        degree = (final_melody_midi % 12 - end_tonic) % 12
        degree_names = {0: '主音（完全终止感）', 7: '属音（开放性收束）',
                        5: '下属音', 9: '下中音', 4: '三音（大调）', 3: '三音（小调）',
                        2: '上主音'}
        if end_is_minor:
            degree_names[4] = '三音（大调）'
        desc = degree_names.get(degree, f'主音上方 {degree} 个半音')
        if end_tonic in final_pcs and degree == 0:
            status = '✅ 通过'
        elif tonic_triad & final_pcs and degree in (0, 3, 4, 7):
            status = '✅ 通过'
        else:
            status = '⚠️ 注意'
        checks.append(('终止式', status,
                       f'{end_key}结尾音级含 {", ".join(NOTE_NAMES_SHARP[p] for p in sorted(final_pcs))}，'
                       f'旋律落在 {_pitch_label(final_melody_midi)}（{desc}）'))
    else:
        checks.append(('终止式', '⚠️ 注意', '未能提取终止处音高'))

    return checks


def _compact_args(args):
    """把脚本 args 压成无竖线、可放进 Markdown 表格的字符串。

    字符串值里的换行与 `|` 会破坏表格结构（标题/参数含竖线时整行错位），
    统一清理：换行折叠为空格，`|` 转义为 GFM 的 `\\|`。"""
    if not isinstance(args, dict):
        return str(args)
    parts = []
    for k, v in args.items():
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        v = str(v).replace('\r', ' ').replace('\n', ' ').replace('|', '\\|')
        parts.append(f'{k}={v}')
    return '，'.join(parts)


def _md_fence(content: str, lang: str = '') -> str:
    """用围栏代码块包裹 content；围栏长度取内容中最长反引号串 +1（至少 3），
    避免内容本身含 ``` 时把外层围栏提前闭合。"""
    runs = re.findall(r'`+', content or '')
    fence = '`' * max(len(max(runs, key=len)) + 1 if runs else 0, 3)
    return f"{fence}{lang}\n{content}\n{fence}"


def _pretty_json(text: str) -> str:
    """JSON 字符串尽量格式化为多行缩进，便于阅读；解析失败则原样返回。"""
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except Exception:
        return text


def _mermaid_flow(trace, spec):
    u = (trace.get('user_text') or '').replace('"', "'").replace('\n', ' ').replace('\r', ' ')
    if len(u) > 36:
        u = u[:36] + '…'
    emo = trace.get('emotion')
    emo_s = ('情绪约束已触发' if trace.get('emotion_applied') else '无情绪词')
    fixes = trace.get('constraint_fixes') or []
    fix_s = ('风格硬约束已纠正' if fixes else '无（LLM 已在规范内）')
    ts = spec.get('time_signature') or '?'
    tempo = spec.get('tempo') or '?'
    bars = spec.get('bars') or '?'
    return '\n'.join([
        '```mermaid',
        'flowchart TD',
        f'  U["① 用户输入<br/>{u}"] --> P["② Planner LLM<br/>提取意图 plan"]',
        f'  P --> E{{"③ 情绪/节奏约束<br/>{emo_s}"}}',
        f'  E --> T["④ Translator LLM<br/>输出脚本 calls"]',
        f'  T --> G{{"⑤ 风格硬约束<br/>{fix_s}"}}',
        f'  G --> K["⑥ 内核 run_program<br/>逐脚本生成音符"]',
        f'  K --> S["⑦ 渲染乐谱<br/>{ts} ♩={tempo} {bars}小节"]',
        '```',
    ])


def _ascii_flow(trace, spec):
    emo = '是' if trace.get('emotion_applied') else '否'
    fixes = '是' if trace.get('constraint_fixes') else '否'
    ts = spec.get('time_signature') or '?'
    tempo = spec.get('tempo') or '?'
    bars = spec.get('bars') or '?'
    return (
        "用户输入\n"
        "  → [② Planner LLM] 提取意图 plan\n"
        f"  → [③ 情绪/节奏约束] 命中={emo}\n"
        "  → [④ Translator LLM] 输出 calls + reasoning\n"
        f"  → [⑤ 风格硬约束] 触发={fixes}\n"
        "  → [⑥ 内核 run_program] 逐脚本执行\n"
        f"  → [⑦ 渲染谱面] {ts} ♩={tempo} {bars}小节"
    )


def _auto_problems(trace, spec, score):
    rows = []
    emo = trace.get('emotion')
    if emo:
        bias = emo.get('mode_bias')
        mode = spec.get('mode')
        if bias and mode and bias != mode:
            rows.append(('情绪↔最终调式', '🔴 严重',
                         f"情绪约束要求 {bias}，但谱面为 {mode}（需求与谱面冲突）"))
        else:
            rows.append(('情绪↔最终调式', '🟢 一致',
                         f"情绪要求 {bias or '不限'}，谱面 {mode}"))
        bpm_want = emo.get('bpm')
        bpm_real = spec.get('tempo')
        if bpm_want and bpm_real:
            if abs(int(bpm_real) - int(bpm_want)) > 12:
                rows.append(('情绪↔最终速度', '🔴 严重',
                             f"情绪要求约 ♩={bpm_want}，但谱面 ♩={bpm_real}（偏差过大）"))
            else:
                rows.append(('情绪↔最终速度', '🟢 一致',
                             f"♩={bpm_real}（情绪目标 {bpm_want}）"))
    else:
        rows.append(('情绪约束', '🟢 未触发', '用户未给情绪/速度词'))
    fixes = trace.get('constraint_fixes') or []
    meter_fix = [f for f in fixes if '拍号' in f]
    ts = spec.get('time_signature')
    if meter_fix:
        rows.append(('意图拍号↔谱面', '🟡 注意',
                     f"LLM 给出的拍号被强制纠正：{'；'.join(meter_fix)}；最终 {ts}"))
    else:
        rows.append(('意图拍号↔谱面', '🟢 一致', f"最终拍号 {ts}"))
    bars_target = spec.get('bars')
    try:
        actual = max((_full_measure_count(staff) for staff in score.staves), default=0) \
            if score else 0
    except Exception:
        actual = bars_target
    if actual and bars_target and actual != bars_target:
        rows.append(('计划小节↔实际小节', '🟡 注意',
                     f"计划 {bars_target} 小节，实际渲染 {actual} 小节"))
    else:
        rows.append(('计划小节↔实际小节', '🟢 一致', f"{bars_target} 小节"))
    if fixes:
        rows.append(('风格规范遵循', '🟡 注意',
                     f"LLM 偏离 genre 规范，已强制纠正 {len(fixes)} 处"
                     f"（说明模型未严格遵循风格包）"))
    else:
        rows.append(('风格规范遵循', '🟢 良好', 'LLM 输出均在 genre 规范内，无需强制'))
    pl = trace.get('pipeline') or ''
    if 'composer' in pl:
        rows.append(('生成路径', '🟡 提示',
                     '旧 composer 路径：LLM 直出乐谱 JSON，无脚本调用序列'
                     '（透明度为脚本级缺失，建议改用翻译路径）'))
    if trace.get('repair') and trace['repair'].get('triggered'):
        rows.append(('本地修复', '🟡 注意',
                     '校验后触发了本地修复（时值/声部进行），'
                     '说明初版谱存在 CRITICAL/MAJOR 问题'))
    # 计划编制 ↔ 实际编制：planner 编了某乐器、谱面却没有（常见于
    # voices 数不足或 translator 只取了部分乐器），三指标遵循度查不到这类差异
    plan_instr = None
    for c in (trace.get('llm_calls') or []):
        if c.get('stage') == 'planner' and isinstance(c.get('parsed'), dict):
            pi = c['parsed'].get('instrumentation')
            if isinstance(pi, list) and pi:
                plan_instr = [str(x) for x in pi]
            break
    if plan_instr:
        actual_names = [str(p.get('name') or '') for p in (spec.get('parts') or [])]
        if not actual_names:
            actual_names = [str(s.name or '') for s in score.staves]
        actual_low = [n.lower() for n in actual_names if n]
        missing = []
        for ins in plan_instr:
            ins_l = ins.strip().lower()
            # 同义词：cello 是 violoncello 的子串，无需特判；其余按双向子串匹配
            if not any(ins_l in n or n in ins_l for n in actual_low):
                missing.append(ins)
        if missing:
            rows.append(('计划编制↔实际编制', '🟡 注意',
                         f"计划编制含 {'、'.join(missing)}，但谱面 "
                         f"{len(actual_names)} 个声部未落地"
                         f"（voices 数不足或 translator 取舍）——"
                         f"用户对编制的明确要求可能未满足"))
        else:
            rows.append(('计划编制↔实际编制', '🟢 一致',
                         f"计划 {len(plan_instr)} 件乐器与谱面 "
                         f"{len(actual_names)} 声部对应"))
    return rows


def _render_transparency(trace, spec, score):
    """把 session.compose_trace 渲染成分析报告的「第 4 节」内容（列表行）。"""
    if not trace:
        return []
    import json as _json
    L = []
    L.append("## 4. 作曲过程透明化（可追溯）\n")
    L.append("> 本节约等于把「需求 → 大模型 → 脚本 → 谱面」的完整链路展开，"
             "便于核对「说得 vs 写得」是否一致。数据来自本次生成的 "
             "`session.compose_trace`，确定性可复现。把本报告整体喂回大模型，"
             "可进一步定位具体小节/声部的问题与修正建议。\n")
    # 4.1 流程图
    L.append("### 4.1 流程图（本次实际链路）\n")
    L.append(_mermaid_flow(trace, spec))
    L.append("")
    L.append("（文字版流程图，便于无 Mermaid 渲染环境阅读）\n")
    L.append("```")
    L.append(_ascii_flow(trace, spec))
    L.append("```\n")
    # 4.2 大模型 I/O
    L.append("### 4.2 大模型输入输出（LLM I/O）\n")
    calls = trace.get('llm_calls') or []
    if not calls:
        L.append("（本次为确定性生成，未调用大模型——见 4.3 / 4.5 说明）\n")
    for c in calls:
        L.append(f"#### {c.get('title', 'LLM 调用')}\n")
        L.append(f"- **系统提示**（固定指令模板，已截断）：\n\n"
                 f"{_md_fence(c.get('system', ''))}\n")
        L.append(f"- **用户输入**（喂给模型的结构化上下文）：\n\n"
                 f"{_md_fence(_pretty_json(c.get('user', '')), 'json')}\n")
        L.append(f"- **模型返回（原始）**：\n\n"
                 f"{_md_fence(_pretty_json(c.get('assistant', '')), 'json')}\n")
    # 4.3 脚本 I/O
    L.append("### 4.3 脚本调用与输入输出\n")
    kc = trace.get('kernel_calls') or []
    if kc:
        L.append("| # | 脚本 | 输入(args) | 输出（新增音符 / 状态变更） |")
        L.append("|---|---|---|---|")
        n_new_score = 0
        for i, e in enumerate(kc, 1):
            inp = _compact_args(e.get('input') or {})
            out = e.get('output') or {}
            added = out.get('added_counts') or []
            added_str = '，'.join(f"声部{j+1}:+{n}" for j, n in enumerate(added) if n)
            if e.get('script') == 'new_score':
                n_new_score += 1
                if n_new_score > 1:
                    inp += '　⟲ **LLM 重试**：此前调用序列出错回滚，从 new_score 重新执行'
            L.append(f"| {i} | {e.get('script')} | {inp} | "
                     f"{out.get('note', '')}；新增 {added_str or '—'} |")
        L.append("")
        L.append("<details><summary>逐脚本新增音符样例（前 8 个 / 声部）</summary>\n")
        for i, e in enumerate(kc, 1):
            samples = e.get('output', {}).get('added_sample') or []
            parts_s = []
            for j, s in enumerate(samples):
                parts_s.append(f"声部{j+1}: " + (', '.join(s) if s else '—'))
            L.append(f"- **{e.get('script')}**：" + '；'.join(parts_s))
        L.append("\n</details>\n")
    else:
        L.append(f"（本次路径未经过确定性脚本内核：`{trace.get('pipeline')}`"
                 "—— 该路径由 LLM 直接产出乐谱或纯确定性生成，无逐脚本记录）\n")
    # 4.4 本地强制约束
    L.append("### 4.4 本地强制约束（确定性兜底，优先级高于 LLM）\n")
    emo = trace.get('emotion')
    if trace.get('emotion_applied') and emo:
        matched = ' / '.join(emo.get('matched') or []) or '—'
        L.append(f"- 情绪/节奏约束命中：`{matched}` → ♩={emo.get('bpm')} "
                 f"{emo.get('tempo_text')} · {emo.get('mode_bias') or '调式不限'} · "
                 f"{emo.get('rhythm')} · {emo.get('dynamics')}")
    else:
        L.append("- 情绪/节奏约束：未触发（用户未给情绪/速度词，或已被风格基线覆盖）")
    fixes = trace.get('constraint_fixes') or []
    if fixes:
        for f in fixes:
            L.append(f"- 风格/速度强制纠正：{f}")
    else:
        L.append("- 风格/速度强制纠正：未触发（LLM 输出已在 genre 规范内）")
    # 学到的实证契约实际施加了哪些约束（style_notes 由内核对齐 style_contract.yaml
    # 时逐条记录）。这一节是「风格是否正确」的唯一凭据：不看提示词，只看内核真做了什么。
    notes = (spec or {}).get('style_notes') or []
    if notes:
        L.append(f"- **风格契约施加的约束**（{len(notes)} 条，来自该包实测语料的 "
                 f"`style_contract.yaml`）：")
        for n in notes[:12]:
            L.append(f"  - {n}")
        if len(notes) > 12:
            L.append(f"  - …另有 {len(notes) - 12} 条")
    else:
        L.append("- **风格契约施加的约束**：无（该风格包没有 `style_contract.yaml`，"
                 "或契约证据量不足、和声分区未生效）")
    # 4.5 自动问题定位
    L.append("### 4.5 自动问题定位（可疑点 + 程度）\n")
    L.append("| 检查项 | 程度 | 说明 |")
    L.append("|---|---|---|")
    for r in _auto_problems(trace, spec, score):
        L.append(f"| {r[0]} | {r[1]} | {r[2]} |")
    L.append("")
    L.append("> 说明：上表为程序自动比对「意图 ↔ 谱面」的初判（🔴 严重 / 🟡 注意 / "
             "🟢 一致）。将本报告整体喂给大模型，可进一步定位具体小节/声部的问题与修正建议。\n")
    return L


def _compose_trace_of(window):
    """取当前窗口关联的流程痕迹（安全返回 None）。

    优先用**本次生成自带**的 trace（骨架路径在 worker 里现建，含逐脚本记录）；
    没有时回落到会话的 compose_trace（LLM 路径由 composer 写入）。
    """
    t = getattr(window, '_last_compose_trace', None)
    if t:
        return t
    sess = getattr(window, '_compose_session', None)
    if sess is None:
        return None
    return getattr(sess, 'compose_trace', None)


def _notation_quality_blocks(spec, notation_log, sec_no):
    """「记谱与演奏法 + 生成质量评估」两节，章节号由 sec_no 决定（两版报告各自顺延）。"""
    out = []
    if not spec:
        return out
    try:
        from notation import notation_report
        rep = notation_report(spec, notation_log)
        rep = rep.replace('## 记谱与演奏法', f'## {sec_no}. 记谱与演奏法', 1)
        out.append(rep)
        out.append("")
    except Exception as e:
        out.append(f"## {sec_no}. 记谱与演奏法\n\n（记谱层不可用：{e}）\n")
    # 三指标：风格重叠率 / 属性遵循度 / 最近邻防抄袭
    try:
        import evaluate as _ev
        if _ev.all_fingerprints():
            rep2 = _ev.evaluate_report(spec)
            rep2 = rep2.replace('## 生成质量评估（三指标）',
                                f'## {sec_no + 1}. 生成质量评估（三指标）', 1)
            out.append(rep2)
            out.append("")
    except Exception:
        pass
    return out


def _dev_report_path(report_path) -> str:
    """由分析报告路径派生程序员版报告路径（与 export_analysis_report 一致）。

    dev 报告以前只有导出端知道路径，UI 拿不到，导致界面里一直看不到。
    这里做成唯一推导入口，两边共用。
    """
    if not report_path:
        return ''
    base, ext = os.path.splitext(str(report_path))
    return f"{base}_dev{ext or '.md'}"


def export_analysis_report(score: Score, path, spec=None, notation_log=None,
                           compose_trace=None):
    """生成两份分析报告：

    1. **编辑者版** → `path`：作曲原则、谱面事实、问题提示、记谱与演奏法、
       生成质量——只关心「曲子写得对不对」，不含任何生成链路细节。
    2. **程序员版** → `path` 去扩展名加 `_dev.md`：会话与生成环境、谱面事实、
       自动检查、全量「作曲过程透明化」（LLM I/O / 脚本 I/O / 本地约束 /
       自动问题定位）——面向产品与工程排查，可整体喂回大模型定位问题。

    返回 (编辑者版文本, (passed, warned, failed))。传入 spec / notation_log
    时两版均含「记谱与演奏法」；传入 compose_trace 时程序员版含「第 4 节
    作曲过程透明化」。
    """
    # compose_trace 的正式形态是 dict（`kernel_calls` 装逐脚本记录）；但
    # `style_skeleton.skeleton_spec(trace=[])` 交出来的是一条**列表**。此前把
    # 列表直接传进来会在 `compose_trace.get` 上抛 AttributeError（且栈深处才炸），
    # 调用方很难从这个隐式契约里猜出正确形态 —— 这里把列表按 `kernel_calls` 兜住。
    if isinstance(compose_trace, (list, tuple)):
        compose_trace = {'kernel_calls': list(compose_trace),
                         'pipeline': 'skeleton', 'llm_calls': [],
                         'skill_ids': ['compose-full']}
        if not compose_trace['kernel_calls']:
            compose_trace = None
    checks = analyze_composition(score, spec=spec)
    key_name, _tonic, allowed = _score_tonality(score)
    # spec 声明小调时显示对应关系小调（调号只给大调名，0 升号会误写成 C 大调）
    disp_key = key_name
    if spec and str(spec.get('mode') or '') == 'minor' and '小调' not in key_name:
        m = re.match(r'^([A-G])(b|#)?', key_name)
        if m:
            letters = 'CDEFGAB'
            disp_key = (letters[(letters.index(m.group(1)) - 2) % 7]
                        + (m.group(2) or '') + ' 小调')

    # ---- 共用块 1：基本信息（两版一致，程序员版标题作「谱面事实」）----
    info_body = []
    sharp_txt = f"{score.key_signature.sharps} 个升号" if score.key_signature.sharps >= 0 \
        else f"{-score.key_signature.sharps} 个降号"
    info_body.append(f"- 调性: {disp_key}（{sharp_txt}）")
    # 段落级转调必须如实列出：否则「X 大调」的总述会掩盖 B 段的新调
    kcs = []
    if spec:
        for kc in (spec.get('key_changes') or []):
            try:
                kcs.append((int(kc[0]), int(kc[1]), str(kc[2])))
            except (TypeError, ValueError, IndexError):
                continue
    if kcs:
        seg = []
        for bar, sh_, md_ in sorted(kcs):
            kn2, _t2 = _key_info(sh_)
            if md_ == 'minor':
                m2 = re.match(r'^([A-G])(b|#)?', kn2)
                if m2:
                    letters = 'CDEFGAB'
                    kn2 = (letters[(letters.index(m2.group(1)) - 2) % 7]
                           + (m2.group(2) or '') + ' 小调')
            seg.append(f'第 {bar} 小节 → {kn2}')
        if seg:
            info_body.append(f"- 转调: {'；'.join(seg)}")
    info_body.append(f"- 拍号: {score.time_signature.numerator}/{score.time_signature.denominator}")
    info_body.append(f"- 速度: 四分音符 = {score.tempo} BPM")
    # 段落变速（set_sections 写入的速度标记）：与全局速度不同时如实列出，
    # 否则「♩=96」的总述会掩盖 B/A' 段的 108/120
    seg_speed = []
    for tm in ((spec or {}).get('tempo_marks') or []):
        try:
            start = int(tm[0])
            txt = str(tm[1] or '')
            bpm = int(tm[2]) if len(tm) > 2 and tm[2] is not None else None
        except (TypeError, ValueError):
            continue
        if bpm is not None and bpm == int(score.tempo or 0):
            continue
        if start == 1 and bpm is None:
            continue
        label = txt if '♩' in txt else (f'{txt}（♩={bpm}）' if bpm else txt)
        seg_speed.append(f'第 {start} 小节 {label}')
    if seg_speed:
        info_body.append(f"- 段落速度: {'；'.join(seg_speed)}")
    scale = ', '.join(NOTE_NAMES_SHARP[p] for p in sorted(allowed))
    info_body.append(f"- 调内音级: {scale}")
    _CLEF_NAMES = {ClefType.TREBLE: '高音谱号', ClefType.BASS: '低音谱号',
                   ClefType.ALTO: '中音谱号', ClefType.TENOR: '次中音谱号',
                   ClefType.TREBLE_8VB: '高音谱号（低八度）',
                   ClefType.BASS_8VB: '低音谱号（低八度）'}
    for si, staff in enumerate(score.staves):
        clef = _CLEF_NAMES.get(staff.clef) or getattr(staff.clef, 'name', str(staff.clef))
        label = staff.name or f'声部 {si + 1}'
        n_full = _full_measure_count(staff)
        extra = '（另有弱起小节）' if n_full < len(staff.measures) else ''
        info_body.append(f"- 声部 {si + 1}: {label}，{clef}，{n_full} 小节{extra}")

    # ---- 共用块 2：问题与提示（两版一致）----
    problems = [c for c in checks if not c[1].startswith('✅')]
    problems_body = []
    if not problems:
        problems_body.append("（无）当前自动检查未发现需要关注的问题。")
    else:
        problems_body.append("| 检查类别 | 状态 | 描述 |")
        problems_body.append("|---|---|---|")
        for cat, status, desc in problems:
            problems_body.append(f"| {cat} | {status} | {desc} |")
    problems_body.append("")
    passed = sum(1 for _, s, _ in checks if s.startswith('✅'))
    warned = sum(1 for _, s, _ in checks if s.startswith('⚠️'))
    failed = sum(1 for _, s, _ in checks if s.startswith('❌'))
    problems_body.append("> 说明：众赞歌/钢琴四部叠写检查全部逻辑声部对的平行五八度；"
                         "管弦织体仅查外声部平行五度（八度加强不报）。"
                         "相邻声部交叉为提示项。"
                         f" 共检查 {len(checks)} 项（✅ {passed} / ⚠️ {warned} / ❌ {failed}）。")
    problems_body.append("")

    dev_path = _dev_report_path(path)
    dev_fname = os.path.basename(dev_path)
    editor_fname = os.path.basename(path)

    # ================= 编辑者版 =================
    lines = []
    lines.append(f"# 作曲分析报告：{score.title}（编辑者版）\n")
    lines.append("> 面向音乐编辑者：核对谱面与作曲原则即可。生产过程、大模型"
                 f"输入输出与脚本链路见《作曲生产报告（程序员版）》：`{dev_fname}`。\n")
    lines.append("## 1. 要核对的作曲原则\n")
    lines.append("1. **调内音高**：各音应落在该调号自然音阶内（调号升降号已计入；小调和声导音亦计入）。")
    lines.append("2. **节奏完整**：每个声部每一小节时值之和应等于拍号。")
    lines.append("3. **声部进行**：众赞歌/四部和声需避免平行五度与平行八度；"
                 "管弦乐外声部平行五度需注意，八度加强不计入错误。")
    lines.append("4. **声部交叉**：功能相邻声部不宜明显越过（木管互叠不计）。")
    lines.append("5. **音域**：各声部应落在该乐器常用音域内。")
    lines.append("6. **旋律**：器乐旋律允许较大跳进与较复杂节奏；极端无序跳进再提示。")
    lines.append("7. **终止**：结束处最好含主音/主三和弦，旋律落在主音上更有完全终止感。")
    lines.append("")
    lines.append("## 2. 基本信息\n")
    lines.extend(info_body)
    lines.append("")
    lines.append("## 3. 需关注的问题与提示\n")
    lines.extend(problems_body)
    # 编辑者版无透明化节：记谱=4、质量=5
    lines.extend(_notation_quality_blocks(spec, notation_log, 4))

    editor_text = '\n'.join(lines)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(editor_text)

    # ================= 程序员版（详细生产报告）=================
    dev = []
    dev.append(f"# 作曲生产报告：{score.title}（程序员版）\n")
    dev.append("> 面向产品/工程：记录本次生成的完整生产链路与系统状态，"
               "用于核对「说得 vs 写得」、排查缺陷与回归测试；可整体喂回大模型"
               "进一步定位具体小节/声部的问题。谱面检查与编辑者版一致，"
               f"编辑者版见：`{editor_fname}`。\n")
    dev.append("## 1. 会话与生成环境\n")
    if compose_trace:
        dev.append(f"- 生成时间: {compose_trace.get('generated_at') or '—'}")
        dev.append(f"- 生成路径: {compose_trace.get('pipeline') or '—'}")
        skills = compose_trace.get('skill_ids') or []
        dev.append(f"- 命中技能: {', '.join(map(str, skills)) or '—'}")
        ut = str(compose_trace.get('user_text') or '').strip()
        if ut:
            dev.append(f"- 用户原始输入: {ut[:120]}{'…' if len(ut) > 120 else ''}")
        dev.append("- 原始数据: `session.compose_trace`（本报告第 4 节即其展开，"
                   "确定性可复现）")
    else:
        dev.append("（本次无可用的 compose_trace：内置示例谱或旧会话，"
                   "无 LLM/脚本链路可追溯）")
    dev.append("")
    dev.append("## 2. 谱面事实（与编辑者版「基本信息」一致）\n")
    dev.extend(info_body)
    dev.append("")
    dev.append("## 3. 自动检查结果（与编辑者版「第 3 节」一致）\n")
    dev.extend(problems_body)
    # 第 4 节：作曲过程透明化（_render_transparency 固定输出「## 4.」标题）
    if compose_trace:
        dev.extend(_render_transparency(compose_trace, spec or {}, score))
    # 有透明化时记谱=5、质量=6；无则 4、5
    dev.extend(_notation_quality_blocks(spec, notation_log,
                                        5 if compose_trace else 4))

    with open(dev_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(dev))

    return editor_text, (passed, warned, failed)


# ============================================================
# 主窗口
# ============================================================

class SelectableScoreView(QGraphicsView):
    """五线谱视图：左键点选小节/符号，按住左键拖动或中键拖拽平移，滚轮缩放（有界）。"""
    selection_changed = pyqtSignal()
    zoom_changed = pyqtSignal(float)  # 相对 100% 的缩放倍数

    ZOOM_MIN = 0.25   # 最小缩放（相对 100%）
    ZOOM_MAX = 5.0    # 最大缩放
    WHEEL_FACTOR = 1.08   # 滚轮每格缩放步长（比之前 1.12 更细腻）
    BUTTON_FACTOR = 1.25  # 缩放按钮步长
    DRAG_THRESHOLD = 5    # 按住左键移动超过该像素数即视为拖拽平移（否则算点选）

    def __init__(self, scene, renderer, parent=None):
        super().__init__(scene, parent)
        self.renderer = renderer
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
        # 右侧滚动条常显（符合 DAW 习惯，避免布局跳动）
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._panning = False
        self._last_pos = None
        self._press_pos = None
        self._press_mods = None
        self._left_panning = False
        self.selected_measures: Set[int] = set()
        self.selected_symbols: List[Tuple[int, int, int]] = []
        self._anchor_measure: Optional[int] = None
        # ---- 编辑模式状态 ----
        self.edit_mode = False
        self.edit_controller = None          # EditController（宿主窗口注入）
        self.suggestion_callback = None      # fn(cands, verdict, si, mi, idx)
        self.state_callback = None           # fn(duration, accidental) 快捷键改状态后同步调色板
        self._press_hit = None
        self._pitch_drag = None              # [key, orig_pitch, moved]
        self._drag_pitch_cur = None

    # ---------- 编辑模式 ----------

    def set_edit_mode(self, on: bool):
        self.edit_mode = bool(on)
        self.setMouseTracking(self.edit_mode)
        if not self.edit_mode:
            self.renderer.clear_ghost()
            self._pitch_drag = None
            self._drag_pitch_cur = None
            if self.suggestion_callback:
                self.suggestion_callback([], None, None, None, None)

    def _note_at(self, key):
        c = self.edit_controller
        if c is None or c.score is None:
            return None
        si, mi, ni = key
        staves = c.score.staves
        if si >= len(staves) or mi >= len(staves[si].measures):
            return None
        notes = staves[si].measures[mi].notes
        return notes[ni] if ni < len(notes) else None

    def _measure_rect(self, si: int, mi: int):
        for h in self.renderer.hit_measures:
            if h['staff_idx'] == si and h['measure_idx'] == mi:
                return h['rect']
        return None

    def _staff_y_of(self, si: int, mi: int, hit=None) -> Optional[float]:
        if hit is not None and hit.get('staff_y') is not None:
            return float(hit['staff_y'])
        for h in self.renderer.hit_measures:
            if h['staff_idx'] == si and h['measure_idx'] == mi:
                if h.get('staff_y') is not None:
                    return float(h['staff_y'])
                return h['rect'].top() + 8
        return None

    def _insert_index(self, hit, x: float) -> int:
        """续写：点在已有音符上则插在它后面。"""
        if hit is not None and hit.get('kind') == 'note' and hit.get('note_idx') is not None:
            return int(hit['note_idx']) + 1
        si, mi = hit['staff_idx'], hit['measure_idx']
        return insert_index_for_x(self.renderer, si, mi, x)

    def _move_selection(self, delta: int):
        """←/→：在当前声部内把音符选中态前/后移一个。"""
        if len(self.selected_symbols) != 1:
            return
        si, mi, ni = self.selected_symbols[0]
        c = self.edit_controller
        if c is None or c.score is None:
            return
        notes = []
        for m_idx, m in enumerate(c.score.staves[si].measures):
            for n_idx in range(len(m.notes)):
                notes.append((si, m_idx, n_idx))
        try:
            cur = notes.index((si, mi, ni))
        except ValueError:
            return
        nxt = max(0, min(len(notes) - 1, cur + delta))
        self.selected_symbols = [notes[nxt]]
        self.apply_highlight()
        self.selection_changed.emit()

    # ---------- 缩放 ----------

    def _current_zoom_rel(self) -> float:
        t = self.transform()
        return (t.m11() + t.m22()) / 2.0

    def _apply_zoom(self, rel: float, anchor_center: bool = False):
        rel = max(self.ZOOM_MIN, min(self.ZOOM_MAX, rel))
        prev_anchor = self.transformationAnchor()
        if anchor_center:
            self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.resetTransform()
        self.scale(rel, rel)
        self.setTransformationAnchor(prev_anchor)
        self.zoom_changed.emit(rel)

    def zoom_step(self, direction: int):
        """direction=+1 放大 / -1 缩小，以视口中心为锚点。"""
        rel = self._current_zoom_rel()
        rel = rel * self.BUTTON_FACTOR if direction > 0 else rel / self.BUTTON_FACTOR
        self._apply_zoom(rel, anchor_center=True)

    def zoom_reset(self):
        """恢复 100%。"""
        self._apply_zoom(1.0, anchor_center=True)

    def zoom_fit_width(self):
        """横向铺满：整页宽度适应视口宽度。"""
        rect = self.sceneRect()
        if rect.isEmpty() or rect.width() <= 0:
            self.zoom_reset()
            return
        avail = max(self.viewport().width() - 24, 100)
        rel = avail / rect.width()
        self._apply_zoom(rel, anchor_center=True)
        self.verticalScrollBar().setValue(0)

    def wheelEvent(self, event):
        rel = self._current_zoom_rel()
        factor = self.WHEEL_FACTOR if event.angleDelta().y() > 0 else 1 / self.WHEEL_FACTOR
        new_rel = max(self.ZOOM_MIN, min(self.ZOOM_MAX, rel * factor))
        if abs(new_rel - rel) > 1e-6:
            self._apply_zoom(new_rel)

    def clear_selection(self):
        self.selected_measures.clear()
        self.selected_symbols.clear()
        self._anchor_measure = None
        self.renderer.set_selection([], [])
        self.selection_changed.emit()

    def apply_highlight(self):
        self.renderer.set_selection(self.selected_measures, self.selected_symbols)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._last_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if event.button() == Qt.LeftButton:
            # 先记录按下位置；是否选区在抬起时决定（未拖动才选，拖动则平移）
            self._press_pos = event.pos()
            self._press_mods = event.modifiers()
            self._left_panning = False
            # 点谱面即抢焦点，否则 Shift+数字等快捷键会落到对话框而「没反应」
            self.setFocus(Qt.MouseFocusReason)
            if self.edit_mode and self.edit_controller is not None:
                scene_pos = self.mapToScene(event.pos())
                self._press_hit = self.renderer.hit_test(scene_pos.x(),
                                                         scene_pos.y())
                # 选择工具 + 按在音符上 → 准备音高拖拽（移动即改音高）
                self._pitch_drag = None
                if (self.edit_controller.tool == 'select'
                        and self._press_hit is not None
                        and self._press_hit['kind'] == 'note'):
                    key = (self._press_hit['staff_idx'],
                           self._press_hit['measure_idx'],
                           self._press_hit['note_idx'])
                    note = self._note_at(key)
                    if note is not None and not note.is_rest:
                        self._pitch_drag = [key, note.pitch, False]
                        self._drag_pitch_cur = None
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._last_pos is not None:
            delta = event.pos() - self._last_pos
            self._last_pos = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return
        if self.edit_mode and self.edit_controller is not None:
            # 音高拖拽进行中
            if self._pitch_drag is not None and self._press_pos is not None:
                if (event.pos() - self._press_pos).manhattanLength() >= 3:
                    self._pitch_drag[2] = True
                    self._update_pitch_drag(event.pos())
                return
            # 未按下：悬停 ghost 预览 + 智能建议
            if self._press_pos is None:
                self._update_edit_hover(event.pos())
                super().mouseMoveEvent(event)
                return
        if self._press_pos is not None and not self._left_panning:
            # 左键按住并移动超过阈值 → 进入拖拽平移（放弃本次点选）
            if self._pitch_drag is not None:
                return  # 拖拽改音高时不平移
            if (event.pos() - self._press_pos).manhattanLength() >= self.DRAG_THRESHOLD:
                self._left_panning = True
                self._last_pos = self._press_pos  # 从按下点起算，单次大跳不丢位移
                self.setCursor(Qt.ClosedHandCursor)
        if self._left_panning and self._last_pos is not None:
            delta = event.pos() - self._last_pos
            self._last_pos = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            return
        if event.button() == Qt.LeftButton:
            if self._left_panning:
                self._left_panning = False
                self._press_pos = None
                self._press_mods = None
                self._press_hit = None
                self.setCursor(Qt.ArrowCursor)
                return
            press = self._press_pos
            mods = self._press_mods or Qt.NoModifier
            self._press_pos = None
            self._press_mods = None
            if self.edit_mode and self.edit_controller is not None:
                self._press_hit = None
                # 音高拖拽收尾
                if self._pitch_drag is not None:
                    key, _orig, moved = self._pitch_drag
                    cur = self._drag_pitch_cur
                    self._pitch_drag = None
                    self._drag_pitch_cur = None
                    self.renderer.clear_ghost()
                    if moved and cur is not None:
                        self.edit_controller.try_set_pitch(key[0], key[1],
                                                           key[2], cur)
                    return
                tool = self.edit_controller.tool
                if tool in ('note', 'rest') and press is not None:
                    self._insert_at(press)
                    return
                if tool == 'erase' and press is not None:
                    scene_pos = self.mapToScene(press)
                    hit = self.renderer.hit_test(scene_pos.x(), scene_pos.y())
                    if hit is not None and hit['kind'] == 'note':
                        self.edit_controller.delete_notes(
                            [(hit['staff_idx'], hit['measure_idx'],
                              hit['note_idx'])])
                        self.clear_selection()
                    return
                # 选择工具：落回原有选区逻辑
            if press is not None:
                self._handle_select_at(press, mods)
            return
        super().mouseReleaseEvent(event)

    def focusNextPrevChild(self, next_):
        # Qt 默认用 Tab 切焦点；编辑时吞掉，真正采纳走 keyPress / QShortcut
        if self.edit_mode:
            return True
        return super().focusNextPrevChild(next_)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            win = self.window()
            if self.edit_mode and bool(getattr(win, '_assist_enabled', False)):
                ctrl = bool(event.modifiers() & Qt.ControlModifier)
                if ctrl and hasattr(win, '_accept_assist_bar'):
                    win._accept_assist_bar()
                elif hasattr(win, '_accept_assist_tab'):
                    win._accept_assist_tab()
                event.accept()
                return
        if event.key() == Qt.Key_Escape:
            self.clear_selection()
            self.renderer.clear_ghost()
            self.renderer.clear_ghost_candidates()
            if self.suggestion_callback:
                self.suggestion_callback([], None, None, None, None)
            return
        c = self.edit_controller
        win = self.window()
        assist_on = (self.edit_mode and c is not None
                     and bool(getattr(win, '_assist_enabled', False)))
        if self.edit_mode and c is not None:
            k = event.key()
            ctrl = bool(event.modifiers() & Qt.ControlModifier)
            shift = bool(event.modifiers() & Qt.ShiftModifier)
            keys = list(self.selected_symbols)

            # ---- 辅助作曲快捷键 ----
            # Tab=下一音 · Ctrl+Tab=本小节 · Ctrl+Enter=全曲
            if assist_on:
                if k in (Qt.Key_Tab, Qt.Key_Backtab):
                    if ctrl and hasattr(win, '_accept_assist_bar'):
                        win._accept_assist_bar()
                    elif hasattr(win, '_accept_assist_tab'):
                        win._accept_assist_tab()
                    return
                if ctrl and k in (Qt.Key_Return, Qt.Key_Enter):
                    if hasattr(win, '_accept_assist_full'):
                        win._accept_assist_full()
                    return
                if k == Qt.Key_Space and not keys:
                    notes = getattr(getattr(win, 'suggestion_bar', None), 'notes', None) or []
                    if notes and hasattr(win, 'statusBar'):
                        win.statusBar().showMessage(
                            f'候选：{notes[0].label} · {notes[0].reason}')
                    return
                # 1–5：切换 ghost 可能性（不写谱）；Shift+1–5 仍改时值
                num_map = {
                    Qt.Key_1: 0, Qt.Key_2: 1, Qt.Key_3: 2,
                    Qt.Key_4: 3, Qt.Key_5: 4,
                }
                if k in num_map and not shift:
                    if hasattr(win, '_select_assist_variant'):
                        win._select_assist_variant(num_map[k], locked=True)
                    return

            if k in (Qt.Key_Delete, Qt.Key_Backspace):
                if keys and c.delete_notes(keys):
                    self.selected_symbols.clear()
                    self.apply_highlight()
                    self.selection_changed.emit()
                return
            if k in (Qt.Key_Up, Qt.Key_Down) and keys:
                step = 12 if shift else 1
                if c.transpose_selected(keys, step if k == Qt.Key_Up else -step):
                    pass
                return
            if k in (Qt.Key_Left, Qt.Key_Right):
                self._move_selection(-1 if k == Qt.Key_Left else 1)
                return
            dur_map = {
                # 数字键（多数平台 Shift+2 仍报 Key_2）
                Qt.Key_1: NoteDuration.WHOLE,
                Qt.Key_2: NoteDuration.HALF,
                Qt.Key_3: NoteDuration.QUARTER,
                Qt.Key_4: NoteDuration.EIGHTH,
                Qt.Key_5: NoteDuration.SIXTEENTH,
                # macOS/美式键盘：Shift+1…5 常报成 ! @ # $ %，否则「Shift+2 没反应」
                Qt.Key_Exclam: NoteDuration.WHOLE,
                Qt.Key_At: NoteDuration.HALF,
                Qt.Key_NumberSign: NoteDuration.QUARTER,
                Qt.Key_Dollar: NoteDuration.EIGHTH,
                Qt.Key_Percent: NoteDuration.SIXTEENTH,
            }
            # 辅助 ON：Shift+数字（或 !@#$%）改时值；辅助 OFF：数字直接改时值
            shifted_glyph = k in (
                Qt.Key_Exclam, Qt.Key_At, Qt.Key_NumberSign,
                Qt.Key_Dollar, Qt.Key_Percent,
            )
            if k in dur_map and (shift or shifted_glyph or not assist_on):
                d = dur_map[k]
                c.duration = d
                if keys:
                    c.set_duration(keys, d)
                if self.state_callback:
                    self.state_callback(d, None)
                win = self.window()
                if hasattr(win, 'statusBar'):
                    win.statusBar().showMessage(
                        f'时值 → {DUR_NAME_ZH.get(d, str(d))}（也可点工具条「全/二/四/八/十六」）')
                return
            if k == Qt.Key_R and keys:
                c.toggle_rest(keys)
                return
            if k == Qt.Key_Period and keys:
                c.toggle_dots(keys)
                return
            mark_map = {Qt.Key_S: 'staccato', Qt.Key_A: 'accent',
                        Qt.Key_T: 'tenuto'}
            if k in mark_map and keys:
                c.toggle_mark(keys, mark_map[k])
                return
            # 升号：单独的 # 键；Shift+3 已在上方当作「四分」时值
            if k == Qt.Key_NumberSign and not shift:
                c.accidental = Accidental.SHARP
                if keys:
                    c.set_accidental(keys, Accidental.SHARP)
                if self.state_callback:
                    self.state_callback(None, Accidental.SHARP)
                return
            if k == Qt.Key_B:
                c.accidental = Accidental.FLAT
                if keys:
                    c.set_accidental(keys, Accidental.FLAT)
                if self.state_callback:
                    self.state_callback(None, Accidental.FLAT)
                return
            if ctrl and k == Qt.Key_Z:
                if shift:
                    c.redo()
                else:
                    c.undo()
                return
            if ctrl and k == Qt.Key_Y:
                c.redo()
                return
        super().keyPressEvent(event)

    # ---------- 编辑模式内部实现 ----------

    def _update_edit_hover(self, pos):
        c = self.edit_controller
        if c is None:
            return
        scene_pos = self.mapToScene(pos)
        x, y = scene_pos.x(), scene_pos.y()
        hit = self.renderer.hit_test(x, y)
        tool = c.tool
        if hit is None or tool in ('select', 'erase'):
            self.renderer.clear_ghost()
            if self.suggestion_callback:
                self.suggestion_callback([], None, None, None, None)
            self.setCursor(
                Qt.PointingHandCursor
                if (hit is not None and hit['kind'] == 'note') else Qt.ArrowCursor)
            return
        # note/rest 工具：ghost + 建议
        si, mi = hit['staff_idx'], hit['measure_idx']
        c_score = c.score
        if c_score is None or si >= len(c_score.staves):
            return
        rect = self._measure_rect(si, mi) or hit['rect']
        staff_y = self._staff_y_of(si, mi, hit)
        if staff_y is None:
            staff_y = rect.top() + 8
        clef = c_score.staves[si].clef
        position = (STAFF_HEIGHT - (y - staff_y)) / STAFF_LINE_SPACING
        position = snap_to_nearest_line(position)
        pitch = staff_position_to_pitch(position, clef)
        pitch.accidental = c.accidental
        idx = self._insert_index(hit, x)
        verdict = c.check(si, mi, pitch, c.duration, idx)
        if hit.get('kind') == 'ghost_cand':
            self.setCursor(Qt.PointingHandCursor)
            win = self.window()
            if hasattr(win, 'statusBar'):
                lab = hit.get('label') or '铺满稿'
                win.statusBar().showMessage(f'单击采纳灰色铺满 · {lab}')
            return
        self.renderer.draw_ghost(pitch, clef, x - 5, staff_y, c.duration,
                                 ok=not verdict.blocked,
                                 is_rest=(tool == 'rest'))
        cands = c.suggest(si, mi, idx, c.duration)
        if self.suggestion_callback:
            self.suggestion_callback(cands, verdict, si, mi, idx)
        win = self.window()
        if hasattr(win, 'statusBar'):
            dur_zh = DUR_NAME_ZH.get(c.duration, '')
            tip = f'悬停 {pitch_label(pitch)} · {dur_zh} · 单击写入'
            if tip != getattr(self, '_last_hover_tip', None):
                self._last_hover_tip = tip
                win.statusBar().showMessage(tip)

    def _insert_at(self, pos):
        c = self.edit_controller
        if c is None:
            return
        scene_pos = self.mapToScene(pos)
        x, y = scene_pos.x(), scene_pos.y()
        hit = self.renderer.hit_test(x, y)
        if hit is None:
            return
        if hit.get('kind') == 'ghost_cand':
            win = self.window()
            if hasattr(win, '_on_suggestion_picked'):
                win._assist_ctx = (
                    hit['staff_idx'], hit['measure_idx'], hit['insert_index'])
                win.suggestion_bar.set_context(
                    hit['staff_idx'], hit['measure_idx'], hit['insert_index'])
                win._on_suggestion_picked(hit['cand'])
            return
        si, mi = hit['staff_idx'], hit['measure_idx']
        rect = self._measure_rect(si, mi)
        if rect is None:
            return
        staff_y = self._staff_y_of(si, mi, hit)
        if staff_y is None:
            staff_y = rect.top() + 8
        c_score = c.score
        clef = c_score.staves[si].clef
        idx = self._insert_index(hit, x)
        if c.tool == 'rest':
            c.try_insert_note(si, mi, idx, None, c.duration, is_rest=True)
            return
        position = (STAFF_HEIGHT - (y - staff_y)) / STAFF_LINE_SPACING
        position = snap_to_nearest_line(position)
        pitch = staff_position_to_pitch(position, clef)
        pitch.accidental = c.accidental
        win = self.window()
        if hasattr(win, '_assist_ctx'):
            win._assist_ctx = (si, mi, (idx or 0) + 1)
            win.suggestion_bar.set_context(si, mi, (idx or 0) + 1)
        c.try_insert_note(si, mi, idx, pitch, c.duration)

    def _update_pitch_drag(self, pos):
        c = self.edit_controller
        if c is None or self._pitch_drag is None:
            return
        scene_pos = self.mapToScene(pos)
        x, y = scene_pos.x(), scene_pos.y()
        key = self._pitch_drag[0]
        si, mi = key[0], key[1]
        rect = self._measure_rect(si, mi)
        if rect is None or c.score is None or si >= len(c.score.staves):
            return
        staff_y = self._staff_y_of(si, mi)
        if staff_y is None:
            staff_y = rect.top() + 8
        clef = c.score.staves[si].clef
        position = (STAFF_HEIGHT - (y - staff_y)) / STAFF_LINE_SPACING
        position = snap_to_nearest_line(position)
        pitch = staff_position_to_pitch(position, clef)
        note = self._note_at(key)
        if note is None:
            return
        pitch.accidental = note.pitch.accidental
        self._drag_pitch_cur = pitch
        verdict = c.check(si, mi, pitch, note.duration, key[2])
        self.renderer.draw_ghost(pitch, clef, x - 5, staff_y, note.duration,
                                 ok=not verdict.blocked)
        if self.suggestion_callback:
            self.suggestion_callback([], verdict, si, mi, key[2])

    def _handle_select(self, event):
        self._handle_select_at(event.pos(), event.modifiers())

    def _handle_select_at(self, pos, mods):
        scene_pos = self.mapToScene(pos)
        hit = self.renderer.hit_test(scene_pos.x(), scene_pos.y())
        multi = bool(mods & Qt.ControlModifier)
        ranged = bool(mods & Qt.ShiftModifier)

        if hit is None:
            if not multi:
                self.clear_selection()
            return

        if hit['kind'] == 'note':
            key = (hit['staff_idx'], hit['measure_idx'], hit['note_idx'])
            if not multi and not ranged:
                self.selected_symbols = [key]
                self.selected_measures.clear()
            else:
                if key in self.selected_symbols:
                    self.selected_symbols = [k for k in self.selected_symbols if k != key]
                else:
                    self.selected_symbols.append(key)
                self.selected_measures.clear()
            self._anchor_measure = hit['measure_idx']
        else:
            mi = hit['measure_idx']
            if ranged and self._anchor_measure is not None:
                lo, hi = sorted((self._anchor_measure, mi))
                self.selected_measures = set(range(lo, hi + 1))
                self.selected_symbols.clear()
            elif multi:
                if mi in self.selected_measures:
                    self.selected_measures.discard(mi)
                else:
                    self.selected_measures.add(mi)
                self.selected_symbols.clear()
                self._anchor_measure = mi
            else:
                self.selected_measures = {mi}
                self.selected_symbols.clear()
                self._anchor_measure = mi

        self.apply_highlight()
        self.selection_changed.emit()


def _pitch_label_from_note(note) -> str:
    if getattr(note, 'is_rest', False):
        return 'rest'
    p = note.pitch
    acc = {Accidental.SHARP: '#', Accidental.FLAT: 'b',
           Accidental.DOUBLE_SHARP: '##', Accidental.DOUBLE_FLAT: 'bb'}.get(
        p.accidental, '')
    return f"{p.name.name}{acc}{p.octave}"


def _duration_label_from_note(note) -> str:
    mapping = {
        NoteDuration.WHOLE: 'whole',
        NoteDuration.HALF: 'half',
        NoteDuration.QUARTER: 'quarter',
        NoteDuration.EIGHTH: 'eighth',
        NoteDuration.SIXTEENTH: '16th',
        NoteDuration.THIRTY_SECOND: '32nd',
    }
    return mapping.get(note.duration, 'quarter')


class _HorizontalScrollArea(QScrollArea):
    """横向滚动容器：滚轮/触控板平移内容，不占竖向滚动条空间。"""

    def wheelEvent(self, event):
        sb = self.horizontalScrollBar()
        d = event.angleDelta()
        delta = d.x() or d.y()
        if delta:
            sb.setValue(sb.value() - delta // 2)
            event.accept()
            return
        super().wheelEvent(event)


class StudioWindow(QMainWindow):
    audio_ready = pyqtSignal(object)
    chat_ready = pyqtSignal(object)
    report_ready = pyqtSignal(object)
    mixer_ready = pyqtSignal(object)
    skeleton_ready = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowIcon(QIcon(os.path.join(_ROOT, "assets", "icon.svg")))
        self.setWindowTitle("乐谱工作室")
        # 默认窗口占满屏幕可用区域（自动排除任务栏/程序坞），随屏幕大小自适应
        try:
            scr = QApplication.primaryScreen().availableGeometry()
            self.setGeometry(scr)
        except Exception:
            self.setGeometry(80, 80, 1440, 900)
        self.setMinimumSize(1024, 640)

        self.audio = None
        self.sample_rate = SAMPLE_RATE
        self.playing = False
        self.audio_path = None
        self.synth_engine = None
        self._score_audio = None
        self._score_sr = SAMPLE_RATE
        self._score_engine = None
        self._viz_cache = {}
        self.report_path = PIRATE_REPORT_PATH
        self.musicxml_path = PIRATE_MUSICXML_PATH
        self.score = Score()
        self.m21_score = None
        self._compose_session = None
        self._chat_busy = False
        self._chat_id = None
        self._chat_messages = []
        self._suppress_chat_switch = False
        self._playback_events = []
        self._play_elapsed = QElapsedTimer()
        self._play_offset = 0.0  # 暂停前已播放的秒数
        self._paused_at = 0.0  # 暂停时的绝对位置（秒）
        # 混音台状态（每轨增益/静音/独奏 + 分轨音频缓存）
        self._mixer_key = None
        self._mixer_count = 0
        self._mixer_gains = []
        self._mixer_mutes = []
        self._mixer_solos = []
        self._staff_audio_cache = {}
        self._mixer_timer = QTimer(self)
        self._mixer_timer.setSingleShot(True)
        self._mixer_timer.setInterval(350)
        self._mixer_timer.timeout.connect(self._mixer_start)
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(40)
        self._play_timer.timeout.connect(self._on_play_tick)
        self._playback_scroll_measure = None
        self.edit_mode = False
        self._assist_enabled = True
        self._assist_ctx = None
        self._pending_play = False
        self._chat_persist_timer = QTimer(self)
        self._chat_persist_timer.setSingleShot(True)
        self._chat_persist_timer.setInterval(800)
        self._chat_persist_timer.timeout.connect(self._persist_current_chat)

        self._init_ui()
        # 默认加载当前对话的乐谱：_init_ui() 里的 _load_or_create_chats()
        # 已按 active chat 恢复其乐谱/报告，此处不要再用示例谱覆盖。

        self.audio_ready.connect(self._on_audio_ready)
        self.chat_ready.connect(self._on_chat_ready)
        self.report_ready.connect(self._on_report_ready)
        self.mixer_ready.connect(self._on_mixer_ready)
        self.skeleton_ready.connect(self._on_skeleton_ready)
        threading.Thread(target=self._startup_background, daemon=True).start()

    def _startup_background(self):
        """启动后后台导出示例乐章，避免阻塞首屏。已存在文件则跳过。"""
        try:
            if not os.path.exists(MUSICXML_PATH):
                write_score_bundle(build_music21_score())
        except Exception:
            pass
        try:
            if not os.path.exists(PIRATE_MUSICXML_PATH):
                write_score_bundle(
                    build_pirate_allegro_score(),
                    musicxml_path=PIRATE_MUSICXML_PATH,
                    midi_path=PIRATE_MIDI_PATH,
                    wav_path=PIRATE_WAV_PATH,
                )
        except Exception:
            pass
        try:
            ensure_bach_bundle(force=False)
        except Exception:
            pass
        try:
            if self.m21_score and self.score and self.report_path and not os.path.exists(self.report_path):
                export_analysis_report(
                    self.score, self.report_path,
                    compose_trace=_compose_trace_of(self))
                self.report_ready.emit(self.report_path)
            elif self.m21_score and self.report_path and os.path.exists(self.report_path):
                self.report_ready.emit(self.report_path)
        except Exception:
            pass
        if self.m21_score is not None:
            threading.Thread(target=self._synth_worker, daemon=True).start()

    def _on_report_ready(self, path):
        if isinstance(path, str) and path.startswith('ERR:'):
            QMessageBox.warning(self, "报告生成失败", path[4:])
            return
        if path:
            self.report_path = path
            self.load_report_into_ui()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.scene = QGraphicsScene()
        self.scene.setBackgroundBrush(QColor('#e9e7e1'))  # 乐谱纸面（深色 UI 中的纸质感）
        self.renderer = ScoreRenderer(self.scene)
        self.view = SelectableScoreView(self.scene, self.renderer)
        self.view.setObjectName("scoreView")
        self.view.selection_changed.connect(self._on_score_selection_changed)

        # ---- 编辑模式：控制器 + 调色板 + 智能建议条 ----
        self.edit_controller = EditController()
        self.edit_controller.score = self.score
        self.edit_palette = ScoreEditPalette()
        self.edit_palette.setVisible(False)
        self.score_palette = ScorePalettePanel()
        self.score_palette.setVisible(False)
        self.suggestion_bar = SuggestionBar()
        self.suggestion_bar.setVisible(False)
        self._wire_edit_controller()
        self._init_midi_keyboard()

        self.score_page = QWidget()
        self.score_page.setObjectName('scorePage')
        _sp_layout = QVBoxLayout(self.score_page)
        _sp_layout.setContentsMargins(0, 0, 0, 0)
        _sp_layout.setSpacing(0)
        _sp_layout.addWidget(self.edit_palette)
        _sp_layout.addWidget(self.suggestion_bar)
        _sp_mid = QHBoxLayout()
        _sp_mid.setContentsMargins(0, 0, 0, 0)
        _sp_mid.setSpacing(0)
        _sp_mid.addWidget(self.score_palette)
        _sp_mid.addWidget(self.view, 1)
        _sp_layout.addLayout(_sp_mid, 1)

        # 走带条按钮多，包进可横向滚动的容器：窄窗口时可以滑动而不是撑爆宽度
        transport_host = _HorizontalScrollArea()
        transport_host.setWidgetResizable(True)
        transport_host.setFrameShape(QFrame.NoFrame)
        transport_host.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        transport_host.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        transport_host.setWidget(self._build_transport_bar())
        layout.addWidget(transport_host)

        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        new_piano_action = QAction("新建钢琴四部和声谱...", self)
        new_piano_action.setShortcut("Ctrl+Shift+N")
        new_piano_action.triggered.connect(self._new_piano_satb_score)
        file_menu.addAction(new_piano_action)
        add_bars_action = QAction("增加小节...", self)
        add_bars_action.setShortcut("Ctrl+Shift+B")
        add_bars_action.setToolTip("在当前乐谱末尾追加空小节")
        add_bars_action.triggered.connect(self._add_measures_dialog)
        file_menu.addAction(add_bars_action)
        add_part_action = QAction("添加声部...", self)
        add_part_action.setShortcut("Ctrl+Shift+P")
        add_part_action.setToolTip("为当前乐谱追加空声部（低音/钢琴左手等）")
        add_part_action.triggered.connect(self._add_part_dialog)
        file_menu.addAction(add_part_action)
        accomp_action = QAction("设置配乐...", self)
        accomp_action.setShortcut("Ctrl+Shift+A")
        accomp_action.setToolTip("根据旋律声部为低音写入伴奏")
        accomp_action.triggered.connect(self._accomp_dialog)
        file_menu.addAction(accomp_action)
        open_score_action = QAction("打开乐谱 (MusicXML/MIDI)...", self)
        open_score_action.setShortcut("Ctrl+Shift+O")
        open_score_action.triggered.connect(self.open_score)
        file_menu.addAction(open_score_action)
        open_action = QAction("打开音频...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_audio)
        file_menu.addAction(open_action)
        restore_action = QAction("恢复乐谱音频", self)
        restore_action.triggered.connect(self.restore_score_audio)
        file_menu.addAction(restore_action)
        file_menu.addSeparator()
        export_menu = file_menu.addMenu("导出")
        export_menu.addAction("MusicXML...", self.export_musicxml_dialog)
        export_menu.addAction("MIDI...", self.export_midi_dialog)
        export_menu.addAction("WAV...", self.export_wav_dialog)
        export_menu.addAction("LilyPond (.ly)...", self.export_lilypond_dialog)
        export_menu.addAction("分析报告...", self.export_report)
        export_menu.addAction("程序员报告（查看）...", self.show_dev_report)
        export_menu.addAction("归档到 runs/ ...", self.archive_run_dialog)
        file_menu.addSeparator()
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        self._view_menu = menubar.addMenu("视图")

        tools_menu = menubar.addMenu("工具")
        skeleton_action = QAction("风格骨架生成器…", self)
        skeleton_action.setShortcut("Ctrl+G")
        skeleton_action.setToolTip("用确定性脚本按风格包生成乐谱骨架（含赋格段），不调 LLM")
        skeleton_action.triggered.connect(self._focus_skeleton_generator)
        tools_menu.addAction(skeleton_action)

        midi_menu = tools_menu.addMenu("MIDI 键盘")
        midi_menu.setToolTip("接入外部 MIDI 键盘步进输入（macOS / Windows）")
        self._midi_enable_action = QAction("启用 MIDI 输入", self)
        self._midi_enable_action.setCheckable(True)
        self._midi_enable_action.setShortcut("Ctrl+Shift+M")
        self._midi_enable_action.triggered.connect(self._on_midi_menu_toggled)
        midi_menu.addAction(self._midi_enable_action)
        midi_ports_act = QAction("选择输入设备…", self)
        midi_ports_act.triggered.connect(self._midi_select_port_dialog)
        midi_menu.addAction(midi_ports_act)
        midi_oct_up = QAction("八度 +1", self)
        midi_oct_up.triggered.connect(lambda: self._midi_shift_octave(12))
        midi_menu.addAction(midi_oct_up)
        midi_oct_dn = QAction("八度 −1", self)
        midi_oct_dn.triggered.connect(lambda: self._midi_shift_octave(-12))
        midi_menu.addAction(midi_oct_dn)
        midi_chord = QAction("和弦模式（抬键后写入）", self)
        midi_chord.setCheckable(True)
        midi_chord.triggered.connect(self._on_midi_chord_mode)
        midi_menu.addAction(midi_chord)
        self._midi_chord_action = midi_chord
        midi_menu.addSeparator()
        midi_hands = QAction("左右手分谱（钢琴大谱表）", self)
        midi_hands.setCheckable(True)
        midi_hands.setChecked(True)
        midi_hands.setToolTip("按分手点把音分到右手高音谱表 / 左手低音谱表；单谱表时自动补左手")
        midi_hands.triggered.connect(self._on_midi_hands_toggled)
        midi_menu.addAction(midi_hands)
        self._midi_hands_action = midi_hands
        midi_pad = QAction("另一只手补休止（两手对齐）", self)
        midi_pad.setCheckable(True)
        midi_pad.setChecked(True)
        midi_pad.triggered.connect(self._on_midi_pad_toggled)
        midi_menu.addAction(midi_pad)
        midi_split = QAction("分手点…", self)
        midi_split.triggered.connect(self._midi_split_dialog)
        midi_menu.addAction(midi_split)
        midi_status = QAction("MIDI 状态…", self)
        midi_status.triggered.connect(self._midi_status_dialog)
        midi_menu.addAction(midi_status)

        # 体系层（非功能和声）：原先在左栏浏览器的「体系层」节点里，左栏已删，改挂菜单
        sys_menu = tools_menu.addMenu("体系层（非功能和声）")
        sys_menu.setToolTip("不走功能和声的另一套组织规则；确定性生成，不调 LLM")
        try:
            import systems as _sy
            _sys_tips = {
                'banqiang': '板腔体：上下句 + 板式变化 + 过门，五声音阶。',
                'qupai': '曲牌体：固定字位网格填腔，多牌联套。',
                'raga': '拉格：调式 + 特征乐句 + 塔拉循环，低声部持续音。',
                'serial': '十二音序列：音列 P/R/I/RI 四形态轮换，无调中心。',
            }
            for sd in _sy.list_systems():
                act = QAction(sd['label'], self)
                act.setToolTip(f"{_sys_tips.get(sd['id'], sd['id'])}（确定性生成，不调 LLM）")
                act.triggered.connect(
                    lambda _c=False, s=sd['id']: self.generate_system(s))
                sys_menu.addAction(act)
        except Exception:
            sys_menu.setEnabled(False)

        # ---- 作曲库菜单：任务 Skill / 风格 Genre / 内核脚本 的唯一入口 ----
        lib_menu = menubar.addMenu("作曲库")
        lib_open = QAction("作曲库管理器…", self)
        lib_open.setShortcut("Ctrl+L")
        lib_open.setToolTip("任务 Skill、风格 Genre、确定性脚本、规则库的查看与编辑")
        lib_open.triggered.connect(lambda: self.open_skill_library())
        lib_menu.addAction(lib_open)
        lib_menu.addSeparator()
        for label, tab in (
            ("诊断：库里哪些东西真正生效", SkillLibraryDialog.TAB_DIAG),
            ("任务 Skill（路由规则与提示词）", SkillLibraryDialog.TAB_SKILL),
            ("风格 Genre（含实证契约）", SkillLibraryDialog.TAB_GENRE),
            ("内核脚本库（确定性脚本与参数）", SkillLibraryDialog.TAB_SCRIPT),
            ("规则库（对位 / 教学 / 脚本 · 文档+代码）", SkillLibraryDialog.TAB_RULES),
            ("维度 taxonomy", SkillLibraryDialog.TAB_TAXONOMY),
        ):
            act = QAction(label, self)
            act.triggered.connect(lambda _c=False, t=tab: self.open_skill_library(t))
            lib_menu.addAction(act)
        lib_menu.addSeparator()
        reload_skills_action = QAction("重新加载库", self)
        reload_skills_action.triggered.connect(self.reload_skill_library)
        lib_menu.addAction(reload_skills_action)

        # ---- 帮助菜单 ----
        help_menu = menubar.addMenu("帮助")
        help_full = QAction("帮助文档（完整 · 每条带示例）", self)
        help_full.setShortcut("Ctrl+H")
        help_full.triggered.connect(lambda: self.show_help_dialog(None, '帮助文档'))
        help_menu.addAction(help_full)
        help_topic = QAction("查看单项说明（命令/功能）…", self)
        help_topic.triggered.connect(self._show_help_topic_prompt)
        help_menu.addAction(help_topic)
        help_design = QAction("设计理念 · 护城河", self)
        help_design.triggered.connect(
            lambda: self.show_help_dialog('确定性内核与护城河', '设计理念'))
        help_menu.addAction(help_design)
        help_edit = QAction("编辑教程 · 莫扎特小夜曲（16小节）", self)
        help_edit.triggered.connect(
            lambda: self.show_help_dialog('编辑教程', '编辑教程 · 莫扎特小夜曲'))
        help_menu.addAction(help_edit)
        help_menu.addSeparator()
        help_pro = QAction("专业文档（命令/功能实测验证版）", self)
        help_pro.triggered.connect(
            lambda: self.open_help_doc_external('runs/help_pro.md'))
        help_menu.addAction(help_pro)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # 底部停靠区：混音台 / 可视化 / 分析报告
        self._build_mixer_page()

        self.viz_names = ["Mel 频谱", "波形", "线性频谱", "色度图", "能量包络"]
        self.viz_canvases = {}
        self.viz_figures = {}
        for name in self.viz_names:
            fig = Figure(figsize=(10, 4.2), tight_layout=False,
                         facecolor=_VIZ_FIG_BG)
            canvas = FigureCanvas(fig)
            self.viz_figures[name] = fig
            self.viz_canvases[name] = canvas
            self.tabs.addTab(canvas, name)

        self.report_view = QTextBrowser()
        self.report_view.setOpenExternalLinks(True)
        self.report_view.setFont(QFont("Consolas", 10))
        self.tabs.addTab(self.report_view, "分析报告")

        # 程序员版报告（同一席面谱的「怎么说 vs 怎么写」的入竞价据）
        self.report_dev_view = QTextBrowser()
        self.report_dev_view.setOpenExternalLinks(True)
        self.report_dev_view.setFont(QFont("Consolas", 10))
        self.tabs.addTab(self.report_dev_view, "程序员报告")

        # 可视化下拉框放停靠区右上角
        self.viz_combo = QComboBox()
        self.viz_combo.addItems(["（不显示可视化）", "Mel 频谱", "波形", "线性频谱", "色度图", "能量包络"])
        self.viz_combo.setCurrentIndex(0)
        self.viz_combo.currentIndexChanged.connect(self._on_viz_combo)
        self.tabs.setCornerWidget(self.viz_combo)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # 中央工作区：谱面（主）/ 编排（MuseScore 声部+时间线）/ 钢琴卷帘
        self.piano_roll = PianoRollView()
        self.arrangement = ArrangementView()
        self.piano_roll.seekRequested.connect(self.seek_to)
        self.arrangement.trackSelected.connect(self._on_track_selected)
        self.arrangement.muteToggled.connect(self._on_arr_mute)
        self.arrangement.soloToggled.connect(self._on_arr_solo)
        self.workspace = QTabWidget()
        self.workspace.setDocumentMode(True)
        self.workspace.addTab(self.score_page, "谱面")
        self.workspace.addTab(self.arrangement, "编排")
        self.workspace.addTab(self.piano_roll, "钢琴卷帘")
        self.workspace.currentChanged.connect(self._on_workspace_changed)

        # 中央：工作区在上，底部停靠区（混音台/可视化/报告）在下
        vw, vh = self.width(), max(self.height(), 640)
        left_splitter = QSplitter(Qt.Vertical)
        left_splitter.setHandleWidth(4)
        left_splitter.addWidget(self.workspace)
        left_splitter.addWidget(self.tabs)
        left_splitter.setCollapsible(0, False)
        left_splitter.setCollapsible(1, False)
        left_splitter.setStretchFactor(0, 1)
        left_splitter.setStretchFactor(1, 0)
        left_splitter.setSizes([max(320, vh - 300), 240])
        self.center_splitter = left_splitter

        # 左侧不再有「浏览器」面板（风格树已删）。
        # 风格基调只从右侧检查器的「风格包」下拉里选，且只列学过的包。

        # 右侧：仅对话（检查器 Tab 已移除；骨架/风格改走菜单与对话命令）
        self._init_chat_panel()
        self.inspector = None  # 兼容旧引用；功能改由对话 / 菜单承担
        self.right_tabs = QTabWidget()
        self.right_tabs.setDocumentMode(True)
        self.right_tabs.addTab(self.chat_panel, "对话")
        self.right_dock = QDockWidget("对话", self)
        self.right_dock.setObjectName("rightDock")
        self.right_dock.setWidget(self.right_tabs)
        self.right_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.RightDockWidgetArea, self.right_dock)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setHandleWidth(4)
        self.main_splitter.addWidget(left_splitter)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setSizes([max(560, vw - 40)])
        layout.addWidget(self.main_splitter, 1)

        # 视图菜单（依赖上面建好的停靠面板与走带按钮）
        vm = self._view_menu
        vm.addAction(self.right_dock.toggleViewAction())
        vm.addSeparator()
        vm.addAction("工作区：谱面", lambda: self.workspace.setCurrentIndex(0))
        vm.addAction("工作区：编排", lambda: self.workspace.setCurrentIndex(1))
        vm.addAction("工作区：钢琴卷帘", lambda: self.workspace.setCurrentIndex(2))
        vm.addSeparator()
        self.loop_action = QAction("循环播放", self)
        self.loop_action.setCheckable(True)
        # 注意：Ctrl+L 已被「作曲库管理器」占用（同一窗口内重复绑定会让两个快捷键
        # 都变成 ambiguous、谁也触发不了），循环播放让位到 Ctrl+Shift+L。
        self.loop_action.setShortcut("Ctrl+Shift+L")
        self.loop_action.triggered.connect(self.set_loop)
        vm.addAction(self.loop_action)
        self.metro_action = QAction("节拍器", self)
        self.metro_action.setCheckable(True)
        self.metro_action.setShortcut("Ctrl+M")
        self.metro_action.triggered.connect(self.set_metronome)
        vm.addAction(self.metro_action)
        self.follow_action = QAction("跟随播放头", self)
        self.follow_action.setCheckable(True)
        self.follow_action.setChecked(True)
        self.follow_action.triggered.connect(self.set_follow)
        vm.addAction(self.follow_action)
        vm.addSeparator()
        lang_menu = vm.addMenu("界面语言 / Language")
        lang_group = QActionGroup(self)
        for code, label in (('zh', '中文'), ('en', 'English')):
            act = QAction(label, self, checkable=True)
            act.setChecked(i18n.language() == code)
            act.triggered.connect(lambda _=False, c=code: self.set_ui_language(c))
            lang_group.addAction(act)
            lang_menu.addAction(act)

        # 走带状态默认值
        self._loop = False
        self._loop_in = 0.0
        self._loop_out = None
        self._metronome = False
        self._follow = True
        self._clicks = None
        self._clicks_key = None
        self._sections = []

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.view.clear_selection)
        QShortcut(QKeySequence(Qt.Key_Space), self.view, activated=self.play)
        # Tab / Ctrl+Tab / Ctrl+Enter：下一音 · 本小节 · 全曲
        self._tab_assist_sc = QShortcut(QKeySequence(Qt.Key_Tab), self)
        self._tab_assist_sc.setContext(Qt.WindowShortcut)
        self._tab_assist_sc.setEnabled(False)
        self._tab_assist_sc.activated.connect(self._accept_assist_tab)
        self._bar_assist_sc = QShortcut(QKeySequence('Ctrl+Tab'), self)
        self._bar_assist_sc.setContext(Qt.WindowShortcut)
        self._bar_assist_sc.setEnabled(False)
        self._bar_assist_sc.activated.connect(self._accept_assist_bar)
        self._full_assist_sc = QShortcut(QKeySequence('Ctrl+Return'), self)
        self._full_assist_sc.setContext(Qt.WindowShortcut)
        self._full_assist_sc.setEnabled(False)
        self._full_assist_sc.activated.connect(self._accept_assist_full)
        # 编辑时值：挂在谱面视图上，点谱后 Shift+1…5 才生效（并兼容 macOS 的 !@#$%）
        for seq, dur in (
            ('Shift+1', NoteDuration.WHOLE),
            ('Shift+2', NoteDuration.HALF),
            ('Shift+3', NoteDuration.QUARTER),
            ('Shift+4', NoteDuration.EIGHTH),
            ('Shift+5', NoteDuration.SIXTEENTH),
            ('!', NoteDuration.WHOLE),
            ('@', NoteDuration.HALF),
            ('$', NoteDuration.EIGHTH),
            ('%', NoteDuration.SIXTEENTH),
        ):
            sc = QShortcut(QKeySequence(seq), self.view)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda d=dur: self._shortcut_set_duration(d))

        self.setStyleSheet(_APP_STYLESHEET)

    def _shortcut_set_duration(self, duration):
        """谱面快捷键改插入时值（工具条同步 + 状态栏提示）。"""
        if not self.edit_mode:
            return
        self.edit_palette._pick_duration(duration)
        self.statusBar().showMessage(
            f'时值 → {DUR_NAME_ZH.get(duration, str(duration))}（也可点工具条「二」「四」）')

    def _build_transport_bar(self):
        """顶部走带控制条（DAW 风格）：走带控制 + 位置 LCD + 工程信息 + 文件/导出。"""
        bar = QWidget()
        bar.setObjectName("transportBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(6)

        def _btn(text, tip, cb, fixed=None):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            if fixed:
                b.setFixedSize(*fixed)
            return b

        def _sep():
            f = QFrame()
            f.setFrameShape(QFrame.VLine)
            f.setStyleSheet("color:#34373f;")
            return f

        h.addWidget(_btn("⏮", "回到开头", self.rewind_to_start, (40, 30)))

        self.play_btn = QPushButton("▶")
        self.play_btn.setObjectName("playBtn")
        self.play_btn.setFixedSize(46, 32)
        self.play_btn.setEnabled(False)
        self.play_btn.setToolTip("播放 / 暂停（乐谱区获得焦点后可按空格）")
        self.play_btn.clicked.connect(self.play)
        h.addWidget(self.play_btn)

        h.addWidget(_btn("⏹", "停止", self.stop, (40, 30)))

        self.edit_btn = QPushButton("✎ 编辑")
        self.edit_btn.setObjectName("editBtn")
        self.edit_btn.setCheckable(True)
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.setToolTip(
            "五线谱编辑模式：谱面上直接插入/拖拽/删除音符与记号。\n"
            "智能提示「下一个符号」，违反乐理规则实时禁止（红）/警告（黄）。")
        self.edit_btn.toggled.connect(self.set_edit_mode)
        h.addWidget(self.edit_btn)

        self.lcd_label = QLabel("001:1  00:00.0")
        self.lcd_label.setObjectName("lcdLabel")
        self.lcd_label.setAlignment(Qt.AlignCenter)
        self.lcd_label.setFixedWidth(172)
        h.addWidget(self.lcd_label)

        self.info_label = QLabel("♪ 正在从 MusicXML 加载…")
        self.info_label.setMinimumWidth(60)  # 窄窗口时可压缩，避免撑大走带条最小宽度
        h.addWidget(self.info_label, stretch=1)

        h.addWidget(QLabel("主音量"))
        self.master_vol = QSlider(Qt.Horizontal)
        self.master_vol.setRange(0, 100)
        self.master_vol.setValue(100)
        self.master_vol.setFixedWidth(110)
        self.master_vol.setToolTip("播放增益（自本次播放开始生效）")
        h.addWidget(self.master_vol)
        self.master_vol_label = QLabel("100")
        h.addWidget(self.master_vol_label)
        self.master_vol.valueChanged.connect(
            lambda v: self.master_vol_label.setText(str(v)))

        h.addWidget(_sep())
        # 缩放 / 视图控制
        h.addWidget(_btn("🔍−", "缩小乐谱", lambda: self.view.zoom_step(-1), (40, 30)))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setToolTip("当前缩放比例（点击恢复 100%）")
        self.zoom_label.setObjectName("zoomLabel")
        self.zoom_label.setFixedWidth(48)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        zoom_reset_cursor = self.zoom_label.cursor()
        self.zoom_label.setCursor(Qt.PointingHandCursor)
        self.zoom_label.mousePressEvent = lambda e: self.view.zoom_reset()
        h.addWidget(self.zoom_label)
        h.addWidget(_btn("🔍+", "放大乐谱", lambda: self.view.zoom_step(1), (40, 30)))
        h.addWidget(_btn("⤢ 宽度", "横向铺满：整页宽度适应窗口", self.view.zoom_fit_width))
        h.addWidget(_btn("◎ 聚焦", "跳到正在播放的小节（未播放时回开头）", self.focus_playback_measure))
        self.view.zoom_changed.connect(
            lambda rel: self.zoom_label.setText(f"{int(round(rel * 100))}%"))

        h.addWidget(_sep())
        h.addWidget(_btn("🎼 打开乐谱", "打开 MusicXML / MIDI", self.open_score))
        h.addWidget(_btn("📂 打开音频", "打开音频文件", self.open_audio))
        self.restore_btn = _btn("↩ 乐谱音频", "切回乐谱原始音频", self.restore_score_audio)
        self.restore_btn.setEnabled(False)
        h.addWidget(self.restore_btn)

        h.addWidget(_sep())
        h.addWidget(_btn("XML", "导出 MusicXML", self.export_musicxml_dialog))
        h.addWidget(_btn("MIDI", "导出 MIDI", self.export_midi_dialog))
        self.export_wav_btn = _btn("WAV", "导出 WAV", self.export_wav_dialog)
        self.export_wav_btn.setEnabled(False)
        h.addWidget(self.export_wav_btn)
        h.addWidget(_btn("刷新报告", "重新生成分析报告", self.refresh_report_panel))
        h.addWidget(_btn("保存报告", "另存分析报告", self.export_report))

        # DAW 增强：循环 / 入出点 / 节拍器 / 跟随播放头 / 吸附
        TransportExtras.build(self, h)
        return bar

    def _master_gain(self):
        try:
            return max(0.0, self.master_vol.value() / 100.0)
        except Exception:
            return 1.0

    # ---------------- DAW：走带增强 ----------------

    def set_loop(self, on: bool):
        self._loop = bool(on)
        self.loop_btn.setChecked(self._loop)
        if hasattr(self, 'loop_action'):
            self.loop_action.setChecked(self._loop)
        self.statusBar().showMessage(
            f"循环播放：{'开' if self._loop else '关'}"
            + (f"（{self._loop_in:.1f}s → {self._loop_out:.1f}s）"
               if self._loop and self._loop_out else ""))

    def set_punch_in(self):
        t = self._current_play_time()
        self._loop_in = max(0.0, t)
        self.statusBar().showMessage(f"循环入点：{self._loop_in:.2f}s")

    def set_punch_out(self):
        t = self._current_play_time()
        if t <= self._loop_in + 0.05:
            self.statusBar().showMessage("出点必须在入点之后")
            return
        self._loop_out = t
        self.statusBar().showMessage(f"循环出点：{self._loop_out:.2f}s")

    def set_ui_language(self, lang: str):
        rt = getattr(QApplication.instance(), '_retranslator', None)
        if rt is None:
            i18n.set_language(lang)
            return
        rt.apply(lang)

    def set_metronome(self, on: bool):
        self._metronome = bool(on)
        self.metro_btn.setChecked(self._metronome)
        if hasattr(self, 'metro_action'):
            self.metro_action.setChecked(self._metronome)
        self.statusBar().showMessage(f"节拍器：{'开' if self._metronome else '关'}"
                                     "（下次播放生效）")

    def set_follow(self, on: bool):
        self._follow = bool(on)
        self.follow_btn.setChecked(self._follow)
        if hasattr(self, 'follow_action'):
            self.follow_action.setChecked(self._follow)

    def _current_play_time(self) -> float:
        if self.playing is True:
            return self._play_offset + self._play_elapsed.elapsed() / 1000.0
        return float(getattr(self, '_paused_at', 0.0) or 0.0)

    def seek_to(self, seconds: float):
        """点击钢琴卷帘/编排视图 → 定位播放头。"""
        if self.audio is None:
            return
        sr = self.sample_rate or SAMPLE_RATE
        t = max(0.0, min(float(seconds), len(self.audio) / float(sr) - 0.01))
        self._paused_at = t
        self._update_lcd(t)
        self.piano_roll.set_playhead(t)
        self.arrangement.set_playhead(t)
        if self.playing is True:
            self._play_from(t)
        else:
            self.playing = 'paused'
            self.play_btn.setText("▶")
            self.statusBar().showMessage(f"播放头定位到 {t:.2f}s（空格继续）")

    def _ensure_clicks(self):
        """节拍器点击轨（与音频等长，按当前 BPM/拍号生成；小节首拍重音）。"""
        if self.audio is None:
            return None
        try:
            bpm = max(float(self.score.tempo) or 120.0, 1.0)
            bpb = max(float(self.score.time_signature.beats_per_measure()) or 4.0, 1.0)
        except Exception:
            bpm, bpb = 120.0, 4.0
        sr = self.sample_rate or SAMPLE_RATE
        key = (bpm, bpb, len(self.audio), sr)
        if self._clicks is not None and self._clicks_key == key:
            return self._clicks
        spb = 60.0 / bpm
        arr = np.zeros(len(self.audio), dtype=np.float32)
        dur = len(self.audio) / float(sr)
        n = int(0.035 * sr)
        env = np.exp(-np.linspace(0.0, 9.0, n)).astype(np.float32)
        t, i = 0.0, 0
        while t < dur:
            accent = (i % round(bpb)) == 0
            freq = 1680.0 if accent else 1150.0
            amp = 0.34 if accent else 0.18
            wave = (np.sin(2 * np.pi * freq * np.arange(n) / sr) * env * amp).astype(np.float32)
            s0 = int(t * sr)
            s1 = min(len(arr), s0 + n)
            if s1 > s0:
                arr[s0:s1] += wave[:s1 - s0]
            t += spb
            i += 1
        self._clicks, self._clicks_key = arr, key
        return arr

    def _play_from(self, start: float):
        """从指定秒数开始播放（带主音量与节拍器叠加）。"""
        if self.audio is None:
            return
        sr = self.sample_rate or SAMPLE_RATE
        idx = min(max(int(max(0.0, start) * sr), 0), len(self.audio))
        gain = self._master_gain()
        seg = np.asarray(self.audio[idx:], dtype=np.float32)
        if gain < 0.999:
            seg = seg * gain
        if self._metronome:
            clicks = self._ensure_clicks()
            if clicks is not None and len(clicks) == len(self.audio):
                seg = seg + clicks[idx:idx + len(seg)]
        sd.stop()
        _sd_play_safe(np.asarray(seg, dtype=np.float32), sr)
        self._play_offset = float(start)
        self._play_elapsed.restart()
        self._play_timer.start()
        self.playing = True
        self.play_btn.setText("⏸")

    def _finish_playback(self):
        self.playing = False
        self._play_timer.stop()
        self._play_offset = 0.0
        self._paused_at = 0.0
        self.renderer.clear_playback()
        self._playback_scroll_measure = None
        self.play_btn.setText("▶")
        self._update_lcd(0.0)
        self.statusBar().showMessage("播放完成")

    # ---------------- DAW：视图同步 ----------------

    def _update_daw_views(self):
        """用当前乐谱模型刷新钢琴卷帘 / 编排视图。"""
        try:
            bpm = float(self.score.tempo or 120)
            bpb = float(self.score.time_signature.beats_per_measure() or 4)
            self.piano_roll.set_score(self.score, bpm, bpb)
            self.arrangement.set_score(self.score, bpm, bpb, self._sections)
        except Exception:
            pass

    def _on_workspace_changed(self, index: int):
        if index in (1, 2):
            self._update_daw_views()

    def _on_track_selected(self, idx: int):
        try:
            self.tabs.setCurrentIndex(0)      # 混音台
            if hasattr(self.arrangement, 'select_track'):
                self.arrangement.select_track(idx)
            self.statusBar().showMessage(f"已选中声部 {idx + 1}（混音台第 {idx + 1} 条通道）")
        except Exception:
            pass

    def _on_arr_mute(self, idx: int, on: bool):
        btns = getattr(self, '_mixer_mute_btns', None) or []
        if 0 <= idx < len(btns):
            btns[idx].setChecked(bool(on))

    def _on_arr_solo(self, idx: int, on: bool):
        btns = getattr(self, '_mixer_solo_btns', None) or []
        if 0 <= idx < len(btns):
            btns[idx].setChecked(bool(on))

    # ---------------- DAW：体系层生成（确定性，不调 LLM）----------------

    def generate_system(self, sysid: str):
        """按体系层（板腔/曲牌/拉格/序列）生成——确定性，不调 LLM。

        原先从左侧浏览器的「体系层」节点双击触发；左栏删掉后改由
        工具菜单 →「体系层（非功能和声）」进入，能力不丢。
        """
        try:
            import systems as _sy
            from llm_compose import spec_to_music21
            opts = {}
            if self._compose_session is not None:
                prefs = self._compose_session.prefs or {}
                opts = {
                    'bars': int(prefs.get('bars') or 16),
                    'seed': str(prefs.get('seed') or '') or None,
                }
            kw = {'bars': int(opts.get('bars') or 16),
                  'seed': str(opts.get('seed') or '') or None}
            kw = {k: v for k, v in kw.items() if v}
            spec = _sy.build_system(sysid, **kw)
            score = spec_to_music21(spec)
            self._apply_composed_score(score, spec, note=f'体系层 · {sysid}')
            self._sections = list(spec.get('sections') or [])
            self._update_daw_views()
            self._chat_append(
                "助手",
                f"已按**{_sy.SYSTEM_LABELS.get(sysid, sysid)}**生成"
                f"（{spec.get('bars')} 小节，seed {spec.get('seed')}）。\n\n"
                "体系层不走功能和声：它按各自的规则组织音乐"
                "（上下句落音 / 字位填腔 / 调式+塔拉 / 音列四形态）。")
            self.right_tabs.setCurrentIndex(0)
        except Exception as e:
            QMessageBox.critical(self, "体系层生成失败", str(e))

    def _focus_skeleton_generator(self):
        self.right_dock.show()
        self.right_tabs.setCurrentIndex(0)
        try:
            self.chat_input.setFocus()
            tip = (
                '骨架生成：在对话输入 `/skeleton 圆舞曲` 或 `/style 圆舞曲` 后创曲。\n'
                '（检查器已移除，风格请用对话命令或上方风格下拉。）'
            )
            self._chat_append('助手', tip)
        except Exception:
            pass
        self.statusBar().showMessage('在对话中用 /skeleton 或 /style 生成骨架')

    @staticmethod
    def _split_brief_commands(brief: str):
        """把「变奏」框内容拆成 /命令 覆盖项 + 剩余自然语言。

        确定性骨架路径原先完全忽略 brief——在检查器里写的 /fugue full、
        /voices 3、/bars 24 等命令不产生任何作用，表现就是「命令没执行」。
        """
        try:
            from llm_compose import parse_user_turn
            cmds, rest = parse_user_turn(brief or '')
        except Exception:
            cmds, rest = [], (brief or '')
        ov: dict = {}
        for name, arg in cmds or []:
            a = str(arg or '').strip()
            try:
                if name == '/bars':
                    m = re.search(r'\d+', a)
                    if m and 8 <= int(m.group()) <= 32:
                        ov['bars'] = int(m.group())
                elif name == '/voices':
                    m = re.search(r'\d+', a)
                    if m and int(m.group()) in (2, 3, 4):
                        ov['voices'] = int(m.group())
                elif name == '/fugue':
                    w = a.split()[0].lower() if a else 'auto'
                    if w in ('auto', 'full', 'off'):
                        ov['fugue'] = w
                elif name == '/key':
                    if a:
                        ov['key'] = a.split()[0]
                elif name == '/tempo':
                    m = re.search(r'\d+', a)
                    if m:
                        ov['tempo'] = int(m.group())
                elif name == '/meter':
                    m = re.search(r'\d+\s*/\s*\d+', a)
                    if m:
                        ov['meter'] = m.group().replace(' ', '')
                elif name == '/style':
                    if a:
                        ov['style'] = a
            except Exception:
                continue
        return ov, (rest or '').strip()

    def generate_skeleton(self, opts: dict):
        """确定性骨架：不调 LLM，直接按风格包生成。"""
        opts = dict(opts or {})
        ov, mood = self._split_brief_commands(opts.get('brief') or '')
        if ov.get('style'):
            try:
                import style_skeleton
                g = style_skeleton.resolve_genre(ov['style'])
                if g is not None:
                    opts['genre_id'] = g.get('id') or g.get('pack_id') or opts.get('genre_id')
            except Exception:
                pass
        if not opts.get('genre_id'):
            QMessageBox.information(
                self, "生成骨架",
                "请先用对话选择风格：例如 `/style 圆舞曲` 或 `/skeleton 圆舞曲`。")
            self.right_tabs.setCurrentIndex(0)
            return
        opts.update({k: v for k, v in ov.items() if k != 'style'})
        opts['mood'] = mood
        self.statusBar().showMessage("正在用确定性脚本生成骨架（含赋格段）…")
        threading.Thread(target=self._skeleton_worker, args=(opts,),
                         daemon=True).start()

    def _skeleton_worker(self, opts: dict):
        try:
            import style_skeleton
            from llm_compose import spec_to_music21
            gid = opts['genre_id']
            kw = {'bars': int(opts.get('bars') or 16),
                  'fugue': opts.get('fugue') or 'auto'}
            if opts.get('voices'):
                kw['voices'] = int(opts['voices'])
            if opts.get('seed'):
                kw['seed'] = str(opts['seed'])
            if opts.get('key'):
                kw['key'] = str(opts['key'])
            if opts.get('tempo'):
                kw['tempo'] = int(opts['tempo'])
            if opts.get('meter'):
                kw['meter'] = str(opts['meter'])
            if opts.get('mood'):
                kw['mood'] = str(opts['mood'])
            # 逐脚本记录：骨架路径本来就是确定性内核，报告 4.3 必须看得见这条链，
            # 否则程序员版会误报「未经过确定性脚本内核」
            kernel_trace: list = []
            spec = style_skeleton.skeleton_spec(gid, trace=kernel_trace, **kw)
            score = spec_to_music21(spec)
            report = style_skeleton.skeleton_report(gid, **kw)
            trace = {
                'user_text': str(opts.get('brief') or opts.get('genre_id') or ''),
                'pipeline': 'skeleton',
                'skill_ids': ['compose-full'],
                'llm_calls': [],
                'emotion': None,
                'emotion_applied': False,
                'constraint_fixes': [],
                'kernel_calls': kernel_trace,
                'repair': None,
                'result': {},
            }
            self.skeleton_ready.emit((score, spec, report, trace))
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.skeleton_ready.emit(('ERR', str(e)))

    def _on_skeleton_ready(self, result):
        # 成功分支 result[0] 是 music21 Score，与字符串比较可能触发
        # music21 的自定义 __eq__；先确认是字符串再比对
        if isinstance(result, tuple) and result and \
                isinstance(result[0], str) and result[0] == 'ERR':
            QMessageBox.warning(self, "骨架生成失败", str(result[1]))
            self.statusBar().showMessage("骨架生成失败")
            return
        if len(result) == 4:
            score, spec, report, trace = result
        else:                        # 兼容旧的三元组
            score, spec, report = result
            trace = None
        note = f"骨架（确定性）· 声部 {len(spec.get('parts') or [])}"
        self._apply_composed_score(score, spec, note=note, compose_trace=trace)
        self._sections = list(spec.get('sections') or [])
        self._update_daw_views()
        try:
            self._chat_append("助手",
                              f"{report}\n\n已载入《{spec.get('title')}》"
                              f"（{spec.get('bars')} 小节）。想变奏就在对话里说，"
                              f"或改种子「再来一版」。")
            self.right_tabs.setCurrentIndex(0)
        except Exception:
            pass
        self.statusBar().showMessage(
            f"骨架已生成：{spec.get('title')} ｜ 赋格：{'有' if spec.get('fugue') else '无'}")

    def generate_full(self, opts: dict):
        """LLM 全曲：骨架作基调，提示词在其上变奏（走对话管线）。"""
        opts = dict(opts or {})
        ov, brief = self._split_brief_commands(opts.get('brief') or '')
        parts = []
        name = ''
        gid = opts.get('genre_id') or ''
        if ov.get('style'):
            # 「变奏」框里的 /style 优先于下拉框选的基调
            try:
                import style_skeleton
                g = style_skeleton.resolve_genre(ov['style'])
                if g is not None:
                    name = g.get('name') or ''
            except Exception:
                name = ''
            if name:
                parts.append(f"/style {name}")
            else:
                parts.append(f"/style {ov['style']}")
        elif gid:
            try:
                import style_skeleton
                name = (style_skeleton.describe_style(gid) or {}).get('name') or ''
            except Exception:
                name = ''
            if name:
                parts.append(f"/style {name}")
        bars = ov.get('bars') or opts.get('bars')
        if bars:
            parts.append(f"/bars {int(bars)}")
        voices = ov.get('voices') or opts.get('voices')
        if voices:
            parts.append(f"/voices {int(voices)}")
        parts.append(f"/fugue {ov.get('fugue') or opts.get('fugue') or 'auto'}")
        for k in ('key', 'tempo', 'meter'):
            if ov.get(k):
                parts.append(f"/{k} {ov[k]}")
        if not brief:
            # 全是 /命令 的一句话只会设定偏好、不会触发作曲
            # （compose_from_text 没有自然语言时抛 CommandOnly），
            # 所以「生成全曲」必须补一句创作描述，否则点了没反应。
            brief = (f'创作一首{name}风格的完整乐曲' if name
                     else '按当前基调创作一首完整乐曲')
        parts.append(brief)
        text = ' '.join(parts)
        self.chat_input.setPlainText(text)
        self.right_tabs.setCurrentIndex(0)
        self.send_chat_message()

    def _update_lcd(self, t):
        """走带位置 LCD：小节:拍 + 分:秒。"""
        bar, beat = 1, 1
        try:
            bpm = max(float(self.score.tempo) or 120.0, 1.0)
            spb = 60.0 / bpm
            beats_pm = float(self.score.time_signature.beats_per_measure()) or 4.0
            bar = int(t // (beats_pm * spb)) + 1
            beat = int((t % (beats_pm * spb)) // spb) + 1
        except Exception:
            pass
        mm = int(t // 60)
        ss = t - mm * 60
        self.lcd_label.setText(f"{bar:03d}:{beat}  {mm:02d}:{ss:04.1f}")

    def rewind_to_start(self):
        self.stop()

    def focus_playback_measure(self):
        """视图跳到当前正在播放的小节；未播放/已停止时回到乐谱开头。"""
        idx = 0
        if self.playing and self.audio is not None:
            if not self._playback_events:
                self._playback_events = self._build_playback_timeline()
            t = self._play_offset + self._play_elapsed.elapsed() / 1000.0
            active = [(t0, mi) for t0, t1, si, mi, ni in self._playback_events if t0 <= t < t1]
            if active:
                idx = min(mi for _, mi in active)
            else:
                past = [(t0, mi) for t0, t1, si, mi, ni in self._playback_events if t0 <= t]
                if past:
                    idx = min(mi for _, mi in past)
        # 同一小节也强制滚动一次
        self._playback_scroll_measure = None
        self._scroll_playback_to_measure(idx)
        self.view.setFocus()

    # ---------- 混音台 ----------

    def _build_mixer_page(self):
        self.mixer_page = QWidget()
        v = QVBoxLayout(self.mixer_page)
        v.setContentsMargins(8, 6, 8, 6)
        self.mixer_scroll = QScrollArea()
        self.mixer_scroll.setWidgetResizable(True)
        self.mixer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.mixer_scroll.setFrameShape(QFrame.NoFrame)
        self.mixer_container = QWidget()
        self.mixer_layout = QHBoxLayout(self.mixer_container)
        self.mixer_layout.setContentsMargins(2, 2, 2, 2)
        self.mixer_layout.setSpacing(8)
        self.mixer_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.mixer_scroll.setWidget(self.mixer_container)
        v.addWidget(self.mixer_scroll)
        self.tabs.addTab(self.mixer_page, "🎛 混音台")

    def _rebuild_mixer(self):
        """按当前乐谱的 m21 声部（part）重建混音台通道条。"""
        src = getattr(self, 'musicxml_path', None)
        m21 = self.m21_score
        parts = []
        if m21 is not None:
            parts = list(m21.parts) or [m21]
        names = []
        for i, p in enumerate(parts):
            nm = None
            try:
                ins = p.getInstrument()
                if ins is not None:
                    nm = ins.partName or ins.instrumentName
            except Exception:
                nm = None
            if nm:
                nm = PART_LABELS.get(nm) or PART_LABELS.get(nm.split()[-1]) or nm
            names.append(nm or f"Track {i + 1}")
        key = (src, len(parts), tuple(names))
        if getattr(self, '_mixer_key', None) == key:
            return
        self._mixer_key = key
        self._staff_audio_cache = {}
        self._mixer_count = len(parts)
        n = self._mixer_count
        self._mixer_gains = [1.0] * n
        self._mixer_mutes = [False] * n
        self._mixer_solos = [False] * n

        # 清空旧通道条
        while self.mixer_layout.count():
            item = self.mixer_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if n == 0:
            hint = QLabel("（当前无可分轨声部：导入外部音频时仅主音量生效）")
            hint.setStyleSheet("color:#8892a0; padding:16px;")
            self.mixer_layout.addWidget(hint)
            return

        self._mixer_sliders = []
        self._mixer_val_labels = []
        self._mixer_mute_btns = []
        self._mixer_solo_btns = []
        for si in range(n):
            strip = QFrame()
            strip.setObjectName("mixerStrip")
            strip.setFixedWidth(88)
            g = QVBoxLayout(strip)
            g.setContentsMargins(6, 8, 6, 8)
            g.setSpacing(4)
            color = STAFF_COLORS[si % len(STAFF_COLORS)]
            chip = QFrame()
            chip.setFixedSize(56, 5)
            chip.setStyleSheet(f"background:{color}; border-radius:2px;")
            g.addWidget(chip, 0, Qt.AlignHCenter)
            name = QLabel(names[si])
            name.setAlignment(Qt.AlignCenter)
            name.setToolTip(names[si])
            g.addWidget(name)
            slider = QSlider(Qt.Vertical)
            slider.setRange(0, 100)
            slider.setValue(100)
            slider.setMinimumHeight(110)
            slider.setToolTip(f"{names[si]} 音量")
            slider.valueChanged.connect(lambda val, i=si: self._on_mixer_gain(i, val))
            g.addWidget(slider, 1, Qt.AlignHCenter)
            val = QLabel("100")
            val.setAlignment(Qt.AlignCenter)
            g.addWidget(val)
            row = QHBoxLayout()
            row.setSpacing(3)
            mb = QPushButton("M")
            mb.setObjectName("muteBtn")
            mb.setCheckable(True)
            mb.setFixedSize(32, 22)
            mb.setToolTip("静音")
            mb.toggled.connect(lambda on, i=si: self._on_mixer_mute(i, on))
            sb = QPushButton("S")
            sb.setObjectName("soloBtn")
            sb.setCheckable(True)
            sb.setFixedSize(32, 22)
            sb.setToolTip("独奏")
            sb.toggled.connect(lambda on, i=si: self._on_mixer_solo(i, on))
            row.addWidget(mb)
            row.addWidget(sb)
            g.addLayout(row)
            self.mixer_layout.addWidget(strip)
            self._mixer_sliders.append(slider)
            self._mixer_val_labels.append(val)
            self._mixer_mute_btns.append(mb)
            self._mixer_solo_btns.append(sb)

    def _on_mixer_gain(self, si, val):
        self._mixer_gains[si] = val / 100.0
        self._mixer_val_labels[si].setText(str(val))
        self._mixer_schedule()

    def _on_mixer_mute(self, si, on):
        self._mixer_mutes[si] = on
        self._mixer_schedule()

    def _on_mixer_solo(self, si, on):
        self._mixer_solos[si] = on
        self._mixer_schedule()

    def _mixer_schedule(self):
        self._mixer_timer.start()

    def _effective_gains(self):
        any_solo = any(self._mixer_solos)
        gains = []
        for i in range(self._mixer_count):
            g = self._mixer_gains[i]
            if any_solo:
                g = g if self._mixer_solos[i] else 0.0
            elif self._mixer_mutes[i]:
                g = 0.0
            gains.append(max(0.0, g))
        return gains

    def _mixer_start(self):
        if self.m21_score is None or not self._mixer_count:
            return
        if self.playing is True:
            # 仅「正在播放」需要先停；'paused' 是暂停态，不在此误停
            self.stop()
        threading.Thread(target=self._mixer_worker, daemon=True).start()

    def _render_single_part(self, idx):
        """渲染单个声部（part）为音频；失败返回 None。"""
        import copy as _copy
        import tempfile
        # music21 非线程安全：deepcopy 共享乐谱树必须与其他遍历互斥
        with _RENDER_LOCK:
            parts = list(self.m21_score.parts) or [self.m21_score]
            p = _copy.deepcopy(parts[idx])
            # 全曲速度图（含曲中变速）取自第一声部，保证分轨与整曲一致
            tempo_map = []
            if parts:
                try:
                    for mm in parts[0].flatten().getElementsByClass(tempo.MetronomeMark):
                        if getattr(mm, 'number', None):
                            tempo_map.append((float(mm.offset), int(mm.number)))
                except Exception:
                    tempo_map = []
        try:
            # 统一速度：移除声部内自带的 MetronomeMark，避免与全曲速度不一致
            for mm in list(p.recurse().getElementsByClass(tempo.MetronomeMark)):
                mm.activeSite.remove(mm)
        except Exception:
            pass
        s = stream.Score()
        s.insert(0, p)
        try:
            if tempo_map:
                for off, bpm in sorted(tempo_map):
                    s.insert(off, tempo.MetronomeMark(number=max(bpm, 1)))
            else:
                s.insert(0, tempo.MetronomeMark(number=max(int(self.score.tempo) or 120, 1)))
        except Exception:
            pass
        tmp = os.path.join(tempfile.gettempdir(), f"scorestudio_mix_{idx}.mid")
        groove = None
        if self._compose_session is not None:
            groove = (self._compose_session.prefs or {}).get('groove')
        export_midi(s, tmp, groove=groove)
        return render_midi_to_audio(tmp, SOUNDFONT, SAMPLE_RATE)

    def _mixer_worker(self):
        """后台线程：按需渲染分轨并按增益混合。"""
        try:
            n = int(self._mixer_count or 0)
            for i in range(n):
                if self._staff_audio_cache.get(i) is None:
                    a = self._render_single_part(i)
                    if a is None:
                        continue
                    self._staff_audio_cache[i] = a
            gains = self._effective_gains()
            n = min(n, len(gains))
            total = 0
            for i in range(n):
                a = self._staff_audio_cache.get(i)
                if a is not None:
                    total = max(total, len(a))
            if total == 0:
                self.mixer_ready.emit(RuntimeError("分轨渲染失败"))
                return
            mixed = np.zeros(total, dtype=np.float32)
            for i in range(n):
                g = gains[i]
                a = self._staff_audio_cache.get(i)
                if g > 0 and a is not None:
                    mixed[:len(a)] += g * a
            peak = float(np.max(np.abs(mixed))) if len(mixed) else 0.0
            if peak > 1.0:
                mixed = mixed / peak
            self.mixer_ready.emit((mixed, self.sample_rate or SAMPLE_RATE))
        except Exception as e:
            self.mixer_ready.emit(e)

    def _on_mixer_ready(self, result):
        if isinstance(result, Exception):
            self.statusBar().showMessage(f"混音失败：{result}")
            return
        audio, sr = result
        self._set_audio(audio, sr, "混音台", path=None, cache_as_score=False)
        self.restore_btn.setEnabled(self._score_audio is not None)

    def _init_chat_panel(self):
        from llm_compose import ComposeSession
        self._compose_session = ComposeSession()

        self.chat_panel = QWidget()
        self.chat_panel.setObjectName("chatPanel")
        chat_layout = QVBoxLayout(self.chat_panel)
        chat_layout.setContentsMargins(8, 8, 8, 8)
        chat_layout.setSpacing(6)

        tip = QLabel(
            "点选小节/音符后对话只改选区；无选区则整曲。"
            "可用下方「风格」下拉选择，或输入 /style 弹出列表，选完后继续写描述。"
            "创曲需等 LLM（约1–3分钟）。Ctrl多选 · Esc清除 · /help"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8a8f98; background:transparent;")
        chat_layout.addWidget(tip)

        sess_row = QHBoxLayout()
        sess_row.addWidget(QLabel("对话"))
        self.chat_combo = QComboBox()
        self.chat_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.chat_combo.currentIndexChanged.connect(self._on_chat_combo_changed)
        sess_row.addWidget(self.chat_combo, stretch=1)
        self.chat_new_btn = QPushButton("新建")
        self.chat_new_btn.clicked.connect(self._new_chat_session)
        sess_row.addWidget(self.chat_new_btn)
        self.chat_del_btn = QPushButton("删除")
        self.chat_del_btn.clicked.connect(self._delete_chat_session)
        sess_row.addWidget(self.chat_del_btn)
        chat_layout.addLayout(sess_row)

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("风格"))
        self.style_combo = QComboBox()
        self.style_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # 长风格名不撑大面板：按最小内容长度计算宽度，超长文本在弹出列表里看全
        self.style_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.style_combo.setMinimumContentsLength(8)
        self.style_combo.setMinimumWidth(100)
        self.style_combo.setMaxVisibleItems(16)
        self.style_combo.activated.connect(self._on_style_combo_picked)
        style_row.addWidget(self.style_combo, stretch=1)
        chat_layout.addLayout(style_row)
        self._refresh_style_combo()

        self.selection_label = QLabel("选区：无")
        self.selection_label.setWordWrap(True)
        self.selection_label.setStyleSheet(
            "background:#202227; border:1px solid #34373f; border-radius:4px; padding:4px 6px; color:#9fd0ff;"
        )
        chat_layout.addWidget(self.selection_label)

        self.skill_trace_label = QLabel("本次 Skill：—")
        self.skill_trace_label.setWordWrap(True)
        self.skill_trace_label.setStyleSheet(
            "background:#262019; border:1px solid #4a3d28; border-radius:4px; "
            "padding:4px 6px; color:#d8b56a;"
        )
        chat_layout.addWidget(self.skill_trace_label)

        self.chat_log = QPlainTextEdit()
        self.chat_log.setReadOnly(True)
        self.chat_log.setFont(QFont(_UI_FONT_FAMILY, 10))
        chat_layout.addWidget(self.chat_log, stretch=1)

        row = QHBoxLayout()
        self.chat_input = QPlainTextEdit()
        self.chat_input.setPlaceholderText(
            "做一首圆舞曲…  或选风格后写描述  ·  /style  ·  /help\n"
            "Enter 发送 · Shift+Enter 换行 · ↑/↓ 翻命令履历"
        )
        self.chat_input.setFixedHeight(64)
        self.chat_input.setFont(QFont(_UI_FONT_FAMILY, 10))
        self.chat_input.textChanged.connect(self._on_chat_input_edited)
        row.addWidget(self.chat_input, stretch=1)
        self.chat_send_btn = QPushButton("发送")
        self.chat_send_btn.setObjectName("chatSendBtn")
        self.chat_send_btn.setFixedHeight(34)
        self.chat_send_btn.clicked.connect(self.send_chat_message)
        row.addWidget(self.chat_send_btn)
        self.chat_demo_btn = QPushButton("/demo")
        self.chat_demo_btn.clicked.connect(lambda: self._chat_quick('/demo'))
        row.addWidget(self.chat_demo_btn)
        chat_layout.addLayout(row)

        # popup list for /style autocomplete
        self._style_popup = QListWidget(self.chat_panel)
        self._style_popup.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self._style_popup.setFocusPolicy(Qt.NoFocus)
        self._style_popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._style_popup.setSelectionMode(QAbstractItemView.SingleSelection)
        self._style_popup.itemClicked.connect(self._on_style_popup_clicked)
        self._style_popup.hide()
        self._style_popup_active = False
        self.chat_input.installEventFilter(self)

        # 命令履历（会话级）：_cmd_hist_idx == len(hist) 表示正在输入新内容
        self._cmd_history: List[str] = []
        self._cmd_hist_idx: int = 0
        self._cmd_hist_draft: str = ''

        self._load_or_create_chats()

    def _chat_paths(self, chat_id: str) -> dict:
        os.makedirs(CHAT_DIR, exist_ok=True)
        return {
            'meta': os.path.join(CHAT_DIR, f'{chat_id}.json'),
            'xml': os.path.join(CHAT_DIR, f'{chat_id}.xml'),
            'mid': os.path.join(CHAT_DIR, f'{chat_id}.mid'),
            'wav': os.path.join(CHAT_DIR, f'{chat_id}.wav'),
            'report': os.path.join(CHAT_DIR, f'{chat_id}_report.md'),
            'report_dev': os.path.join(CHAT_DIR, f'{chat_id}_report_dev.md'),
        }

    def _load_chat_index(self) -> dict:
        os.makedirs(CHAT_DIR, exist_ok=True)
        if os.path.exists(CHAT_INDEX_PATH):
            try:
                with open(CHAT_INDEX_PATH, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get('sessions'), list):
                    return data
            except Exception:
                # 损坏时备份，避免后续 persist 用空索引覆写抹掉历史
                try:
                    bak = CHAT_INDEX_PATH + '.corrupt'
                    os.replace(CHAT_INDEX_PATH, bak)
                    print(f'[chat] index.json 损坏，已备份到 {bak}', flush=True)
                except OSError:
                    pass
        return {'active_id': None, 'sessions': []}

    def _save_chat_index(self, index: dict):
        os.makedirs(CHAT_DIR, exist_ok=True)
        _atomic_json_dump(CHAT_INDEX_PATH, index)

    def _load_or_create_chats(self):
        index = self._load_chat_index()
        if not index.get('sessions'):
            self._create_chat_session(activate=True, title='新对话')
            return
        self._refresh_chat_combo(index)
        active = index.get('active_id') or index['sessions'][0]['id']
        self._switch_chat_session(active, save_current=False)

    def _refresh_chat_combo(self, index=None):
        index = index or self._load_chat_index()
        self._suppress_chat_switch = True
        try:
            self.chat_combo.clear()
            for s in index.get('sessions') or []:
                self.chat_combo.addItem(s.get('title') or s['id'], s['id'])
            # select active
            active = index.get('active_id')
            if active:
                for i in range(self.chat_combo.count()):
                    if self.chat_combo.itemData(i) == active:
                        self.chat_combo.setCurrentIndex(i)
                        break
        finally:
            # 异常时也必须复位，否则对话切换被永久抑制
            self._suppress_chat_switch = False

    def _clear_displayed_score(self, title: str = None, layout: str = 'single'):
        """Blank the staff view with a writable measure grid (assist-ready)."""
        is_en = i18n.language() == 'en'
        if title is None:
            title = 'New Score' if is_en else '新乐谱'
        self.view.clear_selection()
        self.m21_score = None
        # 从会话偏好取调号/拍号/速度；否则 C major 4/4 ♩=120、16 小节
        prefs = getattr(self._compose_session, 'prefs', None) or {}
        sharps = int(prefs.get('key_sharps') or 0)
        tempo = int(prefs.get('tempo') or 120)
        meter = str(prefs.get('meter') or '4/4')
        bars = int(prefs.get('bars') or 16)
        self.score = blank_score(
            title=title, bars=bars, tempo=tempo, sharps=sharps, meter=meter,
            layout=layout)
        self.edit_controller.score = self.score
        self.renderer.set_violations([])
        self.musicxml_path = (
            self._chat_paths(self._chat_id)['xml'] if self._chat_id else LLM_MUSICXML_PATH
        )
        self.renderer.render_score(self.score, SCORE_WIDTH)
        self.audio = None
        self._pending_play = False
        self.play_btn.setEnabled(False)
        self.export_wav_btn.setEnabled(False)
        self.audio_path = None
        self._score_audio = None
        if is_en:
            layout_label = 'Piano Grand Staff' if str(layout).lower() in (
                'piano', 'grand', 'satb', 'four-part', 'four_part') else 'Single'
            self.info_label.setText(
                f"♪ {title} ｜ {bars}-measure empty score ({layout_label}) ｜ Click to enter notes in assist mode")
            self.setWindowTitle(f"Score Studio - {title}")
        else:
            layout_label = '钢琴大谱表' if str(layout).lower() in (
                'piano', 'grand', 'satb', 'four-part', 'four_part') else '单行'
            self.info_label.setText(
                f"♪ {title} ｜ {bars} 小节空谱（{layout_label}）｜ 辅助作曲可直接点写")
            self.setWindowTitle(f"乐谱工作室 - {title}")
        self._set_skill_trace([])
        # 同步辅助引擎风格
        self._sync_assist_style_from_prefs()

    def _new_piano_satb_score(self):
        """新建钢琴大谱表空谱，并提示用 /satb 写入四部和声。"""
        if not self._chat_id:
            self._create_chat_session(activate=True, title='四部和声')
        self._clear_displayed_score(title='四部和声', layout='piano')
        self._enter_assist_compose()
        tip = (
            '已新建**钢琴大谱表**（RH 高音 / LH 低音）。\n\n'
            '写入四部和声示例：\n'
            '- `/satb I V vi IV`\n'
            '- `/satb Bb I IV V I`\n'
            '- `/chords I V vi IV write`\n\n'
            'RH 叠写 S+A，LH 叠写 T+B；可在编辑模式继续改写。'
        )
        now = datetime.now().isoformat(timespec='seconds')
        self._chat_messages.append({'role': 'assistant', 'text': tip, 'ts': now})
        self._render_chat_log()
        self._persist_current_chat()

    def _add_measures_dialog(self):
        """弹出对话框，在谱末追加若干空小节。"""
        from PyQt5.QtWidgets import QInputDialog
        if self.score is None or not getattr(self.score, 'staves', None):
            QMessageBox.information(self, '增加小节', '当前没有乐谱。请先新建或打开乐谱。')
            return
        cur = max((len(s.measures) for s in self.score.staves), default=0)
        n, ok = QInputDialog.getInt(
            self, '增加小节',
            f'当前 {cur} 小节。追加多少空小节？',
            4, 1, 32, 1)
        if not ok:
            return
        self._add_measures(n)

    def _add_measures(self, count: int) -> int:
        """追加空小节并刷新谱面 / 写回。返回新总小节数。"""
        if self.score is None:
            return 0
        if self.edit_controller.score is not self.score:
            self.edit_controller.score = self.score
        total = self.edit_controller.add_measures(int(count))
        if total <= 0:
            return 0
        # 同步会话偏好与 spec 长度，便于后续生成/导出对齐
        if self._compose_session is not None:
            self._compose_session.prefs['bars'] = total
            if self._compose_session.score_spec:
                self._compose_session.score_spec['bars'] = total
        self.renderer.render_score(self.score, SCORE_WIDTH)
        self.info_label.setText(
            f"♪ {self.score.title} ｜ 已增至 {total} 小节")
        self.statusBar().showMessage(f'已增加小节，共 {total} 小节')
        # 走编辑写回（MusicXML + 可播放）
        if hasattr(self, '_edit_sync_timer'):
            self._edit_sync_timer.start()
        elif hasattr(self, '_on_score_edited'):
            self._on_score_edited()
        return total

    def _add_part_dialog(self):
        """弹出对话框，追加空声部。"""
        from PyQt5.QtWidgets import QInputDialog
        if self.score is None or not getattr(self.score, 'staves', None):
            QMessageBox.information(self, '添加声部', '当前没有乐谱。请先新建或打开乐谱。')
            return
        choices = [
            '钢琴左手 (升大谱表)', '低音 Bass', '大提琴 Cello',
            '小提琴 Violin', '长笛 Flute', '中提琴 Viola', '高音声部',
        ]
        item, ok = QInputDialog.getItem(
            self, '添加声部',
            f'当前 {len(self.score.staves)} 个声部。选择要添加的声部：',
            choices, 0, False)
        if not ok:
            return
        token_map = {
            '钢琴左手 (升大谱表)': 'piano',
            '低音 Bass': 'bass',
            '大提琴 Cello': 'cello',
            '小提琴 Violin': 'violin',
            '长笛 Flute': 'flute',
            '中提琴 Viola': 'viola',
            '高音声部': 'treble',
        }
        self._add_part(token_map.get(item, 'bass'))

    def _add_part(self, token: str = 'bass') -> bool:
        """追加声部并刷新。"""
        if self.score is None:
            return False
        if self.edit_controller.score is not self.score:
            self.edit_controller.score = self.score
        idx, msg = self.edit_controller.add_part(token)
        if idx < 0:
            QMessageBox.warning(self, '添加声部', msg or '失败')
            return False
        n = len(self.score.staves)
        self.renderer.render_score(self.score, SCORE_WIDTH)
        self.info_label.setText(f"♪ {self.score.title} ｜ {n} 声部")
        self.statusBar().showMessage(msg)
        if hasattr(self, '_edit_sync_timer'):
            self._edit_sync_timer.start()
        elif hasattr(self, '_on_score_edited'):
            self._on_score_edited()
        return True

    def _accomp_dialog(self):
        """弹出对话框，设置配乐型并写入。"""
        from PyQt5.QtWidgets import QInputDialog
        if self.score is None or not getattr(self.score, 'staves', None):
            QMessageBox.information(self, '设置配乐', '当前没有乐谱。请先新建或打开乐谱。')
            return
        choices = [
            '低音根音 (bass)', '柱式和弦 (block)',
            '分解琶音 (broken)', '阿尔贝蒂低音 (alberti)',
        ]
        item, ok = QInputDialog.getItem(
            self, '设置配乐',
            '根据旋律声部写入伴奏。选择配乐型：',
            choices, 0, False)
        if not ok:
            return
        pat_map = {
            '低音根音 (bass)': 'bass',
            '柱式和弦 (block)': 'block',
            '分解琶音 (broken)': 'broken',
            '阿尔贝蒂低音 (alberti)': 'alberti',
        }
        self._fill_accompaniment(pat_map.get(item, 'bass'))

    def _fill_accompaniment(self, pattern: str = 'bass') -> bool:
        """写入配乐并刷新。"""
        if self.score is None:
            return False
        if self.edit_controller.score is not self.score:
            self.edit_controller.score = self.score
        mode = 'major'
        try:
            mode = str((getattr(self._compose_session, 'prefs', None) or {}).get('mode')
                       or getattr(self.score, 'mode', None) or 'major')
        except Exception:
            mode = 'major'
        ok, msg = self.edit_controller.fill_accompaniment(pattern=pattern, mode=mode)
        if not ok:
            QMessageBox.warning(self, '设置配乐', msg or '失败')
            return False
        n = len(self.score.staves)
        self.renderer.render_score(self.score, SCORE_WIDTH)
        self.info_label.setText(f"♪ {self.score.title} ｜ {n} 声部 · 已配乐")
        self.statusBar().showMessage(msg)
        if hasattr(self, '_edit_sync_timer'):
            self._edit_sync_timer.start()
        elif hasattr(self, '_on_score_edited'):
            self._on_score_edited()
        # 聊天提示
        try:
            now = datetime.now().isoformat(timespec='seconds')
            self._chat_messages.append({'role': 'assistant', 'text': msg, 'ts': now})
            self._render_chat_log()
        except Exception:
            pass
        return True

    # ---- MIDI 键盘输入（macOS / Windows · mido+rtmidi） ----

    def _init_midi_keyboard(self):
        """初始化 MIDI 服务与轮询定时器（默认关闭）。"""
        from PyQt5.QtCore import QTimer
        from music_score.midi_input import MidiInputService, StepInputAccumulator
        self._midi_service = MidiInputService()
        self._midi_accum = StepInputAccumulator(chord_window_ms=80.0)
        self._midi_port_pref = None
        self._midi_timer = QTimer(self)
        self._midi_timer.setInterval(30)
        self._midi_timer.timeout.connect(self._poll_midi_keyboard)
        self._midi_wanted = False
        self._midi_hands = True
        self._midi_pad_rests = True
        self._midi_split = 60
        self._midi_live_sig = None

    def _on_midi_hands_toggled(self, on: bool):
        self._midi_hands = bool(on)
        self.statusBar().showMessage(
            f'MIDI：左右手分谱{"开" if on else "关（写入当前谱表）"}')

    def _on_midi_pad_toggled(self, on: bool):
        self._midi_pad_rests = bool(on)
        self.statusBar().showMessage(
            '另一只手补休止：' + ('开（两手每拍对齐）' if on else '关'))

    def _midi_split_dialog(self):
        from PyQt5.QtWidgets import QInputDialog
        from music_score.piano_step import spell_midi
        from music_score.editor_core import pitch_label
        choices = list(range(48, 73))
        labels = [f'{pitch_label(spell_midi(m))}（MIDI {m}）' for m in choices]
        cur = choices.index(self._midi_split) if self._midi_split in choices else 12
        item, ok = QInputDialog.getItem(
            self, '分手点', '此音及以上进右手，以下进左手：', labels, cur, False)
        if ok and item:
            self._midi_split = choices[labels.index(item)]
            self.statusBar().showMessage(f'分手点：{item}')

    def _on_midi_palette_toggled(self, on: bool):
        self._set_midi_enabled(bool(on), interactive=True)

    def _on_midi_menu_toggled(self, on: bool):
        self._set_midi_enabled(bool(on), interactive=True)

    def _on_midi_chord_mode(self, on: bool):
        if getattr(self, '_midi_accum', None) is not None:
            self._midi_accum.chord_mode = bool(on)
            self._midi_accum.reset()
        tip = '和弦模式：按下多个键，全部抬起后写入和弦' if on else '步进模式：短时多键合并为和弦，单音约 80ms 后写入'
        self.statusBar().showMessage(tip)

    def _midi_shift_octave(self, semitones: int):
        svc = getattr(self, '_midi_service', None)
        if svc is None:
            return
        cur = svc.octave_offset + int(semitones)
        cur = max(-36, min(36, cur))
        svc.set_octave_offset(cur)
        octs = cur // 12
        self.statusBar().showMessage(f'MIDI 八度偏移：{octs:+d}（{cur:+d} 半音）')

    def _set_midi_enabled(self, on: bool, interactive: bool = False) -> bool:
        """打开/关闭 MIDI 输入。interactive=True 时弹出设备选择。"""
        self._midi_wanted = bool(on)
        # 同步 UI
        for w in (getattr(self.edit_palette, 'midi_check', None),
                  getattr(self.score_palette, 'midi_check', None)):
            if w is None:
                continue
            try:
                w.blockSignals(True)
                w.setChecked(bool(on))
                w.blockSignals(False)
            except Exception:
                pass
        try:
            self._midi_enable_action.blockSignals(True)
            self._midi_enable_action.setChecked(bool(on))
            self._midi_enable_action.blockSignals(False)
        except Exception:
            pass
        svc = getattr(self, '_midi_service', None)
        if svc is None:
            return False
        if not on:
            svc.close()
            self._midi_timer.stop()
            if getattr(self, '_midi_accum', None):
                self._midi_accum.reset()
            self.statusBar().showMessage('MIDI 键盘已关闭')
            return True
        # 开启：需要编辑模式更直观
        if not self.edit_mode:
            self.set_edit_mode(True)
        try:
            name = self._midi_port_pref
            if interactive or not name:
                name = self._midi_pick_port_name(allow_cancel=True)
                if name is None and interactive:
                    # 用户取消
                    self._set_midi_enabled(False, interactive=False)
                    return False
            opened = svc.open(name)
            self._midi_port_pref = opened
            self._midi_timer.start()
            self.statusBar().showMessage(f'MIDI 键盘已接入：{opened}')
            return True
        except Exception as e:
            QMessageBox.warning(self, 'MIDI 键盘', str(e))
            self._set_midi_enabled(False, interactive=False)
            return False

    def _midi_pick_port_name(self, allow_cancel: bool = True) -> Optional[str]:
        from PyQt5.QtWidgets import QInputDialog
        from music_score.midi_input import list_midi_input_ports, midi_backend_available
        ok, msg = midi_backend_available()
        if not ok:
            QMessageBox.warning(self, 'MIDI 键盘', msg)
            return None
        ports = list_midi_input_ports()
        if not ports:
            QMessageBox.information(
                self, 'MIDI 键盘',
                '未检测到 MIDI 输入设备。\n\n'
                '请连接 MIDI 键盘后重试。\n'
                'macOS：系统设置 → 隐私与安全性（如有提示请允许）。\n'
                'Windows：确认驱动正常，设备管理器中可见。')
            return None
        current = 0
        if self._midi_port_pref and self._midi_port_pref in ports:
            current = ports.index(self._midi_port_pref)
        item, accepted = QInputDialog.getItem(
            self, 'MIDI 输入设备', '选择 MIDI 键盘：', ports, current, False)
        if not accepted:
            return None if allow_cancel else (ports[0] if ports else None)
        return item

    def _midi_select_port_dialog(self):
        name = self._midi_pick_port_name(allow_cancel=True)
        if not name:
            return
        self._midi_port_pref = name
        if self._midi_wanted:
            self._set_midi_enabled(True, interactive=False)
        else:
            self.statusBar().showMessage(f'已记住 MIDI 设备：{name}（勾选「MIDI键盘」启用）')

    def _midi_status_dialog(self):
        from music_score.midi_input import list_midi_input_ports, midi_backend_available
        ok, msg = midi_backend_available()
        ports = list_midi_input_ports()
        svc = getattr(self, '_midi_service', None)
        lines = [
            f'后端：{"可用" if ok else "不可用"} — {msg}',
            f'设备列表：{", ".join(ports) if ports else "（无）"}',
            f'当前：{getattr(svc, "port_name", None) or "未打开"}',
            f'启用：{"是" if getattr(svc, "enabled", False) else "否"}',
            f'八度偏移：{getattr(svc, "octave_offset", 0):+d} 半音',
            f'和弦模式：{"开" if getattr(self._midi_accum, "chord_mode", False) else "关"}',
            f'左右手分谱：{"开" if getattr(self, "_midi_hands", True) else "关"}'
            f'（分手点 MIDI {getattr(self, "_midi_split", 60)}）',
        ]
        QMessageBox.information(self, 'MIDI 状态', '\n'.join(lines))

    def _poll_midi_keyboard(self):
        svc = getattr(self, '_midi_service', None)
        acc = getattr(self, '_midi_accum', None)
        if svc is None or acc is None or not svc.enabled:
            return
        for ev in svc.poll():
            bundle = acc.feed(ev)
            if bundle:
                self._midi_commit_notes(bundle)
        bundle = acc.tick()
        if bundle:
            self._midi_commit_notes(bundle)
        self._midi_update_live_keys(acc.preview())

    def _midi_grand_staff(self, create: bool):
        """返回 (rh, lh)；create=True 时单谱表自动补左手低音谱表。"""
        from music_score.piano_step import ensure_grand_staff, find_grand_staff
        if self.score is None:
            return None
        found = find_grand_staff(self.score)
        if found is not None or not create:
            return found
        if self.edit_controller.score is not self.score:
            self.edit_controller.score = self.score
        self.edit_controller._snapshot()
        rh, lh, msg = ensure_grand_staff(self.score)
        if msg:
            self.statusBar().showMessage(f'MIDI：{msg}')
        return rh, lh

    def _midi_update_live_keys(self, held):
        """按住未提交的键：在五线谱两手位置实时画出（橙色）。"""
        renderer = getattr(self, 'renderer', None)
        if renderer is None:
            return
        sig = tuple(held or ())
        if sig == self._midi_live_sig:
            return
        self._midi_live_sig = sig
        if not sig or self.score is None:
            renderer.clear_live_keys()
            return
        from music_score.piano_step import preview_groups
        if self._midi_hands:
            pair = self._midi_grand_staff(create=False)
        else:
            pair = None
        si, mi, _idx = self._midi_insert_target()
        if pair is None:
            si = min(si, len(self.score.staves) - 1)
            pair = (si, si)
            split = -1
        else:
            split = self._midi_split
        prev = preview_groups(self.score, pair[0], pair[1], mi, sig, split=split)
        if pair[0] == pair[1]:
            pitches = [p for g in prev['groups'] for p in g['pitches']]
            prev['groups'] = [{'staff_idx': pair[0],
                               'clef': self.score.staves[pair[0]].clef,
                               'pitches': pitches}] if pitches else []
        renderer.draw_live_keys(prev['measure_idx'], prev['onset_ratio'],
                                prev['groups'])

    def _midi_insert_target(self):
        """返回 (staff_idx, measure_idx, insert_index)。"""
        ctx = getattr(self, '_assist_ctx', None)
        if ctx and ctx[0] is not None:
            return int(ctx[0]), int(ctx[1]), ctx[2]
        bar = getattr(self, 'suggestion_bar', None)
        if bar is not None and getattr(bar, 'context', None):
            c = bar.context
            if c and c[0] is not None:
                return int(c[0]), int(c[1]), c[2]
        # 选中音符之后
        syms = list(getattr(self.view, 'selected_symbols', None) or [])
        if syms:
            si, mi, ni = syms[-1]
            return int(si), int(mi), int(ni) + 1
        measures = list(getattr(self.view, 'selected_measures', None) or [])
        if measures:
            si, mi = measures[-1]
            return int(si), int(mi), None
        # 默认：第一声部、第一个有空位的小节
        if self.score is None or not self.score.staves:
            return 0, 0, 0
        si = 0
        staff = self.score.staves[0]
        if not staff.measures:
            return si, 0, 0
        for mi, m in enumerate(staff.measures):
            used = sum(n.duration_value() for n in m.notes)
            cap = self.score.time_signature.beats_per_measure() / 4.0
            if used + 1e-9 < cap:
                return si, mi, len(m.notes)
        return si, max(0, len(staff.measures) - 1), len(staff.measures[-1].notes)

    def _midi_commit_notes(self, bundle):
        """bundle: [(midi, vel), ...] 高→低。"""
        if not bundle or self.score is None:
            return
        if not self.edit_mode:
            self.set_edit_mode(True)
        if self.edit_controller.score is not self.score:
            self.edit_controller.score = self.score
        self._midi_live_sig = None
        if getattr(self, '_midi_hands', True):
            self._midi_commit_piano(bundle)
            return
        from music_score.models import Pitch
        si, mi, idx = self._midi_insert_target()
        midis = [int(m) for m, _v in bundle]
        top = midis[0]
        pitch = Pitch.from_midi(top)
        # 和弦：先插入最高音，再写入 chord_tones
        dur = self.edit_controller.duration
        ok = self.edit_controller.try_insert_note(si, mi, idx, pitch, dur)
        if not ok:
            self.statusBar().showMessage('MIDI：无法写入（小节已满或被规则禁止）')
            return
        if len(midis) > 1:
            # 取刚插入的音符加叠置
            try:
                measure = self.score.staves[si].measures[mi]
                # insert 后索引
                at = len(measure.notes) - 1 if idx is None else min(idx, len(measure.notes) - 1)
                note = measure.notes[at]
                note.chord_tones = [Pitch.from_midi(m) for m in midis[1:]]
                # 触发刷新
                if hasattr(self.edit_controller, '_after_change'):
                    self.edit_controller._after_change(
                        f'MIDI 和弦 {len(midis)} 音')
            except Exception:
                pass
        next_idx = (idx or 0) + 1
        if idx is None:
            next_idx = None
        else:
            next_idx = idx + 1
        # 推进光标到下一插入点
        try:
            measure = self.score.staves[si].measures[mi]
            used = sum(n.duration_value() for n in measure.notes)
            cap = self.score.time_signature.beats_per_measure() / 4.0
            if used + 1e-9 >= cap and mi + 1 < len(self.score.staves[si].measures):
                self._assist_ctx = (si, mi + 1, 0)
            else:
                self._assist_ctx = (si, mi, len(measure.notes))
            self.suggestion_bar.set_context(*self._assist_ctx)
        except Exception:
            self._assist_ctx = (si, mi, next_idx)
        labels = []
        for m in midis:
            try:
                labels.append(Pitch.from_midi(m).name.name + str(Pitch.from_midi(m).octave))
            except Exception:
                labels.append(str(m))
        self.statusBar().showMessage(
            f'MIDI → {"+".join(labels)} · 第 {mi + 1} 小节')
        self.renderer.render_score(self.score, SCORE_WIDTH)
        if hasattr(self, '_edit_sync_timer'):
            self._edit_sync_timer.start()

    def _midi_commit_piano(self, bundle):
        """左右手分谱写入：高于分手点进右手，其余进左手；和弦/两手同拍对齐。"""
        pair = self._midi_grand_staff(create=True)
        if pair is None:
            self.statusBar().showMessage('MIDI：找不到可写入的谱表')
            return
        rh, lh = pair
        _si, mi, _idx = self._midi_insert_target()
        info = self.edit_controller.insert_piano_step(
            rh, lh, mi, [int(m) for m, _v in bundle],
            split=self._midi_split, pad_rests=self._midi_pad_rests)
        if not info:
            return
        nxt = info['next_measure_idx']
        n_bars = len(self.score.staves[rh].measures)
        if nxt >= n_bars:
            nxt = info['measure_idx']
        self._assist_ctx = (rh, nxt, len(self.score.staves[rh].measures[nxt].notes))
        try:
            self.suggestion_bar.set_context(*self._assist_ctx)
        except Exception:
            pass
        self.renderer.render_score(self.score, SCORE_WIDTH)
        if hasattr(self, '_edit_sync_timer'):
            self._edit_sync_timer.start()

    def _create_chat_session(self, activate=True, title=None):
        from llm_compose import ComposeSession
        is_en = i18n.language() == 'en'
        if title is None:
            title = 'New chat' if is_en else '新对话'
        chat_id = uuid.uuid4().hex[:10]
        now = datetime.now().isoformat(timespec='seconds')
        greeting = (
            'Hello! Describe what you want, or select measures/notes and ask for changes. Type /help.'
            if is_en else
            '你好！可直接描述想要的曲子；或点选小节/音符后再说怎么改。输入 /help。'
        )
        data = {
            'id': chat_id,
            'title': title,
            'created_at': now,
            'updated_at': now,
            'messages': [
                {'role': 'assistant', 'text': greeting, 'ts': now},
            ],
            'compose': None,
        }
        paths = self._chat_paths(chat_id)
        _atomic_json_dump(paths['meta'], data)
        index = self._load_chat_index()
        index.setdefault('sessions', []).insert(0, {
            'id': chat_id, 'title': title, 'updated_at': now,
        })
        if activate:
            if self._chat_id:
                self._persist_current_chat()
            index['active_id'] = chat_id
        self._save_chat_index(index)
        self._refresh_chat_combo(index)
        if activate:
            self._compose_session = ComposeSession()
            self._chat_id = chat_id
            self._chat_messages = list(data['messages'])
            self._render_chat_log()
            self._clear_displayed_score()
            # 新建 → 自动进入编辑 + 辅助作曲
            self._enter_assist_compose()
        return chat_id

    def _score_title_for_chat(self) -> Optional[str]:
        """Prefer the loaded / composed score name as the chat display title."""
        if self.score is not None:
            t = (getattr(self.score, 'title', None) or '').strip()
            if t and t not in ('新乐谱', 'Untitled'):
                return t
        spec = getattr(self._compose_session, 'score_spec', None) or {}
        t = str(spec.get('title') or '').strip()
        return t or None

    def _resolve_chat_title(self) -> str:
        score_title = self._score_title_for_chat()
        if score_title:
            return score_title
        for m in self._chat_messages:
            if m.get('role') == 'user' and (m.get('text') or '').strip():
                return (m['text'].strip().replace('\n', ' ')[:40])
        return '新对话'

    def _sync_chat_title_ui(self, title: str):
        """Update combo + index (+ chat meta title) to match the score name."""
        if not self._chat_id or not title:
            return
        self._suppress_chat_switch = True
        try:
            for i in range(self.chat_combo.count()):
                if self.chat_combo.itemData(i) == self._chat_id:
                    self.chat_combo.setItemText(i, title)
                    break
        finally:
            self._suppress_chat_switch = False
        index = self._load_chat_index()
        for s in index.get('sessions') or []:
            if s.get('id') == self._chat_id:
                s['title'] = title
                break
        index['active_id'] = self._chat_id
        self._save_chat_index(index)
        # keep session json title in sync without rewriting messages
        paths = self._chat_paths(self._chat_id)
        if os.path.exists(paths['meta']):
            try:
                with open(paths['meta'], encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('title') != title:
                    data['title'] = title
                    _atomic_json_dump(paths['meta'], data)
            except Exception:
                pass

    def _persist_current_chat(self):
        if not self._chat_id:
            return
        paths = self._chat_paths(self._chat_id)
        now = datetime.now().isoformat(timespec='seconds')
        title = self._resolve_chat_title()
        compose_blob = None
        if self._compose_session is not None:
            compose_blob = {
                'composition_id': self._compose_session.composition_id,
                'revision': self._compose_session.revision,
                'prefs': self._compose_session.prefs,
                'plan': self._compose_session.plan,
                'score_spec': self._compose_session.score_spec,
                'last_skill_ids': getattr(self._compose_session, 'last_skill_ids', []),
                'last_advice': getattr(self._compose_session, 'last_advice', None),
                # compose_trace 持久化：重启/切会话后重新生成程序员版报告仍可追溯
                'compose_trace': getattr(self._compose_session, 'compose_trace', None),
            }
        data = {
            'id': self._chat_id,
            'title': title,
            'updated_at': now,
            'messages': self._chat_messages,
            'compose': compose_blob,
        }
        # preserve created_at
        if os.path.exists(paths['meta']):
            try:
                with open(paths['meta'], encoding='utf-8') as f:
                    old = json.load(f)
                data['created_at'] = old.get('created_at', now)
            except Exception:
                data['created_at'] = now
        else:
            data['created_at'] = now
        _atomic_json_dump(paths['meta'], data)
        index = self._load_chat_index()
        found = False
        for s in index.get('sessions') or []:
            if s.get('id') == self._chat_id:
                s['title'] = title
                s['updated_at'] = now
                found = True
                break
        if not found:
            index.setdefault('sessions', []).insert(0, {
                'id': self._chat_id, 'title': title, 'updated_at': now,
            })
        index['active_id'] = self._chat_id
        # keep most recent first
        index['sessions'] = sorted(
            index['sessions'], key=lambda x: x.get('updated_at') or '', reverse=True)
        self._save_chat_index(index)
        self._refresh_chat_combo(index)

    def _switch_chat_session(self, chat_id: str, save_current=True):
        from llm_compose import ComposeSession
        if save_current and self._chat_id and self._chat_id != chat_id:
            self._persist_current_chat()
        paths = self._chat_paths(chat_id)
        if not os.path.exists(paths['meta']):
            return
        try:
            with open(paths['meta'], encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
            QMessageBox.warning(
                self, "对话", f"无法读取对话记录（文件可能已损坏）：\n{e}")
            return
        if not isinstance(data, dict):
            QMessageBox.warning(self, "对话", "对话记录格式无效。")
            return
        self._chat_id = chat_id
        self._chat_messages = list(data.get('messages') or [])
        self._compose_session = ComposeSession()
        blob = data.get('compose')
        if blob:
            self._compose_session.composition_id = blob.get('composition_id') or self._compose_session.composition_id
            self._compose_session.revision = int(blob.get('revision') or 0)
            self._compose_session.prefs.update(blob.get('prefs') or {})
            self._compose_session.plan = blob.get('plan')
            self._compose_session.score_spec = blob.get('score_spec')
            self._compose_session.last_skill_ids = list(blob.get('last_skill_ids') or [])
            self._compose_session.last_advice = blob.get('last_advice') or None
            if blob.get('compose_trace'):
                self._compose_session.compose_trace = blob['compose_trace']
        if not getattr(self._compose_session, 'last_advice', None):
            for m in reversed(self._chat_messages):
                txt = m.get('text') or ''
                if m.get('role') == 'assistant' and '未修改乐谱' in txt:
                    self._compose_session.last_advice = txt
                    break
                skills = m.get('skills') or []
                if m.get('role') == 'assistant' and 'score-analysis' in skills:
                    self._compose_session.last_advice = txt
                    break
        self._render_chat_log()
        self.view.clear_selection()
        # restore last skill trace from history
        last_skills = None
        for m in reversed(self._chat_messages):
            if m.get('skills'):
                last_skills = m['skills']
                break
        self._set_skill_trace(last_skills or getattr(self._compose_session, 'last_skill_ids', None) or [])
        index = self._load_chat_index()
        index['active_id'] = chat_id
        self._save_chat_index(index)
        # load score for this chat if present; otherwise blank (don't keep previous chat's score)
        if os.path.exists(paths['xml']):
            try:
                self.reload_score_from_xml(paths['xml'], rebuild_report=False)
                if os.path.exists(paths['report']):
                    self.report_path = paths['report']
                    self.load_report_into_ui()
            except Exception:
                self._clear_displayed_score()
        elif self._compose_session.score_spec:
            try:
                from llm_compose import spec_to_music21
                m21 = spec_to_music21(self._compose_session.score_spec)
                self._apply_composed_score(m21, self._compose_session.score_spec, note='', persist=False)
            except Exception:
                self._clear_displayed_score()
        else:
            self._clear_displayed_score()
        # Chat list title follows the score name
        score_title = self._score_title_for_chat()
        if score_title:
            self._sync_chat_title_ui(score_title)
        else:
            # keep stored title / first-user fallback in combo via refresh
            stored = (data.get('title') or self._resolve_chat_title())
            self._sync_chat_title_ui(stored)
    def _on_chat_combo_changed(self, index):
        if self._suppress_chat_switch or index < 0:
            return
        chat_id = self.chat_combo.itemData(index)
        if chat_id and chat_id != self._chat_id:
            self._switch_chat_session(chat_id)

    def _new_chat_session(self):
        title = 'New chat' if i18n.language() == 'en' else '新对话'
        self._create_chat_session(activate=True, title=title)

    def _delete_chat_session(self):
        if not self._chat_id:
            return
        if self.chat_combo.count() <= 1:
            QMessageBox.information(self, "对话", "至少保留一个对话。")
            return
        reply = QMessageBox.question(
            self, "删除对话", "确定删除当前对话记录？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        dead = self._chat_id
        paths = self._chat_paths(dead)
        for k in ('meta', 'xml', 'mid', 'wav', 'report', 'report_dev'):
            p = paths[k]
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        index = self._load_chat_index()
        index['sessions'] = [
            s for s in index.get('sessions') or [] if s.get('id') != dead]
        next_id = (index['sessions'][0].get('id') if index['sessions'] else None)
        index['active_id'] = next_id
        self._save_chat_index(index)
        self._chat_id = None
        if next_id:
            self._refresh_chat_combo(index)
            self._switch_chat_session(next_id, save_current=False)
        else:
            self._create_chat_session(activate=True)

    # ---- 编辑模式（五线谱直接编辑 + 智能提示 + 实时校验） ----

    def _wire_edit_controller(self):
        c = self.edit_controller
        c.on_change = self._on_score_edited
        c.on_status = lambda s: self.statusBar().showMessage(s)
        c.on_verdict = self._on_edit_verdict
        c.on_violations = lambda vs: self.renderer.set_violations(vs)
        self.view.edit_controller = c
        self.view.suggestion_callback = self._on_edit_suggestions
        self.view.state_callback = self._sync_palette_state

        p = self.edit_palette
        p.toolChanged.connect(self._on_edit_tool)
        p.durationChanged.connect(self._on_edit_duration)
        p.accidentalChanged.connect(self._on_edit_accidental)
        p.dotToggled.connect(
            lambda: c.toggle_dots(list(self.view.selected_symbols)))
        p.markToggled.connect(
            lambda m: c.toggle_mark(list(self.view.selected_symbols), m))
        p.lineActionRequested.connect(self._on_edit_line_action)
        p.dynamicApplyRequested.connect(self._on_edit_dynamic)
        p.undoRequested.connect(c.undo)
        p.redoRequested.connect(c.redo)
        p.strictToggled.connect(c.set_strict)
        p.assistToggled.connect(self._on_assist_toggled)
        p.midiToggled.connect(self._on_midi_palette_toggled)

        sp = self.score_palette
        sp.markToggled.connect(
            lambda m: c.toggle_mark(list(self.view.selected_symbols), m))
        sp.lineActionRequested.connect(self._on_edit_line_action)
        sp.barlineRequested.connect(self._on_edit_barline)
        sp.dynamicApplyRequested.connect(self._on_edit_dynamic)
        sp.durationChanged.connect(self._on_edit_duration)
        sp.accidentalChanged.connect(self._on_edit_accidental)
        sp.measureAttrRequested.connect(self._on_measure_attr)
        sp.addMeasuresRequested.connect(self._add_measures_dialog)
        sp.addPartRequested.connect(self._add_part_dialog)
        sp.accompRequested.connect(self._accomp_dialog)
        sp.midiToggled.connect(self._on_midi_palette_toggled)
        sp.assistToggled.connect(self._on_assist_toggled)
        sp.strictToggled.connect(c.set_strict)

        self.suggestion_bar.candidateClicked.connect(self._on_suggestion_picked)
        self.suggestion_bar.phraseClicked.connect(self._on_phrase_picked)
        self.suggestion_bar.variantSelected.connect(self._on_assist_variant_selected)

        # 编辑写回 MusicXML 的防抖定时器
        self._edit_sync_timer = QTimer(self)
        self._edit_sync_timer.setSingleShot(True)
        self._edit_sync_timer.setInterval(700)
        self._edit_sync_timer.timeout.connect(self._sync_edited_score)
        # 辅助作曲：最近一次悬停上下文（供 Tab 在未悬停时也能采纳）
        self._assist_ctx = None  # (si, mi, idx)
        self._assist_enabled = True
        self._assist_ghost_locked = False  # 用户用数字键切过后，LLM 不覆盖
        self._assist_judge_gen = 0
        self._assist_judge_bridge = _AssistJudgeBridge(self)
        self._assist_judge_bridge.finished.connect(self._on_assist_judge_finished)

    def set_edit_mode(self, on: bool):
        self.edit_mode = bool(on)
        self.edit_palette.setVisible(self.edit_mode)
        self.score_palette.setVisible(self.edit_mode)
        self.suggestion_bar.setVisible(self.edit_mode)
        self.view.set_edit_mode(self.edit_mode)
        if self.edit_btn.isChecked() != self.edit_mode:
            self.edit_btn.blockSignals(True)
            self.edit_btn.setChecked(self.edit_mode)
            self.edit_btn.blockSignals(False)
        if self.edit_mode:
            self.edit_controller.score = self.score
            self.edit_controller.on_violations(self.edit_controller.lint())
            # 默认切到音符工具，便于直接点写
            self.edit_palette.set_tool('note')
            self.edit_controller.tool = 'note'
            self.view.setFocus()
            assist_on = self._assist_enabled
            self.statusBar().showMessage(
                ('辅助 ON · 1–5 切换预览 · Tab 下一音 · Ctrl+Tab 本小节 · Ctrl+Enter 全曲')
                if assist_on else
                ('编辑模式：♪ 插入音符 ｜ 休止/擦除 ｜ ↑↓ 移调、Shift+1-5 时值、'
                 'Del 删除、Ctrl+Z 撤销'))
        else:
            self.renderer.clear_ghost()
            self.suggestion_bar.set_suggestions([])
            self.statusBar().showMessage('已退出编辑模式')
        for name in ('_tab_assist_sc', '_bar_assist_sc', '_full_assist_sc'):
            sc = getattr(self, name, None)
            if sc is not None:
                sc.setEnabled(bool(self.edit_mode))

    def _enter_assist_compose(self):
        """新建后的默认路径：开编辑 + 辅助作曲。"""
        self._assist_enabled = True
        if self.edit_controller.assist is not None:
            self.edit_controller.assist.enabled = True
        self._on_assist_toggled(True)
        if not self.edit_mode:
            self.set_edit_mode(True)
        else:
            self.set_edit_mode(True)
        self._sync_assist_style_from_prefs()
        self.statusBar().showMessage(
            '辅助作曲 · 1–5 切换 ghost · Tab 下一音 · Ctrl+Tab 本小节 · Ctrl+Enter 全曲')

    def _sync_assist_style_from_prefs(self):
        assist = getattr(self.edit_controller, 'assist', None)
        if assist is None:
            return
        prefs = getattr(self._compose_session, 'prefs', None) or {}
        gid = str(prefs.get('genre_id') or '')
        assist.style_id = gid
        assist.mode = str(prefs.get('mode') or 'major')
        insp = str(prefs.get('assist_inspiration') or assist.inspiration)
        if insp in INSPIRATION_PROFILES:
            assist.inspiration = insp
        if 'assist_enabled' in prefs:
            on = bool(prefs['assist_enabled'])
            self._assist_enabled = on
            assist.enabled = on
            self._on_assist_toggled(on)
        raw = prefs.get('assist_harmony') or {}
        assist.harmony = {int(k): str(v) for k, v in raw.items()}

    def _on_assist_toggled(self, on: bool):
        self._assist_enabled = bool(on)
        if self.edit_controller.assist is not None:
            self.edit_controller.assist.enabled = self._assist_enabled
        for w in (getattr(self.edit_palette, 'assist_check', None),
                  getattr(self.score_palette, 'assist_check', None)):
            if w is None:
                continue
            w.blockSignals(True)
            w.setChecked(self._assist_enabled)
            w.blockSignals(False)
        self.statusBar().showMessage(
            '辅助作曲已开启' if on else '辅助作曲已关闭（仍可用规则建议）')

    def _on_edit_suggestions(self, cands, verdict, si, mi, idx):
        bar = self.suggestion_bar
        if si is None:
            bar.set_context(None, None, None)
            bar.set_suggestions([], '悬停在谱表上，或选中音符/小节后显示候选')
            return
        if verdict is not None and verdict.msg:
            tip = ('✋ ' if verdict.blocked else '⚠ ') + verdict.msg
            self.statusBar().showMessage(tip)
        self._assist_ctx = (si, mi, idx)
        # hover 已 suggest()，只刷新 UI / ghost，避免重复请求听审抖动
        self._apply_assist_ui(si, mi, idx, cands)

    def _accept_assist_tab(self):
        """Tab：采纳下一音。"""
        if not self.edit_mode:
            return
        focus = QApplication.focusWidget()
        if focus is getattr(self, 'chat_input', None):
            return
        notes = getattr(getattr(self, 'suggestion_bar', None), 'notes', None) or []
        if notes:
            self._on_suggestion_picked(notes[0])
        else:
            self.statusBar().showMessage('没有下一音候选')
        self.view.setFocus(Qt.OtherFocusReason)

    def _accept_assist_bar(self):
        """Ctrl+Tab：采纳当前选中的本小节 ghost。"""
        if not self.edit_mode:
            return
        focus = QApplication.focusWidget()
        if focus is getattr(self, 'chat_input', None):
            return
        cand = None
        bar = getattr(self, 'suggestion_bar', None)
        if bar is not None:
            cand = bar.selected_phrase()
        if cand is None:
            try:
                fills = self.edit_controller.suggest_phrases()
                cand = fills[0] if fills else None
            except Exception:
                cand = None
        if cand is not None:
            self._on_phrase_picked(cand)
            self.statusBar().showMessage('已铺满本小节（Ctrl+Tab）')
        else:
            self.statusBar().showMessage('没有本小节铺满稿：先写入至少一个音符')
        self.view.setFocus(Qt.OtherFocusReason)

    def _accept_assist_full(self):
        """Ctrl+Enter：铺满剩余全部小节。"""
        if not self.edit_mode:
            return
        focus = QApplication.focusWidget()
        if focus is getattr(self, 'chat_input', None):
            return
        fills = []
        try:
            fills = self.edit_controller.suggest_full_fills()
        except Exception:
            fills = []
        if fills:
            self._on_phrase_picked(fills[0])
            self.statusBar().showMessage('已全曲铺满（Ctrl+Enter）')
        else:
            self.statusBar().showMessage('没有全曲铺满稿：先写入至少一个音符')
        self.view.setFocus(Qt.OtherFocusReason)

    def _on_assist_variant_selected(self, index: int):
        self._select_assist_variant(index, locked=True)

    def _select_assist_variant(self, index: int, *, locked: bool = True):
        """数字键 / 点击芯片：只切换 ghost，不写谱。"""
        assist = getattr(self.edit_controller, 'assist', None)
        if assist is None:
            return
        cand = assist.set_ghost_index(index)
        if cand is None:
            self.statusBar().showMessage('没有可切换的可能性')
            return
        if locked:
            self._assist_ghost_locked = True
        bar = self.suggestion_bar
        bar.set_selected(assist.ghost_index(), emit=False)
        ctx = self._assist_ctx or bar.context
        if ctx and ctx[0] is not None:
            self._draw_assist_ghost(ctx[0], ctx[1], ctx[2], cand)
        self.statusBar().showMessage(
            f'预览可能性 {assist.ghost_index() + 1} · {cand.reason}（Ctrl+Tab 采纳）')

    def _draw_assist_ghost(self, si, mi, idx, cand):
        if not self.score or not self.score.staves:
            self.renderer.clear_ghost_candidates()
            return
        staff_y = self.view._staff_y_of(si, mi)
        rect = self.view._measure_rect(si, mi)
        if staff_y is None and rect is not None:
            staff_y = rect.top() + 8
        if staff_y is None:
            return
        x = self._assist_anchor_x(si, mi, idx, rect)
        clef = self.score.staves[si].clef
        self.renderer.draw_ghost_candidates(
            [cand], clef, x, staff_y, si, mi, idx)

    def _refresh_assist_at(self, si, mi, idx):
        if not self.edit_mode or self.score is None:
            return
        cands = self.edit_controller.suggest(
            si, mi, idx, self.edit_controller.duration)
        self._apply_assist_ui(si, mi, idx, cands)

    def _apply_assist_ui(self, si, mi, idx, cands):
        variants = []
        if self._assist_enabled:
            try:
                variants = self.edit_controller.suggest_phrases()
            except Exception:
                variants = []
        assist = getattr(self.edit_controller, 'assist', None)
        sel = assist.ghost_index() if assist is not None else 0
        judge = getattr(self.suggestion_bar, '_judge_note', '') or ''
        self.suggestion_bar.set_context(si, mi, idx)
        self.suggestion_bar.set_suggestions(
            cands or [], phrases=variants, selected=sel, judge_note=judge)
        self._assist_ctx = (si, mi, idx)
        active = assist.active_variant() if assist is not None else None
        if active is None and variants:
            active = variants[min(sel, len(variants) - 1)]
        if active is None:
            self.renderer.clear_ghost_candidates()
            return
        self._draw_assist_ghost(si, mi, idx, active)
        if variants and self._assist_enabled and len(variants) >= 2:
            key = (si, mi, idx, tuple(c.reason for c in variants))
            if key != getattr(self, '_assist_judge_key', None):
                self._assist_judge_key = key
                self._assist_ghost_locked = False
                self.suggestion_bar.set_judge_note('听审中…')
                self._start_assist_llm_judge()

    def _start_assist_llm_judge(self):
        assist = getattr(self.edit_controller, 'assist', None)
        if assist is None:
            return
        variants = list(assist.variants())
        if len(variants) < 2:
            self.suggestion_bar.set_judge_note('')
            return
        self._assist_judge_gen += 1
        gen = self._assist_judge_gen
        prefix = list(assist.prefix_labels())
        key_hint = assist.key_hint()
        bridge = self._assist_judge_bridge

        def _worker():
            try:
                from music_score.assist_judge import judge_variants
                best, reason = judge_variants(
                    variants, prefix_labels=prefix, key_hint=key_hint)
            except Exception as e:
                best, reason = 0, f'听审失败：{type(e).__name__}'
            bridge.finished.emit(gen, int(best), str(reason))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_assist_judge_finished(self, gen_id: int, best_idx: int, reason: str):
        try:
            if gen_id != getattr(self, '_assist_judge_gen', 0):
                return  # 过期
            assist = getattr(self.edit_controller, 'assist', None)
            if assist is None:
                return
            self.suggestion_bar.set_judge_note(f'听审：{reason}')
            if getattr(self, '_assist_ghost_locked', False):
                return
            cand = assist.prefer_ghost_index(best_idx)
            if cand is None:
                return
            self.suggestion_bar.set_selected(assist.ghost_index(), emit=False)
            ctx = self._assist_ctx
            if ctx and ctx[0] is not None:
                self._draw_assist_ghost(ctx[0], ctx[1], ctx[2], cand)
            self.statusBar().showMessage(
                f'听审更新预览 → 可能性 {assist.ghost_index() + 1} · {reason}')
        except Exception as e:
            try:
                self.statusBar().showMessage(f'听审更新失败：{type(e).__name__}')
            except Exception:
                pass

    def _on_suggestion_picked(self, cand):
        ctx = self.suggestion_bar.context or self._assist_ctx
        if not ctx or ctx[0] is None:
            return
        si, mi, idx = ctx
        events = getattr(cand, 'preview_events', None) or [
            (cand.pitch, cand.duration)]
        if getattr(cand, 'kind', '') == 'phrase' or len(events) > 1:
            self._assist_ctx = (si, mi, idx)
            n = self.edit_controller.try_insert_events(si, mi, idx, events)
            if n:
                self.statusBar().showMessage(f'已写入铺满稿 {n} 个符号')
            return
        next_idx = (idx or 0) + 1
        self._assist_ctx = (si, mi, next_idx)
        self.suggestion_bar.set_context(si, mi, next_idx)
        if self.edit_controller.try_insert_note(si, mi, idx, cand.pitch,
                                                cand.duration):
            self._refresh_assist_at(si, mi, next_idx)
        else:
            self._assist_ctx = ctx

    def _on_phrase_picked(self, cand):
        ctx = self.suggestion_bar.context or self._assist_ctx
        if not ctx or ctx[0] is None:
            return
        si, mi, idx = ctx
        events = getattr(cand, 'preview_events', None) or [
            (cand.pitch, cand.duration)]
        n = self.edit_controller.try_insert_phrase(si, mi, idx, events)
        if n:
            self.statusBar().showMessage(f'已写入铺满稿 {n} 个符号')

    def _assist_anchor_x(self, si, mi, idx, rect) -> float:
        """灰色候选画在上一音符右侧。"""
        hits = [
            h for h in self.renderer.hit_notes
            if h.get('staff_idx') == si and h.get('measure_idx') == mi
        ]
        hits.sort(key=lambda h: h.get('note_idx', 0))
        if idx and hits:
            prev = None
            for h in hits:
                if h.get('note_idx', 0) < idx:
                    prev = h
            if prev is not None:
                return prev['rect'].right() + 8
        if rect is not None:
            return rect.left() + 56
        return 120.0

    def _on_score_edited(self):
        self.renderer.render_score(self.score, SCORE_WIDTH)
        self.view.apply_highlight()
        self._edit_sync_timer.start()
        ctx = self._assist_ctx
        if ctx and ctx[0] is not None:
            self._refresh_assist_at(*ctx)
        elif self.score and self.score.staves:
            staff = self.score.staves[0]
            for mi in range(len(staff.measures) - 1, -1, -1):
                n = len(staff.measures[mi].notes)
                if n:
                    self._refresh_assist_at(0, mi, n)
                    break

    def _on_edit_tool(self, tool):
        self.edit_controller.tool = tool
        if tool != 'note':
            self.renderer.clear_ghost()

    def _on_edit_duration(self, d):
        keys = list(self.view.selected_symbols)
        if keys:
            self.edit_controller.set_duration(keys, d)
        self.edit_controller.duration = d
        # 左侧面板与顶栏同步高亮（不重发信号）
        try:
            self.edit_palette._pick_duration(d, emit=False)
        except Exception:
            pass

    def _on_edit_accidental(self, a):
        self.edit_controller.accidental = a
        keys = list(self.view.selected_symbols)
        if keys:
            self.edit_controller.set_accidental(keys, a)
        try:
            self.edit_palette._pick_accidental(a, emit=False)
        except Exception:
            pass

    def _on_edit_dynamic(self, dyn):
        measures = sorted(self.view.selected_measures)
        if not measures:
            # 若选了音符，把力度记到音符所在小节
            for _si, mi, _ni in self.view.selected_symbols:
                measures.append(mi)
            measures = sorted(set(measures))
        if not measures:
            self.statusBar().showMessage('先在谱面点选小节或音符，再应用力度')
            return
        self.edit_controller.set_dynamics(measures, dyn)

    def _on_edit_line_action(self, action: str):
        keys = list(self.view.selected_symbols)
        if not keys:
            self.statusBar().showMessage('先选中音符，再点延音/连线/渐强弱等符号')
            return
        self.edit_controller.apply_line_action(keys, action)

    def _on_edit_barline(self, style: str):
        measures = sorted(self.view.selected_measures)
        if not measures:
            for _si, mi, _ni in self.view.selected_symbols:
                measures.append(mi)
            measures = sorted(set(measures))
        if not measures:
            self.statusBar().showMessage('先选中小节，再设小节线')
            return
        self.edit_controller.set_barline(measures, style)

    def _edit_target_measures(self):
        measures = sorted(self.view.selected_measures)
        if not measures:
            for _si, mi, _ni in self.view.selected_symbols:
                measures.append(mi)
        return sorted(set(measures))

    def _edit_target_staffs(self):
        staffs = sorted({si for si, _mi, _ni in self.view.selected_symbols})
        if not staffs and self.score and self.score.staves:
            staffs = [0]
        return staffs

    def _on_measure_attr(self, spec: str):
        measures = self._edit_target_measures()
        if not measures:
            self.statusBar().showMessage('先选中小节（或选中小节内音符），再应用调号/拍号/谱号等')
            return
        c = self.edit_controller
        if spec.startswith('key:'):
            try:
                c.set_measure_key(measures, int(spec[4:]))
            except ValueError:
                pass
            return
        if spec.startswith('time:'):
            raw = spec[5:]
            if raw == 'common':
                c.set_measure_time(measures, numerator=4, denominator=4, symbol='common')
            elif raw == 'cut':
                c.set_measure_time(measures, numerator=2, denominator=2, symbol='cut')
            elif '/' in raw:
                num_s, den_s = raw.split('/', 1)
                c.set_measure_time(measures, numerator=int(num_s), denominator=int(den_s))
            return
        if spec.startswith('clef:'):
            c.set_measure_clef(self._edit_target_staffs(), measures, spec[5:])
            return
        if spec.startswith('tempo_bpm:'):
            try:
                c.set_measure_tempo(measures, bpm=int(spec[10:]))
            except ValueError:
                pass
            return
        if spec.startswith('tempo:'):
            c.set_measure_tempo(measures, mark=spec[6:])
            return
        if spec.startswith('text:'):
            c.set_measure_text(measures, spec[5:])
            return
        if spec.startswith('rehearsal:'):
            c.set_rehearsal_mark(measures, spec[10:])

    def _sync_palette_state(self, duration, accidental):
        """快捷键改变插入状态后，同步调色板按钮。"""
        if duration is not None:
            self.edit_palette._pick_duration(duration, emit=False)
        if accidental is not None:
            self.edit_palette._pick_accidental(accidental, emit=False)

    def _on_edit_verdict(self, v):
        if v is None or v.level == 'ok':
            return
        if v.blocked:
            return  # controller.on_status 已提示 ✋
        self.statusBar().showMessage(f'⚠ {v.msg}')

    def _sync_edited_score(self):
        """编辑防抖落定：模型 → music21 → MusicXML，并重合成以便播放。"""
        try:
            if self.score is None or not self.score.staves:
                return
            has_sound = any(
                (not getattr(n, 'is_rest', False))
                for st in self.score.staves
                for m in st.measures
                for n in (m.notes or [])
            )
            self.m21_score = model_to_m21(self.score,
                                          title=self.score.title,
                                          composer=self.score.composer)
            if self.musicxml_path:
                self.m21_score.write('musicxml', fp=self.musicxml_path)
            # 同步 MIDI 路径，便于导出与合成
            try:
                mid = None
                if self._chat_id:
                    mid = self._chat_paths(self._chat_id).get('mid')
                if mid:
                    export_midi(self.m21_score, mid)
            except Exception:
                pass
            if not has_sound:
                self.audio = None
                self._pending_play = False
                self.play_btn.setEnabled(False)
                self.export_wav_btn.setEnabled(False)
                self.statusBar().showMessage(
                    '编辑已写回 MusicXML（尚无发声音符，写入后可播放）')
                return
            # 有发声音符：保持 ▶ 可点——已有音频可先播旧版，无音频则点播放排队合成后开播
            self.play_btn.setEnabled(True)
            self.statusBar().showMessage('编辑已写回，正在合成可播放音频…')
            threading.Thread(target=self._synth_worker, daemon=True).start()
        except Exception as e:
            self.statusBar().showMessage(f'编辑写回失败：{e}')

    def _on_score_selection_changed(self):
        from llm_compose import describe_selection
        # 暂停中改了选区：下次点播放从新选的小节开始，而不是续播
        self._play_from_selection = self._selection_start_measure() is not None
        sel = self._current_selection_payload()
        desc = describe_selection(sel) if sel else ''
        self.selection_label.setText(f"选区：{desc}" if desc else "选区：无（对话将作用于整曲）")
        try:
            if self.inspector is not None:
                self.inspector.set_selection(sel)
        except Exception:
            pass

    def _current_selection_payload(self) -> Optional[dict]:
        measures = sorted(self.view.selected_measures)
        symbols = []
        for staff_idx, measure_idx, note_idx in self.view.selected_symbols:
            pitch, dur = None, 'quarter'
            try:
                note = self.score.staves[staff_idx].measures[measure_idx].notes[note_idx]
                pitch = _pitch_label_from_note(note)
                if pitch == 'rest':
                    pitch = None
                dur = _duration_label_from_note(note)
            except Exception:
                pass
            symbols.append({
                'staff_idx': staff_idx,
                'measure_idx': measure_idx,
                'note_idx': note_idx,
                'pitch': pitch,
                'duration': dur,
            })
        if not measures and not symbols:
            return None
        return {'measures': measures, 'symbols': symbols}

    def open_skill_library(self, tab=0):
        """打开作曲库管理器。tab 见 SkillLibraryDialog.TAB_*。"""
        import composition_skills as skill_lib
        dlg = SkillLibraryDialog(self, tab=tab)
        dlg.exec_()
        skill_lib.invalidate_cache()
        self._refresh_style_combo()
        # 关掉库窗口后同步刷新「本次 Skill」标签，避免显示已停用的 skill
        last = getattr(self._compose_session, 'last_skill_ids', None) or []
        self._set_skill_trace(last)
        self.statusBar().showMessage("作曲库已关闭（更改已生效）")

    def reload_skill_library(self):
        import composition_skills as skill_lib
        skill_lib.invalidate_cache()
        n = len(skill_lib.list_skills(force=True))
        g = len(skill_lib.list_genres(force=True))
        learned = len(skill_lib.learned_genres(force=True))
        s = len(skill_lib.script_library())
        live = sum(1 for sid in skill_lib.injection_matrix()
                   if skill_lib.live_stages(sid))
        self._refresh_style_combo()
        self.statusBar().showMessage(
            f"已重新加载：{n} 个任务 Skill（{live} 个注入提示词），"
            f"{learned}/{g} 个风格包已学过（UI 可见），{s} 个内核脚本")
        QMessageBox.information(
            self, "作曲库",
            f"已重新加载\n"
            f"任务 Skill：{n}（其中 {live} 个会注入提示词）\n"
            f"风格 Genre：{g}（已学过 {learned} 个，有 grammar.yaml；UI 只列这些）\n"
            f"内核脚本：{s}",
        )

    def _schedule_chat_persist(self):
        self._chat_persist_timer.start()

    def _set_skill_trace(self, skill_ids, pipeline=''):
        import composition_skills as skill_lib
        if not skill_ids:
            self.skill_trace_label.setText("本次 Skill：—")
            return
        label = skill_lib.format_skill_label(skill_ids)
        ids = ', '.join(skill_ids)
        pipe = f" ｜ 流程 {pipeline}" if pipeline else ''
        self.skill_trace_label.setText(f"本次 Skill：{label}{pipe}\n{ids}")

    def _preview_skills_for_message(self, text, selection):
        """Resolve skills before LLM runs so the user sees what will be called."""
        import composition_skills as skill_lib
        from llm_compose import (
            is_advice_affirmation, parse_user_turn, resolve_style_phrase,
            _detect_learned_grammar, parse_command,
        )
        cmds, nl = parse_user_turn(text)
        pending = None
        prefs = {}
        if self._compose_session is not None:
            pending = getattr(self._compose_session, '_pending_edit', None)
            prefs = self._compose_session.prefs or {}

        if any(c == '/demo' for c, _ in cmds):
            return [], 'command'

        # /compose-grammar 或可识别的已学风格短句 → 确定性语法（不调 Planner）
        if any(c == '/compose-grammar' for c, _ in cmds):
            return ['compose-grammar'], 'compose-grammar'
        if (not selection and not pending
                and _detect_learned_grammar(nl or text)):
            return ['compose-grammar'], 'compose-grammar'

        # Confirm after /advice → full rewrite using stored suggestions
        probe = (nl or text or '').strip()
        has_advice = bool(
            self._compose_session
            and (getattr(self._compose_session, 'last_advice', None) or '').strip()
        )
        if any(c == '/apply' for c, _ in cmds) or (
            has_advice and is_advice_affirmation(probe)
        ):
            return skill_lib.resolve_skills(
                text, None, 'rewrite', slash_cmd='/rewrite',
                style_pref=prefs.get('style') or '',
                genre_id=prefs.get('genre_id') or '',
            )

        # Will this turn actually call the LLM?
        will_compose = bool(nl)
        style_leftover = ''
        for c, a in cmds:
            if c == '/style':
                _label, style_leftover, _gid = resolve_style_phrase(a)
                if style_leftover:
                    will_compose = True
            if c in (
                '/continue', '/extend', '/develop', '/variation', '/harmonize',
                '/orchestrate', '/transpose', '/shorter', '/longer',
                '/simplify', '/complexify', '/rewrite', '/advice', '/apply',
            ):
                will_compose = True
            if c == '/learn-score':
                will_compose = False
            if c == '/compose-grammar':
                return ['compose-grammar'], 'compose-grammar'
        if cmds and not will_compose:
            return [], 'command'

        # 新曲自然语言：只预告语法作曲（不再出现 planner_composer / compose-full）
        if (will_compose and not selection and not pending
                and not (self._compose_session and self._compose_session.score_spec)):
            return ['compose-grammar'], 'compose-grammar'

        hay = nl or style_leftover or text
        for c, a in cmds:
            if c == '/style' and a:
                hay = f'{a} {hay}'
        slash = cmds[-1][0] if cmds else None
        from llm_compose import load_llm_config
        return skill_lib.resolve_skills(
            hay, selection, pending, slash_cmd=slash,
            style_pref=prefs.get('style') or '',
            genre_id=prefs.get('genre_id') or '',
            llm_available=bool((load_llm_config() or {}).get('api_key')),
        )
    def _chat_append(self, who: str, text: str, skills=None):
        role = 'user' if who in ('你', 'You') else 'assistant'
        entry = {
            'role': role,
            'text': text,
            'ts': datetime.now().isoformat(timespec='seconds'),
        }
        if skills:
            entry['skills'] = list(skills)
        self._chat_messages.append(entry)
        is_en = i18n.language() == 'en'
        display_who = ('You' if role == 'user' else 'Assistant') if is_en else who
        sep = ': ' if is_en else '：'
        self.chat_log.appendPlainText(f"{display_who}{sep}{text}\n")
        self.chat_log.verticalScrollBar().setValue(self.chat_log.verticalScrollBar().maximum())
        self._schedule_chat_persist()

    def _chat_append_think(self):
        """把 session.last_translate（意图解析 / LLM 推理 / 脚本调用链）展示到对话区。"""
        tr = getattr(self._compose_session, 'last_translate', None)
        if not tr:
            return
        p = tr.get('plan') or {}
        emo = tr.get('emotion_hint') or {}
        matched = emo.get('matched') if isinstance(emo, dict) else None
        lines = ['']
        lines.append("🧠 思考过程 / 翻译追溯")
        lines.append("─" * 32)
        mood = p.get('mood')
        mood_txt = ', '.join(mood) if isinstance(mood, list) else (mood or '—')
        lines.append("【意图解析】")
        lines.append(f"  风格：{p.get('genre') or '—'}")
        lines.append(f"  情绪：{mood_txt}" + (f"   ← 命中情绪词 {matched}" if matched else ""))
        tt = p.get('tempo_text') or ''
        lines.append(
            f"  速度：♩={p.get('tempo', '?')}" + (f" ({tt})" if tt else "")
            + f"   调式：{p.get('mode') or '—'}   拍号：{p.get('meter') or '—'}"
            + f"   曲式：{p.get('form') or '—'}   小节：{p.get('bars', '?')}")
        if emo:
            lines.append("【情绪 → 参数（本地约束）】")
            lines.append(
                f"  命中「{(' / '.join(matched) if matched else '—')}」→ "
                f"♩={emo.get('bpm')} {emo.get('tempo_text')} · "
                f"{emo.get('mode_bias') or '调式不限'} · "
                f"{emo.get('rhythm')} · {emo.get('dynamics')}")
        genre_fixes = tr.get('genre_fixes') or []
        if genre_fixes:
            lines.append("【风格硬约束（本地执行）】")
            for gf in genre_fixes:
                lines.append(f"  ✔ {gf}")
        if tr.get('deterministic'):
            lines.append("【生成方式】确定性脚本（不调 LLM）")
        else:
            lines.append("【翻译推理（LLM）】")
            lines.append("  " + (tr.get('reasoning') or '（模型未返回推理文本）'))
        calls = tr.get('calls')
        if calls:
            lines.append("【调用的脚本】")
            for cl in (calls.splitlines() if isinstance(calls, str) else calls):
                if cl.strip():
                    lines.append("  " + cl)
        self._chat_append("助手", "\n".join(lines))

    def _render_chat_log(self):
        lines = []
        is_en = i18n.language() == 'en'
        for m in self._chat_messages:
            role = m.get('role')
            if is_en:
                who = 'You' if role == 'user' else 'Assistant'
                sep = ': '
            else:
                who = '你' if role == 'user' else '助手'
                sep = '：'
            lines.append(f"{who}{sep}{m.get('text', '')}\n")
            skills = m.get('skills')
            if skills:
                import composition_skills as skill_lib
                lbl = skill_lib.format_skill_label(skills)
                lines.append(f"(Skill: {lbl})\n" if is_en else f"（Skill：{lbl}）\n")
        self.chat_log.setPlainText('\n'.join(lines) if lines else '')
        self.chat_log.verticalScrollBar().setValue(self.chat_log.verticalScrollBar().maximum())

    def send_chat_message(self):
        self._hide_style_popup()
        text = self.chat_input.toPlainText().strip()
        if not text:
            return
        if self._chat_busy:
            self.statusBar().showMessage("上一请求仍在进行（LLM 最长约 3 分钟），请稍候…")
            return
        self._record_cmd_history(text)
        self.chat_input.clear()
        sel = self._current_selection_payload()
        import composition_skills as skill_lib
        from llm_compose import is_advice_affirmation
        # Affirmation after /advice: ignore leftover selection, apply advice rewrite
        if sel and is_advice_affirmation(text) and (
            self._compose_session
            and (getattr(self._compose_session, 'last_advice', None) or '').strip()
        ):
            self.view.clear_selection()
            sel = None
            self._chat_append("助手", "将按上一轮修改建议整曲重写（已忽略选区）。")
        # Long multi-point rewrite briefs: ignore leftover staff selection
        elif sel and skill_lib.is_full_score_revision_request(text):
            self.view.clear_selection()
            sel = None
            self._chat_append(
                "助手",
                "检测到整曲修改说明，已忽略当前选区，将按整曲重写处理。",
            )
        display = text
        is_en = i18n.language() == 'en'
        if sel:
            from llm_compose import describe_selection
            display = f"[Selection {describe_selection(sel)}]\n{text}" if is_en else f"[选区 {describe_selection(sel)}]\n{text}"
        self._chat_append("You" if is_en else "你", display)
        try:
            skill_ids, pipeline = self._preview_skills_for_message(text, sel)
        except Exception as e:
            err_who = "Assistant" if is_en else "助手"
            err_msg = f"Routing failed: {e}" if is_en else f"路由失败：{e}"
            self._chat_append(err_who, err_msg)
            return
        self._set_skill_trace(skill_ids, pipeline)
        if pipeline != 'command':
            report = skill_lib.format_skill_report(skill_ids, pipeline)
            if pipeline == 'advice':
                wait_line = 'Analyzing (analyzing score -> generating advice, usually 1-2 min)...' if is_en else '分析中（解析乐谱 → 给出修改建议，通常 1–2 分钟）…'
            elif pipeline == 'compose-grammar':
                wait_line = 'Composing with grammar (deterministic /compose-grammar, no LLM call)...' if is_en else '语法作曲中（确定性 /compose-grammar，不调 LLM）…'
                report = ('[Call] Interpretable grammar composition (/compose-grammar)\nMain flow: compose-grammar'
                          if is_en else
                          '【调用】可解释语法作曲（/compose-grammar）\n主流程: compose-grammar')
            elif sel:
                wait_line = 'Editing selection (calling LLM, usually 1-3 min)...' if is_en else '改选区中（调用 LLM，通常 1–3 分钟）…'
            else:
                wait_line = 'Composing (Planner translating intent -> composing, usually 1-3 min)...' if is_en else '创曲中（Planner 翻译意图 → 作曲，通常 1–3 分钟）…'
            preview_title = 'Routing preview:' if is_en else '路由预览：'
            self._chat_append(
                "Assistant" if is_en else "助手",
                f"{preview_title}\n{report}\n{wait_line}",
                skills=skill_ids,
            )
        self._chat_busy = True
        self.chat_send_btn.setEnabled(False)
        self._chat_busy_started = time.time()
        detail = skill_lib.format_skill_label(skill_ids) if skill_ids else ''
        if pipeline == 'advice':
            busy = f"分析建议中… Skill: {detail}" if detail else "正在分析乐谱并生成修改建议…"
            self._chat_busy_kind = 'advice'
        elif sel:
            busy = f"改选区中… Skill: {detail}" if detail else "改选区中…"
            self._chat_busy_kind = 'edit'
        elif pipeline == 'compose-grammar':
            busy = '语法作曲中（/compose-grammar，不调 LLM）…'
            self._chat_busy_kind = 'grammar'
        else:
            busy = f"创曲中… Skill: {detail}" if detail else "创曲中（计划→作曲→校验）…"
            self._chat_busy_kind = 'compose'
        self.statusBar().showMessage(busy)
        if not hasattr(self, '_chat_busy_timer'):
            self._chat_busy_timer = QTimer(self)
            self._chat_busy_timer.setInterval(5000)
            self._chat_busy_timer.timeout.connect(self._on_chat_busy_tick)
        self._chat_busy_timer.start()
        threading.Thread(
            target=self._chat_worker, args=(text, sel), daemon=True
        ).start()

    def _on_chat_busy_tick(self):
        if not self._chat_busy:
            self._chat_busy_timer.stop()
            return
        elapsed = int(time.time() - getattr(self, '_chat_busy_started', time.time()))
        kind = getattr(self, '_chat_busy_kind', 'compose')
        if kind == 'advice':
            msg = f"仍在分析并撰写建议… 已等待 {elapsed}s（LLM 超时约 180s/次）"
        elif kind == 'edit':
            msg = f"改选区仍在进行… 已等待 {elapsed}s（LLM 超时约 180s/次）"
        elif kind == 'grammar':
            msg = f"语法作曲仍在进行… 已等待 {elapsed}s（确定性 /compose-grammar）"
        else:
            msg = f"创曲仍在进行… 已等待 {elapsed}s（LLM 超时约 180s/次）"
        self.statusBar().showMessage(msg)

    def _genre_style_choices(self) -> List[Tuple[str, str, str]]:
        """[(display, name_for_cmd, genre_id), ...]——只列「学过」的风格包。

        与右侧检查器「风格包」下拉同源（都走 composition_skills.learned_genres），
        口径一致：有 grammar.yaml（/learn-grammar）才算学过。
        全库（含未学习）在「作曲库 → 风格 Genre」里看。
        """
        import composition_skills as skill_lib
        out = []
        for g in skill_lib.learned_genres():
            if not g.get('enabled', True) or g.get('taxonomy_errors'):
                continue
            name = g.get('name') or g.get('pack_id') or g['id']
            dims = skill_lib.format_genre_dims(g)
            label = f"{name}  ·  {dims}" if dims else str(name)
            out.append((label, str(name), g['id']))
        return out

    def _refresh_style_combo(self):
        if not hasattr(self, 'style_combo'):
            return
        self.style_combo.blockSignals(True)
        self.style_combo.clear()
        self.style_combo.addItem("（选择风格包，填入后可再写描述）", "")
        for label, name, gid in self._genre_style_choices():
            self.style_combo.addItem(label, name)
            idx = self.style_combo.count() - 1
            self.style_combo.setItemData(idx, gid, Qt.UserRole + 1)
        self.style_combo.blockSignals(False)
        self._refresh_inspector_styles()

    def _refresh_inspector_styles(self):
        """兼容旧调用；检查器已移除，无操作。"""
        return

    def _on_style_combo_picked(self, index: int):
        if index <= 0:
            return
        self._insert_selected_style()

    def _insert_selected_style(self):
        name = self.style_combo.currentData()
        if not name:
            # fallback to first real item if placeholder selected
            if self.style_combo.count() > 1:
                self.style_combo.setCurrentIndex(1)
                name = self.style_combo.currentData()
            if not name:
                return
        idx = self.style_combo.currentIndex()
        gid = self.style_combo.itemData(idx, Qt.UserRole + 1)
        if self._compose_session is not None:
            if gid:
                self._compose_session.prefs['genre_id'] = str(gid)
            self._compose_session.prefs['style'] = str(name)
            self._sync_assist_style_from_prefs()
        self._apply_style_to_input(str(name))
        self.style_combo.setCurrentIndex(0)

    def _apply_style_to_input(self, style_name: str):
        """Put `/style {name} ` into input; keep /new and any description after style."""
        import re
        cur = self.chat_input.toPlainText()
        desc = ''
        lead = ''
        m = re.match(r'^(.*?)(/style\s+)(\S+)(?:\s+(.*))?$', cur, re.I | re.S)
        if m:
            lead = m.group(1).strip()
            desc = (m.group(4) or '').strip()
        else:
            m2 = re.match(r'^((?:/\w+(?:\s+\S+)?\s+)*)(.*)$', cur)
            if m2:
                # peel known leading no-arg cmds only (/new /clear …)
                tokens = cur.strip().split()
                kept = []
                i = 0
                while i < len(tokens) and tokens[i].startswith('/') and tokens[i].lower() in (
                    '/new', '/clear', '/help', '/?', '/config', '/demo',
                ):
                    kept.append(tokens[i])
                    i += 1
                lead = ' '.join(kept)
                rest = ' '.join(tokens[i:])
                if rest.lower().startswith('/style'):
                    rest = re.sub(r'^/style\s*\S*\s*', '', rest, flags=re.I).strip()
                desc = rest
            else:
                desc = cur.strip()
        # drop accidental old /style from lead
        lead = re.sub(r'/style\b.*', '', lead, flags=re.I).strip()
        parts = []
        if lead:
            parts.append(lead)
        parts.append(f'/style {style_name}')
        text = ' '.join(parts) + ' '
        if desc and desc != style_name and not desc.startswith(style_name + ' '):
            # if desc is only the old style name, ignore
            if desc != style_name:
                text = f"{' '.join(parts)} {desc}"
                if not text.endswith(' '):
                    text += ' '
        self.chat_input.setPlainText(text.rstrip() + ' ')
        self.chat_input.setFocus()
        self.chat_input.moveCursor(QTextCursor.End)
        self._hide_style_popup()

    def _style_query_context(self, text: str, cursor: int = None):
        """If editing inside /style argument, return (prefix_before_arg, filter)."""
        import re
        cursor = len(text) if cursor is None else cursor
        # find last /style before cursor
        head = text[:cursor]
        m = None
        for m in re.finditer(r'/style\b', head, re.I):
            pass
        if not m:
            return None
        arg_start = m.end()
        # skip one space
        if arg_start < len(head) and head[arg_start].isspace():
            arg_start += 1
        elif arg_start == len(head):
            # typed `/style` with no space yet — still offer list after completing space
            return head[:m.start()], ''
        # argument runs until next /cmd or end (of head)
        arg = head[arg_start:]
        if re.search(r'\s+/', arg):
            return None  # already past style arg into more commands
        # if there's a space in arg, user may have started description — only filter first token
        if ' ' in arg.strip() or (arg.endswith(' ') and arg.strip()):
            # completed a style token; hide popup so they can type description
            return None
        return head[:arg_start], arg.strip()

    def _on_chat_input_edited(self, *args):
        text = self.chat_input.toPlainText()
        cursor = self.chat_input.textCursor().position()
        ctx = self._style_query_context(text, cursor)
        if ctx is None:
            self._hide_style_popup()
            return
        _prefix, filt = ctx
        self._show_style_popup(filt)

    def _show_style_popup(self, filt: str):
        choices = self._genre_style_choices()
        low = (filt or '').lower()
        self._style_popup.clear()
        for label, name, gid in choices:
            if low and low not in name.lower() and low not in label.lower() and low not in gid.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, name)
            item.setData(Qt.UserRole + 1, gid)
            self._style_popup.addItem(item)
        if self._style_popup.count() == 0:
            self._hide_style_popup()
            return
        self._style_popup.setCurrentRow(0)
        # position under chat_input
        gp = self.chat_input.mapToGlobal(self.chat_input.rect().bottomLeft())
        w = max(self.chat_input.width(), 320)
        rows = min(self._style_popup.count(), 10)
        h = max(28, self._style_popup.sizeHintForRow(0)) * rows + 4
        self._style_popup.setFixedWidth(w)
        self._style_popup.setFixedHeight(min(h, 280))
        self._style_popup.move(gp)
        self._style_popup_active = True
        self._style_popup.show()
        self._style_popup.raise_()

    def _hide_style_popup(self):
        self._style_popup_active = False
        if hasattr(self, '_style_popup'):
            self._style_popup.hide()

    def _on_style_popup_clicked(self, item: QListWidgetItem):
        name = item.data(Qt.UserRole)
        if name:
            self._apply_style_to_input(str(name))

    def eventFilter(self, obj, event):
        if obj is getattr(self, 'chat_input', None) and event.type() == QEvent.KeyPress:
            popup_active = self._style_popup_active and self._style_popup.isVisible()
            key = event.key()
            if popup_active:
                if key == Qt.Key_Down:
                    row = min(self._style_popup.currentRow() + 1, self._style_popup.count() - 1)
                    self._style_popup.setCurrentRow(row)
                    return True
                if key == Qt.Key_Up:
                    row = max(self._style_popup.currentRow() - 1, 0)
                    self._style_popup.setCurrentRow(row)
                    return True
                if key == Qt.Key_Escape:
                    self._hide_style_popup()
                    return True
                if key in (Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter) and self._style_popup.currentItem():
                    self._on_style_popup_clicked(self._style_popup.currentItem())
                    return True
            else:
                # Enter 发送；Shift+Enter 换行
                if key in (Qt.Key_Return, Qt.Key_Enter) and not (
                        event.modifiers() & Qt.ShiftModifier):
                    self._on_chat_return()
                    return True
                # ↑/↓ 翻命令履历（光标在首行/末行时才接管）
                if key == Qt.Key_Up and self._cmd_hist_nav(-1):
                    return True
                if key == Qt.Key_Down and self._cmd_hist_nav(1):
                    return True
        return super().eventFilter(obj, event)

    def focusNextPrevChild(self, next_):
        """编辑时拦截窗口级 Tab，避免光标跳到对话/检查器；不在此采纳候选。"""
        focus = QApplication.focusWidget()
        if self.edit_mode and focus is not getattr(self, 'chat_input', None):
            return True
        return super().focusNextPrevChild(next_)

    # ---------- 命令履历 ----------

    def _record_cmd_history(self, text: str):
        text = (text or '').strip()
        if not text:
            return
        if self._cmd_history and self._cmd_history[-1] == text:
            self._cmd_hist_idx = len(self._cmd_history)
            return
        self._cmd_history.append(text)
        if len(self._cmd_history) > 100:
            self._cmd_history = self._cmd_history[-100:]
        self._cmd_hist_idx = len(self._cmd_history)
        self._cmd_hist_draft = ''

    def _cmd_hist_nav(self, direction: int) -> bool:
        """direction=-1 上一条 / +1 下一条。接管按键返回 True。"""
        hist = self._cmd_history
        if not hist:
            return False
        ed = self.chat_input
        tc = ed.textCursor()
        if direction < 0:
            at_edge = not tc.movePosition(QTextCursor.Up)
        else:
            at_edge = not tc.movePosition(QTextCursor.Down)
        if not at_edge:
            return False  # 多行编辑中，让光标正常移动
        if direction < 0:
            if self._cmd_hist_idx == len(hist):
                cur = ed.toPlainText()
                if cur and cur != hist[-1]:
                    self._cmd_hist_draft = cur
                elif cur == hist[-1]:
                    self._cmd_hist_draft = ''
            self._cmd_hist_idx = max(0, self._cmd_hist_idx - 1)
        else:
            if self._cmd_hist_idx >= len(hist):
                return False
            self._cmd_hist_idx += 1
        if self._cmd_hist_idx >= len(hist):
            ed.setPlainText(self._cmd_hist_draft)
        else:
            ed.setPlainText(hist[self._cmd_hist_idx])
        ed.moveCursor(QTextCursor.End)
        return True

    def _on_chat_return(self):
        if self._style_popup_active and self._style_popup.isVisible():
            item = self._style_popup.currentItem()
            if item:
                self._on_style_popup_clicked(item)
                return
        self.send_chat_message()

    def _chat_quick(self, text: str):
        self.chat_input.setPlainText(text)
        cursor = self.chat_input.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.chat_input.setTextCursor(cursor)
        self.send_chat_message()

    def _chat_worker(self, text, selection):
        from llm_compose import CommandOnly, compose_from_text
        # 入口快照：LLM 等待期用户可能切会话，禁止直读会被主线程改写的字段
        chat_id = self._chat_id
        session = self._compose_session
        m21 = self.m21_score
        try:
            score, spec, note = compose_from_text(
                session, text, selection=selection,
                m21_score=m21,
            )
            paths = self._chat_paths(chat_id) if chat_id else {
                'xml': LLM_MUSICXML_PATH, 'mid': LLM_MIDI_PATH, 'wav': LLM_WAV_PATH,
            }
            with _RENDER_LOCK:
                write_score_bundle(
                    score,
                    musicxml_path=paths['xml'],
                    midi_path=paths['mid'],
                    wav_path=paths.get('wav', LLM_WAV_PATH),
                )
            # mirror XML/MIDI only（跳过第二次整包写出，加快响应）
            try:
                with _RENDER_LOCK:
                    if paths['xml'] != LLM_MUSICXML_PATH:
                        score.write('musicxml', fp=LLM_MUSICXML_PATH)
                    if paths['mid'] != LLM_MIDI_PATH:
                        score.write('midi', fp=LLM_MIDI_PATH)
            except Exception:
                pass
            self.chat_ready.emit(('ok', score, spec, note, paths))
        except CommandOnly as e:
            self.chat_ready.emit(('cmd', e.message))
        except Exception as e:
            self.chat_ready.emit(('err', str(e)))

    def _apply_composed_score(self, m21_score, spec, note='', persist=True, paths=None,
                              compose_trace=None):
        self.m21_score = m21_score
        self._last_spec = dict(spec or {})   # 供 LilyPond/归档/评估读取
        # 本次生成自带的流程痕迹（骨架路径）；为 None 时报告回落会话痕迹
        self._last_compose_trace = compose_trace
        paths = paths or (
            self._chat_paths(self._chat_id) if self._chat_id else {
                'xml': LLM_MUSICXML_PATH, 'report': LLM_REPORT_PATH,
            }
        )
        self.musicxml_path = paths.get('xml', LLM_MUSICXML_PATH)
        is_en = i18n.language() == 'en'
        title = str(spec.get('title') or ('Chat composition' if is_en else '对话创曲'))
        composer = str(spec.get('composer') or ('Chat composition' if is_en else '对话创曲'))
        self.score = m21_to_model(m21_score, title=title, composer=composer)
        self.edit_controller.score = self.score
        self.renderer.set_violations([])
        # keep selection highlights if still valid
        self.renderer.render_score(self.score, SCORE_WIDTH)
        self.view.apply_highlight()
        self.report_path = paths.get('report', LLM_REPORT_PATH)
        n = len(self.score.staves[0].measures) if self.score.staves else 0
        key_name, _ = _key_info(self.score.key_signature.sharps)
        if is_en:
            k_name = i18n.tr(key_name)
            self.info_label.setText(
                f"♪ {self.score.title} ｜ {k_name} ｜ "
                f"{self.score.time_signature.numerator}/{self.score.time_signature.denominator} ｜ "
                f"♩={self.score.tempo} ｜ {n} measures ｜ Source: {os.path.basename(self.musicxml_path)}"
            )
            self.setWindowTitle(f"Score Studio - {self.score.title}")
        else:
            self.info_label.setText(
                f"♪ {self.score.title} ｜ {key_name} ｜ "
                f"{self.score.time_signature.numerator}/{self.score.time_signature.denominator} ｜ "
                f"♩={self.score.tempo} ｜ {n} 小节 ｜ 源: {os.path.basename(self.musicxml_path)}"
            )
            self.setWindowTitle(f"乐谱工作室 - {self.score.title}")
        self._rebuild_mixer()
        self._update_lcd(0.0)
        # Keep chat dropdown title aligned with the score name
        if self._chat_id and title:
            self._sync_chat_title_ui(title)
        # 分析报告放到后台，避免卡住 UI
        report_path = self.report_path
        score_snapshot = self.score
        spec_snapshot = dict(spec or {})
        try:
            import llm_compose as _llm_mod
            not_log = _llm_mod.last_notation_log()
        except Exception:
            not_log = None

        compose_trace_snapshot = _compose_trace_of(self)

        def _report_worker():
            try:
                export_analysis_report(score_snapshot, report_path,
                                       spec=spec_snapshot, notation_log=not_log,
                                       compose_trace=compose_trace_snapshot)
                self.report_ready.emit(report_path)
            except Exception as e:
                self.report_ready.emit(f'ERR:{e}')

        threading.Thread(target=_report_worker, daemon=True).start()
        self.play_btn.setEnabled(False)
        self.export_wav_btn.setEnabled(False)
        if persist:
            self._schedule_chat_persist()
        self._sections = list(spec.get('sections') or self._sections)
        self._update_daw_views()
        threading.Thread(target=self._synth_worker, daemon=True).start()

    def _on_chat_ready(self, result):
        self._chat_busy = False
        self.chat_send_btn.setEnabled(True)
        if hasattr(self, '_chat_busy_timer'):
            self._chat_busy_timer.stop()
        kind = result[0]
        used = list(getattr(self._compose_session, 'last_skill_ids', None) or [])
        if used:
            self._set_skill_trace(used)
        if kind == 'cmd':
            msg = result[1]
            # /addbars N → 直接扩展当前谱面
            if isinstance(msg, str) and msg.startswith('__ADDBARS__:'):
                try:
                    n = int(msg.split(':', 1)[1])
                except Exception:
                    n = 0
                total = self._add_measures(n) if n > 0 else 0
                note = (f'已在谱末增加 {n} 小节，现共 {total} 小节。'
                        if total else '增加小节失败：当前没有乐谱。')
                self._chat_append("助手", note, skills=used or None)
                self.statusBar().showMessage(note)
                return
            if isinstance(msg, str) and msg.startswith('__ADDPART__:'):
                tok = msg.split(':', 1)[1].strip() or 'bass'
                ok = self._add_part(tok)
                note = (f'已添加声部（{tok}），现共 {len(self.score.staves)} 声部。'
                        if ok and self.score else f'添加声部失败（{tok}）。')
                self._chat_append("助手", note, skills=used or None)
                self.statusBar().showMessage(note)
                return
            if isinstance(msg, str) and msg.startswith('__ACCOMP__:'):
                pat = msg.split(':', 1)[1].strip() or 'bass'
                ok = self._fill_accompaniment(pat)
                note = (f'已设置配乐（{pat}）。'
                        if ok else f'设置配乐失败（{pat}）。请先有旋律，或先 /addpart。')
                self._chat_append("助手", note, skills=used or None)
                self.statusBar().showMessage(note)
                return
            self._chat_append("助手", msg, skills=used or None)
            # /new or /clear emptied session score — clear staff too
            if self._compose_session is not None and not self._compose_session.score_spec:
                if isinstance(msg, str) and (
                    msg.startswith('已开始新会话') or msg.startswith('已清空对话')
                ):
                    self._clear_displayed_score()
                    if isinstance(msg, str) and msg.startswith('已开始新会话'):
                        self._enter_assist_compose()
            self._sync_assist_style_from_prefs()
            if isinstance(msg, str) and ('[Skill:' in msg and '未修改乐谱' in msg):
                self.statusBar().showMessage("已生成修改建议（未改谱）")
            else:
                self.statusBar().showMessage("命令已执行")
            return
        if kind == 'err':
            self._chat_append("助手", f"出错：{result[1]}")
            self.statusBar().showMessage("创曲失败")
            return
        _, m21_score, spec, note, paths = result
        try:
            is_en = i18n.language() == 'en'
            title = str(spec.get('title') or ('Chat composition' if is_en else '对话创曲'))
            bars = spec.get('bars', '?')
            tempo_n = spec.get('tempo', '?')
            self._apply_composed_score(m21_score, spec, note=note, paths=paths)
            import composition_skills as skill_lib
            skill_block = ''
            if used:
                skill_block = '\n' + skill_lib.format_skill_report(used)
            if is_en:
                self._chat_append(
                    "Assistant",
                    f"{note}{skill_block}\nLoaded \"{title}\" ({bars} measures, ♩={tempo_n}), "
                    f"synthesizing audio...\nFile: {paths.get('xml', LLM_MUSICXML_PATH)}",
                    skills=used or None,
                )
                self._chat_append_think()
                self.tabs.setCurrentIndex(0)
                label = skill_lib.format_skill_label(used) if used else title
                self.statusBar().showMessage(f"Generated: {title} ｜ Skill: {label}")
            else:
                self._chat_append(
                    "助手",
                    f"{note}{skill_block}\n已载入《{title}》（{bars} 小节，♩={tempo_n}），"
                    f"正在合成音频…\n文件：{paths.get('xml', LLM_MUSICXML_PATH)}",
                    skills=used or None,
                )
                self._chat_append_think()
                self.tabs.setCurrentIndex(0)
                label = skill_lib.format_skill_label(used) if used else title
                self.statusBar().showMessage(f"已生成：{title} ｜ Skill: {label}")
        except Exception as e:
            err_who = "Assistant" if is_en else "助手"
            err_prefix = "Score generated but failed to load: " if is_en else "乐谱已生成但载入失败："
            self._chat_append(err_who, f"{err_prefix}{e}")
            self.statusBar().showMessage("Load failed" if is_en else "载入失败")

    def reload_score_from_xml(self, path, rebuild_report=True):
        """以 MusicXML 为源刷新五线谱显示（不生成 PNG）。"""
        if not os.path.exists(path):
            raise FileNotFoundError(f"找不到 MusicXML: {path}")
        self.musicxml_path = path
        fallback = '怒海快板' if 'pirate' in os.path.basename(path).lower() else (
            '平均律 I · 前奏曲 C 大调' if 'bach' in os.path.basename(path).lower() else (
                '对话创曲' if ('llm' in os.path.basename(path).lower() or path.startswith(CHAT_DIR))
                else '晨光快板'))
        self.m21_score, self.score = load_score_from_musicxml(path, fallback)
        self.edit_controller.score = self.score
        self.renderer.set_violations([])
        self.renderer.render_score(self.score, SCORE_WIDTH)
        self.view.apply_highlight()
        n = len(self.score.staves[0].measures) if self.score.staves else 0
        key_name, _ = _key_info(self.score.key_signature.sharps)
        # A 小调与 C 大调同调号；海盗乐章按曲名显示为 A 小调
        if 'pirate' in os.path.basename(path).lower() or '怒海' in (self.score.title or ''):
            key_name = 'A 小调'
        base = os.path.basename(path).lower()
        if 'bwv846' in base:
            self.report_path = BACH846_REPORT
        elif 'pirate' in base:
            self.report_path = PIRATE_REPORT_PATH
        elif 'bach' in base:
            self.report_path = BACH_REPORT_PATH
        elif 'llm' in base or path.replace('\\', '/').startswith(CHAT_DIR.replace('\\', '/')):
            self.report_path = (
                self._chat_paths(self._chat_id)['report'] if self._chat_id else LLM_REPORT_PATH
            )
        else:
            self.report_path = REPORT_PATH
        self.info_label.setText(
            f"♪ {self.score.title} ｜ {key_name} ｜ "
            f"{self.score.time_signature.numerator}/{self.score.time_signature.denominator} ｜ "
            f"♩={self.score.tempo} ｜ {n} 小节 ｜ 源: {os.path.basename(path)}"
        )
        self.setWindowTitle(f"乐谱工作室 - {self.score.title}")
        self._rebuild_mixer()
        self._update_lcd(0.0)
        self._update_daw_views()
        if rebuild_report:
            try:
                export_analysis_report(
                    self.score, self.report_path,
                    compose_trace=_compose_trace_of(self))
            except Exception as e:
                QMessageBox.warning(self, "报告生成失败", str(e))
            self.load_report_into_ui()
        self.statusBar().showMessage(f"五线谱已从 MusicXML 加载: {path}")

    def load_report_into_ui(self):
        path = getattr(self, 'report_path', REPORT_PATH)
        if not os.path.exists(path):
            self.report_view.setPlainText("（尚无分析报告）")
        else:
            with open(path, encoding='utf-8') as f:
                self.report_view.setHtml(
                    render_markdown_to_html(f.read()))
        # 程序员版：路径由分析报告路径派生（唯一推导入口 _dev_report_path）
        dev_path = _dev_report_path(path)
        dev_view = getattr(self, 'report_dev_view', None)
        if dev_view is None:
            return
        if not dev_path or not os.path.exists(dev_path):
            dev_view.setPlainText(
                "（尚无程序员报告）\n\n"
                "这份报告记录本次生成的完整生产链路（脚本调用、LLM 输入输出、\n"
                "规则触发明细），用于核对「说得 vs 写得」与缺陷定位。\n"
                "Chat 会话新生成的谱都会带；若为内置示例或较早的会话，\n"
                "在「分析师报告」页点一次刷新即可重新生成。")
            return
        with open(dev_path, encoding='utf-8') as f:
            dev_view.setHtml(render_markdown_to_html(f.read()))

    @staticmethod
    def _markdown_to_html(md: str) -> str:
        import html as _html
        lines = []
        in_table = False
        in_fence = False
        for raw in md.splitlines():
            line = raw.rstrip()
            # 围栏代码块（``` / ````）：内部内容原样转义输出，
            # 否则 LLM 输出里的 ``` 会被当成标题/表格错误渲染
            if not in_table and line.lstrip().startswith('```'):
                if in_fence:
                    lines.append('</code></pre>')
                    in_fence = False
                else:
                    lines.append('<pre><code>')
                    in_fence = True
                continue
            if in_fence:
                lines.append(_html.escape(line))
                continue
            if line.startswith('|') and '|' in line[1:]:
                cells = [c.strip() for c in line.strip('|').split('|')]
                if all(set(c) <= set('-: ') and c for c in cells):
                    continue
                tag = 'th' if not in_table else 'td'
                if not in_table:
                    lines.append(
                        '<table border="1" cellpadding="4" cellspacing="0" '
                        'style="border-collapse:collapse;margin:8px 0;">'
                    )
                    in_table = True
                row = ''.join(f'<{tag}>{c}</{tag}>' for c in cells)
                lines.append(f'<tr>{row}</tr>')
                continue
            if in_table:
                lines.append('</table>')
                in_table = False
            if line.startswith('# '):
                lines.append(f'<h1>{line[2:]}</h1>')
            elif line.startswith('## '):
                lines.append(f'<h2>{line[3:]}</h2>')
            elif line.startswith('### '):
                lines.append(f'<h3>{line[4:]}</h3>')
            elif line.startswith('#### '):
                lines.append(f'<h4>{line[5:]}</h4>')
            elif line.startswith('> '):
                lines.append(f'<blockquote>{line[2:]}</blockquote>')
            elif line.startswith('- '):
                lines.append(f'<li>{line[2:]}</li>')
            elif line.strip() == '':
                lines.append('<br/>')
            else:
                s = line
                while '**' in s:
                    s = s.replace('**', '<b>', 1)
                    if '**' in s:
                        s = s.replace('**', '</b>', 1)
                    else:
                        break
                lines.append(f'<p>{s}</p>')
        if in_table:
            lines.append('</table>')
        if in_fence:
            lines.append('</code></pre>')
        return (
            '<html><head><meta charset="utf-8"></head><body '
            'style="font-family:Microsoft YaHei,Segoe UI,sans-serif;font-size:13px;">'
            + '\n'.join(lines) + '</body></html>'
        )

    def show_help_dialog(self, topic=None, title='帮助文档'):
        """弹出一个独立帮助对话框，渲染 help_doc.build_help(topic) 的 markdown。"""
        try:
            import help_doc
            md = help_doc.build_help(topic)
            sub = f"（{topic}）" if topic else "（完整目录 · 每条命令/功能都带示例）"
            dialog_title = f'{title}{sub}'
        except Exception as e:  # 极端情况下仍给出可读提示
            md = f'# 帮助\n\n帮助模块加载失败：{e}'
            dialog_title = title
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton
        dlg = QDialog(self)
        dlg.setWindowTitle(dialog_title)
        dlg.resize(820, 620)
        layout = QVBoxLayout(dlg)
        browser = QTextBrowser(dlg)
        browser.setOpenExternalLinks(True)
        browser.setHtml(render_markdown_to_html(md, title=dialog_title))
        layout.addWidget(browser)
        close_btn = QPushButton('关闭', dlg)
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        dlg.exec_()

    def open_help_doc_external(self, rel_path='runs/help_pro.md'):
        """用系统默认应用打开本地帮助文档（专业版 / 速查版）。

        markdown 文档会被即时转换为同目录同名 .html 再打开，
        避免被当成纯文本显示原始 markdown。
        """
        from PyQt5.QtGui import QDesktopServices
        from PyQt5.QtCore import QUrl
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), rel_path)
        if not os.path.exists(path):
            QMessageBox.information(
                self, '文档未生成',
                f'未找到 {rel_path}。请先运行：python gen_help_pro.py，'
                f'或改用菜单「帮助文档」直接在应用内查看。')
            return
        # markdown 源文件 -> 转成 HTML 打开，确保真正渲染
        if path.lower().endswith('.md'):
            html_path = _ensure_html_for_md(path)
            if html_path:
                path = html_path
        url = QUrl.fromLocalFile(path)
        try:
            opened = QDesktopServices.openUrl(url)
        except Exception as e:  # 极少数环境无默认处理器或沙箱限制
            opened = False
            err = f'\n（错误：{e}）'
        else:
            err = ''
        if not opened:
            QMessageBox.information(
                self, '无法打开',
                f'系统未能用默认程序打开该文档：\n{path}{err}\n\n'
                f'可手动用文本编辑器打开，或改用菜单「帮助文档」在应用内查看。')

    def _show_help_topic_prompt(self):
        """让用户输入命令名或功能名，弹出该单项说明（复用 help_doc.find_topic）。"""
        from PyQt5.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, '查看单项说明',
            '输入命令或功能名（如 /chords、和弦顾问、风格插值）：')
        if not ok or not text.strip():
            return
        topic = text.strip()
        try:
            import help_doc
            if help_doc.find_topic(topic) is None:
                QMessageBox.information(
                    self, '未找到',
                    f'未找到与「{topic}」匹配的命令或功能。\n'
                    f'输入 /help 可查看完整目录。')
                return
        except Exception:
            pass
        self.show_help_dialog(topic, '单项说明')

    def refresh_report_panel(self):
        try:
            path = getattr(self, 'report_path', REPORT_PATH)
            export_analysis_report(self.score, path,
                                   compose_trace=_compose_trace_of(self))
            self.load_report_into_ui()
            self.tabs.setCurrentWidget(self.report_view)
            self.statusBar().showMessage(f"报告已刷新: {path}")
        except Exception as e:
            QMessageBox.critical(self, "刷新失败", str(e))

    def _on_viz_combo(self, index):
        # 0 = 不显示；其余对应 viz_names
        if index <= 0:
            self.tabs.setCurrentIndex(0)
            return
        name_index = index - 1
        if 0 <= name_index < len(self.viz_names):
            name = self.viz_names[name_index]
            self.tabs.setCurrentWidget(self.viz_canvases[name])
            if self.audio is not None:
                self.draw_visualization(name)

    def _on_tab_changed(self, index):
        """仅当用户主动切到可视化标签时再绘制。"""
        w = self.tabs.widget(index)
        for name, canvas in self.viz_canvases.items():
            if w is canvas:
                if self.audio is not None:
                    self.draw_visualization(name)
                # 同步下拉框（+1 因为第 0 项是「不显示」）
                try:
                    self.viz_combo.blockSignals(True)
                    self.viz_combo.setCurrentIndex(self.viz_names.index(name) + 1)
                finally:
                    self.viz_combo.blockSignals(False)
                break

    def _synth_worker(self):
        # 入口快照：合成进行中切对话时，禁止旧谱写入新会话路径
        m21 = self.m21_score
        chat_id = self._chat_id
        xml = getattr(self, 'musicxml_path', MUSICXML_PATH) or MUSICXML_PATH
        session = self._compose_session
        score_obj = self.score
        if m21 is None:
            return
        base = os.path.basename(xml).lower()
        xml_norm = xml.replace('\\', '/')
        if 'pirate' in base:
            mid_path, wav_path = PIRATE_MIDI_PATH, PIRATE_WAV_PATH
        elif 'bach' in base:
            mid_path, wav_path = BACH_MIDI_PATH, BACH_WAV_PATH
        elif 'llm' in base or '/chats/' in xml_norm or '\\chats\\' in xml:
            if chat_id:
                paths = self._chat_paths(chat_id)
                mid_path, wav_path = paths['mid'], paths['wav']
            else:
                mid_path, wav_path = LLM_MIDI_PATH, LLM_WAV_PATH
        else:
            mid_path, wav_path = MIDI_PATH, WAV_PATH
        try:
            with _RENDER_LOCK:
                groove = None
                if session is not None:
                    groove = (session.prefs or {}).get('groove')
                export_midi(m21, mid_path, groove=groove)
        except Exception as e:
            self.audio_ready.emit(e)
            return

        audio = render_midi_to_audio(mid_path, SOUNDFONT, SAMPLE_RATE)
        engine = 'MIDI→FluidSynth'
        if audio is None:
            try:
                audio = synthesize_software(score_obj, SAMPLE_RATE)
                engine = '软件合成(回退)'
            except Exception as e:
                self.audio_ready.emit(e)
                return
        try:
            export_wav(audio, SAMPLE_RATE, wav_path)
        except Exception as e:
            # 音频已合成但落盘失败：仍把波形交给 UI，并带上诊断信息
            engine = f'{engine}（WAV 未写出: {e}）'
        self.audio_ready.emit((engine, audio, SAMPLE_RATE))

    def _set_audio(self, audio, sample_rate, source_name, path=None, cache_as_score=False):
        sd.stop()
        self.playing = False
        self._play_timer.stop()
        self._play_offset = 0.0
        self._paused_at = 0.0
        self.play_btn.setText("▶")
        self._update_lcd(0.0)
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        self.audio = audio
        self.sample_rate = int(sample_rate)
        self.synth_engine = source_name
        self.audio_path = path
        self._viz_cache = {}
        self.play_btn.setEnabled(True)
        self.export_wav_btn.setEnabled(True)
        if cache_as_score:
            self._score_audio = audio
            self._score_sr = self.sample_rate
            self._score_engine = source_name
            try:
                export_wav(audio, self.sample_rate, WAV_PATH)
            except Exception:
                pass
        # 「恢复乐谱音频」：乐谱音频已缓存、且当前加载的不是它本身时才可用
        self.restore_btn.setEnabled(self._score_audio is not None
                                    and not cache_as_score)
        dur = len(audio) / float(self.sample_rate)
        # 不自动打开可视化；用户从下拉框或标签页选择时再绘制
        if path:
            self.statusBar().showMessage(
                f"已打开音频: {path}（{dur:.1f} 秒，{self.sample_rate} Hz）"
            )
        else:
            self.statusBar().showMessage(
                f"MIDI 播放就绪（{source_name}，{dur:.1f}s）｜"
                f"需要时可在下拉框选择 Mel/波形/色度等"
            )

    def _on_audio_ready(self, result):
        if isinstance(result, Exception):
            self._pending_play = False
            # 合成失败时仍允许重试：有缓存音频或谱面有发声音符则保持 ▶ 可用
            has_sound = False
            try:
                has_sound = any(
                    (not getattr(n, 'is_rest', False))
                    for st in (self.score.staves if self.score else [])
                    for m in st.measures
                    for n in (m.notes or [])
                )
            except Exception:
                pass
            self.play_btn.setEnabled(self.audio is not None or has_sound)
            QMessageBox.warning(self, "音频合成失败", str(result))
            self.statusBar().showMessage("音频合成失败（可再点 ▶ 重试）")
            return
        if len(result) == 3:
            engine_name, audio, sr = result
        else:
            engine_name, audio = result
            sr = SAMPLE_RATE
        self._set_audio(audio, sr, engine_name, path=None, cache_as_score=True)
        if getattr(self, '_pending_play', False):
            self._pending_play = False
            self.play()

    def open_score(self, path: str = ''):
        """打开乐谱；传入 path 直接打开，否则弹文件对话框。"""
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "打开乐谱", "scores",
                "乐谱文件 (*.xml *.musicxml *.mxl *.mid *.midi);;"
                "MusicXML (*.xml *.musicxml *.mxl);;MIDI (*.mid *.midi);;所有文件 (*)"
            )
            if not path:
                return
        try:
            parsed = m21converter.parse(path)
            if not isinstance(parsed, stream.Score):
                score = stream.Score()
                score.insert(0, parsed)
                parsed = score
            self.m21_score = parsed
            write_score_bundle(self.m21_score)
            ext = os.path.splitext(path)[1].lower()
            xml_src = path if ext in ('.xml', '.musicxml', '.mxl') else MUSICXML_PATH
            self.reload_score_from_xml(xml_src, rebuild_report=True)
            self.play_btn.setEnabled(False)
            self.export_wav_btn.setEnabled(False)
            self.statusBar().showMessage(f"已打开乐谱: {path} — MIDI 合成中...")
            threading.Thread(target=self._synth_worker, daemon=True).start()
        except Exception as e:
            QMessageBox.critical(self, "打开乐谱失败", str(e))

    def export_musicxml_dialog(self):
        if self.m21_score is None:
            QMessageBox.information(self, "暂无乐谱", "当前没有可导出的乐谱")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 MusicXML", MUSICXML_PATH,
            "MusicXML (*.xml *.musicxml);;所有文件 (*)"
        )
        if not path:
            return
        try:
            export_musicxml(self.m21_score, path)
            QMessageBox.information(self, "导出成功", f"可编辑五线谱已写入:\n{os.path.abspath(path)}")
            self.statusBar().showMessage(f"已导出 MusicXML: {path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def export_midi_dialog(self):
        if self.m21_score is None:
            QMessageBox.information(self, "暂无乐谱", "当前没有可导出的乐谱")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 MIDI", MIDI_PATH,
            "MIDI (*.mid *.midi);;所有文件 (*)"
        )
        if not path:
            return
        try:
            groove = (self._compose_session.prefs.get('groove')
                      if self._compose_session else None)
            export_midi(self.m21_score, path, groove=groove)
            QMessageBox.information(self, "导出成功", f"MIDI 已写入:\n{os.path.abspath(path)}")
            self.statusBar().showMessage(f"已导出 MIDI: {path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def export_wav_dialog(self):
        if self.audio is None:
            QMessageBox.information(self, "请稍候", "音频尚未就绪，请等待 MIDI 合成完成")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 WAV", WAV_PATH,
            "WAV (*.wav);;所有文件 (*)"
        )
        if not path:
            return
        try:
            export_wav(self.audio, self.sample_rate, path)
            QMessageBox.information(self, "导出成功", f"WAV 已写入:\n{os.path.abspath(path)}")
            self.statusBar().showMessage(f"已导出 WAV: {path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def export_lilypond_dialog(self):
        """导出 LilyPond 源码（有 lilypond 二进制时顺带出 PDF）。"""
        spec = getattr(self, '_last_spec', None) or {}
        sc = self.m21_score
        if sc is None:
            QMessageBox.information(self, "暂无乐谱", "当前没有可导出的乐谱")
            return
        import re as _re
        _slug = _re.sub(r'[^\w一-鿿]+', '-', str(spec.get('title') or 'score')).strip('-') or 'score'
        default = os.path.join('scores', f"{_slug}.ly")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 LilyPond", default, "LilyPond (*.ly);;所有文件 (*)")
        if not path:
            return
        try:
            import deliver
            r = deliver.export_lilypond(
                sc, path, title=str(spec.get('title') or ''),
                composer=str(spec.get('composer') or ''),
                sharps=int(spec.get('key_sharps') or 0),
                mode=str(spec.get('mode') or 'major'),
                ts=str(spec.get('time_signature') or '4/4'),
                tempo=int(spec.get('tempo') or 90), spec=spec)
            msg = f"LilyPond 源码已写入:\n{os.path.abspath(path)}"
            if r.get('pdf'):
                msg += f"\n\nPDF: {os.path.abspath(r['pdf'])}"
            elif r.get('msg'):
                msg += f"\n\n{r['msg']}"
            QMessageBox.information(self, "导出成功", msg)
            self.statusBar().showMessage(f"已导出 LilyPond: {path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def archive_run_dialog(self):
        """把当前这一版（spec + XML + MIDI + ly + 报告 + 三指标）归档到 runs/。"""
        spec = getattr(self, '_last_spec', None) or {}
        sc = self.m21_score
        if sc is None or not spec:
            QMessageBox.information(self, "暂无乐谱", "当前没有可归档的生成结果")
            return
        try:
            import deliver
            report = ''
            try:
                with open(self.report_path, encoding='utf-8') as f:
                    report = f.read()
            except Exception:
                pass
            wav = WAV_PATH if os.path.exists(WAV_PATH) else ''
            m = deliver.archive_run(spec, sc, report_md=report, wav_path=wav)
            QMessageBox.information(
                self, "已归档",
                f"归档目录:\n{m['dir']}\n\n产物: "
                + ', '.join(os.path.basename(v) for k, v in m['files'].items()
                            if k != 'lilypond_note'))
            self.statusBar().showMessage(f"已归档: {m['dir']}")
        except Exception as e:
            QMessageBox.critical(self, "归档失败", str(e))

    def open_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开音频文件", "scores",
            "音频文件 (*.wav *.wave *.flac *.ogg *.aiff *.aif *.mp3);;"
            "WAV (*.wav *.wave);;所有文件 (*)"
        )
        if not path:
            return
        try:
            audio, sr = load_audio_file(path)
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))
            return
        if audio.size == 0:
            QMessageBox.warning(self, "打开失败", "文件中没有音频数据")
            return
        self._set_audio(audio, sr, '导入文件', path=path, cache_as_score=False)

    def restore_score_audio(self):
        if self._score_audio is None:
            QMessageBox.information(self, "提示", "乐谱音频尚未合成完成")
            return
        self._set_audio(
            self._score_audio, self._score_sr,
            self._score_engine or '乐谱音频',
            path=None, cache_as_score=False,
        )

    def _build_playback_timeline(self):
        """Build (t0, t1, staff, measure, note) events in seconds from the Score model."""
        events = []
        try:
            bpm = max(int(self.score.tempo), 1)
            spb = 60.0 / bpm
            bar_beats = float(self.score.time_signature.beats_per_measure())
        except Exception:
            return events
        for si, staff in enumerate(self.score.staves or []):
            t = 0.0
            for mi, measure in enumerate(staff.measures or []):
                if not measure.notes:
                    t += bar_beats * spb
                    continue
                for ni, note in enumerate(measure.notes):
                    # duration_value is in whole-note units; *4 => quarter-note beats
                    dur = max(note.duration_value() * 4.0 * spb, 0.01)
                    events.append((t, t + dur, si, mi, ni))
                    t += dur
        return events

    def _scroll_playback_to_measure(self, measure_idx: int):
        """Keep the playing measure centered in the score view."""
        if measure_idx == self._playback_scroll_measure:
            return
        rects = [
            h['rect'] for h in (self.renderer.hit_measures or [])
            if h.get('measure_idx') == measure_idx
        ]
        if not rects:
            return
        union = rects[0]
        for r in rects[1:]:
            union = union.united(r)
        self.view.centerOn(union.center())
        self._playback_scroll_measure = measure_idx

    def _on_play_tick(self):
        if not self.playing:
            self._play_timer.stop()
            self.renderer.clear_playback()
            self._playback_scroll_measure = None
            return
        t = self._play_offset + self._play_elapsed.elapsed() / 1000.0
        audio_dur = 0.0
        if self.audio is not None:
            audio_dur = len(self.audio) / float(self.sample_rate or SAMPLE_RATE)
        # 循环：到出点（或结尾）就跳回入点
        end = float(self._loop_out) if (self._loop and self._loop_out) else audio_dur
        if audio_dur > 0 and t >= end - 0.01:
            if self._loop:
                self._play_from(float(self._loop_in))
                return
            self._finish_playback()
            return
        self._update_lcd(t)
        # 播放头同步（钢琴卷帘 / 编排视图）
        if self.workspace.currentIndex() != 0:
            self.piano_roll.set_playhead(t)
            self.arrangement.set_playhead(t)
        active = [
            (si, mi, ni) for t0, t1, si, mi, ni in self._playback_events
            if t0 <= t < t1
        ]
        self.renderer.set_playback_active(active)
        if active and self._follow:
            # Prefer the earliest measure among currently sounding symbols
            measure_idx = min(mi for _, mi, _ in active)
            self._scroll_playback_to_measure(measure_idx)

    def play(self):
        if self.audio is None:
            # 编辑后尚未合成完 / 空谱刚写入：点播放即触发合成并排队开播
            has_sound = False
            try:
                has_sound = any(
                    (not getattr(n, 'is_rest', False))
                    for st in (self.score.staves if self.score else [])
                    for m in st.measures
                    for n in (m.notes or [])
                )
            except Exception:
                has_sound = False
            if self.m21_score is None and self.score is not None and has_sound:
                try:
                    self.m21_score = model_to_m21(
                        self.score, title=self.score.title,
                        composer=self.score.composer)
                except Exception as e:
                    self.statusBar().showMessage(f'无法从乐谱生成音频：{e}')
                    return
            if self.m21_score is not None and has_sound:
                self._pending_play = True
                self.play_btn.setEnabled(False)
                self.statusBar().showMessage('正在合成音频，合成后自动播放…')
                threading.Thread(target=self._synth_worker, daemon=True).start()
            else:
                self.statusBar().showMessage('尚无可播放内容（请先写入音符）')
            return
        sr = self.sample_rate or SAMPLE_RATE
        audio_dur = len(self.audio) / float(sr)
        if self.playing is True:
            # 播放中 → 暂停：sd.pause/resume 在 macOS 上不可靠，
            # 改为记录精确位置、截取剩余音频，恢复时从该位置续播。
            t = self._play_offset + self._play_elapsed.elapsed() / 1000.0
            sd.stop()
            self._play_timer.stop()
            if t < audio_dur - 0.01:
                self._paused_at = t
                self.playing = 'paused'
                self.play_btn.setText("▶")
                self.statusBar().showMessage(f"已暂停于 {self.lcd_label.text()}")
            else:
                self._finish_playback()
            return
        if self.playing == 'paused' and not getattr(self, '_play_from_selection', False):
            # 暂停中 → 继续：从暂停位置续播剩余音频
            self._play_from(float(getattr(self, '_paused_at', 0.0) or 0.0))
            self.statusBar().showMessage("播放中…")
            return
        # 从选中的小节 / 循环入点 / 开头播放
        sd.stop()
        self.renderer.clear_playback()
        self._playback_scroll_measure = None
        self._paused_at = 0.0
        self._play_from_selection = False
        self._playback_events = self._build_playback_timeline()
        sel_mi = self._selection_start_measure()
        if sel_mi is not None:
            start = min(self._measure_start_time(sel_mi), max(audio_dur - 0.05, 0.0))
        else:
            start = float(self._loop_in) if self._loop else 0.0
        self._play_from(start)
        if self._follow:
            first = sel_mi if sel_mi is not None else (
                self._playback_events[0][3] if self._playback_events else None)
            if first is not None:
                self._scroll_playback_to_measure(first)
        src = os.path.basename(self.audio_path) if self.audio_path else (self.synth_engine or '乐谱')
        tail = "（循环）" if self._loop else ""
        head = ""
        if sel_mi is not None:
            ms0 = self.score.staves[0].measures if self.score and self.score.staves else []
            pickup = bool(ms0) and getattr(ms0[0], 'implicit', False)
            head = f"从第 {sel_mi if pickup else sel_mi + 1} 小节"
        self.statusBar().showMessage(f"{head}播放中…（{src}）{tail}")

    def _selection_start_measure(self) -> Optional[int]:
        """选中的小节（或选中音符所在小节）里最靠前的一个；无选区返回 None。"""
        view = getattr(self, 'view', None)
        if view is None:
            return None
        ms = set(getattr(view, 'selected_measures', None) or ())
        ms |= {mi for _si, mi, _ni in (getattr(view, 'selected_symbols', None) or ())}
        return min(ms) if ms else None

    def _measure_start_time(self, measure_idx: int) -> float:
        """第 measure_idx 小节（0 起）起点的秒数，与播放高亮用同一时间线。"""
        ts = [t0 for t0, _t1, _si, mi, _ni in (self._playback_events or [])
              if mi == measure_idx]
        if ts:
            return min(ts)
        try:
            spb = 60.0 / max(int(self.score.tempo), 1)
            bar = float(self.score.time_signature.beats_per_measure()) * spb
            t = 0.0
            for m in self.score.staves[0].measures[:measure_idx]:
                notes = m.notes or []
                t += sum(max(n.duration_value() * 4.0 * spb, 0.01) for n in notes) if notes else bar
            return t
        except Exception:
            return 0.0

    def stop(self):
        sd.stop()
        self.playing = False
        self._play_timer.stop()
        self._play_offset = 0.0
        self._paused_at = 0.0
        self.renderer.clear_playback()
        self._playback_scroll_measure = None
        self.play_btn.setText("▶")
        self._update_lcd(0.0)
        self.statusBar().showMessage("已停止")

    def refresh_all_visualizations(self, title=None):
        if self.audio is None:
            return
        for name in self.viz_names:
            self.draw_visualization(name, title=title)

    def _viz_bar_seconds(self):
        """一小节对应的秒数，用于画小节网格。"""
        try:
            bpm = max(int(self.score.tempo), 1)
            beats = float(self.score.time_signature.beats_per_measure())
            return beats * 60.0 / bpm
        except Exception:
            return None

    def _draw_bar_grid(self, ax, dur):
        bar = self._viz_bar_seconds()
        if not bar or bar <= 0 or dur / bar > 120:
            return
        for t in np.arange(0, dur + 1e-9, bar):
            ax.axvline(t, color='#ffffff', alpha=0.18, linewidth=0.6, zorder=1)

    @staticmethod
    def _viz_caption(ax, text):
        ax.text(
            0.5, -0.16, text, transform=ax.transAxes,
            ha='center', va='top', fontsize=9, color='#9aa0a8',
            wrap=True,
        )

    @staticmethod
    def _darken_fig(fig):
        """把整张图切换到深色主题：坐标区底色、刻度/文字/边框浅色。"""
        fig.patch.set_facecolor(_VIZ_FIG_BG)
        for ax in fig.axes:
            ax.set_facecolor(_VIZ_AX_BG)
            ax.tick_params(colors=_VIZ_FG, labelcolor=_VIZ_FG)
            for spine in ax.spines.values():
                spine.set_color('#3a3d45')
            ax.xaxis.label.set_color(_VIZ_FG)
            ax.yaxis.label.set_color(_VIZ_FG)
            ax.title.set_color(_VIZ_FG)
            leg = ax.get_legend()
            if leg is not None:
                leg.get_frame().set_facecolor(_VIZ_AX_BG)
                for txt in leg.get_texts():
                    txt.set_color(_VIZ_FG)

    def draw_visualization(self, name, title=None):
        """根据当前内存中的音频现算图形，不写 PNG。"""
        if self.audio is None:
            return
        sr = self.sample_rate
        audio = np.asarray(self.audio, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        dur = len(audio) / float(sr)
        if title is None:
            title = os.path.basename(self.audio_path) if self.audio_path else self.score.title
        fig = self.viz_figures[name]
        canvas = self.viz_canvases[name]
        fig.clear()
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        rms_all = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0

        if name == "Mel 频谱":
            ax = fig.add_subplot(111)
            if "mel" not in self._viz_cache:
                self._viz_cache["mel"] = log_mel_spectrogram(audio, sr)
            M = self._viz_cache["mel"]
            # 时间轴过长时降采样，避免糊成一片
            step = max(1, M.shape[1] // 1800)
            Md = M[:, ::step]
            vmin, vmax = np.percentile(Md, [15, 99.5])
            fmax = min(8000.0, sr / 2.0)
            im = ax.imshow(
                Md, origin='lower', aspect='auto', cmap='magma',
                extent=[0, dur, 0, Md.shape[0]], vmin=vmin, vmax=vmax,
                interpolation='nearest',
            )
            ticks = np.linspace(0, Md.shape[0] - 1, 7)
            hz = _mel_to_hz(np.linspace(_hz_to_mel(30), _hz_to_mel(fmax), 7))
            ax.set_yticks(ticks)
            ax.set_yticklabels([f"{int(round(h))}" for h in hz])
            ax.set_ylabel("频率 (Hz)")
            self._draw_bar_grid(ax, dur)
            fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02, label='能量 (dB)')
            ax.set_title(f"Mel 频谱 — {title}（{dur:.0f}s）")
            self._viz_caption(
                ax,
                "读法：越亮=该频段越响。下方是低音，上方是高音；明暗变化反映音色与织体疏密。"
            )

        elif name == "波形":
            ax = fig.add_subplot(111)
            # 长音频用峰值包络，比抽点折线更能看出响度起伏
            n_bins = min(2400, max(400, len(audio) // 200))
            chunk = max(1, len(audio) // n_bins)
            usable = (len(audio) // chunk) * chunk
            blocks = audio[:usable].reshape(-1, chunk)
            env_hi = blocks.max(axis=1)
            env_lo = blocks.min(axis=1)
            t = (np.arange(blocks.shape[0]) + 0.5) * chunk / float(sr)
            lim = max(0.05, float(np.percentile(np.abs(audio), 99.5)) * 1.15)
            ax.fill_between(t, env_lo, env_hi, color='#3d7bd9', alpha=0.85, linewidth=0)
            ax.axhline(0, color='#5d626b', linewidth=0.6)
            ax.set_ylim(-lim, lim)
            ax.set_ylabel("振幅")
            ax.set_title(
                f"波形 — {title}（{dur:.0f}s｜峰值 {peak:.2f}｜RMS {rms_all:.2f}）"
            )
            self._draw_bar_grid(ax, dur)
            ax.grid(True, alpha=0.25)
            self._viz_caption(
                ax,
                "读法：包络上下张开=响；收窄=弱。尖峰多为和弦/重音，长静音段会贴近中线。"
            )

        elif name == "线性频谱":
            ax = fig.add_subplot(111)
            if "stft" not in self._viz_cache:
                self._viz_cache["stft"] = stft_spectrogram_db(audio, sr)
            S = self._viz_cache["stft"]
            # 只显示对人耳更有信息的 80Hz–4kHz，对比度按分位拉伸
            freqs = np.linspace(0, sr / 2.0, S.shape[0])
            f_lo, f_hi = 80.0, min(4000.0, sr / 2.0)
            i0 = int(np.searchsorted(freqs, f_lo))
            i1 = int(np.searchsorted(freqs, f_hi))
            Sd = S[i0:i1]
            step = max(1, Sd.shape[1] // 1800)
            Sd = Sd[:, ::step]
            vmin, vmax = np.percentile(Sd, [20, 99.5])
            im = ax.imshow(
                Sd, origin='lower', aspect='auto', cmap='viridis',
                extent=[0, dur, f_lo, f_hi], vmin=vmin, vmax=vmax,
                interpolation='nearest',
            )
            fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02, label='能量 (dB)')
            ax.set_ylabel("频率 (Hz)")
            ax.set_title(f"线性频谱 — {title}（显示 {f_lo:.0f}–{f_hi:.0f} Hz）")
            self._draw_bar_grid(ax, dur)
            self._viz_caption(
                ax,
                "读法：横条=持续音高，垂直纹理=敲击/起音。比 Mel 更适合看具体音高与泛音。"
            )

        elif name == "色度图":
            # 左侧色度热图 + 右侧整曲音级占比
            gs = fig.add_gridspec(1, 2, width_ratios=[4.2, 1.1], wspace=0.28)
            ax = fig.add_subplot(gs[0, 0])
            axb = fig.add_subplot(gs[0, 1])
            if "chroma" not in self._viz_cache:
                self._viz_cache["chroma"] = chromagram(audio, sr)
            C = self._viz_cache["chroma"]
            step = max(1, C.shape[1] // 1800)
            Cd = C[:, ::step]
            im = ax.imshow(
                Cd, origin='lower', aspect='auto', cmap='YlOrRd',
                extent=[0, dur, 0, 12], interpolation='nearest', vmin=0, vmax=max(0.15, float(Cd.max())),
            )
            ax.set_yticks(np.arange(12) + 0.5)
            ax.set_yticklabels(NOTE_PC_NAMES)
            ax.set_ylabel("音级")
            ax.set_title(f"色度图 — {title}")
            self._draw_bar_grid(ax, dur)
            # 主导音级轨迹
            dom = np.argmax(Cd, axis=0)
            t_dom = np.linspace(0, dur, num=len(dom), endpoint=False)
            ax.plot(t_dom, dom + 0.5, color='#7cc4ff', linewidth=0.7, alpha=0.55)

            avg = C.mean(axis=1)
            colors = ['#c0392b' if i == int(np.argmax(avg)) else '#f0c27f' for i in range(12)]
            axb.barh(np.arange(12) + 0.5, avg, color=colors, height=0.75)
            axb.set_yticks(np.arange(12) + 0.5)
            axb.set_yticklabels(NOTE_PC_NAMES)
            axb.set_xlabel("占比")
            axb.set_title("整曲")
            axb.set_xlim(0, max(avg) * 1.25 if avg.max() > 0 else 1)
            top3 = np.argsort(avg)[::-1][:3]
            top_txt = '、'.join(NOTE_PC_NAMES[i] for i in top3)
            self._viz_caption(
                ax,
                f"读法：某一行越红=该音级越常出现。蓝线=每时主导音。本曲最强音级：{top_txt}。"
            )

        elif name == "能量包络":
            ax = fig.add_subplot(111)
            if "rms" not in self._viz_cache:
                self._viz_cache["rms"] = rms_envelope(audio, sr)
            times, env = self._viz_cache["rms"]
            env = np.asarray(env, dtype=np.float64)
            # 平滑一点，便于看乐句呼吸
            win = max(3, int(0.25 * sr / 512))
            if win % 2 == 0:
                win += 1
            kernel = np.ones(win) / win
            smooth = np.convolve(env, kernel, mode='same')
            norm = smooth / (smooth.max() + 1e-12)
            ax.fill_between(times, norm, color='#f0c27f', alpha=0.45)
            ax.plot(times, norm, color='#ffb020', linewidth=1.4)
            # 标注局部峰值（乐句重音）
            min_gap = max(1, int(1.2 * sr / 512))
            peaks = []
            for i in range(1, len(norm) - 1):
                if norm[i] >= norm[i - 1] and norm[i] >= norm[i + 1] and norm[i] > 0.55:
                    if not peaks or i - peaks[-1] >= min_gap:
                        peaks.append(i)
            for i in peaks[:24]:
                ax.plot(times[i], norm[i], 'o', color='#ff6b35', markersize=3.5)
            ax.set_ylim(0, 1.05)
            ax.set_ylabel("相对响度")
            ax.set_title(
                f"能量包络 — {title}（已归一化｜标出 {min(len(peaks), 24)} 个重音峰）"
            )
            self._draw_bar_grid(ax, dur)
            ax.grid(True, alpha=0.25)
            self._viz_caption(
                ax,
                "读法：曲线升高=渐强/织体变密；下降=减弱或休止。圆点是局部重音，对应乐句起伏。"
            )

        else:
            ax = fig.add_subplot(111)

        ax.set_xlabel("时间 (秒)")
        self._darken_fig(fig)
        fig.subplots_adjust(left=0.08, right=0.96, top=0.88, bottom=0.22)
        canvas.draw_idle()

    def show_dev_report(self):
        """切到「程序员报告」标签页；缺失时先按当前谱面重新导出一份。"""
        try:
            path = getattr(self, 'report_path', REPORT_PATH)
            dev = _dev_report_path(path)
            if not os.path.exists(dev) and getattr(self, 'score', None):
                export_analysis_report(
                    self.score, path,
                    compose_trace=_compose_trace_of(self))
            self.load_report_into_ui()
            view = getattr(self, 'report_dev_view', None)
            if view is not None:
                self.tabs.setCurrentWidget(view)
                self.statusBar().showMessage(
                    f"程序员报告: {dev}" if os.path.exists(dev)
                    else "程序员报告缺失，已尝试重新导出")
        except Exception as e:
            QMessageBox.critical(self, "程序员报告", str(e))

    def export_report(self):
        try:
            path = getattr(self, 'report_path', REPORT_PATH)
            _text, (p, w, f) = export_analysis_report(
                self.score, path, compose_trace=_compose_trace_of(self))
            self.load_report_into_ui()
            self.tabs.setCurrentWidget(self.report_view)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
            return
        _dev = _dev_report_path(path)
        QMessageBox.information(
            self, "已保存",
            f"分析报告已写入并显示:\n{os.path.abspath(path)}\n"
            f"程序员报告同时写入:\n{os.path.abspath(_dev)}\n\n"
            f"✅ {p} 项通过，⚠️ {w} 项提示，❌ {f} 项问题")
        self.statusBar().showMessage(f"分析报告已导出: {path}")

    def closeEvent(self, event):
        try:
            self._persist_current_chat()
        except Exception:
            pass
        for name in ('_midi_timer', '_play_timer', '_mixer_timer',
                     '_chat_persist_timer', '_edit_sync_timer'):
            try:
                t = getattr(self, name, None)
                if t is not None:
                    t.stop()
            except Exception:
                pass
        try:
            if getattr(self, '_midi_service', None) is not None:
                self._midi_service.close()
        except Exception:
            pass
        sd.stop()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(os.path.join(_ROOT, "assets", "icon.svg")))
    i18n.set_language(i18n.initial_language(), persist=False)
    app._retranslator = i18n.Retranslator(app)
    window = StudioWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
