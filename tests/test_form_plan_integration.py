# -*- coding: utf-8 -*-
"""form_plan（人工段落关系）接入 plan_form 的回归测试。

覆盖两个真实踩过的坑：
1. `plan.get(str(n))` 取段数 → 但段数键在 `plan['policies']` 之下（曾静默返回 None）
2. 人工标注的段长（8/10/13…）不等于请求小节数 → 必须缩放，否则 plan['bars'] 对不上
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from score_learning.leadsheet import script as lscript  # noqa: E402
from score_learning.leadsheet.compose import (  # noqa: E402
    _relation_form, plan_form, SubGrammar)


def _has_form_plan():
    try:
        sc = lscript.load('jazz')
    except Exception:
        return False
    return 'form_plan' in (sc['subs'].get('swing', {}).get('form') or {})


@unittest.skipUnless(_has_form_plan(), 'grammar.yaml 尚未注入 form_plan')
class TestRelationForm(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sc = lscript.load('jazz')
        cls.g = SubGrammar(cls.sc, 'swing', 'major')

    def test_relation_form_found(self):
        """段数键必须在 policies 之下 —— 曾经的静默 None。"""
        r = _relation_form(self.g, 4, random.Random(0), 32)
        self.assertIsNotNone(r, 'form_plan 存在但 _relation_form 返回 None')
        self.assertEqual(r['source'], 'form_plan')

    def test_segments_carry_role_and_ref(self):
        r = _relation_form(self.g, 5, random.Random(1), 40)
        self.assertIsNotNone(r)
        segs = r['segments']
        self.assertTrue(segs)
        for s in segs:
            self.assertIn('role', s)
            self.assertIn('bars', s)
            self.assertIn('label', s)
        # 至少应有一个非引入角色（递进/引用/尾声），否则关系信息没生效
        self.assertTrue(any(s['role'] != 'introduce' for s in segs),
                        [s['role'] for s in segs])

    def test_no_self_reference(self):
        r = _relation_form(self.g, 5, random.Random(2), 40)
        for s in r['segments']:
            self.assertNotEqual(s.get('ref'), s['label'],
                                f"自引用：{s['label']}←{s.get('ref')}")

    def test_segment_bars_sum_to_total(self):
        """段长之和必须精确等于请求小节数（缩放逻辑回归点）。"""
        for bars in (32, 40, 48, 64, 96):
            r = _relation_form(self.g, max(1, min(12, round(bars / 8))),
                               random.Random(bars), bars)
            if r is None or not r['segments']:
                continue
            self.assertEqual(sum(s['bars'] for s in r['segments']), bars,
                             f'bars={bars} 段长合计不符')
            self.assertEqual(r['bars'], bars, f'bars={bars} plan["bars"] 不符')

    def test_plan_form_uses_form_plan(self):
        p = plan_form(self.g, random.Random(3), 32, 'downbeat')
        self.assertEqual(p.get('source'), 'form_plan')
        self.assertIn('人工段落标注', p['form_rule'])

    def test_fallback_when_no_policy(self):
        """段数没有对应 policy 时回退到 templates（source 不为 form_plan）。"""
        p = plan_form(self.g, random.Random(4), 8, 'downbeat')
        self.assertNotEqual(p.get('source'), 'form_plan')

    def test_end_to_end_generation(self):
        """端到端：走 compose()，必须零违例且小节数正确。"""
        from score_learning.leadsheet.compose import compose
        res = compose(self.sc, 'swing', 'major', 0, 32,
                      seed='fp-test', temperature=1.0, layout='downbeat')
        self.assertFalse(res['violations'], res['violations'][:2])
        self.assertEqual(res['plan'].get('source'), 'form_plan')
        self.assertEqual(res['spec']['bars'], 32)


if __name__ == '__main__':
    unittest.main()
