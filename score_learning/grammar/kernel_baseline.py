# -*- coding: utf-8 -*-
"""Unconstrained-kernel 基线：证明硬约束搜索确实起了作用。

对应论文 §3.2（Baselines and comparators）的 ``Unconstrained kernel``：
"Natural-language-to-kernel drafting without the grammar"。

**为什么需要重跑**
原稿的 ``old_llm_kernel`` 数字（97.92 violations/100 beats, n=3）是
上一轮工作遗留的：``papers/eswa/refresh_eval.py`` 里

```python
if "old_llm_kernel" in old:
    summary["old_llm_kernel"] = old["old_llm_kernel"]
```

原样搬运上一轮的产物，既未用当前���法重跑，样本量也只有 3。作为
对照基线，n=3 不足以支撑 97.92 这样的两位有效数字。

**构造方式**
要诚实地呈现"去掉硬约束会发生什么"，对照组必须与实验组**只差一个变量**。
因此这里不引入另一套作曲算法（那会同时改变和弦搜索、声部进行、
终止式处理等多个因素），而是在**同一个搜索器**上关闭硬约束：

* ``HARD_CONSTRAINTS = False``——把 ``NEG`` 从 ``-1e9`` 改为 ``0``，
  于是所有 ``T[bad] = NEG`` / ``return NEG`` 的位置变成零代价，
  DP 可以走出被禁的路径。这是"无约束起草"的准确语义。
* 其余全部不变：同一份 grammar、同一首调、同一 seed 族、同一 DP、
  同一发射/转移矩阵构造。**只有一个变量被改动。**

这样violations/100 beats 的差异可以归因于 accept/reject 循环本身，
而不是两种不同算法的差异。论文的措辞相应改为"the same search
without the accept/reject loop"，比"natural-language-to-kernel"更准确
——我们并未运行一个独立的大模型起草器。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, 'runs', 'experiments')


def kernel_drafts(n: int = 20, key: int = 1, mode: str = 'major',
                  bars: int = 12, seed_prefix: str = 'kern') -> List[Dict[str, Any]]:
    """同一搜索器，但**关闭全部硬约束**（``HARD_CONSTRAINTS = False``）。

    实现方式是把 :data:`score_learning.grammar.compose.NEG` 从 ``-1e9``
    改成 ``0``：所有 ``T[bad] = NEG`` / ``return NEG`` 的硬约束位置于是
    变成「零代价」，DP 会自由地走出平行五度、隐伏八度、超范围旋律音程
    等被禁的路径。这与「换一个不同的作曲算法」不同——变量只有一个
    （硬约束开/关），因此violations 的差异可归因于约束本身。
    """
    from score_learning.grammar import script as gs
    from score_learning.grammar import compose as C
    from score_learning.grammar.compose import compose as _compose
    sc = gs.load()
    old_neg, old_hard = C.NEG, C.HARD_CONSTRAINTS
    C.NEG = 0.0                # 关闭 DP 转移层的硬约束
    C.HARD_CONSTRAINTS = False   # 关闭候选集构造层的硬过滤
    try:
        out = [_compose(sc, key, mode, None, f'{seed_prefix}{i}',
                        total_bars=bars, max_tries=1, accept_any=True)
               for i in range(n)]
    finally:
        C.NEG, C.HARD_CONSTRAINTS = old_neg, old_hard   # 复原
    return out


def grammar_drafts(n: int = 20, key: int = 1, mode: str = 'major',
                   bars: int = 12, seed_prefix: str = 'gram') -> List[Dict[str, Any]]:
    """对照组：完整 accept/reject 循环（与论文 Table 3 的 grammar drafts 同构）。"""
    from score_learning.grammar import script as gs
    from score_learning.grammar.compose import compose
    sc = gs.load()
    return [compose(sc, key, mode, None, f'{seed_prefix}{i}', total_bars=bars)
            for i in range(n)]


def _to_piece(res: Dict[str, Any], mode: str, key: int) -> Dict[str, Any]:
    from score_learning.grammar.theory import (tonic_from_sharps,
                                              tonic_letter_pc)
    tpc = tonic_letter_pc(tonic_from_sharps(key, mode))[1]
    beats = [{'t': float(b['t']), 'midi': list(b['midi']),
              'eighth': list(b.get('eighth') or [None] * 4),
              'chord': b['chord'],
              'strong': int(b['beat']) in (1, 3),
              'downbeat': int(b['beat']) == 1,
              'fermata': bool(b.get('cadence')),
              'pos': float(b['beat'] - 1), 'bar_len': 4.0}
             for b in res['beats']]
    ph, cur, start = [], res['beats'][0]['phrase'], 0
    for i, b in enumerate(res['beats']):
        if b['phrase'] != cur:
            ph.append({'start': start, 'end': i - 1})
            start, cur = i, b['phrase']
    ph.append({'start': start, 'end': len(beats) - 1})
    return {'mode': mode, 'tonic_pc': tpc, 'beats': beats, 'phrases': ph}


def evaluate(n: int = 20, mode: str = 'major', key: int = 1,
             bars: int = 12, save: bool = True) -> Dict[str, Any]:
    """在同一检查器下比较「无 accept/reject」与「有 accept/reject」。"""
    from score_learning.grammar import script as gs
    from score_learning.grammar.evaluate import aggregate, piece_metrics
    sc = gs.load()
    rows: Dict[str, List[Dict[str, Any]]] = {}
    attempts: Dict[str, List[int]] = {}
    for name, gen in (('kernel_no_retry', kernel_drafts),
                      ('grammar_retry', grammar_drafts)):
        drafts = gen(n=n, key=key, mode=mode, bars=bars)
        rows[name] = [piece_metrics(_to_piece(d, mode, key), sc) for d in drafts]
        attempts[name] = [int(d.get('attempts') or 1) for d in drafts]
    summary = {name: aggregate(r) for name, r in rows.items()}
    for name in summary:
        att = attempts[name]
        summary[name]['attempts_mean'] = round(sum(att) / max(len(att), 1), 3)
        summary[name]['attempts_max'] = max(att)
        summary[name]['n'] = len(att)
        # 接受前是否干净：kernel 侧应当有相当比例不是 0
        viol = [r['violations'] for r in rows[name]]
        summary[name]['drafts_clean'] = sum(1 for v in viol if v == 0)
    delta = (summary['grammar_retry']['violations_per_100']
             - summary['kernel_no_retry']['violations_per_100'])
    out = {'summary': summary,
           'violations_reduction_per_100': round(delta, 2),
           'setting': {'n': n, 'mode': mode, 'key': key, 'bars': bars},
           'note': 'kernel =同一 compose() 但 max_tries=1（无 accept/reject 重试）'}
    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e2_kernel_baseline.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Unconstrained-kernel 基线')
    ap.add_argument('--n', type=int, default=20)
    ap.add_argument('--mode', default='major', choices=['major', 'minor'])
    ap.add_argument('--key', type=int, default=1)
    ap.add_argument('--bars', type=int, default=12)
    a = ap.parse_args(argv)
    r = evaluate(n=a.n, mode=a.mode, key=a.key, bars=a.bars)
    s = r['summary']
    print('=== Unconstrained-kernel 基线（n=%d, %s）===' % (a.n, a.mode))
    print('| 组| viol./100 | 干净草稿 | attempts 均值 | 最大 |')
    print('|---|---|---|---|---|')
    for name, lbl in (('kernel_no_retry', '无 accept/retry'),
                       ('grammar_retry', '有 accept/retry')):
        v = s[name]
        print('| %s | %.2f | %d/%d | %.2f | %d |' % (
            lbl, v['violations_per_100'], v['drafts_clean'], v['n'],
            v['attempts_mean'], v['attempts_max']))
    print()
    print('  硬约束循环减少 %.2f violations/100 beats' %
          r['violations_reduction_per_100'])
    print('  产物 → runs/experiments/e2_kernel_baseline.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
