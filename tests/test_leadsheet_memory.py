# -*- coding: utf-8 -*-
"""乐谱"记忆"回归测试：段落再现 / 引用 / 变奏、跨段动机回忆、带编号段落标签（A1A2B1A3）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from score_learning.leadsheet import script as S  # noqa: E402
from score_learning.leadsheet.compose import (HEAD_BARS, _memory_plan,  # noqa: E402
                                              _letter_sections, _section, compose)

DUR = {'whole': 4, 'dotted_half': 3, 'half': 2, 'dotted_quarter': 1.5, 'quarter': 1,
       'dotted_eighth': .75, 'eighth': .5, '16th': .25, '32nd': .125}


def _kinds(sections):
    return [(m['kind'], m['src']) for m in _memory_plan(sections)]


def test_memory_plan_letters():
    assert _kinds(_letter_sections('AABA')) == [
        ('new', None), ('repeat', 'A'), ('new', None), ('repeat', 'A')]


def test_memory_plan_numbered_sections():
    secs = [_section('A1', role='introduce'), _section('A2', role='sequence', ref='A1'),
            _section('B1', role='contrast'), _section('A3', role='quote', ref='A1'),
            _section('A1')]
    assert _kinds(secs) == [('new', None), ('variant', 'A1'), ('new', None),
                            ('quote', 'A1'), ('repeat', 'A1')]
    # 同族、无 ref 也按变奏处理；变奏段开头来自来源段，结尾是自己
    m = _memory_plan([_section('A1'), _section('B1'), _section('A2')])
    assert m[2]['kind'] == 'variant' and m[2]['head'] == 'A1' and m[2]['tail'] == 'A2'


def _parts_ql(spec):
    return {p['name']: sum(DUR[e[1]] for e in p['events']) for p in spec['parts']}


def test_jazz_numbered_form_parts_aligned_and_variants():
    sc = S.load('jazz')
    found_variant = False
    for seed in ('0', '1', '2'):
        r = compose(sc, 'swing', 'major', 0, 32, seed=seed)
        assert not r['violations']
        assert len(r['units']) == 4 == len(r['plan']['sections'])
        assert len(set(_parts_ql(r['spec']).values())) == 1, _parts_ql(r['spec'])
        for u in r['units']:
            if u['variant_of']:
                found_variant = True
                src = r['units'][u['variant_of'] - 1]
                # 变奏段前 HEAD_BARS 小节的和弦与来源段一致
                assert u['chords'].split(' | ')[:HEAD_BARS] == src['chords'].split(' | ')[:HEAD_BARS]
                assert all('沿用' in x for x in u['rhythm'][:HEAD_BARS])
                assert u['memory'].startswith('变奏')
    assert found_variant


def test_motif_recall_learned_and_used():
    sc = S.load('sonata')
    mr = sc['subs']['allegro']['rhythm']['motif_recall']
    assert mr['p_same'] + mr['p_shift'] > 0.05 and mr['bars'] > 100
    recalls = 0
    for seed in range(6):
        r = compose(sc, 'allegro', 'major', 0, 32, seed=str(seed))
        assert not r['violations']
        recalls += sum('回忆第' in x for u in r['units'] for x in u['rhythm'])
    assert recalls > 0


def test_motif_recall_deterministic():
    sc = S.load('folk')
    a = compose(sc, 'reel', 'major', 2, 32, seed='m')
    b = compose(sc, 'reel', 'major', 2, 32, seed='m')
    assert a['spec']['parts'] == b['spec']['parts']
    assert [u['rhythm'] for u in a['units']] == [u['rhythm'] for u in b['units']]
