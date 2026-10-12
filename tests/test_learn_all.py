# -*- coding: utf-8 -*-
"""`/learn-all` 统一学习入口的回归测试。

覆盖三个真实踩过的坑：
1. jazz 的 6 个体裁全在同一个 pack 的不同 sub —— 按 pack 聚合写盘，否则互相覆盖
2. `form_plan.policies` 的段数键在 yaml 里是 str，代码里查 int
3. 干跑时 `sc` 未定义 —— 评估字段不能引用（曾经的 UnboundLocalError 隐患）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from score_learning.relation.learn_all import (  # noqa: E402
    LEADSHOT_STYLES, Stage, learn_all, render_report)


class TestStageModel(unittest.TestCase):
    """Stage 数据模型"""

    def test_stage_defaults(self):
        s = Stage('x', 'desc')
        self.assertIsNone(s.ok)
        self.assertEqual(s.detail, {})
        d = s.as_dict()
        self.assertEqual(d['stage'], 'x')
        self.assertIsNone(d['ok'])
        self.assertIn('secs', d)


class TestDryRun(unittest.TestCase):
    """干跑：不写盘且不炸"""

    def test_relation_only_dry(self):
        stages = learn_all(skip=['chorale', 'leadsheet'], dry=True)
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0].name, 'relation')

    def test_all_stages_dry(self):
        stages = learn_all(dry=True)
        names = [s.name for s in stages]
        self.assertIn('chorale', names)
        self.assertEqual(len([n for n in names if n.startswith('leadsheet:')]),
                         len(LEADSHOT_STYLES))
        self.assertIn('relation', names)

    def test_style_filter(self):
        stages = learn_all(skip=['chorale', 'relation'], styles=['jazz'], dry=True)
        self.assertEqual([s.name for s in stages], ['leadsheet:jazz'])


class TestReport(unittest.TestCase):
    """报告渲染"""

    def test_report_handles_failure(self):
        bad = Stage('broken', '会失败的阶段')
        bad.ok = False
        bad.error = 'RuntimeError: 炸了'
        bad.detail['trace'] = 'Traceback...'
        rep = render_report([bad])
        self.assertIn('broken', rep)
        self.assertIn('炸了', rep)
        self.assertIn('❌', rep)

    def test_report_handles_empty(self):
        self.assertIn('统一学习报告', render_report([]))

    def test_report_marks_dry_run(self):
        rep = render_report([], dry=True)
        self.assertIn('干跑', rep)


class TestLearnedRulesExist(unittest.TestCase):
    """产物落盘检查（只读）"""

    def test_form_plan_injected_in_jazz(self):
        import yaml
        p = 'skills/composition/genre/packs/jazz/swing-standard/grammar.yaml'
        if not os.path.exists(p):
            self.skipTest('jazz 规则脚本不存在')
        d = yaml.safe_load(open(p, encoding='utf-8'))
        subs = d.get('subs') or {}
        for name in ('swing', 'hardbop', 'cool'):
            if name not in subs:
                continue
            fp = (subs[name].get('form') or {}).get('form_plan')
            self.assertIsNotNone(fp, f'{name} 缺 form_plan')
            self.assertIn('policies', fp)
            # 段数键在 policies 之下（不是顶层）
            pol = fp['policies']
            self.assertTrue(pol, f'{name} 的 policies 为空')
            for k, v in pol.items():
                self.assertIsInstance(k, str, 'yaml 里的段数键应是 str')
                self.assertIsInstance(v, list)
                for it in v:
                    self.assertIn('form', it)
                    self.assertIn('p', it)

    def test_no_self_reference_in_segments(self):
        import yaml
        p = 'skills/composition/genre/packs/jazz/swing-standard/grammar.yaml'
        if not os.path.exists(p):
            self.skipTest('jazz 规则脚本不存在')
        d = yaml.safe_load(open(p, encoding='utf-8'))
        for name, sub in (d.get('subs') or {}).items():
            fp = (sub.get('form') or {}).get('form_plan')
            if not fp:
                continue
            for n, items in (fp.get('policies') or {}).items():
                for it in items:
                    for s in it.get('segments') or []:
                        self.assertNotEqual(s.get('ref'), s['label'],
                                           f'{name}/{n} 自引用 {s}')


if __name__ == '__main__':
    unittest.main()
