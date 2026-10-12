# -*- coding: utf-8 -*-
"""评估统计工具：分组交叉验证、bootstrap 置信区间、置换检验。

为什么需要这个模块
------------------
ESWA 稿 ``main.tex`` §5.3 的全部数字都来自**单次** MD5 划分
（350 首中留出 36 首，major 1062 个 slot / minor 904 个 slot），
报三个点估计（15.23 / 19.04 / 23.10），**没有置信区间，也没有显著性检验**。
审稿人几乎必然会问：15.23 与 19.04 的差距是否显著？n=1062 的留出集能支撑
多大的效应量？

还有两个更隐蔽的问题，本模块一并解决：

1. **重复 id 导致的分组泄漏。** ``is_heldout``（``learn_core.py:23``）按 piece
   id 做 MD5 哈希，但缓存里同一个 id 可能对应多个 piece：

   =========  =======  ========  ==========
   体裁        pieces   unique   最大重复
   =========  =======  ========  ==========
   symphony     640      62 31
   chopin      242      73        14
   sonata      724     175        13
   strings     1905        77
   folk        908     908         1
   jazz        410     410         1
   =========  =======  ========  ==========

   同一首曲子的不同调区变体（同id）会**同时**落进训练集和留出集。
   ``strings`` 尤其严重：190 个 piece 只有 5 个唯一 id，一个 id 重复 77 次。
   随机划分会高估泛化能力。本模块提供 :class:`GroupKFold` 按 id 分组。

2. **验证集二次使用。** ``prune_with_regression`` 用留出集决定剪不剪枝，
   论文又在同一个留出集上报困惑度。:func:`nested_select` 提供嵌套选择：
   内层验证集只用于选模型，外层留出集只用于报告。

零第三方依赖：numpy + 标准库 random。刻意不引入 scipy（venv 里没有）。
"""
from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


# ====================================================================== 分组
def group_of(item_id: str) -> str:
    """把 piece 归到「独立曲目」粒度，用于分组交叉验证。

    **实测结论：直接用 id 本身作group 即可，不要做前缀归一化。**

    曾经的启发式（按 ``_``/``-`` 分割取前段）是错的：``humdrum_haydn_..._sym001a``
    会被归成 ``"humdrum"``，640 首交响曲全部塌成一组。

    真实情况（实测 runs/grammar_cache/）：

    =========  =======  ========  =====  ================================
    体裁        pieces   unique   最大重复  重复 id 的性质
    =========  =======  ========  =====  ================================
    folk        908     908     1      无重复
    jazz        410     410     1      无重复
    pop         198     198     1      无重复
    madrigal    48      48       1      无重复
    symphony    640     62       31     同一首的调区切分片段（``region`` 首尾相接）
    sonata      724     175      13     同上
    chopin      242     73       14     同上
    strings     190     5        77     同上（占位 id，只有 5 首真实曲目）
    =========  =======  ========  =====  ================================

    所以「重复 id」不是数据脏，而是**长曲被切成多个片段**（``region=[0,14],[14,72]…``）。
    ``is_heldout``（``learn_core.py:23``）按 id 哈希，已保证同曲片段同侧（实测
    泄漏0%）。但由此产生另一个更要紧的问题：**有效样本量 = unique id 数**。
    ``strings`` 只有 5 首，论文 Table 4 报的 ``strings`` 行实质是单首曲子的分数；
    ``symphony`` 只有 62 首，n=640 片段会造成伪精度。

    因此本模块的 :class:`GroupKFold` 以 id 为 group，``k`` 不能超过 unique id 数，
    且报告的 n 应当是**曲目数**而非片段数。
    """
    return str(item_id)


