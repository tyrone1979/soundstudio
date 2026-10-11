# -*- coding: utf-8 -*-
"""SATB 主评测的**单一来源**（论文 Table 5 / §5.2 / §5.3 / §6）。

为什么要有这个脚本
------------------
论文 Table 5 的四个数字块此前来自**两个不同的批次**：Bach 行与草稿行取自
``runs/grammar_eval/summary.json``，无约束对照臂取自
``runs/experiments/e2_kernel_baseline.json``；而 §5.2 正文在描述对照臂时
又切回了 ``e2_kernel_baseline`` 的 grammar 臂（0.71 / 0.013），于是与
同表 Table 5 的 0.69 / 0.009 直接打架。同一篇论文里「六倍」「四十九倍」
「七十一倍」三个数各自对得上出处、互相之间对不上。

第二轮同行评审（M-2）的要求是：**两臂必须来自同一批次的一次运行**。本脚本
一次性产出 Table 5 的全部行（Bach 留出 / 语法草稿大调 / 语法草稿小调 /
无约束对照），同一个检查器、同一份 YAML、同一次进程，并额外带上此前没有的
``out_of_grammar`` 列（M-3：对照臂 OOV 从 0.00 升到 0.59，论文未报，而这
恰恰是对照臂困惑度差距的主因）。

复现要求（必须逐格吻合，否则就是回归）：

============  ====================  ====================  ================
行             违例/100              困惑度                 OOV
============  ====================  ====================  ================
Bach major      30.53                 20.75（重打分 20.45）  0.149
Bach minor      25.38                 17.31（重打分 17.15）  0.130
草稿 major      0.00                  4.53                  0.000
草稿 minor      0.00                  5.25                  0.000
无约束对照      445.19                300.67                0.590
============  ====================  ====================  ================

用法::

    venv/bin/python -m score_learning.grammar.satb_eval
产物 ``runs/experiments/e2_satb.json``。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from score_learning.grammar import script as gs  # noqa: E402
from score_learning.grammar.analyze import analyze_corpus  # noqa: E402
from score_learning.grammar.evaluate import (  # noqa: E402
    aggregate, heldout_pieces, piece_metrics,
)
from score_learning.grammar.kernel_baseline import (  # noqa: E402
    _to_piece, kernel_drafts,
)

OUT = os.path.join(ROOT, 'runs', 'experiments')
CACHE = os.path.join(ROOT, 'runs', 'grammar_cache', 'bach_chorales.json')
RULES = ('R01', 'R02', 'R03', 'R03/R04', 'R05', 'R06', 'R07', 'R08', 'R09')


def _arm(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    agg = aggregate(rows)
    cnt = Counter(r for x in rows for r in x['violation_rules'])
    beats = sum(x['beats'] for x in rows) or 1
    agg['by_rule'] = {r: cnt.get(r, 0) for r in RULES}
    agg['by_rule_per_100'] = {r: round(100 * cnt.get(r, 0) / beats, 2)
                              for r in RULES}
    return agg


def run(n_major: int = 20, n_minor: int = 16, n_control: int = 20,
        save: bool = True) -> Dict[str, Any]:
    from score_learning.grammar.compose import compose
    from score_learning.grammar.learn import is_heldout  # noqa: F401

    sc = gs.load()
    pieces = analyze_corpus(cache=CACHE, progress=False)
    held = heldout_pieces(pieces)

    out: Dict[str, Any] = {
        'question': ('Executability and load-bearingness of the acquired '
                     'constraint set (RQ2), scored by one checker.'),
        'setting': {
            'checker': 'grammar/check.py:check_satb, published evaluation '
                       'config (ranges 0-127 so R05 is off, tb_max=24)',
            'search_layer_tb_max': 19,
            'draft_seeds': {'major': 'evalmajor{i}, i=0..%d' % (n_major - 1),
                            'minor': 'evalminor{i}, i=0..%d' % (n_minor - 1),
                            'control': 'kern{i}, i=0..%d' % (n_control - 1)},
            'total_bars': 12,
            'single_run': True,
        },
        'arms': {},
    }

    for mode in ('major', 'minor'):
        rows = [piece_metrics(p, sc) for p in held if p['mode'] == mode]
        out['arms']['bach_heldout_%s' % mode] = _arm(rows)

    for mode, key, n in (('major', 1, n_major), ('minor', -1, n_minor)):
        drafts = [compose(sc, key, mode, None, f'eval{mode}{i}', total_bars=12)
                  for i in range(n)]
        rows = [piece_metrics(_to_piece(d, mode, key), sc) for d in drafts]
        att = [int(d.get('attempts') or 1) for d in drafts]
        a = _arm(rows)
        a['attempts_mean'] = round(sum(att) / len(att), 3)
        a['attempts_max'] = max(att)
        a['first_seed_rate'] = round(sum(1 for x in att if x == 1) / len(att), 3)
        a['drafts_clean'] = sum(1 for r in rows if r['violations'] == 0)
        out['arms']['grammar_drafts_%s' % mode] = a

    ctl = kernel_drafts(n=n_control, key=1, mode='major', bars=12)
    rows = [piece_metrics(_to_piece(d, 'major', 1), sc) for d in ctl]
    a = _arm(rows)
    a['drafts_clean'] = sum(1 for r in rows if r['violations'] == 0)
    a['attempts_mean'] = 1.0
    a['attempts_max'] = 1
    out['arms']['unconstrained_control'] = a
    out['control_switches'] = {
        'NEG': 'set to 0.0 (DP transition layer)',
        'HARD_CONSTRAINTS': 'set to False (candidate-construction layer)',
        'max_tries': 1,
        'accept_any': True,
        'note': ('three switches move together; the attested-bigram rule R10 '
                 'is released along with the voice-leading predicates, which '
                 'is why out_of_grammar rises from 0.00 to 0.59'),
    }

    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e2_satb.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def _fmt(o: Dict[str, Any]) -> str:
    L = ['| Arm | n | ppl | re-scored | cadence | step | leap | OOV '
         '| viol./100 | clean |', '|---|---|---|---|---|---|---|---|---|---|']
    for name in ('bach_heldout_major', 'bach_heldout_minor',
                 'grammar_drafts_major', 'grammar_drafts_minor',
                 'unconstrained_control'):
        v = o['arms'][name]
        L.append('| %s | %d | %.2f | %.2f | %.2f | %.3f | %.3f | %.3f '
                 '| %.2f | %s |'
                 % (name, v['pieces'], v['harmony_ppl'],
                    v['harmony_ppl_model_penalty'], v['cadence_match'],
                    v['sop_step'], v['sop_leap'], v['out_of_grammar'],
                    v['violations_per_100'], v.get('drafts_clean', '—')))
    return '\n'.join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='SATB 主评测（Table 5 单一来源）')
    ap.add_argument('--n-major', type=int, default=20)
    ap.add_argument('--n-minor', type=int, default=16)
    ap.add_argument('--n-control', type=int, default=20)
    a = ap.parse_args(argv)
    o = run(a.n_major, a.n_minor, a.n_control)
    print(_fmt(o))
    print('\nwrote', os.path.join(OUT, 'e2_satb.json'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
