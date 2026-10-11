# -*- coding: utf-8 -*-
"""两套 grammar 学习流水线的共享核心（chorale + leadsheet）。

择优统一：
- Witten-Bell 回退模型（BackoffModel）
- 上下文级剪枝（只删样本不足的整条上下文，保留低频后继）
- 剪枝回归：留出困惑度变差则自动回退到未剪枝模型
- k-means++ 多次重启 + 惯性最小选取
- heldout 切分、分布序列化、轮廓重采样与命名
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

CTX_MIN = 3


def is_heldout(pid: str, fold: int = 10) -> bool:
    return int(hashlib.md5(pid.encode()).hexdigest(), 16) % fold == 0


class BackoffModel:
    """Witten-Bell 回退：L3 (p2,p1,pos) → L2 (p1,pos) → L1 (p1) → L0 (pos) → 均匀。

    floor > 0 时再与整体频率线性插值：自动识别的和声长尾噪声多，WB 给未见转移留的质量不够。
    """

    def __init__(self, vocab: List[str], floor: float = 0.0):
        self.vocab = list(vocab)
        self.V = len(self.vocab)
        self.t = {k: defaultdict(Counter) for k in ('L3', 'L2', 'L1', 'L0')}
        self.floor = floor
        self._uni: Optional[Counter] = None

    @staticmethod
    def ctx(level, p2, p1, pos):
        return {'L3': (p2, p1, pos), 'L2': (p1, pos), 'L1': (p1,), 'L0': (pos,)}[level]

    def add(self, p2, p1, pos, c):
        for lv in self.t:
            self.t[lv][self.ctx(lv, p2, p1, pos)][c] += 1
        self._uni = None

    def snapshot(self) -> Dict[str, Dict[Any, Counter]]:
        return {lv: {h: Counter(c) for h, c in tab.items()} for lv, tab in self.t.items()}

    def restore(self, snap: Dict[str, Dict[Any, Counter]]) -> None:
        self.t = {lv: defaultdict(Counter, snap.get(lv, {})) for lv in ('L3', 'L2', 'L1', 'L0')}
        self._uni = None

    def prune_context(self, levels: Tuple[str, ...] = ('L3', 'L2'),
                      ctx_min: int = CTX_MIN) -> int:
        """只删样本不足的整条上下文；保留低频后继（保护 Witten-Bell 的 types 分母）。"""
        removed = 0
        for lv in levels:
            tab = self.t[lv]
            for h in [h for h, c in tab.items() if sum(c.values()) < ctx_min]:
                del tab[h]
                removed += 1
        return removed

    def prune(self, min_count: Dict[str, int], ctx_min: int = CTX_MIN):
        """兼容旧接口：先按后继频次剪，再按上下文总量剪。

        新代码请优先用 prune_context（效果更好）。保留本方法供显式消融对照。
        """
        for lv, tab in self.t.items():
            mc = min_count.get(lv, 1)
            for h in list(tab):
                cnt = tab[h]
                for c in [c for c, n in cnt.items() if n < mc]:
                    del cnt[c]
                if sum(cnt.values()) < (ctx_min if lv != 'L0' else 1):
                    del tab[h]

    def prob(self, p2, p1, pos, c) -> float:
        p = 1.0 / max(self.V, 1)
        for lv in ('L0', 'L1', 'L2', 'L3'):
            cnt = self.t[lv].get(self.ctx(lv, p2, p1, pos))
            if not cnt:
                continue
            n = sum(cnt.values())
            types = len(cnt)
            p = (cnt.get(c, 0) + types * p) / (n + types)
        if self.floor:
            p = (1 - self.floor) * p + self.floor * self.unigram(c)
        return p

    def unigram(self, c) -> float:
        if self._uni is None:
            self._uni = Counter()
            for cnt in self.t['L1'].values():
                self._uni.update(cnt)
        n, types = sum(self._uni.values()), len(self._uni)
        return (self._uni.get(c, 0) + types / max(self.V, 1)) / max(n + types, 1)

    def attested(self, p1, c) -> bool:
        cnt = self.t['L1'].get((p1,))
        return bool(cnt) and cnt.get(c, 0) > 0


def logloss(model: BackoffModel, events) -> Tuple[float, int]:
    tot, n = 0.0, 0
    for p2, p1, pos, c in events:
        tot += -math.log(max(model.prob(p2, p1, pos, c), 1e-12))
        n += 1
    return tot, n


def perplexity(model: BackoffModel, events) -> float:
    tot, n = logloss(model, events)
    return round(math.exp(tot / max(n, 1)), 2)


def prune_with_regression(model: BackoffModel, held_events,
                          levels: Tuple[str, ...] = ('L3', 'L2'),
                          ctx_min: int = CTX_MIN) -> Dict[str, Any]:
    """上下文剪枝；若留出困惑度变差则恢复未剪枝模型。

    返回 {'pruned': bool, 'perplexity_unpruned': float|None,
          'perplexity_script': float|None, 'removed': int}
    """
    if not held_events:
        removed = model.prune_context(levels=levels, ctx_min=ctx_min)
        return {'pruned': True, 'perplexity_unpruned': None,
                'perplexity_script': None, 'removed': removed}
    unpruned = perplexity(model, held_events)
    snap = model.snapshot()
    removed = model.prune_context(levels=levels, ctx_min=ctx_min)
    script = perplexity(model, held_events)
    kept = True
    if script > unpruned + 1e-9:
        model.restore(snap)
        script = unpruned
        kept = False
        removed = 0
    return {
        'pruned': kept,
        'perplexity_unpruned': unpruned,
        'perplexity_script': script,
        'removed': removed,
    }


def kmeans(X: np.ndarray, k: int, seed: int = 7, iters: int = 60,
           n_init: int = 10):
    """k-means++，多次重启取惯性最小解。"""
    if X is None or len(X) == 0:
        return np.zeros((max(k, 1), 1)), np.zeros(0, dtype=int)
    k = max(1, min(int(k), len(X)))
    best = None
    for init in range(max(1, int(n_init))):
        rng = np.random.default_rng(seed + init * 9973)
        C = [X[rng.integers(len(X))]]
        for _ in range(1, k):
            d2 = np.min(((X[:, None, :] - np.array(C)[None]) ** 2).sum(-1), axis=1)
            s = float(d2.sum())
            if s <= 0:
                C.append(X[rng.integers(len(X))])
            else:
                C.append(X[rng.choice(len(X), p=d2 / s)])
        C = np.array(C, dtype=float)
        lab = np.zeros(len(X), dtype=int)
        for _ in range(iters):
            lab = np.argmin(((X[:, None, :] - C[None]) ** 2).sum(-1), axis=1)
            newC = np.array([
                X[lab == j].mean(0) if np.any(lab == j) else C[j] for j in range(k)
            ])
            if np.allclose(newC, C):
                break
            C = newC
        lab = np.argmin(((X[:, None, :] - C[None]) ** 2).sum(-1), axis=1)
        inertia = float(((X - C[lab]) ** 2).sum())
        if best is None or inertia < best[0]:
            best = (inertia, C, lab)
    return best[1], best[2]


def contour_name(c: np.ndarray) -> str:
    peak, low = int(np.argmax(c)), int(np.argmin(c))
    n = len(c)
    rise = c[-1] - c[0]
    where = lambda i: '前' if i < n / 3 else '中' if i < 2 * n / 3 else '后'  # noqa: E731
    span = f'，幅度 {c.max() - c.min():.0f} 半音'
    if 0 < peak < n - 1 and c[peak] - max(c[0], c[-1]) > 1.2:
        return f'拱形（高点在{where(peak)}段{"，落低" if rise < -2 else ""}{span}）'
    if 0 < low < n - 1 and min(c[0], c[-1]) - c[low] > 1.2:
        return f'谷形（低点在{where(low)}段{span}）'
    if rise < -2:
        return f'下行{span}'
    if rise > 2:
        return f'上行{span}'
    return f'平稳波形{span}'


def resample(vals: List[float], n: int = 8) -> List[float]:
    if len(vals) == 1:
        return [vals[0]] * n
    xs = np.linspace(0, len(vals) - 1, n)
    return list(np.interp(xs, np.arange(len(vals)), vals))


def dist(counter: Counter, top: Optional[int] = None, min_n: int = 1) -> List[List]:
    tot = sum(counter.values()) or 1
    items = [(k, v) for k, v in counter.most_common(top) if v >= min_n]
    return [[k, round(v / tot, 4), v] for k, v in items]


# 兼容旧名称（grammar/leadsheet 曾用下划线前缀）
_contour_name = contour_name
_resample = resample
_dist = dist
_logloss = logloss
_ppl = perplexity
