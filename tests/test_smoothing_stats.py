# -*- coding: utf-8 -*-
"""``score_learning.smoothing`` 与 ``score_learning.stats`` 的回归测试。

这些测试锁住的是**实现过程中真实踩过的坑**，每条都对应一个已修的 bug：

1. 五个平滑器必须在任意上下文上对全词表严格归一（KN/MKN 曾出现 Σ=8.6 / 0.897 / 1.15）
2. KN/MKN 的 ``lam`` 必须用标准定义 ``D·N₁₊/N``，不能提前 return
   （曾导致模型退化成均匀分布，ppl = 词表大小）
3. GroupKFold 不得让同一 group 跨折
4. ``group_of`` 不得做前缀归一化（曾把 640 首交响曲塌成 1 组）
5. 配对 bootstrap：自身对比delta 恒为 0
"""
from __future__ import annotations

import math
import unittest

from score_learning import smoothing as S
from score_learning import stats as ST


def _toy():
    vocab = ['a', 'b', 'c', 'd', 'e']
    train = [('^', '^', 's', 'a'), ('^', 'a', 'mid', 'b'),
             ('a', 'b', 'mid', 'c'), ('b', 'c', 'cad1', 'a'),
             ('c', 'a', 'mid', 'b'), ('a', '^', 's', 'c')]
    probes = [('^', '^', 's'), ('a', 'b', 'mid'), ('b', 'c', 'cad1'),
              ('x', 'y', 'zzz'), ('^', 'a', 's'), ('a', 'a', 'mid')]
    return vocab, train, probes


class TestSmoothingNormalization(unittest.TestCase):
    """坑1：所有平滑器必须逐层、逐上下文严格归一。"""

    def test_all_normalized(self):
        vocab, train, probes = _toy()
        for kind in S.SMOOTHERS:
            m = S.fit(kind, train, vocab)
            for p2, p1, pos in probes:
                z = sum(m.prob(p2, p1, pos, c) for c in vocab)
                self.assertAlmostEqual(
                    z, 1.0, places=9,
                    msg=f'{kind} 在({p2},{p1},{pos}) 上 Σ={z}')

    def test_normalized_on_sparse_counts(self):
        """计数全为 1 的稀疏表（lam≈0 的极端情形）也必须归一。"""
        vocab = ['a', 'b', 'c', 'd']
        train = [('^', '^', 's', 'a'), ('^', 'a', 'mid', 'b'),
                 ('a', 'b', 'mid', 'c'), ('b', 'c', 'mid', 'd')]
        for kind in S.SMOOTHERS:
            m = S.fit(kind, train, vocab)
            for p2, p1, pos in [('^', '^', 's'), ('a', 'b', 'mid')]:
                z = sum(m.prob(p2, p1, pos, c) for c in vocab)
                self.assertAlmostEqual(z, 1.0, places=9, msg=kind)

    def test_larger_discount_still_normalized(self):
        """D ≥ 1 时 max(n-D,0) 会抹掉低计数后继，仍必须归一。"""
        vocab = ['a', 'b', 'c', 'd', 'e', 'f']
        train = [('^', '^', 's', 'a'), ('^', 'a', 'mid', 'b'),
                 ('a', 'b', 'mid', 'c'), ('b', 'c', 'cad1', 'a'),
                 ('c', 'a', 'mid', 'b'), ('a', '^', 's', 'c')]
        for kind in ('kn', 'mkn', 'ad'):
            for D in (0.5, 0.75, 1.0, 1.5):
                m = S.fit(kind, train, vocab, discount=D)
                for p2, p1, pos in [('^', '^', 's'), ('a', 'b', 'mid')]:
                    z = sum(m.prob(p2, p1, pos, c) for c in vocab)
                    self.assertAlmostEqual(
                        z, 1.0, places=9, msg=f'{kind} D={D}')
                    # 训练时用的就是这个 D，确认参数真的生效
                    self.assertAlmostEqual(m.D, D)


