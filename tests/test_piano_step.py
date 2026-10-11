# -*- coding: utf-8 -*-
"""MIDI 钢琴步进：左右手分谱、和弦、两手对齐、自动大谱表、跨小节。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from music_score.assist_engine import blank_score  # noqa: E402
from music_score.editor_core import EditController  # noqa: E402
from music_score.midi_input import MidiNoteEvent, StepInputAccumulator  # noqa: E402
from music_score.models import Accidental, ClefType, NoteDuration  # noqa: E402
from music_score.piano_step import (  # noqa: E402
    ensure_grand_staff, find_grand_staff, preview_groups, resolve_cursor,
    spell_midi, split_hands, write_step,
)

_FAILS = 0


def check(desc, cond, detail=''):
    global _FAILS
    if cond:
        print(f'  ✓ {desc}')
    else:
        _FAILS += 1
        print(f'  ✗ {desc}' + (f' — {detail}' if detail else ''))


def midis_of(note):
    return [p.to_midi() for p in [note.pitch] + list(note.chord_tones)]


def test_split_and_spell():
    print('[1] 分手与拼写')
    rh, lh = split_hands([48, 72, 64, 60, 55, 64])
    check('右手 ≥ C4 高→低去重', rh == [72, 64, 60], str(rh))
    check('左手 < C4', lh == [55, 48], str(lh))
    p = spell_midi(70, key_sharps=-2)
    check('降号调拼 Bb', p.accidental == Accidental.FLAT and p.to_midi() == 70)
    p = spell_midi(66, key_sharps=1)
    check('升号调拼 F#', p.accidental == Accidental.SHARP and p.to_midi() == 66)


def test_auto_grand_staff():
    print('[2] 单谱表自动升钢琴大谱表')
    sc = blank_score(bars=2, layout='single')
    check('起始无大谱表', find_grand_staff(sc) is None)
    rh, lh, msg = ensure_grand_staff(sc)
    check('补出左手', (rh, lh) == (0, 1) and msg, f'{rh},{lh},{msg}')
    check('左手是低音谱号', sc.staves[lh].clef == ClefType.BASS)
    check('小节数对齐', len(sc.staves[0].measures) == len(sc.staves[1].measures))
    check('再次调用不重复添加', ensure_grand_staff(sc)[2] is None
          and len(sc.staves) == 2)


def test_two_hand_chord():
    print('[3] 两手同时按和弦')
    sc = blank_score(bars=2, layout='single')
    rh, lh, _ = ensure_grand_staff(sc)
    info = write_step(sc, rh, lh, 0, [48, 55, 64, 67, 72], NoteDuration.QUARTER)
    r = sc.staves[rh].measures[0].notes
    l_ = sc.staves[lh].measures[0].notes
    check('右手一个三音和弦', len(r) == 1 and midis_of(r[0]) == [72, 67, 64],
          str([midis_of(n) for n in r]))
    check('左手一个双音和弦', len(l_) == 1 and midis_of(l_[0]) == [55, 48],
          str([midis_of(n) for n in l_]))
    check('返回写入信息', info['rh'] == [72, 67, 64] and info['lh'] == [55, 48])


def test_alignment_with_rests():
    print('[4] 只弹一只手时另一只手补休止，两手对齐')
    sc = blank_score(bars=2, layout='single')
    rh, lh, _ = ensure_grand_staff(sc)
    write_step(sc, rh, lh, 0, [72], NoteDuration.QUARTER)
    write_step(sc, rh, lh, 0, [74], NoteDuration.QUARTER)
    write_step(sc, rh, lh, 0, [48], NoteDuration.HALF)
    r = sc.staves[rh].measures[0]
    l_ = sc.staves[lh].measures[0]
    check('左手前两拍是休止', [n.is_rest for n in l_.notes[:2]] == [True, True])
    check('两手小节等长', abs(r.total_duration() - l_.total_duration()) < 1e-9,
          f'{r.total_duration()} vs {l_.total_duration()}')
    check('第一小节写满', abs(r.total_duration() - 1.0) < 1e-9)


def test_no_pad_then_catch_up():
    print('[5] 不补休止时，落后的手在下一笔先补齐再写')
    sc = blank_score(bars=2, layout='single')
    rh, lh, _ = ensure_grand_staff(sc)
    write_step(sc, rh, lh, 0, [72], NoteDuration.QUARTER, pad_rests=False)
    check('左手暂空', sc.staves[lh].measures[0].notes == [])
    write_step(sc, rh, lh, 0, [48], NoteDuration.QUARTER, pad_rests=False)
    l_ = sc.staves[lh].measures[0].notes
    check('左手先补一个四分休止再写 C3',
          len(l_) == 2 and l_[0].is_rest and midis_of(l_[1]) == [48],
          str([(n.is_rest, midis_of(n)) for n in l_]))


def test_overflow_next_measure():
    print('[6] 小节写满自动进下一小节；超出末小节自动加小节')
    sc = blank_score(bars=1, layout='single')
    rh, lh, _ = ensure_grand_staff(sc)
    info = write_step(sc, rh, lh, 0, [60, 48], NoteDuration.WHOLE)
    check('写满后 next 指向下一小节', info['next_measure_idx'] == 1)
    info = write_step(sc, rh, lh, 0, [62], NoteDuration.QUARTER)
    check('自动落到第 2 小节', info['measure_idx'] == 1)
    check('两手都追加了小节', len(sc.staves[rh].measures) == 2
          and len(sc.staves[lh].measures) == 2)


def test_clamp_duration():
    print('[7] 剩余不足时缩短时值')
    sc = blank_score(bars=1, layout='single')
    rh, lh, _ = ensure_grand_staff(sc)
    write_step(sc, rh, lh, 0, [72], NoteDuration.HALF)
    write_step(sc, rh, lh, 0, [74], NoteDuration.QUARTER)
    info = write_step(sc, rh, lh, 0, [76], NoteDuration.HALF)
    check('二分被缩成四分填满', info['duration'] == NoteDuration.QUARTER)
    check('光标在下一小节', resolve_cursor(sc, rh, lh, 0)[0] == 1)


def test_preview_and_controller():
    print('[8] 预览分组 + EditController 撤销')
    sc = blank_score(bars=2, layout='single')
    rh, lh, _ = ensure_grand_staff(sc)
    prev = preview_groups(sc, rh, lh, 0, [76, 52])
    staffs = sorted(g['staff_idx'] for g in prev['groups'])
    check('预览分到两只手', staffs == [rh, lh], str(staffs))
    check('预览不改乐谱', all(not m.notes for s in sc.staves for m in s.measures))

    ctl = EditController()
    ctl.score = sc
    info = ctl.insert_piano_step(rh, lh, 0, [76, 72, 48])
    check('控制器写入', info is not None
          and len(sc.staves[rh].measures[0].notes) == 1)
    check('可撤销', ctl.undo() and not sc.staves[rh].measures[0].notes)


def test_accumulator_preview():
    print('[9] 累加器预览：按住未提交的键')
    acc = StepInputAccumulator(chord_window_ms=80)
    acc.chord_mode = True
    t0 = time.monotonic()
    acc.feed(MidiNoteEvent(60, 70, True, timestamp=t0))
    acc.feed(MidiNoteEvent(48, 70, True, timestamp=t0))
    check('和弦模式按住时预览两键', acc.preview() == [60, 48], str(acc.preview()))
    acc.feed(MidiNoteEvent(60, 0, False, timestamp=t0 + 0.1))
    bundle = acc.feed(MidiNoteEvent(48, 0, False, timestamp=t0 + 0.11))
    check('全抬后提交', [m for m, _ in bundle] == [60, 48])
    check('提交后预览清空', acc.preview() == [])


if __name__ == '__main__':
    test_split_and_spell()
    test_auto_grand_staff()
    test_two_hand_chord()
    test_alignment_with_rests()
    test_no_pad_then_catch_up()
    test_overflow_next_measure()
    test_clamp_duration()
    test_preview_and_controller()
    test_accumulator_preview()
    print(f'\n{"全部通过" if _FAILS == 0 else f"失败 {_FAILS}"}')
    sys.exit(1 if _FAILS else 0)
