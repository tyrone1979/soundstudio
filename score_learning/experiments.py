# -*- coding: utf-8 -*-
"""重设计的实验矩阵（对应 ESWA 稿 §5 的 RQ1--RQ4）。

用法::

    venv/bin/python -m score_learning.experiments            # 全部
    venv/bin/python -m score_learning.experiments e1 e1b# 只跑指定

与论文现有实验的差异
--------------------
============================  ==================================  ============
论文现状                      本模块                                  解决什么
============================  ==================================  ============
RQ1 只做「有/无位置条件」      E1: 5 种平滑器 × 3 组上下文 × 剪枝×2  平滑器选择从未被辩护
困惑度是单次划分的点估计       E1 全部带 10 折 GroupKFold + 配对bootstrap  给出置信区间与显著性
无任何显著性检验               E1/E1b 报告 p 值与效应量            回答"差距是否显著"
位置条件只有 7 类/无两档      E1b: 位置粒度 4 档消融               位置条件的收益来自哪部分
复杂度预算只报规则数            E1c: 预算扫描 vs 留出拟合            预算是"更大"还是"更好"
关系学习(form_plan)无实验     E7: 用人工标注做曲式规划消融          上一轮新能力缺验证
============================  ==================================  ============

E1 的核心问题
-------------
论文 §2.2 与 §4.1 都写"The smoother itself is not new"，把 Witten-Bell 当作
既定选择，全文未与Kneser-Ney / Jelinek-Mercer / Absolute Discounting 比较过。
这在审稿中几乎必然被追问：为什么是 Witten-Bell？Kneser-Ney(1990) 是 n-gram
语言建模的事实标准，Jelinek-Mercer(1985) 是回退法的经典，Absolute
Discounting(Ney 1993) 是简化基线。本实验首次给出这四者的受控对比。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from score_learning.smoothing import (  # noqa: E402
    SMOOTHERS, fit, make_smoother, per_event_loss, perplexity,
)
from score_learning.stats import (  # noqa: E402
    GroupKFold, bootstrap_ci, bootstrap_paired_ci, mcnemar_exact,
    paired_permutation_test, summarize_folds,
)

OUT = os.path.join(ROOT, 'runs', 'experiments')
CHORALE_CACHE = os.path.join(ROOT, 'runs', 'grammar_cache',
                             'bach_chorales.json')
CTX_MIN = 3
#: 一元基线：Witten-Bell 在全均匀底座上退化的特例，论文 Table 2 的第 4 列


# ======================================================== 语料与事件流
def load_chorale_events():
    """返回 ``{mode: [(piece_id, piece), ...]}``（保留原始 piece）。

    存原始 piece 而非预切事件，因为 ``harmony_events`` 需要 ``vocab`` 参数，
    而词表要等训练折确定了才能算出来。这里**必须**复用
    ``grammar/learn.py`` 的真实实现，不能自己重写乐句切分 ——
    否则实验结论与生产代码不一致（这正是本模块第一版的问题：
    训练侧未过滤词表，导致词表 77 而 L1 上下文 158 个）。
    """
    d = json.load(open(CHORALE_CACHE, encoding='utf-8'))
    out: Dict[str, List[Tuple[str, Dict]]] = defaultdict(list)
    for p in d['pieces']:
        out[p['mode']].append((p['id'], p))
    return dict(out)


def events_of(piece: Dict, vset: set, pos_fn=None) -> List[Tuple]:
    """按 ``grammar/learn.py:101`` 的语义切事件：``(p2,p1,pos,chord)``。

    ``pos_fn`` 用于位置粒度消融（E1b）；为 None 时沿用 ``pos_class``。
    """
    from score_learning.grammar.learn import harmony_events
    evs = harmony_events(piece, vset)
    if pos_fn is None:
        return evs
    return [(a, b, pos_fn(p), c) for a, b, p, c in evs]


def pos_strategies() -> Dict[str, Any]:
    """位置条件的几种粒度。``None`` 表示抹掉位置信息（退化为纯 bigram）。

    - ``full``  论文现状：start / mid_s / mid_w / cad0..cad3（7 类）
    - ``coarse``  合并强/弱与cad1..3：start / mid / cad1 / cad0（4 类）
    - ``binary``  只分「终止式前/非终止式前」（2 类）
    - ``none``   常量 x（等价论文的 no-position 消融）
    """
    def full(p):
        return p

    def coarse(p):
        if p == 'start':
            return 'start'
        if p == 'cad0':
            return 'cad0'
        if p in ('cad1', 'cad2', 'cad3'):
            return 'cadN'
        return 'mid'

    def binary(p):
        return 'cad0' if p == 'cad0' else ('cadN' if p in ('cad1', 'cad2',
                                                            'cad3') else 'other')

    def none(_p):
        return 'x'

    return {'full': full, 'coarse': coarse, 'binary': binary, 'none': none}


def build_vocab(pieces, min_count: int = 8) -> List[str]:
    """复现 ``grammar/learn.py:92`` 的词表：训练集中出现 >= min_count 次的和弦。

    统计口径必须与 learn.py 一致 —— 它数的是 ``beats[*]['chord']`` 的原始出现
    次数，**不是**过滤后的事件数（两者在低频和弦上不同）。
    """
    c = Counter()
    for _pid, p in pieces:
        for b in p['beats']:
            if b.get('chord'):
                c[b['chord']] += 1
    return sorted(k for k, n in c.items() if n >= min_count)


# ================================================================ E1
def _flat(items) -> List[Tuple]:
    """把 [(pid, events), ...] 压平成事件列表。"""
    out = []
    for _pid, evs in items:
        out.extend(evs)
    return out


def _losses(model, events) -> List[float]:
    return per_event_loss(model, events)


def run_e1(n_splits: int = 10, n_boot: int = 2000,
           smoothers: Sequence[str] = ('wb', 'jm', 'ad', 'kn', 'mkn'),
           save: bool = True) -> Dict[str, Any]:
    """E1：平滑器 × 位置条件 × 剪枝 的完整消融（10 折 GroupKFold）。"""
    data = load_chorale_events()
    rows: List[Dict[str, Any]] = []
    # 基线损失：留出折上的一元分布
    strategies = pos_strategies()

    for mode in ('major', 'minor'):
        pieces = data[mode]
        ids = [pid for pid, _ in pieces]
        n_groups = len(set(ids))
        k = min(n_splits, max(2, n_groups))
        gkf = GroupKFold(n_splits=k, shuffle=True, random_state=0)

        for fold, (tr_i, te_i) in enumerate(gkf.split(ids)):
            tr_g = {ids[i] for i in tr_i}
            te_g = {ids[i] for i in te_i}
            tr = [(pid, p) for pid, p in pieces if pid in tr_g]
            te = [(pid, p) for pid, p in pieces if pid in te_g]
            vocab = build_vocab(tr)
            vset = set(vocab)
            if not vset:
                continue

            for pos_name, pos_fn in strategies.items():
                # 与 learn.py:101 一致：训练侧也按词表过滤
                ev_tr = [e for _pid, p in tr
                         for e in events_of(p, vset, pos_fn)]
                ev_te = [e for _pid, p in te
                         for e in events_of(p, vset, pos_fn)]
                if not ev_te or not ev_tr:
                    continue
                for sm in smoothers:
                    for pruned in (False, True):
                        m = fit(sm, ev_tr, vocab)
                        if pruned:
                            m.prune_context(('L3', 'L2'), CTX_MIN)
                        losses = _losses(m, ev_te)
                        rows.append({
                            'mode': mode, 'fold': fold, 'smoother': sm,
                            'pos': pos_name, 'pruned': int(pruned),
                            'n': len(losses),
                            'ppl': round(math.exp(sum(losses) / len(losses)), 4),
                            'losses': losses,
                        })
    # 汇总：按 (mode, smoother, pos, pruned) 跨折聚合
    summary = _summarize_e1(rows)
    if save:
        os.makedirs(OUT, exist_ok=True)
        slim = [{k: v for k, v in r.items() if k != 'losses'} for r in rows]
        with open(os.path.join(OUT, 'e1_matrix.json'), 'w',
                  encoding='utf-8') as f:
            json.dump({'summary': summary, 'folds': slim}, f,
                      ensure_ascii=False, indent=1)
    return {'rows': rows, 'summary': summary}


def _summarize_e1(rows) -> List[Dict[str, Any]]:
    """跨折汇总 + 配对 bootstrap（对 full-pos 参照做显著性）。"""
    by = defaultdict(list)
    for r in rows:
        by[(r['mode'], r['smoother'], r['pos'], r['pruned'])].append(r)
    out = []
    for key, rs in sorted(by.items()):
        mode, sm, pos, pruned = key
        stats = summarize_folds([r['ppl'] for r in rs])
        # 配对 bootstrap：同折内与「WB + no-pos + 同剪枝」比较
        ref_key = (mode, 'wb', 'none', pruned)
        ref_by_fold = {r['fold']: r for r in by.get(ref_key, [])}
        paired = None
        if ref_key != key:
            # 逐事件配对（按 fold 拼接）
            a, b = [], []
            for r in rs:
                rr = ref_by_fold.get(r['fold'])
                if rr is None:
                    continue
                a.extend(r['losses'])
                b.extend(rr['losses'])
            if a and len(a) == len(b):
                paired = bootstrap_paired_ci(a, b, n_boot=1000)
        out.append({
            'mode': mode, 'smoother': sm, 'pos': pos, 'pruned': pruned,
            'ppl_mean': round(stats['mean'], 4),
            'ppl_sd': round(stats['sd'], 4),
            'ppl_lo': round(stats['lo'], 4),
            'ppl_hi': round(stats['hi'], 4),
            'folds': stats['n'],
            'n_events': sum(r['n'] for r in rs),
            'delta_vs_wb_nopos': (round(paired['delta'], 4)
                                  if paired else None),
            'delta_ci': ([round(paired['lo'], 4), round(paired['hi'], 4)]
                         if paired else None),
            'p_paired': (round(paired['p_two_sided'], 5) if paired else None),
            'significant': (bool(paired['p_two_sided'] < 0.05) if paired
                            else None),
        })
    return out


# ============================================================== E1b
def run_e1b(n_splits: int = 10, smoothers=('wb', 'kn'),
            save: bool = True) -> Dict[str, Any]:
    """E1b：位置条件粒度消融（4 档），聚焦剪枝/未剪枝。"""
    data = load_chorale_events()
    strategies = pos_strategies()
    rows = []
    for mode in ('major', 'minor'):
        pieces = data[mode]
        ids = [pid for pid, _ in pieces]
        k = min(n_splits, max(2, len(set(ids))))
        gkf = GroupKFold(n_splits=k, shuffle=True, random_state=0)
        for fold, (tr_i, te_i) in enumerate(gkf.split(ids)):
            tr_g = {ids[i] for i in tr_i}
            te_g = {ids[i] for i in te_i}
            tr = [(pid, p) for pid, p in pieces if pid in tr_g]
            te = [(pid, p) for pid, p in pieces if pid in te_g]
            vocab = build_vocab(tr)
            vset = set(vocab)
            for pos_name, pos_fn in strategies.items():
                ev_tr = [x for _pid, p in tr
                         for x in events_of(p, vset, pos_fn)]
                ev_te = [x for _pid, p in te
                         for x in events_of(p, vset, pos_fn)]
                if not ev_te or not ev_tr:
                    continue
                for sm in smoothers:
                    m = fit(sm, ev_tr, vocab)
                    losses = _losses(m, ev_te)
                    rows.append({'mode': mode, 'fold': fold, 'smoother': sm,
                                 'pos': pos_name, 'pruned': 0,
                                 'n': len(losses),
                                 'ppl': round(math.exp(sum(losses) / len(losses)), 4),
                                 'losses': losses})
    summary = _summarize_e1(rows)
    if save:
        os.makedirs(OUT, exist_ok=True)
        slim = [{k: v for k, v in r.items() if k != 'losses'} for r in rows]
        with open(os.path.join(OUT, 'e1b_position.json'), 'w',
                  encoding='utf-8') as f:
            json.dump({'summary': summary, 'folds': slim}, f,
                      ensure_ascii=False, indent=1)
    return {'rows': rows, 'summary': summary}


# ============================================================== E1d
def run_e1d(n_splits: int = 10, smoothers=('wb',), save: bool = True) -> Dict[str, Any]:
    """E1d：验证集二次使用的乐观偏差（嵌套交叉验证）。

    ``prune_with_regression``（``learn_core.py:120``）用**留出集**决定「剪不剪枝」，
    论文 §5.1 又在**同一个留出集**上报困惑度。这是标准的模型选择泄漏。

    正确做法是嵌套交叉验证：内层验证集只用于选剪枝，外层留出集只用于报告。
    本实验在同一份语料、同一组折上同时算两种口径，量化偏差。

    **实测结论（10 折，chorale）**：major 偏差 −2.4%、minor −2.1%。
    偏差为**负**（论文口径反而略低）说明泄漏存在但影响有限—— 因为
    ``ctx_min=3`` 下「剪or 不剪」的决策在 10 折中高度一致，偶尔选错也被
    ``restore`` 兜回。这是个应当如实报告的**小**偏差，不推翻论文结论，
    但审稿人问到时可以立刻回答。
    """
    data = load_chorale_events()
    rows = []
    for mode in ('major', 'minor'):
        pieces = data[mode]
        ids = [pid for pid, _ in pieces]
        k = min(n_splits, max(2, len(set(ids))))
        gkf = GroupKFold(n_splits=k, shuffle=True, random_state=0)
        for fold, (tr_i, te_i) in enumerate(gkf.split(ids)):
            tr_g = {ids[i] for i in tr_i}
            te_g = {ids[i] for i in te_i}
            tr = [(pid, p) for pid, p in pieces if pid in tr_g]
            te = [(pid, p) for pid, p in pieces if pid in te_g]
            vocab = build_vocab(tr)
            vset = set(vocab)
            ev_tr = [x for _pid, p in tr for x in events_of(p, vset)]
            ev_te = [x for _pid, p in te for x in events_of(p, vset)]
            if not ev_te or not ev_tr:
                continue
            for sm in smoothers:
                # A) 论文现状：在 ev_te 上剪枝并在 ev_te 上报告
                mA = fit(sm, ev_tr, vocab)
                u = perplexity(mA, ev_te)
                mA.prune_context(('L3', 'L2'), CTX_MIN)
                s = perplexity(mA, ev_te)
                ppl_a = min(s, u)
                # B) 嵌套：内层折半选剪枝，外层只报告
                half = max(1, len(ev_te) // 2)
                va, vb = ev_te[:half], ev_te[half:]
                mB = fit(sm, ev_tr, vocab)
                u2 = perplexity(mB, va)
                mB.prune_context(('L3', 'L2'), CTX_MIN)
                if perplexity(mB, va) > u2:
                    mB = fit(sm, ev_tr, vocab)   # 回退，等价 prune_with_regression
                ppl_b = perplexity(mB, vb) if vb else float('nan')
                rows.append({'mode': mode, 'fold': fold, 'smoother': sm,
                             'ppl_paper': ppl_a, 'ppl_nested': ppl_b})
    summary = []
    for mode in ('major', 'minor'):
        rs = [r for r in rows if r['mode'] == mode]
        if not rs:
            continue
        a = summarize_folds([r['ppl_paper'] for r in rs])
        b = summarize_folds([r['ppl_nested'] for r in rs])
        bias = 100.0 * (a['mean'] - b['mean']) / b['mean'] if b['mean'] else float('nan')
        paired = bootstrap_paired_ci(
            [r['ppl_paper'] for r in rs], [r['ppl_nested'] for r in rs],
            n_boot=2000)
        summary.append({
            'mode': mode, 'folds': a['n'],
            'ppl_paper': round(a['mean'], 4),
            'ppl_paper_lo': round(a['lo'], 4), 'ppl_paper_hi': round(a['hi'], 4),
            'ppl_nested': round(b['mean'], 4),
            'ppl_nested_lo': round(b['lo'], 4), 'ppl_nested_hi': round(b['hi'], 4),
            'bias_pct': round(bias, 3),
            'p_paired': round(paired['p_two_sided'], 5),
        })
    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e1d_nested.json'), 'w',
                  encoding='utf-8') as f:
            json.dump({'summary': summary,
                       'note': '论文口径在留出集上选剪枝并报数；嵌套口径内外分离'},
                      f, ensure_ascii=False, indent=1)
    return {'summary': summary, 'rows': rows}


# ============================================================== E1c
def run_e1c(n_splits: int = 10, smoothers=('wb', 'jm'),
            save: bool = True) -> Dict[str, Any]:
    """E1c：复杂度预算 ``s`` 的消融 —— 「更大」是否等于「更好」。

    论文 §4.1（Eq. 6）用复杂度预算 ``s`` 决定剪枝阈值、节奏模板数、轮廓类数，
    §5.5（Experiment 5）**只统计了规则条数**（pop 1586 / jazz 4860 /
    symphony 4164），并由此得出「预算随织体扩大知识库，as claimed」。

    但「知识库更大」是关于**容量**的陈述，不是关于**泛化**的陈述。
    更大的 s 降低 ``ctx_min``（``leadsheet/learn.py:70``），保留更多低计数上下文，
    规则更多也更可能过拟合。本实验直接扫描 ``ctx_min ∈ {2,3,4,6,8,99}``
    （99 ≈ 不剪枝），在**同一份语料**上测留出困惑度与知识库规模，给出
    「容量—泛化」曲线。

    这同时回答一个之前发现的现象：``hardbop/minor`` 的规则模型曾比一元基线
    还差（18.34 vs 17.37），说明当前的 ``CTX_MIN=3`` 对多数体裁偏激进。

    **必须同时跑 ``jm``**：本实验早先只跑 ``wb``，据此得出「``CTX_MIN=3``
    偏松、6~8 更优」。但出厂语法早已改用 ``jm``，而在 ``jm`` 下结论**翻转**
    ——最优是 2~3，6/8 显著更差（配对置换 p=0.0002）。剪枝阈值的最优位置
    依赖估计器，只测一个估计器就会得出对出厂配置的錯誤建议。
    """
    data = load_chorale_events()
    ctx_mins = (2, 3, 4, 6, 8, 99)
    rows = []
    for mode in ('major', 'minor'):
        pieces = data[mode]
        ids = [pid for pid, _ in pieces]
        k = min(n_splits, max(2, len(set(ids))))
        gkf = GroupKFold(n_splits=k, shuffle=True, random_state=0)
        for fold, (tr_i, te_i) in enumerate(gkf.split(ids)):
            tr_g = {ids[i] for i in tr_i}
            te_g = {ids[i] for i in te_i}
            tr = [(pid, p) for pid, p in pieces if pid in tr_g]
            te = [(pid, p) for pid, p in pieces if pid in te_g]
            vocab = build_vocab(tr)
            vset = set(vocab)
            ev_tr = [x for _pid, p in tr for x in events_of(p, vset)]
            ev_te = [x for _pid, p in te for x in events_of(p, vset)]
            if not ev_te or not ev_tr:
                continue
            for sm in smoothers:
                for cm in ctx_mins:
                    m = fit(sm, ev_tr, vocab)
                    if cm < 99:
                        m.prune_context(('L3', 'L2'), ctx_min=cm)
                    # 知识库规模 = 存活上下文数 × 存活后继数
                    # m.t[lv] 是 {context: Counter}，故 .values() 直接是 Counter
                    n_ctx = sum(len(t) for lv in ('L3', 'L2')
                                for t in m.t[lv].values())
                    n_succ = sum(len(cnt) for lv in ('L3', 'L2')
                                 for cnt in m.t[lv].values())
                    losses = per_event_loss(m, ev_te)
                    rows.append({
                        'mode': mode, 'fold': fold, 'smoother': sm,
                        'ctx_min': cm, 'n_ctx': n_ctx, 'n_succ': n_succ,
                        'n': len(losses),
                        'ppl': round(math.exp(sum(losses) / len(losses)), 4),
                        'losses': losses,
                    })
    summary = _summarize_e1c(rows)
    if save:
        os.makedirs(OUT, exist_ok=True)
        slim = [{k: v for k, v in r.items() if k != 'losses'} for r in rows]
        with open(os.path.join(OUT, 'e1c_budget.json'), 'w',
                  encoding='utf-8') as f:
            json.dump({'summary': summary, 'folds': slim}, f,
                      ensure_ascii=False, indent=1)
    return {'rows': rows, 'summary': summary}


def _summarize_e1c(rows) -> List[Dict[str, Any]]:
    """跨折汇总 + 相对 ctx_min=CTX_MIN(3) 与「不剪枝」的配对检验。"""
    by = defaultdict(list)
    for r in rows:
        by[(r['mode'], r['smoother'], r['ctx_min'])].append(r)
    out = []
    for key, rs in sorted(by.items()):
        mode, sm, cm = key
        st = summarize_folds([r['ppl'] for r in rs])
        out.append({
            'mode': mode, 'smoother': sm, 'ctx_min': cm,
            'ppl_mean': round(st['mean'], 4), 'ppl_sd': round(st['sd'], 4),
            'ppl_lo': round(st['lo'], 4), 'ppl_hi': round(st['hi'], 4),
            'folds': st['n'],
            'n_ctx': int(sum(r['n_ctx'] for r in rs) / max(len(rs), 1)),
            'n_succ': int(sum(r['n_succ'] for r in rs) / max(len(rs), 1)),
        })
    # 找出每个 (mode, smoother) 的最优 ctx_min
    best = {}
    for r in out:
        k = (r['mode'], r['smoother'])
        if k not in best or r['ppl_mean'] < best[k]['ppl_mean']:
            best[k] = r
    for r in out:
        r['is_best'] = (best[(r['mode'], r['smoother'])]['ctx_min']
                        == r['ctx_min'])
    return out


# ============================================================== E1c-p
def pvalues_e1c(src: Optional[str] = None, save: bool = True) -> Dict[str, Any]:
    """E1c-p：为 ``e1c_budget.json`` 的 ``ctx_min`` 扫描补配对置换检验。

    为什么单列一个函数
    ------------------
    论文 §5.5 报了四个 p 值（``ctx_min=6`` / ``8`` / unpruned 对 ``3``，以及
    minor ``ctx_min=2`` 对 ``3``），但 ``e1c_budget.json`` 的行里**没有 p 值
    字段**（只有 ``ppl_mean/sd/lo/hi/folds/n_ctx/n_succ/is_best``）。第二轮
    同行评审把它列为 F-1②：**论文声明"每个表都有脚本"，而这几个统计量
    在全库里找不到出处**。

    本函数不重新训练（那会花几十分钟且必然复现同样的数），而是**直接读
    已发布的逐折困惑度**做配对置换检验：同一折、同一语料、同一估计器下
    两个 ``ctx_min`` 的困惑度逐折相减，用
    :func:`stats.paired_permutation_test` 做符号翻转置换。逐折配对（n=10）
    是这里唯一诚实的粒度——``e1c_budget.json`` 发布时剥掉了逐事件损失
    （``slim`` 去掉 ``losses``），所以事件级 bootstrap 无法从产物复算。

    产物 ``runs/experiments/e1c_budget_p.json``。
    """
    path = src or os.path.join(OUT, 'e1c_budget.json')
    d = json.load(open(path, encoding='utf-8'))
    folds = d['folds']
    by: Dict[Tuple, Dict[int, float]] = defaultdict(dict)
    for r in folds:
        by[(r['mode'], r['smoother'], r['ctx_min'])][r['fold']] = r['ppl']
    out = []
    for (mode, sm, cm), per_fold in sorted(by.items()):
        if cm == CTX_MIN:
            continue
        ref = by.get((mode, sm, CTX_MIN))
        if not ref:
            continue
        common = sorted(set(per_fold) & set(ref))
        a = [per_fold[f] for f in common]
        b = [ref[f] for f in common]
        if len(common) < 3:
            continue
        # 效应量用**逐折 nats/event** 的平均（ln ppl 之差再平均），不是
        # 「均值取 ln」：后者被折间方差带偏，且论文 §5.5 报的是 nats/event。
        na = [math.log(x) for x in a]
        nb = [math.log(x) for x in b]
        pt = paired_permutation_test(na, nb, n_perm=20000, random_state=0)
        delta = sum(x - y for x, y in zip(a, b)) / len(common)
        dnat = sum(x - y for x, y in zip(na, nb)) / len(common)
        out.append({
            'mode': mode, 'smoother': sm, 'ctx_min': cm,
            'vs_ctx_min': CTX_MIN, 'n_folds': len(common),
            'delta_ppl': round(delta, 4),
            'delta_nats_per_event': round(dnat, 5),
            'p_paired_permutation': round(pt['p_two_sided'], 5),
            'n_perm': pt['n_perm'],
        })
    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e1c_budget_p.json'), 'w',
                  encoding='utf-8') as f:
            json.dump({'source': 'runs/experiments/e1c_budget.json',
                       'test': 'paired permutation (sign flip), fold-level',
                       'rows': out}, f, ensure_ascii=False, indent=1)
    return {'rows': out}


# ============================================================== E1f
def run_e1f(n_splits: int = 10, ks: Sequence[int] = (2, 4, 8, 16),
            smoothers: Sequence[str] = ('wb', 'jm', 'kn'),
            save: bool = True) -> Dict[str, Any]:
    """E1f：学出来的隐状态（Baum--Welch HMM）对照（论文 §5.1 第二段）。

    论文报 ``23.3``（major）/ ``30.7``（minor），但 ``experiments.py`` 里
    **没有** ``run_e1f``，``runs/experiments/e1f_hmm.json`` 是一个无脚本可
    复现的孤儿产物——第二轮同行评审把它列为 F-1①（最高优先级）。本函数
    把它补成正规实验入口：``venv/bin/python -m score_learning.experiments e1f``。

    设计要点（都是为了让「HMM 输了」这条结论站得住）：

    - **同折、同词表、同指标**：与 E1 完全一致的 10 折 GroupKFold、同样的
      ``build_vocab(min_count=8)``、同样的逐事件负对数似然。
    - **同信息**：HMM 用 ``tie_pos=True``，即每个 ``pos`` 类各有一套发射/
      转移参数、状态空间共享。若用纯 HMM，它在终止式拍上的预测必然吃亏，
      因为 n-gram 直接拿到了 ``pos`` 而它只能靠状态间接推断——那不是公平
      比较。两个模型拿到的信息相同，差别只在**是否共享统计**。
    - **HMM 必须分段评测**：见 :func:`_hmm_segments`。
    - 同时报 ``wb`` / ``jm``（λ=0.5，出厂配置）/ ``kn`` 三个 n-gram 对照，
      因此「HMM vs 回退」的差距读的是**同一次运行**，不是跨文件拼表。
    """
    data = load_chorale_events()
    per_mode: Dict[str, Dict[str, List[float]]] = {}
    rows: List[Dict[str, Any]] = []
    for mode in ('major', 'minor'):
        pieces = data[mode]
        ids = [pid for pid, _ in pieces]
        k = min(n_splits, max(2, len(set(ids))))
        gkf = GroupKFold(n_splits=k, shuffle=True, random_state=0)
        acc: Dict[str, List[float]] = defaultdict(list)
        for fold, (tr_i, te_i) in enumerate(gkf.split(ids)):
            tr_g = {ids[i] for i in tr_i}
            te_g = {ids[i] for i in te_i}
            tr = [(pid, p) for pid, p in pieces if pid in tr_g]
            te = [(pid, p) for pid, p in pieces if pid in te_g]
            vocab = build_vocab(tr)
            vset = set(vocab)
            ev_tr = [x for _pid, p in tr for x in events_of(p, vset)]
            ev_te = [x for _pid, p in te for x in events_of(p, vset)]
            if not ev_te or not ev_tr:
                continue
            for sm in smoothers:
                m = fit(sm, ev_tr, vocab)
                m.prune_context(('L3', 'L2'), CTX_MIN)
                losses = per_event_loss(m, ev_te)
                ppl = math.exp(sum(losses) / len(losses))
                acc[sm].append(ppl)
                rows.append({'mode': mode, 'fold': fold, 'arm': sm,
                             'ppl': round(ppl, 4), 'n': len(losses)})
            for K in ks:
                # ★ HMM 必须**显式分段训练**。**不能**用 ``fit('hmm', ...)``：
                # 训练事件里 ``c`` 从不等于 ``'BOS'``（句首标志在 ``p1`` 上），
                # 于是 ``_segments()`` 会把整个训练集当成**一条**长序列，EM 学到
                # 跨乐句、跨曲子的虚假转移，与按乐句推理的结构不一致
                # （``HarmonicHMM.new_segment`` 的注释：实测 187 vs 24）。
                m = make_smoother('hmm', vocab, n_states=K)
                for e in ev_tr:
                    if e[1] == 'BOS':
                        m.new_segment()
                    m.add(*e)
                m.finalize()
                segs, cur = [], []
                for e in ev_te:
                    if e[1] == 'BOS':
                        if len(cur) >= 2:
                            segs.append(cur)
                        cur = []
                    cur.append((e[2], e[3]))
                if len(cur) >= 2:
                    segs.append(cur)
                losses = m.per_event_loss(ev_te, segments=segs)
                ppl = math.exp(sum(losses) / len(losses))
                acc['hmm%d' % K].append(ppl)
                rows.append({'mode': mode, 'fold': fold, 'arm': 'hmm%d' % K,
                             'ppl': round(ppl, 4), 'n': len(losses)})
        per_mode[mode] = {a: v for a, v in acc.items()}
    # 汇总 + HMM 最优 K 对 jm 的配对置换检验
    summary = {}
    for mode, acc in per_mode.items():
        summary[mode] = {}
        for arm, xs in acc.items():
            st = summarize_folds(xs)
            summary[mode][arm] = {
                'ppl_mean': round(st['mean'], 4), 'ppl_sd': round(st['sd'], 4),
                'ppl_lo': round(st['lo'], 4), 'ppl_hi': round(st['hi'], 4),
                'folds': st['n'],
            }
        hmm = {a: v for a, v in summary[mode].items() if a.startswith('hmm')}
        if hmm and 'jm' in acc:
            best = min(hmm, key=lambda a: hmm[a]['ppl_mean'])
            pt = paired_permutation_test(acc[best], acc['jm'], n_perm=20000,
                                         random_state=0)
            summary[mode]['best_hmm'] = best
            summary[mode]['best_hmm_vs_jm'] = {
                'delta_ppl': round(hmm[best]['ppl_mean']
                                   - summary[mode]['jm']['ppl_mean'], 4),
                'p_paired_permutation': round(pt['p_two_sided'], 5),
            }
    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e1f_hmm.json'), 'w',
                  encoding='utf-8') as f:
            json.dump({'folds': per_mode, 'rows': rows, 'summary': summary,
                       'note': ('Baum-Welch HMM (tie_pos=True) vs backoff '
                                'n-gram; same folds, vocab, metric. HMM '
                                'evaluated per phrase segment.')},
                      f, ensure_ascii=False, indent=1)
    return {'folds': per_mode, 'summary': summary, 'rows': rows}


# ============================================================== E1g
#: 论文 Eq.(3) 里写死的 ``λ=0.7``。这个数**不是**从文献搬来的：Jelinek–Mercer
#: 原文（Jelinek & Mercer 1985）把 λ 留作待估参数，教科书（Ney 1993）也只说
#: "typically 0.5–0.9"。它在我们的实现里是 ``smoothing.py:160`` 的默认值。
#: 本实验把它扫出来，好让论文写"实测最优"而不是"工程值"。
JM_LAMBDAS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def run_e1g(n_splits: int = 10, lams: Sequence[float] = JM_LAMBDAS,
            save: bool = True) -> Dict[str, Any]:
    """E1g：Jelinek–Mercer 插值权重 λ 的留出扫描（论文 Eq. 3 的 0.7 出处）。

    动机：``λ=0.7`` 是代码默认值，论文若只写"取 0.7"而不给依据，等于把
    一个未验证的自由参数藏在公式里。这里用与 E1 相同的 10 折 GroupKFold
    扫 λ∈{0.3..0.9}，报告每个 λ 的留出困惑度、最优 λ，以及
    **当前默认值 0.7 相对最优值的差距**——最后这项才是审稿人会问的
    "你的 0.7 是不是过拟合到留出集上了"。
    """
    data = load_chorale_events()
    rows: List[Dict[str, Any]] = []
    for mode in ('major', 'minor'):
        pieces = data[mode]
        ids = [pid for pid, _ in pieces]
        k = min(n_splits, max(2, len(set(ids))))
        gkf = GroupKFold(n_splits=k, shuffle=True, random_state=0)
        for fold, (tr_i, te_i) in enumerate(gkf.split(ids)):
            tr_g = {ids[i] for i in tr_i}
            te_g = {ids[i] for i in te_i}
            tr = [(pid, p) for pid, p in pieces if pid in tr_g]
            te = [(pid, p) for pid, p in pieces if pid in te_g]
            vocab = build_vocab(tr)
            vset = set(vocab)
            ev_tr = [x for _pid, p in tr for x in events_of(p, vset)]
            ev_te = [x for _pid, p in te for x in events_of(p, vset)]
            if not ev_te or not ev_tr:
                continue
            for lam in lams:
                m = fit('jm', ev_tr, vocab, lam=lam)
                m.prune_context(('L3', 'L2'), CTX_MIN)
                losses = per_event_loss(m, ev_te)
                rows.append({
                    'mode': mode, 'fold': fold, 'lam': lam, 'n': len(losses),
                    'ppl': round(math.exp(sum(losses) / len(losses)), 4),
                    'losses': losses,
                })
    summary = _summarize_e1g(rows)
    if save:
        os.makedirs(OUT, exist_ok=True)
        slim = [{k: v for k, v in r.items() if k != 'losses'} for r in rows]
        with open(os.path.join(OUT, 'e1g_lambda.json'), 'w',
                  encoding='utf-8') as f:
            json.dump({'summary': summary, 'folds': slim}, f,
                      ensure_ascii=False, indent=1)
    return {'rows': rows, 'summary': summary}


def _summarize_e1g(rows) -> List[Dict[str, Any]]:
    """按 (mode, λ) 跨折汇总，并标注最优 λ 与默认 0.7 的差距。"""
    by = defaultdict(list)
    for r in rows:
        by[(r['mode'], r['lam'])].append(r)
    out = []
    for (mode, lam), rs in sorted(by.items()):
        st = summarize_folds([r['ppl'] for r in rs])
        out.append({
            'mode': mode, 'lam': lam,
            'ppl_mean': round(st['mean'], 4), 'ppl_sd': round(st['sd'], 4),
            'ppl_lo': round(st['lo'], 4), 'ppl_hi': round(st['hi'], 4),
            'folds': st['n'], 'n_events': sum(r['n'] for r in rs),
            'is_default': abs(lam - 0.7) < 1e-9,
        })
    for mode in {r['mode'] for r in out}:
        rs = [r for r in out if r['mode'] == mode]
        best = min(rs, key=lambda r: r['ppl_mean'])
        dflt = next((r for r in rs if r['is_default']), None)
        for r in rs:
            r['is_best'] = (r is best)
            r['delta_vs_best_pct'] = (
                round(100.0 * (r['ppl_mean'] - best['ppl_mean'])
                      / best['ppl_mean'], 3))
        best['_best_lam'] = best['lam']
        if dflt is not None:
            best['_default_gap_pct'] = dflt['delta_vs_best_pct']
    return out


# ============================================================== E7
#: 边界匹配容差（小节）。人工标注的段界与规则网格的段界常差 1-2 小节
#: （弱起、终止式延音、anacrusis 会让「第一段起点」不是 bar 0），逐小节
#: 严格相等的 F1 会把两种方法都压到 0.15 以下，无法区分优劣。
E7_TOL = 2


def _truth_boundaries(solo) -> set:
    """真实 FORM 段界（**bar 号**，不是序列位置）。

    注意两个必须处理的细节：
    1. 标注里存在 ``bar_from = -1`` 的前导段（如 ``I1bars -1-0``），
       所以标签序列不一定从 bar 0 开始。
    2. 相邻两段之间可能有**未标注小节**（``I1 bars -1-0`` 之后 ``A1 bars 1-16``，
       bar 0 与 bar 1 之间是断点）。边界定义为「标签发生变化的那一小节」，
       并映射回真实 bar 号。
    """
    xs = sorted((b, lab) for s in solo['form_segs']
                for b, lab in ((b, s['label'])
                               for b in range(s['bar_from'], s['bar_to'] + 1)))
    out = set()
    for i in range(1, len(xs)):
        if xs[i][1] != xs[i - 1][1]:
            out.add(xs[i][0])
    return out


def _match(pred: set, truth: set, tol: int = E7_TOL) -> set:
    """容差匹配：pred 中每个元素若tol 之内有 truth 元素即算命中（贪心去重）。"""
    used, hits = set(), set()
    for p in sorted(pred):
        best, bestd = None, None
        for t in sorted(truth):
            if t in used:
                continue
            d = abs(p - t)
            if d <= tol and (bestd is None or d < bestd):
                best, bestd = t, d
        if best is not None:
            used.add(best)
            hits.add(p)
    return hits


def _prf(pred: set, truth: set, tol: int = E7_TOL):
    hits = _match(pred, truth, tol)
    prec = len(hits) / max(len(pred), 1)
    rec = len(hits) / max(len(truth), 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)
    return prec, rec, f1


def run_e7(save: bool = True) -> Dict[str, Any]:
    """E7：关系学习（``form_plan``）对曲式规划质量的消融。

    上一轮把 Weimar 人工段落标注编译成 ``form.form_plan`` 并注入风格包，
    但**没有任何实验验证它是否真的改善了曲式规划**。本实验用人工 FORM
    标注本身作 ground truth，比较三种规划器：

    - ``grid-8``   固定 8 小节网格（论文 ``form.templates`` 的隐式 baseline）
    - ``form_plan`` 实际调用 :func:`plan_form`，读注入后的 ``form_plan``
    - ``oracle``   直接用该曲的真实 FORM 段界（上界校验，应≈1.0）

    指标：段落边界 F1（容差 ±2 小节）。
    """
    rel_json = os.path.join(ROOT, 'runs', 'relation', 'bar_relations.json')
    if not os.path.exists(rel_json):
        return {'skipped': 'runs/relation/bar_relations.json 不存在；先跑 extract'}
    d = json.load(open(rel_json, encoding='utf-8'))
    results = d['results']

    plan_of = _load_form_plans()
    rows = []
    for r in results:
        solo = r['solo']
        tb = _truth_boundaries(solo)
        nbar = solo['nbar']
        if len(tb) < 2 or nbar < 16:
            continue
        n_units = len(solo['form_segs'])
        row0 = {'melid': solo['melid'], 'nbar': nbar, 'n_units': n_units,
                'n_truth_bounds': len(tb), 'style': solo.get('style')}

        # --- 1) 固定 8 小节网格
        rows.append(dict(row0, method='grid-8',
                         **dict(zip(('prec', 'rec', 'f1'),
                                    _prf(set(range(8, nbar, 8)), tb)))))
        # --- 2) 真实 form_plan（按该曲的段数与体裁取 policies）
        pred_fp = _form_plan_boundaries(plan_of, solo)
        if pred_fp is not None:
            rows.append(dict(row0, method='form_plan',
                             **dict(zip(('prec', 'rec', 'f1'),
                                        _prf(pred_fp, tb)))))
        # --- 3) oracle（上界校验）
        rows.append(dict(row0, method='oracle',
                         **dict(zip(('prec', 'rec', 'f1'), _prf(tb, tb)))))
    agg = defaultdict(list)
    for row in rows:
        agg[row['method']].append(row)
    summary = {m: _agg_form(v) for m, v in agg.items()}
    # 配对 bootstrap：form_plan vs grid-8（同曲成对）
    paired = None
    if 'form_plan' in agg and 'grid-8' in agg:
        a = {x['melid']: x['f1'] for x in agg['form_plan']}
        b = {x['melid']: x['f1'] for x in agg['grid-8']}
        common = sorted(set(a) & set(b))
        if len(common) >= 3:
            paired = bootstrap_paired_ci([a[m] for m in common],
                                         [b[m] for m in common], n_boot=2000)
            paired['n_pairs'] = len(common)
    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e7_form.json'), 'w',
                  encoding='utf-8') as f:
            json.dump({'summary': summary,
                       'paired_formplan_vs_grid8': paired,
                       'tol_bars': E7_TOL,
                       'n_solos': len({r['solo']['melid'] for r in results})},
                      f, ensure_ascii=False, indent=1)
    return {'summary': summary, 'rows': rows, 'paired': paired}


def _load_form_plans() -> Dict[str, Dict[str, List]]:
    """读 ``runs/relation/form_plan.json`` → ``{style: {n_seg: [items]}}``。"""
    p = os.path.join(ROOT, 'runs', 'relation', 'form_plan.json')
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding='utf-8'))
    return d.get('policies') or {}


def _form_plan_boundaries(plan_of, solo) -> Optional[set]:
    """按 ``form_plan`` 的段数策略推出段界（用最常见模式的中位段长）。"""
    pol = plan_of.get((solo.get('style') or '').upper())
    if not pol:
        return None
    nbar = solo['nbar']
    # 选段数：优先与真实段数相同的那档（这正是 plan_form 的匹配逻辑）
    cand = None
    for k in sorted(pol, key=lambda x: int(x)):
        if int(k) == len(solo['form_segs']):
            cand = pol[k]
            break
    if not cand:
        return None
    item = max(cand, key=lambda it: it.get('p') or 0.0)
    segs = item.get('segments') or []
    if not segs:
        return None
    total = sum(s['bars'] for s in segs) or 1
    # 按比例缩放到实际小节数并累积
    bounds, acc = set(), 0.0
    for s in segs[:-1]:
        acc += s['bars'] / total * nbar
        bounds.add(int(round(acc)))
    return bounds


def _agg_form(rows):
    """按 method 汇总 P/R/F1 的均值与 95% 置信区间。"""
    out = {}
    for m in ('prec', 'rec', 'f1'):
        s = summarize_folds([r[m] for r in rows])
        out[m] = round(s['mean'], 4)
        out[m + '_sd'] = round(s['sd'], 4)
        out[m + '_lo'] = round(s['lo'], 4)
        out[m + '_hi'] = round(s['hi'], 4)
    out['n'] = len(rows)
    return out


def _cluster_bootstrap(diff_sum: Sequence[float], n_ev: Sequence[int],
                       n_boot: int = 10000, alpha: float = 0.05,
                       random_state: int = 0) -> Dict[str, float]:
    """按**乐曲**重采样的配对 bootstrap。

    ``diff_sum[i]`` 是第 i 首曲子上两模型逐拍损失差之和，``n_ev[i]`` 是拍数；
    统计量是比率估计 ``sum(diff) / sum(n)``（nats/拍）。同曲内各拍时间相关，
    逐拍重采样会把它们当独立样本，低估方差。
    """
    d = np.asarray(diff_sum, dtype=float)
    n = np.asarray(n_ev, dtype=float)
    point = float(d.sum() / n.sum())
    rng = np.random.default_rng(random_state)
    idx = rng.integers(0, len(d), size=(int(n_boot), len(d)))
    reps = d[idx].sum(axis=1) / n[idx].sum(axis=1)
    lo, hi = np.quantile(reps, [alpha / 2, 1 - alpha / 2])
    extreme = int(np.count_nonzero(reps >= 0) if point < 0
                  else np.count_nonzero(reps <= 0))
    # Plus-one correction avoids impossible Monte Carlo p=0 and makes the
    # finite resolution of the bootstrap explicit.
    tail = (extreme + 1.0) / (len(reps) + 1.0)
    return {'delta': point, 'lo': float(lo), 'hi': float(hi),
            'p_two_sided': float(min(1.0, 2 * tail)), 'n_pieces': len(d)}


def run_e1h(n_splits: int = 10, n_boot: int = 10000,
            smoothers: Sequence[Tuple[str, Dict[str, Any]]] = (
                ('wb', {}), ('jm', {'lam': 0.5})),
            save: bool = True) -> Dict[str, Any]:
    """E1h：位置条件的效应，按乐曲（而非逐拍）重采样的置信区间。

    与 E1 同一折分、同一词表、``ctx_min=3`` 剪枝；只把配对单位从拍换成曲子。
    """
    data = load_chorale_events()
    strategies = pos_strategies()
    acc: Dict[Tuple[str, str], Dict[str, List[float]]] = defaultdict(
        lambda: {'diff': [], 'n': [], 'beat_full': [], 'beat_none': []})
    for mode in ('major', 'minor'):
        pieces = data[mode]
        ids = [pid for pid, _ in pieces]
        k = min(n_splits, max(2, len(set(ids))))
        gkf = GroupKFold(n_splits=k, shuffle=True, random_state=0)
        for tr_i, te_i in gkf.split(ids):
            tr_g = {ids[i] for i in tr_i}
            te_g = {ids[i] for i in te_i}
            tr = [(pid, p) for pid, p in pieces if pid in tr_g]
            te = [(pid, p) for pid, p in pieces if pid in te_g]
            vocab = build_vocab(tr)
            vset = set(vocab)
            if not vset:
                continue
            models = {}
            for pos_name in ('full', 'none'):
                fn = strategies[pos_name]
                ev_tr = [e for _pid, p in tr for e in events_of(p, vset, fn)]
                for sm, kw in smoothers:
                    m = fit(sm, ev_tr, vocab, **kw)
                    m.prune_context(('L3', 'L2'), CTX_MIN)
                    models[(sm, pos_name)] = m
            for _pid, p in te:
                ev_f = events_of(p, vset, strategies['full'])
                ev_n = events_of(p, vset, strategies['none'])
                if not ev_f:
                    continue
                for sm, _kw in smoothers:
                    lf = _losses(models[(sm, 'full')], ev_f)
                    ln = _losses(models[(sm, 'none')], ev_n)
                    a = acc[(mode, sm)]
                    a['diff'].append(sum(lf) - sum(ln))
                    a['n'].append(len(lf))
                    a['beat_full'].extend(lf)
                    a['beat_none'].extend(ln)
    summary = []
    for (mode, sm), a in sorted(acc.items()):
        cb = _cluster_bootstrap(a['diff'], a['n'], n_boot=n_boot)
        bb = bootstrap_paired_ci(a['beat_full'], a['beat_none'], n_boot=2000)
        summary.append({
            'mode': mode, 'smoother': sm, 'n_beats': int(sum(a['n'])),
            'ppl_full': round(math.exp(sum(a['beat_full']) / len(a['beat_full'])), 3),
            'ppl_none': round(math.exp(sum(a['beat_none']) / len(a['beat_none'])), 3),
            'delta_nats': round(cb['delta'], 4),
            'piece_ci': [round(cb['lo'], 4), round(cb['hi'], 4)],
            'piece_p': round(cb['p_two_sided'], 5),
            'n_pieces': cb['n_pieces'],
            'beat_ci': [round(bb['lo'], 4), round(bb['hi'], 4)],
            'beat_p': round(bb['p_two_sided'], 5),
        })
    out = {'question': 'Cadence-distance effect with pieces, not beats, as '
                       'the resampling unit',
           'setting': {'folds': n_splits, 'ctx_min': CTX_MIN,
                       'delta': 'full-position minus no-position, nats/beat',
                       'n_boot_piece': n_boot},
           'summary': summary}
    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e1h_piece_bootstrap.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def run_e1i(n_splits: int = 10, n_boot: int = 10000,
            lambdas: Sequence[float] = (0.7, 0.5),
            save: bool = True) -> Dict[str, Any]:
    """E1i: compare JM with WB using whole pieces as paired clusters.

    Every held-out piece is scored by both estimators trained on the same fold.
    Bootstrap resampling therefore preserves both the within-piece transitions
    and the estimator pairing requested by the SPE review.
    """
    data = load_chorale_events()
    acc: Dict[Tuple[str, float], Dict[str, List[float]]] = defaultdict(
        lambda: {'diff': [], 'n': [], 'jm': [], 'wb': []})
    full = pos_strategies()['full']
    for mode in ('major', 'minor'):
        pieces = data[mode]
        ids = [pid for pid, _ in pieces]
        k = min(n_splits, max(2, len(set(ids))))
        gkf = GroupKFold(n_splits=k, shuffle=True, random_state=0)
        for tr_i, te_i in gkf.split(ids):
            tr_g = {ids[i] for i in tr_i}
            te_g = {ids[i] for i in te_i}
            tr = [(pid, p) for pid, p in pieces if pid in tr_g]
            te = [(pid, p) for pid, p in pieces if pid in te_g]
            vocab = build_vocab(tr)
            vset = set(vocab)
            if not vset:
                continue
            ev_tr = [e for _pid, p in tr for e in events_of(p, vset, full)]
            wb = fit('wb', ev_tr, vocab)
            wb.prune_context(('L3', 'L2'), CTX_MIN)
            jm_models = {}
            for lam in lambdas:
                jm = fit('jm', ev_tr, vocab, lam=float(lam))
                jm.prune_context(('L3', 'L2'), CTX_MIN)
                jm_models[float(lam)] = jm
            for _pid, piece in te:
                ev_te = events_of(piece, vset, full)
                if not ev_te:
                    continue
                wb_loss = _losses(wb, ev_te)
                for lam, jm in jm_models.items():
                    jm_loss = _losses(jm, ev_te)
                    a = acc[(mode, lam)]
                    a['diff'].append(sum(jm_loss) - sum(wb_loss))
                    a['n'].append(len(ev_te))
                    a['jm'].extend(jm_loss)
                    a['wb'].extend(wb_loss)

    summary = []
    for (mode, lam), a in sorted(acc.items()):
        cb = _cluster_bootstrap(a['diff'], a['n'], n_boot=n_boot)
        summary.append({
            'mode': mode,
            'jm_lambda': lam,
            'comparison': 'JM minus WB',
            'n_transitions': int(sum(a['n'])),
            'n_pieces': cb['n_pieces'],
            'jm_ppl_pooled': round(math.exp(sum(a['jm']) / len(a['jm'])), 4),
            'wb_ppl_pooled': round(math.exp(sum(a['wb']) / len(a['wb'])), 4),
            'delta_nats_per_transition': round(cb['delta'], 4),
            'piece_ci95': [round(cb['lo'], 4), round(cb['hi'], 4)],
            'piece_p_two_sided': round(cb['p_two_sided'], 5),
        })
    out = {
        'question': 'JM versus WB with pieces as paired resampling clusters',
        'setting': {
            'folds': n_splits,
            'position': 'full',
            'ctx_min': CTX_MIN,
            'n_boot_piece': n_boot,
            'pairing': 'same held-out piece scored by both estimators',
        },
        'summary': summary,
    }
    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e1i_estimator_piece_bootstrap.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


# ============================================================== 报告
def _fmt_table(summary, modes=('major', 'minor')) -> str:
    L = []
    L.append('| Mode | Smoother | Pos | Pruned | PPL (mean±sd) | 95% CI | Δ vs WB/none | p |')
    L.append('|---|---|---|---|---|---|---|---|')
    for m in modes:
        for r in summary:
            if r['mode'] != m:
                continue
            d = ('%.3f' % r['delta_vs_wb_nopos']) if r['delta_vs_wb_nopos'] is not None else '—'
            p = ('%.4f' % r['p_paired']) if r['p_paired'] is not None else '—'
            ci = '[%.2f, %.2f]' % (r['ppl_lo'], r['ppl_hi'])
            star = ' ✓' if r.get('significant') else ''
            L.append('| %s | %s | %s | %d | %.2f±%.2f | %s | %s%s | %s |'
                     % (m, r['smoother'], r['pos'], r['pruned'],
                        r['ppl_mean'], r['ppl_sd'], ci, d, star, p))
    return '\n'.join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description='重设计的实验矩阵')
    ap.add_argument('which', nargs='*',
                    default=['e1', 'e1b', 'e1c', 'e1d', 'e7'],
                    help='可选: e1 e1b e1c e1c_p e1d e1f e1g e1h e1i e2e e7 e8')
    ap.add_argument('--quick', action='store_true', help='少折少 bootstrap，冒烟用')
    a = ap.parse_args(argv)
    k = 3 if a.quick else 10
    nb = 300 if a.quick else 2000
    os.makedirs(OUT, exist_ok=True)

    if 'e1' in a.which:
        print('=== E1: 平滑器 × 位置 × 剪枝 消融 ===')
        r = run_e1(n_splits=k, n_boot=nb, save=not a.quick)
        print(_fmt_table(r['summary']))
    if 'e1b' in a.which:
        print('\n=== E1b: 位置粒度消融 ===')
        r = run_e1b(n_splits=k, save=not a.quick)
        print(_fmt_table(r['summary']))
    if 'e1h' in a.which:
        print('\n=== E1h: 位置效应，按乐曲重采样 ===')
        r = run_e1h(n_splits=k, save=not a.quick)
        print('| Mode | Smoother | Δ nats/拍 | 曲级 95% CI | 曲级 p | 拍级 95% CI |')
        print('|---|---|---|---|---|---|')
        for row in r['summary']:
            print('| %s | %s | %+.4f | [%+.4f, %+.4f] | %.5f | [%+.4f, %+.4f] |'
                  % (row['mode'], row['smoother'], row['delta_nats'],
                     *row['piece_ci'], row['piece_p'], *row['beat_ci']))
    if 'e1i' in a.which:
        print('\n=== E1i: JM vs WB，按乐曲配对重采样 ===')
        r = run_e1i(n_splits=k, save=not a.quick)
        print('| Mode | JM λ | JM PPL | WB PPL | Δ nats/transition | piece 95% CI | p |')
        print('|---|---|---|---|---|---|---|')
        for row in r['summary']:
            print('| %s | %.1f | %.4f | %.4f | %+.4f | [%+.4f, %+.4f] | %.5f |'
                  % (row['mode'], row['jm_lambda'], row['jm_ppl_pooled'],
                     row['wb_ppl_pooled'], row['delta_nats_per_transition'],
                     *row['piece_ci95'], row['piece_p_two_sided']))
    if 'e2e' in a.which:
        from score_learning.grammar import diversity
        print('\n=== E2e: SATB 草稿多样性 vs 留出巴赫 ===')
        print(diversity.fmt(diversity.run(save=not a.quick)))
    if 'e8' in a.which:
        from score_learning.grammar import chord_agreement
        print('\n=== E8: 自动和弦标注 vs When-in-Rome 人工分析 ===')
        r = chord_agreement.run(save=not a.quick)
        for g, s in r['summary'].items():
            print('  %-18s 曲 %3d 比较 %5d | 根音 %.3f 根音+性质 %.3f 全一致 %.3f'
                  % (g, s['pieces'], s['compared'], s['root'], s['root_quality'], s['full']))
    if 'e1d' in a.which:
        print('\n=== E1d: 验证集二次使用的偏差（嵌套 CV）===')
        r = run_e1d(n_splits=k, save=not a.quick)
        print('| Mode | 论文口径 PPL | 嵌套口径 PPL | 偏差 | p |')
        print('|---|---|---|---|---|')
        for row in r['summary']:
            print('| %s | %.2f [%.2f, %.2f] | %.2f [%.2f, %.2f] | %+.1f%% | %.4f |'
                  % (row['mode'], row['ppl_paper'], row['ppl_paper_lo'],
                     row['ppl_paper_hi'], row['ppl_nested'],
                     row['ppl_nested_lo'], row['ppl_nested_hi'],
                     row['bias_pct'], row['p_paired']))
    if 'e1c' in a.which:
        print('\n=== E1c: 复杂度预算（ctx_min）消融 ===')
        r = run_e1c(n_splits=k, save=not a.quick)
        print('| Estimator | Mode | ctx_min | 存活上下文 | 存活后继 '
              '| PPL (mean±sd) | 95% CI | 最优 |')
        print('|---|---|---|---|---|---|---|---|')
        for row in r['summary']:
            print('| %s | %s | %s | %d | %d | %.2f±%.2f | [%.2f, %.2f] |%s'
                  % (row['smoother'], row['mode'],
                     'none' if row['ctx_min'] == 99 else row['ctx_min'],
                     row['n_ctx'], row['n_succ'],
                     row['ppl_mean'], row['ppl_sd'],
                     row['ppl_lo'], row['ppl_hi'],
                     ' ★' if row['is_best'] else ''))
        # 剪枝阈值的最优位置依赖估计器：出厂语法用 jm，必须看 jm 那一组
        jm = [x for x in r['summary'] if x['smoother'] == 'jm']
        if jm:
            print('\n  出厂估计器 jm 下的最优ctx_min：')
            for mode in ('major', 'minor'):
                rs = [x for x in jm if x['mode'] == mode]
                if rs:
                    b = min(rs, key=lambda x: x['ppl_mean'])
                    cur = next((x for x in rs if x['ctx_min'] == CTX_MIN), None)
                    print('    %-6s 最优 ctx_min=%-3s (%.2f)；出厂 CTX_MIN=%d 为 %.2f'
                          ' (%+.2f%%)'
                          % (mode, b['ctx_min'], b['ppl_mean'], CTX_MIN,
                             cur['ppl_mean'] if cur else float('nan'),
                             100.0 * (cur['ppl_mean'] - b['ppl_mean'])
                             / b['ppl_mean'] if cur else float('nan')))
    if 'e7' in a.which:
        print('\n=== E7: 关系学习(form_plan) 消融（边界 F1，容差 ±%d 小节）==='
              % E7_TOL)
        r = run_e7(save=not a.quick)
        if 'skipped' in r:
            print('  跳过:', r['skipped'])
        else:
            for m, v in sorted(r['summary'].items(),
                               key=lambda kv: -kv[1]['f1']):
                print('  %-10s F1=%.3f±%.3f [%.3f,%.3f]  P=%.3f R=%.3f  n=%d'
                      % (m, v['f1'], v['f1_sd'], v['f1_lo'], v['f1_hi'],
                         v['prec'], v['rec'], v['n']))
            p = r.get('paired')
            if p:
                print('  配对 bootstrap（form_plan − grid-8，F1）:')
                print('    Δ=%.4f95%%CI=[%.4f, %.4f]  p=%.4f  n_pairs=%d'
                      % (p['delta'], p['lo'], p['hi'],
                         p['p_two_sided'], p['n_pairs']))
    if 'e1c_p' in a.which:
        print('\n=== E1c-p: ctx_min 扫描的配对置换检验（补论文 §5.5 的四个 p 值）===')
        r = pvalues_e1c(save=not a.quick)
        print('| Mode | Smoother | ctx_min | ΔPPL vs 3 | p | n_folds |')
        print('|---|---|---|---|---|---|')
        for row in r['rows']:
            print('| %s | %s | %s | %+.4f | %.4f | %d |'
                  % (row['mode'], row['smoother'],
                     'none' if row['ctx_min'] == 99 else row['ctx_min'],
                     row['delta_ppl'], row['p_paired_permutation'],
                     row['n_folds']))
    if 'e1f' in a.which:
        print('\n=== E1f: HMM（学出来的隐状态）vs 回退 n-gram ===')
        r = run_e1f(n_splits=k, save=not a.quick)
        for mode, arms in sorted(r['summary'].items()):
            print('  [%s]' % mode)
            for arm, v in sorted(arms.items()):
                if 'ppl_mean' not in v:
                    print('    %-14s %s' % (arm, v))
                    continue
                print('    %-14s %.2f±%.2f [%.2f, %.2f]'
                      % (arm, v['ppl_mean'], v['ppl_sd'], v['ppl_lo'],
                         v['ppl_hi']))
    if 'e1g' in a.which:
        print('\n=== E1g: Jelinek–Mercer λ 扫描（论文 Eq.3 的 0.7 出处）===')
        r = run_e1g(n_splits=k, save=not a.quick)
        print('| Mode | λ | PPL (mean±sd) | 95% CI | 相对最优 |')
        print('|---|---|---|---|---|')
        for row in r['summary']:
            mark = ' ←默认' if row['is_default'] else (' ←最优' if row['is_best'] else '')
            print('| %s | %.1f | %.2f±%.2f | [%.2f, %.2f] | %+.2f%%%s |'
                  % (row['mode'], row['lam'], row['ppl_mean'], row['ppl_sd'],
                     row['ppl_lo'], row['ppl_hi'],
                     row['delta_vs_best_pct'], mark))
        for mode in ('major', 'minor'):
            rs = [x for x in r['summary'] if x['mode'] == mode]
            if rs:
                b = next(x for x in rs if x['is_best'])
                d = next((x for x in rs if x['is_default']), None)
                print('  %s: 最优 λ=%.1f (%.2f)；默认 0.7 差 %+.2f%%'
                      % (mode, b['lam'], b['ppl_mean'],
                         d['delta_vs_best_pct'] if d else float('nan')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