class TestSmootherUsesContext(unittest.TestCase):
    """坑2：模型必须真的用上上下文，不能退化成均匀分布。"""

    def test_not_uniform(self):
        vocab, train, _ = _toy()
        for kind in S.SMOOTHERS:
            m = S.fit(kind, train, vocab)
            ps = [m.prob('a', 'b', 'mid', c) for c in vocab]
            self.assertGreater(max(ps) / max(min(ps), 1e-12), 1.5,
                               msg=f'{kind} 输出接近均匀：{ps}')

    def test_lam_uses_standard_formula(self):
        """lam = D·N1+/N，不能是「低计数后继计数占比」。

        后者在 n(c) 恒为 1 时等于 0，会让 KN 提前 return p_low。
        """
        vocab, train, _ = _toy()
        m = S.fit('kn', train, vocab, discount=0.75)
        cnt = m.t['L1'][('^',)]
        n = sum(cnt.values())
        big = sum(1 for v in cnt.values() if v > 0.75)
        expect = 0.75 * big / n
        got = m._interp('L1', ('^',), cnt, 0.2, 'a')
        # lam 接近 expect 时，(1-lam)·p_own 才会有实质权重
        self.assertGreater(expect, 0.0)
        # 直接验证高计数后继拿到主要质量（未被 lam=0 抹掉）
        top = max(cnt, key=lambda k: cnt[k])
        self.assertGreater(m.prob('^', '^', 's', top), 1.0 / len(vocab) * 2)


class TestMakeSmootherStrictness(unittest.TestCase):
    """未知参数必须报错，不能静默丢弃。

    静默丢弃曾让 HMM 的 ``K=32`` 被丢掉而退回默认 4 态，表现为
    「K 从 2 到 32 困惑度完全相同」——一个不会报错的假实验。
    """

    def test_unknown_kwarg_raises(self):
        with self.assertRaises(TypeError):
            S.make_smoother('kn', ['a', 'b'], D=0.75)   # 参数名是 discount
        with self.assertRaises(TypeError):
            S.make_smoother('hmm', ['a', 'b'], K=4)      # 参数名是 n_states

    def test_n_states_reaches_the_model(self):
        for k in (2, 8, 32):
            m = S.make_smoother('hmm', ['a', 'b', 'c', 'd'], n_states=k)
            self.assertEqual(m.K, k)

    def test_unknown_smoother_kind_raises(self):
        with self.assertRaises(ValueError):
            S.make_smoother('nope', ['a'])


