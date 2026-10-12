# -*- coding: utf-8 -*-
"""钢琴大谱表四部和声 + 伴奏低音跳进收紧。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from music_score.assist_engine import blank_score
from music_score.models import ClefType
from music_score.satb_piano import build_satb_piano_score, apply_satb_to_score
from music_score.exporter import model_to_m21
import compose_kernel as K
from llm_compose import ComposeSession, compose_from_text, CommandOnly


def check(name, cond, detail=''):
    status = 'OK' if cond else 'FAIL'
    print(f'  [{status}] {name}' + (f' — {detail}' if detail and not cond else ''))
    if not cond:
        raise AssertionError(name)


def test_blank_piano():
    print('[1] blank_score(layout=piano)')
    sc = blank_score(layout='piano', bars=8, title='测试')
    check('两行谱表', len(sc.staves) == 2)
    check('RH 高音', sc.staves[0].clef == ClefType.TREBLE)
    check('LH 低音', sc.staves[1].clef == ClefType.BASS)
    check('花括号分组', bool(sc.staff_groups) and sc.staff_groups[0]['symbol'] == 'brace')
    check('8 小节', len(sc.staves[0].measures) == 8 == len(sc.staves[1].measures))


def test_satb_write():
    print('[2] build_satb_piano_score')
    sc, report, meta = build_satb_piano_score('I V vi IV', title='演示')
    check('大谱表', len(sc.staves) == 2)
    check('4 小节', meta['bars'] == 4)
    check('RH 有音符', all(len(m.notes) >= 1 for m in sc.staves[0].measures))
    check('LH 有音符', all(len(m.notes) >= 1 for m in sc.staves[1].measures))
    check('RH 含 chord_tones(S+A)', any(n.chord_tones for n in sc.staves[0].measures[0].notes))
    check('报告提及写入', '已写入钢琴大谱表' in report)
    m21 = model_to_m21(sc)
    check('导出两 Part', len(list(m21.parts)) == 2)


def test_satb_command():
    print('[3] /satb 命令端到端')
    session = ComposeSession()
    score, spec, note = compose_from_text(session, '/satb I V vi IV')
    check('返回 m21', score is not None)
    check('staff_groups', bool(spec.get('staff_groups')))
    check('note 含四声部', '四声部' in note or 'S' in note)
    check('2 parts', len(list(score.parts)) == 2)


def test_chords_write_alias():
    print('[4] /chords … write 别名')
    session = ComposeSession()
    score, spec, note = compose_from_text(session, '/chords I V vi IV write')
    check('write 产出谱', score is not None and len(list(score.parts)) == 2)
    # 纯顾问仍 CommandOnly
    try:
        compose_from_text(ComposeSession(), '/chords I V vi IV')
        check('/chords 不抛 CommandOnly', False)
    except CommandOnly as e:
        check('/chords 仍是报告', '根音运动' in e.message)


def _bass_root_midis(spec) -> list:
    """取低音声部每小节第一个根音事件的最低 MIDI。"""
    parts = spec.get('parts') or []
    if len(parts) < 2:
        return []
    bass = parts[-1] if 'piano' not in (parts[0].get('name') or '').lower() else parts[1]
    # 更稳：按 clef=bass 或最后一个 pitched part
    for p in reversed(parts):
        if (p.get('clef') or '') == 'bass' or 'bass' in (p.get('name') or '').lower():
            bass = p
            break
    else:
        bass = parts[-1]
    events = bass.get('events') or []
    # 按小节拆：用 kernel 导出的 bars 结构若有
    bars = {}
    # events 是扁平列表；从 finalize 的 parts[*].events
    # 简化：收集所有非休止最低音，按出现顺序取「像根音」的
    # 更好：读 kernel state — 用 run_program 后的 bars
    return events


def test_bass_leap_clamp():
    print('[5] 伴奏低音跨小节跳进收紧')
    # 进行：C–B–E 在固定八度下根音会跳 11 半音；收紧后应 ≤ 7（五度）
    from compose_kernel import SCRIPT_IMPL, _State
    prog = [
        {'script': 'new_score', 'args': {
            'title': 'bass-leap', 'tempo': 100, 'meter': '4/4',
            'voices': 2, 'mode': 'major', 'key_sharps': 0, 'bars': 8}},
        {'script': 'set_sections', 'args': {
            'sections': [{'name': 'A', 'bars': 8, 'meter': '4/4'}]}},
        {'script': 'set_harmony', 'args': {
            'section': 'A',
            'progression': ['I', 'vii°', 'iii', 'vi', 'IV', 'V', 'I', 'I']}},
        {'script': 'accompaniment', 'args': {
            'section': 'A', 'pattern': 'sustained', 'register': 'low', 'density': 1}},
    ]
    st = _State()
    for c in prog:
        SCRIPT_IMPL[c['script']](st, c['args'])
    bass = st.parts[st.bass_idx]
    roots = []
    for bar in range(1, 9):
        evs = bass['bars'].get(bar) or []
        for e in evs:
            m = K._lowest_midi_of_ev(e)
            if m is not None:
                # sustained：取和弦根音锚点（首个与和弦根同音级的音）
                roots.append(m)
                break
    # 更精确：用根音 PC 锚定（与 align 一致）
    roots = []
    for bar in range(1, 9):
        chord = st.harmony.get(bar)
        evs = bass['bars'].get(bar) or []
        root_pc = K._pc(chord[0]) if chord else None
        anchor = None
        for e in evs:
            p = e[0] if e else None
            if p is None:
                continue
            try:
                ms = ([K._midi(str(x)) for x in p]
                      if isinstance(p, (list, tuple)) else [K._midi(str(p))])
            except Exception:
                continue
            for m in ms:
                if root_pc is not None and m % 12 == root_pc:
                    anchor = m
                    break
            if anchor is not None:
                break
            if ms and anchor is None:
                anchor = min(ms)
        if anchor is not None:
            roots.append(anchor)
    check('取到 8 个低音根', len(roots) == 8, str(roots))
    leaps = [abs(roots[j + 1] - roots[j]) for j in range(len(roots) - 1)]
    check('跨小节最大跳进 ≤ 7', max(leaps) <= 7, f'roots={roots} leaps={leaps}')
    # 对照：未对齐时 I→vii° 在 oct2 是 C2=36 → B2=47（11）
    check('确实做过八度选择（含 B1）', 35 in roots or min(leaps) <= 5, f'roots={roots}')


def test_align_helper_unit():
    print('[6] _align_acc_bar_to_prev 单元')
    # prev=G2(43), 下一和弦根 C 固定写成 C2(36) → 应升到 C3(48) 差 5，或留 C2 差 7
    ev = [['C2', 'half'], ['G2', 'half']]
    out, new_prev = K._align_acc_bar_to_prev(ev, 43, ['C', 'E', 'G'], 24, 60, max_leap=7)
    first = K._lowest_midi_of_ev(out[0])
    check('对齐后距 prev ≤7', abs(first - 43) <= 7, f'first={first}')
    check('new_prev 更新', new_prev == first)


def test_chorale_stays_piano_grand():
    print('[7] 众赞歌默认钢琴大谱表（不再四行 SATB）')
    from llm_compose import (
        _expand_piano_to_grand, _ensure_instruments, _collapse_satb_to_piano_grand,
        _wants_piano_grand,
    )
    from style_skeleton import skeleton_spec
    check('piano → 两行', _expand_piano_to_grand(['piano']) == ['piano', 'piano'])
    prog = {'calls': [{'script': 'new_score', 'args': {
        'title': 'T', 'voices': 1, 'instruments': ['piano'], 'bars': 16}}]}
    plan = {'genre_id': 'sacred/chorale', 'instrumentation': ['piano'],
            'user_brief': '写一部众赞歌'}
    prog2, _note = _ensure_instruments(prog, plan, ['genre:sacred/chorale'])
    args = prog2['calls'][0]['args']
    check('ensure voices=2', args['voices'] == 2)
    check('ensure piano×2', args['instruments'] == ['piano', 'piano'])
    check('want grand', _wants_piano_grand(plan, ['genre:sacred/chorale'], '写一部众赞歌'))
    spec = {
        'title': 'X', 'bars': 2, 'time_signature': '4/4',
        'parts': [
            {'name': 'Soprano', 'events': [['G4', 'whole'], ['A4', 'whole']]},
            {'name': 'Alto', 'events': [['E4', 'whole'], ['F4', 'whole']]},
            {'name': 'Tenor', 'events': [['C4', 'whole'], ['D4', 'whole']]},
            {'name': 'Bass', 'events': [['C3', 'whole'], ['B2', 'whole']]},
        ],
    }
    msg = _collapse_satb_to_piano_grand(spec)
    check('collapse 成功', bool(msg))
    check('收成两行', len(spec['parts']) == 2)
    check('有花括号', bool(spec.get('staff_groups')))
    sk = skeleton_spec('genre:sacred/chorale', bars=16)
    names = [p.get('name') for p in (sk.get('parts') or [])]
    check('skeleton=Piano RH/LH', names == ['Piano RH', 'Piano LH'], str(names))
    check('skeleton 有 brace', bool(sk.get('staff_groups')))


def test_chorale_parallel_octaves_reported():
    print('[8] 众赞歌平行八度写入分析报告')
    from music_score.assist_engine import blank_score
    from music_score.models import Note, NoteDuration, Pitch, NoteName
    from score_studio import analyze_composition, _detect_parallels_for_score
    sc = blank_score(layout='piano', bars=2, title='众赞歌', composer='四部和声')
    sc.staves[0].measures[0].notes = [
        Note(Pitch(NoteName.G, 4), NoteDuration.WHOLE,
             chord_tones=[Pitch(NoteName.E, 4)])]
    sc.staves[1].measures[0].notes = [
        Note(Pitch(NoteName.C, 4), NoteDuration.WHOLE,
             chord_tones=[Pitch(NoteName.G, 3)])]
    sc.staves[0].measures[1].notes = [
        Note(Pitch(NoteName.A, 4), NoteDuration.WHOLE,
             chord_tones=[Pitch(NoteName.F, 4)])]
    sc.staves[1].measures[1].notes = [
        Note(Pitch(NoteName.D, 4), NoteDuration.WHOLE,
             chord_tones=[Pitch(NoteName.A, 3)])]
    spec = {'genre_id': 'sacred/chorale', 'genre': '众赞歌'}
    pars = _detect_parallels_for_score(sc, spec)
    check('检出平行八度', any('平行八度' in p for p in pars), str(pars))
    checks = analyze_composition(sc, spec)
    bad = [d for c, st, d in checks
           if c == '声部进行' and st.startswith('❌') and '平行八度' in d]
    check('报告标 ❌', bool(bad), str(bad))


if __name__ == '__main__':
    test_blank_piano()
    test_satb_write()
    test_satb_command()
    test_chords_write_alias()
    test_bass_leap_clamp()
    test_align_helper_unit()
    test_chorale_stays_piano_grand()
    test_chorale_parallel_octaves_reported()
    print('\n全部通过')
