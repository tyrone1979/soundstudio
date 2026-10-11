# -*- coding: utf-8 -*-
"""E1h：按乐曲重采样的位置效应。"""
from __future__ import annotations

import unittest

from score_learning import experiments as X


class TestClusterBootstrap(unittest.TestCase):
    def test_ratio_estimate_and_ci(self):
        r = X._cluster_bootstrap([-2.0, -4.0, -3.0], [10, 20, 15], n_boot=500)
        self.assertAlmostEqual(r['delta'], -9.0 / 45.0)
        self.assertLessEqual(r['lo'], r['delta'])
        self.assertGreaterEqual(r['hi'], r['delta'])
        self.assertEqual(r['n_pieces'], 3)

    def test_null_effect_not_significant(self):
        r = X._cluster_bootstrap([1.0, -1.0, 1.0, -1.0], [5, 5, 5, 5],
                                 n_boot=2000)
        self.assertGreater(r['p_two_sided'], 0.5)


class TestRunE1h(unittest.TestCase):
    def test_position_helps_at_piece_level(self):
        out = X.run_e1h(n_splits=3, n_boot=300, save=False)
        rows = out['summary']
        self.assertEqual({(r['mode'], r['smoother']) for r in rows},
                         {(m, s) for m in ('major', 'minor') for s in ('wb', 'jm')})
        for r in rows:
            self.assertLess(r['delta_nats'], 0)
            self.assertLess(r['piece_ci'][1], 0)
            self.assertLess(r['ppl_full'], r['ppl_none'])


if __name__ == '__main__':
    unittest.main()