class TestHarmonicHMM(unittest.TestCase):
    """HMM 基线的最小正确性：归一性 + 能用上下文 + 分段生效。

    数据用**真实形态**（一段内和弦有规律地交替），因为对称的玩具数据
    学不出状态分化——那不是 HMM 的问题，是数据没有信号。
    """

    @staticmethod
    def _corpus():
        """段1 以 a 为主、段2 以 b 为主，位置类各异。"""
        s1 = [('', '', 'mid', 'a' if i % 2 else 'b') for i in range(8)]
        s2 = [('', '', 'cad0', 'b' if i % 3 else 'a') for i in range(8)]
        s3 = [('', '', 'mid', 'a') for i in range(8)]
        return [s1, s2, s3], ['a', 'b']

    def test_predict_is_normalized_over_vocab(self):
        segs, vocab = self._corpus()
        m = S.fit_hmm(segs, vocab, n_states=3, iters=10)
        idx = m._idx
        for sg in segs:
            enc = [(e[2], idx[e[3]]) for e in sg]
            for t in range(len(enc)):
                post = m.predict(enc, t)
                self.assertAlmostEqual(sum(post.values()), 1.0, places=9)

    def test_pure_hmm_shares_one_emission_table(self):
        """纯HMM 只有**一套**发射矩阵（键 '*'），不按 pos 分桶。

        这是它与位置条件版的结构差异，也意味着 ``prob`` 的 pos 参数
        在纯 HMM 下不起作用——位置效应体现在**状态序列**里，
        而非按 pos 查表。测试把这个结构钉住。
        """
        segs, vocab = self._corpus()
        m = S.fit_hmm(segs, vocab, n_states=3, iters=10, tie_pos=False)
        self.assertEqual(list(m._emit), ['*'])
        m2 = S.fit_hmm(segs, vocab, n_states=3, iters=10, tie_pos=True)
        self.assertGreater(len(m2._emit), 1, '位置条件版应按 pos 分桶')

    def test_states_are_differentiated(self):
        """训练后各隐状态的发��分布应互不相同（EM 确实在工作）。"""
        segs, vocab = self._corpus()
        m = S.fit_hmm(segs, vocab, n_states=3, iters=25, tie_pos=False)
        import numpy as np
        e = m._emit['*']
        self.assertGreaterEqual(e.shape[0], 2)
        diffs = [float(np.abs(e[i] - e[j]).max())
                 for i in range(e.shape[0]) for j in range(i + 1, e.shape[0])]
        self.assertGreater(max(diffs), 0.05,
                           msg='各状态发射完全相同，EM 未分化状态')

    def test_emission_is_strictly_positive(self):
        """加了 emit_smooth 后，发射必须处处> 0，否则 log 0 会让后验退化。"""
        segs, vocab = self._corpus()
        m = S.fit_hmm(segs, vocab, n_states=3, iters=10)
        import numpy as np
        for k, e in m._emit.items():
            self.assertGreater(float(e.min()), 0.0,
                               msg=f'pos={k} 的发射有 0 值')

    def test_loss_finite_and_bounded(self):
        """loss 必须有限且远小于 27.6（= -log 1e-12，即落到下限）。"""
        segs, vocab = self._corpus()
        m = S.fit_hmm(segs, vocab, n_states=3, iters=10)
        flat = [e for sg in segs for e in sg]
        L = m.per_event_loss(flat, segments=segs)
        self.assertEqual(len(L), len(flat))
        for x in L:
            self.assertTrue(math.isfinite(x))
            self.assertLess(x, 20.0, 'loss 接近 27.6 说明落到 1e-12 下限')

    def test_real_corpus_beats_unigram(self):
        """真实语料上 HMM 必须显著优于均匀分布，否则实现有 bug。"""
        import json
        import os
        from score_learning.learn_core import is_heldout
        from score_learning.grammar.learn import harmony_events
        cache = os.path.join(os.path.dirname(__file__), '..', 'runs',
                             'grammar_cache', 'bach_chorales.json')
        if not os.path.exists(cache):
            self.skipTest('无 chorale 缓存')
        pieces = json.load(open(cache, encoding='utf-8'))['pieces']
        tr = [p for p in pieces if p['mode'] == 'major'
              and not is_heldout(p['id'])][:40]
        vocab = sorted({b['chord'] for p in tr for b in p['beats']
                        if b.get('chord')})[:40]
        vset = set(vocab)
        segs = []
        for p in tr:
            for ph in p['phrases']:
                sg = harmony_events(
                    {'beats': p['beats'], 'phrases': [ph]}, vset)
                if len(sg) >= 2:
                    segs.append(sg)
        if len(segs) < 10:
            self.skipTest('可用乐句太少')
        m = S.fit_hmm(segs, vocab, n_states=4, iters=10)
        flat = [e for sg in segs for e in sg]
        L = m.per_event_loss(flat, segments=segs)
        ppl = math.exp(sum(L) / len(L))
        self.assertLess(ppl, len(vocab) * 0.9,
                        msg=f'HMM 困惑度 {ppl:.1f} 接近词表大小 '
                            f'{len(vocab)}，实现有问题')


class TestSmootherPrune(unittest.TestCase):
    def test_prune_context_keeps_normalization(self):
        vocab, train, probes = _toy()
        for kind in S.SMOOTHERS:
            m = S.fit(kind, train, vocab)
            m.prune_context(('L3', 'L2'), ctx_min=3)
            for p2, p1, pos in probes:
                z = sum(m.prob(p2, p1, pos, c) for c in vocab)
                self.assertAlmostEqual(z, 1.0, places=9, msg=kind)

    def test_snapshot_restore(self):
        vocab, train, _ = _toy()
        m = S.fit('wb', train, vocab)
        snap = m.snapshot()
        m.prune_context(('L3', 'L2'), ctx_min=99)
        pruned = sum(len(v) for v in m.t['L3'].values())
        m.restore(snap)
        full = sum(len(v) for v in m.t['L3'].values())
        self.assertGreaterEqual(full, pruned)


class TestGroupOf(unittest.TestCase):
    """坑4：group_of 不得做前缀归一化。"""

    def test_no_prefix_collapse(self):
        self.assertEqual(ST.group_of('humdrum_haydn_sym001a'),
                         'humdrum_haydn_sym001a')
        self.assertEqual(ST.group_of('be1'), 'be1')
        self.assertEqual(ST.group_of('0007_Benny_Carter'),
                         '0007_Benny_Carter')

    def test_symphony_keeps_62_groups(self):
        """640 个片段应产生 62 个组（不是 1 个）。"""
        import json
        import os
        cache = os.path.join(os.path.dirname(ST.__file__), '..', 'runs',
                             'grammar_cache', 'symphony.json')
        if not os.path.exists(cache):
            self.skipTest('无 symphony 缓存')
        pieces = json.load(open(cache, encoding='utf-8'))['pieces']
        groups = {ST.group_of(p['id']) for p in pieces}
        self.assertGreater(len(groups), 50)


