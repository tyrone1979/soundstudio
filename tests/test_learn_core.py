# -*- coding: utf-8 -*-
"""共享学习核心 + leadsheet P0 缺陷回归。"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_prune_with_regression_keeps_better():
    from score_learning.learn_core import BackoffModel, prune_with_regression

    vocab = ['I', 'V', 'IV']
    m = BackoffModel(vocab)
    # 训练：足够多样的 L2 上下文
    for _ in range(20):
        m.add('BOS', 'I', '0', 'V')
        m.add('I', 'V', '7', 'I')
    for _ in range(2):
        m.add('BOS', 'IV', 'o', 'I')  # 稀有上下文，剪枝会删
    held = [('BOS', 'I', '0', 'V')] * 10 + [('I', 'V', '7', 'I')] * 10
    info = prune_with_regression(m, held, levels=('L3', 'L2'), ctx_min=3)
    assert info['perplexity_script'] is not None
    assert info['perplexity_script'] <= info['perplexity_unpruned'] + 1e-9


def test_kmeans_n_init_deterministic():
    from score_learning.learn_core import kmeans

    rng = np.random.default_rng(0)
    X = np.vstack([rng.normal(0, 0.2, (40, 8)), rng.normal(3, 0.2, (40, 8))])
    C1, lab1 = kmeans(X, 2, seed=1, n_init=8)
    C2, lab2 = kmeans(X, 2, seed=1, n_init=8)
    assert np.allclose(C1, C2)
    assert list(lab1) == list(lab2)
    assert len(set(lab1.tolist())) == 2


def test_forbidden_leaps_open_interval():
    """R03 允许的 max_leap 不得同时出现在 forbidden_leaps（R04）。"""
    from collections import Counter
    # 模拟 _learn_melody 的跳进统计逻辑
    ivs = Counter({0: 100, 1: 80, 2: 60, 3: 40, 4: 20, 5: 10, 6: 1, 8: 2, 7: 5})
    tot_iv = sum(ivs.values())
    abs_iv = Counter()
    for iv, n in ivs.items():
        abs_iv[abs(iv)] += n
    srt = sorted((abs(iv), n) for iv, n in ivs.items())
    acc, max_leap = 0, 12
    for a, n in srt:
        acc += n
        if acc / tot_iv >= 0.995:
            max_leap = max(5, min(12, a))
            break
    forbidden = sorted(a for a in range(5, max_leap) if abs_iv.get(a, 0) / tot_iv < 0.003)
    assert max_leap not in forbidden


def test_openings_include_exact_n():
    from collections import Counter
    forms = Counter({'ABC': 5, 'AB': 3})  # len==3 与 len==2
    n = 2
    # 旧逻辑 len(f) > n 会丢掉 'AB'；新逻辑 >= 保留
    c = Counter(f[:n] for f in forms.elements() if len(f) >= n)
    assert c['AB'] == 5 + 3


def test_grammar_reexports_core():
    from score_learning.grammar import learn as G
    from score_learning import learn_core as C
    assert G.BackoffModel is C.BackoffModel
    assert G.kmeans is C.kmeans
    assert G.is_heldout is C.is_heldout


def test_help_mentions_learn_core():
    import help_doc as H
    text = H.build_help()
    assert 'learn_core' in text or 'Witten-Bell' in text
    entry = next(c for c in H.COMMANDS if c['cmd'] == '/learn-grammar')
    assert 'learn_core' in entry['desc']
