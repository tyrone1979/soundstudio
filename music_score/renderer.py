"""
Music score rendering using QGraphicsScene / QGraphicsView.
Supports multi-line (wrapped) staff layout.
"""
from typing import Dict, List, Optional, Tuple
import math
from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtGui import QPainter, QFont, QPen, QBrush, QColor, QFontMetrics, QPainterPath
from PyQt5.QtWidgets import (
    QGraphicsScene, QGraphicsView, QGraphicsItem,
    QGraphicsLineItem, QGraphicsEllipseItem, QGraphicsTextItem,
    QGraphicsRectItem, QGraphicsPathItem
)

from .models import (
    Score, Staff, Measure, Note, Pitch,
    ClefType, NoteName, Accidental, NoteDuration, TimeSignature, KeySignature,
    CLEF_BOTTOM_LINE,
)


# Constants for rendering
STAFF_LINE_SPACING = 10
STAFF_HEIGHT = 4 * STAFF_LINE_SPACING
LEFT_MARGIN = 60
RIGHT_MARGIN = 40
TOP_MARGIN = 80
STAFF_GAP = 140
SYSTEM_GAP = 180       # Gap between staff systems (wrapped lines)
# 点击/悬停加线区：约上下 4 间（够到 B5/C6/D6；多声部时按间距收缩以免重叠）
HIT_LEDGER_PAD = 4 * STAFF_LINE_SPACING
NOTE_HEAD_WIDTH = 10
NOTE_HEAD_HEIGHT = 7
DEFAULT_MEASURE_WIDTH = 160
MIN_MEASURE_WIDTH = 80
# 每个音符事件的最小横向占位（避免两音挤在一起）
NOTE_SLOT_WIDTH = 44
# 谱号区基础宽度（不含调号/拍号）
CLEF_PAD = 52
# 符干标准长度（3.5 间）
STEM_LEN = 35.0
# 倚音符头缩放与每个倚音占的横向宽度（谱面单位）
GRACE_SCALE = 0.65
GRACE_UNITS = 0.45
# 小节内容左侧给首音变音记号留的空
ACC_PAD = 8.0
# 符杠粗细、多重符杠间距
BEAM_THICK = 4.5
BEAM_GAP = 7.0

# Clef symbols (using Unicode musical symbols)
CLEF_SYMBOLS = {
    ClefType.TREBLE: "𝄞",
    ClefType.BASS: "𝄢",
    ClefType.ALTO: "𝄡",
    ClefType.TENOR: "𝄡",
    ClefType.TREBLE_8VB: "𝄞",
    ClefType.BASS_8VB: "𝄢",
}

# Note duration to graphical representation mapping
DURATION_FLAGS = {
    NoteDuration.WHOLE: {"filled": False, "stem": False, "flags": 0},
    NoteDuration.HALF: {"filled": False, "stem": True, "flags": 0},
    NoteDuration.QUARTER: {"filled": True, "stem": True, "flags": 0},
    NoteDuration.EIGHTH: {"filled": True, "stem": True, "flags": 1},
    NoteDuration.SIXTEENTH: {"filled": True, "stem": True, "flags": 2},
    NoteDuration.THIRTY_SECOND: {"filled": True, "stem": True, "flags": 3},
}

# 谱表坐标：0 = 下加第一线（最底线），4 = 上加第一线（最顶线）。1 = 一条线的距离。
_DIATONIC = {
    NoteName.C: 0, NoteName.D: 1, NoteName.E: 2, NoteName.F: 3,
    NoteName.G: 4, NoteName.A: 5, NoteName.B: 6,
}

# 调号位置（0 = 底线，每 0.5 一个线/间）。低音谱号 = 高音谱号整体下移一线，
# 中音谱号下移半线；次中音谱号的升号按惯例从第 2 线的 F 起（不跑到谱表上方）。
KEY_SIG_POSITIONS_SHARP = {
    ClefType.TREBLE: {
        NoteName.F: 4.0, NoteName.C: 2.5, NoteName.G: 4.5,
        NoteName.D: 3.0, NoteName.A: 1.5, NoteName.E: 3.5, NoteName.B: 2.0,
    },
    ClefType.BASS: {
        NoteName.F: 3.0, NoteName.C: 1.5, NoteName.G: 3.5,
        NoteName.D: 2.0, NoteName.A: 0.5, NoteName.E: 2.5, NoteName.B: 1.0,
    },
    ClefType.ALTO: {
        NoteName.F: 3.5, NoteName.C: 2.0, NoteName.G: 4.0,
        NoteName.D: 2.5, NoteName.A: 1.0, NoteName.E: 3.0, NoteName.B: 1.5,
    },
    ClefType.TENOR: {
        NoteName.F: 1.0, NoteName.C: 3.0, NoteName.G: 1.5,
        NoteName.D: 3.5, NoteName.A: 2.0, NoteName.E: 4.0, NoteName.B: 2.5,
    },
}
KEY_SIG_POSITIONS_FLAT = {
    ClefType.TREBLE: {
        NoteName.B: 2.0, NoteName.E: 3.5, NoteName.A: 1.5,
        NoteName.D: 3.0, NoteName.G: 1.0, NoteName.C: 2.5, NoteName.F: 0.5,
    },
    ClefType.BASS: {
        NoteName.B: 1.0, NoteName.E: 2.5, NoteName.A: 0.5,
        NoteName.D: 2.0, NoteName.G: 0.0, NoteName.C: 1.5, NoteName.F: -0.5,
    },
    ClefType.ALTO: {
        NoteName.B: 1.5, NoteName.E: 3.0, NoteName.A: 1.0,
        NoteName.D: 2.5, NoteName.G: 0.5, NoteName.C: 2.0, NoteName.F: 0.0,
    },
    ClefType.TENOR: {
        NoteName.B: 2.5, NoteName.E: 4.0, NoteName.A: 2.0,
        NoteName.D: 3.5, NoteName.G: 1.5, NoteName.C: 3.0, NoteName.F: 1.0,
    },
}
# 带 8 的谱号谱面位置与原谱号相同
for _tbl in (KEY_SIG_POSITIONS_SHARP, KEY_SIG_POSITIONS_FLAT):
    _tbl[ClefType.TREBLE_8VB] = _tbl[ClefType.TREBLE]
    _tbl[ClefType.BASS_8VB] = _tbl[ClefType.BASS]


def pitch_to_staff_position(pitch: Pitch, clef: ClefType) -> float:
    """音高到谱表位置。只按音名（全音阶），变音记号不改变位置。"""
    base_name, base_octave = CLEF_BOTTOM_LINE.get(clef, CLEF_BOTTOM_LINE[ClefType.TREBLE])
    steps = (pitch.octave - base_octave) * 7 + (_DIATONIC[pitch.name] - _DIATONIC[base_name])
    return steps * 0.5


def stem_down_for(positions) -> bool:
    """符干方向：离中线（位置 2）最远的音在中线及以上 → 朝下；单音中线朝下。"""
    if not positions:
        return False
    hi, lo = max(positions), min(positions)
    up_dist, down_dist = hi - 2.0, 2.0 - lo
    if abs(up_dist - down_dist) < 1e-9:
        return hi >= 2.0
    return up_dist > down_dist


def beam_unit(ts: Optional[TimeSignature]) -> float:
    """符杠分组单位（全音符=1）：复拍子按附点四分，其余按四分。"""
    if ts is not None and ts.denominator == 8 and ts.numerator % 3 == 0:
        return 0.375
    if ts is not None and ts.denominator == 16:
        return 0.125
    return 0.25


def displayed_accidentals(pitches, state: dict, key_alter: dict,
                          tied: bool = False) -> List[Optional[Accidental]]:
    """按调号与小节内已出现的变音，决定每个音实际要画的变音记号（含还原号）。

    state：本小节 {(音名, 八度): 当前生效的升降}，原地更新。
    tied：延音线后半个音沿用前音，不重复画变音。
    """
    out: List[Optional[Accidental]] = []
    for p in pitches:
        k = (p.name, p.octave)
        cur = state.get(k, key_alter.get(p.name, 0))
        want = int(p.accidental.value)
        state[k] = want
        if tied or want == cur:
            out.append(None)
        else:
            out.append(Accidental(want))
    return out


def accidental_symbol(acc: Accidental) -> str:
    return {
        Accidental.SHARP: "♯",
        Accidental.FLAT: "♭",
        Accidental.NATURAL: "♮",
        Accidental.DOUBLE_SHARP: "𝄪",
        Accidental.DOUBLE_FLAT: "𝄫",
    }.get(acc, "")


def staff_gap_for(n_staves: int) -> float:
    if n_staves >= 6:
        return 58
    return 72 if n_staves > 2 else 100


def hit_ledger_pad(n_staves: int) -> float:
    """小节可点高度：单谱够到加线（B5/D6）；多声部不侵入邻谱。"""
    gap = staff_gap_for(max(1, int(n_staves)))
    if n_staves <= 1:
        return float(HIT_LEDGER_PAD)
    return float(min(HIT_LEDGER_PAD, max(16.0, gap / 2.0 - 4.0)))


