# -*- coding: utf-8 -*-
"""确定性风格契约（Style Contract）—— 把风格从「提示词」变成「可执行参数」。

## 为什么要这一层

此前风格包（genre pack）里的数值只有一条真正的作用路径：

    meta.yaml → profile → serialize_profile() → LLM system prompt

即 `tempo.min=66` 这类本该被机器读取的数字，被序列化成 YAML 文本交给 LLM
自行领会。这带来三个问题：

1. **不确定**：同一个风格包两次生成结果可能不同
2. **不可验证**：无法回答「这首曲子到底符不符合众赞歌风格」
3. **学与用断链**：`score_learning` 学到的统计量写进 `empirical_statistics.json`
   侧车文件，没有任何确定性代码读取它

本模块把风格固化成一个**纯数值、机器可读的契约**，让内核确定性地消费它。

## 契约的三个原则

- **数值优先**：凡是能被程序消费的参数，一律用数值表达，不用自然语言
- **带证据**：每个字段标注 evidence_n（语料样本数）。样本不足的字段不生效，
  避免用 2 首曲子推断出的 genre 规律污染生成
- **确定性**：契约相同 + seed 相同 ⇒ 生成结果逐音符相同

## 数据流

    语料 → analyzer(features) → aggregate → build_contract() → style_contract.yaml
                                                                      ↓
    compose_kernel.new_score(style=...) ← load_contract() ────────────┘
                                                                      ↓
    score_studio 反向校验 style_check() ← compare_contract()
"""
from __future__ import annotations

import os
import random
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence

try:
    import yaml
except ImportError:      # pragma: no cover
    yaml = None

SCHEMA_VERSION = 'style-contract/v1'

CONTRACT_FILENAME = 'style_contract.yaml'

# 低于该样本量的字段视为证据不足，不做 Genre 推断（宁缺毋滥）
MIN_EVIDENCE_DEFAULT = 8


# --------------------------------------------------------------------------
# 和弦品质：体裁差异最集中体现的地方
# 内核 parse_roman 支持的后缀见 compose_kernel（'' 7 ° °7 ø7 + maj7）
# --------------------------------------------------------------------------
# 这里的 key 是「归一化的品质名」，与 analyzer 提取到的 music21 quality 对齐
QUALITY_KEYS = (
    'triad',        # 三和弦（大小/减，无七音）
    'dominant7',    # 大小七（属七）
    'major7',       # 大七
    'minor7',       # 小七
    'diminished7',  # 减七
    'half_diminished7',  # 半减七 ø7
    'augmented',    # 增三
    'other',
)

# music21 chord.quality 的取值 → 我们的归一化品质名
_M21_QUALITY_MAP = {
    'major': 'triad',
    'minor': 'triad',
    'diminished': 'triad',
    'augmented': 'augmented',
    'dominant-seventh': 'dominant7',
    'major-seventh': 'major7',
    'minor-seventh': 'minor7',
    'diminished-seventh': 'diminished7',
    'half-diminished-seventh': 'half_diminished7',
    'minor-major-seventh': 'other',
    'major-minor': 'dominant7',
}


def normalize_quality(raw: str) -> str:
    """把 music21 的 quality 字符串归一化到 QUALITY_KEYS。"""
    if not raw:
        return 'other'
    k = str(raw).strip().lower().replace('_', '-').replace(' ', '-')
    if k in _M21_QUALITY_MAP:
        return _M21_QUALITY_MAP[k]
    # 兜底：模糊匹配
    if 'major-seventh' in k or 'maj7' in k:
        return 'major7'
    if 'minor-seventh' in k or 'min7' in k:
        return 'minor7'
    if 'dominant' in k:
        return 'dominant7'
    if 'half-diminished' in k:
        return 'half_diminished7'
    if 'diminished-seventh' in k:
        return 'diminished7'
    if 'augmented' in k:
        return 'augmented'
    if 'major' in k or 'minor' in k or 'diminished' in k:
        return 'triad'
    return 'other'


# 品质名 → 内核 parse_roman 可识别的后缀
# （大写罗马级数用 maj7，小写留给 minor；这里只表达「音程结构」那一层）
QUALITY_SUFFIX = {
    'triad': '',
    'dominant7': '7',
    'major7': 'maj7',
    'minor7': '7',
    'diminished7': '°7',
    'half_diminished7': 'ø7',
    'augmented': '+',
    'other': '',
}


