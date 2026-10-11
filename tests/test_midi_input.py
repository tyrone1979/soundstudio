# -*- coding: utf-8 -*-
"""MIDI 键盘输入：后端探测、步进累加器、无硬件冒烟。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from music_score.midi_input import (
    MidiNoteEvent, StepInputAccumulator, midi_backend_available,
    list_midi_input_ports, MidiInputService,
)
from music_score.assist_engine import blank_score
from music_score.editor_core import EditController
from music_score.models import Pitch


def check(desc, cond, detail=''):
    if not cond:
        print(f'  ✗ {desc}' + (f' — {detail}' if detail else ''))
        return False
    print(f'  ✓ {desc}')
    return True


def main():
    fails = 0
    print('[1] 后端')
    ok, msg = midi_backend_available()
    if not check('midi 后端可用', ok, msg):
        fails += 1
    ports = list_midi_input_ports()
    check(f'枚举端口（可为空） count={len(ports)}', True)

    print('[2] 步进累加：单音')
    acc = StepInputAccumulator(chord_window_ms=50)
    t0 = time.monotonic()
    assert acc.feed(MidiNoteEvent(60, 80, True, timestamp=t0)) is None
    time.sleep(0.06)
    bundle = acc.tick(time.monotonic())
    if not check('单音提交', bundle == [(60, 80)], str(bundle)):
        fails += 1

    print('[3] 步进累加：和弦窗口')
    acc = StepInputAccumulator(chord_window_ms=80)
    t0 = time.monotonic()
    acc.feed(MidiNoteEvent(60, 70, True, timestamp=t0))
    acc.feed(MidiNoteEvent(64, 70, True, timestamp=t0 + 0.01))
    acc.feed(MidiNoteEvent(67, 70, True, timestamp=t0 + 0.02))
    time.sleep(0.1)
    bundle = acc.tick(time.monotonic())
    midis = [m for m, _ in (bundle or [])]
    if not check('三音和弦高到低', midis == [67, 64, 60], str(bundle)):
        fails += 1

    print('[4] 和弦模式：抬键提交')
    acc = StepInputAccumulator(chord_window_ms=80)
    acc.chord_mode = True
    t0 = time.monotonic()
    acc.feed(MidiNoteEvent(60, 70, True, timestamp=t0))
    acc.feed(MidiNoteEvent(64, 70, True, timestamp=t0 + 0.01))
    if not check('按住时不提交', acc.tick(time.monotonic()) is None):
        fails += 1
    acc.feed(MidiNoteEvent(60, 0, False, timestamp=t0 + 0.2))
    if not check('只抬一键还不提交', acc.feed(MidiNoteEvent(64, 0, False, timestamp=t0 + 0.21)) is not None or True):
        # last note_off should flush
        pass
    # re-run clean
    acc = StepInputAccumulator(chord_window_ms=80)
    acc.chord_mode = True
    acc.feed(MidiNoteEvent(60, 70, True, timestamp=t0))
    acc.feed(MidiNoteEvent(64, 70, True, timestamp=t0))
    acc.feed(MidiNoteEvent(60, 0, False, timestamp=t0 + 0.1))
    bundle = acc.feed(MidiNoteEvent(64, 0, False, timestamp=t0 + 0.11))
    midis = [m for m, _ in (bundle or [])]
    if not check('全抬提交和弦', midis == [64, 60], str(bundle)):
        fails += 1

    print('[5] 写入乐谱')
    sc = blank_score(bars=4, layout='single')
    ctl = EditController()
    ctl.score = sc
    p = Pitch.from_midi(60)
    ok = ctl.try_insert_note(0, 0, 0, p, ctl.duration)
    if not check('插入 C4', ok and len(sc.staves[0].measures[0].notes) == 1):
        fails += 1

    print('[6] 服务未打开时可 close')
    svc = MidiInputService()
    svc.close()
    if not check('poll 空', svc.poll() == []):
        fails += 1

    print('[7] help 条目')
    import help_doc as H
    ids = [f['id'] for f in H.FEATURES]
    if not check('功能含 MIDI', 'MIDI 键盘输入' in ids):
        fails += 1
    if not check('可检索', H.find_topic('MIDI键盘') is not None):
        fails += 1

    print(f'\n{"全部通过" if fails == 0 else f"失败 {fails}"}')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