class GroupKFold:
    """按 group 划分的 K 折。**贪心均衡**：每折把样本数最少的组先放进去。

    比 ``MD5 % K`` 好的地方：
    - 严格保证同一 group 不跨折（无泄漏）
    - 各折样本量偏差 < 1 个group（MD5 划分实测 symphony 只有 2% 落入 fold 0）

    参数
    ----
    n_splits : 折数
    shuffle  : 是否打乱 group 顺序（配合 random_state 保证可复现）
    """

    def __init__(self, n_splits: int = 10, shuffle: bool = True,
                 random_state: int = 0):
        self.n_splits = max(2, int(n_splits))
        self.shuffle = bool(shuffle)
        self.random_state = int(random_state)

    def split(self, ids: Sequence[str]):
        """输入每个样本的 piece id，产出 (train_idx, test_idx) 索引对。"""
        groups: Dict[str, List[int]] = defaultdict(list)
        for i, pid in enumerate(ids):
            groups[group_of(pid)].append(i)
        keys = list(groups)
        sizes = {g: len(groups[g]) for g in keys}
        if self.shuffle:
            rng = random.Random(self.random_state)
            rng.shuffle(keys)
        # 贪心：每轮挑「当前累计样本数最小」的折，把这个 group 放进去
        load = [0] * self.n_splits
        assign: Dict[str, int] = {}
        for g in sorted(keys, key=lambda k: -sizes[k]):   # 大组先放，减小残差
            j = min(range(self.n_splits), key=lambda k: load[k])
            assign[g] = j
            load[j] += sizes[g]
        for k in range(self.n_splits):
            test = [i for g, j in assign.items() if j == k
                    for i in groups[g]]
            train = [i for g, j in assign.items() if j != k
                     for i in groups[g]]
            yield sorted(train), sorted(test)

    def fold_of(self, ids: Sequence[str]) -> List[int]:
        """返回每个样本所属的折号（用于诊断各折规模）。"""
        out = [0] * len(ids)
        for k, (_tr, te) in enumerate(self.split(ids)):
            for i in te:
                out[i] = k
        return out


# ================================================================= bootstrap
def bootstrap_ci(values: Sequence[float], n_boot: int = 2000,
                 alpha: float = 0.05, statistic: Callable = None,
                 random_state: int = 0) -> Tuple[float, float, float]:
    """对**逐样本**的统计量做bootstrap 百分位区间。

    参数
    ----
    values : 逐样本的值（如逐事件 NLL）。**不要传聚合后的困惑度**——
             困惑度是 exp(均值)，不是均值，bootstrap 必须在原始尺度上重算。
    statistic : 默认取均值。若要算 exp(mean)请传 ``lambda v: math.exp(sum(v)/len(v))``。

    返回 (点估计, 下界, 上界)。
    """
    vals = [float(v) for v in values]
    n = len(vals)
    if n == 0:
        return (float('nan'),) * 3
    stat = statistic or (lambda v: sum(v) / len(v))
    point = stat(vals)
    if n == 1:
        return (point, point, point)
    rng = random.Random(random_state)
    reps = []
    for _ in range(int(n_boot)):
        sample = [vals[rng.randrange(n)] for _ in range(n)]
        reps.append(stat(sample))
    reps.sort()
    lo = reps[max(0, int(math.floor(alpha / 2 * len(reps))))]
    hi = reps[min(len(reps) - 1, int(math.ceil((1 - alpha / 2) * len(reps))))]
    return (point, lo, hi)


def bootstrap_paired_ci(loss_a: Sequence[float], loss_b: Sequence[float],
                        n_boot: int = 2000, alpha: float = 0.05,
                        random_state: int = 0) -> Dict[str, float]:
    """**配对**bootstrap：比较两个模型在同一批样本上的损失差。

    配对是关键——两个模型在同一事件上的损失高度相关，独立重采样会低估方差。
    返回 ``{'delta', 'lo', 'hi', 'p_two_sided'}``，其中 delta = mean(a) - mean(b)，
    delta<0 表示模型 a 更差（损失更低的是 b）。
    """
    a = [float(x) for x in loss_a]
    b = [float(x) for x in loss_b]
    if len(a) != len(b):
        raise ValueError('配对 bootstrap 要求等长')
    n = len(a)
    if n == 0:
        return {'delta': float('nan'), 'lo': float('nan'),
                'hi': float('nan'), 'p_two_sided': float('nan')}
    diff = [a[i] - b[i] for i in range(n)]
    point = sum(diff) / n
    rng = random.Random(random_state)
    reps = []
    for _ in range(int(n_boot)):
        s = 0.0
        for _ in range(n):
            s += diff[rng.randrange(n)]
        reps.append(s / n)
    reps.sort()
    lo = reps[max(0, int(math.floor(alpha / 2 * len(reps))))]
    hi = reps[min(len(reps) - 1, int(math.ceil((1 - alpha / 2) * len(reps))))]
    # 双侧 p 值：bootstrap 分布跨 0 的比例
    cross = sum(1 for r in reps if (r <= 0.0) if point > 0) or \
        sum(1 for r in reps if r >= 0.0) if point <= 0 else 0
    p = 2.0 * min(
        sum(1 for r in reps if r <= 0.0) / len(reps),
        sum(1 for r in reps if r >= 0.0) / len(reps),
    )
    p = min(1.0, p)
    return {'delta': point, 'lo': lo, 'hi': hi, 'p_two_sided': p,
            'n': n, 'crossings': cross}