# --------------------------------------------------------------------------
# 契约数据结构
# --------------------------------------------------------------------------

@dataclass
class TempoSpec:
    """速度：全部数值，可直接钳制 spec['tempo']。"""
    default: Optional[int] = None
    min: Optional[int] = None
    max: Optional[int] = None
    source: str = 'unknown'      # expert | empirical | blend
    evidence_n: int = 0

    def is_usable(self, min_evidence: int = MIN_EVIDENCE_DEFAULT) -> bool:
        return (self.default is not None
                and (self.source == 'expert' or self.evidence_n >= min_evidence))

    def clamp(self, value: float) -> float:
        """把任意速度钳进 [min,max]；越界返回 default。"""
        v = float(value)
        lo = self.min if self.min is not None else 0
        hi = self.max if self.max is not None else 10 ** 9
        if v < lo:
            return float(self.default if self.default is not None else lo)
        if v > hi:
            return float(self.default if self.default is not None else hi)
        return v


@dataclass
class MeterSpec:
    """拍号：primary + 带权重的候选表（确定性抽样）。"""
    primary: Optional[str] = None
    allowed: List[str] = field(default_factory=list)
    weights: Dict[str, float] = field(default_factory=dict)
    source: str = 'unknown'
    evidence_n: int = 0

    def is_usable(self, min_evidence: int = MIN_EVIDENCE_DEFAULT) -> bool:
        return bool(self.allowed) and (
            self.source == 'expert' or self.evidence_n >= min_evidence)

    def pick(self, rng: random.Random, fallback: str = '4/4') -> str:
        """按语料分布确定性地抽一个拍号（同一 seed 结果一致）。"""
        if not self.weights:
            return self.primary or fallback
        items = [(m, float(w)) for m, w in self.weights.items() if float(w) > 0]
        if not items:
            return self.primary or fallback
        total = sum(w for _, w in items)
        if total <= 0:
            return self.primary or fallback
        r = rng.random() * total
        acc = 0.0
        for m, w in items:
            acc += w
            if r <= acc:
                return m
        return items[-1][0]


@dataclass
class HarmonySpec:
    """和声：和声节奏 + 品质分布 + 可选的和弦词汇表。

    quality_weights 是体裁差异的核心 —— 爵士大量七和弦，众赞歌几乎全是三和弦。
    """
    chords_per_bar: Optional[float] = None
    quality_weights: Dict[str, float] = field(default_factory=dict)
    unique_roots_mean: Optional[float] = None
    evidence_n: int = 0

    def is_usable(self, min_evidence: int = MIN_EVIDENCE_DEFAULT) -> bool:
        return bool(self.quality_weights) and self.evidence_n > 0

    def pick_quality(self, rng: random.Random,
                     allowed: Optional[Sequence[str]] = None) -> str:
        """按语料分布确定性地抽一个和弦品质名。"""
        pool = dict(self.quality_weights)
        if allowed:
            pool = {k: v for k, v in pool.items() if k in allowed}
        items = [(q, float(w)) for q, w in pool.items() if float(w) > 0]
        if not items:
            return 'triad'
        total = sum(w for _, w in items)
        r = rng.random() * total
        acc = 0.0
        for q, w in items:
            acc += w
            if r <= acc:
                return q
        return items[-1][0]

    def seventh_ratio(self) -> float:
        """七和弦类（含七音）占比 —— 判断「像不像爵士」的关键单一指标。"""
        if not self.quality_weights:
            return 0.0
        total = sum(float(v) for v in self.quality_weights.values()) or 1.0
        seventh = sum(
            float(v) for k, v in self.quality_weights.items()
            if k in ('dominant7', 'major7', 'minor7', 'diminished7',
                     'half_diminished7')
        )
        return round(seventh / total, 4)


@dataclass
class MelodySpec:
    """旋律形态目标：内核据此调整音区窗口与音程偏好。"""
    stepwise_ratio: Optional[float] = None
    leap_ratio: Optional[float] = None
    repeated_note_ratio: Optional[float] = None
    range_center: Optional[float] = None    # MIDI 均值含量 centering
    range_width: Optional[float] = None
    mean_midi: Optional[float] = None
    evidence_n: int = 0

    def is_usable(self, min_evidence: int = MIN_EVIDENCE_DEFAULT) -> bool:
        return self.evidence_n >= min_evidence and any(
            x is not None for x in (
                self.stepwise_ratio, self.leap_ratio, self.mean_midi,
                self.range_center,
            )
        )


