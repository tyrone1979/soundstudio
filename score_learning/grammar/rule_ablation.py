# -*- coding: utf-8 -*-
"""逐条硬规则消融（RQ2 的后半问：多少约束在承重）。

背景
----
RQ2 问 *"how much of the constraint set is load-bearing?"*，但此前唯一真正
**逐条**做过的消融是 R11（``cadence_type_dist.py`` 的 R11-off 对照）；
R01--R10 一条都没有（``kernel_baseline`` 做的是**整层**开关）。第二轮同行
评审把它列为 M-9，并称其为「全文性价比最高的单个实验」。

本脚本补上能补的部分，并且**明确写出补不了的部分**：

===================  ========================================  ==========
规则                  关闭方式                                  能否单条关闭
===================  ========================================  ==========
R01 平行五八度        ``compose.parallel_perfect``               ✅
R02 隐伏五八度        ``compose.hidden_perfect_outer``           ✅
R03 声部超越          ``compose.overlap``                        ✅
R05 习得音域          ``Grammar.ranges`` 放宽到全 MIDI 跨度       ✅
R08 不良旋律音程      ``compose.bad_melodic``                    ✅
R03/R04 交叉与间距    ``_inner_states`` 内的候选集构造            ❌
R06 导音解决          女高音转移矩阵内部                          ❌
R07 七音解决          女高音转移矩阵内部                          ❌
R09 和弦完整性         ``_inner_states`` 内的候选集构造            ❌
R10 实证和弦二元组    女高音转移矩阵内部                          ❌
R11 终止公式锁定      ``compose_phrase_candidates``（已有）        ✅
===================  ========================================  ==========

「关不掉」的五种不是懒：它们在**候选集构造层**（``_inner_states`` 的七条
``continue``）或转移矩阵内部，与别的规则共用同一段代码，没有单点开关。
把它们伪装成可单条关闭才是问题。本报告因此只报可关闭的六条（含 R11），
并在 ``not_switchable`` 字段里逐条给原因。

评测口径：**关闭某条规则后重新生成，再用完整的检查器（论文已发表配置）
重新打分**，这样「少了这条约束，违规数涨多少」是可读的。

用法::

    venv/bin/python -m score_learning.grammar.rule_ablation [--n 20]
产物 ``runs/experiments/e2c_rule_ablation.json``。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from score_learning.grammar import script as gs  # noqa: E402
from score_learning.grammar import compose as C  # noqa: E402
from score_learning.grammar.check import check_satb as _check_satb  # noqa: E402
from score_learning.grammar.evaluate import aggregate, piece_metrics  # noqa: E402


def _as_int(ranges):
    return {k: {'low': int(v['low']), 'high': int(v['high'])}
            for k, v in ranges.items()}

OUT = os.path.join(ROOT, 'runs', 'experiments')

#: 可单条关闭的规则 → compose 模块里的谓词名
SWITCHABLE = {
    'R01': 'parallel_perfect',
    'R02': 'hidden_perfect_outer',
    'R03': 'overlap',
    'R08': 'bad_melodic',
}
NOT_SWITCHABLE = {
    'R03/R04': 'enforced in _inner_states candidate construction '
               '(voice order and spacing), no single predicate',
    'R06': 'inside the soprano/chord transition matrix',
    'R07': 'inside the soprano/chord transition matrix',
    'R09': 'enforced in _inner_states candidate construction '
           '(chord completeness, leading-tone doubling)',
    'R10': 'inside the soprano/chord transition matrix',
}
RULES = ('R01', 'R02', 'R03', 'R03/R04', 'R05', 'R06', 'R07', 'R08', 'R09')


def _piece_from_compose(res, mode, key):
    """把 compose() 的产出包成 ``piece_metrics`` 认得的 piece（同 refresh_eval）。"""
    from score_learning.grammar.theory import tonic_from_sharps, tonic_letter_pc

    tonic_pc = tonic_letter_pc(tonic_from_sharps(key, mode))[1]
    beats = [
        {
            't': float(b['t']), 'midi': list(b['midi']),
            'eighth': list(b.get('eighth') or [None] * 4), 'chord': b['chord'],
            'strong': int(b['beat']) in (1, 3), 'downbeat': int(b['beat']) == 1,
            'fermata': bool(b.get('cadence')), 'pos': float(b['beat'] - 1),
            'bar_len': 4.0,
        }
        for b in res['beats']
    ]
    phrases = []
    cur = res['beats'][0]['phrase']
    start = 0
    for i, b in enumerate(res['beats']):
        if b['phrase'] != cur:
            phrases.append({'start': start, 'end': i - 1})
            start = i
            cur = b['phrase']
    phrases.append({'start': start, 'end': len(beats) - 1})
    return {'mode': mode, 'tonic_pc': tonic_pc, 'beats': beats, 'phrases': phrases}


class _Patch:
    """上下文管理器：临时把一条硬规则从**搜索**与**验收门**里同时摘掉。

    两处都必须改，否则要么搜索放宽但草稿仍被门拒（attempts 虚高），要么
    门放行但搜索没放宽（什么都没发生）。
    """

    def __init__(self, rule: Optional[str]):
        self.rule = rule
        self.saved: Dict[str, Any] = {}

    def __enter__(self):
        if self.rule is None:
            return self
        if self.rule in SWITCHABLE:
            name = SWITCHABLE[self.rule]
            self.saved[name] = getattr(C, name)
            setattr(C, name, lambda *a, **k: False)
        elif self.rule == 'R05':
            self.saved['_GrammarInit'] = C.Grammar.__init__

            def init(self2, script, mode):
                self.saved['_GrammarInit'](self2, script, mode)
                self2.ranges = {
                    k: {'low': 0, 'high': 127,
                        'mean': v.get('mean', 60)}
                    for k, v in self2.ranges.items()}
            C.Grammar.__init__ = init
        else:
            raise ValueError(self.rule)
        # 验收门：把该规则从违例清单里过滤掉（其余规则照旧）
        orig = _check_satb

        def filtered(grid, beats, ranges, tb_max=19):
            return [v for v in orig(grid, beats, ranges, tb_max)
                    if v['rule'] != self.rule]
        self.saved['check_satb'] = C.check_satb
        C.check_satb = filtered
        return self

    def __exit__(self, *exc):
        if self.rule is None:
            return False
        if self.rule in SWITCHABLE:
            setattr(C, SWITCHABLE[self.rule], self.saved[SWITCHABLE[self.rule]])
        elif self.rule == 'R05':
            C.Grammar.__init__ = self.saved['_GrammarInit']
        C.check_satb = self.saved['check_satb']
        return False


def _arm(script, rule, n, mode='major', key=1):
    pieces = []
    attempts = []
    fails = 0
    for i in range(n):
        with _Patch(rule):
            try:
                res = C.compose(script, key, mode, None, f'abl{i}',
                                total_bars=12)
            except Exception:
                fails += 1
                continue
        attempts.append(res.get('attempts', 1))
        pieces.append(_piece_from_compose(res, mode, key))
    if not pieces:
        return None
    # 两套评测配置都要跑：论文已发表配置把 R05 关掉，于是「关掉 R05」这一
    # 臂在该配置下**必然**读出 0.00——那不是「R05 不承重」，是量表没开。
    # 用习得音域重打分才能看出它到底承重多少。
    rows = [piece_metrics(p, script) for p in pieces]
    rows_lr = [piece_metrics(p, script,
                             ranges=_as_int(script['voicing']['ranges']))
               for p in pieces]
    agg = aggregate(rows)
    cnt = Counter(r for x in rows for r in x['violation_rules'])
    beats = sum(x['beats'] for x in rows)
    return {
        'n': len(rows), 'failed': fails,
        'violations_per_100': agg['violations_per_100'],
        'harmony_ppl': agg['harmony_ppl'],
        'out_of_grammar': agg['out_of_grammar'],
        'attempts_mean': round(sum(attempts) / len(attempts), 3),
        'first_seed_rate': round(sum(1 for a in attempts if a == 1)
                                 / len(attempts), 3),
        'by_rule': {r: cnt.get(r, 0) for r in RULES},
        'by_rule_per_100': {r: round(100 * cnt.get(r, 0) / beats, 2)
                            for r in RULES},
        'learned_ranges_config': {
            'violations_per_100': aggregate(rows_lr)['violations_per_100'],
            'by_rule_per_100': {
                r: round(100 * Counter(x for y in rows_lr
                                       for x in y['violation_rules']).get(r, 0)
                         / (sum(x['beats'] for x in rows_lr) or 1), 2)
                for r in RULES},
        },
    }


def run(n: int = 20, save: bool = True) -> Dict[str, Any]:
    script = gs.load()
    out: Dict[str, Any] = {
        'question': ('RQ2 (second half): how much of the hard-constraint set '
                     'is load-bearing, rule by rule?'),
        'setting': {
            'n_drafts_per_arm': n, 'mode': 'major', 'key_sharps': 1,
            'total_bars': 12, 'seeds': 'abl0..abl%d' % (n - 1),
            'scored_with': 'full checker, published config (0-127 ranges, '
                           'tb_max=24) -- i.e. what the rule costs, not what '
                           'the ablated checker reports',
        },
        'arms': {}, 'not_switchable': NOT_SWITCHABLE,
        'switchable': sorted(list(SWITCHABLE) + ['R05']),
    }
    base = _arm(script, None, n)
    out['arms']['none (baseline)'] = base
    for rule in sorted(list(SWITCHABLE) + ['R05']):
        out['arms']['%s off' % rule] = _arm(script, rule, n)
    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e2c_rule_ablation.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def before_after_r02(max_seeds: int = 20, save: bool = True) -> Dict[str, Any]:
    """「关掉 R02 再重新解码」的真实前后对照：同一 seed，R02 开 / 关各生成一次，
    用完整检查器打分，取关闭后第一处被报出的 R02 及其前一拍。"""
    script = gs.load()
    for i in range(max_seeds):
        seed = f'abl{i}'
        drafts = {}
        for arm, rule in (('on', None), ('off', 'R02')):
            with _Patch(rule):
                res = C.compose(script, 1, 'major', None, seed, total_bars=12)
            drafts[arm] = _piece_from_compose(res, 'major', 1)
        m_on = piece_metrics(drafts['on'], script)
        m_off = piece_metrics(drafts['off'], script)
        hits = [v for v in m_off['violation_detail'] if v[0] == 'R02']
        if not hits:
            continue
        t = hits[0][1]
        b_on, b_off = drafts['on']['beats'], drafts['off']['beats']
        k = next(j for j, b in enumerate(b_off) if b['t'] == t)
        window = [{'t': b_off[j]['t'],
                   'on': {'midi': b_on[j]['midi'], 'chord': b_on[j]['chord']},
                   'off': {'midi': b_off[j]['midi'], 'chord': b_off[j]['chord']}}
                  for j in range(max(0, k - 1), k + 1)]
        out = {
            'question': 'Disable R02 and re-decode: what changes, and what '
                        'does the unchanged checker report?',
            'seed': seed, 'mode': 'major', 'key_sharps': 1, 'total_bars': 12,
            'beats_differing': sum(1 for a, b in zip(b_on, b_off)
                                   if a['midi'] != b['midi']),
            'n_beats': len(b_on),
            'violations_on': m_on['violation_detail'],
            'violations_off': m_off['violation_detail'],
            'first_r02': {'t': t, 'msg': hits[0][2]},
            'window': window,
        }
        if save:
            os.makedirs(OUT, exist_ok=True)
            with open(os.path.join(OUT, 'e2d_r02_case.json'), 'w',
                      encoding='utf-8') as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
        return out
    raise RuntimeError('no seed produced an R02 violation with R02 disabled')


def _fmt(out: Dict[str, Any]) -> str:
    L = ['| Arm | viol./100 | ppl | OOV | first-seed | top rule released |',
         '|---|---|---|---|---|---|']
    for arm, v in out['arms'].items():
        if not v:
            L.append('| %s | — | — | — | — | — |' % arm)
            continue
        rel = max(v['by_rule_per_100'].items(), key=lambda kv: kv[1])
        L.append('| %s | %.2f | %.2f | %.3f | %.2f | %s %.2f |'
                 % (arm, v['violations_per_100'], v['harmony_ppl'],
                    v['out_of_grammar'], v['first_seed_rate'],
                    rel[0], rel[1]))
    return '\n'.join(L)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=20)
    ap.add_argument('--r02-case', action='store_true',
                    help='只跑「关闭 R02」的单例前后对照')
    a = ap.parse_args()
    if a.r02_case:
        print(json.dumps(before_after_r02(), ensure_ascii=False, indent=1))
        raise SystemExit(0)
    o = run(n=a.n)
    print(_fmt(o))
    print('\nwrote', os.path.join(OUT, 'e2c_rule_ablation.json'))
