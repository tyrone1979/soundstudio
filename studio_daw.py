# -*- coding: utf-8 -*-
"""Studio 的 DAW / 编排面板。

* InspectorPanel 右侧检查器：风格基调参数 + 生成控制（骨架/全曲/赋格/声部）
* PianoRollView  钢琴卷帘：按小节/拍网格显示音符，播放头跟随，点击定位
* ArrangementView 编排（MuseScore 布局）：左声部栏 + 右时间线
* TransportExtras 走带增强控件：循环、入/出点、节拍器、跟随播放头、吸附

风格基调只列「学过」的包（有 grammar.yaml），未学过的包不进 UI。
只负责 UI；生成逻辑一律走 style_skeleton / llm_compose 的确定性内核。
"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt, pyqtSignal, QRectF, QPointF
from PyQt5.QtGui import QColor, QPen, QBrush, QFont, QPainter
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QComboBox, QSpinBox, QLineEdit, QTextEdit, QFrame,
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsRectItem,
    QSplitter, QCheckBox, QButtonGroup, QAbstractItemView, QSizePolicy,
    QScrollArea, QFormLayout, QGroupBox,
)

TRACK_COLORS = ['#ff8c1a', '#4ea1ff', '#7bd88f', '#d98cff', '#ffd166',
                '#ff6b6b', '#5ad2d2', '#b0b7c3']

_PITCH_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def pitch_label(midi: int) -> str:
    return f'{_PITCH_NAMES[midi % 12]}{midi // 12 - 1}'


def _is_black(midi: int) -> bool:
    return (midi % 12) in (1, 3, 6, 8, 10)


# ============================================================
# 右侧检查器（Logic Pro Inspector 风格）
# ============================================================

class InspectorPanel(QWidget):
    """风格基调 + 生成控制 + 选中信息。"""

    generateSkeleton = pyqtSignal(dict)   # {'genre_id','bars','voices','fugue','seed','brief'}
    generateFull = pyqtSignal(dict)
    regenerate = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(8)

        # ---- 基调 ----
        g1 = QGroupBox("基调（style）")
        f1 = QFormLayout(g1)
        f1.setLabelAlignment(Qt.AlignRight)
        self.style_combo = QComboBox()
        self.style_combo.setToolTip(
            "只列出已学过的风格包（有 grammar.yaml）："
            "决定拍号/速度/和声/织体/音区")
        self.style_combo.currentIndexChanged.connect(self._on_style_changed)
        f1.addRow("风格包", self.style_combo)
        self.style_info = QLabel("（未选择）")
        self.style_info.setWordWrap(True)
        self.style_info.setObjectName("panelHint")
        f1.addRow("", self.style_info)
        v.addWidget(g1)

        # ---- 生成 ----
        g2 = QGroupBox("生成")
        f2 = QFormLayout(g2)
        f2.setLabelAlignment(Qt.AlignRight)
        self.bars_spin = QSpinBox()
        self.bars_spin.setRange(8, 32)
        self.bars_spin.setValue(16)
        f2.addRow("小节", self.bars_spin)
        self.voices_combo = QComboBox()
        self.voices_combo.addItems(["自动（对位风格=3）", "2 声部", "3 声部", "4 声部"])
        self.voices_combo.setToolTip("new_score 的 voices：2=创意曲，3/4=赋格")
        f2.addRow("声部", self.voices_combo)
        self.fugue_combo = QComboBox()
        self.fugue_combo.addItems(["auto：必含赋格段", "full：整首赋格", "off：不要赋格"])
        self.fugue_combo.setToolTip("赋格由内核确定性生成：主题→答题→对题→间插→密接→终止")
        f2.addRow("赋格", self.fugue_combo)
        self.seed_edit = QLineEdit()
        self.seed_edit.setPlaceholderText("留空=每次不同；填了可逐音符复现")
        f2.addRow("种子", self.seed_edit)
        self.brief_edit = QTextEdit()
        self.brief_edit.setPlaceholderText("提示词变奏：只改你提到的维度（情绪/速度/结构…）")
        self.brief_edit.setFixedHeight(70)
        f2.addRow("变奏", self.brief_edit)

        row = QHBoxLayout()
        self.skel_btn = QPushButton("生成骨架（确定性）")
        self.skel_btn.setToolTip("不调 LLM：按风格包直接生成乐谱骨架")
        self.skel_btn.clicked.connect(lambda: self.generateSkeleton.emit(self.opts()))
        self.full_btn = QPushButton("生成全曲（LLM）")
        self.full_btn.setToolTip("骨架作基调，LLM 在其上做提示词变奏")
        self.full_btn.clicked.connect(lambda: self.generateFull.emit(self.opts()))
        row.addWidget(self.skel_btn)
        row.addWidget(self.full_btn)
        f2.addRow("", self._wrap(row))
        self.again_btn = QPushButton("再来一版（换种子）")
        self.again_btn.clicked.connect(self._emit_again)
        f2.addRow("", self.again_btn)
        v.addWidget(g2)

        # ---- 选中 ----
        g3 = QGroupBox("选中")
        self.sel_label = QLabel("（乐谱区未选中）")
        self.sel_label.setWordWrap(True)
        self.sel_label.setObjectName("panelHint")
        lay3 = QVBoxLayout(g3)
        lay3.addWidget(self.sel_label)
        v.addWidget(g3)
        v.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._styles = []
        self.refresh_styles()

    def _wrap(self, layout):
        w = QWidget()
        w.setLayout(layout)
        return w

    def refresh_styles(self):
        """只列「学过」的风格包（有 grammar.yaml）。

        未学过的包不进 UI：它们没有可解释语法支撑。
        全库列表仍在「作曲库 → 风格 Genre」里看。
        """
        self._styles = []
        self.style_combo.blockSignals(True)
        self.style_combo.clear()
        self.style_combo.addItem("（无 / 用提示词判断）", '')
        try:
            import composition_skills as skill_lib
            for g in skill_lib.learned_genres():
                fam = (g.get('dimensions') or {}).get('family') or ''
                self._styles.append(g)
                self.style_combo.addItem(f"{g['name']}（{fam}）", g['id'])
        except Exception:
            pass
        self.style_combo.blockSignals(False)
        if self.style_combo.count() > 1:
            self.style_combo.setToolTip(
                f"已学过的风格包（{self.style_combo.count() - 1} 个，均有 grammar.yaml）："
                "决定拍号/速度/和声/织体/音区；自然语言可走 /compose-grammar")
        else:
            self.style_combo.setToolTip(
                "还没有学过的风格包。先用 /learn-grammar 学习")

    def _on_style_changed(self, _idx):
        gid = self.style_combo.currentData() or ''
        if not gid:
            self.style_info.setText("（未选择）")
            return
        try:
            import style_skeleton
            d = style_skeleton.describe_style(gid)
            keys = '、'.join(str(k) for k in (d.get('keys') or [])[:4])
            harm = '、'.join(str(h) for h in (d.get('harmony') or [])[:3])
            self.style_info.setText(
                f"拍号 {d.get('meter')} · ♩={d.get('tempo')} · {d.get('tempo_energy')}\n"
                f"织体 {d.get('texture')} / 伴奏 {d.get('accompaniment')}\n"
                f"和声 {harm}\n常用调 {keys}\n{d.get('summary') or ''}")
        except Exception as e:
            self.style_info.setText(f"（读取失败 {e}）")

    def set_style(self, genre_id: str):
        idx = self.style_combo.findData(genre_id)
        if idx >= 0:
            self.style_combo.setCurrentIndex(idx)

    def opts(self) -> dict:
        vmap = {0: 0, 1: 2, 2: 3, 3: 4}
        fmap = {0: 'auto', 1: 'full', 2: 'off'}
        return {
            'genre_id': self.style_combo.currentData() or '',
            'bars': int(self.bars_spin.value()),
            'voices': vmap.get(self.voices_combo.currentIndex(), 0),
            'fugue': fmap.get(self.fugue_combo.currentIndex(), 'auto'),
            'seed': (self.seed_edit.text() or '').strip(),
            'brief': (self.brief_edit.toPlainText() or '').strip(),
        }

    def _emit_again(self):
        self.seed_edit.setText('')
        self.regenerate.emit(self.opts())

    def set_selection(self, payload):
        if not payload:
            self.sel_label.setText("（乐谱区未选中）")
            return
        syms = payload.get('symbols') or []
        meas = payload.get('measures') or []
        parts = []
        if meas:
            parts.append(f"小节 {min(meas) + 1}–{max(meas) + 1}")
        if syms:
            parts.append(f"{len(syms)} 个音符")
        self.sel_label.setText(" / ".join(parts) if parts else "（空选区）")


# ============================================================
# 钢琴卷帘（FL Studio Piano roll 风格）
# ============================================================

class PianoRollView(QGraphicsView):
    """按 (拍, 音高) 网格显示各声部音符；支持点击定位播放头。"""

    seekRequested = pyqtSignal(float)

    PX_PER_BEAT = 26.0
    PX_PER_SEMI = 11.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.Antialiasing, False)
        self.setBackgroundBrush(QColor('#15161a'))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMouseTracking(True)
        self.beats = 0.0
        self.bpm = 120.0
        self.lo_midi, self.hi_midi = 36, 96
        self._playhead = None
        self.setToolTip("钢琴卷帘：点击可定位播放头")

    def set_score(self, model, bpm: float = 120.0, beats_per_bar: float = 4.0):
        self.scene.clear()
        self._playhead = None
        self.bpm = max(float(bpm or 120.0), 1.0)
        self.beats_per_bar = float(beats_per_bar or 4.0)
        try:
            staves = list(model.staves or [])
        except Exception:
            staves = []
        if not staves:
            return
        # 时间轴：按每个声部顺序铺开音符（与 _build_playback_timeline 一致）
        total_beats = 0.0
        notes = []          # (staff, start_beat, dur_beats, midi)
        for si, staff in enumerate(staves):
            t = 0.0
            for m in (staff.measures or []):
                if not m.notes:
                    t += self.beats_per_bar
                    continue
                for n in m.notes:
                    dur = max(n.duration_value() * 4.0, 0.05)
                    try:
                        midi = n.pitch.to_midi()
                    except Exception:
                        midi = 60
                    notes.append((si, t, dur, midi))
                    t += dur
            total_beats = max(total_beats, t)
        self.beats = max(total_beats, 1.0)

        mids = [n[3] for n in notes] or [60]
        self.lo_midi = max(21, min(mids) - 3)
        self.hi_midi = min(108, max(mids) + 3)

        # 背景：八度带 + 小节网格
        for m in range(self.lo_midi, self.hi_midi + 1):
            y = (self.hi_midi - m) * self.PX_PER_SEMI
            if _is_black(m):
                self.scene.addRect(0, y, self.beats * self.PX_PER_BEAT,
                                   self.PX_PER_SEMI, QPen(Qt.NoPen),
                                   QBrush(QColor('#1b1d23')))
            if m % 12 == 0:
                self.scene.addRect(0, y, self.beats * self.PX_PER_BEAT, 1,
                                   QPen(QColor('#2c2f36')), QBrush(QColor('#2c2f36')))
        n_bars = int(self.beats // self.beats_per_bar) + 1
        for b in range(n_bars + 1):
            x = b * self.beats_per_bar * self.PX_PER_BEAT
            pen = QPen(QColor('#3a3e47') if b % 4 else QColor('#5a5f6b'))
            self.scene.addLine(x, 0, x, (self.hi_midi - self.lo_midi + 1) * self.PX_PER_SEMI, pen)
        # 音符
        for si, t, dur, midi in notes:
            y = (self.hi_midi - midi) * self.PX_PER_SEMI
            color = QColor(TRACK_COLORS[si % len(TRACK_COLORS)])
            item = self.scene.addRect(
                t * self.PX_PER_BEAT, y,
                max(3.0, dur * self.PX_PER_BEAT - 1.5), self.PX_PER_SEMI - 1.5,
                QPen(QColor('#0b0c0f')), QBrush(color))
            item.setToolTip(f"{pitch_label(midi)} · {dur:g} 拍")
        self.setSceneRect(self.scene.itemsBoundingRect().adjusted(-8, -8, 8, 8))

    def set_playhead(self, seconds: float):
        if not self.beats:
            return
        beats = seconds * self.bpm / 60.0
        x = beats * self.PX_PER_BEAT
        h = (self.hi_midi - self.lo_midi + 1) * self.PX_PER_SEMI
        if self._playhead is None:
            self._playhead = self.scene.addLine(x, 0, x, h, QPen(QColor('#ff8c1a'), 2))
            self._playhead.setZValue(10)
        else:
            self._playhead.setLine(x, 0, x, h)

    def mousePressEvent(self, event):
        pos = self.mapToScene(event.pos())
        beats = pos.x() / self.PX_PER_BEAT
        self.seekRequested.emit(max(0.0, beats * 60.0 / self.bpm))
        super().mousePressEvent(event)


# ============================================================
# 编排视图（MuseScore 布局：左侧声部栏 + 右侧时间线）
# ============================================================

# MuseScore Studio 浅色配色
_MS_BG = '#f3f3f3'
_MS_PANEL = '#fafafa'
_MS_BORDER = '#c8c8c8'
_MS_LANE = '#ffffff'
_MS_LANE_ALT = '#f0f4f8'
_MS_GRID = '#d0d5db'
_MS_GRID_BAR = '#9aa3ad'
_MS_TEXT = '#2c2f36'
_MS_ACCENT = '#3b8eea'
_MS_PLAYHEAD = '#e31c23'
_MS_SECTION = '#6c63ff'


class _ArrangementTimeline(QGraphicsView):
    """右侧时间线：每声部一行、每小节一格（MuseScore Timeline 观感）。"""

    trackSelected = pyqtSignal(int)

    BAR_W = 40.0
    LANE_H = 28.0
    HEADER_H = 22.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setBackgroundBrush(QColor(_MS_BG))
        self.setRenderHint(QPainter.Antialiasing, False)
        self.setFrameShape(QFrame.NoFrame)
        self._playhead = None
        self.bpm = 120.0
        self.beats_per_bar = 4.0
        self.n_bars = 0
        self.setToolTip("时间线：点击小节块可选中声部")

    def set_score(self, model, bpm: float = 120.0, beats_per_bar: float = 4.0,
                  sections=None):
        self.scene.clear()
        self._playhead = None
        self.bpm = max(float(bpm or 120.0), 1.0)
        self.beats_per_bar = float(beats_per_bar or 4.0)
        try:
            staves = list(model.staves or [])
        except Exception:
            staves = []
        if not staves:
            return
        self.n_bars = max((len(s.measures or []) for s in staves), default=0)
        # 小节号标尺
        for mi in range(self.n_bars):
            x = mi * self.BAR_W
            if mi == 0 or (mi + 1) % 4 == 0 or mi + 1 == 1:
                t = self.scene.addText(str(mi + 1))
                t.setDefaultTextColor(QColor('#66707a'))
                t.setPos(x + 2, 2)
                f = t.font()
                f.setPointSize(8)
                t.setFont(f)
            pen = QPen(QColor(_MS_GRID_BAR if (mi + 1) % 4 == 0 else _MS_GRID))
            self.scene.addLine(x, self.HEADER_H, x,
                               self.HEADER_H + len(staves) * self.LANE_H, pen)
        for si, staff in enumerate(staves):
            y = self.HEADER_H + si * self.LANE_H
            lane_bg = QColor(_MS_LANE_ALT if si % 2 else _MS_LANE)
            self.scene.addRect(0, y, max(1, self.n_bars) * self.BAR_W, self.LANE_H,
                               QPen(QColor(_MS_BORDER)), QBrush(lane_bg))
            color = QColor(TRACK_COLORS[si % len(TRACK_COLORS)])
            for mi, m in enumerate(staff.measures or []):
                dur = sum(n.duration_value() * 4.0 for n in (m.notes or []))
                dense = min(1.0, dur / max(self.beats_per_bar, 0.001))
                if dense < 0.02:
                    continue
                c = QColor(color)
                c.setAlphaF(0.35 + 0.55 * dense)
                x = mi * self.BAR_W
                item = self.scene.addRect(
                    x + 1, y + 3, self.BAR_W - 2, self.LANE_H - 6,
                    QPen(QColor('#ffffff')), QBrush(c))
                item.setData(0, si)
                name = getattr(staff, 'name', '') or f'声部 {si + 1}'
                item.setToolTip(f"{name} · 第 {mi + 1} 小节 · 密度 {dense:.0%}")
        # 段落条（MuseScore Timeline 式）
        if sections:
            base_y = self.HEADER_H + len(staves) * self.LANE_H + 4
            for sec in sections:
                if isinstance(sec, dict):
                    nm = str(sec.get('name') or '')
                    x = (int(sec.get('start') or 1) - 1) * self.BAR_W
                    width = int(sec.get('bars') or 1) * self.BAR_W
                    fuga = bool(sec.get('fugue'))
                else:
                    nm, x, width, fuga = str(sec), 0.0, self.BAR_W, False
                if not nm:
                    continue
                col = QColor('#e8a317' if fuga else _MS_SECTION)
                self.scene.addRect(x, base_y, max(12.0, width - 2), 16,
                                   QPen(col.darker(120)), QBrush(col))
                t = self.scene.addText(nm + ('（赋格）' if fuga else ''))
                t.setDefaultTextColor(QColor('#ffffff'))
                t.setPos(x + 2, base_y - 1)
                f = t.font()
                f.setPointSize(8)
                t.setFont(f)
        self.setSceneRect(self.scene.itemsBoundingRect().adjusted(-4, -4, 24, 8))

    def set_playhead(self, seconds: float):
        if not self.n_bars:
            return
        beats = seconds * self.bpm / 60.0
        x = (beats / max(self.beats_per_bar, 0.001)) * self.BAR_W
        h = max(self.HEADER_H + self.LANE_H, self.scene.itemsBoundingRect().height())
        if self._playhead is None:
            self._playhead = self.scene.addLine(
                x, 0, x, h, QPen(QColor(_MS_PLAYHEAD), 2))
            self._playhead.setZValue(10)
        else:
            self._playhead.setLine(x, 0, x, h)

    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        if item is not None and item.data(0) is not None:
            try:
                self.trackSelected.emit(int(item.data(0)))
            except (TypeError, ValueError):
                pass
        super().mousePressEvent(event)


class ArrangementView(QWidget):
    """MuseScore 式编排页：左声部列表 + 右时间线。对外 API 与旧 QGraphicsView 版兼容。"""

    trackSelected = pyqtSignal(int)
    muteToggled = pyqtSignal(int, bool)
    soloToggled = pyqtSignal(int, bool)
    visibilityToggled = pyqtSignal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('arrangementPage')
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(3)
        split.setChildrenCollapsible(False)

        # ---- 左侧：声部栏（Instruments）----
        left = QWidget()
        left.setObjectName('msInstruments')
        left.setMinimumWidth(168)
        left.setMaximumWidth(280)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        head = QLabel("  声部")
        head.setObjectName('msPanelHeader')
        head.setFixedHeight(28)
        lv.addWidget(head)
        self.instruments = QTreeWidget()
        self.instruments.setObjectName('msInstrumentTree')
        self.instruments.setHeaderLabels(['👁', 'M', 'S', '名称'])
        self.instruments.setRootIsDecorated(False)
        self.instruments.setUniformRowHeights(True)
        self.instruments.setSelectionMode(QAbstractItemView.SingleSelection)
        self.instruments.setColumnWidth(0, 28)
        self.instruments.setColumnWidth(1, 28)
        self.instruments.setColumnWidth(2, 28)
        self.instruments.header().setStretchLastSection(True)
        self.instruments.itemSelectionChanged.connect(self._on_instr_select)
        self.instruments.itemChanged.connect(self._on_instr_changed)
        lv.addWidget(self.instruments, 1)
        hint = QLabel("  可见 / 静音 / 独奏")
        hint.setObjectName('panelHint')
        hint.setFixedHeight(22)
        lv.addWidget(hint)

        # ---- 右侧：时间线 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        th = QLabel("  时间线")
        th.setObjectName('msPanelHeader')
        th.setFixedHeight(28)
        rv.addWidget(th)
        self._timeline = _ArrangementTimeline()
        self._timeline.trackSelected.connect(self._on_timeline_track)
        rv.addWidget(self._timeline, 1)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([200, 600])
        root.addWidget(split)

        self.setStyleSheet(f"""
            #arrangementPage {{ background: {_MS_BG}; }}
            #msInstruments {{ background: {_MS_PANEL}; border-right: 1px solid {_MS_BORDER}; }}
            #msPanelHeader {{
                background: #e8e8e8; color: {_MS_TEXT}; font-weight: 600;
                border-bottom: 1px solid {_MS_BORDER}; padding-left: 4px;
            }}
            #msInstrumentTree {{
                background: {_MS_PANEL}; color: {_MS_TEXT}; border: none;
                font-size: 12px;
            }}
            #msInstrumentTree::item:selected {{ background: {_MS_ACCENT}; color: #fff; }}
            #panelHint {{ color: #8892a0; font-size: 11px; }}
        """)
        self._block_item = False

    def set_score(self, model, bpm: float = 120.0, beats_per_bar: float = 4.0,
                  sections=None):
        self._timeline.set_score(model, bpm, beats_per_bar, sections)
        self._block_item = True
        self.instruments.clear()
        try:
            staves = list(model.staves or [])
        except Exception:
            staves = []
        for si, staff in enumerate(staves):
            name = getattr(staff, 'name', '') or f'声部 {si + 1}'
            clef = getattr(staff, 'clef', None)
            clef_s = ''
            if clef is not None:
                clef_s = getattr(clef, 'name', None) or str(clef) or ''
            label = f"{name}" + (f"  · {clef_s}" if clef_s else '')
            item = QTreeWidgetItem(['', '', '', label])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable
                          | Qt.ItemIsEnabled)
            item.setCheckState(0, Qt.Checked)   # visible
            item.setCheckState(1, Qt.Unchecked)  # mute
            item.setCheckState(2, Qt.Unchecked)  # solo
            item.setData(0, Qt.UserRole, si)
            item.setToolTip(3, f"声部 {si + 1}：{name}")
            # 色标
            color = QColor(TRACK_COLORS[si % len(TRACK_COLORS)])
            item.setBackground(3, color.lighter(170))
            self.instruments.addTopLevelItem(item)
        self._block_item = False

    def set_playhead(self, seconds: float):
        self._timeline.set_playhead(seconds)

    def select_track(self, idx: int):
        for i in range(self.instruments.topLevelItemCount()):
            it = self.instruments.topLevelItem(i)
            if it and int(it.data(0, Qt.UserRole) or -1) == idx:
                self.instruments.setCurrentItem(it)
                break

    def _on_instr_select(self):
        items = self.instruments.selectedItems()
        if not items:
            return
        idx = items[0].data(0, Qt.UserRole)
        if idx is not None:
            self.trackSelected.emit(int(idx))

    def _on_timeline_track(self, idx: int):
        self.select_track(idx)
        self.trackSelected.emit(idx)

    def _on_instr_changed(self, item, column):
        if self._block_item or item is None:
            return
        idx = item.data(0, Qt.UserRole)
        if idx is None:
            return
        idx = int(idx)
        if column == 0:
            self.visibilityToggled.emit(idx, item.checkState(0) == Qt.Checked)
        elif column == 1:
            self.muteToggled.emit(idx, item.checkState(1) == Qt.Checked)
        elif column == 2:
            self.soloToggled.emit(idx, item.checkState(2) == Qt.Checked)


# ============================================================
# 走带增强控件（循环 / 节拍器 / 跟随 / 吸附）
# ============================================================

class TransportExtras:
    """往既有走带条里追加的 DAW 控件集合（属性挂在宿主窗口上）。"""

    @staticmethod
    def build(host, layout):
        def _btn(text, tip, cb, w=40):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(w, 30)
            b.setCheckable(True)
            b.clicked.connect(cb)
            return b

        def _sep():
            f = QFrame()
            f.setFrameShape(QFrame.VLine)
            f.setStyleSheet("color:#34373f;")
            return f

        layout.addWidget(_sep())
        host.loop_btn = _btn("🔁", "循环播放（Ctrl+Shift+L）：播到出点/结尾自动回到入点",
                             lambda: host.set_loop(host.loop_btn.isChecked()), 38)
        layout.addWidget(host.loop_btn)
        host.punch_in_btn = QPushButton("⟦入")
        host.punch_in_btn.setToolTip("把当前播放位置设为循环入点")
        host.punch_in_btn.setFixedSize(38, 30)
        host.punch_in_btn.clicked.connect(host.set_punch_in)
        layout.addWidget(host.punch_in_btn)
        host.punch_out_btn = QPushButton("出⟧")
        host.punch_out_btn.setToolTip("把当前播放位置设为循环出点")
        host.punch_out_btn.setFixedSize(38, 30)
        host.punch_out_btn.clicked.connect(host.set_punch_out)
        layout.addWidget(host.punch_out_btn)

        host.metro_btn = _btn("🎯", "节拍器：随播放打拍（每小节第一拍重音）",
                              lambda: host.set_metronome(host.metro_btn.isChecked()), 38)
        layout.addWidget(host.metro_btn)
        host.follow_btn = _btn("⇥", "跟随播放头：播放时自动滚动乐谱/卷帘",
                               lambda: host.set_follow(host.follow_btn.isChecked()), 38)
        host.follow_btn.setChecked(True)
        layout.addWidget(host.follow_btn)

        layout.addWidget(_sep())
        host.snap_combo = QComboBox()
        host.snap_combo.addItems(["吸附 1/4", "吸附 1/8", "吸附 1/16", "关"])
        host.snap_combo.setToolTip("钢琴卷帘/编排视图的网格吸附粒度")
        host.snap_combo.setFixedWidth(92)
        layout.addWidget(host.snap_combo)
        return host
