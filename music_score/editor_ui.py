"""
编辑模式 UI（布局对齐 MuseScore Score 工作区）：

* ScoreEditPalette —— 顶部「音符输入」工具条
* ScorePalettePanel —— 左侧符号面板（演奏法 / 装饰音 / 连线 / 力度…）
* SuggestionBar —— 辅助作曲建议条

深色主题与主窗口一致。纯展示：信号与宿主通信，业务在 EditController。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QCheckBox, QFrame, QScrollArea, QGridLayout, QToolButton, QLineEdit,
)

from .models import (
    NoteDuration, Accidental,
    ARTICULATION_MARKS, ORNAMENT_MARKS, OTHER_MARKS,
)


_TOOLS = [
    ('select', '✥', '选择（Esc）· 拖拽改音高 · 方向键微调'),
    ('note', '♪', '音符输入（N）· 点击谱表插入'),
    ('rest', '𝄽', '休止符 · 点击谱表插入'),
    ('erase', '⌫', '擦除 · 点击删除音符'),
]
_DURATIONS = [
    ('𝅝', NoteDuration.WHOLE, '全音符（1）'),
    ('𝅗𝅥', NoteDuration.HALF, '二分音符（2）'),
    ('♩', NoteDuration.QUARTER, '四分音符（3）'),
    ('♪', NoteDuration.EIGHTH, '八分音符（4）'),
    ('𝅘𝅥𝅯', NoteDuration.SIXTEENTH, '十六分音符（5）'),
    ('𝅘𝅥𝅰', NoteDuration.THIRTY_SECOND, '三十二分音符（6）'),
]
_ACCIDENTALS = [
    ('♮', Accidental.NATURAL, '还原（0 / =）'),
    ('♯', Accidental.SHARP, '升号（+）'),
    ('♭', Accidental.FLAT, '降号（-）'),
    ('𝄪', Accidental.DOUBLE_SHARP, '重升（++）'),
    ('𝄫', Accidental.DOUBLE_FLAT, '重降（--）'),
]

_MARK_LABELS = {
    'staccato': ('·', 'Staccato 跳音'),
    'staccatissimo': ('·|', 'Staccatissimo'),
    'accent': ('>', 'Accent 重音'),
    'marcato': ('^', 'Marcato'),
    'tenuto': ('–', 'Tenuto 保持'),
    'fermata': ('𝄐', 'Fermata 延长'),
    'trill': ('tr', 'Trill 颤音'),
    'mordent': ('m', 'Mordent 波音'),
    'inverted_mordent': ('inv', '逆波音'),
    'turn': ('∾', 'Turn 回音'),
    'arpeggio': ('⫽', 'Arpeggio 琶音'),
    'breath': ('\'', 'Breath 换气'),
}


def _mark_items(mark_ids):
    out = []
    for mid in mark_ids:
        lab, tip = _MARK_LABELS.get(mid, (mid[:4], mid))
        out.append((mid, lab, tip))
    return out


_ARTICULATION_ITEMS = _mark_items(ARTICULATION_MARKS)
_ORNAMENT_ITEMS = _mark_items(ORNAMENT_MARKS)
_OTHER_ITEMS = _mark_items(OTHER_MARKS)
_LINE_ITEMS = [
    ('tie', '⌒', '延音线 Tie（T）'),
    ('slur', '⌢', '连音线 Slur'),
    ('crescendo', '<', '渐强 Hairpin'),
    ('diminuendo', '>', '渐弱 Hairpin'),
    ('grace', '𝅘𝅥𝅮', '倚音 Grace'),
]
_TUPLET_ITEMS = [
    ('tuplet3', '3', '三连音 3:2'),
    ('tuplet5', '5', '五连音'),
    ('tuplet6', '6', '六连音'),
    ('tuplet7', '7', '七连音'),
]
_BARLINE_ITEMS = [
    ('bar:regular', '|', '单小节线'),
    ('bar:double', '∥', '复纵线 Double'),
    ('bar:final', '∥·', '终止线 Final'),
    ('bar:dashed', '·|·', '虚线 Dashed'),
    ('bar:repeat_start', '𝄆', '反复开始'),
    ('bar:repeat_end', '𝄇', '反复结束'),
]
_DYNAMICS = [
    'ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff', 'fff',
    'fp', 'sf', 'sfz', 'sfp', 'rf', 'fz',
]
_KEY_ITEMS = [
    ('key:0', 'C', 'C 大调 / a 小调'),
    ('key:1', 'G', 'G 大调 / e 小调'),
    ('key:2', 'D', 'D 大调 / b 小调'),
    ('key:3', 'A', 'A 大调 / f♯ 小调'),
    ('key:4', 'E', 'E 大调 / c♯ 小调'),
    ('key:5', 'B', 'B 大调 / g♯ 小调'),
    ('key:6', 'F♯', 'F♯ 大调'),
    ('key:7', 'C♯', 'C♯ 大调'),
    ('key:-1', 'F', 'F 大调 / d 小调'),
    ('key:-2', 'B♭', 'B♭ 大调 / g 小调'),
    ('key:-3', 'E♭', 'E♭ 大调'),
    ('key:-4', 'A♭', 'A♭ 大调'),
    ('key:-5', 'D♭', 'D♭ 大调'),
    ('key:-6', 'G♭', 'G♭ 大调'),
    ('key:-7', 'C♭', 'C♭ 大调'),
]
_TIME_ITEMS = [
    ('time:common', 'C', '4/4 Common time'),
    ('time:cut', '¢', '2/2 Cut time'),
    ('time:4/4', '4/4', '四四拍'),
    ('time:3/4', '3/4', '三四拍'),
    ('time:2/4', '2/4', '二四拍'),
    ('time:6/8', '6/8', '六八拍'),
    ('time:9/8', '9/8', '九八拍'),
    ('time:12/8', '12/8', '十二八拍'),
    ('time:2/2', '2/2', '二二拍'),
    ('time:3/8', '3/8', '三八拍'),
    ('time:5/4', '5/4', '五四拍'),
    ('time:6/4', '6/4', '六四拍'),
    ('time:7/4', '7/4', '七四拍'),
]
_CLEF_ITEMS = [
    ('clef:TREBLE', '𝄞', '高音谱号 Treble'),
    ('clef:BASS', '𝄢', '低音谱号 Bass'),
    ('clef:ALTO', '𝄡', '中音谱号 Alto'),
    ('clef:TENOR', '𝄡', '次中音 Tenor'),
    ('clef:TREBLE_8VB', '𝄞8', '高音 8va bassa'),
    ('clef:BASS_8VB', '𝄢8', '低音 8va bassa'),
]
_TEMPO_ITEMS = [
    ('tempo:Largo', 'Largo', '广板'),
    ('tempo:Adagio', 'Adagio', '柔板'),
    ('tempo:Andante', 'Andante', '行板'),
    ('tempo:Moderato', 'Moderato', '中板'),
    ('tempo:Allegro', 'Allegro', '快板'),
    ('tempo:Presto', 'Presto', '急板'),
    ('tempo:Rit.', 'Rit.', '渐慢'),
    ('tempo:accel.', 'accel.', '渐快'),
    ('tempo:a tempo', 'a tempo', '回原速'),
    ('tempo_bpm:120', '♩=120', '速度 120 BPM'),
    ('tempo_bpm:92', '♩=92', '速度 92 BPM'),
    ('tempo_bpm:60', '♩=60', '速度 60 BPM'),
]
_JUMP_ITEMS = [
    ('text:D.C.', 'D.C.', 'Da Capo'),
    ('text:D.S.', 'D.S.', 'Dal Segno'),
    ('text:Fine', 'Fine', 'Fine'),
    ('text:To Coda', 'Coda', 'To Coda'),
    ('text:Segno', 'Segno', 'Segno'),
    ('rehearsal:A', 'A', '排练号 A'),
    ('rehearsal:B', 'B', '排练号 B'),
    ('rehearsal:C', 'C', '排练号 C'),
]


_MUSIC_FONT = QFont('Noto Music')
_MUSIC_FONT.setStyleHint(QFont.SansSerif)
_MUSIC_FONT.setPointSize(13)


def _flat_btn(text, tip, checkable=True, min_w=28):
    b = QPushButton(text)
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    b.setProperty('paletteBtn', True)
    b.setMinimumWidth(min_w)
    b.setFixedHeight(28)
    b.setFont(_MUSIC_FONT)
    return b


def _sep():
    f = QFrame()
    f.setFrameShape(QFrame.VLine)
    f.setStyleSheet('color:#34373f;')
    return f


class ScoreEditPalette(QWidget):
    """顶部音符输入工具条（对应 MuseScore Note input toolbar）。"""

    toolChanged = pyqtSignal(str)
    durationChanged = pyqtSignal(object)
    accidentalChanged = pyqtSignal(object)
    dotToggled = pyqtSignal()
    markToggled = pyqtSignal(str)
    lineActionRequested = pyqtSignal(str)  # tie/slur/crescendo/…
    dynamicApplyRequested = pyqtSignal(str)
    undoRequested = pyqtSignal()
    redoRequested = pyqtSignal()
    assistToggled = pyqtSignal(bool)
    strictToggled = pyqtSignal(bool)
    addMeasuresRequested = pyqtSignal()
    addPartRequested = pyqtSignal()
    accompRequested = pyqtSignal()
    midiToggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('editPalette')
        self.setFixedHeight(46)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        cap = QLabel('  Note input')
        cap.setObjectName('noteInputCaption')
        cap.setFixedHeight(18)
        outer.addWidget(cap)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedHeight(28)
        inner = QWidget()
        h = QHBoxLayout(inner)
        h.setContentsMargins(6, 2, 6, 2)
        h.setSpacing(3)

        self._tool_btns = {}
        for i, (tool, label, tip) in enumerate(_TOOLS):
            b = _flat_btn(label, tip, min_w=32)
            b.setChecked(i == 0)
            b.clicked.connect(lambda _=False, t=tool: self._pick_tool(t))
            h.addWidget(b)
            self._tool_btns[tool] = b
        h.addWidget(_sep())

        self._dur_btns = {}
        for i, (label, dur, tip) in enumerate(_DURATIONS):
            b = _flat_btn(label, tip, min_w=34)
            b.setChecked(i == 2)
            b.clicked.connect(lambda _=False, d=dur: self._pick_duration(d))
            h.addWidget(b)
            self._dur_btns[dur] = b
        dot = _flat_btn('·', '附点（.）', min_w=26)
        dot.clicked.connect(self.dotToggled.emit)
        h.addWidget(dot)
        h.addWidget(_sep())

        self._acc_btns = {}
        for i, (label, acc, tip) in enumerate(_ACCIDENTALS):
            b = _flat_btn(label, tip, min_w=28)
            b.setChecked(i == 0)
            b.clicked.connect(lambda _=False, a=acc: self._pick_accidental(a))
            h.addWidget(b)
            self._acc_btns[acc] = b
        h.addWidget(_sep())

        tie_b = _flat_btn('⌒', '延音 Tie（T）', checkable=False, min_w=26)
        tie_b.clicked.connect(lambda: self.lineActionRequested.emit('tie'))
        h.addWidget(tie_b)
        for mk, label, tip in _ARTICULATION_ITEMS[:3]:
            b = _flat_btn(label, tip, checkable=False, min_w=26)
            b.clicked.connect(lambda _=False, m=mk: self.markToggled.emit(m))
            h.addWidget(b)
        for mid, label, tip in _TUPLET_ITEMS[:2]:
            b = _flat_btn(label, tip, checkable=False, min_w=24)
            b.clicked.connect(lambda _=False, m=mid: self.lineActionRequested.emit(m))
            h.addWidget(b)
        h.addWidget(_sep())

        undo_b = _flat_btn('↩', '撤销（Ctrl+Z）', checkable=False)
        undo_b.clicked.connect(self.undoRequested.emit)
        redo_b = _flat_btn('↪', '重做（Ctrl+Y）', checkable=False)
        redo_b.clicked.connect(self.redoRequested.emit)
        h.addWidget(undo_b)
        h.addWidget(redo_b)
        h.addStretch(1)

        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # 兼容 score_studio：辅助/MIDI/严格 已移到左侧 Palettes，保留控件供状态同步
        self.midi_check = QCheckBox()
        self.midi_check.hide()
        self.midi_check.toggled.connect(self.midiToggled.emit)
        self.assist_check = QCheckBox()
        self.assist_check.setChecked(True)
        self.assist_check.hide()
        self.assist_check.toggled.connect(self.assistToggled.emit)
        self.strict_check = QCheckBox()
        self.strict_check.setChecked(True)
        self.strict_check.hide()
        self.strict_check.toggled.connect(self.strictToggled.emit)

    def _pick_tool(self, tool):
        for t, b in self._tool_btns.items():
            b.setChecked(t == tool)
        self.toolChanged.emit(tool)

    def _pick_duration(self, dur, *, emit: bool = True):
        for d, b in self._dur_btns.items():
            b.setChecked(d == dur)
        if emit:
            self.durationChanged.emit(dur)

    def _pick_accidental(self, acc, *, emit: bool = True):
        for a, b in self._acc_btns.items():
            b.setChecked(a == acc)
        if emit:
            self.accidentalChanged.emit(acc)

    def set_tool(self, tool):
        self._pick_tool(tool)


class _PaletteSection(QWidget):
    """可折叠的一类符号格子。"""

    itemClicked = pyqtSignal(str)

    def __init__(self, title: str, items: list, parent=None, *, cols: int = 3):
        super().__init__(parent)
        self._title = title
        self._open = True
        self._buttons: list = []
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self._hdr = QToolButton()
        self._hdr.setText(f'▾ {title}')
        self._hdr.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._hdr.setCursor(Qt.PointingHandCursor)
        self._hdr.setFocusPolicy(Qt.NoFocus)
        self._hdr.setObjectName('paletteSectionHdr')
        self._hdr.clicked.connect(self._toggle)
        v.addWidget(self._hdr)
        self._body = QWidget()
        g = QGridLayout(self._body)
        g.setContentsMargins(4, 2, 4, 6)
        g.setSpacing(4)
        for i, (mid, label, tip) in enumerate(items):
            b = _flat_btn(label, tip, checkable=False, min_w=36)
            b.setFixedHeight(32)
            b.setProperty('paletteItemId', mid)
            b.setProperty('paletteSearchText', f'{title} {label} {tip} {mid}'.lower())
            b.clicked.connect(lambda _=False, m=mid: self.itemClicked.emit(m))
            g.addWidget(b, i // cols, i % cols)
            self._buttons.append(b)
        v.addWidget(self._body)

    def apply_filter(self, text: str):
        q = (text or '').strip().lower()
        any_visible = False
        for b in self._buttons:
            hay = str(b.property('paletteSearchText') or '')
            show = not q or q in hay
            b.setVisible(show)
            if show:
                any_visible = True
        self.setVisible(any_visible or not q)

    def _toggle(self):
        self._open = not self._open
        self._body.setVisible(self._open)
        self._hdr.setText(('▾ ' if self._open else '▸ ') + self._title)


class ScorePalettePanel(QWidget):
    """左侧符号面板（对应 MuseScore Palettes）。"""

    markToggled = pyqtSignal(str)
    lineActionRequested = pyqtSignal(str)
    barlineRequested = pyqtSignal(str)
    dynamicApplyRequested = pyqtSignal(str)
    accidentalChanged = pyqtSignal(object)
    durationChanged = pyqtSignal(object)
    measureAttrRequested = pyqtSignal(str)
    addMeasuresRequested = pyqtSignal()
    addPartRequested = pyqtSignal()
    accompRequested = pyqtSignal()
    midiToggled = pyqtSignal(bool)
    assistToggled = pyqtSignal(bool)
    strictToggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('scorePalettePanel')
        self.setMinimumWidth(168)
        self.setMaximumWidth(280)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        title = QLabel('  Palettes')
        title.setObjectName('msPanelHeader')
        title.setFixedHeight(28)
        root.addWidget(title)

        self._search = QLineEdit()
        self._search.setObjectName('paletteSearch')
        self._search.setPlaceholderText('搜索符号…')
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(28)
        self._search.textChanged.connect(self._on_search)
        root.addWidget(self._search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(4, 6, 4, 6)
        bv.setSpacing(6)
        self._sections: list = []

        sections = [
            ('Key signatures', _KEY_ITEMS, 3),
            ('Time signatures', _TIME_ITEMS, 3),
            ('Clefs', _CLEF_ITEMS, 3),
            ('Accidentals', [(f'acc:{a.name}', lab, tip) for lab, a, tip in _ACCIDENTALS], 3),
            ('Articulations', _ARTICULATION_ITEMS, 3),
            ('Ornaments', _ORNAMENT_ITEMS, 3),
            ('Dynamics', [(f'dyn:{d}', d, f'Dynamic {d}') for d in _DYNAMICS], 3),
            ('Hairpins', _LINE_ITEMS[2:4], 2),
            ('Lines', _LINE_ITEMS[:2] + _LINE_ITEMS[4:5], 2),
            ('Barlines', _BARLINE_ITEMS, 3),
            ('Tuplets', _TUPLET_ITEMS, 4),
            ('Tempo', _TEMPO_ITEMS, 3),
            ('Repeats & jumps', _JUMP_ITEMS, 3),
            ('Note durations', [(f'dur:{d.name}', lab, tip) for lab, d, tip in _DURATIONS], 3),
            ('Other', _OTHER_ITEMS, 2),
        ]
        for sec_title, items, cols in sections:
            sec = _PaletteSection(sec_title, items, cols=cols)
            sec.itemClicked.connect(self._on_item)
            bv.addWidget(sec)
            self._sections.append(sec)

        tools = _PaletteSection('Score tools', [
            ('tool:add_measures', '+小节', '谱末增加空小节'),
            ('tool:add_part', '+声部', '添加空声部'),
            ('tool:accomp', '配乐', '根据旋律写伴奏'),
        ], cols=1)
        tools.itemClicked.connect(self._on_item)
        bv.addWidget(tools)
        self._sections.append(tools)

        opts = QWidget()
        ov = QVBoxLayout(opts)
        ov.setContentsMargins(6, 4, 6, 8)
        ov.setSpacing(4)
        self.midi_check = QCheckBox('MIDI 输入')
        self.midi_check.setToolTip('MIDI 键盘步进输入')
        self.midi_check.toggled.connect(self.midiToggled.emit)
        self.assist_check = QCheckBox('辅助作曲')
        self.assist_check.setChecked(True)
        self.assist_check.toggled.connect(self.assistToggled.emit)
        self.strict_check = QCheckBox('严格校验')
        self.strict_check.setChecked(True)
        self.strict_check.toggled.connect(self.strictToggled.emit)
        for w in (self.midi_check, self.assist_check, self.strict_check):
            w.setFocusPolicy(Qt.NoFocus)
            ov.addWidget(w)
        bv.addWidget(opts)

        bv.addStretch(1)
        tip = QLabel('音符：选中后点符号\n小节：选中后点调号/拍号等')
        tip.setObjectName('panelHint')
        tip.setWordWrap(True)
        tip.setAlignment(Qt.AlignCenter)
        bv.addWidget(tip)

        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    def _on_search(self, text: str):
        for sec in self._sections:
            sec.apply_filter(text)

    def _on_item(self, mid: str):
        if mid.startswith('tool:'):
            if mid == 'tool:add_measures':
                self.addMeasuresRequested.emit()
            elif mid == 'tool:add_part':
                self.addPartRequested.emit()
            elif mid == 'tool:accomp':
                self.accompRequested.emit()
            return
        if mid.startswith('dyn:'):
            self.dynamicApplyRequested.emit(mid[4:])
        elif mid.startswith('dur:'):
            try:
                self.durationChanged.emit(NoteDuration[mid[4:]])
            except KeyError:
                pass
        elif mid.startswith('acc:'):
            try:
                self.accidentalChanged.emit(Accidental[mid[4:]])
            except KeyError:
                pass
        elif mid.startswith('bar:'):
            self.barlineRequested.emit(mid[4:])
        elif mid.startswith(('key:', 'time:', 'clef:', 'tempo:', 'tempo_bpm:', 'text:', 'rehearsal:')):
            self.measureAttrRequested.emit(mid)
        elif mid.startswith('tuplet') or mid in ('tie', 'slur', 'crescendo', 'diminuendo', 'grace'):
            self.lineActionRequested.emit(mid)
        else:
            self.markToggled.emit(mid)


class SuggestionBar(QWidget):
    """多种铺满可能性（数字键切换 ghost）+ 下一音行。"""
    candidateClicked = pyqtSignal(object)   # Candidate (note)
    phraseClicked = pyqtSignal(object)      # Candidate (phrase) — 采纳
    variantSelected = pyqtSignal(int)       # 0-based：只切换 ghost

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('suggestionBar')
        self.setMinimumHeight(34)
        self._context = None
        self._last_sig = None
        self._notes: list = []
        self._phrases: list = []
        self._selected: int = 0
        self._judge_note: str = ''
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 3, 10, 3)
        root.setSpacing(2)

        self._row1 = QHBoxLayout()
        self._row1.setSpacing(6)
        self.title_label = QLabel('💡 可能性')
        self.title_label.setObjectName('sugTitle')
        self._row1.addWidget(self.title_label)
        self._variant_chips: list = []
        root.addLayout(self._row1)

        # 听审说明常驻，重建 chips 时绝不 deleteLater（否则异步回调 setText 会闪退）
        self.judge_label = QLabel('')
        self.judge_label.setObjectName('sugEmpty')
        self.judge_label.setWordWrap(True)
        root.addWidget(self.judge_label)

        self._row2 = QHBoxLayout()
        self._row2.setSpacing(6)
        self.note_label = QLabel('下一音')
        self.note_label.setObjectName('sugTitle')
        self._row2.addWidget(self.note_label)
        self._note_chips: list = []
        root.addLayout(self._row2)
        self.note_label.setVisible(False)

        self.set_suggestions([], '悬停在谱表上，或写入音符后显示多种可能性')

    def set_context(self, staff_idx, measure_idx, insert_index):
        self._context = (staff_idx, measure_idx, insert_index)

    @property
    def context(self):
        return self._context

    @property
    def notes(self):
        return list(self._notes)

    @property
    def phrases(self):
        return list(self._phrases)

    @property
    def selected_index(self):
        return int(self._selected)

    def selected_phrase(self):
        if 0 <= self._selected < len(self._phrases):
            return self._phrases[self._selected]
        return self._phrases[0] if self._phrases else None

    def set_judge_note(self, text: str):
        self._judge_note = text or ''
        try:
            self.judge_label.setText(self._judge_note)
        except RuntimeError:
            pass

    def set_selected(self, index: int, *, emit: bool = False):
        if not self._phrases:
            self._selected = 0
            return
        self._selected = max(0, min(int(index), len(self._phrases) - 1))
        self._restyle_variant_chips()
        if emit:
            self.variantSelected.emit(self._selected)

    def set_suggestions(self, candidates, note='', phrases=None,
                        selected: int = 0, judge_note: str = ''):
        phrases = list(phrases or [])
        candidates = list(candidates or [])
        sel = max(0, min(int(selected), max(0, len(phrases) - 1))) if phrases else 0
        try:
            cand_sig = tuple((c.label, c.reason) for c in candidates)
            phr_sig = tuple((c.label, c.reason) for c in phrases)
        except Exception:
            cand_sig, phr_sig = (), ()
        sig = (note, judge_note, sel, cand_sig, phr_sig)
        if sig == self._last_sig:
            return
        self._last_sig = sig
        self._notes = candidates
        self._phrases = phrases
        self._selected = sel
        if judge_note is not None:
            self._judge_note = judge_note or ''
            try:
                self.judge_label.setText(self._judge_note)
            except RuntimeError:
                pass

        for c in self._variant_chips:
            try:
                c.deleteLater()
            except RuntimeError:
                pass
        self._variant_chips = []
        while self._row1.count() > 1:
            item = self._row1.takeAt(1)
            w = item.widget()
            if w is not None:
                try:
                    w.deleteLater()
                except RuntimeError:
                    pass

        if not phrases:
            lbl = QLabel(note or '暂无铺满可能性（先写至少一个音）')
            lbl.setObjectName('sugEmpty')
            self._row1.addWidget(lbl, 1)
            self._variant_chips.append(lbl)
        else:
            for i, cand in enumerate(phrases):
                prefix = f'{i + 1} ' if i < 9 else ''
                style = (cand.reason.split('（')[-1].rstrip('）')
                         if '（' in cand.reason else cand.reason)
                try:
                    lab = cand.label
                except Exception:
                    lab = '?'
                chip = QPushButton(f'{prefix}{lab} · {style}')
                tip = ' · '.join(getattr(cand, 'reasons', None) or [cand.reason])
                chip.setToolTip(
                    f'{i + 1} 键切换 ghost；Ctrl+Tab 采纳本小节\n{tip}')
                chip.setCursor(Qt.PointingHandCursor)
                chip.setFocusPolicy(Qt.NoFocus)
                chip.setProperty('sugChip', True)
                chip.setProperty('variantIndex', i)
                chip.setFlat(True)
                chip.clicked.connect(
                    lambda _=False, idx=i: self._on_variant_clicked(idx))
                self._row1.addWidget(chip)
                self._variant_chips.append(chip)
            self._row1.addStretch(1)
            self._restyle_variant_chips()

        for c in self._note_chips:
            try:
                c.deleteLater()
            except RuntimeError:
                pass
        self._note_chips = []
        while self._row2.count() > 1:
            item = self._row2.takeAt(1)
            w = item.widget()
            if w is not None:
                try:
                    w.deleteLater()
                except RuntimeError:
                    pass

        if candidates:
            self.note_label.setVisible(True)
            self.setMinimumHeight(72)
            self.setMaximumHeight(96)
            for cand in candidates[:5]:
                try:
                    lab = cand.label
                except Exception:
                    lab = '?'
                chip = QPushButton(f'Tab→ {lab}')
                chip.setToolTip(f'Tab 采纳下一音 {lab} · {cand.reason}')
                chip.setCursor(Qt.PointingHandCursor)
                chip.setFocusPolicy(Qt.NoFocus)
                chip.setProperty('sugChip', True)
                chip.setFlat(True)
                chip.clicked.connect(
                    lambda _=False, c=cand: self.candidateClicked.emit(c))
                self._row2.addWidget(chip)
                self._note_chips.append(chip)
            self._row2.addStretch(1)
        else:
            self.note_label.setVisible(False)
            self.setMinimumHeight(48)
            self.setMaximumHeight(64)

    def _on_variant_clicked(self, index: int):
        self.set_selected(index, emit=True)

    def _restyle_variant_chips(self):
        for w in self._variant_chips:
            if not isinstance(w, QPushButton):
                continue
            try:
                i = int(w.property('variantIndex') or 0)
                on = i == self._selected
                w.setStyleSheet(
                    'QPushButton { font-weight: 600; text-decoration: underline; }'
                    if on else 'QPushButton { font-weight: 400; }')
            except RuntimeError:
                continue
