"""
Mouse interaction for adding/editing notes on the score.
"""
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtWidgets import QGraphicsSceneMouseEvent

from .models import (Note, Pitch, ClefType, NoteName, Accidental, NoteDuration,
                     CLEF_BOTTOM_LINE)
from .renderer import STAFF_LINE_SPACING, STAFF_HEIGHT, LEFT_MARGIN, TOP_MARGIN, STAFF_GAP

# 与 renderer._DIATONIC 一致的全音阶音级（C=0…B=6）
_DIATONIC = {
    NoteName.C: 0, NoteName.D: 1, NoteName.E: 2, NoteName.F: 3,
    NoteName.G: 4, NoteName.A: 5, NoteName.B: 6,
}
_DIA_NAMES = [NoteName.C, NoteName.D, NoteName.E, NoteName.F,
              NoteName.G, NoteName.A, NoteName.B]


def _staff_gap(n_staves: int) -> float:
    """与 renderer._render_system 一致的声部间距。"""
    if n_staves >= 6:
        return 58
    return 72 if n_staves > 2 else 100


def staff_position_to_pitch(position: float, clef: ClefType) -> Pitch:
    """Convert staff position to pitch. Position 0 = bottom line.

    ``pitch_to_staff_position`` 的精确逆映射：position 每 0.5 为一个
    全音阶音级（线/间），只定音名与八度，不产生变音记号。
    """
    base_name, base_octave = CLEF_BOTTOM_LINE.get(clef, CLEF_BOTTOM_LINE[ClefType.TREBLE])
    steps = int(round(position / 0.5))           # 半个谱表位置 = 一个音级
    idx = _DIATONIC[base_name] + steps
    octave = base_octave + (idx // 7)
    name = _DIA_NAMES[idx % 7]
    return Pitch(name, octave, Accidental.NATURAL)


def find_staff_and_measure_at(y: float, x: float, score, width: int):
    """Find which staff and measure the click is on."""
    n_staves = len(score.staves)
    if n_staves == 0:
        return None
    gap = _staff_gap(n_staves)
    staff_y = TOP_MARGIN
    for staff_idx, staff in enumerate(score.staves):
        if staff_y <= y <= staff_y + STAFF_HEIGHT + 20:
            if not staff.measures:
                return None
            measure_width = (width - LEFT_MARGIN - 20) / len(staff.measures)
            if x >= LEFT_MARGIN:
                measure_idx = int((x - LEFT_MARGIN) // measure_width)
                measure_idx = max(0, min(measure_idx, len(staff.measures) - 1))
                measure_x = LEFT_MARGIN + measure_idx * measure_width
                return staff_idx, measure_idx, measure_x, measure_width, staff_y
            return None
        # renderer 逐声部 y += STAFF_HEIGHT + staff_gap
        staff_y += STAFF_HEIGHT + gap
    return None


def snap_to_nearest_line(position: float) -> float:
    """Snap a staff position to the nearest line or space."""
    return round(position * 2) / 2


def insert_index_for_x(renderer, staff_idx: int, measure_idx: int,
                       x: float) -> int:
    """按点击横坐标推断插入序号。

    落在已有符头上（或其中线以右）→ 插在该音**之后**，这样写完 G5 再悬停
    在它附近时，推荐的是「下一个音」（D5），而不是当作空小节起步。
    """
    notes = [
        h for h in renderer.hit_notes
        if h.get('staff_idx') == staff_idx and h.get('measure_idx') == measure_idx
    ]
    if not notes:
        return 0
    notes.sort(key=lambda h: h['rect'].left())
    if x < notes[0]['rect'].left() + 2:
        return 0
    count = 0
    for h in notes:
        # 符头中线以右（含点在符头上）视为已经越过该音
        if x >= h['rect'].center().x():
            count += 1
        elif h['rect'].contains(x, h['rect'].center().y()):
            count += 1
    return count


def staff_y_from_measure_rect(rect, hit=None) -> float:
    """由小节 hit 反推谱表顶部 y。优先用 hit['staff_y']。"""
    if hit is not None and hit.get('staff_y') is not None:
        return float(hit['staff_y'])
    # 兼容旧 rect（曾为 y-8, height=STAFF_HEIGHT+16）
    return rect.top() + 8
