# -*- coding: utf-8 -*-
"""E2e：SATB 草稿的多样性与分布指标，对照留出巴赫众赞歌。

Table 5 只报规则通过率、困惑度与女高音级进/跳进率；「合法」不等于「多样」。
这里在同一批草稿（与 ``satb_eval.run`` 同种子：evalmajor0..19 / evalminor0..15）
与留出巴赫上算：

* 所有指标都只取每首前 48 拍（草稿的长度）；
* ``ambitus``：每首各声部音域（最高−最低，半音），取各曲均值；
* ``sop_interval_js``：女高音相邻拍音程（|半音|，>12 归一档）分布对巴赫的 Jensen–Shannon 散度（bits）；
* ``chord_entropy``：每首和弦（根音+性质，不含转位）分布的香农熵（bits），取各曲均值；
* ``chord_types``：每首出现的不同和弦数，取均值；
* ``sop_4gram_repeat``：每首女高音音程 4-gram 的重复率 1 − 不同数/总数；
* ``cross_piece_jaccard``：同组任意两首之间和弦 4-gram 集合的 Jaccard 相似度均值（越高越雷同）。

全部在拍级网格上计算（八分经过音不计），巴赫与草稿口径一致。
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
from collections import Counter
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, 'runs', 'experiments')
CACHE = os.path.join(ROOT, 'runs', 'grammar_cache', 'bach_chorales.json')
WIN = 48


def _chord(c):
    if not c:
        return None
    r, q, _ = c.split(':')
    return f'{r}:{q}'


def _entropy(cnt: Counter) -> float:
    n = sum(cnt.values())
    return -sum(v / n * math.log2(v / n) for v in cnt.values() if v) if n else 0.0


def _js(p: Counter, q: Counter) -> float:
    keys = set(p) | set(q)
    np_, nq = sum(p.values()) or 1, sum(q.values()) or 1
    P = {k: p.get(k, 0) / np_ for k in keys}
    Q = {k: q.get(k, 0) / nq for k in keys}
    M = {k: (P[k] + Q[k]) / 2 for k in keys}
    kl = lambda A: sum(A[k] * math.log2(A[k] / M[k]) for k in keys if A[k] > 0)  # noqa: E731
    return 0.5 * kl(P) + 0.5 * kl(Q)


def piece_stats(piece: Dict[str, Any]) -> Dict[str, Any]:
    # 草稿恰为 48 拍；巴赫更长，统一截到前 WIN 拍，否则音域与 n-gram 计数随长度虚增
    b = piece['beats'][:WIN]
    inner = [(i - 1, i) for ph in piece['phrases']
             for i in range(ph['start'] + 1, min(ph['end'], WIN - 1) + 1)]
    sop_iv = [min(abs(b[i]['midi'][0] - b[h]['midi'][0]), 13) for h, i in inner]
    amb = {v: max(x['midi'][j] for x in b) - min(x['midi'][j] for x in b)
           for j, v in enumerate('SATB')}
    chords = [_chord(x['chord']) for x in b[:WIN] if x['chord']]
    signed = [b[i]['midi'][0] - b[h]['midi'][0] for h, i in inner]
    grams = [tuple(signed[k:k + 4]) for k in range(len(signed) - 3)]
    allc = [_chord(x['chord']) for x in b if x['chord']]
    cg = {tuple(allc[k:k + 4]) for k in range(len(allc) - 3)}
    return {
        'ambitus': amb, 'sop_iv': Counter(sop_iv),
        'chord_entropy': _entropy(Counter(chords)), 'chord_types': len(set(chords)),
        'sop_4gram_repeat': (1 - len(set(grams)) / len(grams)) if grams else 0.0,
        'chord_4grams': cg,
    }


def arm_stats(pieces: List[Dict[str, Any]], ref_iv: Counter = None) -> Dict[str, Any]:
    st = [piece_stats(p) for p in pieces]
    n = len(st)
    iv = sum((s['sop_iv'] for s in st), Counter())
    pairs = list(itertools.combinations(range(n), 2))
    jac = [len(st[i]['chord_4grams'] & st[j]['chord_4grams']) /
           max(len(st[i]['chord_4grams'] | st[j]['chord_4grams']), 1) for i, j in pairs]
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    tot = sum(iv.values()) or 1
    return {
        'pieces': n,
        'ambitus': {v: mean([s['ambitus'][v] for s in st]) for v in 'SATB'},
        'chord_entropy': mean([s['chord_entropy'] for s in st]),
        'chord_types': mean([s['chord_types'] for s in st]),
        'sop_4gram_repeat': mean([s['sop_4gram_repeat'] for s in st]),
        'cross_piece_jaccard': mean(jac),
        'sop_interval_dist': {str(k): round(iv[k] / tot, 4) for k in sorted(iv)},
        'sop_interval_js': round(_js(iv, ref_iv), 4) if ref_iv is not None else 0.0,
        '_iv': iv,
    }


def run(n_major: int = 20, n_minor: int = 16, save: bool = True) -> Dict[str, Any]:
    from score_learning.grammar import script as gs
    from score_learning.grammar.analyze import analyze_corpus
    from score_learning.grammar.compose import compose
    from score_learning.grammar.evaluate import heldout_pieces
    from score_learning.grammar.kernel_baseline import _to_piece
    sc = gs.load()
    held = heldout_pieces(analyze_corpus(cache=CACHE, progress=False))
    out: Dict[str, Any] = {
        'question': 'Diversity and distributional fit of SATB drafts vs held-out Bach '
                    '(same draft seeds as e2_satb).',
        'setting': {'grid': 'beat level', 'window_for_chord_counts': WIN,
                    'draft_seeds': 'eval{mode}{i}', 'total_bars': 12},
        'arms': {},
    }
    for mode, key, n in (('major', 1, n_major), ('minor', -1, n_minor)):
        bach = arm_stats([p for p in held if p['mode'] == mode])
        drafts = [_to_piece(compose(sc, key, mode, None, f'eval{mode}{i}', total_bars=12),
                            mode, key) for i in range(n)]
        dr = arm_stats(drafts, ref_iv=bach['_iv'])
        for a in (bach, dr):
            a.pop('_iv')
        out['arms'][f'bach_heldout_{mode}'] = bach
        out['arms'][f'grammar_drafts_{mode}'] = dr
    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e2e_diversity.json'), 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def fmt(o: Dict[str, Any]) -> str:
    L = ['| Arm | n | ambitus S/A/T/B | chord H | chord types | sop 4-gram rep. '
         '| cross-piece Jaccard | sop interval JS |', '|---|---|---|---|---|---|---|---|']
    for name, v in o['arms'].items():
        a = v['ambitus']
        L.append('| %s | %d | %.1f/%.1f/%.1f/%.1f | %.2f | %.1f | %.3f | %.3f | %.3f |'
                 % (name, v['pieces'], a['S'], a['A'], a['T'], a['B'], v['chord_entropy'],
                    v['chord_types'], v['sop_4gram_repeat'], v['cross_piece_jaccard'],
                    v['sop_interval_js']))
    return '\n'.join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='E2e SATB 多样性')
    ap.parse_args(argv)
    o = run()
    print(fmt(o))
    print('产物 → runs/experiments/e2e_diversity.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
