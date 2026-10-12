# -*- coding: utf-8 -*-
"""只读规则脚本的确定性众赞歌作曲器。

流程（每一步都只用 grammar.yaml 里的规则，并把引用的规则编号写进追溯）：

1. 全曲层：乐句数 → 句终规划（F 规则马尔可夫链，末句落主和弦）→ 是否 Barform 反复
2. 乐句层：每句抽一个终止公式（C 规则）与旋律轮廓模板（M 规则）
3. 拍层：和声 + 女高音联合动态规划（前向滤波–后向采样 FFBS 出候选，再用含前两拍
   上下文的完整得分排序）；转移只允许语料里出现过的和弦二元组（R10），句末三拍
   锁定在终止公式上（R11）
4. 声部层：低音、内声部各自做 Viterbi（硬规则 R01–R09 作为不可行约束，学到的
   音程 / 重复音 / 间距分布作为代价），最后按学到的概率加八分经过音 / 辅助音
5. 成品检查：硬规则违例必须为 0，否则换下一个种子分支重来

同一 (脚本, 调, 种子, 参数) 恒得同一首曲子。
"""
from __future__ import annotations

import hashlib
import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .check import (PAIRS, bad_melodic, check_satb, hidden_perfect_outer, overlap,
                    parallel_perfect, secondary_lt)
from .learn import BackoffModel, pos_class, _resample
from .theory import (QUALITIES, SEVENTHS, chord_members, chord_pcs, chord_tone_names,
                     name_with_octave, parse_chord_key, scale_names, scale_of,
                     tonic_from_sharps, tonic_letter_pc, KEY_SHARPS_MAJOR, KEY_SHARPS_MINOR)

#: 硬约束的「不可行」代价。搜索里所有违反 R01--R09 的转移都被置为该值，
#: 因此**任何被接受的草稿都必然零硬规则违例**——accept/reject 循环
#: （:func:`compose` 里的 ``if not res['violations']``）实际从不触发，
#: ``attempts`` 恒为 1（实测 20/20 首均如此）。
#:
#: 设``HARD_CONSTRAINTS = False`` 可把该值改为 0，从而关闭全部硬约束，
#: 用于构造「无约束起草」对照（见 :mod:`score_learning.grammar.kernel_baseline`）。
#: 这是论文 RQ2 中「dropping hard search destroys legality」的唯一可复现形式。
NEG = -1e9
HARD_CONSTRAINTS = True

#: 是否把「相邻两拍同音」的声部合并成一个延留长音。
#:
#: 为什么要：``grammar.yaml`` 里 learn 学到了 ``voicing.common_tone_keep_prob``
#: ——内声部保持共同音的比例（大调 0.666 / 小调 0.640），即语料里三分之二的
#: 相邻拍内声部**根本不动**。但这个数字此前只出现在 ``script.py`` 的报告文本里，
#: **生成器从未使用**：``_to_spec`` 过去对每一拍、每个声部都无条件写一个四分
#: 音符，于是四声部每拍齐奏。实测（20 首盲评包，12 首机器）齐奏率 94.5%，
#: 人类对照组 82.8%、训练外真实巴赫 87.5%——这就是听审说的「织体单一」。
#: 同音反复率同样偏高（61.8% vs 人类 53.1%），即「旋律在原地打转」。
#:
#: 安全性：延留**不改变任何音高**，故不新增平行五八度、声部交叉或超八度
#: （区间不变 ⇒ 没有新的移动），:func:`_check` 的硬规则判定完全不受影响。
#: 只需排除语料上非法的延留（七音挂留、需上行解决的导音）与低音延留
#: （低音由和弦转位定义，延留会与 ``inv`` 冲突）。
TIE_HOLDS = True

#: 声部关系代价的权重。实测（18 首 × 5 组配置，scripts/eval/check_texture.py --ab）：
#: 把**音高分布的对数概率**当代价，mean_voices_per_attack 全部落在 3.20–3.24 的
#: 噪声里，而 voice_density_cv 反而从 0.074 掉到 0.066——分布代价会把 DP 收敛到
#: 典型值、**压缩**声部间的对比度。结论：决定「几个声部在动」的是**节奏层**
#: （延留 / 错位起音，见 :data:`TIE_HOLDS`），不是音高分布。所以间距保留原来的
#: 手写线性惩罚（实测 density_cv 0.090，明显优于分布版 0.066），缺音仍用语料
#: 实测的 log 概率（-3.36 × 0.35 ≈ -1.18，与旧常数 -1.2 等价但有语料依据）。
VOICING_WEIGHT = 0.35
#: 声部对运动关系（similar / contrary / oblique / static）已从语料学出并存进
#: ``voicing.pair_motion``，但**默认不启用**：实测 0.8 / 1.5 两档都只换来噪声内的
#: 变化。原因同上——分布代价收敛到均值，而「织体单一」是节奏问题。数据保留在
#: grammar.yaml 里可供审计与后续实验，置 0 即关闭。
PAIR_MOTION_WEIGHT = 0.0


def _motion_kind(a0: int, a1: int, b0: int, b1: int) -> str:
    """两个声部在这一拍到下一拍之间的运动关系。"""
    da, db = a1 - a0, b1 - b0
    if da == 0 and db == 0:
        return 'static'
    if da == 0 or db == 0:
        return 'oblique'
    return 'similar' if (da > 0) == (db > 0) else 'contrary'


def _dir(d: int) -> str:
    return 'up' if d > 0 else ('down' if d < 0 else 'stay')


#: 条件运动分布 P(声部方向 | 女高方向) 的权重。1.0 = 直接把语料条件概率当作
#: 转移代价的一部分（与 inner_iv / bass_iv 同量级）。
#:
#: 实测（10 首，L1 距语料基线）：外声部 SB 由此**几乎完美对齐**
#: similar .286 / contrary .480（基线 .29 / .43，改前 .343 / .385）；
#: 内声部 SA 则改不动（similar .52 vs 基线 .35），且权重越大越糟（3.0 → .55，
#: 8.0 → .61）。原因不是权重不够，而是**硬约束**：`_inner_states` 要求
#: ``b < a < t < s``，女高跳进时女低不跟随就会声部交叉，那些候选直接被 NEG 淘汰。
#: 也就是说「声部序」与「声部独立（oblique）」存在真实张力，外声部没有这层
#: 约束所以能对齐。加大权重只会把内声部推向 similar，别处反而变差。
COND_MOTION_WEIGHT = 1.0


def _hold_ok(g, tonic_pc, b, b1, vi):
    """这一拍该声部能否延留到下一拍。

    条件（全部来自乐理，不含调式枚举）：
      * 下一拍存在，且在同一小节内（避免跨小节线的时值合并）；
      * 下一拍不是句首（乐句边界必须清晰）；
      * 两拍同音（延留的定义）；
      * 音在下一拍和弦里仍是和弦音——否则延留会改变实际和声；
      * 不是七音（必须下行解决），也不是「下一拍进主和弦」的导音（悬挂导音）。
    低音（vi == 3）一律不延留：它的音由和弦转位定义。
    """
    if vi == 3 or not TIE_HOLDS:
        return False
    if b1 is None or b1.get('phrase_start') or b1.get('cadence'):
        return False
    if b.get('bar') != b1.get('bar') or b.get('beat', 1) >= 4:
        return False
    if b['midi'][vi] != b1['midi'][vi]:
        return False
    root1, q1, _inv1 = g.chords[b1['ci']]
    mem1 = chord_members(root1, q1)
    rel = (b['midi'][vi] - tonic_pc) % 12
    role = mem1.get(rel)
    if role is None:
        return False                 # 下一拍和弦里没有这个音 → 会改变和声
    if role == '7':
        return False                 # 七音必须下行解决
    root0, q0, _ = g.chords[b['ci']]
    if rel == LT and root0 in (7, 11) and root1 == 0:
        return False                 # 悬挂导音
    return True