@dataclass
class RhythmSpec:
    """节奏与密度目标。"""
    density_notes_per_bar: Optional[float] = None
    beat1_onset_share: Optional[float] = None
    syncopation: Optional[float] = None
    swing_ratio: Optional[float] = None     # >1 表示前长后短
    # 不等分八分占全部八分音对的比例。光有 swing_ratio 不够：整曲里偶然出现
    # 两三个不等分音型就会让比值成立，看起来像整曲都在 swing。
    swing_pair_share: Optional[float] = None
    triplet_ratio: Optional[float] = None
    evidence_n: int = 0

    def is_usable(self, min_evidence: int = MIN_EVIDENCE_DEFAULT) -> bool:
        return self.evidence_n >= min_evidence and any(
            x is not None for x in (
                self.density_notes_per_bar, self.beat1_onset_share,
                self.syncopation, self.swing_ratio,
            )
        )


@dataclass
class TextureSpec:
    """织体：声部数等。"""
    voice_count: Optional[float] = None
    evidence_n: int = 0

    def is_usable(self, min_evidence: int = MIN_EVIDENCE_DEFAULT) -> bool:
        return self.voice_count is not None and self.evidence_n >= min_evidence


@dataclass
class FormSpec:
    """曲式：句法长度与重复度。

    注意：这里**没有** A/A2/B 这种写死的段落名 —— 那是 analyzer 早期版本的
    伪造行为（273 首 jig 有 224 首得到完全相同的结构）。真实的可测量量只有
    「乐句长度」「动机重复比」这类统计量。
    """
    phrase_len: Optional[float] = None
    repetition_ratio: Optional[float] = None
    unique_motifs: Optional[float] = None
    evidence_n: int = 0

    def is_usable(self, min_evidence: int = MIN_EVIDENCE_DEFAULT) -> bool:
        return self.evidence_n >= min_evidence and any(
            x is not None for x in (
                self.phrase_len, self.repetition_ratio, self.unique_motifs,
            )
        )


@dataclass
class StyleContract:
    """一份完整的可执行风格契约。"""
    pack_id: str = ''
    family: str = ''
    schema: str = SCHEMA_VERSION
    evidence_n: int = 0              # 语料样本数
    evidence_weight: float = 1.0     # 0=纯专家 1=纯实证
    tempo: TempoSpec = field(default_factory=TempoSpec)
    meter: MeterSpec = field(default_factory=MeterSpec)
    harmony: HarmonySpec = field(default_factory=HarmonySpec)
    melody: MelodySpec = field(default_factory=MelodySpec)
    rhythm: RhythmSpec = field(default_factory=RhythmSpec)
    texture: TextureSpec = field(default_factory=TextureSpec)
    form: FormSpec = field(default_factory=FormSpec)
    provenance: Dict[str, Any] = field(default_factory=dict)

    # ---------- 序列化 ----------

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    def to_yaml_text(self) -> str:
        if yaml is None:
            raise RuntimeError('需要 PyYAML：pip install pyyaml')
        header = (
            '# 确定性风格契约 —— 数值由程序读取并约束生成，不是提示词。\n'
            f'# schema: {SCHEMA_VERSION}\n'
            '# evidence_n 为该字段的语料样本数；低于 min_evidence 的字段不生效。\n'
        )
        return header + yaml.dump(
            self.to_dict(), allow_unicode=True, default_flow_style=False,
            sort_keys=False,
        )

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.to_yaml_text())
        return path

    # ---------- 行为 ----------

    def summarize(self) -> str:
        bits = [f'<{self.pack_id or "anonymous"}>']
        if self.evidence_n:
            bits.append(f'n={self.evidence_n}')
        if self.harmony.is_usable():
            bits.append(f'七和弦占比={self.harmony.seventh_ratio():.0%}')
        if self.melody.stepwise_ratio is not None:
            bits.append(f'级进={self.melody.stepwise_ratio:.2f}')
        if self.tempo.default is not None:
            bits.append(f'♩={self.tempo.default}')
        if self.meter.primary:
            bits.append(f'{self.meter.primary}')
        return ' '.join(bits)

    def active_sections(self, min_evidence: int = MIN_EVIDENCE_DEFAULT) -> List[str]:
        """哪些分区真正会约束生成（用于诊断「风格到底生效了几项」）。"""
        out = []
        for name in ('tempo', 'meter', 'harmony', 'melody', 'rhythm',
                     'texture', 'form'):
            spec = getattr(self, name)
            try:
                if spec.is_usable(min_evidence):
                    out.append(name)
            except Exception:
                pass
        return out


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------

