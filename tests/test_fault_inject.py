# -*- coding: utf-8 -*-
"""注入故障实验（Experiment 6(iii)）的回归测试。

锁住的性质：
1. 合法草稿在注入前必须**零违例**（否则测的是别的东西）
2. 注入后必须报出**目标规则**（R01/R03/R06）
3. 注入不能误伤合法草稿（误报率 0）
4. R01 的判定要正确处理「斜向进行」（一声部不动不算平行）
"""
from __future__ import annotations

import unittest

from score_learning.grammar import fault_inject as FI
from score_learning.grammar.check import check_satb, parallel_perfect

N_DRAFTS = 4          # 测试用少量草稿，完整实验用 20


def _ranges():
    from score_learning.grammar import script as gs
    sc = gs.load()
    return {k: dict(v) for k, v in sc['voicing']['ranges'].items()}


class TestInjectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ranges = _ranges()
        cls.drafts = FI.gen_drafts(N_DRAFTS)

    def test_drafts_exist(self):
        self.assertEqual(len(self.drafts), N_DRAFTS)
        for d in self.drafts:
            self.assertGreater(len(d['beats']), 8, '草稿太短')

    def test_legal_drafts_are_clean(self):
        """性质 1：合法草稿注入前必须零违例。"""
        for i, d in enumerate(self.drafts):
            info = FI._build_info(d['beats'], d['tpc'])
            v = check_satb(FI._to_grid(info), info, self.ranges,
                           tb_max=FI.TB_MAX)
            self.assertEqual(v, [], f'草稿 {i} 在注入前已有违例：{v}')

    def test_R01_detected(self):
        """性质 2a：平行五度注入必须被 R01 检出。"""
        hits = 0
        for d in self.drafts:
            r = FI.run_draft(d, 'R01', self.ranges)
            if r['placed']:
                self.assertTrue(r['detected'],
                                f'R01 注入未被检出：{r}')
                self.assertIn('R01', r['all_rules'])
                hits += 1
        self.assertGreater(hits, 0, 'R01 一次都没注入成功')

    def test_R03_detected(self):
        """性质 2b：声部交叉注入必须被检出。"""
        hits = 0
        for d in self.drafts:
            r = FI.run_draft(d, 'R03', self.ranges)
            if r['placed']:
                self.assertTrue(r['detected'], f'R03 注入未被检出：{r}')
                hits += 1
        self.assertEqual(hits, N_DRAFTS,
                         '合法草稿必然存在可注入的交叉点')

    def test_R06_when_placeable(self):
        """性质 2c：导音未解决注入必须被 R06 检出。"""
        hits = placed = 0
        for d in self.drafts:
            r = FI.run_draft(d, 'R06', self.ranges)
            if r['placed']:
                placed += 1
                self.assertTrue(r['detected'], f'R06 注入未被检出：{r}')
                hits += 1
        self.assertLessEqual(hits, placed)

    def test_no_false_positive_on_clean_drafts(self):
        """性质 3：未注入的合法草稿不得被误报。"""
        for i, d in enumerate(self.drafts):
            for rule in ('R01', 'R03', 'R06'):
                r = FI.run_draft(d, rule, self.ranges)
                self.assertTrue(r['base_clean'],
                                f'草稿 {i} 基线不干净：{r["base_rules"]}')

    def test_R01_ignores_oblique_motion(self):
        """性质 4：R01 不应把「一声部不动」判为平行。"""
        # 女高音 72→74 移动，次中音 65 保持 → 斜向，不是平行
        self.assertFalse(parallel_perfect(72, 65, 74, 65))
        # 两声部都动且保持纯五 → 是平行
        self.assertTrue(parallel_perfect(72, 65, 74, 67))
        # 纯八同理
        self.assertTrue(parallel_perfect(72, 60, 79, 67))

    def test_R01_injection_stays_in_range(self):
        """R01 注入**必须**保持在音域内。

        R01 把次中音移到女高音的纯五/八度，跨度仍合法，因此不应附带触发
        R05（音域）或 R03（交叉）—— 否则信号会被其他规则淹没。
        R03 注入则相反：它**故意**破坏声部序，压低中音后可能连带出音域
        （实测附带 R05），这属于真实的规则耦合，不在此断言范围内。
        """
        for d in self.drafts:
            info = FI._build_info(d['beats'], d['tpc'])
            if FI.inject_R01(info, d['tpc']) is None:
                continue
            for x in info:
                for vi, nm in enumerate('SATB'):
                    r = self.ranges[nm]
                    self.assertTrue(r['low'] <= x['midi'][vi] <= r['high'],
                                    f'R01 注入后 {nm} 出音域：{x["midi"][vi]}')
            # 声部序保持 → 不应因交叉而附带 R03
            for x in info:
                v = x['midi']
                self.assertTrue(v[0] >= v[1] >= v[2] >= v[3],
                                f'R01 注入破坏了声部序：{v}')


class TestRunSummary(unittest.TestCase):
    def test_summary_shape(self):
        r = FI.run(n_drafts=3, rules=('R01', 'R03'), save=False)
        s = r['summary']
        self.assertIn('per_rule', s)
        self.assertEqual(set(s['per_rule']), {'R01', 'R03'})
        for v in s['per_rule'].values():
            for k in ('n_placed', 'n_detected', 'recall', 'isolated'):
                self.assertIn(k, v)
        self.assertEqual(s['baseline_clean_rate'], 1.0)
        self.assertGreaterEqual(s['overall']['recall'], 0.9)


class TestRemainingRules(unittest.TestCase):
    """R02/R04/R05/R07/R08/R09 注入：放置成功即必须报出目标规则。"""

    @classmethod
    def setUpClass(cls):
        cls.ranges = _ranges()
        cls.drafts = FI.gen_drafts(N_DRAFTS)

    def test_every_rule_has_an_injector(self):
        self.assertEqual(set(FI.ALL_RULES),
                         {f'R0{i}' for i in range(1, 10)})

    def test_detected_when_placed(self):
        for rule in ('R02', 'R04', 'R05', 'R07', 'R08', 'R09'):
            placed = 0
            for d in self.drafts:
                r = FI.run_draft(d, rule, self.ranges)
                if r['placed']:
                    placed += 1
                    self.assertTrue(r['detected'], f'{rule} 注入未被检出：{r}')
            self.assertGreater(placed, 0, f'{rule} 一次都没注入成功')

    def test_R04_reported_by_spacing_predicate(self):
        self.assertEqual(FI.ACCEPT['R04'], {'R03/R04'})

    def test_mutation_updates_rel(self):
        d = self.drafts[0]
        info = FI._build_info(d['beats'], d['tpc'])
        site = FI.inject_R09(info, d['tpc'])
        if site is None:
            self.skipTest('no R09 site')
        x = info[site['index']]
        self.assertEqual(x['rel'], [(m - d['tpc']) % 12 for m in x['midi']])


if __name__ == '__main__':
    unittest.main()
