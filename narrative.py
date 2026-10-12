"""叙事层（drama）：张力曲线、高潮定位、动机注册表与变形、出场时间表、
惊喜注入、峰值后静默。

纯逻辑模块，不依赖 compose_kernel：输入是"每小节的客观特征"，输出是一份
**叙事计划（plan）**；由内核脚本 `narrative` 把计划落到音符/力度/演奏法上。

设计原则
--------
1. 全部由 seed 决定 → 同 seed 复现，不同 seed 给出不同戏剧走向；
2. 计划是**数据**（dict），可序列化进 spec、可在 UI 里画出来、可被人改；
3. 只做"结构性"的戏剧决策，不碰和声正确性——惊喜和弦仍走内核的
   `parse_roman`，静默仍保持小节时值填满。
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 动机注册表

#: 动机变形算子。每个算子 (motif, ctx) -> motif
MOTIF_TRANSFORMS = (
    'original',     # 原型
    'transpose',    # 移位（默认上五度/上四度，ctx.degrees 给级数差）
    'invert',       # 倒影（以首音为轴）
    'retrograde',   # 逆行
    'augment',      # 增时（×2）
    'diminish',     # 减时（÷2）
    'fragment',     # 碎片化（取前半）
    'ornament',     # 加花（长音前插邻音）
    'sequence',     # 模进（整体下移一级重复）
)

#: 出场角色 → 建议变形（巴赫/贝多芬传统：呈示用原型与移位，发展用倒影/碎片，
#: 密接用减时，再现回原型）
ROLE_TRANSFORMS: Dict[str, Tuple[str, ...]] = {
    'statement':   ('original',),
    'answer':      ('transpose',),
    'development': ('invert', 'fragment', 'sequence', 'retrograde'),
    'buildup':     ('sequence', 'augment'),
    'stretto':     ('diminish', 'fragment'),
    'recap':       ('original', 'ornament'),
    'coda':        ('fragment', 'augment'),
}

#: 惊喜和弦（意外度从高到低）。全部是功能和声里的"合理意外"：
#: 那不勒斯 bII、副属 vii°7、♭VI、以及同主音小调的 iv。
SURPRISE_CHORDS: Tuple[Tuple[str, str, float], ...] = (
    ('bII', '那不勒斯六和（意外离调）', 1.00),
    ('vii7', '副属减七（强倾向）', 0.85),
    ('bVI', '♭VI（同主音小调借用）', 0.70),
    ('iv', '同主音小调 iv（色彩转暗）', 0.55),
    ('V7', '属七（稳中加压）', 0.35),
)

_DYN_LEVELS = ('pp', 'p', 'mp', 'mf', 'f', 'ff')
_DYN_INDEX = {d: i for i, d in enumerate(_DYN_LEVELS)}


class NarrativeError(Exception):
    pass


def _rng(seed: str, *tags: str) -> random.Random:
    key = '|'.join(str(x) for x in (seed,) + tags)
    return random.Random(hashlib.md5(key.encode('utf-8')).hexdigest())


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


# ---------------------------------------------------------------------- 动机

class Motif:
    """动机：以「相对首音的半音数 + 时值（四分音符为单位）」表示。

    这样表示使变形算子与调性、音区解耦：换一个起始音就换一个调位。
    """

    __slots__ = ('intervals', 'durs', 'name')

    def __init__(self, intervals: Sequence[int], durs: Sequence[float],
                 name: str = 'motif'):
        if not intervals or not durs:
            raise NarrativeError('动机不能为空')
        self.intervals = [int(i) for i in intervals]
        self.durs = [float(d) for d in durs]
        self.name = str(name or 'motif')
        if len(self.intervals) != len(self.durs):
            # 长度不等时按时值截断/循环补齐，保证后续算子总能工作
            n = min(len(self.intervals), len(self.durs))
            self.intervals = self.intervals[:n]
            self.durs = self.durs[:n]

    # ---- 基本性质 ----
    @property
    def length_ql(self) -> float:
        return float(sum(self.durs))

    @property
    def span(self) -> int:
        return max(self.intervals) - min(self.intervals)

    def to_dict(self) -> Dict[str, Any]:
        return {'name': self.name, 'intervals': list(self.intervals),
                'durs': list(self.durs), 'length_ql': self.length_ql}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'Motif':
        return cls(d.get('intervals') or [0], d.get('durs') or [1.0],
                   d.get('name') or 'motif')

    @classmethod
    def from_midis(cls, midis: Sequence[int], durs: Sequence[float],
                   name: str = 'motif') -> 'Motif':
        if not midis:
            raise NarrativeError('动机音高为空')
        base = int(midis[0])
        return cls([int(m) - base for m in midis], durs, name)

    def notes_from(self, start_midi: int) -> List[Tuple[int, float]]:
        """以 start_midi 为首音展开为 (midi, ql) 序列。"""
        return [(int(start_midi) + int(i), float(d))
                for i, d in zip(self.intervals, self.durs)]

    def copy(self) -> 'Motif':
        return Motif(list(self.intervals), list(self.durs), self.name)


class MotifRegistry:
    """动机注册表：一部作品里所有"可识别材料"及其派生变体。"""

    def __init__(self):
        self._items: Dict[str, Motif] = {}
        self._lineage: List[Dict[str, str]] = []

    def register(self, motif: Motif, role: str = 'subject') -> Motif:
        key = motif.name
        self._items[key] = motif
        self._lineage.append({'motif': key, 'role': role,
                              'transform': 'original', 'parent': ''})
        return motif

    def derive(self, motif: Motif, kind: str, **ctx) -> Motif:
        m = transform_motif(motif, kind, **ctx)
        if m.name not in self._items:
            self._items[m.name] = m
            self._lineage.append({'motif': m.name, 'role': ctx.get('role', ''),
                                  'transform': kind, 'parent': motif.name})
        return m

    @property
    def lineage(self) -> List[Dict[str, str]]:
        return list(self._lineage)

    def as_dict(self) -> Dict[str, Any]:
        return {'motifs': {k: v.to_dict() for k, v in self._items.items()},
                'lineage': self.lineage}


def transform_motif(motif: Motif, kind: str, **ctx) -> Motif:
    """动机变形算子。ctx 可含 scale（调内音 midi 列表）、degrees（级数差）、
    role（角色名，用于命名）。"""
    kind = str(kind or 'original').lower()
    iv = list(motif.intervals)
    du = list(motif.durs)
    tag = ctx.get('role') or ''

    if kind == 'original':
        out = Motif(iv, du, f'{motif.name}')

    elif kind == 'transpose':
        # 按级数移位（默认上五度 = 4 级）；给 scale 时按调内音求半音差。
        # intervals 是「相对首音」的音程表示（intervals[0] 恒为 0）：
        # 整体移位 = 所有音程都加 step，展开时（notes_from(base_m)）
        # 整条旋律上移 step 而形状不变。只改 intervals[0] 会破坏
        # 「相对首音」约定，把后续音程全部拉歪。
        deg = int(ctx.get('degrees') or 4)
        scale = ctx.get('scale')
        step = _degree_semitones(scale, deg) if scale else 7
        out = Motif([i + step for i in iv], du, f'{motif.name}_T{deg}')

    elif kind == 'invert':
        out = Motif([-i for i in iv], du, f'{motif.name}_I')

    elif kind == 'retrograde':
        out = Motif(list(reversed(iv)), list(reversed(du)), f'{motif.name}_R')

    elif kind == 'augment':
        out = Motif(iv, [_clamp(d * 2.0, 0.25, 4.0) for d in du],
                    f'{motif.name}_A')

    elif kind == 'diminish':
        out = Motif(iv, [_clamp(d * 0.5, 0.25, 4.0) for d in du],
                    f'{motif.name}_D')

    elif kind == 'fragment':
        n = max(2, (len(iv) + 1) // 2)
        out = Motif(iv[:n], du[:n], f'{motif.name}_F')
        # 碎片要能站得住：补一个长音收尾
        if out.length_ql < 1.0:
            out.durs[-1] = _clamp(out.durs[-1] + 1.0, 0.25, 4.0)

    elif kind == 'ornament':
        niv, ndu = [], []
        for i, d in zip(iv, du):
            if d >= 1.0 and len(niv) < 12:
                half = _clamp(d / 2.0, 0.25, 4.0)
                niv.append(i + 1)     # 上方邻音
                ndu.append(half)
                niv.append(i)
                ndu.append(half)
            else:
                niv.append(i)
                ndu.append(d)
        out = Motif(niv, ndu, f'{motif.name}_O')

    elif kind == 'sequence':
        # 模进：整体下移一个调内级，接在原动机之后
        scale = ctx.get('scale')
        step = -_degree_semitones(scale, 1) if scale else -2
        out = Motif(iv + [i + step for i in iv], du + list(du),
                    f'{motif.name}_S')

    else:
        raise NarrativeError(f'未知变形算子 {kind!r}')

    if tag:
        out.name = f'{out.name}@{tag}'
    return out


def _degree_semitones(scale: Optional[Sequence[int]], deg: int) -> int:
    """调内音序列里从主音移动 deg 级对应的半音数（scale 为升序 midi 列表）。

    支持负级（下行）与跨八度：以一个八度内的音级数为周期，超出部分按八度折算。
    """
    if not scale or not deg:
        return 0
    sc = sorted(int(x) for x in scale)
    if len(sc) < 2:
        return 0
    base = sc[0]
    # 一个八度内的音级数：末音若恰为高八度主音则不计入
    per_oct = len(sc) - 1 if sc[-1] - base == 12 else len(sc)
    oct_shift, idx = divmod(int(deg), per_oct)
    if idx < len(sc):
        return sc[idx] - base + 12 * oct_shift
    return 12 * (int(deg) // per_oct)


# ------------------------------------------------------------------ 张力曲线

def tension_series(density: Sequence[float], register: Sequence[float],
                   instability: Sequence[float], activity: Sequence[float],
                   weights: Optional[Dict[str, float]] = None) -> List[float]:
    """把每小节的四项客观特征合成为 0–1 的张力值。

    density      发音密度（每拍音符数，已归一）
    register     音区偏离中心程度（0–1）
    instability  和声不稳定度（离调/减七/属七越多越高）
    activity     节奏活跃度（最短时值越小越高）
    """
    w = {'density': 0.30, 'register': 0.22, 'instability': 0.28,
         'activity': 0.20}
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w})
    n = max(len(density), len(register), len(instability), len(activity))
    out = []
    for i in range(n):
        def g(seq, dflt=0.0):
            return float(seq[i]) if i < len(seq) else dflt
        v = (w['density'] * _clamp(g(density), 0, 1)
             + w['register'] * _clamp(g(register), 0, 1)
             + w['instability'] * _clamp(g(instability), 0, 1)
             + w['activity'] * _clamp(g(activity), 0, 1))
        out.append(round(_clamp(v, 0.0, 1.0), 4))
    return out


def smooth(xs: Sequence[float], k: int = 3) -> List[float]:
    """居中移动平均，消除逐小节抖动（张力是"听感"量，不该逐格跳变）。"""
    if not xs:
        return []
    k = max(1, int(k))
    if k == 1:
        return [float(x) for x in xs]
    half = k // 2
    out = []
    for i in range(len(xs)):
        lo, hi = max(0, i - half), min(len(xs), i + half + 1)
        out.append(round(sum(xs[lo:hi]) / (hi - lo), 4))
    return out


def dramatic_arc(n_bars: int, climax_bar: int, intensity: float = 1.0,
                 resolution: float = 0.35) -> List[float]:
    """目标张力曲线：起势 → 爬升 → 高潮 → 回落收束。

    高潮前用较陡的指数爬升，高潮后快速泄压但保留一个"余韵"台阶，
    末段回到 resolution（避免结尾比开头还响）。
    """
    if n_bars <= 0:
        return []
    c = int(_clamp(climax_bar, 1, n_bars))
    out = []
    for i in range(1, n_bars + 1):
        if i <= c:
            # 从 0.25 爬到 1.0：越接近高潮越陡
            r = (i - 1) / max(1, c - 1)
            v = 0.25 + 0.75 * (r ** 1.6)
        else:
            r = (i - c) / max(1, n_bars - c)
            v = 1.0 - (1.0 - resolution) * (r ** 0.75)
        out.append(round(_clamp(0.15 + (v - 0.15) * _clamp(intensity, 0, 1.6),
                                0.0, 1.0), 4))
    return out


def locate_climax(actual: Sequence[float], sections: Optional[Sequence[Dict[str, Any]]] = None,
                  bias: float = 0.72, exclude_tail: int = 2) -> int:
    """定位高潮小节。

    先看客观张力的峰值；若峰值落在尾声（最后 exclude_tail 小节）或开头，
    就用「结构偏置」把它拉到全曲 bias 处——结尾的强奏是收束不是高潮。
    """
    if not actual:
        return 1
    n = len(actual)
    tail_guard = max(0, n - max(1, exclude_tail))
    cand = [i for i in range(1, n + 1)
            if not (sections and _is_coda(sections, i))]
    if not cand:
        cand = list(range(1, n + 1))
    in_range = [i for i in cand if 2 <= i <= tail_guard]
    pool = in_range or cand
    peak = max(pool, key=lambda i: actual[i - 1])
    # 客观峰值太靠近开头（缺乏铺垫）→ 用 bias 位置
    if peak <= max(2, int(n * 0.25)):
        return int(_clamp(round(n * bias), 1, n))
    return peak


def _is_coda(sections: Sequence[Dict[str, Any]], bar: int) -> bool:
    for s in sections:
        try:
            if int(s.get('start', 0)) <= bar <= int(s.get('end', 0)):
                nm = str(s.get('name') or '')
                return any(k in nm for k in ('尾', '收', 'Coda', 'coda', 'CODA'))
        except Exception:
            continue
    return False


# ------------------------------------------------------------------ 叙事计划

def build_plan(*, bars: int, sections: Optional[Sequence[Dict[str, Any]]] = None,
               actual: Optional[Sequence[float]] = None,
               seed: str = 'seed', intensity: float = 1.0,
               motif: Optional[Motif] = None,
               surprises: int = 1, silence: bool = True,
               scale: Optional[Sequence[int]] = None,
               base_dynamics: Optional[Dict[int, str]] = None,
               appearances: bool = True) -> Dict[str, Any]:
    """生成完整叙事计划。

    bars       总小节数
    sections   段落表（含 start/end/name/dynamic）
    actual     客观张力（每小节）；不给则用目标曲线代替
    seed       决定惊喜位置、变形选择等随机项
    intensity  戏剧强度（0.6=克制，1.0=标准，1.4=夸张）
    motif      主导动机；不给则只做力度/演奏法规划
    surprises  惊喜和弦数量（0 关闭）
    silence    高潮后是否插入静默（留白）
    appearances 是否用「动机变形出场」重写高音声部（倒影/逆行/增时/减时/加花）。
                置 False 则只做力度弧线/演奏法/惊喜和弦/留白，旋律保持作曲家写下的
                形态 —— 明确「不要赋格/不要复调模仿/不要卡农」的作品应关掉。
    """
    bars = max(1, int(bars))
    sections = list(sections or [])
    rng = _rng(seed, 'narrative')
    intensity = _clamp(float(intensity), 0.4, 1.6)

    target_actual = list(actual or [])
    if len(target_actual) < bars:
        target_actual = dramatic_arc(bars, max(1, int(bars * 0.72)), intensity)
    target_actual = smooth(target_actual[:bars])

    climax = locate_climax(target_actual, sections)
    target = dramatic_arc(bars, climax, intensity)

    log: List[str] = []
    log.append(f'高潮定位：第 {climax} 小节'
               f'（客观张力 {target_actual[climax - 1]:.2f}，'
               f'目标 {target[climax - 1]:.2f}）')

    # ---- 力度计划 ----
    dynamics: Dict[int, str] = {}
    for b in range(1, bars + 1):
        base = (base_dynamics or {}).get(b) or _section_dynamic(sections, b) or 'mf'
        bi = _DYN_INDEX.get(str(base).strip(), 3)
        t = target[b - 1]
        idx = int(round(bi + (t - 0.5) * 4.0 * intensity))
        idx = int(_clamp(idx, 0, len(_DYN_LEVELS) - 1))
        dynamics[b] = _DYN_LEVELS[idx]

    # ---- 演奏法计划 ----
    marks: Dict[int, str] = {}
    for b in range(1, bars + 1):
        if b == climax:
            marks[b] = 'accent'
        elif abs(b - climax) == 1:
            marks[b] = 'tenuto'
        elif target[b - 1] > 0.72:
            marks[b] = 'tenuto'
        elif target[b - 1] < 0.30:
            marks[b] = 'legato'

    # ---- 动机出场时间表 ----
    appearances_plan: List[Dict[str, Any]] = []
    registry = MotifRegistry()
    if motif is not None and appearances:
        registry.register(motif, 'subject')
        appearances_plan = _appearance_plan(bars, sections, climax, rng,
                                            registry, motif, scale)

    # ---- 惊喜注入 ----
    sup: List[Dict[str, Any]] = []
    n_sup = max(0, min(int(surprises), 3))
    if n_sup:
        sup = _surprise_plan(bars, climax, n_sup, rng, log)

    # ---- 峰值后静默 ----
    sil: List[Dict[str, Any]] = []
    if silence and bars >= 8 and climax + 1 <= bars:
        b = min(climax + 1, bars)
        beats = 1.0 if intensity < 1.1 else 1.5
        sil.append({'bar': b, 'beats': beats,
                    'reason': '高潮后留白：让峰值被"听见"，也给出句读'})
        log.append(f'静默：第 {b} 小节留白 {beats} 拍')

    return {
        'bars': bars,
        'climax_bar': climax,
        'intensity': round(float(intensity), 3),
        'tension_actual': target_actual,
        'tension_target': target,
        'dynamics': dynamics,
        'marks': marks,
        'appearances': appearances_plan,
        'surprises': sup,
        'silences': sil,
        'motif_registry': registry.as_dict(),
        'log': log,
        'seed': str(seed),
    }


def _section_dynamic(sections: Sequence[Dict[str, Any]], bar: int) -> Optional[str]:
    for s in sections:
        try:
            if int(s.get('start', 0)) <= bar <= int(s.get('end', 0)):
                return str(s.get('dynamic') or '') or None
        except Exception:
            continue
    return None


def _appearance_plan(bars: int, sections: Sequence[Dict[str, Any]], climax: int,
                     rng: random.Random, registry: MotifRegistry, motif: Motif,
                     scale: Optional[Sequence[int]]) -> List[Dict[str, Any]]:
    """出场时间表：把全曲切成「呈示 / 发展 / 推进 / 再现 / 尾声」若干槽位，
    每个槽位指定动机以何种变形出现。"""
    out: List[Dict[str, Any]] = []
    if bars < 4:
        return out

    # 槽位划分（按到高潮的相对位置）
    def role_for(b: int) -> str:
        r = b / max(1, bars)
        if b <= max(1, int(bars * 0.18)):
            return 'statement'
        if abs(b - climax) <= 1:
            return 'stretto' if r > 0.6 else 'buildup'
        if r >= 0.85:
            return 'coda'
        if climax and b > climax:
            return 'recap'
        return 'development' if r < 0.55 else 'buildup'

    step = max(2, bars // max(3, min(6, bars // 3)))
    b = 1
    first = True
    while b <= bars:
        role = role_for(b)
        picks = ROLE_TRANSFORMS.get(role, ('original',))
        kind = picks[0] if first else str(rng.choice(picks))
        if role == 'statement' and not first:
            kind = 'transpose'
        m = registry.derive(motif, kind, role=role, scale=scale, degrees=4)
        out.append({'bar': b, 'role': role, 'transform': kind,
                    'motif': m.name,
                    'note': _ROLE_NOTE.get(role, '')})
        first = False
        b += step
    return out


_ROLE_NOTE = {
    'statement': '原型呈示',
    'answer': '答题（移位）',
    'development': '发展（变形）',
    'buildup': '推进（模进/增时）',
    'stretto': '密接（减时/碎片）',
    'recap': '再现（原型回归）',
    'coda': '尾声（碎片收束）',
}


def _surprise_plan(bars: int, climax: int, n: int, rng: random.Random,
                   log: List[str]) -> List[Dict[str, Any]]:
    """惊喜：放在高潮**之前**的爬升段（先制造意外，再兑现高潮），
    或放在再现处做"回眸"。不放结尾——结尾需要稳定。"""
    out: List[Dict[str, Any]] = []
    if bars < 6:
        return out
    # 候选区：高潮前 1–3 小节；以及高潮后 2–4 小节
    cand = [b for b in range(max(2, climax - 3), climax)
            if 1 <= b <= bars]
    cand += [b for b in range(climax + 2, min(bars, climax + 5))
             if 1 <= b <= bars]
    cand = sorted({b for b in cand if 1 <= b <= bars - 1})
    rng.shuffle(cand)
    for b in cand[:n]:
        pool = [c for c in SURPRISE_CHORDS]
        weights = [c[2] for c in pool]
        total = sum(weights)
        x = rng.random() * total
        acc = 0.0
        pick = pool[0]
        for c in pool:
            acc += c[2]
            if x <= acc:
                pick = c
                break
        out.append({'bar': b, 'roman': pick[0], 'kind': pick[0],
                    'note': pick[1], 'weight': pick[2]})
        log.append(f'惊喜：第 {b} 小节 {pick[0]}（{pick[1]}）')
    return out


# ------------------------------------------------------------------ 计划应用

def plan_summary(plan: Dict[str, Any]) -> str:
    """给 UI / 报告用的一句话摘要。"""
    if not plan:
        return '（无叙事计划）'
    bars = int(plan.get('bars') or 0)
    climax = int(plan.get('climax_bar') or 0)
    apps = plan.get('appearances') or []
    roles = []
    seen = set()
    for a in apps:
        r = str(a.get('role') or '')
        if r and r not in seen:
            seen.add(r)
            roles.append(_ROLE_NOTE.get(r, r))
    sup = plan.get('surprises') or []
    sil = plan.get('silences') or []
    parts = [f'{bars} 小节，高潮在第 {climax} 小节']
    if roles:
        parts.append('动机出场：' + ' → '.join(roles))
    if sup:
        parts.append('惊喜 ' + '、'.join(
            f"第{s['bar']}小节 {s['roman']}" for s in sup))
    if sil:
        parts.append('静默 ' + '、'.join(
            f"第{s['bar']}小节 {s['beats']}拍" for s in sil))
    return '；'.join(parts)


def ascii_curve(plan: Dict[str, Any], width: int = 48) -> str:
    """在终端/报告里画一条 ASCII 张力曲线（不依赖任何绘图库）。"""
    tgt = plan.get('tension_target') or []
    if not tgt:
        return ''
    act = plan.get('tension_actual') or tgt
    n = len(tgt)
    lo, hi = 0.0, 1.0
    rows = 8
    grid = [[' '] * n for _ in range(rows)]
    for i in range(n):
        v = float(tgt[i])
        r = int(round((hi - v) / (hi - lo) * (rows - 1)))
        grid[_clamp_int(r, 0, rows - 1)][i] = '#'
        a = float(act[i]) if i < len(act) else v
        ra = _clamp_int(int(round((hi - a) / (hi - lo) * (rows - 1))), 0, rows - 1)
        if grid[ra][i] == ' ':
            grid[ra][i] = '.'
    body = '\n'.join(''.join(row) for row in grid)
    axis = ''.join('^' if (i + 1) == plan.get('climax_bar') else '-'
                   for i in range(n))
    return f'{body}\n{axis}\n（# 目标张力曲线，. 客观张力，^ 高潮）'


def _clamp_int(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else (hi if v > hi else v)