def _coerce(cls, data: Any, evidence_n: int = 0):
    if not isinstance(data, dict):
        return cls()
    known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
    obj = cls(**known)
    if getattr(obj, 'evidence_n', 0) == 0 and evidence_n:
        obj.evidence_n = evidence_n
    return obj


def load_contract(path: str) -> StyleContract:
    """从 style_contract.yaml 加载契约（缺文件返回空契约而非抛错）。"""
    if yaml is None:
        raise RuntimeError('需要 PyYAML：pip install pyyaml')
    if not os.path.isfile(path):
        return StyleContract()
    with open(path, encoding='utf-8') as f:
        d = yaml.safe_load(f) or {}
    return contract_from_dict(d)


def contract_from_dict(d: Dict[str, Any]) -> StyleContract:
    """从字典构建契约 —— 兼容任意缺失字段（向后/向前都安全）。"""
    d = dict(d or {})
    n = int(d.get('evidence_n') or 0)
    return StyleContract(
        pack_id=str(d.get('pack_id') or ''),
        family=str(d.get('family') or ''),
        schema=str(d.get('schema') or SCHEMA_VERSION),
        evidence_n=n,
        evidence_weight=float(d.get('evidence_weight') or 1.0),
        tempo=_coerce(TempoSpec, d.get('tempo'), n),
        meter=_coerce(MeterSpec, d.get('meter'), n),
        harmony=_coerce(HarmonySpec, d.get('harmony'), n),
        melody=_coerce(MelodySpec, d.get('melody'), n),
        rhythm=_coerce(RhythmSpec, d.get('rhythm'), n),
        texture=_coerce(TextureSpec, d.get('texture'), n),
        form=_coerce(FormSpec, d.get('form'), n),
        provenance=dict(d.get('provenance') or {}),
    )


# --------------------------------------------------------------------------
# 从语料特征构建契约 —— 这是「学习」的终点、「使用」的起点
# --------------------------------------------------------------------------

def _median(xs: Sequence[float]) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    mid = n // 2
    return round((xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0), 3)