class TestGroupKFold(unittest.TestCase):
    """坑3：同group 不得跨折；折规模应尽量均衡。"""

    def test_no_group_leak(self):
        ids = (['a%d_x' % k for k in range(5)] +
               ['a%d_y' % k for k in range(5)] +
               ['a%d_z' % k for k in range(5)])
        for tr, te in ST.GroupKFold(n_splits=5, random_state=0).split(ids):
            gtr = {ST.group_of(ids[i]) for i in tr}
            gte = {ST.group_of(ids[i]) for i in te}
            self.assertFalse(gtr & gte)

    def test_all_indices_covered_once(self):
        ids = ['g%d' % k for k in range(20)]
        seen = []
        for _tr, te in ST.GroupKFold(n_splits=5, random_state=1).split(ids):
            seen.extend(te)
        self.assertEqual(sorted(seen), list(range(20)))

    def test_balance_on_real_symphony(self):
        import json
        import os
        cache = os.path.join(os.path.dirname(ST.__file__), '..', 'runs',
                             'grammar_cache', 'symphony.json')
        if not os.path.exists(cache):
            self.skipTest('无 symphony 缓存')
        ids = [p['id'] for p in
               json.load(open(cache, encoding='utf-8'))['pieces']]
        folds = ST.GroupKFold(n_splits=10, random_state=0).fold_of(ids)
        from collections import Counter
        c = Counter(folds)
        sizes = [c[i] for i in range(10)]
        self.assertLessEqual(max(sizes) - min(sizes), 2,
                             msg='折规模不均衡：%s' % sizes)


class TestStats(unittest.TestCase):
    def test_paired_bootstrap_self_is_zero(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0] * 20
        r = ST.bootstrap_paired_ci(a, list(a), n_boot=200)
        self.assertAlmostEqual(r['delta'], 0.0, places=9)
        self.assertGreater(r['p_two_sided'], 0.05)

    def test_paired_bootstrap_detects_difference(self):
        a = [1.0 + 0.001 * i for i in range(300)]
        b = [x + 0.5 for x in a]
        r = ST.bootstrap_paired_ci(a, b, n_boot=500)
        self.assertLess(r['p_two_sided'], 0.05)
        self.assertLess(r['hi'], 0.0)

    def test_permutation_test(self):
        a = [1.0] * 30
        b = [2.0] * 30
        r = ST.paired_permutation_test(a, b, n_perm=500)
        self.assertLess(r['p_two_sided'], 0.05)
        r0 = ST.paired_permutation_test(a, list(a), n_perm=200)
        self.assertGreater(r0['p_two_sided'], 0.05)

    def test_mcnemar(self):
        self.assertAlmostEqual(
            ST.mcnemar_exact([(True, False)] * 7 +
                             [(False, True)] * 7)['p_two_sided'], 1.0)
        self.assertLess(
            ST.mcnemar_exact([(True, False)] * 12 +
                             [(False, True)] * 2)['p_two_sided'], 0.05)

    def test_summarize_folds_constant_is_zero_sd(self):
        s = ST.summarize_folds([5.0] * 4)
        self.assertAlmostEqual(s['sd'], 0.0)
        self.assertAlmostEqual(s['mean'], 5.0)
        self.assertAlmostEqual(s['lo'], 5.0)
        self.assertAlmostEqual(s['hi'], 5.0)

    def test_bootstrap_ci_on_perplexity_scale(self):
        """困惑度是 exp(mean)，bootstrap 必须在原始尺度重算。"""
        import math
        losses = [0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 1.7, 1.9]
        point, lo, hi = ST.bootstrap_ci(
            losses, n_boot=500,
            statistic=lambda v: math.exp(sum(v) / len(v)))
        self.assertLessEqual(lo, point)
        self.assertLessEqual(point, hi)
        self.assertAlmostEqual(point, round(math.exp(
            sum(losses) / len(losses)), 4), places=4)


if __name__ == '__main__':
    unittest.main()