def apply_tie_holds(g, tonic_pc, beats, rng):
    """给每拍每个声部打 ``hold`` 标记（该拍音延留到下一拍）。

    概率用 learn 学到的 ``common_tone_keep_prob``；内声部 A/T 各自独立抽签，
    于是实际生成里会出现「A 停 T 动」这类声部对比，而不是四声部齐奏。
    女高不延留——旋律线被切断比织体单一更伤；低音不延留——见 :func:`_hold_ok`。
    """
    p = float(g.keep_p or 0.0)
    if p <= 0:
        return beats
    for j, b in enumerate(beats):
        b1 = beats[j + 1] if j + 1 < len(beats) else None
        b.setdefault('hold', [False] * 4)
        for vi in (1, 2):
            if _hold_ok(g, tonic_pc, b, b1, vi) and rng.random() < p:
                b['hold'][vi] = True
    return beats

LT = 11   # 导音（大调第 7 级 / 小调升 7 级）在调内坐标里的 pc


def _reachable(score: float) -> bool:
    """DP 得分是否代表「可行解」。

    原本写作 ``score > NEG/2``，这在 ``NEG = -1e9`` 时是对的；但关闭硬约束
    后 ``NEG`` 被置 0（见 :data:`HARD_CONSTRAINTS`），判据会退化成
    ``score > 0``，把合法的低分解一并判成不可行（实测整句被 _Dead 弃掉）。
    故改为按当前 NEG 的语义判断：NEG<0 时用绝对阈值，NEG==0 时无阈值。
    """
    if NEG < 0:
        return score > NEG / 2.0
    return score > float('-inf')


def _logp_table(dist: List[List], floor: float = 1e-4) -> Dict[Any, float]:
    return {k: math.log(max(p, floor)) for k, p, *_ in dist}


def _logsumexp(a: np.ndarray, axis: int) -> np.ndarray:
    m = np.max(a, axis=axis, keepdims=True)
    m = np.where(np.isfinite(m), m, 0.0)
    return np.squeeze(m, axis) + np.log(np.sum(np.exp(a - m), axis=axis))


class Grammar:
    """规则脚本的运行时视图（某一调式）。"""

    def __init__(self, script: Dict[str, Any], mode: str):
        self.script = script
        self.mode = mode
        self.temperature = 1.0
        # 和声 × 音程 × 角色 × 轮廓相乘时"原地不动"被重复计分；此偏置把生成的同音比例
        # 校准回语料水平（见 evaluate 的 sop_repeat）
        self.repeat_bias = float(script.get('calibration', {}).get('sop_repeat_bias', 0.6))
        self.vocab: List[str] = list(script['vocab'][mode])
        self.idx = {c: i for i, c in enumerate(self.vocab)}
        self.chords = [parse_chord_key(c) for c in self.vocab]
        self.roman = script['vocab'][mode]
        self.model = BackoffModel(self.vocab)
        self.rule_of: Dict[Tuple[str, Tuple], str] = {}
        keys = {'L3': ('prev2', 'prev', 'pos'), 'L2': ('prev', 'pos'), 'L1': ('prev',),
                'L0': ('pos',)}
        for lv, rules in script['harmony'][mode].items():
            for r in rules:
                ctx = tuple(r['when'][k] for k in keys[lv])
                self.rule_of[(lv, ctx)] = r['id']
                for c, _p, n in r['then']:
                    self.model.t[lv][ctx][c] = n
        self.V = len(self.vocab)
        self._H: Dict[str, np.ndarray] = {}
        att = np.zeros((self.V, self.V), dtype=bool)
        for i, c in enumerate(self.vocab):
            cnt = self.model.t['L1'].get((c,))
            if cnt:
                for c2 in cnt:
                    if c2 in self.idx:
                        att[i, self.idx[c2]] = True
        self.attested = att
        mel = script['melody'][mode]
        self.iv = {pos: _logp_table(d) for pos, d in mel['interval_by_pos'].items()}
        tot = sum(n for d in mel['interval_by_pos'].values() for _k, _p, n in d)
        self.repeat_rate = sum(n for d in mel['interval_by_pos'].values()
                               for k, _p, n in d if k == 0) / max(tot, 1)
        self.role = {pos: _logp_table(d) for pos, d in mel['chord_role_by_pos'].items()}
        self.templates = mel['contour_templates']
        self.arc = {int(k): v for k, v in mel['register_arc'].items()}
        self.join = _logp_table(mel['phrase_join_interval'])
        self.start_deg = _logp_table(mel['start_degree'])
        self.sop_center = mel['abs_mean']
        self.first_chord = _logp_table(script['form'][mode]['first_chord'])
        self.bass_iv = _logp_table(script['bass'][mode]['interval'])
        vo = script['voicing'][mode]
        self.inner_iv = {k: _logp_table(v) for k, v in vo['inner_motion'].items()}
        self.doubling = {k: _logp_table(v) for k, v in vo['doubling'].items()}
        self.lt_inner = _logp_table(vo['inner_leading_tone'])
        self.keep_p = vo['common_tone_keep_prob']
        self.ranges = script['voicing']['ranges']
        self.orn = script['ornament']
        # ↓ 声部之间的关系（旧版这些代价全是手写常数：间距超 9 每半音罚 0.08、
        #   缺五度罚 1.2，现在一律改用语料实测的对数概率）
        self.pair_span = {k: _logp_table(v) for k, v in
                          (vo.get('pair_span') or {}).items()}
        self.pair_motion = {k: {kk: math.log(max(pp, 1e-4)) for kk, pp, _n in v}
                            for k, v in (vo.get('pair_motion') or {}).items()}
        self.omit_logpen = dict(vo.get('omission_logpen') or {})
        #: P(声部方向 | 女高方向) 的对数概率。**这是「声部之间关系」的正确载体**：
        #: 边缘分布（inner_motion 只存音程绝对值）丢掉了方向，无法表达「与女高同向
        #: 还是反向」这个相关性——而那恰恰是巴赫织体的核心（女高↑时低音 57% ↓）。
        self.cond_motion = {}
        for v, tab in (vo.get('cond_motion') or {}).items():
            self.cond_motion[v] = {k: math.log(max(p, 1e-3))
                                    for k, (_n, p) in tab.items()}
        self.cadences = script['cadences'][mode]
        self.plan_rules = script['form'][mode]['cadence_plan']

    # ---------------------------------------------------------------- 和声
    def H(self, pos: str) -> np.ndarray:
        """位置条件下的和弦转移对数概率（L2→L1→L0 回退），未出现过的二元组为 -inf。"""
        if pos not in self._H:
            M = np.full((self.V, self.V), NEG)
            for i, a in enumerate(self.vocab):
                for j, b in enumerate(self.vocab):
                    if self.attested[i, j]:
                        M[i, j] = math.log(self.model.prob(None, a, pos, b))
            self._H[pos] = M
        return self._H[pos]

    def harmony_logp(self, p2, p1, pos, c) -> float:
        return math.log(max(self.model.prob(p2, p1, pos, c), 1e-12))

    def harmony_rule(self, p2, p1, pos, c) -> str:
        for lv, ctx in (('L3', (p2, p1, pos)), ('L2', (p1, pos)), ('L1', (p1,)), ('L0', (pos,))):
            cnt = self.model.t[lv].get(ctx)
            if cnt and cnt.get(c, 0) > 0:
                return self.rule_of.get((lv, ctx), lv)
        return 'L0'