def _mean(xs: Sequence[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return round(sum(xs) / len(xs), 3)


def _pct(xs: Sequence[float], q: float) -> Optional[float]:
    """分位数（线性插值），用于取速度区间而不是 min/max 这种对离群值敏感的写法。"""
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return round(float(xs[0]), 3)
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return round(float(xs[lo]) * (1 - frac) + float(xs[hi]) * frac, 3)


def _fnum(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def build_contract_from_features(
    features: List[Dict[str, Any]],
    pack_id: str = '',
    family: str = '',
    expert_profile: Optional[Dict[str, Any]] = None,
    min_evidence: int = MIN_EVIDENCE_DEFAULT,
) -> StyleContract:
    """把一批语料特征聚合成确定性风格契约。

    两条重要的取值原则：

    1. **取中位数而不是均值** —— 个别极端曲（如一首超快练习曲）会把均值拽偏，
       中位数对体裁中心的估计更稳。
    2. **证据不足的字段不填** —— evidence_n < min_evidence 的分区留空，
       运行时 `is_usable()` 为 False，不会约束生成。宁可少了几条规则，
       也不能用 2 首曲子推断出的「规律」去污染每一次创作。

    expert_profile 用于在实证不足时兜底（专家手写的数值，source='expert'）。
    """
    feats = [f for f in (features or [])
             if isinstance(f, dict) and not (f.get('metadata') or {}).get('error')]
    n = len(feats)
    if n == 0:
        c = StyleContract(pack_id=pack_id, family=family, evidence_n=0)
        _apply_expert(c, expert_profile)
        return c

    # ---- tempo ----
    tempos = [_fnum((f.get('global') or {}).get('tempo')) for f in feats]
    tempos = [t for t in tempos if t is not None and 20 <= t <= 260]
    tempo = TempoSpec(
        default=int(_median(tempos)) if tempos else None,
        min=int(_pct(tempos, 0.10)) if tempos else None,
        max=int(_pct(tempos, 0.90)) if tempos else None,
        source='empirical' if len(tempos) >= min_evidence else 'insufficient',
        evidence_n=len(tempos),
    )

    # ---- meter ----
    meters = Counter(str((f.get('global') or {}).get('meter') or '?')
                     for f in feats)
    wsum = sum(meters.values()) or 1
    weights = {m: round(c / wsum, 4) for m, c in meters.items()}
    primary = meters.most_common(1)[0][0] if meters else None
    meter = MeterSpec(
        primary=primary,
        allowed=list(meters.keys()),
        weights=weights,
        source='empirical' if n >= min_evidence else 'insufficient',
        evidence_n=n,
    )

    # ---- harmony：和弦品质是体裁的核心指纹 ----
    qual_acc: Dict[str, List[float]] = {k: [] for k in QUALITY_KEYS}
    harmonic_rates, seventh_rates, uniq_roots = [], [], []
    for f in feats:
        har = f.get('harmony') or {}
        if not har.get('available'):
            continue
        qh = har.get('quality_hist') or {}
        if isinstance(qh, dict) and qh:
            for k in QUALITY_KEYS:
                qual_acc[k].append(float(qh.get(k) or 0.0))
            # seventh_ratio 若已算出则直接用，否则由品质分布推
            sr = _fnum(har.get('seventh_ratio'))
            if sr is None:
                sr = sum(float(qh.get(k) or 0.0) for k in (
                    'dominant7', 'major7', 'minor7', 'diminished7',
                    'half_diminished7'))
            seventh_rates.append(sr)
        hr = _fnum(har.get('harmonic_rhythm_chords_per_bar'))
        if hr is not None:
            harmonic_rates.append(hr)
        ur = _fnum(har.get('unique_roots'))
        if ur is not None:
            uniq_roots.append(ur)
    n_h = len(seventh_rates)
    quality_weights = {}
    for k, vals in qual_acc.items():
        if not vals:
            continue
        m = _mean(vals)
        if m and m > 0.0005:
            quality_weights[k] = round(m, 4)
    harmony = HarmonySpec(
        chords_per_bar=_median(harmonic_rates),
        quality_weights=quality_weights,
        unique_roots_mean=_mean(uniq_roots),
        evidence_n=n_h,
    )

    # ---- melody ----
    melody = MelodySpec(
        stepwise_ratio=_mean([_fnum((f.get('melody') or {}).get('stepwise_ratio'))
                              for f in feats]),
        leap_ratio=_mean([_fnum((f.get('melody') or {}).get('leap_ratio'))
                          for f in feats]),
        repeated_note_ratio=_mean(
            [_fnum((f.get('melody') or {}).get('repeated_note_ratio'))
             for f in feats]),
        mean_midi=_median([_fnum((f.get('melody') or {}).get('mean_midi'))
                           for f in feats]),
        range_center=_median([_fnum((f.get('melody') or {}).get('mean_midi'))
                              for f in feats]),
        evidence_n=n,
    )

    # ---- rhythm + groove ----
    rhythm = RhythmSpec(
        density_notes_per_bar=_median(
            [_fnum((f.get('rhythm') or {}).get('density_notes_per_bar'))
             for f in feats]),
        beat1_onset_share=_mean(
            [_fnum((f.get('rhythm') or {}).get('beat1_onset_share'))
             for f in feats]),
        syncopation=_mean(
            [_fnum((f.get('rhythm') or {}).get('syncopation_proxy'))
             for f in feats]),
        swing_ratio=_median([_fnum((f.get('groove') or {}).get('swing_ratio'))
                             for f in feats]),
        swing_pair_share=_mean(
            [_fnum((f.get('groove') or {}).get('swing_pair_share'))
             for f in feats]),
        triplet_ratio=_mean([_fnum((f.get('groove') or {}).get('triplet_ratio'))
                             for f in feats]),
        evidence_n=n,
    )

    # ---- texture ----
    vcs = [_fnum((f.get('texture') or {}).get('voice_count')) for f in feats]
    vcs = [v for v in vcs if v is not None]
    texture = TextureSpec(voice_count=_median(vcs), evidence_n=len(vcs))

    # ---- form（只有可测量的统计量，不伪造段落名）----
    reps = [_fnum((f.get('form_metrics') or {}).get('repetition_ratio'))
            for f in feats]
    phrases = [_fnum((f.get('form_metrics') or {}).get('phrase_len'))
               for f in feats]
    mots = [_fnum((f.get('form_metrics') or {}).get('unique_motifs'))
            for f in feats]
    detected = [r for r in reps
                if r is not None and str(r) != 'nan']
    form = FormSpec(
        phrase_len=_median([p for p in phrases if p is not None]),
        repetition_ratio=_mean(detected),
        unique_motifs=_median([m for m in mots if m is not None]),
        evidence_n=len(detected),
    )

    c = StyleContract(
        pack_id=pack_id, family=family,
        evidence_n=n,
        evidence_weight=1.0,
        tempo=tempo, meter=meter, harmony=harmony, melody=melody,
        rhythm=rhythm, texture=texture, form=form,
        provenance={
            'built_from': 'features',
            'scores_analyzed': n,
            'scores_with_harmony': n_h,
            'min_evidence': min_evidence,
        },
    )
    _apply_expert(c, expert_profile)
    return c


def _apply_expert(c: StyleContract, expert: Optional[Dict[str, Any]]) -> None:
    """实证不足时用专家手写的数值兜底（标记 source='expert'）。

    专家值是写在 meta.yaml profile 里的人工知识，本来也是有效的确定性参数，
    只是此前被序列化成提示词浪费了。这里把它接回机器可读通道。
    """
    if not isinstance(expert, dict):
        return
    t = expert.get('tempo') if isinstance(expert.get('tempo'), dict) else {}
    if not c.tempo.is_usable() and any(
            t.get(k) is not None for k in ('default', 'min', 'max')):
        c.tempo = TempoSpec(
            default=_fnum(t.get('default')),
            min=_fnum(t.get('min')),
            max=_fnum(t.get('max')),
            source='expert', evidence_n=c.evidence_n,
        )
    m = expert.get('meter') if isinstance(expert.get('meter'), dict) else {}
    if not c.meter.is_usable() and m.get('primary'):
        allowed = list(m.get('allowed') or []) or [str(m['primary'])]
        c.meter = MeterSpec(
            primary=str(m['primary']),
            allowed=[str(x) for x in allowed],
            weights={str(x): 1.0 / max(len(allowed), 1) for x in allowed},
            source='expert', evidence_n=c.evidence_n,
        )


def resolve_contract(style: Any, packs_root: Optional[str] = None,
                     default_family: str = '') -> Optional[StyleContract]:
    """把 new_score 的 style 参数解析成契约。

    接受三种写法（都确定）：
      - dict            → 内联契约
      - 'sacred/chorale' 或 pack_id → 到 genre packs 下找 style_contract.yaml
      - StyleContract   → 原样返回
    """
    if style is None:
        return None
    if isinstance(style, StyleContract):
        return style
    if isinstance(style, dict):
        return contract_from_dict(style)
    if isinstance(style, str):
        s = style.strip()
        if not s:
            return None
        root = packs_root or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'skills', 'composition', 'genre', 'packs',
        )
        # family/pack 或裸 pack_id；裸 id 需要搜索各 family
        cands = []
        if '/' in s:
            fam, pid = s.split('/', 1)
            cands.append(os.path.join(root, fam, pid, CONTRACT_FILENAME))
        else:
            if default_family:
                cands.append(
                    os.path.join(root, default_family, s, CONTRACT_FILENAME))
            if os.path.isdir(root):
                for fam in sorted(os.listdir(root)):
                    cands.append(os.path.join(root, fam, s, CONTRACT_FILENAME))
        for p in cands:
            if not os.path.isfile(p):
                continue
            # 同目录已有可解释 grammar.yaml → 旧契约作废
            if os.path.isfile(os.path.join(os.path.dirname(p), 'grammar.yaml')):
                continue
            return load_contract(p)
        return None
    return None