def paired_permutation_test(loss_a: Sequence[float], loss_b: Sequence[float],
                            n_perm: int = 5000,
                            random_state: int = 0) -> Dict[str, float]:
    """配对置换检验（符号翻转）：非参数，不依赖正态假设。

    H0:两模型在每个样本上的损失差符号随机，即无系统性差异。
    """
    a = [float(x) for x in loss_a]
    b = [float(x) for x in loss_b]
    if len(a) != len(b):
        raise ValueError('配对置换检验要求等长')
    diff = [a[i] - b[i] for i in range(len(a))]
    obs = abs(sum(diff) / max(len(diff), 1))
    rng = random.Random(random_state)
    at_least = 0
    for _ in range(int(n_perm)):
        s = 0.0
        for d in diff:
            s += -d if rng.random() < 0.5 else d
        if abs(s / max(len(diff), 1)) >= obs - 1e-12:
            at_least += 1
    return {'observed': obs,
            'p_two_sided': (at_least + 1) / (n_perm + 1),
            'n_perm': n_perm}


# =============================================================== McNemar
def mcnemar_exact(pairs: Iterable[Tuple[bool, bool]]) -> Dict[str, float]:
    """McNemar 精确检验：配对二分类（同一 beat 上 A、B 是否违规）。

    用于「生成草稿的硬规则违规数」这类成对计数。只关心不一致对
    (A 违规 B 不违规) vs (A 不违规 B 违规)，用二项精确检验。
    """
    b = sum(1 for x, y in pairs if x and not y)
    c = sum(1 for x, y in pairs if not x and y)
    n = b + c
    if n == 0:
        return {'b': 0, 'c': 0, 'p_two_sided': 1.0, 'n_discordant': 0}
    from math import comb
    k = min(b, c)
    # P(X <= min(b,c)) * 2，X~Binom(n, 0.5)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2.0 ** n)
    return {'b': b, 'c': c, 'n_discordant': n,
            'p_two_sided': min(1.0, 2.0 * tail)}


# ===================================================== 嵌套选择（修泄漏）
def nested_select(build_fn: Callable[[List[str]], Any],
                  score_fn: Callable[[Any, List[str]], float],
                  ids: Sequence[str],
                  n_splits: int = 10,
                  random_state: int = 0) -> Dict[str, Any]:
    """嵌套交叉验证：**内层**选模型，**外层**报告性能。

    修的是 ``prune_with_regression`` 的验证集二次使用问题：那里用留出集决定
    「剪不剪枝」，论文又在同一留出集上报困惑度。本函数把「选」和「评」分开。

    参数
    ----
    build_fn : ``group_names -> model``，只用内层训练集构建
    score_fn : ``(model, group_names) -> float``（越低越好），在内层验证集上打分

    返回每折的 ``{'inner_score', 'outer_score'}`` 与汇总均值。
    """
    uniq = sorted(set(group_of(p) for p in ids))
    by_group: Dict[str, List[int]] = defaultdict(list)
    for i, pid in enumerate(ids):
        by_group[group_of(pid)].append(i)
    gkf = GroupKFold(n_splits=n_splits, random_state=random_state)
    # 对 group 层面再切外层
    gof: Dict[str, int] = {}
    for k, pair in enumerate(_group_split(uniq, n_splits, random_state)):
        _tr, te_groups = pair
        for g in te_groups:
            gof[g] = k
    rows = []
    for k in range(n_splits):
        outer_te_groups = [g for g, j in gof.items() if j == k]
        outer_te = [i for g in outer_te_groups for i in by_group[g]]
        outer_tr_groups = [g for g, j in gof.items() if j != k]
        # 内层再切一次
        inner = GroupKFold(n_splits=3, random_state=random_state + k + 1)
        best, best_s = None, float('inf')
        for itr, ite in inner.split([g for g in outer_tr_groups]):
            tr_g = [outer_tr_groups[i] for i in itr]
            te_g = [outer_tr_groups[i] for i in ite]
            cand = build_fn(tr_g)
            s = score_fn(cand, te_g)
            if s < best_s:
                best_s, best = s, tr_g
        model = build_fn(best or outer_tr_groups)
        rows.append({'fold': k, 'inner_score': best_s,
                     'outer_score': score_fn(model, outer_te_groups)})
    mi = sum(r['inner_score'] for r in rows) / len(rows)
    mo = sum(r['outer_score'] for r in rows) / len(rows)
    return {'folds': rows, 'mean_inner': mi, 'mean_outer': mo}


def _group_split(groups: Sequence[str], k: int, random_state: int):
    n = len(groups)
    idx = list(range(n))
    random.Random(random_state).shuffle(idx)
    fold_of = {}
    for rank, j in enumerate(idx):
        fold_of[groups[j]] = rank % k
    for f in range(k):
        te = [g for g in groups if fold_of[g] == f]
        tr = [g for g in groups if fold_of[g] != f]
        yield tr, te


