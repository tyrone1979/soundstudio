# -*- coding: utf-8 -*-
"""小节关系提取的回归测试。

关键回归点是`sections.start/end` 的索引语义 —— 误用eventid 会静默返回
空结果（见模块 docstring），因此用真实已知值钉住。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from score_learning.relation.extract import (  # noqa: E402
    DEFAULT_DB, RelationExtractor, form_family, form_index, idea_theme)


class TestLabelSemantics(unittest.TestCase):
    """标签解析"""

    def test_form_family(self):
        for lab, fam in [('A1', 'A'), ('A2', 'A'), ('B1', 'B'), ('C1', 'C'),
                         ('I1', 'I'), ("A'2", 'A'), ('D1', 'D')]:
            self.assertEqual(form_family(lab), fam, lab)

    def test_form_index(self):
        self.assertEqual(form_index('A1'), 1)
        self.assertEqual(form_index('A4'), 4)
        self.assertIsNone(form_index('A'))

    def test_idea_theme(self):
        # 前缀修饰不应影响归类
        self.assertEqual(idea_theme('lick'), '即兴乐句')
        self.assertEqual(idea_theme('#lick'), '即兴乐句·装饰')
        self.assertEqual(idea_theme('~line_w'), 'Bebop 线·变形')
        # 带编号的 lick（#7lick / #10lick）须剥掉数字后归类
        self.assertEqual(idea_theme('#7lick'), '即兴乐句·装饰')
        self.assertEqual(idea_theme('#10lick'), '即兴乐句·装饰')
        # theme / quote 须保留编号
        self.assertEqual(idea_theme('theme:t1'), '主题引用(t1)')
        self.assertEqual(idea_theme('*theme:t3-4'), '主题引用(t3-4)')


@unittest.skipUnless(os.path.exists(DEFAULT_DB), '标注库不存在')
class TestExtractor(unittest.TestCase):
    """真实数据回归"""

    @classmethod
    def setUpClass(cls):
        cls.ex = RelationExtractor(DEFAULT_DB)

    def test_index_semantics_is_row_offset(self):
        """melid=1：FORM 标注 0-529，melody 恰好 530 行 —— 必须是行偏移而非 eventid。"""
        rows = self.ex._rows(1)
        self.assertEqual(len(rows), 530)
        starts = [s for s, _e, _v in self.ex._sections(1, 'FORM')]
        self.assertEqual(min(starts), 0)
        self.assertLess(max(starts), len(rows),
                        '标注索引超出 melody 行数 → 说明不是行偏移语义')

    def test_solo7_is_aaba(self):
        """melid=7 = Benny Carter《I Got It Bad》，标注应为 A1/A2/A3/B1/B2/A4。"""
        s = self.ex.extract_solo(7)
        self.assertIsNotNone(s)
        self.assertEqual(s['title'], 'I Got It Bad')
        labels = [g['label'] for g in s['form_segs']]
        for want in ('A1', 'A2', 'A3', 'B1'):
            self.assertIn(want, labels, labels)

    def test_bar_ids_unique(self):
        s = self.ex.extract_solo(7)
        ids = [b['bar_id'] for b in s['bars']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(b.startswith('0007-') for b in ids))

    def test_hierarchy_phrase_has_single_parent(self):
        """同一乐句不得被挂到两个 FORM 段下（曾出现的重叠 bug 回归点）。"""
        for mid in (1, 3, 7, 20):
            s = self.ex.extract_solo(mid)
            self.assertIsNotNone(s, f'melid={mid}')
            ph_ids = [p['node_id'] for seg in self.ex.hierarchy(s)
                      for p in seg['children']]
            self.assertEqual(len(ph_ids), len(set(ph_ids)), f'melid={mid} 乐句重复挂载')

    def test_relations_types_and_evidence(self):
        s = self.ex.extract_solo(7)
        rels = self.ex.relations(s)
        kinds = {r['rel'] for r in rels}
        self.assertTrue({'introduce', 'contrast', 'quote', 'cause'} <= kinds, kinds)
        for r in rels:
            self.assertTrue(r['evidence'], r)          # 每条边必须可追溯
            self.assertIn(r['confidence'], ('high', 'medium', 'low', 'none'))
            if r['rel'] == 'unclear':
                self.assertIsNone(r['confidence'] if False else r['tgt_bar_range'])

    def test_quote_points_to_first_occurrence(self):
        """引用边必须指向该字母族的首次出现段，而非上一段。

        melid=7 的 FORM 序列是 A1 A2 B1 A3 B2 A4 —— 既有 A→A3 也有 B→B2，
        因此不能硬编码只查A1。
        """
        s = self.ex.extract_solo(7)
        rels = self.ex.relations(s)
        quotes = [r for r in rels if r['rel'] == 'quote']
        self.assertTrue(quotes)
        first_idx = {}
        for i, g in enumerate(s['form_segs']):
            first_idx.setdefault(g['family'], i)
        for q in quotes:
            fam = form_family(q['tgt_label'])
            expect = s['form_segs'][first_idx[fam]]['label']
            self.assertEqual(q['src_label'], expect, q)


if __name__ == '__main__':
    unittest.main()
