from collections import Counter

from score_learning.grammar.chord_agreement import compare_piece
from score_learning.grammar.diversity import _js, piece_stats


def _piece(chords, sop):
    beats = [{'t': float(i), 'pos': float(i % 4), 'downbeat': i % 4 == 0,
              'midi': [s, s - 5, s - 12, s - 24], 'chord': c}
             for i, (c, s) in enumerate(zip(chords, sop))]
    return {'beats': beats, 'phrases': [{'start': 0, 'end': len(beats) - 1}],
            'tonic_pc': 0, 'mode': 'major'}


def test_js_bounds():
    p = Counter({0: 5, 2: 5})
    assert _js(p, p) == 0
    assert abs(_js(Counter({0: 1}), Counter({2: 1})) - 1.0) < 1e-9


def test_piece_stats_counts():
    st = piece_stats(_piece(['0:M:0', '7:M:0', '0:M:0', '5:M:0'], [72, 74, 76, 77]))
    assert st['chord_types'] == 3
    assert st['ambitus']['S'] == 5
    assert st['sop_iv'] == Counter({2: 2, 1: 1})


class _RN:
    """最小替身：只提供 compare_piece 用到的接口。"""

    def __init__(self, root, quality, inv=0, seventh=None):
        self._r, self.quality, self._inv, self.seventh = root, quality, inv, seventh

    def root(self):
        class P:
            pass
        p = P()
        p.pitchClass = self._r
        return p

    def inversion(self):
        return self._inv


def test_compare_piece_alignment_and_transpose():
    piece = _piece(['0:M:0', '7:M:0', '9:m:0', '0:M:1'], [72, 74, 72, 76])
    ev = [(1, 1.0, _RN(2, 'major')), (1, 2.0, _RN(9, 'major')),
          (1, 3.0, _RN(11, 'major')), (1, 4.0, _RN(2, 'major', 1))]
    rows = compare_piece(piece, ev, shift=0, transpose=2)
    cats = [r['cat'] for r in rows]
    assert cats == ['match', 'match', 'quality', 'match']