class ScoreRenderer:
    """Renders a Score model to QGraphicsScene items with automatic line wrapping."""

    def __init__(self, scene: QGraphicsScene):
        self.scene = scene
        self.font = QFont("Segoe UI Symbol", 14)
        self.music_font = QFont("Segoe UI Symbol", 26)
        self.small_font = QFont("Segoe UI Symbol", 14)
        self.title_font = QFont("Segoe UI", 20)
        self.title_font.setBold(True)
        self.hit_measures: List[dict] = []
        self.hit_notes: List[dict] = []
        self._highlight_items: List[QGraphicsItem] = []
        self._selected_measures: set = set()
        self._selected_symbols: List[Tuple[int, int, int]] = []
        # 编辑模式：违规徽章（(si,mi,ni) -> level/msg）与 ghost 预览
        self._violations: dict = {}
        self._violation_items: List[QGraphicsItem] = []
        self._ghost_items: List[QGraphicsItem] = []
        self._ghost_cand_items: List[QGraphicsItem] = []
        self.hit_ghost_cands: List[dict] = []
        self._live_items: List[QGraphicsItem] = []
        # note key -> list of (item, kind, default_brush_or_None, default_pen_or_None, default_color_or_None)
        self.note_gfx: dict = {}
        self._playback_active: set = set()
        self._play_color = QColor(220, 45, 55)
        self._play_fill = QBrush(self._play_color)
        self._play_outline = QPen(self._play_color, 1.8)
        # 跨音符的弧线/楔形按声部各自记账（多声部互不串扰；圆滑线可嵌套）
        self._slur_pending: Dict[int, list] = {}     # staff -> [(x, top, bot, sys_y), …]
        self._tie_pending: Dict[tuple, tuple] = {}   # (staff, midi) -> (x, y, sys_y, above)
        self._hairpin_pending: Dict[int, tuple] = {}  # staff -> (x, kind, sys_y)
        self._volta_prev: Optional[str] = None
        self._sys_right = 1160.0
        self._sys_left = float(LEFT_MARGIN)
        self._eff: List[List[dict]] = []

    def clear(self):
        self.scene.clear()
        self.hit_measures = []
        self.hit_notes = []
        self._highlight_items = []
        self._violation_items = []
        self._ghost_items = []
        self._ghost_cand_items = []
        self.hit_ghost_cands = []
        self._live_items = []
        self.note_gfx = {}
        self._playback_active = set()
        self._slur_pending = {}
        self._tie_pending = {}
        self._hairpin_pending = {}
        self._volta_prev = None

    def hit_test(self, x: float, y: float) -> Optional[dict]:
        """Return best hit: ghost candidate, then note, then measure."""
        for h in self.hit_ghost_cands:
            if h['rect'].contains(x, y):
                return h
        for h in self.hit_notes:
            if h['rect'].contains(x, y):
                return h
        hits = [h for h in self.hit_measures if h['rect'].contains(x, y)]
        if not hits:
            return None
        if len(hits) == 1:
            return hits[0]

        def _dist(h):
            sy = h.get('staff_y', h['rect'].top() + 8)
            return abs(y - (sy + STAFF_HEIGHT / 2.0))

        return min(hits, key=_dist)

    def set_selection(self, measures=None, symbols=None):
        """Highlight selected measures (0-based) and symbols [(staff, measure, note), ...]."""
        self._selected_measures = set(measures or [])
        self._selected_symbols = list(symbols or [])
        self._refresh_highlights()

    def _refresh_highlights(self):
        for item in self._highlight_items:
            try:
                self.scene.removeItem(item)
            except RuntimeError:
                pass
        self._highlight_items = []
        measure_brush = QBrush(QColor(60, 120, 220, 55))
        measure_pen = QPen(QColor(40, 90, 200, 160), 1.5)
        note_brush = QBrush(QColor(220, 140, 40, 120))
        note_pen = QPen(QColor(180, 100, 20, 200), 1.5)
        seen_m = set()
        for h in self.hit_measures:
            mi = h['measure_idx']
            if mi not in self._selected_measures:
                continue
            key = (h['staff_idx'], mi)
            if key in seen_m:
                continue
            seen_m.add(key)
            rect = QGraphicsRectItem(h['rect'])
            rect.setBrush(measure_brush)
            rect.setPen(measure_pen)
            rect.setZValue(-1)
            self.scene.addItem(rect)
            self._highlight_items.append(rect)
        sym_set = set(self._selected_symbols)
        for h in self.hit_notes:
            key = (h['staff_idx'], h['measure_idx'], h['note_idx'])
            if key not in sym_set:
                continue
            rect = QGraphicsRectItem(h['rect'])
            rect.setBrush(note_brush)
            rect.setPen(note_pen)
            rect.setZValue(50)
            self.scene.addItem(rect)
            self._highlight_items.append(rect)
        # 违规徽章：error=红实线 / warn=黄虚线（编辑模式的实时 lint 反馈）
        err_pen = QPen(QColor(255, 69, 58, 220), 2.0)
        err_brush = QBrush(QColor(255, 69, 58, 28))
        warn_pen = QPen(QColor(255, 214, 10, 210), 1.6, Qt.DashLine)
        warn_brush = QBrush(QColor(255, 214, 10, 22))
        for h in self.hit_notes:
            key = (h['staff_idx'], h['measure_idx'], h['note_idx'])
            viol = self._violations.get(key)
            if viol is None:
                continue
            r = h['rect']
            badge = QGraphicsRectItem(r.adjusted(-2, -2, 2, 2))
            level = viol.get('level') if isinstance(viol, dict) else viol
            if level == 'error':
                badge.setPen(err_pen)
                badge.setBrush(err_brush)
            else:
                badge.setPen(warn_pen)
                badge.setBrush(warn_brush)
            badge.setZValue(55)
            self.scene.addItem(badge)
            self._highlight_items.append(badge)

    # ---------- 编辑模式：违规徽章 + ghost 预览 ----------

    def set_violations(self, violations):
        """violations: list of objects with staff_idx/measure_idx/note_idx/level。"""
        table = {}
        for v in violations or []:
            try:
                table[(v.staff_idx, v.measure_idx, v.note_idx)] = v
            except AttributeError:
                pass
        self._violations = table
        self._refresh_highlights()

    def clear_violations(self):
        self._violations = {}
        self._refresh_highlights()

    def draw_ghost(self, pitch, clef, x: float, staff_y: float,
                   duration, ok: bool = True, is_rest: bool = False):
        """半透明预览音符（跟随鼠标）：绿色=可放置，红色=违规禁止。"""
        self.clear_ghost()
        color = QColor(48, 209, 88, 150) if ok else QColor(255, 69, 58, 180)
        self._draw_ghost_note(pitch, clef, x, staff_y, duration, color, is_rest)

    def draw_ghost_phrase(self, events, clef, x0: float, staff_y: float,
                          ok: bool = True):
        """短分支多音 ghost（半透明、略偏蓝以区别悬停主候选）。"""
        self.clear_ghost()
        color = QColor(90, 170, 255, 140) if ok else QColor(255, 69, 58, 160)
        x = x0
        for pitch, duration in (events or []):
            self._draw_ghost_note(pitch, clef, x, staff_y, duration, color,
                                  is_rest=False)
            from .models import NoteDuration as _ND
            step = { _ND.WHOLE: 36, _ND.HALF: 28, _ND.QUARTER: 22,
                     _ND.EIGHTH: 16, _ND.SIXTEENTH: 12}.get(duration, 20)
            x += step

    def draw_ghost_candidates(self, cands, clef, x: float, staff_y: float,
                              staff_idx: int, measure_idx: int,
                              insert_index: int):
        """灰色铺满预览：只预览，不占整段点击区。点「铺满」标签或灰音符才采纳。"""
        self.clear_ghost_candidates()
        fills = [c for c in (cands or []) if getattr(c, 'kind', '') == 'phrase']
        if not fills:
            fills = list(cands or [])[:1]
        if not fills:
            return
        cand = fills[0]
        gray = QColor(110, 112, 120, 155)
        hot = QColor(70, 70, 78, 220)
        events = list(cand.preview_events or [])
        if not events and cand.pitch:
            events = [(cand.pitch, cand.duration)]
        xs: Dict[int, float] = {}
        ys: Dict[int, float] = {}
        for h in self.hit_measures:
            if h.get('staff_idx') != staff_idx:
                continue
            mi = int(h.get('measure_idx', 0))
            r = h['rect']
            ys[mi] = float(h.get('staff_y', r.top() + 8))
            xs.setdefault(mi, r.left() + 12)
        xs[measure_idx] = x
        ys.setdefault(measure_idx, staff_y)
        from .models import NoteDuration as _ND
        step_map = {_ND.WHOLE: 36, _ND.HALF: 28, _ND.QUARTER: 22,
                    _ND.EIGHTH: 16, _ND.SIXTEENTH: 12}

        def _hit(cand_obj, rect, label):
            self.hit_ghost_cands.append({
                'kind': 'ghost_cand',
                'cand': cand_obj,
                'staff_idx': staff_idx,
                'measure_idx': measure_idx,
                'insert_index': insert_index,
                'staff_y': staff_y,
                'rect': rect,
                'label': (label or '')[:48],
            })

        tag = QGraphicsTextItem('Ctrl+Tab 采纳')
        tag.setFont(QFont("Segoe UI", 8))
        tag.setDefaultTextColor(hot)
        tag.setPos(x, staff_y - 16)
        tag.setZValue(90)
        self.scene.addItem(tag)
        self._ghost_cand_items.append(tag)
        _hit(cand, QRectF(x - 2, staff_y - 18, 88, 16), cand.label)

        for ev in events:
            if isinstance(ev, dict):
                kind = ev.get('kind') or 'note'
                pitch = ev.get('pitch')
                duration = ev.get('duration')
                bar_off = int(ev.get('bar_offset') or 0)
                is_rest = kind == 'rest' or ev.get('is_rest')
                dyn = ev.get('dynamic')
            else:
                pitch, duration = ev[0], ev[1]
                kind, bar_off, is_rest, dyn = 'note', 0, False, None
            mi = measure_idx + bar_off
            sy = ys.get(mi, staff_y)
            cx = xs.get(mi, x)
            if dyn:
                dlab = QGraphicsTextItem(str(dyn))
                fnt = QFont("Georgia", 9)
                fnt.setItalic(True)
                dlab.setFont(fnt)
                dlab.setDefaultTextColor(gray)
                dlab.setPos(cx, sy + STAFF_HEIGHT + 2)
                dlab.setZValue(90)
                self.scene.addItem(dlab)
                self._ghost_cand_items.append(dlab)
            if duration is None:
                continue
            self._draw_ghost_note(
                pitch, clef, cx, sy, duration, gray,
                is_rest=bool(is_rest), bucket=self._ghost_cand_items)
            w = step_map.get(duration, 20)
            # 仅音符头小范围可点采纳；空白谱面仍可手写下一音
            if is_rest:
                line_y = sy + 1.5 * STAFF_LINE_SPACING
            elif pitch is not None:
                pos = pitch_to_staff_position(pitch, clef)
                line_y = sy + (4 - pos) * STAFF_LINE_SPACING
            else:
                line_y = sy + STAFF_HEIGHT / 2
            _hit(cand, QRectF(cx - 2, line_y - 10, max(14, w - 4), 20),
                 cand.label)
            xs[mi] = cx + w

    def _draw_ghost_note(self, pitch, clef, x, staff_y, duration, color,
                         is_rest=False, bucket=None):
        dest = self._ghost_items if bucket is None else bucket
        pen = QPen(color, 1.4)
        brush = QBrush(color)
        dur_info = DURATION_FLAGS.get(duration, {"filled": True, "stem": True,
                                                 "flags": 0})
        if is_rest:
            head = QGraphicsEllipseItem(x, staff_y + 1.5 * STAFF_LINE_SPACING - 4,
                                        NOTE_HEAD_WIDTH, NOTE_HEAD_HEIGHT)
            head.setPen(pen)
            head.setBrush(brush)
            head.setZValue(85)
            self.scene.addItem(head)
            dest.append(head)
            return
        pos = pitch_to_staff_position(pitch, clef)
        line_y = staff_y + (4 - pos) * STAFF_LINE_SPACING
        if pos < 0 or pos > 4:
            last = int(pos) if pos == math.floor(pos) else (
                math.ceil(pos) if pos < 0 else math.floor(pos))
            ledger_positions = (range(-1, int(last) - 1, -1) if pos < 0
                                else range(5, int(last) + 1))
            dash = QPen(color, 1)
            for lp in ledger_positions:
                ly = staff_y + (4 - lp) * STAFF_LINE_SPACING
                line = QGraphicsLineItem(x - 4, ly, x + 14, ly)
                line.setPen(dash)
                line.setZValue(84)
                self.scene.addItem(line)
                dest.append(line)
        head = QGraphicsEllipseItem(x, line_y - NOTE_HEAD_HEIGHT / 2,
                                    NOTE_HEAD_WIDTH, NOTE_HEAD_HEIGHT)
        head.setPen(pen)
        head.setBrush(QBrush(Qt.GlobalColor.white) if not dur_info["filled"]
                      else brush)
        head.setZValue(85)
        self.scene.addItem(head)
        dest.append(head)
        if dur_info["stem"]:
            stem_up = not stem_down_for([pos])
            if stem_up:
                tip_y = line_y - 40
                stem = QGraphicsLineItem(x + NOTE_HEAD_WIDTH - 1, line_y,
                                         x + NOTE_HEAD_WIDTH - 1, tip_y)
            else:
                tip_y = line_y + 40
                stem = QGraphicsLineItem(x + 1, line_y, x + 1, tip_y)
            stem.setPen(pen)
            stem.setZValue(85)
            self.scene.addItem(stem)
            dest.append(stem)
            n_flags = int(dur_info.get("flags") or 0)
            if n_flags > 0:
                dest.extend(self._draw_note_flags(
                    x, stem_up, tip_y, n_flags, key=None,
                    pen=pen, brush=brush, z=85))
        if pitch.accidental != Accidental.NATURAL:
            acc_item = QGraphicsTextItem(accidental_symbol(pitch.accidental))
            acc_item.setFont(self.small_font)
            acc_item.setDefaultTextColor(color)
            acc_item.setPos(x - 18, line_y - 10)
            acc_item.setZValue(86)
            self.scene.addItem(acc_item)
            dest.append(acc_item)

    def clear_ghost(self):
        for item in self._ghost_items:
            try:
                self.scene.removeItem(item)
            except RuntimeError:
                pass
        self._ghost_items = []

    def draw_live_keys(self, measure_idx: int, onset_ratio: float, groups):
        """MIDI 键盘按住未提交的键：在两手谱表同一 x 处画橙色和弦预览。

        groups: [{'staff_idx', 'clef', 'pitches'}]，每组是一只手的和弦。
        """
        self.clear_live_keys()
        if not groups:
            return
        color = QColor(255, 140, 26, 210)
        pen = QPen(color, 1.5)
        brush = QBrush(color)
        by_staff = {h['staff_idx']: h for h in self.hit_measures
                    if h.get('measure_idx') == measure_idx}
        if not by_staff:
            return
        # 两手取同一 x，保证上下对齐
        ratio = max(0.0, min(1.0, onset_ratio))
        xs = []
        for h in by_staff.values():
            tm = h.get('time_map')
            if tm:
                xs.append(self._time_map_x(tm, ratio * tm['end']))
                continue
            cx = float(h.get('content_x', h['rect'].left() + 12))
            ce = float(h.get('content_end', h['rect'].right() - 10))
            xs.append(cx + ratio * max(0.0, ce - cx - 16))
        x = max(xs)
        for g in groups:
            h = by_staff.get(g['staff_idx'])
            if h is None:
                continue
            staff_y = float(h['staff_y'])
            clef = g['clef']
            positions = []
            for p in g['pitches']:
                pos = pitch_to_staff_position(p, clef)
                positions.append(pos)
                line_y = staff_y + (4 - pos) * STAFF_LINE_SPACING
                if pos > 4:
                    ledgers = range(5, int(math.floor(pos)) + 1)
                elif pos < 0:
                    ledgers = range(-1, int(math.ceil(pos)) - 1, -1)
                else:
                    ledgers = ()
                for lp in ledgers:
                    ly = staff_y + (4 - lp) * STAFF_LINE_SPACING
                    led = QGraphicsLineItem(x - 4, ly, x + NOTE_HEAD_WIDTH + 4, ly)
                    led.setPen(QPen(color, 1))
                    led.setZValue(94)
                    self.scene.addItem(led)
                    self._live_items.append(led)
                head = QGraphicsEllipseItem(x, line_y - NOTE_HEAD_HEIGHT / 2,
                                            NOTE_HEAD_WIDTH, NOTE_HEAD_HEIGHT)
                head.setPen(pen)
                head.setBrush(brush)
                head.setZValue(95)
                self.scene.addItem(head)
                self._live_items.append(head)
                if p.accidental != Accidental.NATURAL:
                    acc = QGraphicsTextItem(accidental_symbol(p.accidental))
                    acc.setFont(self.small_font)
                    acc.setDefaultTextColor(color)
                    acc.setPos(x - 18, line_y - 10)
                    acc.setZValue(96)
                    self.scene.addItem(acc)
                    self._live_items.append(acc)
            if positions:
                hi_y = staff_y + (4 - max(positions)) * STAFF_LINE_SPACING
                lo_y = staff_y + (4 - min(positions)) * STAFF_LINE_SPACING
                stem_up = not stem_down_for(positions)
                if stem_up:
                    stem = QGraphicsLineItem(x + NOTE_HEAD_WIDTH - 1, lo_y,
                                             x + NOTE_HEAD_WIDTH - 1, hi_y - 34)
                else:
                    stem = QGraphicsLineItem(x + 1, hi_y, x + 1, lo_y + 34)
                stem.setPen(pen)
                stem.setZValue(95)
                self.scene.addItem(stem)
                self._live_items.append(stem)

    def clear_live_keys(self):
        for item in self._live_items:
            try:
                self.scene.removeItem(item)
            except RuntimeError:
                pass
        self._live_items = []

    def clear_ghost_candidates(self):
        for item in self._ghost_cand_items:
            try:
                self.scene.removeItem(item)
            except RuntimeError:
                pass
        self._ghost_cand_items = []
        self.hit_ghost_cands = []

    def set_playback_active(self, keys):
        """Highlight currently playing note/rest symbols with a distinct color."""
        new_active = set(tuple(k) for k in (keys or []))
        if new_active == self._playback_active:
            return
        for key in self._playback_active - new_active:
            self._apply_note_style(key, playing=False)
        for key in new_active - self._playback_active:
            self._apply_note_style(key, playing=True)
        self._playback_active = new_active

    def clear_playback(self):
        self.set_playback_active([])

    def _apply_note_style(self, key, playing: bool):
        entries = self.note_gfx.get(key) or []
        for item, kind, def_brush, def_pen, def_color in entries:
            try:
                if kind == 'ellipse':
                    if playing:
                        if def_brush is not None and def_brush.style() != Qt.NoBrush:
                            c = def_brush.color()
                            if c.red() > 240 and c.green() > 240 and c.blue() > 240:
                                item.setBrush(QBrush(Qt.GlobalColor.white))
                            else:
                                item.setBrush(self._play_fill)
                        else:
                            item.setBrush(self._play_fill)
                        item.setPen(self._play_outline)
                    else:
                        item.setBrush(def_brush)
                        item.setPen(def_pen)
                elif kind == 'line':
                    item.setPen(self._play_outline if playing else def_pen)
                elif kind == 'text':
                    item.setDefaultTextColor(
                        self._play_color if playing else (def_color or QColor(Qt.GlobalColor.black))
                    )
            except RuntimeError:
                pass

    def _register_note_item(self, key, item, kind: str):
        brush = item.brush() if hasattr(item, 'brush') else None
        pen = item.pen() if hasattr(item, 'pen') else None
        color = None
        if kind == 'text':
            color = item.defaultTextColor()
            brush, pen = None, None
        elif kind == 'line':
            brush = None
        self.note_gfx.setdefault(key, []).append((item, kind, brush, pen, color))

    # ------------------------------------------------------------------
    # 整谱排版
    # ------------------------------------------------------------------

    def _effective_states(self, score: Score) -> List[List[dict]]:
        """逐声部逐小节的实际谱号/调号/拍号（曲中换谱号、换调、换拍都在这里展开）。"""
        out: List[List[dict]] = []
        base_ts = score.time_signature
        for staff in score.staves:
            clef = staff.clef
            key = int(getattr(score.key_signature, 'sharps', 0) or 0)
            ts = base_ts
            arr = []
            for mi, m in enumerate(staff.measures):
                clef_changed = key_changed = ts_changed = False
                prev_key = key
                if m.clef is not None and m.clef != clef:
                    clef_changed = mi > 0
                    clef = m.clef
                if m.key_signature is not None:
                    new_key = int(m.key_signature.sharps or 0)
                    if new_key != key:
                        key_changed = mi > 0
                        key = new_key
                if m.time_signature is not None:
                    ts_changed = mi > 0
                    ts = m.time_signature
                arr.append({'clef': clef, 'key': key, 'prev_key': prev_key, 'ts': ts,
                            'clef_changed': clef_changed, 'key_changed': key_changed,
                            'ts_changed': ts_changed})
            out.append(arr)
        return out

    def _eff_at(self, staff_idx: int, mi: int, score: Score) -> dict:
        try:
            return self._eff[staff_idx][mi]
        except (IndexError, TypeError):
            return {'clef': score.staves[staff_idx].clef if staff_idx < len(score.staves)
                    else ClefType.TREBLE,
                    'key': int(score.key_signature.sharps or 0),
                    'prev_key': int(score.key_signature.sharps or 0),
                    'ts': score.time_signature, 'clef_changed': False,
                    'key_changed': False, 'ts_changed': False}

    def render_score(self, score: Score, width: Optional[int] = None):
        saved_m = set(self._selected_measures)
        saved_s = list(self._selected_symbols)
        saved_v = dict(self._violations)
        self.clear()
        self._selected_measures = saved_m
        self._selected_symbols = saved_s
        self._violations = saved_v
        if width is None:
            width = 1200

        usable_width = width - LEFT_MARGIN - RIGHT_MARGIN
        self._sys_right = float(width - RIGHT_MARGIN)
        self._eff = self._effective_states(score)

        title_item = QGraphicsTextItem(score.title)
        title_item.setFont(self.title_font)
        title_item.setPos(width / 2 - title_item.boundingRect().width() / 2, 20)
        self.scene.addItem(title_item)

        if score.composer:
            comp_item = QGraphicsTextItem(score.composer)
            comp_item.setFont(QFont("Segoe UI", 12))
            comp_item.setPos(width - 180, 30)
            self.scene.addItem(comp_item)

        n_measures = max((len(s.measures) for s in score.staves), default=0)

        def _note_count_at(mi: int) -> int:
            n = 0
            for staff in score.staves:
                if mi < len(staff.measures):
                    ms = staff.measures[mi].notes
                    n = max(n, len(ms) + sum(1 for x in ms if x.grace) // 2)
            return n

        def _key_width(sharps: int) -> float:
            return abs(int(sharps or 0)) * 10.0

        def _preamble_pad(mi: int, with_time: bool) -> float:
            """系统首小节谱号/调号/拍号占用的宽度。"""
            e = self._eff_at(0, mi, score) if score.staves else None
            w = float(CLEF_PAD)
            w += max((_key_width(self._eff_at(si, mi, score)['key'])
                      for si in range(len(score.staves))), default=0.0)
            if with_time or (e and e['ts_changed']):
                w += 30
            return w + 12

        def _change_pad(mi: int) -> float:
            """系统中途的换谱号/换调/换拍占用的宽度。"""
            extra = 0.0
            for si in range(len(score.staves)):
                e = self._eff_at(si, mi, score)
                w = 0.0
                if e['clef_changed']:
                    w += 28
                if e['key_changed']:
                    w += _key_width(e['key']) + _key_width(e['prev_key']) + 6
                if e['ts_changed']:
                    w += 30
                extra = max(extra, w)
            return extra

        measure_widths = []
        for mi in range(n_measures):
            note_count = _note_count_at(mi)
            if note_count == 0:
                measure_widths.append(DEFAULT_MEASURE_WIDTH)
            else:
                w = max(MIN_MEASURE_WIDTH, note_count * NOTE_SLOT_WIDTH + ACC_PAD)
                measure_widths.append(min(w + _change_pad(mi), usable_width * 0.65))

        def _widen_system_heads(systems):
            for si, system_measures in enumerate(systems):
                if not system_measures:
                    continue
                mi0 = system_measures[0]
                note_need = max(MIN_MEASURE_WIDTH,
                                _note_count_at(mi0) * NOTE_SLOT_WIDTH + ACC_PAD)
                pad = _preamble_pad(mi0, with_time=(si == 0))
                measure_widths[mi0] = min(
                    max(measure_widths[mi0], note_need + pad),
                    usable_width * 0.85,
                )

        systems = self._pack_systems(measure_widths, usable_width)
        # 系统首小节加谱号/调号/拍号预留，保证音符区仍够宽（否则两音会贴在一起）
        _widen_system_heads(systems)
        # 加宽后可能超行：按新宽度重排一次
        systems = self._pack_systems(measure_widths, usable_width)
        _widen_system_heads(systems)

        y = TOP_MARGIN
        for system_idx, system_measures in enumerate(systems):
            y = self._render_system(
                score, system_measures, measure_widths, y, width, usable_width,
                stretch=True,
                first_system=(system_idx == 0),
                last_system=(system_idx == len(systems) - 1),
            )

        self.scene.setSceneRect(0, 0, width, y + 40)
        self._refresh_highlights()

    def _pack_systems(self, measure_widths: List[float], usable_width: int) -> List[List[int]]:
        systems = []
        current, current_width = [], 0.0
        for idx, mw in enumerate(measure_widths):
            extra = 90 if not current else 0
            if current_width + mw + extra > usable_width and current:
                systems.append(current)
                current, current_width = [idx], mw
            else:
                current.append(idx)
                current_width += mw + extra
        if current:
            systems.append(current)
        return systems or [[]]

    @staticmethod
    def _is_piano_like(score: Score) -> bool:
        if score.staff_groups:
            return True
        if len(score.staves) == 2:
            names = [(s.name or '').lower() for s in score.staves]
            return any('piano' in n or '钢琴' in n or n in ('rh', 'lh') for n in names)
        return False

    def _measure_time_map(self, score: Score, mi: int) -> Optional[dict]:
        """本小节所有声部共用的「时间 → 谱面单位」映射。

        各声部按同一张表摆音符，上下严格对齐；短时值按 0.6 次方压缩、设最小宽度，
        避免按时值严格正比时十六分音符挤成一团；倚音在主音前额外占位。
        """
        onsets: Dict[float, int] = {}
        total = 0.0
        for staff in score.staves:
            if mi >= len(staff.measures):
                continue
            t, g = 0.0, 0
            for n in staff.measures[mi].notes:
                if n.grace:
                    g += 1
                    continue
                k = round(t, 6)
                onsets[k] = max(onsets.get(k, 0), g)
                g = 0
                t += n.duration_value()
            total = max(total, t)
        if not onsets:
            return None
        keys = sorted(onsets)
        end = max(total, keys[-1])
        units: Dict[float, float] = {}
        u = 0.0
        for i, t in enumerate(keys):
            u += onsets[t] * GRACE_UNITS
            units[t] = u
            nxt = keys[i + 1] if i + 1 < len(keys) else end
            d = max(nxt - t, 0.0)
            u += max(0.55, (d / 0.25) ** 0.6) if d > 0 else 0.55
        return {'units': units, 'total': max(u, 1e-6), 'end': end, 'keys': keys}

    @staticmethod
    def _time_map_x(tm: dict, t: float) -> float:
        """hit_measures 里存的映射：按起拍（全音符单位）线性插值出 x。"""
        pts = tm.get('points') or []
        if not pts:
            return float(tm.get('x0', 0.0))
        if t <= pts[0][0]:
            return pts[0][1]
        for (t0, x0), (t1, x1) in zip(pts, pts[1:]):
            if t0 <= t <= t1:
                return x0 if t1 <= t0 else x0 + (x1 - x0) * (t - t0) / (t1 - t0)
        return pts[-1][1]

    def _render_system(self, score: Score, system_measures: List[int],
                       measure_widths: List[float], y: float, width: int,
                       usable_width: int, stretch: bool, first_system: bool,
                       last_system: bool) -> float:
        """同一系统内各声部共用小节宽度，使小节线对齐。返回下一系统的 y。"""
        if not system_measures:
            return y

        total = sum(measure_widths[i] for i in system_measures)
        scale = (usable_width / total) if stretch and total > 0 else 1.0
        staff_gap = staff_gap_for(len(score.staves))
        top_y = y
        time_maps = {mi: self._measure_time_map(score, mi) for mi in system_measures}
        bar_xs: List[Tuple[float, Optional[str]]] = []
        staff_tops: List[float] = []

        for staff_idx, staff in enumerate(score.staves):
            staff_y = y
            staff_tops.append(staff_y)
            for i in range(5):
                line_y = staff_y + i * STAFF_LINE_SPACING
                line = QGraphicsLineItem(LEFT_MARGIN, line_y, width - RIGHT_MARGIN, line_y)
                line.setPen(QPen(Qt.GlobalColor.black, 1))
                self.scene.addItem(line)

            if staff.name:
                # 第一声部的系统首小节号在 x+3，声部名往右让开，避免重叠
                label = QGraphicsTextItem(staff.name)
                label.setFont(QFont("Segoe UI", 10))
                label.setPos(LEFT_MARGIN + (22 if staff_idx == 0 else 0), staff_y - 22)
                self.scene.addItem(label)

            current_x = LEFT_MARGIN
            for pos_in_system, measure_idx in enumerate(system_measures):
                mw = measure_widths[measure_idx] * scale
                is_last = last_system and pos_in_system == len(system_measures) - 1
                if measure_idx < len(staff.measures):
                    m = staff.measures[measure_idx]
                    eff = self._eff_at(staff_idx, measure_idx, score)
                    self._render_measure(
                        m, eff['clef'], score,
                        current_x, staff_y, mw,
                        show_clef_key_time=(pos_in_system == 0),
                        show_time_signature=first_system or eff['ts_changed'],
                        staff_idx=staff_idx,
                        measure_idx=measure_idx,
                        eff=eff,
                        time_map=time_maps.get(measure_idx),
                    )
                    style = m.barline_right or ('final' if is_last else None)
                else:
                    style = 'final' if is_last else None
                current_x += mw
                self._draw_barline(current_x, staff_y, staff_y + STAFF_HEIGHT, style)
                if staff_idx == 0:
                    bar_xs.append((current_x, style))

            y += STAFF_HEIGHT + staff_gap

        bottom = top_y + (len(score.staves) - 1) * (STAFF_HEIGHT + staff_gap) + STAFF_HEIGHT
        # 系统起始线：所有声部连成一条
        sys_line = QGraphicsLineItem(LEFT_MARGIN, top_y, LEFT_MARGIN, bottom)
        sys_line.setPen(QPen(Qt.GlobalColor.black, 1))
        self.scene.addItem(sys_line)

        groups = list(score.staff_groups or [])
        if not groups and self._is_piano_like(score) and len(score.staves) >= 2:
            groups = [{'parts': [0, 1], 'symbol': 'brace'}]
        covered = set()
        for g in groups:
            idxs = sorted(int(i) for i in (g.get('parts') or [])
                          if 0 <= int(i) < len(staff_tops))
            if len(idxs) < 2:
                continue
            covered.update(idxs)
            g_top = staff_tops[idxs[0]]
            g_bot = staff_tops[idxs[-1]] + STAFF_HEIGHT
            if str(g.get('symbol') or 'brace') == 'brace':
                self._draw_brace(g_top, g_bot)
            else:
                self._draw_bracket(g_top, g_bot)
            # 组内小节线贯通（钢琴大谱表惯例）
            for bx, style in bar_xs:
                for a, b in zip(idxs, idxs[1:]):
                    self._draw_barline(bx, staff_tops[a] + STAFF_HEIGHT, staff_tops[b], style,
                                       connector=True)
        if len(score.staves) > 1 and not covered:
            self._draw_bracket(top_y, bottom)

        return y + 36

    def _draw_brace(self, top: float, bot: float):
        brace = QPainterPath()
        brace.moveTo(LEFT_MARGIN - 2, top)
        brace.cubicTo(LEFT_MARGIN - 26, top + 8, LEFT_MARGIN - 26, bot - 8, LEFT_MARGIN - 2, bot)
        item = QGraphicsPathItem(brace)
        item.setPen(QPen(Qt.GlobalColor.black, 1.6))
        self.scene.addItem(item)

    def _draw_bracket(self, top: float, bot: float):
        """合奏/合唱的方括号：粗竖线 + 上下小钩。"""
        x = LEFT_MARGIN - 8
        bar = QGraphicsLineItem(x, top - 2, x, bot + 2)
        bar.setPen(QPen(Qt.GlobalColor.black, 3.5))
        self.scene.addItem(bar)
        for yy, dy in ((top - 2, -5), (bot + 2, 5)):
            path = QPainterPath()
            path.moveTo(x - 1.5, yy)
            path.quadTo(x + 6, yy, x + 10, yy + dy)
            hook = QGraphicsPathItem(path)
            hook.setPen(QPen(Qt.GlobalColor.black, 1.6))
            self.scene.addItem(hook)

    def _draw_barline(self, x: float, top: float, bot: float, style: Optional[str],
                      connector: bool = False):
        """小节线：普通 / 双线 / 终止线 / 反复记号 / 虚线。connector=谱表间贯通段。"""
        black = Qt.GlobalColor.black

        def line(xx, w, dash=False):
            it = QGraphicsLineItem(xx, top, xx, bot)
            pen = QPen(black, w)
            if dash:
                pen.setStyle(Qt.DashLine)
            it.setPen(pen)
            self.scene.addItem(it)

        style = style or 'regular'
        if style == 'double':
            line(x - 4, 1)
            line(x, 1)
        elif style == 'final':
            line(x - 6, 1)
            line(x - 1.5, 4)
        elif style == 'repeat_end':
            line(x - 6, 1)
            line(x - 1.5, 4)
            if not connector:
                for dy in (1.5, 2.5):
                    d = QGraphicsEllipseItem(x - 13, top + dy * STAFF_LINE_SPACING - 2, 4, 4)
                    d.setBrush(QBrush(black))
                    d.setPen(QPen(black, 0.5))
                    self.scene.addItem(d)
        elif style == 'dashed':
            line(x, 1, dash=True)
        else:
            line(x, 1)

    def _draw_repeat_start(self, x: float, y: float):
        black = Qt.GlobalColor.black
        thick = QGraphicsLineItem(x + 1.5, y, x + 1.5, y + STAFF_HEIGHT)
        thick.setPen(QPen(black, 4))
        self.scene.addItem(thick)
        thin = QGraphicsLineItem(x + 6, y, x + 6, y + STAFF_HEIGHT)
        thin.setPen(QPen(black, 1))
        self.scene.addItem(thin)
        for dy in (1.5, 2.5):
            d = QGraphicsEllipseItem(x + 9, y + dy * STAFF_LINE_SPACING - 2, 4, 4)
            d.setBrush(QBrush(black))
            d.setPen(QPen(black, 0.5))
            self.scene.addItem(d)

    # ------------------------------------------------------------------
    # 小节
    # ------------------------------------------------------------------

    def _text(self, text: str, x: float, y: float, font: QFont,
              color: QColor = None, z: float = 20) -> QGraphicsTextItem:
        item = QGraphicsTextItem(text)
        item.setFont(font)
        item.setDefaultTextColor(color or QColor(40, 40, 40))
        item.setPos(x, y)
        item.setZValue(z)
        self.scene.addItem(item)
        return item

    def _render_measure(self, measure: Measure, clef: ClefType, score: Score,
                        x: float, y: float, width: float,
                        show_clef_key_time: bool = False,
                        show_time_signature: bool = True,
                        staff_idx: int = 0,
                        measure_idx: int = 0,
                        eff: Optional[dict] = None,
                        time_map: Optional[dict] = None):
        """Render a single measure."""
        eff = eff or self._eff_at(staff_idx, measure_idx, score)
        clef = eff.get('clef', clef)
        ts = eff.get('ts') or measure.time_signature or score.time_signature
        content_x = x + 10
        n_staves = len(score.staves) if score is not None else 1
        pad = hit_ledger_pad(n_staves)
        hit = {
            'kind': 'measure',
            'staff_idx': staff_idx,
            'measure_idx': measure_idx,
            'staff_y': y,
            'rect': QRectF(x, y - pad, width, STAFF_HEIGHT + 2 * pad),
            'content_x': content_x,
            'content_end': x + width - 10,
        }
        self.hit_measures.append(hit)

        # 小节编号（仅最上方声部）：1-based，便于对话指定「第 n 小节」；
        # 弱起小节是第 0 小节、不标号，第一个完整小节记作 1（与 MusicXML/spec 一致）
        pickup = bool(score is not None and score.staves and score.staves[0].measures
                      and score.staves[0].measures[0].implicit)
        bar_no = measure_idx if pickup else measure_idx + 1
        if staff_idx == 0 and bar_no > 0:
            num_font = QFont("Segoe UI", 9)
            num_font.setBold(True)
            self._text(str(bar_no), x + 3, y - 22, num_font,
                       QColor(70, 90, 130))
        if staff_idx == 0:
            if measure.rehearsal:
                rf = QFont("Segoe UI", 12)
                rf.setBold(True)
                item = self._text(str(measure.rehearsal), x + 2, y - 72, rf, QColor(0, 0, 0))
                br = item.boundingRect()
                m = item.document().documentMargin()
                box = QGraphicsRectItem(item.x() + m - 3, item.y() + m - 1,
                                        br.width() - 2 * m + 6, br.height() - 2 * m + 2)
                box.setPen(QPen(Qt.GlobalColor.black, 1.4))
                box.setZValue(19)
                self.scene.addItem(box)
            self._draw_volta(measure, x, y, width)

        # 力度记号（小节起始）：谱表下方，斜体粗体，音乐排版惯例
        if measure.dynamic:
            self._text(str(measure.dynamic), content_x - 2, y + STAFF_HEIGHT + 4,
                       self._dyn_font())

        # 速度标记（小节起始）：最上方声部的谱表上方，粗斜体
        if staff_idx == 0 and measure.tempo_mark:
            tm_font = QFont("Georgia", 12)
            tm_font.setBold(True)
            tm_font.setItalic(True)
            self._text(measure.tempo_mark, x + 2, y - 46, tm_font)
        # 文字指示（dolce / D.C. al Fine …）：谱表上方斜体，与速度分行
        if measure.text:
            tf = QFont("Georgia", 11)
            tf.setItalic(True)
            tx = x + 2 + (34 if (staff_idx == 0 and measure.rehearsal) else 0)
            self._text(str(measure.text), tx, y - (30 if staff_idx else 68), tf)

        if show_clef_key_time:
            self._draw_clef(clef, x + 8, y)
            content_x = x + 48
            content_x = self._draw_key_signature(eff['key'], clef, content_x, y)
            if show_time_signature and ts is not None:
                content_x = self._draw_time_signature(ts, content_x, y)
        else:
            if eff.get('clef_changed'):
                self._draw_clef(clef, content_x - 4, y, small=True)
                content_x += 24
            if eff.get('key_changed'):
                content_x = self._draw_key_signature(eff['key'], clef, content_x, y,
                                                     prev=eff.get('prev_key'))
                content_x += 4
            if eff.get('ts_changed') and ts is not None:
                content_x = self._draw_time_signature(ts, content_x, y)
        if measure.barline_left == 'repeat_start':
            # 系统首：放在谱号调号之后；系统中：就压在小节线上
            self._draw_repeat_start(content_x if show_clef_key_time else x, y)
            content_x += 16 if show_clef_key_time else 12
        hit['content_x'] = content_x

        if not measure.notes:
            return

        content_x += ACC_PAD
        end_x = x + width - 10
        available = max(end_x - content_x, len(measure.notes) * 20.0)

        # ---- 每个音符的横坐标（共享时间映射） ----
        tm = time_map or self._measure_time_map(
            Score(staves=[Staff(clef=clef, measures=[measure])]), 0)
        xs: List[float] = []
        t = 0.0
        grace_run: List[int] = []
        for i, n in enumerate(measure.notes):
            if n.grace:
                grace_run.append(i)
                xs.append(0.0)
                continue
            k = round(t, 6)
            u = tm['units'].get(k) if tm else None
            if u is None:
                u = (t / tm['end'] * tm['total']) if tm and tm['end'] > 0 else 0.0
            nx = content_x + u / tm['total'] * available if tm else content_x
            xs.append(nx)
            for j, gi in enumerate(reversed(grace_run)):
                xs[gi] = nx - (j + 1) * (GRACE_UNITS / tm['total'] * available if tm else 12)
            grace_run = []
            t += n.duration_value()
        for j, gi in enumerate(reversed(grace_run)):
            xs[gi] = end_x - (j + 1) * 12
        if tm:
            pts = sorted((kk, content_x + uu / tm['total'] * available)
                         for kk, uu in tm['units'].items())
            pts.append((tm['end'], end_x))
            hit['time_map'] = {'points': pts, 'end': tm['end'], 'x0': content_x}

        # ---- 变音记号：按调号 + 本小节已出现的变音 ----
        key_alter = {nm: acc.value for nm, acc in KeySignature(eff['key']).accidentals()}
        state: dict = {}
        acc_lists: List[Optional[list]] = []
        for n in measure.notes:
            if n.is_rest:
                acc_lists.append(None)
                continue
            acc_lists.append(displayed_accidentals(
                [n.pitch] + list(n.chord_tones), state, key_alter, tied=n.tie_end))

        # ---- 符杠分组 ----
        beam_plan = self._plan_beams(measure, ts, clef, xs, y)

        bar_len = ts.beats_per_measure() / 4.0 if ts else 1.0
        solo_rest = (len(measure.notes) == 1 and measure.notes[0].is_rest
                     and abs(measure.notes[0].duration_value() - bar_len) < 1e-6)

        infos: List[Optional[dict]] = []
        for note_idx, note in enumerate(measure.notes):
            bp = beam_plan.get(note_idx)
            nx = xs[note_idx]
            if solo_rest:
                nx = content_x + available / 2.0 - 6
            infos.append(self._render_note(
                note, clef, nx, y, 0.0,
                staff_idx=staff_idx, measure_idx=measure_idx, note_idx=note_idx,
                acc_show=acc_lists[note_idx],
                stem_up=bp['stem_up'] if bp else None,
                stem_tip=bp['tip'] if bp else None,
                beamed=bp is not None,
                full_bar_rest=solo_rest,
            ))
        self._draw_beams(measure, beam_plan, infos)
        self._draw_tuplets(measure, infos, y)

    def _dyn_font(self) -> QFont:
        f = QFont("Georgia", 13)
        f.setBold(True)
        f.setItalic(True)
        return f

    def _draw_volta(self, measure: Measure, x: float, y: float, width: float):
        """反复房子：同号连续小节共用一个括号，首小节标号并下钩。"""
        v = measure.volta
        if not v:
            self._volta_prev = None
            return
        vy = y - 36
        first = (v != self._volta_prev)
        line = QGraphicsLineItem(x + (2 if first else 0), vy, x + width - 4, vy)
        line.setPen(QPen(Qt.GlobalColor.black, 1.2))
        self.scene.addItem(line)
        if first:
            hook = QGraphicsLineItem(x + 2, vy, x + 2, vy + 14)
            hook.setPen(QPen(Qt.GlobalColor.black, 1.2))
            self.scene.addItem(hook)
            f = QFont("Segoe UI", 10)
            f.setBold(True)
            self._text(f'{v}.', x + 4, vy - 2, f, QColor(0, 0, 0))
        self._volta_prev = v

    # ------------------------------------------------------------------
    # 谱号 / 调号 / 拍号
    # ------------------------------------------------------------------

    def _draw_key_signature(self, sharps: int, clef: ClefType, x: float, y: float,
                            prev: Optional[int] = None) -> float:
        """调号（换调时先画还原号取消旧调里不再需要的升降）。返回新的 x。"""
        sharps = int(sharps or 0)
        if prev is not None:
            prev = int(prev or 0)
            old = KeySignature(prev).accidentals()
            new_names = {nm for nm, _a in KeySignature(sharps).accidentals()}
            same_side = (prev > 0) == (sharps > 0) and sharps != 0
            cancel = [(nm, a) for nm, a in old if not (same_side and nm in new_names)]
            if cancel:
                table = KEY_SIG_POSITIONS_SHARP if prev > 0 else KEY_SIG_POSITIONS_FLAT
                positions = table.get(clef, table[ClefType.TREBLE])
                for nm, _a in cancel:
                    pos = positions.get(nm, 0)
                    self._text("♮", x, y + (4 - pos) * STAFF_LINE_SPACING - 12,
                               self.small_font, QColor(0, 0, 0), z=1)
                    x += 10
                x += 4
        if sharps != 0:
            table = KEY_SIG_POSITIONS_SHARP if sharps > 0 else KEY_SIG_POSITIONS_FLAT
            positions = table.get(clef, table[ClefType.TREBLE])
            for note_name, acc in KeySignature(sharps).accidentals():
                pos = positions.get(note_name, 0)
                # 降号的"肚子"在字形下半部，需上移才能落在线/间上
                dy = -17 if sharps < 0 else -12
                self._text(accidental_symbol(acc), x, y + (4 - pos) * STAFF_LINE_SPACING + dy,
                           self.small_font, QColor(0, 0, 0), z=1)
                x += 10
            x += 4
        return x

    def _draw_time_signature(self, ts: TimeSignature, x: float, y: float) -> float:
        """拍号：数字，或 C（common）/ ¢（cut）。返回新的 x。"""
        sym = getattr(ts, 'symbol', None)
        if sym in ('common', 'cut'):
            f = QFont("Georgia", 22)
            f.setBold(True)
            item = self._text("C", x - 2, y + 2, f, QColor(0, 0, 0), z=1)
            if sym == 'cut':
                cx = x - 2 + item.boundingRect().width() / 2.0
                bar = QGraphicsLineItem(cx, y - 6, cx, y + STAFF_HEIGHT + 6)
                bar.setPen(QPen(Qt.GlobalColor.black, 1.6))
                self.scene.addItem(bar)
            return x + 30
        f = QFont("Segoe UI", 14)
        f.setBold(True)
        self._text(str(ts.numerator), x, y - 3, f, QColor(0, 0, 0), z=1)
        self._text(str(ts.denominator), x, y + STAFF_LINE_SPACING * 2 - 3, f,
                   QColor(0, 0, 0), z=1)
        return x + 30

    def _draw_clef(self, clef: ClefType, x: float, y: float, small: bool = False):
        """矢量谱号（不依赖 Unicode 音乐符号字体）：G / F / C（中音、次中音）/ 带 8。"""
        sp = STAFF_LINE_SPACING
        s = 0.75 if small else 1.0
        pen_w = 2.2 * s
        if clef in (ClefType.BASS, ClefType.BASS_8VB):
            # F 谱号：绕 F3 线（自上第 2 线）的粗点钩 + 两侧圆点
            fy = y + sp
            path = QPainterPath()
            path.moveTo(x + 6 * s, fy - 1.2 * sp * s)
            path.cubicTo(x + 22 * s, fy - 1.6 * sp * s, x + 26 * s, fy + 0.2 * sp * s,
                         x + 14 * s, fy + 0.9 * sp * s)
            path.cubicTo(x + 6 * s, fy + 1.4 * sp * s, x + 4 * s, fy + 0.2 * sp * s,
                         x + 12 * s, fy - 0.15 * sp * s)
            hook = QGraphicsPathItem(path)
            hook.setPen(QPen(Qt.GlobalColor.black, 2.4 * s, Qt.SolidLine,
                             Qt.RoundCap, Qt.RoundJoin))
            hook.setBrush(QBrush(Qt.NoBrush))
            self.scene.addItem(hook)
            for dy in (-0.55 * sp, 0.55 * sp):
                d = QGraphicsEllipseItem(x + 22 * s, fy + dy - 2.2, 4.4 * s, 4.4 * s)
                d.setBrush(QBrush(Qt.GlobalColor.black))
                d.setPen(QPen(Qt.GlobalColor.black, 0.5))
                self.scene.addItem(d)
            if clef == ClefType.BASS_8VB:
                self._text("8", x + 6 * s, y + STAFF_HEIGHT - 2, QFont("Georgia", 9),
                           QColor(0, 0, 0), z=1)
            return
        if clef in (ClefType.ALTO, ClefType.TENOR):
            # C 谱号：中心对准 C4 线（中音=第 3 线，次中音=第 4 线）
            cy = y + (2 if clef == ClefType.ALTO else 1) * sp
            path = QPainterPath()
            path.moveTo(x + 4, cy - 1.6 * sp * s)
            path.lineTo(x + 4, cy + 1.6 * sp * s)
            path.moveTo(x + 4, cy - 1.2 * sp * s)
            path.cubicTo(x + 18 * s, cy - 1.6 * sp * s, x + 18 * s, cy - 0.2 * sp * s, x + 8, cy)
            path.cubicTo(x + 18 * s, cy + 0.2 * sp * s, x + 18 * s, cy + 1.6 * sp * s,
                         x + 4, cy + 1.2 * sp * s)
            item = QGraphicsPathItem(path)
            item.setPen(QPen(Qt.GlobalColor.black, pen_w, Qt.SolidLine,
                             Qt.RoundCap, Qt.RoundJoin))
            item.setBrush(QBrush(Qt.NoBrush))
            self.scene.addItem(item)
            return
        # 高音谱号（G 谱号）：螺旋绕过 G4 线（自下第 2 线 = y+3sp）
        gy = y + 3 * sp
        path = QPainterPath()

        def px(v):
            return x + v * s

        def py(v):
            return gy + (v - gy) * s

        path.moveTo(px(10), py(y + 4.6 * sp))
        path.lineTo(px(10), py(y + 0.2 * sp))
        path.cubicTo(px(22), py(y - 0.3 * sp), px(26), py(y + 1.2 * sp), px(14), py(y + 1.8 * sp))
        path.cubicTo(px(4), py(y + 2.3 * sp), px(2), py(y + 3.4 * sp), px(12), py(gy))
        path.cubicTo(px(22), py(gy + 0.5 * sp), px(18), py(gy + 1.4 * sp), px(8), py(gy + 1.1 * sp))
        path.cubicTo(px(2), py(gy + 0.9 * sp), px(4), py(gy - 0.1 * sp), px(12), py(gy - 0.15 * sp))
        gclef = QGraphicsPathItem(path)
        gclef.setPen(QPen(Qt.GlobalColor.black, pen_w, Qt.SolidLine,
                          Qt.RoundCap, Qt.RoundJoin))
        gclef.setBrush(QBrush(Qt.NoBrush))
        self.scene.addItem(gclef)
        ball = QGraphicsEllipseItem(px(7.5), py(y + 4.4 * sp), 5 * s, 5 * s)
        ball.setBrush(QBrush(Qt.GlobalColor.black))
        ball.setPen(QPen(Qt.GlobalColor.black, 0.4))
        self.scene.addItem(ball)
        if clef == ClefType.TREBLE_8VB:
            self._text("8", px(5), py(y + 4.6 * sp) - 2, QFont("Georgia", 9),
                       QColor(0, 0, 0), z=1)

    # ------------------------------------------------------------------
    # 符杠 / 连音括号
    # ------------------------------------------------------------------

    def _plan_beams(self, measure: Measure, ts, clef: ClefType, xs: List[float],
                    y: float) -> Dict[int, dict]:
        """八分及更短的有音音符按拍分组连杠；返回 {note_idx: {stem_up, tip, group}}。"""
        unit = beam_unit(ts)
        beamable_durs = (NoteDuration.EIGHTH, NoteDuration.SIXTEENTH,
                         NoteDuration.THIRTY_SECOND)
        groups: List[List[int]] = []
        cur: List[int] = []
        cur_unit = None
        t = 0.0
        for i, n in enumerate(measure.notes):
            if n.grace:
                continue
            d = n.duration_value()
            u0 = int((t + 1e-9) // unit)
            u1 = int((t + d - 1e-9) // unit)
            ok = (not n.is_rest) and n.duration in beamable_durs and u0 == u1
            if ok and cur and cur_unit == u0:
                cur.append(i)
            else:
                if len(cur) >= 2:
                    groups.append(cur)
                cur = [i] if ok else []
                cur_unit = u0 if ok else None
            t += d
        if len(cur) >= 2:
            groups.append(cur)

        plan: Dict[int, dict] = {}
        sp = STAFF_LINE_SPACING
        for gi, idxs in enumerate(groups):
            pos_all = []
            per = []
            for i in idxs:
                n = measure.notes[i]
                ps = [pitch_to_staff_position(p, clef) for p in [n.pitch] + list(n.chord_tones)]
                pos_all.extend(ps)
                per.append((i, max(ps), min(ps)))
            stem_up = not stem_down_for(pos_all)
            pts = []
            for i, hi, lo in per:
                if stem_up:
                    sx = xs[i] + NOTE_HEAD_WIDTH - 1
                    ext = y + (4 - hi) * sp
                else:
                    sx = xs[i] + 1
                    ext = y + (4 - lo) * sp
                pts.append((i, sx, ext))
            sx0, sx1 = pts[0][1], pts[-1][1]
            t0 = pts[0][2] + (-STEM_LEN if stem_up else STEM_LEN)
            t1 = pts[-1][2] + (-STEM_LEN if stem_up else STEM_LEN)
            slope = max(-sp, min(sp, t1 - t0))
            span = (sx1 - sx0) or 1.0

            def beam_y(sx):
                return t0 + slope * (sx - sx0) / span

            shift = 0.0
            for i, sx, ext in pts:
                by = beam_y(sx)
                if stem_up:
                    shift = max(shift, by - (ext - 25))
                else:
                    shift = max(shift, (ext + 25) - by)
            for i, sx, ext in pts:
                tip = beam_y(sx) + (-shift if stem_up else shift)
                plan[i] = {'stem_up': stem_up, 'tip': tip, 'group': gi}
        return plan

    def _draw_beams(self, measure: Measure, plan: Dict[int, dict], infos):
        if not plan:
            return
        by_group: Dict[int, List[int]] = {}
        for i, p in plan.items():
            by_group.setdefault(p['group'], []).append(i)
        levels = {NoteDuration.EIGHTH: 1, NoteDuration.SIXTEENTH: 2,
                  NoteDuration.THIRTY_SECOND: 3}
        for idxs in by_group.values():
            idxs.sort()
            pts = []
            for i in idxs:
                info = infos[i] if i < len(infos) else None
                if not info or info.get('stem_x') is None:
                    continue
                pts.append((i, info['stem_x'], info['tip_y'], info['stem_up']))
            if len(pts) < 2:
                continue
            up = pts[0][3]
            direction = 1 if up else -1   # 次级符杠朝符头方向排

            def seg(xa, ya, xb, yb, level):
                off = (level - 1) * BEAM_GAP * direction
                th = BEAM_THICK if up else -BEAM_THICK
                path = QPainterPath()
                path.moveTo(xa, ya + off)
                path.lineTo(xb, yb + off)
                path.lineTo(xb, yb + off + th)
                path.lineTo(xa, ya + off + th)
                path.closeSubpath()
                item = QGraphicsPathItem(path)
                item.setBrush(QBrush(Qt.GlobalColor.black))
                item.setPen(QPen(Qt.GlobalColor.black, 0.5))
                self.scene.addItem(item)
                for k in (pts[0][0],):
                    self._register_note_item((None, None, None), item, 'shape') if False else None
                return item

            seg(pts[0][1], pts[0][2], pts[-1][1], pts[-1][2], 1)
            lv = [levels.get(measure.notes[i].duration, 1) for i, *_ in pts]
            for level in (2, 3):
                k = 0
                while k < len(pts):
                    if lv[k] < level:
                        k += 1
                        continue
                    j = k
                    while j + 1 < len(pts) and lv[j + 1] >= level:
                        j += 1
                    if j > k:
                        seg(pts[k][1], pts[k][2], pts[j][1], pts[j][2], level)
                    else:
                        # 单个短音：朝相邻音画半截符杠
                        xa, ya = pts[k][1], pts[k][2]
                        if k + 1 < len(pts):
                            xb = xa + 9
                            yb = ya + (pts[k + 1][2] - ya) * 9 / max(1.0, pts[k + 1][1] - xa)
                        else:
                            xb = xa - 9
                            yb = ya - (ya - pts[k - 1][2]) * 9 / max(1.0, xa - pts[k - 1][1])
                        seg(xa, ya, xb, yb, level)
                    k = j + 1

    def _draw_tuplets(self, measure: Measure, infos, y: float):
        """连音：组首到组尾画括号与数字（已连杠的只标数字）。"""
        start = None
        for i, n in enumerate(measure.notes):
            if not n.tuplet:
                start = None
                continue
            if n.tuplet_start or start is None:
                start = i
            if not (n.tuplet_end or i == len(measure.notes) - 1
                    or not measure.notes[i + 1].tuplet
                    or measure.notes[i + 1].tuplet_start):
                continue
            group = [k for k in range(start, i + 1)
                     if k < len(infos) and infos[k] is not None]
            start = None
            if not group:
                continue
            x0 = infos[group[0]]['x'] - 2
            x1 = infos[group[-1]]['x'] + NOTE_HEAD_WIDTH + 2
            tops = [infos[k]['top'] for k in group]
            by = min(min(tops) - 14, y - 10)
            label = str(n.tuplet[0])
            f = QFont("Georgia", 10)
            f.setItalic(True)
            f.setBold(True)
            mid = (x0 + x1) / 2.0
            self._text(label, mid - 5, by - 11, f, QColor(0, 0, 0))
            beamed = all(infos[k].get('beamed') for k in group)
            if not beamed:
                pen = QPen(Qt.GlobalColor.black, 1)
                for xa, xb in ((x0, mid - 7), (mid + 7, x1)):
                    ln = QGraphicsLineItem(xa, by, xb, by)
                    ln.setPen(pen)
                    self.scene.addItem(ln)
                for xx in (x0, x1):
                    hk = QGraphicsLineItem(xx, by, xx, by + 5)
                    hk.setPen(pen)
                    self.scene.addItem(hk)

    # ------------------------------------------------------------------
    # 音符
    # ------------------------------------------------------------------

    def _draw_accidental(self, acc: Accidental, x: float, line_y: float, scale: float,
                         key, color: QColor = None):
        """变音记号：重升用矢量 ×（macOS 常缺 𝄪 字形），重降画两个 ♭。"""
        if acc == Accidental.DOUBLE_SHARP:
            r = 3.2 * scale
            path = QPainterPath()
            cx = x + 8
            path.moveTo(cx - r, line_y - r)
            path.lineTo(cx + r, line_y + r)
            path.moveTo(cx + r, line_y - r)
            path.lineTo(cx - r, line_y + r)
            item = QGraphicsPathItem(path)
            item.setPen(QPen(color or Qt.GlobalColor.black, 2.0 * scale, Qt.SolidLine, Qt.RoundCap))
            self.scene.addItem(item)
            self._register_note_item(key, item, 'shape')
            return
        sym = "♭♭" if acc == Accidental.DOUBLE_FLAT else accidental_symbol(acc)
        f = QFont(self.small_font)
        if scale < 1.0:
            f.setPointSizeF(self.small_font.pointSizeF() * 0.8)
        item = QGraphicsTextItem(sym)
        item.setFont(f)
        if color is not None:
            item.setDefaultTextColor(color)
        dx = -6 if acc == Accidental.DOUBLE_FLAT else 0
        dy = 16 if acc in (Accidental.FLAT, Accidental.DOUBLE_FLAT) else 11
        item.setPos(x + dx, line_y - dy * scale)
        self.scene.addItem(item)
        self._register_note_item(key, item, 'text')

    def _render_note(self, note: Note, clef: ClefType, x: float, y: float, width: float,
                     staff_idx: int = 0, measure_idx: int = 0, note_idx: int = 0,
                     acc_show: Optional[list] = None, stem_up: Optional[bool] = None,
                     stem_tip: Optional[float] = None, beamed: bool = False,
                     full_bar_rest: bool = False) -> Optional[dict]:
        key = (staff_idx, measure_idx, note_idx)
        hit_pad = max(18.0, min(width or 28.0, 36.0))
        vpad = HIT_LEDGER_PAD
        for h in reversed(self.hit_measures):
            if h.get('staff_idx') == staff_idx and h.get('measure_idx') == measure_idx:
                vpad = (h['rect'].height() - STAFF_HEIGHT) / 2.0
                break
        self.hit_notes.append({
            'kind': 'note',
            'staff_idx': staff_idx,
            'measure_idx': measure_idx,
            'note_idx': note_idx,
            'staff_y': y,
            'rect': QRectF(x - 4, y - vpad, hit_pad, STAFF_HEIGHT + 2 * vpad),
        })
        self._render_note_attachments(note, x, y, staff_idx)
        if note.is_rest:
            self._render_rest(note, x, y, width, key=key, full_bar=full_bar_rest)
            return {'x': x, 'top': y + STAFF_LINE_SPACING, 'stem_x': None,
                    'tip_y': None, 'stem_up': True, 'beamed': False}

        sp = STAFF_LINE_SPACING
        scale = GRACE_SCALE if note.grace else 1.0
        hw, hh = NOTE_HEAD_WIDTH * scale, NOTE_HEAD_HEIGHT * scale
        pitches = [note.pitch] + list(note.chord_tones)
        dur_info = DURATION_FLAGS.get(note.duration, {"filled": True, "stem": True, "flags": 0})
        positions = [pitch_to_staff_position(p, clef) for p in pitches]
        if stem_up is None:
            stem_up = not stem_down_for(positions) or note.grace

        # 二度叠写：符干朝上时较高音右移，朝下时较低音左移（主列始终贴符干）
        n_p = len(pitches)
        x_off = [0.0] * n_p
        if stem_up:
            order = sorted(range(n_p), key=lambda i: positions[i])
            shifted = False
            for k in range(1, n_p):
                a, b = order[k - 1], order[k]
                if positions[b] - positions[a] <= 0.5 + 1e-6 and not shifted:
                    x_off[b] = hw - 1
                    shifted = True
                else:
                    shifted = False
        else:
            order = sorted(range(n_p), key=lambda i: -positions[i])
            shifted = False
            for k in range(1, n_p):
                a, b = order[k - 1], order[k]
                if positions[a] - positions[b] <= 0.5 + 1e-6 and not shifted:
                    x_off[b] = -(hw - 1)
                    shifted = True
                else:
                    shifted = False

        line_ys = []
        for i, p in enumerate(pitches):
            pos = positions[i]
            line_y = y + (4 - pos) * sp
            line_ys.append(line_y)
            hx = x + x_off[i]
            if pos < 0 or pos > 4:
                if pos < 0:
                    last = int(pos) if pos == math.floor(pos) else math.ceil(pos)
                    ledger_positions = range(-1, int(last) - 1, -1)
                else:
                    last = int(pos) if pos == math.floor(pos) else math.floor(pos)
                    ledger_positions = range(5, int(last) + 1)
                for lp in ledger_positions:
                    ly = y + (4 - lp) * sp
                    line = QGraphicsLineItem(hx - 4 * scale, ly, hx + hw + 4 * scale, ly)
                    line.setPen(QPen(Qt.GlobalColor.black, 1))
                    self.scene.addItem(line)
            head = QGraphicsEllipseItem(hx, line_y - hh / 2, hw, hh)
            head.setBrush(QBrush(Qt.GlobalColor.black if dur_info["filled"] else Qt.GlobalColor.white))
            head.setPen(QPen(Qt.GlobalColor.black, 1))
            self.scene.addItem(head)
            self._register_note_item(key, head, 'ellipse')

        # 变音记号：按需显示（含还原号）；和弦内相距不足六度的错列避免重叠
        if acc_show is None:
            acc_show = [p.accidental if p.accidental != Accidental.NATURAL else None
                        for p in pitches]
        left_edge = x + min(x_off)
        cols: List[List[float]] = []
        for i in sorted(range(n_p), key=lambda i: -positions[i]):
            acc = acc_show[i] if i < len(acc_show) else None
            if acc is None:
                continue
            col = 0
            while col < len(cols) and any(abs(positions[i] - q) < 3.0 for q in cols[col]):
                col += 1
            if col == len(cols):
                cols.append([])
            cols[col].append(positions[i])
            self._draw_accidental(acc, left_edge - (14 + 9 * col) * scale, line_ys[i], scale, key)

        top_y, bot_y = min(line_ys), max(line_ys)
        stem_x = tip_y = None
        if dur_info["stem"]:
            mid_y = y + 2 * sp
            if stem_up:
                stem_x = x + hw - 1
                tip_y = stem_tip if stem_tip is not None else min(top_y - STEM_LEN * scale,
                                                                  mid_y if not note.grace else 1e9)
                stem = QGraphicsLineItem(stem_x, bot_y, stem_x, tip_y)
            else:
                stem_x = x + 1
                tip_y = stem_tip if stem_tip is not None else max(bot_y + STEM_LEN * scale, mid_y)
                stem = QGraphicsLineItem(stem_x, top_y, stem_x, tip_y)
            stem.setPen(QPen(Qt.GlobalColor.black, 1))
            self.scene.addItem(stem)
            self._register_note_item(key, stem, 'line')
            n_flags = int(dur_info.get("flags") or 0)
            if n_flags > 0 and not beamed:
                self._draw_note_flags(x, stem_up, tip_y, n_flags, key=key,
                                      head_w=hw, scale=scale)
            if note.grace and n_flags > 0 and not beamed:
                # 短倚音：符干上的斜杠
                sl = QGraphicsLineItem(stem_x - 5, tip_y + 12, stem_x + 6, tip_y + 4)
                sl.setPen(QPen(Qt.GlobalColor.black, 1))
                self.scene.addItem(sl)

        # 附点：线上音的附点挪到上方的间里；和弦每个音都点
        dot_x0 = x + max(x_off) + hw + 4
        seen_rows = set()
        for i, pos in enumerate(positions):
            dy = line_ys[i] - (sp / 2 if abs(pos - round(pos)) < 1e-6 else 0)
            row = round(dy, 1)
            if row in seen_rows:
                continue
            seen_rows.add(row)
            for d in range(note.dots):
                dot = QGraphicsEllipseItem(dot_x0 + d * 6, dy - 1.6, 3.2, 3.2)
                dot.setBrush(QBrush(Qt.GlobalColor.black))
                self.scene.addItem(dot)
                self._register_note_item(key, dot, 'ellipse')

        self._render_marks(note, x, y, top_y, bot_y, stem_up, tip_y, key,
                           left_edge - 9 * len(cols))
        self._render_ties(note, pitches, positions, line_ys, x, hw, y, staff_idx, stem_up)
        self._render_slur(note, x, line_ys, y, staff_idx, stem_up)

        top = min(top_y - hh, tip_y if (stem_up and tip_y is not None) else top_y)
        return {'x': x, 'top': top, 'stem_x': stem_x, 'tip_y': tip_y,
                'stem_up': stem_up, 'beamed': beamed}

    def _render_note_attachments(self, note: Note, x: float, y: float, staff_idx: int):
        """音符级文字：中途力度、渐强渐弱、歌词、和弦记号。"""
        if note.dynamic:
            self._text(str(note.dynamic), x - 4, y + STAFF_HEIGHT + 4, self._dyn_font())
        if note.chord_symbol:
            f = QFont("Segoe UI", 11)
            f.setBold(True)
            self._text(str(note.chord_symbol), x - 4, y - 34, f, QColor(20, 20, 20))
        if note.lyric:
            self._text(str(note.lyric), x - 4, y + STAFF_HEIGHT + 28, QFont("Segoe UI", 10),
                       QColor(20, 20, 20))
        hy = y + STAFF_HEIGHT + 24
        if note.hairpin_end and staff_idx in self._hairpin_pending:
            sx, kind, sys_y = self._hairpin_pending.pop(staff_idx)
            ex = x + NOTE_HEAD_WIDTH
            if abs(sys_y - y) < 1:
                self._draw_hairpin(sx, ex, hy, kind)
            else:
                # 跨行：前一行画到行尾，新行从行首续画
                self._draw_hairpin(sx, self._sys_right - 4, sys_y + STAFF_HEIGHT + 24, kind,
                                   open_end=True)
                self._draw_hairpin(LEFT_MARGIN + 50, ex, hy, kind, open_start=True)
        if note.hairpin_start:
            self._hairpin_pending[staff_idx] = (x, note.hairpin_start, y)

    def _draw_hairpin(self, x0: float, x1: float, yc: float, kind: str,
                      open_start: bool = False, open_end: bool = False):
        if x1 - x0 < 6:
            x1 = x0 + 24
        h = 5.0
        cresc = str(kind).startswith('cresc')
        a0 = (h * 0.5 if open_start else 0.0) if cresc else h
        a1 = h if cresc else (h * 0.5 if open_end else 0.0)
        pen = QPen(Qt.GlobalColor.black, 1.1)
        for sgn in (-1, 1):
            ln = QGraphicsLineItem(x0, yc + sgn * a0, x1, yc + sgn * a1)
            ln.setPen(pen)
            self.scene.addItem(ln)

    def _render_marks(self, note: Note, x: float, y: float, top_y: float, bot_y: float,
                      stem_up: bool, tip_y: Optional[float], key, left_x: float):
        """演奏法（符头一侧）、延长号/装饰音（谱表上方）、琶音、换气记号。"""
        if not note.marks:
            return
        black = Qt.GlobalColor.black
        sp = STAFF_LINE_SPACING
        cx = x + NOTE_HEAD_WIDTH / 2
        # 演奏法放在符头一侧（符干对侧）
        above = not stem_up
        near = (top_y - 11) if above else (bot_y + 11)
        step = -9 if above else 9
        k = 0
        # 延长号/装饰音在谱表与符干之上
        hi = min(y - 10, top_y - 14, (tip_y - 8) if (stem_up and tip_y is not None) else 1e9)
        orn_k = 0
        for mk in note.marks:
            if mk in ('staccato', 'staccatissimo', 'accent', 'tenuto', 'marcato'):
                my = near + step * k
                k += 1
                if mk == 'staccato':
                    dot = QGraphicsEllipseItem(cx - 2, my - 2, 4, 4)
                    dot.setBrush(QBrush(black))
                    dot.setPen(QPen(black, 0.5))
                    self.scene.addItem(dot)
                    self._register_note_item(key, dot, 'ellipse')
                elif mk == 'staccatissimo':
                    path = QPainterPath()
                    tip = my + (3 if above else -3)
                    base = my - (3 if above else -3)
                    path.moveTo(cx - 2.2, base)
                    path.lineTo(cx + 2.2, base)
                    path.lineTo(cx, tip)
                    path.closeSubpath()
                    it = QGraphicsPathItem(path)
                    it.setBrush(QBrush(black))
                    it.setPen(QPen(black, 0.6))
                    self.scene.addItem(it)
                    self._register_note_item(key, it, 'shape')
                elif mk == 'accent':
                    path = QPainterPath()
                    path.moveTo(x - 3, my - 4)
                    path.lineTo(x + 9, my)
                    path.lineTo(x - 3, my + 4)
                    it = QGraphicsPathItem(path)
                    it.setPen(QPen(black, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    it.setBrush(QBrush(Qt.NoBrush))
                    self.scene.addItem(it)
                    self._register_note_item(key, it, 'shape')
                elif mk == 'tenuto':
                    line = QGraphicsLineItem(x - 1, my, x + 11, my)
                    line.setPen(QPen(black, 2.0, Qt.SolidLine, Qt.RoundCap))
                    self.scene.addItem(line)
                    self._register_note_item(key, line, 'line')
                elif mk == 'marcato':
                    path = QPainterPath()
                    if above:
                        path.moveTo(cx - 4.5, my + 3)
                        path.lineTo(cx, my - 5)
                        path.lineTo(cx + 4.5, my + 3)
                    else:
                        path.moveTo(cx - 4.5, my - 3)
                        path.lineTo(cx, my + 5)
                        path.lineTo(cx + 4.5, my - 3)
                    it = QGraphicsPathItem(path)
                    it.setPen(QPen(black, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    it.setBrush(QBrush(Qt.NoBrush))
                    self.scene.addItem(it)
                    self._register_note_item(key, it, 'shape')
            elif mk in ('fermata', 'trill', 'mordent', 'inverted_mordent', 'turn'):
                oy = hi - 11 * orn_k
                orn_k += 1
                self._draw_ornament(mk, cx, oy, key)
            elif mk == 'arpeggio':
                path = QPainterPath()
                ax = left_x - 4
                yy = bot_y + 5
                path.moveTo(ax, yy)
                up = True
                while yy > top_y - 5:
                    path.quadTo(ax + (3 if up else -3), yy - 2.5, ax, yy - 5)
                    yy -= 5
                    up = not up
                it = QGraphicsPathItem(path)
                it.setPen(QPen(black, 1.2))
                it.setBrush(QBrush(Qt.NoBrush))
                self.scene.addItem(it)
                self._register_note_item(key, it, 'shape')
            elif mk == 'breath':
                f = QFont("Georgia", 16)
                f.setBold(True)
                self._text(",", x + NOTE_HEAD_WIDTH + 12, y - 2 * sp - 4, f, QColor(0, 0, 0))

    def _draw_ornament(self, mk: str, cx: float, oy: float, key):
        black = Qt.GlobalColor.black
        if mk == 'fermata':
            path = QPainterPath()
            path.moveTo(cx - 8, oy + 3)
            path.cubicTo(cx - 8, oy - 8, cx + 8, oy - 8, cx + 8, oy + 3)
            it = QGraphicsPathItem(path)
            it.setPen(QPen(black, 1.8, Qt.SolidLine, Qt.RoundCap))
            it.setBrush(QBrush(Qt.NoBrush))
            self.scene.addItem(it)
            self._register_note_item(key, it, 'shape')
            d = QGraphicsEllipseItem(cx - 1.8, oy - 0.5, 3.6, 3.6)
            d.setBrush(QBrush(black))
            d.setPen(QPen(black, 0.5))
            self.scene.addItem(d)
            return
        if mk == 'trill':
            f = QFont("Georgia", 12)
            f.setBold(True)
            f.setItalic(True)
            self._text("tr", cx - 8, oy - 12, f, QColor(0, 0, 0))
            return
        if mk == 'turn':
            path = QPainterPath()
            path.moveTo(cx - 9, oy)
            path.cubicTo(cx - 9, oy - 6, cx - 2, oy - 6, cx, oy)
            path.cubicTo(cx + 2, oy + 6, cx + 9, oy + 6, cx + 9, oy)
            it = QGraphicsPathItem(path)
            it.setPen(QPen(black, 2.0, Qt.SolidLine, Qt.RoundCap))
            it.setBrush(QBrush(Qt.NoBrush))
            self.scene.addItem(it)
            self._register_note_item(key, it, 'shape')
            return
        # 波音：锯齿；（下）波音再加一道竖线
        path = QPainterPath()
        path.moveTo(cx - 9, oy + 2)
        for i, xx in enumerate((-6, -3, 0, 3, 6, 9)):
            path.lineTo(cx + xx, oy + (-3 if i % 2 == 0 else 2))
        it = QGraphicsPathItem(path)
        it.setPen(QPen(black, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        it.setBrush(QBrush(Qt.NoBrush))
        self.scene.addItem(it)
        self._register_note_item(key, it, 'shape')
        if mk == 'mordent':
            ln = QGraphicsLineItem(cx, oy - 6, cx, oy + 5)
            ln.setPen(QPen(black, 1.2))
            self.scene.addItem(ln)

    def _arc(self, x0: float, y0: float, x1: float, y1: float, above: bool,
             height: float, width: float = 1.4):
        path = QPainterPath()
        path.moveTo(x0, y0)
        peak = (min(y0, y1) - height) if above else (max(y0, y1) + height)
        mx = (x0 + x1) / 2.0
        path.cubicTo(mx - (mx - x0) * 0.3, peak, mx + (x1 - mx) * 0.3, peak, x1, y1)
        item = QGraphicsPathItem(path)
        item.setPen(QPen(Qt.GlobalColor.black, width, Qt.SolidLine, Qt.RoundCap))
        item.setBrush(QBrush(Qt.NoBrush))
        self.scene.addItem(item)
        return item

    def _render_ties(self, note: Note, pitches, positions, line_ys, x: float, hw: float,
                     y: float, staff_idx: int, stem_up: bool):
        """延音线：tie_end 处把前一个同音高的音接上（跨行画两段半弧）。"""
        n_p = len(pitches)
        mid_pos = (max(positions) + min(positions)) / 2.0 if n_p > 1 else None

        def side(i):
            if n_p == 1:
                return not stem_up            # 单音：与符干相反
            if positions[i] == max(positions):
                return True
            if positions[i] == min(positions):
                return False
            return positions[i] >= mid_pos

        if note.tie_end:
            for i, p in enumerate(pitches):
                pend = self._tie_pending.pop((staff_idx, p.to_midi()), None)
                if pend is None:
                    continue
                sx, sy, sys_y, above = pend
                off = -5 if above else 5
                ex = x - 1
                if abs(sys_y - y) < 1 and ex > sx:
                    self._arc(sx, sy + off, ex, line_ys[i] + off, above, 6)
                else:
                    self._arc(sx, sy + off, self._sys_right - 2, sy + off, above, 5)
                    self._arc(max(LEFT_MARGIN + 40.0, ex - 26), line_ys[i] + off,
                              ex, line_ys[i] + off, above, 5)
        if note.tie_start:
            for i, p in enumerate(pitches):
                self._tie_pending[(staff_idx, p.to_midi())] = (
                    x + hw + 1, line_ys[i], y, side(i))

    def _render_slur(self, note: Note, x: float, line_ys, y: float, staff_idx: int,
                     stem_up: bool):
        """圆滑线：按声部用栈配对（可嵌套），跨行时分两段画。"""
        stack = self._slur_pending.setdefault(staff_idx, [])
        cx = x + NOTE_HEAD_WIDTH / 2
        top, bot = min(line_ys), max(line_ys)
        if note.slur_end and stack:
            sx, s_top, s_bot, sys_y, s_up = stack.pop()
            above = not s_up            # 放在符头一侧（与起音符干方向相反）
            if abs(sys_y - y) < 1 and cx > sx:
                y1 = (s_top - 10) if above else (s_bot + 10)
                y2 = (top - 10) if above else (bot + 10)
                self._arc(sx, y1, cx, y2, above, 14, 1.6)
            else:
                y1 = (s_top - 10) if above else (s_bot + 10)
                self._arc(sx, y1, self._sys_right - 4, y1, above, 10, 1.6)
                y2 = (top - 10) if above else (bot + 10)
                self._arc(max(LEFT_MARGIN + 40.0, cx - 40), y2, cx, y2, above, 10, 1.6)
        if note.slur_start:
            stack.append((cx, top, bot, y, stem_up))

    # ------------------------------------------------------------------
    # 休止符 / 符尾
    # ------------------------------------------------------------------

    def _render_rest(self, note: Note, x: float, y: float, width: float, key=None,
                     full_bar: bool = False):
        """矢量绘制休止符：macOS/Linux 无 U+1D13B-1D13F 字形（Segoe UI Symbol 是
        Windows 专属字体），文本渲染会变成乱码块，因此全部用图形绘制。

        full_bar：整小节休止一律画全休止符（3/4、6/8 也一样），由调用方居中。
        """
        items = []
        cx = x + 4  # 休止符水平中心
        sp = STAFF_LINE_SPACING

        def _rect_rest(line_from_top: int, hang: bool):
            """whole/half 休止：挂在第2线下方 / 坐在第3线上方的小矩形。"""
            ly = y + line_from_top * sp
            h = sp * 0.55
            ry = ly if hang else ly - h
            r = QGraphicsRectItem(cx - 5, ry, 11, h)
            r.setBrush(QBrush(Qt.GlobalColor.black))
            r.setPen(QPen(Qt.GlobalColor.black, 0.5))
            self.scene.addItem(r)
            items.append(r)

        dot_y = y + 1.5 * sp
        if full_bar or note.duration == NoteDuration.WHOLE:
            _rect_rest(1, hang=True)
            dot_y = y + 1.5 * sp
        elif note.duration == NoteDuration.HALF:
            _rect_rest(2, hang=False)
            dot_y = y + 1.5 * sp
        elif note.duration == NoteDuration.EIGHTH:
            self._draw_hooked_rest(x, y, hooks=1, items=items)
        elif note.duration == NoteDuration.SIXTEENTH:
            self._draw_hooked_rest(x, y, hooks=2, items=items)
        elif note.duration == NoteDuration.THIRTY_SECOND:
            self._draw_hooked_rest(x, y, hooks=3, items=items)
        else:
            # 四分休止：经典折线（锯齿）形状
            mid = y + 2 * sp
            path = QPainterPath()
            path.moveTo(cx + 4, mid - 1.6 * sp)
            path.lineTo(cx - 3, mid - 0.5 * sp)
            path.lineTo(cx + 4, mid + 0.2 * sp)
            path.lineTo(cx - 3, mid + 1.0 * sp)
            path.lineTo(cx + 3, mid + 1.7 * sp)
            path.lineTo(cx - 1, mid + 1.95 * sp)
            zig = QGraphicsPathItem(path)
            zig.setPen(QPen(Qt.GlobalColor.black, 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            zig.setBrush(QBrush(Qt.GlobalColor.black))
            self.scene.addItem(zig)
            items.append(zig)

        if not full_bar:
            for d in range(note.dots):
                dot = QGraphicsEllipseItem(cx + 9 + d * 6, dot_y - 1.6, 3.2, 3.2)
                dot.setBrush(QBrush(Qt.GlobalColor.black))
                dot.setPen(QPen(Qt.GlobalColor.black, 0.4))
                self.scene.addItem(dot)
                items.append(dot)

        if key is not None:
            for it in items:
                self._register_note_item(key, it, 'shape')

    def _draw_note_flags(self, x: float, stem_up: bool, tip_y: float,
                         n_flags: int, key=None, *,
                         pen=None, brush=None, z: float = None,
                         head_w: float = NOTE_HEAD_WIDTH, scale: float = 1.0):
        """在符干末端画 1–3 条符尾（八分/十六分/三十二分）。返回绘制的 items。

        符尾总是朝右；符干朝下时只做上下镜像。
        """
        stem_x = (x + head_w - 1) if stem_up else (x + 1)
        pen = pen if pen is not None else QPen(
            Qt.GlobalColor.black, 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        brush = brush if brush is not None else QBrush(Qt.GlobalColor.black)
        s = scale
        out = []
        for i in range(max(0, int(n_flags))):
            path = QPainterPath()
            if stem_up:
                fy = tip_y + i * 6.5 * s
                path.moveTo(stem_x, fy)
                path.cubicTo(stem_x + 9 * s, fy + 1 * s, stem_x + 14 * s, fy + 9 * s,
                             stem_x + 3 * s, fy + 17 * s)
                path.cubicTo(stem_x + 10 * s, fy + 10 * s, stem_x + 8 * s, fy + 4 * s,
                             stem_x, fy + 3 * s)
            else:
                fy = tip_y - i * 6.5 * s
                path.moveTo(stem_x, fy)
                path.cubicTo(stem_x + 9 * s, fy - 1 * s, stem_x + 14 * s, fy - 9 * s,
                             stem_x + 3 * s, fy - 17 * s)
                path.cubicTo(stem_x + 10 * s, fy - 10 * s, stem_x + 8 * s, fy - 4 * s,
                             stem_x, fy - 3 * s)
            flag = QGraphicsPathItem(path)
            flag.setPen(pen)
            flag.setBrush(brush)
            if z is not None:
                flag.setZValue(z)
            self.scene.addItem(flag)
            if key is not None:
                self._register_note_item(key, flag, 'shape')
            out.append(flag)
        return out

    def _draw_hooked_rest(self, x, y, hooks: int, items):
        """八分/十六分休止：右上到左下的斜干 + 若干贴在斜干左侧的圆钩。"""
        cx = x + 6
        top = y + 1.2 * STAFF_LINE_SPACING
        bot = y + 3.4 * STAFF_LINE_SPACING
        stem = QGraphicsLineItem(cx + 3, top, cx - 3, bot)
        stem.setPen(QPen(Qt.GlobalColor.black, 1.8, Qt.SolidLine, Qt.RoundCap))
        self.scene.addItem(stem)
        items.append(stem)
        r = 2.8
        for i in range(hooks):
            fy = top + i * (bot - top) * 0.42
            sx = cx + 3 - 6 * (fy - top) / (bot - top)   # 斜干在该高度的 x
            ball = QGraphicsEllipseItem(sx - 2 * r + 0.5, fy - r, r * 2, r * 2)
            ball.setBrush(QBrush(Qt.GlobalColor.black))
            ball.setPen(QPen(Qt.GlobalColor.black, 0.5))
            self.scene.addItem(ball)
            items.append(ball)
