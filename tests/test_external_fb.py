# -*- coding: utf-8 -*-
"""外部系统对照（music21 数字低音实现器）的回归测试：数字低音转写与实现。"""
from __future__ import annotations

import unittest

from score_learning.grammar import external_fb as E


class TestFigures(unittest.TestCase):
    def test_root_position_triad(self):
        bp, fig, _ = E.figure_for('0:M:0', 'G', 'major', 55)
        self.assertEqual(bp.nameWithOctave, 'G3')
        self.assertEqual(fig, '5,3')

    def test_first_inversion_dominant(self):
        bp, fig, _ = E.figure_for('7:M:1', 'G', 'major', 54)
        self.assertEqual(bp.nameWithOctave, 'F#3')
        self.assertEqual(fig, '6,3')

    def test_minor_dominant_raises_third(self):
        bp, fig, _ = E.figure_for('7:M:0', 'D', 'minor', 45)
        self.assertEqual(bp.nameWithOctave, 'A2')
        self.assertEqual(fig, '5,#3')

    def test_seventh_inversions(self):
        self.assertEqual(E.figure_for('7:7:1', 'C', 'major', 59)[1], '6,5,3')
        self.assertEqual(E.figure_for('7:7:3', 'C', 'major', 65)[1], '6,4,2')

    def test_neapolitan_spelled_flat(self):
        bp, fig, _ = E.figure_for('1:M:1', 'C', 'minor', 53)
        self.assertEqual(bp.name, 'F')
        self.assertEqual(fig, 'b6,3')


class TestRealize(unittest.TestCase):
    def test_short_progression_realizes(self):
        beats = [{'t': float(i), 'chord': c, 'midi': [0, 0, 0, b]}
                 for i, (c, b) in enumerate((('0:M:0', 48), ('5:M:0', 53),
                                             ('7:M:0', 55), ('0:M:0', 48)))]
        piece = {'tonic': 'C', 'mode': 'major', 'meter': '4/4',
                 'beats': beats, 'phrases': [{'start': 0, 'end': 3}]}
        v, chunks = E.realize_piece(piece, 0)
        self.assertEqual(chunks, [(0, 4)])
        self.assertEqual(sorted(v), [0, 1, 2, 3])
        for i, s in v.items():
            self.assertEqual(s[3], beats[i]['midi'][3])
            self.assertTrue(s[0] >= s[1] >= s[2] >= s[3])


if __name__ == '__main__':
    unittest.main()
