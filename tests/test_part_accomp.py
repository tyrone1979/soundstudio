# -*- coding: utf-8 -*-
"""添加声部 + 设置配乐。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from music_score.assist_engine import blank_score
from music_score.models import Note, NoteDuration, Pitch
from music_score.part_accomp import (
    add_part_from_token, fill_accompaniment,
    detect_add_part_request, detect_accomp_request,
)
from music_score.editor_core import EditController


def check(desc, cond, detail=''):
    if not cond:
        print(f'  ✗ {desc}' + (f' — {detail}' if detail else ''))
        return False
    print(f'  ✓ {desc}')
    return True


def main():
    fails = 0

    print('[1] 检测提示词')
    if not check('识别添加声部', detect_add_part_request('添加一个低音声部') == '低音'
                 or detect_add_part_request('添加一个低音声部') == 'bass'):
        fails += 1
    if not check('识别钢琴左手', detect_add_part_request('添加声部 钢琴') in ('钢琴', 'piano')):
        fails += 1
    if not check('识别配乐', detect_accomp_request('设置配乐') == 'bass'):
        fails += 1
    if not check('识别阿尔贝蒂', detect_accomp_request('加个阿尔贝蒂配乐') == 'alberti'):
        fails += 1
    if not check('不误伤', detect_add_part_request('写一首圆舞曲') is None):
        fails += 1

    print('[2] 单声部 → 钢琴大谱表')
    sc = blank_score(title='旋律', bars=4, layout='single')
    # 写简单旋律
    for i, midi in enumerate([60, 62, 64, 65]):
        sc.staves[0].measures[i].notes = [
            Note(Pitch.from_midi(midi), NoteDuration.WHOLE)]
    idx, msg = add_part_from_token(sc, 'piano')
    if not check('升大谱表', idx == 1 and len(sc.staves) == 2, msg):
        fails += 1
    if not check('有花括号', bool(sc.staff_groups), str(sc.staff_groups)):
        fails += 1

    print('[3] 写入配乐')
    meta = fill_accompaniment(sc, pattern='bass', mode='major')
    if not check('填了小节', meta.get('bars_filled') == 4, str(meta)):
        fails += 1
    has_notes = any(not n.is_rest for n in sc.staves[1].measures[0].notes)
    if not check('LH 有音', has_notes):
        fails += 1

    print('[4] alberti')
    meta2 = fill_accompaniment(sc, pattern='alberti', overwrite=True)
    n0 = len(sc.staves[1].measures[0].notes)
    if not check('阿尔贝蒂多音', n0 >= 4, f'n={n0}'):
        fails += 1

    print('[5] EditController')
    sc2 = blank_score(bars=4, layout='single')
    for i, midi in enumerate([67, 65, 64, 62]):
        sc2.staves[0].measures[i].notes = [
            Note(Pitch.from_midi(midi), NoteDuration.WHOLE)]
    ctl = EditController()
    ctl.score = sc2
    i2, m2 = ctl.add_part('bass')
    if not check('controller 加声部', i2 == 1, m2):
        fails += 1
    ok, m3 = ctl.fill_accompaniment('broken')
    if not check('controller 配乐', ok, m3):
        fails += 1

    print('[6] 命令登记')
    import llm_compose as L
    import help_doc as H
    if not check('/addpart 在 ARG', '/addpart' in L._ARG_CMDS):
        fails += 1
    if not check('/accomp 在 ARG', '/accomp' in L._ARG_CMDS):
        fails += 1
    cmds = {c['cmd'] for c in H.COMMANDS}
    if not check('help 含 /addpart', '/addpart' in cmds):
        fails += 1
    if not check('help 含 /accomp', '/accomp' in cmds):
        fails += 1

    print(f'\n{"全部通过" if fails == 0 else f"失败 {fails}"}')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