def _pick(rng: random.Random, items: Sequence[Tuple[Any, float]]):
    tot = sum(max(p, 0.0) for _x, p in items)
    if tot <= 0:
        return items[0][0]
    r = rng.random() * tot
    for x, p in items:
        r -= max(p, 0.0)
        if r <= 0:
            return x
    return items[-1][0]


# ======================================================================== 计划
def _bars_layout(total: int, rng: random.Random, p3: float) -> List[int]:
    """把 total 小节拆成 2 / 3 / 4 小节的乐句。

    旧版**只允许 2/3 小节**，于是 16 小节必被切成 7–8 句，每 2 小节一个终止式：
    终止式密度实测 8.0 个/8 小节，而训练外真实巴赫只有 2.2 个/8 小节
    （盲评包 12 首机器 vs 60 首参考池）。乐句被切碎、终止式等间距机械重复，
    听感正是评审说的「乐句乱」。

    语料侧并不支持「一句就是 2 小节」：``form.<mode>.phrase_count`` 显示一首平均
    6 句（major 6 句占 35.2%），16 小节 ÷ 6 句 ≈ 2.7 小节/句。允许 4 小节句
    才能落到这个密度上，终止式密度随句数同步下降。
    """
    opts: List[List[int]] = []

    def rec(rest: int, acc: List[int]) -> None:
        if rest == 0:
            if len(acc) >= 2:
                opts.append(acc[:])
            return
        if len(acc) >= 12:
            return
        for b in (4, 3, 2):                 # 先试长句，得到偏好 4 小节句的组合
            left = rest - b
            if left == 0 or left >= 2:
                acc.append(b)
                rec(left, acc)
                acc.pop()

    rec(total, [])
    if not opts:
        raise ValueError(f'{total} 小节无法拆成 2/3/4 小节的乐句')
    # p3 = 语料里 3 小节句的占比；4 小节句更少，按 (1-p3) 打折
    w = [sum(p3 if b == 3 else (1.0 - p3) * 0.35 if b == 4 else 1.0 for b in o)
         for o in opts]
    pick = rng.random() * sum(w)
    acc_w = 0.0
    bars = opts[-1]
    for o, wi in zip(opts, w):
        acc_w += wi
        if acc_w >= pick:
            bars = o
            break
    return bars


def plan_piece(g: Grammar, rng: random.Random, n_phrases: Optional[int],
               barform: Optional[bool], total_bars: Optional[int] = None,
               layout: str = 'auto') -> Dict[str, Any]:
    if layout not in ('pickup', 'downbeat'):
        p_pick = (g.script['form'].get('layout') or {}).get('pickup', 0.0)
        layout = 'pickup' if rng.random() < p_pick else 'downbeat'
    if layout == 'pickup':
        lay = g.script['form'].get('pickup_phrase_slots') or [[8, 1.0, 1]]
        p3 = sum(p for k, p, _n in lay if int(k) >= 10)
    else:
        lay = g.script['form'].get('downbeat_phrase_slots') or [[7, 1.0, 1]]
        p3 = sum(p for k, p, _n in lay if int(k) >= 9)
    fixed_bars = _bars_layout(int(total_bars), rng, p3) if total_bars else None
    if fixed_bars:
        n_phrases = len(fixed_bars)
    if not n_phrases:
        dist = [(int(k), p) for k, p, _n in g.script['form'][g.mode]['phrase_count']
                if 4 <= int(k) <= 8]
        n_phrases = _pick(rng, dist)
    n_phrases = max(2, min(12, int(n_phrases)))
    if barform is None:
        barform = n_phrases >= 5 and rng.random() < g.script['form'].get('barform_prob', 0)
    tonic_targets = [t for t in g.cadences if t.startswith('0:')]
    targets, rules = [], []
    prev = 'BOS'
    for k in range(n_phrases):
        if barform and k in (2, 3):
            targets.append(targets[k - 2])
            rules.append(f'Barform（复用第 {k - 1} 句）')
            prev = targets[-1]
            continue
        phase = 'first' if k == 0 else 'last' if k == n_phrases - 1 else \
            'penult' if k == n_phrases - 2 else 'middle'
        rule = next((r for r in g.plan_rules
                     if r['when']['prev'] == prev and r['when']['phase'] == phase), None)
        if rule is None:
            rule = next((r for r in g.plan_rules if r['when']['phase'] == phase), None)
        cand = [(t, p) for t, p, _n in (rule['then'] if rule else []) if t in g.cadences]
        if phase == 'last':
            cand = [(t, p) for t, p in cand if t in tonic_targets] or \
                [(tonic_targets[0], 1.0)]
        if not cand:
            cand = [(t, c['n']) for t, c in g.cadences.items()]
        t = _pick(rng, cand)
        targets.append(t)
        rules.append(rule['id'] if rule else '—')
        prev = t
    if fixed_bars:
        bars = fixed_bars
        if barform and (bars[2], bars[3]) != (bars[0], bars[1]):
            barform = False
            rules[2:4] = ['Barform 取消（乐句长度不同），沿用目标'] * 2
    else:
        bars = [3 if (k not in (2, 3) or not barform) and rng.random() < p3 * 0.6 else 2
                for k in range(n_phrases)]
        if barform:
            bars[2], bars[3] = bars[0], bars[1]
    return {'n': n_phrases, 'targets': targets, 'plan_rules': rules, 'barform': barform,
            'bars': bars, 'layout': layout}