# ================================================================= 汇总
def summarize_folds(fold_values: Sequence[float]) -> Dict[str, float]:
    """跨折汇总：均值 + 标准差 + 标准误 + 95% t 区间。

    论文现在只报「一个划分上的一个数」，跨折后能给出稳定性。
    """
    v = [float(x) for x in fold_values]
    n = len(v)
    if n == 0:
        return {'mean': float('nan'), 'sd': float('nan'), 'se': float('nan'),
                'lo': float('nan'), 'hi': float('nan'), 'n': 0}
    mean = sum(v) / n
    if n == 1:
        return {'mean': mean, 'sd': 0.0, 'se': 0.0, 'lo': mean, 'hi': mean,
                'n': 1}
    var = sum((x - mean) ** 2 for x in v) / (n - 1)
    sd = math.sqrt(var)
    se = sd / math.sqrt(n)
    # t 分布 97.5 分位（n<=30 用表，否则正态近似）
    tcrit = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 7: 2.447,
             8: 2.365, 9: 2.306, 10: 2.262, 12: 2.179, 15: 2.131,
             20: 2.086, 25: 2.060, 30: 2.042}.get(n, 1.96)
    return {'mean': mean, 'sd': sd, 'se': se,
            'lo': mean - tcrit * se, 'hi': mean + tcrit * se, 'n': n}


def _selftest():
    print('=== GroupKFold 分组泄漏检查 ===')
    # 构造 5 个"曲目"，各 3 个调区片段（模拟 symphony 的真实结构）
    ids = ['sym%03d_mvt_a' % k for k in range(1, 6)] + \
          ['sym%03d_mvt_b' % k for k in range(1, 6)] + \
          ['sym%03d_mvt_c' % k for k in range(1, 6)]
    gkf = GroupKFold(n_splits=5, shuffle=True, random_state=0)
    leaks = 0
    for tr, te in gkf.split(ids):
        gtr = {group_of(ids[i]) for i in tr}
        gte = {group_of(ids[i]) for i in te}
        if gtr & gte:
            leaks += 1
    print(f'  5 曲目×3 片段，5 折 → 泄漏折数 = {leaks}（应为 0）')
    print('  group_of 不做前缀归一化:', group_of('humdrum_haydn_sym001a'))

    print()
    print('=== 折规模均衡（真实 symphony 缓存）===')
    import json
    import os
    cache = os.path.join(os.path.dirname(__file__), '..', 'runs',
                         'grammar_cache', 'symphony.json')
    if os.path.exists(cache):
        pieces = json.load(open(cache, encoding='utf-8'))['pieces']
        pids = [p['id'] for p in pieces]
        folds = GroupKFold(n_splits=10, shuffle=True).fold_of(pids)
        from collections import Counter
        c = Counter(folds)
        sizes = [c[i] for i in range(10)]
        print('  640 片段 / 62 曲目 → 各折片段数:', sizes)
        print('  极差 =', max(sizes) - min(sizes), '（贪心均衡预期 0~1）')
        # MD5 对比
        import hashlib
        md5 = [int(hashlib.md5(p.encode()).hexdigest(), 16) % 10 for p in pids]
        cm = Counter(md5)
        ms = [cm[i] for i in range(10)]
        print('  MD5 % 10  → 各折片段数:', ms,
              '极差 =', max(ms) - min(ms))
    else:
        print('  （未找到 symphony 缓存，跳过）')

    print()
    print('=== 配对 bootstrap ===')
    a = [1.0 + 0.1 * (i % 7) for i in range(400)]
    b = [1.2 + 0.1 * (i % 5) for i in range(400)]
    r = bootstrap_paired_ci(a, b, n_boot=800)
    print('  delta=%.4f  95%%CI=[%.4f, %.4f]  p=%.4f'
          % (r['delta'], r['lo'], r['hi'], r['p_two_sided']))
    r0 = bootstrap_paired_ci(a, list(a), n_boot=400)
    print('  自身对比 delta=%.4f（应为 0），p=%.2f（应 >0.05）'
          % (r0['delta'], r0['p_two_sided']))

    print()
    print('=== McNemar 精确检验 ===')
    print(' ', mcnemar_exact([(True, False)] * 12 + [(False, True)] * 2))
    print(' ', mcnemar_exact([(True, False)] * 7 + [(False, True)] * 7))

    print()
    print('=== 跨折汇总 ===')
    print('  ', {k: round(v, 4) if isinstance(v, float) else v
                for k, v in summarize_folds([15.2, 15.9, 14.8, 16.1, 15.5]).items()})


if __name__ == '__main__':
    _selftest()
