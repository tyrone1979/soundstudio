# -*- coding: utf-8 -*-
"""语料 NN + 大师审计门禁单测。"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import evaluate as ev
import master_audit as MA
import style_skeleton as sk
from compose_kernel import run_program


@pytest.fixture(scope='module')
def mazurka_spec():
    return run_program(sk.build_skeleton_calls(
        'genre:dance/mazurka', bars=16, voices=3, seed='ma-test-mz',
        fugue='off'))


def test_corpus_piece_pool_mazurka():
    pool = ev.corpus_piece_pool('mazurka')
    assert len(pool) >= 24
    assert all('vector' in p and 'name' in p for p in pool)


def test_nearest_corpus_piece_reproducible(mazurka_spec):
    a = ev.nearest_corpus_piece(mazurka_spec, 'mazurka')
    b = ev.nearest_corpus_piece(mazurka_spec, 'mazurka')
    assert a['name'] == b['name']
    assert a['similarity'] == b['similarity']
    assert a['n_pool'] >= 24
    # 骨架生成不应整曲撞语料阈值
    assert a['similarity'] is None or a['similarity'] < ev.CORPUS_PIECE_NEAR_DUP + 1e-9


def test_nearest_corpus_fragment_has_windows(mazurka_spec):
    r = ev.nearest_corpus_fragment(
        mazurka_spec, 'mazurka', max_corpus_pieces=6, hop=4)
    assert r['n_gen_windows'] >= 1
    assert r['n_pool'] >= 1
    if r['similarity'] is not None:
        assert -1.0 <= r['similarity'] <= 1.0


def test_evaluate_report_includes_corpus_section(mazurka_spec):
    md = ev.evaluate_report(mazurka_spec, pack_id='mazurka',
                            want={'bars': 16})
    assert '语料对照' in md
    assert '语料单曲最近邻' in md


def test_symbolic_gate_structure(mazurka_spec):
    gate = MA._symbolic_gate(mazurka_spec, 'mazurka', frag_max_pieces=6)
    assert 'ok' in gate
    for key in ('style_top3', 'corpus_piece', 'corpus_fragment'):
        assert key in gate['checks']
        assert 'ok' in gate['checks'][key]


def test_listening_pass_thresholds():
    rubric = MA._load_rubric()
    good = {
        'melody_memorable': 4, 'phrase_breath': 4, 'harmony_clear': 5,
        'texture_balance': 4, 'genre_identity': 4,
        'mazurka_accent': 4, 'mazurka_dance': 4, 'mazurka_piano': 5,
    }
    bad_hard = dict(good, mazurka_accent=2)
    pending = {'melody_memorable': 4}
    assert MA._listening_pass(good, 'mazurka', rubric)['ok'] is True
    assert MA._listening_pass(bad_hard, 'mazurka', rubric)['ok'] is False
    assert MA._listening_pass(pending, 'mazurka', rubric)['pending'] is True


def test_resolve_pilot_packs():
    for q in ('mazurka', 'fugue', 'swing-standard'):
        assert MA._resolve_pack_meta(q) is not None
