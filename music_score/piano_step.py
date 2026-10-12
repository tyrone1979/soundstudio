# -*- coding: utf-8 -*-
"""MIDI 钢琴步进写入：左右手分谱 + 和弦 + 两手垂直对齐。

纯模型逻辑（不依赖 Qt），供 score_studio 的 MIDI 键盘输入与测试共用。

约定
----
- 分手点 ``split``（默认 60 = C4）：``midi >= split`` 进右手（高音谱表），
  其余进左手（低音谱表）。
- 两手共用一个「光标」：当前小节 + 小节内起点 onset。onset 取两手已写时值的
  较大者，落后的那只手先用休止符补齐，保证同一时刻的音上下对齐。
- 某只手本次没弹时（``pad_rests=True``）补一个等长休止符，小节始终两手等长。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .models import (
    Accidental, ClefType, Measure, Note, NoteDuration, NoteName, Pitch, Score,
)

DEFAULT_SPLIT = 60
_EPS = 1e-9

_SHARP_SPELL = {
    0: (NoteName.C, Accidental.NATURAL), 1: (NoteName.C, Accidental.SHARP),
    2: (NoteName.D, Accidental.NATURAL), 3: (NoteName.D, Accidental.SHARP),
    4: (NoteName.E, Accidental.NATURAL), 5: (NoteName.F, Accidental.NATURAL),
    6: (NoteName.F, Accidental.SHARP), 7: (NoteName.G, Accidental.NATURAL),
    8: (NoteName.G, Accidental.SHARP), 9: (NoteName.A, Accidental.NATURAL),
    10: (NoteName.A, Accidental.SHARP), 11: (NoteName.B, Accidental.NATURAL),
}
_FLAT_SPELL = dict(_SHARP_SPELL)
_FLAT_SPELL.update({
    1: (NoteName.D, Accidental.FLAT), 3: (NoteName.E, Accidental.FLAT),
    6: (NoteName.G, Accidental.FLAT), 8: (NoteName.A, Accidental.FLAT),
    10: (NoteName.B, Accidental.FLAT),
})

_PLAIN_DURS = sorted(NoteDuration, key=lambda d: -d.value)


def spell_midi(midi: int, key_sharps: int = 0) -> Pitch:
    """按调号拼写黑键：降号调用降号，其余用升号。"""
    midi = int(midi)
    table = _FLAT_SPELL if int(key_sharps or 0) < 0 else _SHARP_SPELL
    name, acc = table[midi % 12]
    return Pitch(name, midi // 12 - 1, acc)


def split_hands(midis: Sequence[int], split: int = DEFAULT_SPLIT
                ) -> Tuple[List[int], List[int]]:
    """按分手点拆成 (右手, 左手)，各自高→低去重。"""
    uniq = sorted({int(m) for m in midis}, reverse=True)
    rh = [m for m in uniq if m >= split]
    lh = [m for m in uniq if m < split]
    return rh, lh


def find_grand_staff(score: Score) -> Optional[Tuple[int, int]]:
    """返回 (右手谱表, 左手谱表)；找不到返回 None。"""
    if score is None or not score.staves:
        return None
    n = len(score.staves)
    for g in score.staff_groups or []:
        parts = [int(p) for p in (g.get('parts') or []) if 0 <= int(p) < n]
        if g.get('symbol') == 'brace' and len(parts) >= 2:
            return parts[0], parts[1]
    treble = next((i for i, s in enumerate(score.staves)
                   if s.clef != ClefType.BASS), None)
    if treble is None:
        return None
    bass = next((i for i, s in enumerate(score.staves)
                 if i > treble and s.clef == ClefType.BASS), None)
    if bass is None:
        return None
    return treble, bass


def ensure_grand_staff(score: Score) -> Tuple[int, int, Optional[str]]:
    """确保有钢琴大谱表；必要时追加左手低音谱表。返回 (rh, lh, 说明或 None)。"""
    found = find_grand_staff(score)
    if found is not None:
        return found[0], found[1], None
    from .part_accomp import add_part_from_token, add_staff
    if len(score.staves) == 1:
        idx, msg = add_part_from_token(score, 'piano')
        return 0, idx, msg
    treble = next((i for i, s in enumerate(score.staves)
                   if s.clef != ClefType.BASS), 0)
    idx = add_staff(score, name='LH', clef=ClefType.BASS)
    return treble, idx, f'已追加左手低音谱表（索引 {idx}）'


def measure_capacity(score: Score, measure_idx: int = 0) -> float:
    """小节容量（全音符为单位）。"""
    ts = score.time_signature
    for staff in score.staves:
        if measure_idx < len(staff.measures):
            m_ts = staff.measures[measure_idx].time_signature
            if m_ts is not None:
                ts = m_ts
                break
    return ts.beats_per_measure() / 4.0


def _fill(score: Score, staff_idx: int, measure_idx: int) -> float:
    staff = score.staves[staff_idx]
    if measure_idx >= len(staff.measures):
        return 0.0
    return staff.measures[measure_idx].total_duration()


def resolve_cursor(score: Score, rh: int, lh: int, measure_idx: int
                   ) -> Tuple[int, float]:
    """不修改乐谱，算出下一笔落点 (小节, onset)。小节满则进到下一小节。"""
    mi = max(0, int(measure_idx))
    while True:
        cap = measure_capacity(score, mi)
        onset = max(_fill(score, rh, mi), _fill(score, lh, mi))
        if onset < cap - _EPS:
            return mi, onset
        mi += 1
        if mi >= max(len(score.staves[rh].measures),
                     len(score.staves[lh].measures)):
            return mi, 0.0


def largest_fitting(remaining: float) -> Optional[NoteDuration]:
    for d in _PLAIN_DURS:
        if d.value <= remaining + _EPS:
            return d
    return None


def decompose_gap(gap: float) -> List[NoteDuration]:
    """把一段空缺拆成若干普通时值（从大到小）。"""
    out: List[NoteDuration] = []
    left = float(gap)
    while left > _EPS:
        d = largest_fitting(left)
        if d is None:
            break
        out.append(d)
        left -= d.value
    return out


def _rest_pitch(clef: ClefType) -> Pitch:
    return {ClefType.TREBLE: Pitch(NoteName.B, 4),
            ClefType.BASS: Pitch(NoteName.D, 3),
            ClefType.ALTO: Pitch(NoteName.C, 4)}.get(clef, Pitch(NoteName.B, 4))


def _make_note(midis: Sequence[int], duration: NoteDuration,
               key_sharps: int) -> Note:
    ordered = sorted({int(m) for m in midis}, reverse=True)
    top = spell_midi(ordered[0], key_sharps)
    chord = [spell_midi(m, key_sharps) for m in ordered[1:]]
    return Note(top, duration, chord_tones=chord)


def _ensure_measure(score: Score, measure_idx: int) -> None:
    need = measure_idx + 1
    cur = max(len(s.measures) for s in score.staves)
    if cur >= need:
        for s in score.staves:
            while len(s.measures) < cur:
                s.measures.append(Measure(number=len(s.measures) + 1))
        return
    from .assist_engine import append_empty_measures
    append_empty_measures(score, need - cur)


def write_step(score: Score, rh: int, lh: int, measure_idx: int,
               midis: Sequence[int], duration: NoteDuration, *,
               split: int = DEFAULT_SPLIT, pad_rests: bool = True
               ) -> Dict:
    """把一次按键（单音或和弦，可跨两手）写进大谱表。

    返回 ``{'measure_idx', 'next_measure_idx', 'onset', 'duration',
    'rh', 'lh'}``；rh/lh 为本次写入该手的 midi 列表。
    """
    rh_m, lh_m = split_hands(midis, split)
    if not rh_m and not lh_m:
        raise ValueError('没有可写入的音')
    mi, onset = resolve_cursor(score, rh, lh, measure_idx)
    _ensure_measure(score, mi)
    cap = measure_capacity(score, mi)
    key_sharps = int(getattr(score.key_signature, 'sharps', 0) or 0)

    for si in (rh, lh):
        gap = onset - _fill(score, si, mi)
        clef = score.staves[si].clef
        for d in decompose_gap(gap):
            score.staves[si].measures[mi].notes.append(
                Note(_rest_pitch(clef), d, is_rest=True))

    dur = duration if duration.value <= cap - onset + _EPS else (
        largest_fitting(cap - onset) or duration)

    for si, hand in ((rh, rh_m), (lh, lh_m)):
        measure = score.staves[si].measures[mi]
        if hand:
            measure.notes.append(_make_note(hand, dur, key_sharps))
        elif pad_rests:
            measure.notes.append(
                Note(_rest_pitch(score.staves[si].clef), dur, is_rest=True))

    end = onset + dur.value
    next_mi = mi + 1 if end >= cap - _EPS else mi
    return {
        'measure_idx': mi,
        'next_measure_idx': next_mi,
        'onset': onset,
        'duration': dur,
        'rh': rh_m,
        'lh': lh_m,
    }


def preview_groups(score: Score, rh: int, lh: int, measure_idx: int,
                   midis: Sequence[int], split: int = DEFAULT_SPLIT
                   ) -> Dict:
    """按住未提交的键 → 预览分组（不改乐谱）。"""
    rh_m, lh_m = split_hands(midis, split)
    mi, onset = resolve_cursor(score, rh, lh, measure_idx)
    key_sharps = int(getattr(score.key_signature, 'sharps', 0) or 0)
    cap = measure_capacity(score, min(mi, max(
        len(score.staves[rh].measures), 1) - 1))
    groups = []
    for si, hand in ((rh, rh_m), (lh, lh_m)):
        if hand:
            groups.append({
                'staff_idx': si,
                'clef': score.staves[si].clef,
                'pitches': [spell_midi(m, key_sharps) for m in hand],
            })
    return {
        'measure_idx': mi,
        'onset_ratio': (onset / cap) if cap > 0 else 0.0,
        'groups': groups,
    }
