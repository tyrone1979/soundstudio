# -*- coding: utf-8 -*-
"""终止式类型分布实验（论文 §5.3 的替代指标）关键性质。

不重跑完整实验（要生成 72 首草稿），而是锁住三条**论文必须依赖**的性质：

1. R11 关闭臂真的生效——末三拍被释放后类型分布发生实质变化；
2. 关掉 R11 之后分布**远离**而不是靠近巴赫（这否证了「R11 是唯一决定项」）；
3. 草稿的类型分布与 C 规则池几乎重合（这就是为什么它**不能**替代
   cadence match 当独立证据——论文已按此改写）。
"""
from __future__ import annotations

import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'runs', 'experiments', 'e3_cadence_type.json')


@pytest.fixture(scope='module')
def res():
    if not os.path.exists(OUT):
        pytest.skip('先跑 venv/bin/python -m score_learning.grammar.cadence_type_dist')
    with open(OUT, encoding='utf-8') as f:
        return json.load(f)


@pytest.mark.parametrize('mode', ['major', 'minor'])
def test_r11_off_actually_changes_distribution(res, mode):
    """R11 关闭臂必须与完整草稿显著不同，否则消融没生效。"""
    t = res['arms'][mode]['tvd']
    assert t['r11off_vs_drafts'][0] > 0.20, t
    assert t['r11off_vs_drafts'][1] > 0.0, '自助 CI 下界不应触及 0'


@pytest.mark.parametrize('mode', ['major', 'minor'])
def test_r11_off_moves_away_from_bach(res, mode):
    """核心结论：关掉 R11 后类型分布离巴赫更远，说明位置类 H 规则也在塑形。"""
    t = res['arms'][mode]['tvd']
    d = t['drafts_vs_bach'][0]
    r = t['r11off_vs_bach'][0]
    assert r > d, f'{mode}: R11关 {r} 应大于 完整草稿 {d}'
    # 内部句（排除末句必须落主目标的效应）同样成立
    assert t['r11off_interior_vs_bach_interior'][0] > \
           t['drafts_interior_vs_bach_interior'][0]


@pytest.mark.parametrize('mode', ['major', 'minor'])
def test_drafts_track_the_c_rule_pool(res, mode):
    """草稿分布 ≈ C 规则池分布 ⇒ 该指标是 R11 抽样分布的重述，不是独立证据。"""
    t = res['arms'][mode]['tvd']
    assert t['drafts_vs_pool'] < 0.20, t
    assert t['r11off_vs_pool'] > 0.30, t


@pytest.mark.parametrize('mode', ['major', 'minor'])
def test_drafts_differ_from_bach(res, mode):
    """草稿与巴赫确有差异，且自助 CI 不含 0（否则该指标什么也没测出来）。"""
    t = res['arms'][mode]['tvd']
    p, lo, hi = t['drafts_vs_bach']
    assert lo > 0.0, t
    assert 0.05 < p < 0.60, t


@pytest.mark.parametrize('mode', ['major', 'minor'])
def test_no_bare_tonic_cadence_in_drafts(res, mode):
    """R11 的实际作用之一：草稿里不会出现无proper approach的裸主和弦收束。"""
    d = res['arms'][mode]['grammar_drafts']['counts']
    b = res['arms'][mode]['bach_heldout']['counts']
    assert d.get('主和弦终止', 0) == 0
    assert b.get('主和弦终止', 0) > 0


def test_modulation_cadences_are_underproduced(res):
    """大调草稿的离调/转调类终止显著少于巴赫——一条可解释的生成端局限。"""
    def bucket(arm):
        c = res['arms']['major'][arm]['counts']
        return sum(c.get(k, 0) for k in ('离调终止（副属→目标）', '离调半终止（停于副属）',
                                         '转调终止（离调后完全终止）', '转调终止（离调后变格终止）'))
    d = bucket('grammar_drafts')
    b = bucket('bach_heldout')
    assert d < 0.65 * b, f'草稿 {d} 应远少于巴赫 {b}'
