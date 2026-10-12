"""可点加线区须覆盖小夜曲开篇的 B5 / D6（原先只高出 8px，最高约 G5）。"""
from PyQt5.QtCore import QRectF

from music_score.interaction import snap_to_nearest_line, staff_position_to_pitch
from music_score.models import Accidental, ClefType, NoteName, Pitch
from music_score.renderer import STAFF_HEIGHT, STAFF_LINE_SPACING, hit_ledger_pad, pitch_to_staff_position


def _pitch_at_dy(dy_from_staff_top: float):
    pos = snap_to_nearest_line(
        (STAFF_HEIGHT - dy_from_staff_top) / STAFF_LINE_SPACING)
    return staff_position_to_pitch(pos, ClefType.TREBLE)


def test_b5_is_above_staff_not_middle_line():
    b4p = Pitch(NoteName.B, 4, Accidental.NATURAL)
    b5p = Pitch(NoteName.B, 5, Accidental.NATURAL)
    g5p = Pitch(NoteName.G, 5, Accidental.NATURAL)
    assert pitch_to_staff_position(b4p, ClefType.TREBLE) == 2.0  # 中间线
    assert pitch_to_staff_position(g5p, ClefType.TREBLE) == 4.5
    assert pitch_to_staff_position(b5p, ClefType.TREBLE) == 5.5


def test_single_staff_hit_rect_covers_b5_and_d6():
    pad = hit_ledger_pad(1)
    staff_y = 80.0
    rect = QRectF(60, staff_y - pad, 160, STAFF_HEIGHT + 2 * pad)
    for name, octv, pos in (
        ('G', 5, 4.5),
        ('B', 5, 5.5),
        ('D', 6, 6.5),
    ):
        y = staff_y + (4 - pos) * STAFF_LINE_SPACING
        assert rect.contains(70, y), f'{name}{octv} y={y} not in hit rect'
        p = _pitch_at_dy(y - staff_y)
        assert p.name.name == name and p.octave == octv


def test_old_8px_pad_misses_b5():
    staff_y = 80.0
    old = QRectF(60, staff_y - 8, 160, STAFF_HEIGHT + 16)
    b5_y = staff_y + (4 - 5.5) * STAFF_LINE_SPACING
    assert not old.contains(70, b5_y)


def test_hover_on_g5_inserts_after_not_before():
    from music_score.interaction import insert_index_for_x

    class R:
        hit_notes = [{
            'staff_idx': 0, 'measure_idx': 0, 'note_idx': 0,
            'rect': QRectF(100, 80, 36, 40),
        }]

    r = R()
    # 点在 G5 符头上 → 插入序号 1（下一音），不是 0
    assert insert_index_for_x(r, 0, 0, 118) == 1
    assert insert_index_for_x(r, 0, 0, 110) == 1
    # 点在该音左边空白 → 仍插到最前
    assert insert_index_for_x(r, 0, 0, 90) == 0
