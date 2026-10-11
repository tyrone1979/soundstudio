# -*- coding: utf-8 -*-
"""``hard_rules`` 必须是**活数据**，不能只当报告文本。

背景 —— 这是一条被反复踩的坑：
``leadsheet/learn.py::_hard_rules`` 只产出 ``{id, rule}`` 文本，
``leadsheet/check.py`` 于是把 0.70 / 0.85 两个阈值**各写了一份字面量**。
后果：改learn.py 的阈值对判定行为毫无影响，"只改 learn.py 等于白改"。

现在 learn 侧把阈值与语料实测值一并写进 ``hard_rules[*].param``，
check侧优先读 YAML，旧 YAML 才退回常量。本测试锁住这个契约。

运行：``QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_hard_rules_live.py -q``
"""
from __future__ import annotations

import copy
import glob
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import yaml  # noqa: E402

from score_learning.leadsheet import check as C  # noqa: E402
from score_learning.leadsheet import learn as L  # noqa: E402

PACKS = sorted(glob.glob(os.path.join(
    ROOT, 'skills', 'composition', 'genre', 'packs', '*', '*', 'grammar.yaml')))


def _subs():
    for f in PACKS:
        d = yaml.safe_load(open(f, encoding='utf-8')) or {}
        for name, sub in (d.get('subs') or {}).items():
            yield os.path.basename(os.path.dirname(f)), name, sub


ALL_SUBS = list(_subs())


# ----------------------------------------------------------------------
# 1. learn 侧：阈值常量只有一份，且真的挂进了 YAML
# ----------------------------------------------------------------------
def test_thresholds_are_single_source():
    """learn.py 必须导出阈值常量，checker 不得再写第二份字面量。"""
    assert hasattr(L, 'STRICT_CT_THRESHOLD')
    assert hasattr(L, 'STRICT_STEP_THRESHOLD')
    assert L.STRICT_CT_THRESHOLD == 0.70
    assert L.STRICT_STEP_THRESHOLD == 0.85
    src = open(C.__file__, encoding='utf-8').read()
    # check.py 里不允许再出现裸字面量 0.7 / 0.85（0.75 是时值，无关）
    for bad in ('>= 0.7', '>= 0.85', '>=0.7', '>=0.85'):
        assert bad not in src, f'check.py 仍有硬编码阈值：{bad}'


@pytest.mark.parametrize('family,name,sub', ALL_SUBS,
                         ids=[f'{a}-{b}' for a, b, _ in ALL_SUBS])
def test_rules_carry_machine_params(family, name, sub):
    """每条可机器执行的规则都必须带 param。"""
    hr = {r['id']: r for r in (sub.get('hard_rules') or [])}
    assert 'R01' in hr and 'R03' in hr, f'{family}/{name} 缺 R01/R03'
    for rid in ('R01', 'R02', 'R03', 'R04', 'R05', 'R10'):
        if rid not in hr:
            continue
        p = hr[rid].get('param')
        assert isinstance(p, dict) and p, \
            f'{family}/{name} 的 {rid} 没有 param —— 只改 learn.py 将无效'


def test_param_matches_corpus():
    """param 里的语料实测值必须与 melody 块一致（防止 learn 内部写错来源）。"""
    for family, name, sub in ALL_SUBS:
        mel = sub['melody']
        cs = dict((k, p) for k, p, _n in mel['class_by_strength'].get('S', []))
        hr = {r['id']: r for r in (sub.get('hard_rules') or [])}
        p01 = hr['R01']['param']
        assert abs(p01['corpus_ct_rate'] - float(cs.get('CT', 0.0))) < 5e-4, \
            f'{family}/{name} R01.corpus_ct_rate 与语料不符'
        assert p01['strict'] == (p01['corpus_ct_rate'] >= p01['strict_ct_threshold'])
        p02 = hr['R02']['param']
        assert abs(p02['corpus_step_rate']
                   - float(mel['nct_step_either'])) < 5e-4, \
            f'{family}/{name} R02.corpus_step_rate 与语料不符'
        assert hr['R03']['param']['max_leap'] == mel['max_leap']


# ----------------------------------------------------------------------
# 2. check 侧：真的读 YAML —— 篡改 param 必须改变判定
# ----------------------------------------------------------------------
def test_check_reads_yaml_not_literals():
    """把 YAML 里的 strict 翻成 False，checker 必须跟着变。

    这是「不再是死数据」的决定性证据：改 YAML 就改行为。
    """
    sub = copy.deepcopy(ALL_SUBS[0][2])
    hr = {r['id']: r for r in sub['hard_rules']}
    assert hr['R01']['param']['strict'] in (True, False)

    # 直接改判定所依据的字段
    rid = 'R01' if 'R01' in hr else 'R02'
    orig = hr[rid]['param']['strict']
    hr[rid]['param']['strict'] = not orig
    got = C._rule_param(sub, rid, 'strict', None)
    assert got == (not orig), \
        'checker 未读取 YAML 的 param，仍在用硬编码字面量'


def test_legacy_yaml_falls_back():
    """旧 YAML（无 param）不能崩，应退回常量且行为与历史一致。"""
    sub = copy.deepcopy(ALL_SUBS[0][2])
    for r in sub['hard_rules']:
        r.pop('param', None)
    assert C._has_param(sub, 'R01') is False
    assert C._rule_param(sub, 'R01', 'strict_ct_threshold', 0.70) == 0.70
    assert C._rule_param(sub, 'R03', 'max_leap',
                         sub['melody']['max_leap']) == sub['melody']['max_leap']


def test_max_leap_param_is_used():
    """R03 的 max_leap 由 YAML 提供（改动 YAML 即改动跳进上限）。"""
    sub = copy.deepcopy(ALL_SUBS[0][2])
    hr = {r['id']: r for r in sub['hard_rules']}
    assert C._rule_param(sub, 'R03', 'max_leap', None) == hr['R03']['param']['max_leap']
    hr['R03']['param']['max_leap'] = 3
    assert C._rule_param(sub, 'R03', 'max_leap', None) == 3


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q']))
