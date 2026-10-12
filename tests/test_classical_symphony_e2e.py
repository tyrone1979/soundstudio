# -*- coding: utf-8 -*-
"""简单提示词扩写 + 交响编制兜底 e2e（小夜曲包已下线）。"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import brief_expand as BE
import llm_compose as L
import score_reverse as R


def test_brief_expand_schubert():
    meta = BE.expand_brief('写一首明朗的交响曲快板乐章，像舒伯特早期')
    assert meta['template'] == 'schubert_allegro'
    assert meta['pack_hint'] == 'symphony'
    assert '交响乐团' in meta['expanded'] or '双管' in meta['expanded']


def test_brief_expand_symphony():
    """普通「交响」短句不再扩写（改走 /compose-grammar）；expand_brief 仍可手工调用。"""
    assert not BE.should_expand('写一首C大调交响乐章')
    assert not BE.should_expand('做一首交响')
    meta = BE.expand_brief('写一首C大调交响乐章')
    assert meta['template'] == 'symphony_allegro'
    assert meta['pack_hint'] == 'symphony'
    assert meta['key_sharps'] == 0


def test_should_expand_schubert_still():
    assert BE.should_expand('写一首舒伯特早期交响曲快板')
    assert BE.should_expand('写一首明朗的交响曲快板乐章，像舒伯特早期')


def test_should_expand_skips_professional():
    pro = '【作品】xxx\n【编制】yyy\n' + ('字' * 50)
    assert not BE.should_expand(pro)


def test_should_expand_skips_serenade():
    """小夜曲包已删：短句不再走假小夜曲扩写。"""
    assert not BE.should_expand('写一首G大调弦乐小夜曲快板')


def test_ensure_instruments_from_symphony_pack():
    """编制来自风格包 foundation，不按关键词写死交响模板。"""
    def _ns(instruments=None, voices=2):
        args = {'voices': voices, 'title': 't', 'key_sharps': 0,
                'mode': 'major', 'meter': '4/4', 'tempo': 120, 'bars': 16}
        if instruments:
            args['instruments'] = instruments
        return {'calls': [{'script': 'new_score', 'args': args}]}

    sym_plan = {
        'user_brief': '写一首明朗的交响曲快板乐章',
        'genre_id': 'classical/symphony',
    }
    L._bind_learned_genre(L.ComposeSession(), sym_plan, ['compose-full'])
    prog2, _note2 = L._ensure_instruments(_ns(), sym_plan)
    args2 = prog2['calls'][0]['args']
    assert args2['instruments'][0] == 'violin'
    assert 'contrabass' in args2['instruments']
    assert len(args2['instruments']) >= 8


def test_ensure_style_contract_symphony():
    prog = {'calls': [{'script': 'new_score', 'args': {
        'title': 't', 'key_sharps': 0, 'mode': 'major', 'meter': '4/4',
        'tempo': 120, 'bars': 16, 'voices': 8,
    }}]}
    out, note = L._ensure_style_contract(
        prog, ['genre:classical/symphony'],
        {'user_brief': '交响曲'})
    assert out['calls'][0]['args']['style'] == 'classical/symphony'
    assert 'style=' in note


def test_mozart_reverse_still_note_equal():
    """K.525 逆向往返仍可用（与风格包无关的回归）。"""
    src = R.load_mozart_nachtmusik_spec(short=True)
    text = R.build_score_description(src)
    out = R.text_to_spec(text)
    assert R.specs_note_equal(src, out)


def test_schubert_style_injects_symphony_winds():
    """交响编制来自风格包 foundation，经 bind + ensure 注入。"""
    meta = BE.expand_brief('写一首明朗的交响曲快板乐章，像舒伯特早期')
    plan = {
        'user_brief': meta['expanded'],
        'genre_id': 'classical/symphony',
    }
    L._bind_learned_genre(L.ComposeSession(), plan, ['compose-full'])
    program = {'calls': [
        {'script': 'new_score', 'args': {
            'title': '早春', 'key_sharps': 2, 'mode': 'major',
            'meter': '4/4', 'tempo': 112, 'bars': 32, 'seed': 'e2e-sch-v1',
            'voices': 2,
        }},
    ]}
    prog2, _note = L._ensure_instruments(program, plan)
    inst = prog2['calls'][0]['args']['instruments']
    assert inst[0] == 'violin'
    assert 'contrabass' in inst
    assert prog2['calls'][0]['args']['voices'] == len(inst)
    assert len(inst) >= 8
