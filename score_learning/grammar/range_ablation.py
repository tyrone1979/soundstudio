# -*- coding: utf-8 -*-
"""R05（习得音域）开关实验 + 巴赫违例的逐规则分解。

为什么要有这个脚本
------------------
论文 Fig.~\ref{fig:arch} 的图注写 *"the checker reads the same hard predicates
with thresholds stored in the rule entries"*。这句话在众赞歌（SATB）分支上
**只有一族规则成立**：``grammar/check.py`` 全文不读 YAML，阈值是 Python 常量
与函数默认参数；唯一从 YAML 流进该检查器的数据是 ``voicing['ranges']``
（``learn.py`` 的 ``_learn_ranges`` 习得），即 R05。

更麻烦的是评测器把这条活阈值也关了：``evaluate.piece_metrics`` 默认把
``ranges`` 换成 0--127，于是**论文所有违例数字里 R05 恒为 0**。本脚本把这条
链路做成可复现的数字，而不是一句断言：

=========  ==========================  =====================
配置       含义                        目的
=========  ==========================  =====================
A          0--127, ``tb_max=24``       论文已发表口径（必须复现 30.53/25.38）
B          习得音域, ``tb_max=24``      只改 R05，其余不动
C          习得音域上下各收窄 1 半音    **改存储阈值即改判决**的演示
D          习得音域, ``tb_max=19``      与搜索层完全对齐（搜索用 19）
=========  ==========================  =====================

B→C 就是贡献 1 需要的演示：把 ``script['voicing']['ranges']`` 上下各收窄一个
半音，留出巴赫的违例率与 R05 计数都显著上升 —— 不需要重构 ``check.py``，
也不依赖 lead-sheet 分支。

同时产出巴赫违例的**逐规则分解**（此前只有聚合值 30.53/25.38，读者无法判断
严格性来自哪一条规则）。

用法::

    venv/bin/python -m score_learning.grammar.range_ablation
产物 ``runs/experiments/e2b_range_ablation.json``。
"""
from __future__ import annotations

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

OUT = os.path.join(ROOT, 'runs', 'experiments')
CACHE = os.path.join(ROOT, 'runs', 'grammar_cache', 'bach_chorales.json')

RULES = ('R01', 'R02', 'R03', 'R03/R04', 'R05', 'R06', 'R07', 'R08', 'R09')


def _wide() -> Dict[str, Dict[str, int]]:
    """全 MIDI 跨度：等价于把 R05 关掉（论文已发表口径）。"""
    return {k: {'low': 0, 'high': 127} for k in 'SATB'}


def _shrunk(ranges: Dict[str, Any], d: int) -> Dict[str, Dict[str, int]]:
    """把习得音域上下各收窄（``d>0``）/放宽（``d<0``）``d`` 个半音。"""
    return {k: {'low': int(v['low']) + d, 'high': int(v['high']) - d}
            for k, v in ranges.items()}


def _as_int(ranges: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    return {k: {'low': int(v['low']), 'high': int(v['high'])}
            for k, v in ranges.items()}


def configs(script: Dict[str, Any]) -> List[Dict[str, Any]]:
    learned = _as_int(script['voicing']['ranges'])
    return [
        {'name': 'A', 'desc': 'published: 0-127 ranges (R05 disabled), tb_max=24',
         'ranges': _wide(), 'tb_max': 24},
        {'name': 'B', 'desc': 'learned voicing.ranges, tb_max=24',
         'ranges': learned, 'tb_max': 24},
        {'name': 'C', 'desc': 'learned ranges shrunk by 1 semitone each side, tb_max=24',
         'ranges': _shrunk(learned, 1), 'tb_max': 24},
        {'name': 'D', 'desc': 'learned ranges, tb_max=19 (the search-layer value)',
         'ranges': learned, 'tb_max': 19},
    ]


def run(save: bool = True) -> Dict[str, Any]:
    script = gs.load()
    pieces = analyze_corpus(cache=CACHE, progress=False)
    held = heldout_pieces(pieces)
    by_mode = {m: [p for p in held if p['mode'] == m] for m in ('major', 'minor')}

    out: Dict[str, Any] = {
        'question': ('Does a threshold stored in the grammar change what the '
                     'checker reports, on the chorale branch?'),
        'setting': {
            'n_pieces': {m: len(v) for m, v in by_mode.items()},
            'rules_scored': list(RULES),
            'split': 'MD5 held-out piece id, same as all chorale experiments',
            'learned_ranges': _as_int(script['voicing']['ranges']),
            'search_layer_tb_max': 19,
        },
        'configs': {},
    }

    for cfg in configs(script):
        rng = cfg['ranges']
        row: Dict[str, Any] = {
            'desc': cfg['desc'], 'tb_max': cfg['tb_max'],
            'ranges': {k: [v['low'], v['high']] for k, v in rng.items()},
            'per_mode': {},
        }
        for mode, ps in by_mode.items():
            rows = [piece_metrics(p, script, ranges=rng, tb_max=cfg['tb_max'])
                    for p in ps]
            agg = aggregate(rows)
            cnt = Counter(r for x in rows for r in x['violation_rules'])
            beats = sum(x['beats'] for x in rows)
            row['per_mode'][mode] = {
                'pieces': len(rows),
                'beats': beats,
                'violations_per_100': agg['violations_per_100'],
                'violations': sum(x['violations'] for x in rows),
                'by_rule': {r: cnt.get(r, 0) for r in RULES},
                'by_rule_per_100': {
                    r: round(100 * cnt.get(r, 0) / beats, 2) for r in RULES},
                'by_rule_share': {
                    r: round(cnt.get(r, 0) / max(sum(cnt.values()), 1), 4)
                    for r in RULES},
                'harmony_ppl': agg['harmony_ppl'],
            }
        out['configs'][cfg['name']] = row

    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e2b_range_ablation.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def _fmt(out: Dict[str, Any]) -> str:
    L = ['| Config | tb_max | major viol./100 | minor viol./100 '
         '| R05 maj/min |', '|---|---|---|---|---|']
    for name, row in out['configs'].items():
        mj = row['per_mode']['major']
        mn = row['per_mode']['minor']
        L.append('| **%s** %s | %d | %.2f | %.2f | %d / %d |'
                 % (name, row['desc'].split(':')[0], row['tb_max'],
                    mj['violations_per_100'], mn['violations_per_100'],
                    mj['by_rule']['R05'], mn['by_rule']['R05']))
    L.append('')
    L.append('Per-rule decomposition (published config A):')
    L.append('| Rule | major n | major /100 | share | minor n | minor /100 | share |')
    L.append('|---|---|---|---|---|---|---|')
    a = out['configs']['A']['per_mode']
    for r in RULES:
        mj, mn = a['major'], a['minor']
        L.append('| %s | %d | %.2f | %.1f%% | %d | %.2f | %.1f%% |'
                 % (r, mj['by_rule'][r], mj['by_rule_per_100'][r],
                    100 * mj['by_rule_share'][r],
                    mn['by_rule'][r], mn['by_rule_per_100'][r],
                    100 * mn['by_rule_share'][r]))
    return '\n'.join(L)


if __name__ == '__main__':
    o = run()
    print(_fmt(o))
    print('\nwrote', os.path.join(OUT, 'e2b_range_ablation.json'))
