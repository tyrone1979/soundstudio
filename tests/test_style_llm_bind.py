# -*- coding: utf-8 -*-
"""风格正式名剥离 + 已学包绑定（不用关键词表挂包）。"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import composition_skills as skill_lib
from llm_compose import (
    ComposeSession, _bind_learned_genre, _normalize_instrument_list,
    resolve_style_phrase,
)


def test_style_peels_formal_name_and_leftover():
    label, leftover, gid = resolve_style_phrase(
        '摇摆标准曲（爵士） 写一首爵士摇摆标准曲，Bb大调',
        llm_available=True,
    )
    assert gid == 'genre:jazz/swing-standard'
    assert '摇摆' in label
    assert leftover  # must compose
    assert 'Bb' in leftover or '爵士' in leftover


def test_style_exact_name_no_compose_leftover():
    label, leftover, gid = resolve_style_phrase(
        '摇摆标准曲（爵士）', llm_available=True)
    assert gid == 'genre:jazz/swing-standard'
    assert leftover == ''


def test_free_nl_with_llm_still_has_leftover_for_compose():
    # 无正式名前缀时：整段进 leftover，开跑创曲；genre 留给 LLM 目录
    label, leftover, gid = resolve_style_phrase(
        '写一首爵士摇摆标准曲，Bb大调', llm_available=True)
    assert leftover
    assert gid is None


def test_match_genre_by_name_no_keywords():
    # 「七和弦」不得因关键词误挂爵士
    assert skill_lib.match_genre_by_name('七和弦进行', learned_only=True) is None
    g = skill_lib.match_genre_by_name('摇摆标准曲（爵士）', learned_only=True)
    assert g and g['pack_id'] == 'swing-standard'


def test_bind_learned_genre_raises_bars_and_instruments():
    sess = ComposeSession()
    plan = {
        'bars': 8,
        'genre_id': 'jazz/swing-standard',
        'instrumentation': ['Piano', 'Double Bass', 'Drum Kit'],
    }
    ids = _bind_learned_genre(sess, plan, ['compose-full'])
    assert 'genre:jazz/swing-standard' in ids
    assert int(plan['bars']) >= 16
    assert plan['instrumentation'] == [
        'trumpet', 'piano', 'contrabass']


def test_normalize_folk_instruments():
    # Bodhrán 内核无鼓组实现：丢弃，不顶替成定音鼓
    assert _normalize_instrument_list(
        ['Fiddle', 'Tin Whistle', 'Bodhrán']
    ) == ['violin', 'flute']


def test_catalog_learned_only():
    cat = skill_lib._genre_catalog_block(learned_only=True)
    assert 'jazz/swing-standard' in cat
    assert 'nocturne' not in cat
    assert 'epic-battle' not in cat


def test_technical_prompts_still_compose_full_only():
    ids, _ = skill_lib.resolve_skills(
        '减七和弦解决的四声部密集排列', llm_available=True)
    assert ids == ['compose-full']