# ======================================================================== 和声 + 女高音
def _slot_meta(bars: int, pickup: bool = False):
    """强拍起两小节句：1 2 3 4 | 1 2 3(终止，延长两拍)；
    弱起两小节句：4 | 1 2 3 4 | 1 2 3(终止)，第 4 拍是下一句的弱起。三小节同理。
    bar 是相对小节：弱起时 0 = 句首弱起拍所在的（上一句末）小节。"""
    L = bars * 4 if pickup else bars * 4 - 1
    shift = 3 if pickup else 0
    out = []
    for i in range(L):
        beat = (i + shift) % 4
        out.append({'strong': beat in (0, 2), 'pos': pos_class(i, 0, L - 1, beat in (0, 2)),
                    'bar': (i + shift) // 4, 'beat': beat})
    return out


def _sop_candidates(g: Grammar, tonic_pc: int, ci: int, fixed_pc: Optional[int]):
    root, q, _inv = g.chords[ci]
    pcs = set(chord_pcs(root, q))
    lo, hi = g.ranges['S']['low'], g.ranges['S']['high']
    out = []
    for m in range(lo, hi + 1):
        rel = (m - tonic_pc) % 12
        if rel in pcs and (fixed_pc is None or rel == fixed_pc):
            out.append(m)
    return out


def _phrase_states(g, tonic_pc, meta, formula, prev_sop=None):
    L = len(meta)
    fixed_ch = [None] * L
    fixed_sp = [None] * L
    for k, c in enumerate(formula['chords']):
        fixed_ch[L - 3 + k] = g.idx[c]
    fixed_sp[L - 2], fixed_sp[L - 1] = formula['soprano']
    states = []
    for t in range(L):
        cis = [fixed_ch[t]] if fixed_ch[t] is not None else range(g.V)
        st = [(ci, m) for ci in cis for m in _sop_candidates(g, tonic_pc, ci, fixed_sp[t])]
        states.append(st)
    return states


def _emission(g, tonic_pc, meta, states, template, center):
    L = len(meta)
    shape = _resample(template['shape'], L)
    E = []
    for t in range(L):
        pos = meta[t]['pos']
        role = g.role.get(pos, {})
        e = np.empty(len(states[t]))
        for k, (ci, m) in enumerate(states[t]):
            root, q, _ = g.chords[ci]
            rl = chord_members(root, q).get((m - tonic_pc) % 12, 'N')
            target = center + shape[t]
            e[k] = role.get(rl, math.log(1e-4)) - 0.5 * ((m - target) / 2.6) ** 2
        E.append(e)
    return E


def _transition(g, tonic_pc, pos, sa, sb):
    """女高音 + 和弦的转移矩阵（含 R06 导音、R07 七音、R08 旋律、R10 语法内硬约束）。"""
    Hm = g.H(pos)
    ivt = g.iv.get(pos) or g.iv.get('mid_w', {})
    ca = np.array([s[0] for s in sa])
    cb = np.array([s[0] for s in sb])
    ma = np.array([s[1] for s in sa])
    mb = np.array([s[1] for s in sb])
    T = Hm[ca[:, None], cb[None, :]].copy()
    D = mb[None, :] - ma[:, None]
    ivv = np.vectorize(lambda d: ivt.get(int(max(-12, min(12, d))), math.log(1e-4)))
    T += ivv(D)
    T[D == 0] -= g.repeat_bias
    bad = (np.abs(D) == 6) | (np.abs(D) == 10) | (np.abs(D) == 11) | (np.abs(D) > 12)
    T[bad] = NEG
    for i, (ci, m) in enumerate(sa):
        root, q, _ = g.chords[ci]
        rel = (m - tonic_pc) % 12
        mem = chord_members(root, q)
        if q in SEVENTHS and mem.get(rel) == '7':
            ok = (D[i] == -1) | (D[i] == -2) | (D[i] == 0)
            T[i, ~ok] = NEG
        if rel == LT and root in (7, 11) and q in ('M', '7', 'd', 'o7', 'h7'):
            for j, (cj, _m2) in enumerate(sb):
                if g.chords[cj][0] == 0 and D[i, j] != 1:
                    T[i, j] = NEG
        if q in ('M', '7'):
            for j, (cj, _m2) in enumerate(sb):
                if (D[i, j] != 1 and
                        secondary_lt(root, q, g.chords[cj][0], g.mode) == rel):
                    T[i, j] = NEG
    return T


def _path_score(path, init, E, Traw):
    s = init[path[0]] + E[0][path[0]]
    for t in range(1, len(path)):
        s += Traw[t][path[t - 1], path[t]] + E[t][path[t]]
    return s


def _ffbs(states, E, Traw, init, rng_np, n_samples, tau):
    L = len(states)
    alpha = [(init + E[0]) / tau]
    Ts = [None] + [Traw[t] / tau for t in range(1, L)]
    for t in range(1, L):
        a = _logsumexp(alpha[-1][:, None] + Ts[t], axis=0) + E[t] / tau
        alpha.append(a)
    # Viterbi（最可能路径）
    delta = [(init + E[0])]
    back = [None]
    for t in range(1, L):
        sc = delta[-1][:, None] + Ts[t] * tau
        back.append(np.argmax(sc, axis=0))
        delta.append(np.max(sc, axis=0) + E[t])
    paths = []
    if _reachable(np.max(delta[-1])):
        j = int(np.argmax(delta[-1]))
        path = [j]
        for t in range(L - 1, 0, -1):
            j = int(back[t][j])
            path.append(j)
        paths.append(path[::-1])
    if not np.isfinite(np.max(alpha[-1])) or np.max(alpha[-1]) < NEG / 2:
        return paths
    for _ in range(n_samples):
        p = np.exp(alpha[-1] - np.max(alpha[-1]))
        j = int(rng_np.choice(len(p), p=p / p.sum()))
        path = [j]
        for t in range(L - 1, 0, -1):
            sc = alpha[t - 1] + Ts[t][:, j]
            p = np.exp(sc - np.max(sc))
            if not np.isfinite(p.sum()) or p.sum() <= 0:
                break
            j = int(rng_np.choice(len(p), p=p / p.sum()))
            path.append(j)
        else:
            paths.append(path[::-1])
    return [p for p in paths if _reachable(_path_score(p, init, E, Traw))]


def compose_phrase_candidates(g, tonic_pc, meta, formula, template, center, prev,
                              rng_np, n_samples=24, tau=0.9, first=False):
    """一句的 (和弦, 女高音) 候选序列，按完整得分降序。prev = 上一句终止 (ci, sop)。"""
    states = _phrase_states(g, tonic_pc, meta, formula)
    if any(not s for s in states):
        return []
    E = _emission(g, tonic_pc, meta, states, template, center)
    init = np.zeros(len(states[0]))
    for k, (ci, m) in enumerate(states[0]):
        if first:
            init[k] = g.first_chord.get(g.vocab[ci], NEG) + \
                g.start_deg.get((m - tonic_pc) % 12, math.log(1e-3))
        else:
            pci, pm = prev
            h = g.H('start')[pci, ci]
            d = m - pm
            init[k] = h + g.join.get(max(-12, min(12, d)), math.log(1e-4)) + \
                (NEG if bad_melodic(pm, m) else 0.0)
    Traw = [None] + [_transition(g, tonic_pc, meta[t]['pos'], states[t - 1], states[t])
                     for t in range(1, len(meta))]
    paths = _ffbs(states, E, Traw, init, rng_np, n_samples, tau)
    seen, cands = set(), []
    for path in paths:
        seq = [states[t][j] for t, j in enumerate(path)]
        key = tuple(seq)
        if key in seen:
            continue
        seen.add(key)
        cands.append(seq)
    return cands


def score_candidate(g, tonic_pc, meta, seq, prev_chords, E_center, template, prev_sops):
    """完整得分：含前两拍和弦的 L3 规则、跳进回补、重复与变化。"""
    sc = 0.0
    p2, p1 = prev_chords
    for t, (ci, m) in enumerate(seq):
        c = g.vocab[ci]
        sc += g.harmony_logp(p2, p1, meta[t]['pos'], c)
        p2, p1 = p1, c
    mel = [m for _c, m in seq]
    for t in range(2, len(mel)):
        if abs(mel[t - 1] - mel[t - 2]) >= 5:
            d0, d1 = mel[t - 1] - mel[t - 2], mel[t] - mel[t - 1]
            if not (0 < abs(d1) <= 2 and (d1 > 0) != (d0 > 0)):
                sc -= 1.5
        if mel[t] == mel[t - 1] == mel[t - 2]:
            sc -= 2.5
    reps = sum(1 for t in range(1, len(mel)) if mel[t] == mel[t - 1])
    sc -= 1.5 * max(0, reps - round(g.repeat_rate * (len(mel) - 1)))
    chs = [ci for ci, _m in seq]
    for t in range(2, len(chs)):
        if chs[t] == chs[t - 1] == chs[t - 2]:
            sc -= 2.0
    if tuple(mel) in prev_sops:
        sc -= 25.0
    # 逐拍复制：**音程序列**与某前句完全一致、但音高不同（= 移调重复）。
    # 这是「旋律没有记忆点」的真正机制——原逻辑只罚「整句音高完全相同」，
    # 于是 A 句被安全地原样复制成 A'，听感是短动机在原地机械反复。
    # 只罚「逐拍复制」而**不罚「轮廓相似」**：巴赫的 A A' 正是轮廓相近而细节有变，
    # 那恰恰是人类作品被听成「有记忆点」的东西，一刀切禁止相似会把它一起杀掉。
    ivs = tuple(mel[t + 1] - mel[t] for t in range(len(mel) - 1))
    for pm in prev_sops:
        if len(pm) == len(mel):
            if tuple(pm[t + 1] - pm[t] for t in range(len(pm) - 1)) == ivs:
                sc -= 8.0
                break
    # 句内 3-gram 重复：短动机在原地反复，是「旋律没有记忆点」的直接度量
    # （实测生成曲 0.38 vs 真实巴赫 0.17）。上面的整句罚分抓不到它——重复发生
    # 在乐句内部。FFBS 已给出 24 条候选，这里挑重复最少的。
    grams = [tuple(mel[t:t + 3]) for t in range(len(mel) - 3)]
    if grams:
        sc -= 0.6 * (len(grams) - len(set(grams)))
    shape = _resample(template['shape'], len(seq))
    sc -= 0.25 * sum(((m - (E_center + shape[t])) / 2.6) ** 2 for t, m in enumerate(mel))
    return sc


# ======================================================================== 低音
def compose_bass(g, tonic_pc, chords, sop, prev):
    """低音 Viterbi。prev = (上一拍女高音, 上一拍低音, 上一拍和弦) 或 None。"""
    lo, hi = g.ranges['B']['low'], g.ranges['B']['high']
    mean = g.ranges['B']['mean']
    L = len(chords)
    cands = []
    for t in range(L):
        root, q, inv = g.chords[chords[t]]
        bpc = (root + QUALITIES[q][min(inv, len(QUALITIES[q]) - 1)]) % 12
        cs = [m for m in range(lo, hi + 1) if (m - tonic_pc) % 12 == bpc
              and 7 <= sop[t] - m <= 36]
        cands.append(cs)
        if not cs:
            return None

    def emit(t, m):
        e = -0.5 * ((m - mean) / 5.0) ** 2
        if t == L - 1 and g.chords[chords[t]][0] == 0:
            e -= 0.04 * (m - lo)
        return e

    def trans(t, a, b, sa, sb, ca, cb):
        if bad_melodic(a, b) and abs(b - a) != 12:
            return NEG
        if parallel_perfect(sa, a, sb, b) or hidden_perfect_outer(sa, a, sb, b):
            return NEG
        ra, qa, _ = g.chords[ca]
        rb = g.chords[cb][0]
        rel = (a - tonic_pc) % 12
        if qa in SEVENTHS and chord_members(ra, qa).get(rel) == '7':
            if not (b - a in (-1, -2) or (b == a and (b - tonic_pc) % 12 in
                                              chord_pcs(*g.chords[cb][:2]))):
                return NEG
        if rel == LT and ra in (7, 11) and rb == 0 and b - a != 1:
            return NEG
        if b - a != 1 and secondary_lt(ra, qa, rb, g.mode) == rel:
            return NEG
        r = g.bass_iv.get(max(-12, min(12, b - a)), math.log(1e-4))
        # 外声部条件关系 P(低音方向 | 女高方向)：语料里女高↑时低音 57% ↓、
        # 女高↓时低音 53% ↑，而低音「不动」只有 7%——反向进行是巴赫织体的核心手法，
        # 边缘的 bass_iv（只看低音自己走多远）完全表达不出这个相关性。
        if g.cond_motion:
            tab = g.cond_motion.get('B')
            if tab:
                r += (COND_MOTION_WEIGHT
                      * tab.get(f'{_dir(sb - sa)}|{_dir(b - a)}', math.log(0.05)))
        # 外声部（女高 vs 低音）运动关系：语料 43% 反向、29% 同向，生成器此前
        # 完全不管——低音只按自己的音程分布走，于是外声部大量同向，织体变窄。
        if g.pair_motion and PAIR_MOTION_WEIGHT:
            tab = g.pair_motion.get('SB')
            if tab:
                r += PAIR_MOTION_WEIGHT * tab.get(
                    _motion_kind(sa, sb, a, b), math.log(0.05))
        return r

    delta = []
    for m in cands[0]:
        s = emit(0, m)
        if prev is not None:
            ps, pb, pc = prev
            s += trans(0, pb, m, ps, sop[0], pc, chords[0])
        delta.append(s)
    delta = [np.array(delta)]
    back = [None]
    for t in range(1, L):
        cur, bk = [], []
        for m in cands[t]:
            best, arg = NEG * 2, 0
            for k, a in enumerate(cands[t - 1]):
                s = delta[-1][k] + trans(t, a, m, sop[t - 1], sop[t], chords[t - 1], chords[t])
                if s > best:
                    best, arg = s, k
            cur.append(best + emit(t, m))
            bk.append(arg)
        delta.append(np.array(cur))
        back.append(bk)
    if not _reachable(np.max(delta[-1])):
        return None
    j = int(np.argmax(delta[-1]))
    path = [j]
    for t in range(L - 1, 0, -1):
        j = back[t][j]
        path.append(j)
    path = path[::-1]
    return [cands[t][j] for t, j in enumerate(path)]


# ======================================================================== 内声部
def _inner_states(g, tonic_pc, ci, s, b):
    """内声部候选 (A, T) 的枚举。

    这里有**七条独立的硬过滤**（和弦音成员、声部序 b<a<t<s、相邻间距
    <=12、低音间距 <=19、三度必须存在、导音不重复、七音不重复），它们
    **不走** :data:`NEG`——NEG 只作用于 DP 的转移代价，而这里是候选集的
    **构造**边界。因此仅把 NEG 置0 不能关闭约束：候选集仍可能为空，
    :func:`compose_inner` 返回 None、整句被 :class:`_Dead` 弃掉
    （实测「第1 句无可行声部进行」）。

    ``HARD_CONSTRAINTS = False`` 时逐条放宽：音域放宽到全局 MIDI 跨度、
    声部序与间距不检查、成员要求取消、导音/七音重复放行。这样才能构造出
    真正「无约束」的对照（见
    :mod:`score_learning.grammar.kernel_baseline`）。
    """
    root, q, inv = g.chords[ci]
    pcs = chord_pcs(root, q)
    mem = chord_members(root, q)
    ra, rt = g.ranges['A'], g.ranges['T']
    if HARD_CONSTRAINTS:
        a_lo, a_hi = ra['low'], ra['high']
        t_lo, t_hi = rt['low'], rt['high']
    else:
        a_lo, a_hi = 0, 127
        t_lo, t_hi = 0, 127
    out = []
    for a in range(a_lo, a_hi + 1):
        if (a - tonic_pc) % 12 not in pcs:
            continue
        if HARD_CONSTRAINTS and (not (b < a < s) or s - a > 12):
            continue
        for t in range(t_lo, t_hi + 1):
            if (t - tonic_pc) % 12 not in pcs:
                continue
            if HARD_CONSTRAINTS and (not (b < t < a)
                                     or a - t > 12 or t - b > 19):
                continue
            rel = [(x - tonic_pc) % 12 for x in (s, a, t, b)]
            roles = [mem.get(r) for r in rel]
            if HARD_CONSTRAINTS:
                need = {'R', '3'} | ({'7'} if q in SEVENTHS else set())
                if not need <= set(roles):
                    continue
                if LT in pcs and rel.count(LT) > 1 and root in (7, 11):
                    continue
                if q in SEVENTHS and roles.count('7') > 1:
                    continue
            key = f'{"7th" if q in SEVENTHS else "triad"}:{inv}'
            e = 0.0
            dbl = [r for r in set(roles) if roles.count(r) >= 2]
            tab = g.doubling.get(key, {})
            for r in dbl:
                e += tab.get(r, math.log(0.02))
            # 缺五度：旧版是手写常数 `e -= 1.2`，现改用语料实测的对数概率
            # （triad 原位实测 -3.36，即语料里缺五度比缺三度更少见）
            if '5' not in roles and q not in SEVENTHS:
                e += VOICING_WEIGHT * g.omit_logpen.get(key, {}).get('5', math.log(0.05))
            # 声部间距：保留原来的线性惩罚 `0.08 * max(0, 间距 - 9)`。
            # 曾改成用语料 SA/AT 间距的对数分布，实测 voice_density_cv 反而从
            # 0.090 掉到 0.066——分布概率让 DP 只挑「典型间距」，对比度被抹平。
            # 要改织体得改节奏层（延留），不是改音高分布。语料间距分布已存进
            # ``voicing.pair_span`` 供审计。
            e -= 0.08 * max(0, (s - a) - 9) + 0.08 * max(0, (a - t) - 9)
            out.append(((a, t), e))
    return out


def compose_inner(g, tonic_pc, chords, sop, bass, prev):
    """内声部 Viterbi。prev = 上一拍 (S, A, T, B, 和弦) 或 None。"""
    L = len(chords)
    states = [_inner_states(g, tonic_pc, chords[t], sop[t], bass[t]) for t in range(L)]
    if any(not s for s in states):
        return None
    ia, it = g.inner_iv['A'], g.inner_iv['T']

    def trans(pv, cv, pch, cch):
        if overlap(pv, cv):
            return NEG
        for i, j in PAIRS:
            if (i, j) == (0, 3):
                continue
            if parallel_perfect(pv[i], pv[j], cv[i], cv[j]):
                return NEG
        s = 0.0
        rp, qp, _ = g.chords[pch]
        mem_p = chord_members(rp, qp)
        cur_pcs = set(chord_pcs(*g.chords[cch][:2]))
        for vi, tab in ((1, ia), (2, it)):
            d = cv[vi] - pv[vi]
            if bad_melodic(pv[vi], cv[vi]):
                return NEG
            rel = (pv[vi] - tonic_pc) % 12
            if qp in SEVENTHS and mem_p.get(rel) == '7':
                if not (d in (-1, -2) or (d == 0 and rel in cur_pcs)):
                    return NEG
            if rel == LT and rp in (7, 11) and g.chords[cch][0] == 0:
                kind = 'up_to_tonic' if d == 1 else 'down_to_fifth' if d in (-3, -4) else None
                if kind is None:
                    return NEG
                s += g.lt_inner.get(kind, math.log(0.05))
            s += tab.get(min(12, abs(d)), math.log(1e-4))
            # 条件关系 P(本声部方向 | 女高方向)：语料里女高动时女低有 32–36% 保持
            # 不动（oblique 的来源），且女高上行时女低更倾向同向上行、下行时相反。
            # 这些是相关性，边缘的 inner_iv（只存绝对音程）表达不出来。
            if g.cond_motion:
                ctab = g.cond_motion.get('A' if vi == 1 else 'T')
                if ctab:
                    s += (COND_MOTION_WEIGHT
                          * ctab.get(f'{_dir(cv[0] - pv[0])}|{_dir(d)}',
                                     math.log(0.05)))
        # 声部对运动关系：把 learn 学到的分布本身当代价。
        # 语料里女高-女低 35% similar / 40% oblique / 16% contrary，外声部 43% contrary；
        # 生成器此前完全不管这一层，于是每个声部都跟着女高一起动 = 织体单一。
        if g.pair_motion:
            for (i, j), pair in (((0, 1), 'SA'), ((1, 2), 'AT'), ((2, 3), 'TB')):
                tab = g.pair_motion.get(pair)
                if tab:
                    kind = _motion_kind(pv[i], cv[i], pv[j], cv[j])
                    s += PAIR_MOTION_WEIGHT * tab.get(kind, math.log(0.05))
        return s

    delta = []
    for (at, e) in states[0]:
        s = e
        if prev is not None:
            ps, pa, pt, pb, pch = prev
            s += trans((ps, pa, pt, pb), (sop[0], at[0], at[1], bass[0]), pch, chords[0])
        delta.append(s)
    delta = [np.array(delta)]
    back = [None]
    for t in range(1, L):
        cur, bk = [], []
        for (at, e) in states[t]:
            cv = (sop[t], at[0], at[1], bass[t])
            best, arg = NEG * 2, 0
            for k, (pat, _pe) in enumerate(states[t - 1]):
                if delta[-1][k] < NEG / 2:
                    continue
                pv = (sop[t - 1], pat[0], pat[1], bass[t - 1])
                s = delta[-1][k] + trans(pv, cv, chords[t - 1], chords[t])
                if s > best:
                    best, arg = s, k
            cur.append(best + e)
            bk.append(arg)
        delta.append(np.array(cur))
        back.append(bk)
    if not _reachable(np.max(delta[-1])):
        return None
    j = int(np.argmax(delta[-1]))
    path = [j]
    for t in range(L - 1, 0, -1):
        j = back[t][j]
        path.append(j)
    path = path[::-1]
    return [states[t][j][0] for t, j in enumerate(path)]


# ======================================================================== 装饰音
def add_ornaments(g, tonic_pc, beats, rng: random.Random):
    """八分经过音 / 辅助音：按学到的各声部概率，且不得在八分网格上产生平行五八度。"""
    base = set(scale_of(g.mode))
    if g.mode == 'minor':
        up, down, nb = base - {8, 10} | {9, 11}, base, base - {10} | {11}
    else:
        up = down = nb = base
    names = ('S', 'A', 'T', 'B')
    for k in range(len(beats) - 1):
        b0, b1 = beats[k], beats[k + 1]
        if b0.get('cadence') or b1.get('phrase_start'):
            continue
        used = 0
        order = list(range(4))
        rng.shuffle(order)
        for vi in order:
            if used >= 2:
                break
            if b0.get('hold') and b0['hold'][vi]:
                continue          # 本拍延留成二分音符，不能再插八分经过音
            a, c = b0['midi'][vi], b1['midi'][vi]
            gap = abs(c - a)
            o = g.orn[names[vi]]
            mid = None
            if gap in (3, 4) and rng.random() < o['passing_prob'] * 0.8:
                sc = up if c > a else down
                lo_, hi_ = min(a, c), max(a, c)
                ms = [m for m in range(lo_ + 1, hi_)
                      if (m - tonic_pc) % 12 in sc and m - lo_ <= 2 and hi_ - m <= 2]
                mid = ms[0] if ms else None
                kind = 'passing'
            elif gap == 0 and vi > 0 and rng.random() < o['neighbor_prob'] * 0.8:
                dirs = (-1, -2) if rng.random() < 0.6 else (1, 2)
                for dlt in dirs:
                    if (a + dlt - tonic_pc) % 12 in nb:
                        mid = a + dlt
                        break
                kind = 'neighbor'
            if mid is None:
                continue
            v_half = [b0['eighth'][x] if b0['eighth'][x] is not None else b0['midi'][x]
                      for x in range(4)]
            v_half[vi] = mid
            ok = True
            for i, j in PAIRS:
                if parallel_perfect(b0['midi'][i], b0['midi'][j], v_half[i], v_half[j]) or \
                        parallel_perfect(v_half[i], v_half[j], b1['midi'][i], b1['midi'][j]):
                    ok = False
                    break
            if ok and not (v_half[0] > v_half[1] > v_half[2] > v_half[3]):
                ok = False
            if ok and hidden_perfect_outer(v_half[0], v_half[3], b1['midi'][0], b1['midi'][3]):
                ok = False
            if ok:
                b0['eighth'][vi] = mid
                b0.setdefault('orn', []).append((names[vi], kind))
                used += 1


# ======================================================================== 主流程
def _note_name(g, ci, midi, tonic, tonic_pc, passing=False):
    """和弦音按三度叠置拼写，经过音按调内音阶拼写；返回 music21 记法（B-4、F#4）。"""
    rel = (midi - tonic_pc) % 12
    chord_n = chord_tone_names(g.chords[ci], tonic, g.mode)
    scale_n = scale_names(tonic, g.mode)
    first, second = (scale_n, chord_n) if passing else (chord_n, scale_n)
    nm = first.get(rel) or second.get(rel)
    if nm is None:
        from music21 import pitch as _p
        return _p.Pitch(midi=midi).nameWithOctave
    return _m21_name(name_with_octave(nm, midi))


def _m21_name(name: str) -> str:
    """'Bb4' → 'B-4'（music21 记法），'F#4' 不变。"""
    return name[0] + name[1:].replace('b', '-')


def compose(script: Dict[str, Any], key_sharps: int = 0, mode: str = 'major',
            phrases: Optional[int] = None, seed: Any = 0, barform: Optional[bool] = None,
            title: str = '', tempo: int = 72, max_tries: int = 12,
            temperature: float = 1.0, total_bars: Optional[int] = None,
            layout: str = 'auto', accept_any: bool = False) -> Dict[str, Any]:
    """按规则脚本作一首四部众赞歌，返回 {'spec', 'phrases', 'beats', 'plan', 'violations',
    'seed_used'}。temperature 越高越多样（越接近巴赫的分布），0 = 每步取最可能；
    total_bars 给定时乐句布局恰好填满该小节数（此时忽略 phrases）。
    layout：'pickup' 弱起（一拍弱起小节 + total_bars 个完整小节）、'downbeat' 强拍起、
    'auto' 按学到的比例抽签。

    ``accept_any=True`` 跳过「零违例才返回」这道门，直接返回第一次搜索到的
    结果（含违例）。**仅用于构造「无约束起草」对照**（见
    :mod:`score_learning.grammar.kernel_baseline`）；配合把 :data:`NEG` 置 0
    即可得到「关掉硬约束」的系统。默认 False，正常路径的 accept/reject 语义不变。"""
    if mode not in script['vocab']:
        raise ValueError(f'脚本里没有 {mode} 调式的规则')
    g = Grammar(script, mode)
    g.temperature = float(temperature)
    tonic = tonic_from_sharps(int(key_sharps), mode)
    _, tonic_pc = tonic_letter_pc(tonic)
    last_err = None
    for attempt in range(max_tries):
        sub = f'{seed}/{attempt}' if attempt else str(seed)
        try:
            res = _compose_once(g, tonic, tonic_pc, int(key_sharps), phrases, sub, barform,
                                title, tempo, total_bars, layout)
        except _Dead as e:
            last_err = str(e)
            continue
        if accept_any or not res['violations']:
            res['seed_used'] = sub
            res['attempts'] = attempt + 1
            res['accepted_any'] = bool(accept_any)
            return res
        last_err = f'{len(res["violations"])} 处违例'
    raise RuntimeError(f'{max_tries} 个种子分支都未能生成零违例的众赞歌：{last_err}')


class _Dead(Exception):
    pass


def _compose_once(g, tonic, tonic_pc, key_sharps, phrases, seed, barform, title, tempo,
                  total_bars=None, layout='auto'):
    rng = random.Random(f'chorale|{seed}')
    rng_np = np.random.default_rng(
        int(hashlib.md5(f'chorale|{seed}'.encode()).hexdigest()[:12], 16))
    plan = plan_piece(g, rng, phrases, barform, total_bars, layout)
    pickup = plan['layout'] == 'pickup'
    beats: List[Dict[str, Any]] = []
    trace_phr = []
    prev_state = None            # (ci, sop)
    prev_chords = ('BOS', 'BOS')
    prev_voices = None           # (S, A, T, B, ci)
    prev_sops = set()
    n_ph = plan['n']
    copies: Dict[int, Any] = {}
    bar_cursor = 0
    for k in range(n_ph):
        meta = _slot_meta(plan['bars'][k], pickup)
        L = len(meta)
        tgt = plan['targets'][k]
        center = g.sop_center + g.arc.get(min(4, int(5 * k / n_ph)), 0.0)
        src = copies.get(k - 2) if plan['barform'] and k in (2, 3) else None
        if src is not None and prev_state is not None:
            ci0, m0 = src['seq'][0]
            if not _reachable(g.H('start')[prev_state[0], ci0]) or bad_melodic(prev_state[1], m0):
                src = None
        forms = g.cadences[tgt]['forms']
        if k == n_ph - 1:
            forms = [f for f in forms if 'PAC' in f['type']] or forms
        chosen = None
        for local in range(5):
            if src is not None and local == 0:
                formula, template, cands = src['formula'], src['template'], [src['seq']]
            else:
                formula = _pick(rng, [(f, f['p']) for f in forms])
                template = _pick(rng, [(t, t['p']) for t in g.templates])
                cands = compose_phrase_candidates(
                    g, tonic_pc, meta, formula, template, center, prev_state, rng_np,
                    tau=max(g.temperature, 0.5), first=(k == 0))
                # Gumbel 扰动排序 = 按 exp(得分 / 温度) 无放回抽样；温度 0 即取最高分
                T = g.temperature
                keyed = []
                for s in cands:
                    sc = score_candidate(g, tonic_pc, meta, s, prev_chords, center, template,
                                         prev_sops)
                    gum = -math.log(-math.log(rng.random() or 1e-12)) if T > 0 else 0.0
                    keyed.append((sc / T + gum if T > 0 else sc, s))
                keyed.sort(key=lambda x: -x[0])
                cands = [s for _k, s in keyed]
            for seq in cands[:40]:
                chords = [ci for ci, _m in seq]
                sop = [m for _ci, m in seq]
                pb = (prev_voices[0], prev_voices[3], prev_voices[4]) if prev_voices else None
                bass = compose_bass(g, tonic_pc, chords, sop, pb)
                if bass is None:
                    continue
                inner = compose_inner(g, tonic_pc, chords, sop, bass, prev_voices)
                if inner is None:
                    continue
                chosen = (seq, chords, sop, bass, inner)
                break
            if chosen is not None:
                break
        if chosen is None:
            raise _Dead(f'第 {k + 1} 句无可行声部进行')
        seq, chords, sop, bass, inner = chosen
        copies[k] = {'formula': formula, 'template': template, 'seq': seq}
        prev_sops.add(tuple(sop))
        p2, p1 = prev_chords
        for t in range(L):
            c = g.vocab[chords[t]]
            pos = meta[t]['pos']
            rule = g.harmony_rule(p2, p1, pos, c)
            in_formula = t >= L - 3
            if pickup:
                # 时间从弱起拍 0 起算；第 0 小节是只有一拍的弱起小节
                bt, bn = float(bar_cursor * 4 + t), bar_cursor + meta[t]['bar']
            else:
                bt = float((bar_cursor + meta[t]['bar']) * 4 + meta[t]['beat'])
                bn = bar_cursor + meta[t]['bar'] + 1
            beats.append({
                't': bt, 'bar': bn, 'beat': meta[t]['beat'] + 1,
                'ci': chords[t], 'chord': c, 'roman': g.roman[c],
                'midi': [sop[t], inner[t][0], inner[t][1], bass[t]],
                'eighth': [None] * 4, 'phrase': k, 'pos': pos,
                'phrase_start': t == 0, 'cadence': t == L - 1,
                'rule': formula['id'] if in_formula else rule,
                'harmony_rule': rule,
            })
            p2, p1 = p1, c
        prev_chords = (p2, p1)
        prev_state = (chords[-1], sop[-1])
        prev_voices = (sop[-1], inner[-1][0], inner[-1][1], bass[-1], chords[-1])
        trace_phr.append({
            'phrase': k + 1, 'bars': plan['bars'][k],
            'target': g.roman.get(tgt + ':0', tgt), 'plan_rule': plan['plan_rules'][k],
            'formula': formula['id'], 'formula_say': formula['say'],
            'contour': template['id'], 'contour_name': template['name'],
            'harmony': ' '.join(b['roman'] for b in beats[-L:]),
        })
        bar_cursor += plan['bars'][k]
    apply_tie_holds(g, tonic_pc, beats, rng)
    add_ornaments(g, tonic_pc, beats, rng)
    spec = _to_spec(g, beats, tonic, tonic_pc, key_sharps, title, tempo, bar_cursor, pickup)
    violations = _check(g, beats, tonic_pc)
    return {'spec': spec, 'plan': plan, 'phrases': trace_phr, 'beats': beats,
            'violations': violations}


def _check(g, beats, tonic_pc):
    grid = []
    info = []
    for b in beats:
        grid.append((b['t'], list(b['midi'])))
        half = [b['eighth'][v] if b['eighth'][v] is not None else b['midi'][v] for v in range(4)]
        if half != b['midi']:
            grid.append((b['t'] + 0.5, half))
        root, q, inv = g.chords[b['ci']]
        mem = chord_members(root, q)
        rel = [(m - tonic_pc) % 12 for m in b['midi']]
        sev = (root + QUALITIES[q][3]) % 12 if q in SEVENTHS else None
        info.append({'t': b['t'], 'midi': b['midi'], 'rel': rel, 'members': mem,
                     'lt_pc': LT if (root in (7, 11) and LT in mem) else None,
                     'tonic_chord': root == 0, 'seventh_pc': sev, 'is_seventh': q in SEVENTHS,
                     'phrase_start': b['phrase_start'], 'chord': b.get('chord'),
                     'root': root, 'quality': q, 'mode': g.mode})
    return check_satb(grid, info, g.ranges)


def _to_spec(g, beats, tonic, tonic_pc, key_sharps, title, tempo, n_bars, pickup=False):
    parts = []
    names = ('Soprano', 'Alto', 'Tenor', 'Bass')
    clefs = ('treble', 'treble', 'treble8vb', 'bass')
    for vi in range(4):
        ev = []
        j = 0
        L = len(beats)
        while j < L:
            b = beats[j]
            hold = bool(b.get('hold') and b['hold'][vi])
            nm = _m21_name(_note_name(g, b['ci'], b['midi'][vi], tonic, tonic_pc))
            if hold:
                # 延留：与下一拍同音，合成一个二分音符，并跳过下一拍该声部
                ev.append([nm, 'half', 'fermata' if b['cadence'] else None])
                j += 2
                continue
            if b['cadence']:
                # 弱起写法的中间终止只占第 3 拍，第 4 拍让给下一句弱起
                dur = 'quarter' if pickup and j < L - 1 else 'half'
                ev.append([nm, dur, 'fermata'])
            elif b['eighth'][vi] is not None:
                ev.append([nm, 'eighth', None])
                ev.append([_m21_name(_note_name(g, b['ci'], b['eighth'][vi], tonic, tonic_pc,
                                                passing=True)), 'eighth', None])
            else:
                ev.append([nm, 'quarter', None])
            j += 1
        parts.append({'name': names[vi], 'program': None, 'clef': clefs[vi], 'events': ev})
    harmony = {}
    for b in beats:
        if b['beat'] == 1:
            names_ = chord_tone_names(g.chords[b['ci']], tonic, g.mode)
            harmony[b['bar']] = [_m21_name(n) for n in names_.values()]
    spec = {
        'title': title or f'众赞歌（学习自巴赫 · {tonic}{"大调" if g.mode == "major" else "小调"}）',
        'key_sharps': key_sharps, 'mode': g.mode, 'time_signature': '4/4', 'tempo': tempo,
        'bars': n_bars, 'voices': 4, 'parts': parts, 'harmony': harmony,
        'tempo_marks': [], 'meter_changes': [], 'key_changes': [],
        'style_notes': ['grammar:bach-chorale'], 'spelling_locked': True,
    }
    if pickup:
        spec['pickup_ql'] = 1.0
    return spec
