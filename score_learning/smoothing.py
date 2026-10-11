# -*- coding: utf-8 -*-
"""n-gram 平滑器家族：Witten-Bell 及其标准替代品。

背景（论文 RQ1 的方法论缺口）
--------------------------------
ESWA 稿 ``main.tex`` §5.1 声明 "The smoother itself is not new"，把 Witten-Bell
列为已确立估计器（Chen & Goodman 1999）。但全文**从未与任何标准替代平滑器
比较过**，只做了「有/无位置条件」的消融。审稿人几乎必然追问：为什么是
Witten-Bell？Kneser-Ney（1990）是语言学 n-gram 的事实标准，Jelinek-Mercer
（1985）是回退法的经典，Absolute Discounting（Ney 1993）是简化基线。

本模块提供可插拔平滑器，接口与 :class:`score_learning.learn_core.BackoffModel`
完全一致（``prob(p2, p1, pos, c)``），因此可直接替换而不动 ``learn.py``。

四种平滑器共用同一个四层回退骨架 L3→L2→L1→L0，区别只在**每层的插值权重**：

======================  ====================================================
平滑器                   每层公式
======================  ====================================================
``wb``  Witten-Bell      (n(h,c) + T(h)·P_low(c)) / (n(h) + T(h))
``jm``  Jelinek-Mercer   λ·n(h,c)/n(h) + (1-λ)·P_low(c)
``ad``  Absolute Disc.   max(n(h,c) - D, 0)/n(h) + λ(h)·P_low(c)
``kn``  Kneser-Ney       continuation counts：分子分母都用 D(c|·) 而非 n(h,·)
``mkn`` Modified KN      标准 KN + 归一化（插值版），M 个候选词上做重归一化
======================  ====================================================

Witten-Bell 的分母 ``n(h)+T(h)`` 隐含地假设「未见词的先验是均匀分布」；
Kneser-Ney 用 continuation count 修正这一点——一个从未在 ``h`` 之后出现过的词
**根本不该**被分配质量。这是两者在稀疏语料上差距的来源。
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

import numpy as np

LEVELS = ('L0', 'L1', 'L2', 'L3')
PRUNE_PROTECTED = ('L0',)   # 这两层不参与剪枝（本身就是回退底座）


def _ctx(level: str, p2, p1, pos):
    return {'L3': (p2, p1, pos), 'L2': (p1, pos),
            'L1': (p1,), 'L0': (pos,)}[level]


class _BaseSmoother:
    """四层回退骨架。子类实现 ``_interp``。"""

    name = 'base'
    #: 是否需要两遍扫描（KN 需要先统计 continuation count）
    needs_continuation = False

    def __init__(self, vocab: List[str], floor: float = 0.0):
        self.vocab = list(vocab)
        self.V = len(self.vocab)
        self.t: Dict[str, Dict[Any, Counter]] = {
            lv: defaultdict(Counter) for lv in LEVELS}
        self.floor = floor
        self._uni: Optional[Counter] = None
        self._cont: Optional[Counter] = None      # KN 的 continuation count
        self._param: Dict[str, float] = {}       # 各层的插值参数记录

    # ------------------------------------------------------------ 训练
    def add(self, p2, p1, pos, c) -> None:
        for lv in LEVELS:
            self.t[lv][_ctx(lv, p2, p1, pos)][c] += 1
        self._uni = None
        if self.needs_continuation:
            # 最高层出现过的 (前两和弦) → 下一个和弦，构成 continuation
            self._cont = None

    def snapshot(self):
        return {lv: {h: Counter(c) for h, c in tab.items()}
                for lv, tab in self.t.items()}

    def restore(self, snap) -> None:
        self.t = {lv: defaultdict(Counter, snap.get(lv, {})) for lv in LEVELS}
        self._uni = None
        self._cont = None

    # ------------------------------------------- KN 的 continuation count
    def _finalize_continuation(self) -> Counter:
        """D(c1|·)：以 c1 为**上下文**出现过的不同历史数。

        经典 KN 定义 D(c1|·) = |{h0 : (h0, c1) ∈ 训练}|。因为 L1 上下文就是
        前一个和弦 p1，所以直接用 L1 表的 key 集合即可。
        """
        if self._cont is None:
            self._cont = Counter()
            for h in self.t['L1']:
                self._cont[h[0]] += 1
        return self._cont

    def _unigram(self) -> Counter:
        if self._uni is None:
            self._uni = Counter()
            for cnt in self.t['L1'].values():
                self._uni.update(cnt)
        return self._uni

    def unigram(self, c) -> float:
        u = self._unigram()
        n, types = sum(u.values()), len(u)
        return (u.get(c, 0) + types / max(self.V, 1)) / max(n + types, 1)

    def attested(self, p1, c) -> bool:
        cnt = self.t['L1'].get((p1,))
        return bool(cnt) and cnt.get(c, 0) > 0

    # ------------------------------------------------------------ 剪枝
    def prune_context(self, levels=('L3', 'L2'), ctx_min: int = 3) -> int:
        removed = 0
        for lv in levels:
            tab = self.t[lv]
            for h in [h for h, c in tab.items() if sum(c.values()) < ctx_min]:
                del tab[h]
                removed += 1
        self._cont = None
        self._uni = None
        return removed

    # ------------------------------------------------------------ 概率
    def _interp(self, lv: str, h, cnt: Counter, p_low: float, c: str) -> float:
        raise NotImplementedError

    def prob(self, p2, p1, pos, c) -> float:
        # 自底向上：L0(仅 pos) → L1(p1) → L2(p1,pos) → L3(p2,p1,pos)
        p = 1.0 / max(self.V, 1)
        for lv in ('L0', 'L1', 'L2', 'L3'):
            cnt = self.t[lv].get(_ctx(lv, p2, p1, pos))
            if not cnt:
                continue
            p = self._interp(lv, _ctx(lv, p2, p1, pos), cnt, p, c)
        if self.floor:
            p = (1 - self.floor) * p + self.floor * self.unigram(c)
        return p


# ====================================================================== WB
class WittenBell(_BaseSmoother):
    """现有生产模型（learn_core.BackoffModel 的等价物），作为对照基准。"""
    name = 'wb'

    def _interp(self, lv, h, cnt, p_low, c):
        n, types = sum(cnt.values()), len(cnt)
        return (cnt.get(c, 0) + types * p_low) / (n + types)


# ====================================================================== JM
class JelinekMercer(_BaseSmoother):
    """Jelinek-Mercer 线性插值回退：λ·n(h,c)/n(h) + (1-λ)·P_low(c)。

    λ 默认 **0.5**，由 10 折 GroupKFold 在巴赫众赞歌上实测选定
    （``score_learning/experiments.py::run_e1g``，产物
    ``runs/experiments/e1g_lambda.json``）：

    ==========  ===============  ================
    λ留出困惑度   major          minor
    ==========  ===============  ================
    0.3          12.64          13.80
    0.4          11.74          12.77
    **0.5**      **11.52**      **12.49**
    0.6          11.82          12.78
    0.7          12.68          13.70
    0.8          14.43          15.63
    0.9          18.55          20.27
    ==========  ===============  ================

    两调式都在 0.5 取最小，且曲线在 0.5 附近很平（0.4/0.6 都在 +2.3% 内），
    故 0.5 是稳健选择而非过拟合尖峰。此前的默认值 **0.7 是错的**，
    比最优差 +10.0%(major) / +9.7%(minor)——它来自"教科书说 0.5~0.9"的
    随手取值，从未被测量过。λ=1 退化为最高层硬切换，λ=0 退化为纯低层。
    """

    name = 'jm'

    #: 实测最优插值权重；出处见类文档与 run_e1g()
    LAM_DEFAULT = 0.5

    def __init__(self, vocab, lam: float = None, floor: float = 0.0):
        super().__init__(vocab, floor=floor)
        self.lam = float(self.LAM_DEFAULT if lam is None else lam)
        self._param = {'lam': self.lam}

    def _interp(self, lv, h, cnt, p_low, c):
        n = sum(cnt.values())
        if n <= 0:
            return p_low
        return self.lam * (cnt.get(c, 0) / n) + (1.0 - self.lam) * p_low


# ====================================================================== AD
class AbsoluteDiscounting(_BaseSmoother):
    """Ney (1993) 绝对折扣：把低计数后继的质量按比例推到低层。

    P(c|h) = max(n(h,c)-D, 0)/n(h) + λ(h)·P_low(c)，其中
    λ(h) = D·N_{1+}(h)/n(h)，N_{1+}(h) 是 n(h,c)>D 的后继数。
    关键性质：**保证概率归一**（不像纯折扣会把总质量削掉）。
    """

    name = 'ad'

    def __init__(self, vocab, discount: float = 0.75, floor: float = 0.0):
        super().__init__(vocab, floor=floor)
        self.D = float(discount)
        self._param = {'D': self.D}

    def _interp(self, lv, h, cnt, p_low, c):
        n = sum(cnt.values())
        if n <= 0:
            return p_low
        big = sum(1 for v in cnt.values() if v > self.D)
        lam = self.D * big / n
        q = max(cnt.get(c, 0) - self.D, 0.0) / n
        # ``D >= max后继计数`` 时big=0 → lam=0 且分子全为 0，整层输出 0，
        # 级联后整表和塌成 0（实测 n(c)=1、D=1.0 时 Σ=0.0）。此时退回经验频率。
        if q + lam <= 0.0:
            return cnt.get(c, 0.0) / n
        return q + lam * p_low


# ====================================================================== KN
class KneserNey(_BaseSmoother):
    """Kneser-Ney（绝对折扣型），带 continuation count。

    P(c|h) = (max(n(h,c)-D,0) + γ(h)·D(c)) / n(h)
    γ(h) = D·N_{1+}(h) / n(h)

    与 AD 的唯一区别是**先验项**用 D(c)（continuation count）而非均匀/低层估计。
    这让「只在前一个和弦后出现过、但从未在该完整上下文后出现」的词获得质量，
    在稀疏语料上通常明显更好。
    """

    name = 'kn'
    needs_continuation = True

    def __init__(self, vocab, discount: float = 0.75, floor: float = 0.0):
        super().__init__(vocab, floor=floor)
        self.D = float(discount)
        self._param = {'D': self.D}

    def _interp(self, lv, h, cnt, p_low, c):
        """Kneser-Ney 绝对折扣，**逐层严格归一**。

        教科书写法 ``P(c|h) = (max(n(c)-D,0) + lam·n·D(c)/Z) / n`` 有两个陷阱，
        都会在真实语料上静默失效（本模块的 ``_selftest`` 与 E1 实验都踩到了）：

        1. **归一化**：``Σ_c P(c|h) = 1`` 只在「所有后继计数都 > D」时成立。
           整数计数下若 D=0.75 而所有 n(c)≥1，该条件恒成立，看似安全；但一旦
           D≥1（常见的绝对折扣取值），``max(n-D,0)`` 就会抹掉n(c)=D 的后继，
           整表和 < 1。四级级联把误差放大，实测 Σ 可达 5.0。
        2. **lam 语义**：标准定义是 ``lam = D·N₁₊(h)/N(h)``（被折扣掉的**绝对**
           质量占比，∈[0,1]）。若误写成「低计数后继的计数占比」，
           在 n(c) 均为 1 的稀疏表上恒为 0，导致 ``lam<=0 → return p_low``
           **每层提前返回**，高计数信息被完全丢弃、模型退化成均匀分布
           （实测 perplexity 158.00 = 词表大小，四个位置档位结果完全相同）。

        正确做法：把高计数部分与 continuation 先验各自归一，再做**凸组合**，
        使 Σ=1 由构造保证，且不依赖 ``lam>0``：
            p_own(c)  = max(n(c)-D, 0) / Σ_x max(n(x)-D, 0)
            p_prior(c)= D(c) / Σ_x D(x)                （回退均匀）
            P(c|h)    = (1-lam)·p_own(c) + lam·p_prior(c)
        """
        n = sum(cnt.values())
        if n <= 0:
            return p_low
        cands = list(cnt)
        cont = self._finalize_continuation()
        V = max(len(self.vocab), 1)
        # lam = 折扣掉的绝对质量占比 = D · N₁₊(h) / N(h)，钳到 [0,1]
        big = sum(1 for v in cnt.values() if v > self.D)
        lam = min(max((self.D * big / n) if n > 0 else 0.0, 0.0), 1.0)
        # ★ 关键设计：把 p_own 与 p_prior 都构造成在**全词表 V 上归一**的分布。
        #   p_own[cands] = max(n(c)-D,0)/Σ，cands 外为 0 → Σ_V = 1
        #   p_prior[V]   = D(c)/Σ_V D(c)             → Σ_V = 1
        # 这样 mix = (1-lam)·p_own + lam·p_prior 天然归一，**不需要任何手工
        # 压缩系数**。（早先在 cands 内归一再手工按比例压缩 candidate 外份额，
        # 结果 Σ=1.15 —— 压缩系数本身是错的。）
        own = {x: max(cnt.get(x, 0) - self.D, 0.0) for x in cands}
        tot = sum(own.values())
        if tot <= 0:
            # 全部后继都被折扣掉（D ≥ max后继计数）→ 退回未折扣的经验频率。
            # 不能返回全零：那会让本层输出恒为 0，级联后整表和塌成 0
            #（实测 n(c)=1、D=1.0 时 Σ=0.0）。
            emp = {x: (cnt.get(x, 0.0) / n) for x in cands}
        else:
            emp = {x: (own[x] / tot if x in own else 0.0) for x in self.vocab}
        p_own = emp
        ctot = sum(cont.get(x, 0.0) for x in self.vocab)
        if lv != 'L0' and ctot > 0:
            p_prior = {x: cont.get(x, 0.0) / ctot for x in self.vocab}
        else:
            p_prior = {x: 1.0 / V for x in self.vocab}
        return (1.0 - lam) * p_own.get(c, 0.0) + lam * p_prior.get(c, 1.0 / V)


# ===================================================================== mKN
class ModifiedKneserNey(_BaseSmoother):
    """插值型（modified / "Interpolated"）Kneser-Ney，M 个候选词重归一化。

    这是 Chen & Goodman (1999) 里实证表现最好的经典变体，也是当前 n-gram
    文献的默认基线。流程：
      1. 对当前层每个候选 c 先算未归一化的 w(c)
      2. 求 Z = Σ w(c)
      3. P(c|h) = w(c)/Z  —— 这一步保证**严格归一**，避免折扣掉的总质量丢失

    与原版 KN 的差别在低计数上：原版把剩余质量按 D(c) 摊到全局，MKN 在
    候选集内重新分配，更适合 ``prune_context`` 之后上下文稀疏的情形
    （这正是 learn.py 剪枝后的实际状态）。
    """

    name = 'mkn'
    needs_continuation = True

    def __init__(self, vocab, discount: float = 0.75, floor: float = 0.0,
                 max_candidates: int = 0):
        super().__init__(vocab, floor=floor)
        self.D = float(discount)
        #: 0 = 用整个词表做候选集（慢但精确）；>0 只取计数最高的前 M 个
        self.M = int(max_candidates)
        self._param = {'D': self.D, 'M': self.M}

    def _candidates(self, cnt: Counter) -> List[str]:
        if self.M <= 0 or len(cnt) <= self.M:
            return list(cnt)
        return [c for c, _ in cnt.most_common(self.M)]

    def _interp(self, lv, h, cnt, p_low, c):
        """MKN：候选集内重归一化的Kneser-Ney（Chen & Goodman 1999 最优变体）。

        与 :class:`KneserNey` 共享同一个归一化正确的凸组合骨架，差别在候选集：
        本类只取计数最高的前 ``M`` 个后继做候选（本项目 M=0 即全取），
        在稀疏上下文（``prune_context`` 之后）上更省算力，且与知识库
        「只保留高频后继」的编辑原则一致。

        注意 ``lam`` 必须用标准定义 ``D·N₁₊/N``，**不能**用「低计数后继计数
        占比」—— 后者在 n(c) 恒为 1 的稀疏表上等于 0，会让每层提前返回
        低层、把高计数信息全部丢弃（详见 KneserNey._interp 的两陷阱说明）。
        """
        n = sum(cnt.values())
        if n <= 0:
            return p_low
        cands = self._candidates(cnt)
        cont = self._finalize_continuation()
        V = max(len(self.vocab), 1)
        big = sum(1 for v in cnt.values() if v > self.D)
        lam = min(max((self.D * big / n) if n > 0 else 0.0, 0.0), 1.0)
        # 与 KneserNey 同一骨架：两个分量都在**全词表 V 上归一**，凸组合后
        # 天然 Σ=1，不需要手工压缩（见 KneserNey._interp 的说明）。
        own = {x: max(cnt.get(x, 0) - self.D, 0.0) for x in cands}
        tot = sum(own.values())
        if tot <= 0:
            # D ≥ max后继计数 → 折扣抹掉全部计数，退回经验频率（同 KneserNey）
            p_own = {x: (cnt.get(x, 0.0) / n if x in cnt else 0.0)
                     for x in self.vocab}
        else:
            p_own = {x: (own[x] / tot if x in own else 0.0) for x in self.vocab}
        ctot = sum(cont.get(x, 0.0) for x in self.vocab)
        if lv != 'L0' and ctot > 0:
            p_prior = {x: cont.get(x, 0.0) / ctot for x in self.vocab}
        else:
            p_prior = {x: 1.0 / V for x in self.vocab}
        return (1.0 - lam) * p_own.get(c, 0.0) + lam * p_prior.get(c, 1.0 / V)


# ==================================================================== HMM
class HarmonicHMM(_BaseSmoother):
    """隐马尔可夫基线（Hild 1991 / Allan & Williams 2005 的标准做法）。

    这是论文 §2.2 定位为 "baseline family" 的一员，也是 ``pos_t`` 手工设定
    所替代的那个**学出来的**隐状态。实现 Baum-Welch EM（前向-后向），
    无第三方依赖，与 n-gram 用**同一份事件流、同一词表、同一指标**，
    因此 Table 2 的比较只隔离了模型族这一个变量。

    隐状态设计
    ----------
    纯 HMM 把「乐句位置」吸收进状态序列。这里用**位置条件 HMM**：
    每个 ``pos`` 类各有一套发射/转移参数，但状态空间共享。
    这样做的理由是：若用纯 HMM，则它在 ``cad0``（终止式前一拍）上的
    预测必然不如 n-gram，因为 n-gram 直接拿到了 ``pos`` 而 HMM 只能靠
    状态间接推断—— 那不是公平比较。两个模型拿到的**信息相同**
    （都有 pos），差别只在**是否共享统计**。

    与 :class:`_BaseSmoother` 的接口差异：``prob`` 需要先跑完 EM
    （:meth:`finalize`），因为参数是全局的而非按上下文查表。
    :func:`fit` 已代为调用，故对实验脚本透明。
    """

    name = 'hmm'
    needs_continuation = False

    def __init__(self, vocab, n_states: int = 4, iters: int = 30,
                 seed: int = 0, floor: float = 0.0, tie_pos: bool = True,
                 emit_smooth: float = 1e-4):
        super().__init__(vocab, floor=floor)
        self.K = max(2, int(n_states))
        self.iters = int(iters)
        self.seed = int(seed)
        #: 发射平滑。**HMM 没有这个就会退化成确定性分配**：EM 把每个状态
        #: 的责任集中到少数和弦上，其余发射概率恰为 0（实测每状态只覆盖
        #: 24~59 个和弦，其余为 0），于是 log 0 使后验变成 one-hot，任何
        #: 训练中未见的事件都得到 ~0 概率，留出困惑度爆到 1e11。
        #: 加floor 后发射恒正，log 域前向后向才稳定。
        self.emit_smooth = float(emit_smooth)
        #: True 时为「位置条件 HMM」（每个 pos 一套参数，状态空间共享）；
        #: False 时为纯 HMM（只有一套参数，需自行学出位置效应）
        self.tie_pos = bool(tie_pos)
        self._events: List[Tuple] = []
        self._seg_open = True          # 首个事件前需开一段
        self._emitted = False
        self._emit: Dict[str, Any] = None      # (pos|*) -> [K][V] log-prob
        self._trans: Dict[str, Any] = None     # (pos|*) -> [K][K] log-prob
        self._init: Dict[str, Any] = None      # (pos|*) -> [K]
        self._param = {'K': self.K, 'tie_pos': self.tie_pos}

    # ------------------------------------------------------------ 事件累积
    def add(self, p2, p1, pos, c) -> None:
        """HMM 不做逐上下文查表，改为累积**有序**事件序列。

        顺序是 HMM 的关键：n-gram 可以打乱事件，HMM 不行。因此这里
        记录到达顺序，:meth:`finalize` 时按段切分。
        """
        self._events.append((pos, c))
        self._uni = None

    def new_segment(self) -> None:
        """显式开启新的一段（乐句/ 曲子）。

        **必须**在 :meth:`add` 之前调用。若不调用，全部训练事件会被
        当作**一条**长序列，EM 学到跨曲子的虚假转移，而评测按乐句分段，
        训练与推理结构不一致（实测困惑度 187，而正确分段时 24）。
        """
        self._seg_open = True

    @property
    def _idx(self) -> Dict[str, int]:
        if not hasattr(self, '_idx_cache'):
            self._idx_cache = {c: i for i, c in enumerate(self.vocab)}
        return self._idx_cache

    def _segments(self) -> List[List[Tuple[str, int]]]:
        """把累积的事件流切成独立段。

        切分依据两个信号：显式的 :meth:`new_segment` 标记，以及 ``'BOS'``
        （与 ``harmony_events`` 的语义一致）。和弦已编码为词表下标，
        落在词表外的和弦被丢弃。
        """
        idx = self._idx
        segs, cur = [], []
        for pos, c in self._events:
            if c == 'BOS' or self._seg_open:
                if len(cur) >= 2:
                    segs.append(cur)
                cur = []
                self._seg_open = False
                if c == 'BOS' or c not in idx:
                    continue
            if c not in idx:
                if len(cur) >= 2:
                    segs.append(cur)
                cur = []
                continue
            cur.append((pos, idx[c]))
        if len(cur) >= 2:
            segs.append(cur)
        return segs

    # ------------------------------------------------------------ EM
    def finalize(self) -> None:
        """跑 Baum-Welch EM。幂等。"""
        if self._emitted:
            return
        segs = self._segments()
        V = self.V
        rng = np.random.default_rng(self.seed)
        # 参数键：tie_pos 时按 pos 分桶，否则统一用 '*'
        def key(pos):
            return pos if self.tie_pos else '*'
        keys = sorted({key(p) for seg in segs for p, _ in seg}) or ['*']
        # 初始化（可复现）
        lam0 = self.emit_smooth
        # ★ seed 必须随 K 变化：``default_rng(seed).dirichlet(a, size=K)`` 的
        # 前 4 行与 K 无关（同一 seed 的前 K 个样本），于是 K=4 与 K=16 得到
        # **相同**的初始矩阵，EM 收敛到同一个局部最优、K 只影响复制的状态数
        # （实测 K=4 与 K=16 的发射矩阵逐位相同、困惑度完全一致）。
        rng2 = np.random.default_rng(self.seed + 7919 * self.K)
        emit = {k: (1.0 - lam0) * rng2.dirichlet(np.ones(V) / V, size=self.K)
                   + lam0 / V for k in keys}
        rng = np.random.default_rng(self.seed + 104729 * self.K)
        trans = {k: rng.dirichlet(np.ones(self.K) / self.K, size=self.K)
                 for k in keys}
        init = {k: rng.dirichlet(np.ones(self.K) / self.K) for k in keys}

        def logsumexp(a, axis=None):
            a = np.asarray(a, dtype=float)
            m = np.max(a, axis=axis, keepdims=(axis is not None))
            out = m + np.log(np.sum(np.exp(a - m), axis=axis,
                                    keepdims=(axis is not None)))
            out = np.squeeze(out, axis=axis) if axis is not None else float(out)
            return out

        for _ in range(self.iters):
            num_e = {k: np.zeros((self.K, V)) for k in keys}
            den_e = {k: np.zeros(self.K) for k in keys}
            num_t = {k: np.zeros((self.K, self.K)) for k in keys}
            den_t = {k: np.zeros(self.K) for k in keys}
            num_i = {k: np.zeros(self.K) for k in keys}
            for seg in segs:
                k = key(seg[0][0])
                if k not in emit:
                    k = keys[0]
                obs = [c for _, c in seg]
                n = len(obs)
                if n == 0:
                    continue
                # 前向（log 域，缩放）
                lg = np.full((n, self.K), -np.inf)
                lg[0] = np.log(np.maximum(init[k], 1e-300))
                # 首个观测的发射用**它自己的** pos 桶
                b0 = key(seg[0][0])
                lg[0] = lg[0] + np.log(np.maximum(
                    emit[b0 if b0 in emit else k][:, obs[0]], 1e-300))
                for t in range(1, n):
                    bb = key(seg[t][0])
                    e = emit[bb if bb in emit else k]
                    tr = trans[k] if self.tie_pos else trans[bb if bb in trans else k]
                    prev = lg[t - 1][:, None] + np.log(np.maximum(tr, 1e-300))
                    lg[t] = logsumexp(prev, axis=0) + np.log(
                        np.maximum(e[:, obs[t]], 1e-300))
                # 后向
                lb = np.full((n, self.K), -np.inf)
                lb[n - 1] = 0.0
                for t in range(n - 2, -1, -1):
                    bb = key(seg[t][0])
                    e = emit[bb if bb in emit else k]
                    nxt = key(seg[t + 1][0])
                    tr = trans[k if self.tie_pos else nxt]
                    nxt_e = emit[nxt if nxt in emit else k][:, obs[t + 1]]
                    lb[t] = logsumexp(
                        np.log(np.maximum(tr, 1e-300)) + lb[t + 1][None, :]
                        + np.log(np.maximum(nxt_e, 1e-300))[None, :], axis=1)
                # 后向归一化
                g = lg + lb
                mx = np.max(g, axis=1, keepdims=True)
                g = g - (mx + np.log(np.sum(np.exp(g - mx), axis=1, keepdims=True)))
                gam = np.exp(g)                      # [n, K]
                gsum = gam.sum(axis=0)                # 状态占据
                # 累积
                for t, (pos_t, c_t) in enumerate(seg):
                    kk = key(pos_t)
                    if kk not in emit:
                        kk = k
                    num_e[kk][:, c_t] += gam[t]
                    den_e[kk] += gam[t]
                num_i[k] += gam[0]
                for t in range(n - 1):
                    kk = key(seg[t][0])
                    if kk not in emit:
                        kk = k
                    xi = gam[t][:, None] * np.log(np.maximum(trans[kk], 1e-300)) \
                        + lb[t + 1][None, :] \
                        + np.log(np.maximum(
                            emit[key(seg[t + 1][0]) if key(seg[t + 1][0]) in emit
                            else k][:, obs[t + 1]], 1e-300))[None, :]
                    xi = np.exp(xi - logsumexp(xi))
                    num_t[kk] += xi
                    den_t[kk] += gam[t]
            # M 步
            changed = 0.0
            lam = self.emit_smooth
            for k in keys:
                if den_e[k].sum() > 1e-9:
                    ne = num_e[k] / den_e[k][:, None]
                    # 发射平滑：与均匀分布混合，保证严格正
                    ne = (1.0 - lam) * ne + lam / V
                    changed = max(changed, float(np.abs(ne - emit[k]).max()))
                    emit[k] = ne
                if den_t[k].sum() > 1e-9:
                    nt = num_t[k] / den_t[k][:, None]
                    changed = max(changed, float(np.abs(nt - trans[k]).max()))
                    trans[k] = nt
                if num_i[k].sum() > 1e-9:
                    init[k] = num_i[k] / num_i[k].sum()
            if changed < 1e-6:
                break
        self._emit, self._trans, self._init = emit, trans, init
        self._emitted = True

    # ------------------------------------------------------------ 预测
    def _key(self, pos):
        if not self.tie_pos:
            return '*'
        if pos in self._emit:
            return pos
        return '*' if '*' in self._emit else next(iter(self._emit))

    def predict(self, events: List[Tuple], i: int) -> Dict[str, float]:
        """给定前缀 ``o_0..o_i``，返回**下一和弦**的后验分布 ``P(c | o_0..o_i)``。

        标准 HMM 用法：先求位置 ``i`` 的（缩放）前向-后向后验
        ``gamma_t(k) ∝ alpha_t(k)·beta_t(k)``，再对每个候选和弦求
        ``P(c) = Σ_k gamma_t(k)·B_k(c)``。

        全程log 域并逐步减 logsumexp 缩放：概率域的缩放前向后向在长乐句
        （约 20--24 拍）上会连续下溢到0（实测困惑度爆到 1e11）。

        返回值以**和弦符号**为键、对全词表归一（Σ=1），故
        :meth:`per_event_loss` 取 ``P(o_i | o_0..o_{i-1})`` 即
        ``predict(seg, i-1)[o_i]``。注意必须取**前一个位置**的后验：
        ``o_i`` 自己已在条件里，用 ``predict(seg, i)`` 会泄露目标。
        """
        self.finalize()
        n = len(events)
        if n == 0:
            return {}
        k = self._key(events[n - 1][0])

        def lse(a, axis=None):
            a = np.asarray(a, dtype=float)
            m = np.max(a, axis=axis, keepdims=(axis is not None))
            m = np.where(np.isfinite(m), m, 0.0)
            out = m + np.log(np.sum(np.exp(a - m), axis=axis,
                                    keepdims=(axis is not None)))
            return np.squeeze(out, axis=axis) if axis is not None else float(out)

        def log_emis(t):
            kk = self._key(events[t][0])
            return np.log(np.maximum(self._emit[kk][:, events[t][1]], 1e-300))

        def log_trans(t):
            """t-1 -> t 的转移（位置条件 HMM 用 t-1 的 pos 桶）。"""
            kk = self._key(events[t - 1][0])
            m = self._trans[k] if self.tie_pos else self._trans[kk]
            return np.log(np.maximum(m, 1e-300))

        # 前向（每步减 lse 缩放）
        fwd = np.zeros((n, self.K))
        la = np.log(np.maximum(self._init[k], 1e-300))
        fwd[0] = la - lse(la)
        for t in range(1, n):
            cur = lse(fwd[t - 1][:, None] + log_trans(t), axis=0) + log_emis(t)
            fwd[t] = cur - lse(cur)
        # 后向
        bwd = np.zeros((n, self.K))
        bwd[n - 1] = 0.0
        for t in range(n - 2, -1, -1):
            cur = lse(log_trans(t + 1) + (log_emis(t + 1) + bwd[t + 1])[None, :],
                      axis=1)
            bwd[t] = cur - lse(cur)
        g = np.atleast_1d(fwd[n - 1] + bwd[n - 1])
        g = g - lse(g)                        # gamma_{n-1}(k)，已归一
        # g 形状 (K,)、emit 形状 (K, V)：需 g[:, None] 把状态维提到前面
        # 才能按 (K,) × (K, V) 广播（numpy 要求末维对齐）。
        # 早先误写成 g[None, :]（得到 (1,K)）与 g（(K,)）都 broadcast 失败。
        lp = g[:, None] + np.log(np.maximum(self._emit[k], 1e-300))  # [K, V]
        lp = lp - lse(lp, axis=1)[:, None]     # 每个状态对全词表归一
        p = np.exp(lp).sum(axis=0)            # Σ_k gamma_k·B_k(c)，对 V 归一
        s = p.sum()
        if s <= 0:
            return {c: 1.0 / self.V for c in self.vocab}
        p = p / s
        return {c: float(p[i]) for c, i in self._idx.items()}

    def per_event_loss(self, events: List[Tuple],
                       segments: Optional[List[List[Tuple]]] = None) -> List[float]:
        """逐事件 NLL，按乐句分段用完整后验计算（见 :meth:`predict`）。

        ``segments`` **必须**由调用方给出，每段是一条独立乐句的
        ``[(pos, chord), ...]``。原因：HMM 的后向递推沿序列展开，若把
        整个留出集拼成一段（实测 1062 步），后验概率指数衰减到 1e-107，
        困惑度爆到 1e11；而 n-gram 逐事件查表不受影响，故这个坑只打HMM。
        评测方（:mod:`score_learning.experiments`）按 piece/乐句构造分段。

        接受标准 4 元组 ``(p2, p1, pos, c)``（与其他平滑器一致），
        也接受 2 元组 ``(pos, c)``。返回值顺序与
        :func:`smoothing.per_event_loss` 一致（跳过 BOS 与词表外和弦）。
        """
        self.finalize()
        idx = self._idx
        flow = ([(e[2], e[3]) for e in events] if events
                and len(events[0]) >= 4 else [(e[0], e[1]) for e in events])
        # 编码 + 过滤
        enc = [(pos, idx[c]) for pos, c in flow if c != 'BOS' and c in idx]
        if segments is None:
            # 无分段信息时，退化为按 BOS 切；若无 BOS 则整段——调用方应避免
            segs, cur = [], []
            for pos, c in flow:
                if c == 'BOS' or c not in idx:
                    if cur:
                        segs.append(cur)
                    cur = []
                    continue
                cur.append((pos, idx[c]))
            if cur:
                segs.append(cur)
        else:
            segs = []
            for sg in segments:
                ss = ([(e[2], e[3]) for e in sg] if sg and len(sg[0]) >= 4
                      else [(e[0], e[1]) for e in sg])
                cur = [(pos, idx[c]) for pos, c in ss
                       if c != 'BOS' and c in idx]
                if cur:
                    segs.append(cur)
            if not segs:
                segs = [enc] if enc else []
        idx = self._idx
        inv = {i: c for c, i in idx.items()}
        out = []
        for seg in segs:
            for t in range(len(seg)):
                # P(o_t | o_0..o_{t-1})：用**前一个位置**的后验。
                # t=0 时无前缀，退回初始分布 × 发射。
                if t == 0:
                    k = self._key(seg[0][0])
                    ci = seg[0][1]
                    pr = float(np.dot(self._init[k], self._emit[k][:, ci]))
                else:
                    # ★ 必须传**前缀** ``seg[:t]``。``predict`` 的语义是
                    # "给定 o_0..o_i 返回下一和弦的后验"，它用 ``len(events)``
                    # 定位，因此调用方必须把列表截到前缀长度。早先传了整段
                    # ``seg``，于是每个 t 都拿到**段末**的后验
                    # （且该后验的前向还包含了 o_{n-1} 自身），逐事件损失
                    # 全部错位——实测留出困惑度从 ~23 虚高到 ~76。
                    pre = self.predict(seg[:t], t - 1)
                    pr = pre.get(inv.get(seg[t][1], ''), 0.0)
                out.append(-math.log(max(pr, 1e-12)))
        return out

    def _per_event_loss_legacy(self, events: List[Tuple]) -> List[float]:
        """逐事件 NLL，按乐句分段用完整后验计算（见 :meth:`predict`）。

        接受标准 4 元组 ``(p2, p1, pos, c)``（与其他平滑器一致），
        也接受 2 元组 ``(pos, c)``。返回值顺序与
        :func:`smoothing.per_event_loss` 一致（跳过 BOS 与词表外和弦），
        可直接喂给 bootstrap。
        """
        self.finalize()
        idx = self._idx
        # 统一成 (pos, chord) 流
        if events and len(events[0]) >= 4:
            flow = [(e[2], e[3]) for e in events]
        else:
            flow = [(e[0], e[1]) for e in events]
        segs, cur = [], []
        for pos, c in flow:
            if c == 'BOS' or c not in idx:
                if cur:
                    segs.append(cur)
                cur = []
                continue
            cur.append((pos, idx[c]))
        if cur:
            segs.append(cur)
        out = []
        for seg in segs:
            for t in range(len(seg)):
                post = self.predict(seg, t)
                p = max(post.get(seg[t][1], 1e-12), 1e-12)
                out.append(-math.log(p))
        return out

    def prob(self, p2, p1, pos, c) -> float:
        """P(c | pos) —— 逐事件接口下的**近似**边缘，仅供接口兼容。

        它用初始分布近似状态边缘，对乐句开头尚可，对中段会高估不确定性。
        评测请用 :meth:`per_event_loss`（完整前向-后向后验）。保留本方法
        是因为 :class:`_BaseSmoother` 的统一接口需要它，且
        :func:`smoothing.perplexity` 之类旧调用仍可用。
        """
        self.finalize()
        if c == 'BOS' or c not in self._idx:
            return 1.0 / max(self.V, 1)
        k = self._key(pos)
        col = self._emit[k][:, self._idx[c]]
        a = self._init[k]
        p = max(float(np.dot(a, col)), 1e-12)
        if self.floor:
            p = (1 - self.floor) * p + self.floor * self.unigram(c)
        return p

    @property
    def _idx(self):
        if not hasattr(self, '_idx_cache'):
            self._idx_cache = {c: i for i, c in enumerate(self.vocab)}
        return self._idx_cache

    def unigram(self, c) -> float:
        """HMM 的unigram 直接用发射矩阵的加权平均。"""
        self.finalize()
        i = self._idx.get(c)
        if i is None:
            return 1.0 / max(self.V, 1)
        k = self._key('*' if '*' in self._emit else next(iter(self._emit)))
        a = self._init[k]
        return float(max(a @ self._emit[k][:, i], 1e-12))

    def attested(self, p1, c) -> bool:
        self.finalize()
        i = self._idx.get(c)
        if i is None:
            return False
        for kk in self._emit:
            if self._emit[kk][:, i].max() > 0:
                return True
        return False

    def prune_context(self, levels=('L3', 'L2'), ctx_min: int = 3) -> int:
        """HMM 无逐上下文表可剪；为接口一致返回 0。"""
        return 0

    def _interp(self, lv, h, cnt, p_low, c):
        return p_low


# ================================================================= 工厂
SMOOTHERS = {
    'wb': WittenBell,
    'jm': JelinekMercer,
    'ad': AbsoluteDiscounting,
    'kn': KneserNey,
    'mkn': ModifiedKneserNey,
    'hmm': HarmonicHMM,
}


def make_smoother(kind: str, vocab: List[str], **kw) -> _BaseSmoother:
    key = (kind or 'wb').lower()
    if key not in SMOOTHERS:
        raise ValueError(f'未知平滑器 {kind!r}；可选 {sorted(SMOOTHERS)}')
    cls = SMOOTHERS[key]
    valid = set(cls.__init__.__code__.co_varnames) - {'self'}
    # 未知参数**报错**而非静默丢弃：静默丢弃曾让 HMM 的 ``K=32`` 被丢掉
    # 而退回默认 4 态，表现为「K 从 2 到 32 困惑度完全相同」。
    unknown = sorted(set(kw) - valid)
    if unknown:
        raise TypeError(
            f'{key} 不接受参数 {unknown}；可用参数 {sorted(valid)}')
    return cls(list(vocab), **kw)


def fit_hmm(segments, vocab, **kw) -> 'HarmonicHMM':
    """在**分段**事件流上拟合 HMM。

    ``segments`` 是 ``[[(p2,p1,pos,c), ...], ...]``，每项是一条独立的
    乐句/曲子。这是HMM 训练的正确入口：把多首曲子拼成一条序列会让 EM
    学到跨曲子的虚假转移，与按乐句推理的结构不一致（实测 187 vs 24）。
    """
    m = make_smoother('hmm', vocab, **kw)
    for sg in segments:
        m.new_segment()
        for e in sg:
            m.add(*e)
    m.finalize()
    return m


def fit(kind: str, events, vocab, **kw) -> _BaseSmoother:
    """在 (p2, p1, pos, c) 事件流上拟合。

    HMM 需要跑完EM（:meth:`HarmonicHMM.finalize`）才能预测；
    其余平滑器只需把事件累积进查表，故调 ``_finalize_continuation``。
    两者都在这里收口，对实验脚本透明。
    """
    m = make_smoother(kind, vocab, **kw)
    for e in events:
        m.add(*e)
    if isinstance(m, HarmonicHMM):
        m.finalize()
    else:
        m._finalize_continuation()
    return m


def perplexity(model: _BaseSmoother, events) -> float:
    tot, n = 0.0, 0
    for p2, p1, pos, c in events:
        tot += -math.log(max(model.prob(p2, p1, pos, c), 1e-12))
        n += 1
    return round(math.exp(tot / max(n, 1)), 4)


def per_event_loss(model: _BaseSmoother, events) -> List[float]:
    """逐事件负对数似然——bootstrap 统计必须用它，不能只用聚合困惑度。"""
    return [-math.log(max(model.prob(e[0], e[1], e[2], e[3]), 1e-12))
            for e in events]


def _selftest():
    """概率归一性自检：所有平滑器在若干上下文上对整个词表的概率和应为 1。"""
    vocab = ['a', 'b', 'c', 'd', 'e']
    train = [('^', '^', 's', 'a'), ('^', 'a', 'mid', 'b'),
             ('a', 'b', 'mid', 'c'), ('b', 'c', 'cad1', 'a'),
             ('c', 'a', 'mid', 'b'), ('a', '^', 's', 'c')]
    probes = [('^', '^', 's'), ('a', 'b', 'mid'), ('b', 'c', 'cad1'),
              ('x', 'y', 'zzz')]
    print(f'{"smoother":10} ' + ' '.join(f'{"ctx" + str(i):>12}'
                                          for i in range(len(probes))))
    for kind in SMOOTHERS:
        m = fit(kind, train, vocab)
        row = []
        for p2, p1, pos in probes:
            z = sum(m.prob(p2, p1, pos, c) for c in vocab)
            row.append(f'{z:12.6f}')
        worst = max(abs(sum(m.prob(a, b, p, c) for c in vocab) - 1.0)
                    for a, b, p in probes)
        flag = '' if worst < 1e-6 else '  ← 未归一!'
        print(f'{m.name:10} ' + ' '.join(row) + flag)


if __name__ == '__main__':
    _selftest()
