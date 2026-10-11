"""辅助作曲引擎：已写前缀 → 下一音排序 + 铺满剩余小节（纯程序，非 LLM）。

输入越多，参与预测的前缀越长；每次重算从光标到曲末的完整稿。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .editor_core import (
    Candidate, RuleEngine, SuggestionEngine, DUR_ORDER,
    key_tonic, pitch_midi, pitch_label, scale_pcs, scale_pitch_names,
)
from .models import (
    Accidental, ClefType, KeySignature, Measure, Note, NoteDuration, NoteName,
    Pitch, Score, Staff, TimeSignature,
)

# 灵感重排权重档（P3）：程序化换权重，不调 LLM
INSPIRATION_PROFILES: Dict[str, Dict[str, float]] = {
    'balanced': {
        'step': 1.0, 'chord': 1.0, 'leading': 1.0, 'leap_pen': 1.0,
        'motif': 1.0, 'repeat_pen': 1.0,
    },
    'stepwise': {
        'step': 1.6, 'chord': 0.9, 'leading': 1.2, 'leap_pen': 1.5,
        'motif': 0.8, 'repeat_pen': 1.1,
    },
    'jazzy': {
        'step': 0.85, 'chord': 1.5, 'leading': 0.7, 'leap_pen': 0.7,
        'motif': 1.1, 'repeat_pen': 0.9,
    },
    'angular': {
        'step': 0.55, 'chord': 1.1, 'leading': 0.8, 'leap_pen': 0.4,
        'motif': 1.3, 'repeat_pen': 1.2,
    },
    # 号角/分解和弦（小夜曲开篇 G5→D5 这类）
    'arpeggio': {
        'step': 0.75, 'chord': 1.7, 'leading': 0.8, 'leap_pen': 0.35,
        'motif': 1.2, 'repeat_pen': 1.0,
    },
}

# 默认隐式进行（无和声轨时）：大调 I–V–vi–IV
_DEFAULT_PROG_MAJOR = ['I', 'V', 'vi', 'IV']
_DEFAULT_PROG_MINOR = ['i', 'V', 'VI', 'iv']

# 罗马数字 → 相对主音的半音（大调音阶度数）
_ROMAN_DEG = {
    'I': 0, 'i': 0, 'II': 2, 'ii': 2, 'III': 4, 'iii': 4,
    'IV': 5, 'iv': 5, 'V': 7, 'v': 7, 'VI': 9, 'vi': 9,
    'VII': 11, 'vii': 11,
}


def blank_score(*, title: str = '新乐谱', bars: int = 16, tempo: int = 120,
                sharps: int = 0, meter: str = '4/4',
                composer: str = '', layout: str = 'single') -> Score:
    """带空小节网格的空白谱（可直接点写，不用先「加小节」）。

    layout:
      - ``single``：单行高音谱表（默认）
      - ``piano``：钢琴大谱表（RH 高音 + LH 低音，花括号）
    """
    try:
        num, den = str(meter).split('/')
        ts = TimeSignature(int(num), int(den))
    except Exception:
        ts = TimeSignature(4, 4)
    n_bars = max(1, int(bars))
    measures = [Measure(number=i + 1) for i in range(n_bars)]
    layout_key = str(layout or 'single').strip().lower()
    if layout_key in ('piano', 'grand', 'satb', 'four-part', 'four_part'):
        rh = Staff(clef=ClefType.TREBLE, name='RH',
                   measures=[Measure(number=i + 1) for i in range(n_bars)])
        lh = Staff(clef=ClefType.BASS, name='LH',
                   measures=[Measure(number=i + 1) for i in range(n_bars)])
        score = Score(title=title, composer=composer, tempo=int(tempo),
                      time_signature=ts, key_signature=KeySignature(int(sharps)),
                      staves=[rh, lh],
                      staff_groups=[{'parts': [0, 1], 'symbol': 'brace',
                                     'name': 'Piano'}])
        return score
    score = Score(title=title, composer=composer, tempo=int(tempo),
                  time_signature=ts, key_signature=KeySignature(int(sharps)))
    score.staves[0].measures = measures
    return score


def append_empty_measures(score: Score, count: int = 1) -> int:
    """在所有声部末尾追加空小节，返回追加后的总小节数。

    空小节与 ``blank_score`` 一致（无音符，可直接点写）。各声部长度对齐到最长。
    """
    if score is None or not score.staves:
        raise ValueError('没有可追加的谱表')
    n = max(1, min(32, int(count)))
    cur = max((len(s.measures) for s in score.staves), default=0)
    # 先把较短声部补齐到当前最长
    for staff in score.staves:
        while len(staff.measures) < cur:
            staff.measures.append(Measure(number=len(staff.measures) + 1))
    for _ in range(n):
        cur += 1
        for staff in score.staves:
            staff.measures.append(Measure(number=cur))
    # 统一编号
    for staff in score.staves:
        for i, m in enumerate(staff.measures):
            m.number = i + 1
    return cur

def _pc_of_pitch(p: Pitch) -> int:
    return pitch_midi(p) % 12


def _chord_pcs_from_roman(roman: str, tonic_pc: int, mode: str = 'major') -> List[int]:
    """极简罗马 → pitch-class（三和弦）。"""
    tok = str(roman or '').strip()
    # 剥后缀
    base = tok
    for suf in ('maj7', 'M7', 'ø7', '°7', 'dim7', '7', '°', 'ø', '+'):
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    base = base.lstrip('b#')
    deg = _ROMAN_DEG.get(base) or _ROMAN_DEG.get(base.upper()) or 0
    root = (tonic_pc + deg) % 12
    is_minor = bool(base) and base[0].islower()
    third = 3 if is_minor else 4
    if mode == 'minor' and base.upper() == 'I':
        third = 3
    return [root, (root + third) % 12, (root + 7) % 12]


def _melody_pitch_pool_pcs(chord_pcs, scale_pc_set):
    """与 compose_kernel._melody_pitch_pool 同口径（本地副本，避免循环依赖）。"""
    chord_set = {int(c) % 12 for c in chord_pcs}
    nb = set()
    for c in chord_set:
        for d in (-2, -1, 1, 2):
            p = (c + d) % 12
            if p in scale_pc_set and p not in chord_set:
                nb.add(p)
    return chord_set, nb


@dataclass
class AssistContext:
    staff_idx: int
    measure_idx: int
    insert_index: Optional[int]
    duration: NoteDuration
    sharps: int
    mode: str = 'major'
    remaining: float = 1.0
    prev: Optional[Note] = None
    back1: Optional[Note] = None
    prefix_midis: List[int] = field(default_factory=list)
    prefix_durs: List[NoteDuration] = field(default_factory=list)
    chord_pcs: List[int] = field(default_factory=list)
    scale_pc_set: set = field(default_factory=set)
    tonic_pc: int = 0
    style_id: str = ''
    weights: Dict[str, float] = field(default_factory=lambda: dict(
        INSPIRATION_PROFILES['balanced']))
    stepwise_bias: float = 0.5
    leap_bias: float = 0.2


class AssistEngine:
    """光标处下一音 / 短分支推荐。"""

    def __init__(self, rule_engine: Optional[RuleEngine] = None):
        self.rules = rule_engine or RuleEngine()
        self.fallback = SuggestionEngine(self.rules)
        # 会话旁路状态（由 UI / /assist 设置）
        self.enabled: bool = True
        self.style_id: str = ''
        self.mode: str = 'major'
        self.harmony: Dict[int, str] = {}          # 1-based bar → roman
        self.inspiration: str = 'balanced'
        self.phrase_limit: int = 5
        self.note_limit: int = 5
        self._last_notes: List[Candidate] = []
        self._last_phrases: List[Candidate] = []
        self._last_bar_fills: List[Candidate] = []
        self._last_full_fills: List[Candidate] = []
        self._ghost_index: int = 0
        self._last_prefix_labels: List[str] = []
        self._last_key_hint: str = ''
        self._grammar = None  # SubGrammar 缓存视图
        self.sub_id: str = ''

    # ---------- 公开 API ----------

    def set_inspiration(self, name: str) -> str:
        key = str(name or 'balanced').lower().strip()
        if key not in INSPIRATION_PROFILES:
            return f'未知灵感档 {name!r}（可用：{", ".join(INSPIRATION_PROFILES)}）'
        self.inspiration = key
        return f'灵感重排 → {key}'

    def set_harmony_bar(self, bar: int, roman: str):
        self.harmony[int(bar)] = str(roman).strip()

    def clear_harmony(self):
        self.harmony.clear()

    def inferred_harmony(self, bars: int, sharps: int = 0) -> Dict[int, str]:
        """无显式和声时的建议进行（灰字轨）。优先用 grammar.yaml 的 H 规则。"""
        g = self._grammar
        if g is not None:
            try:
                from .assist_grammar import chord_plan, tok_to_roman
                toks = chord_plan(g, bars, self.harmony)
                return {i + 1: tok_to_roman(g, t) for i, t in enumerate(toks)}
            except Exception:
                pass
        prog = list(_DEFAULT_PROG_MINOR if self.mode == 'minor'
                    else _DEFAULT_PROG_MAJOR)
        # 风格包 progressions 优先
        try:
            import composition_skills as skill_lib
            for g in skill_lib.list_genres():
                if (g.get('id') == self.style_id
                        or g.get('pack_id') == self.style_id
                        or str(g.get('id') or '').endswith('/' + self.style_id)):
                    raws = ((g.get('profile') or {}).get('harmony')
                            or {}).get('progressions') or []
                    if raws:
                        import re
                        toks = [t for t in re.split(r'[–—\-→>,，/]+',
                                                    str(raws[0])) if t.strip()]
                        if len(toks) >= 2:
                            prog = toks
                    break
        except Exception:
            pass
        out = {}
        for b in range(1, max(1, bars) + 1):
            out[b] = prog[(b - 1) % len(prog)]
        return out

    def harmony_for_bar(self, bar: int, n_bars: int = 16) -> str:
        if bar in self.harmony:
            return self.harmony[bar]
        return self.inferred_harmony(n_bars).get(bar, 'I')

    def suggest(self, score: Score, staff_idx: int, measure_idx: int,
                insert_index: Optional[int], duration: NoteDuration,
                limit: Optional[int] = None) -> List[Candidate]:
        if not self.enabled:
            return self.fallback.suggest(
                score, staff_idx, measure_idx, insert_index, duration,
                limit=limit or self.note_limit)
        ctx = self._build_context(score, staff_idx, measure_idx,
                                  insert_index, duration)
        if ctx is None:
            return []
        notes = self._rank_next(score, ctx, limit or self.note_limit)
        bar_fills: List[Candidate] = []
        full_fills: List[Candidate] = []
        if ctx.prefix_midis:
            bar_fills = self.predict_remaining(score, ctx, max_bars=1)
            full_fills = self.predict_remaining(score, ctx, max_bars=None)
        self._last_notes = notes
        self._last_bar_fills = bar_fills
        self._last_full_fills = full_fills
        new_keys = tuple((c.reason, c.label) for c in bar_fills)
        old_keys = tuple((c.reason, c.label) for c in (self._last_phrases or []))
        self._last_phrases = bar_fills
        if new_keys != old_keys:
            self._ghost_index = 0
        elif bar_fills:
            self._ghost_index = max(0, min(self._ghost_index, len(bar_fills) - 1))
        else:
            self._ghost_index = 0
        labels = []
        for m in ctx.prefix_midis[-8:]:
            p = _snap_midi(m, scale_pitch_names(ctx.sharps), None)
            labels.append(pitch_label(p) if p else str(m))
        self._last_prefix_labels = labels
        self._last_key_hint = f'sharps={ctx.sharps} mode={ctx.mode}'
        return notes

    def last_phrases(self) -> List[Candidate]:
        return list(self._last_phrases)

    def last_bar_fills(self) -> List[Candidate]:
        return list(getattr(self, '_last_bar_fills', self._last_phrases) or [])

    def last_full_fills(self) -> List[Candidate]:
        return list(getattr(self, '_last_full_fills', []) or [])

    def last_notes(self) -> List[Candidate]:
        return list(self._last_notes)

    def variants(self) -> List[Candidate]:
        """当前 ghost 可切换池（本小节多种可能性）。"""
        return list(self._last_phrases or [])

    def ghost_index(self) -> int:
        return int(self._ghost_index)

    def active_variant(self) -> Optional[Candidate]:
        vs = self.variants()
        if not vs:
            return None
        i = max(0, min(self._ghost_index, len(vs) - 1))
        return vs[i]

    def set_ghost_index(self, index: int) -> Optional[Candidate]:
        vs = self.variants()
        if not vs:
            self._ghost_index = 0
            return None
        self._ghost_index = max(0, min(int(index), len(vs) - 1))
        return vs[self._ghost_index]

    def prefer_ghost_index(self, index: int) -> Optional[Candidate]:
        """LLM 听审结果：仅改默认选中，不强制覆盖调用方锁定。"""
        return self.set_ghost_index(index)

    def prefix_labels(self) -> List[str]:
        return list(getattr(self, '_last_prefix_labels', []) or [])

    def key_hint(self) -> str:
        return str(getattr(self, '_last_key_hint', '') or '')

    # ---------- Context ----------

    def _build_context(self, score: Score, staff_idx: int, measure_idx: int,
                       insert_index: Optional[int],
                       duration: NoteDuration) -> Optional[AssistContext]:
        if not (0 <= staff_idx < len(score.staves)):
            return None
        staff = score.staves[staff_idx]
        if not (0 <= measure_idx < len(staff.measures)):
            return None
        measure = staff.measures[measure_idx]
        ts = measure.time_signature or score.time_signature
        sharps = (measure.key_signature or score.key_signature).sharps
        remaining = measure.remaining_beats(ts)
        dur = duration
        while dur.value > remaining + 1e-9 and dur != NoteDuration.THIRTY_SECOND:
            i = DUR_ORDER.index(dur)
            if i == len(DUR_ORDER) - 1:
                break
            dur = DUR_ORDER[i + 1]
        if remaining <= 1e-9:
            if measure_idx + 1 < len(staff.measures):
                return self._build_context(
                    score, staff_idx, measure_idx + 1, 0, duration)
            return None

        idx = insert_index if insert_index is not None else len(measure.notes)
        seq: List[Note] = []
        seq_all: List[Note] = []
        for mi in range(0, measure_idx + 1):
            notes = staff.measures[mi].notes
            if mi == measure_idx:
                notes = notes[:max(0, idx)]
            seq_all.extend(notes)
            seq.extend(n for n in notes if not n.is_rest)
        prev = seq[-1] if seq else None
        back1 = seq[-2] if len(seq) >= 2 else None
        prefix_midis = [pitch_midi(n.pitch) for n in seq]
        prefix_durs = [
            n.duration for n in seq_all
            if n.duration is not None
        ]

        tonic_name, tonic_acc = key_tonic(sharps)
        tonic_pc = (_pc_of_pitch(Pitch(tonic_name, 4, tonic_acc)))
        ts_s = f'{int(ts.numerator)}/{int(ts.denominator)}'
        self._bind_grammar(score, ts_s)
        roman = self.harmony_for_bar(measure_idx + 1, len(staff.measures))
        chord_pcs = _chord_pcs_from_roman(roman, tonic_pc, self.mode)
        sc = scale_pcs(sharps)
        if self._grammar is not None:
            try:
                from .assist_grammar import chord_plan, tok_to_pcs, complexity_scale
                toks = chord_plan(self._grammar, len(staff.measures), self.harmony)
                if 0 <= measure_idx < len(toks):
                    chord_pcs = tok_to_pcs(self._grammar, toks[measure_idx], tonic_pc)
                scale = complexity_scale(self._grammar)
                self.phrase_limit = max(3, min(8, int(round(3 + scale))))
            except Exception:
                pass

        weights = dict(INSPIRATION_PROFILES.get(
            self.inspiration, INSPIRATION_PROFILES['balanced']))
        stepwise_bias, leap_bias = 0.5, 0.2
        try:
            from style_contract import resolve_contract
            ct = resolve_contract(self.style_id) if self.style_id else None
            if ct is not None and getattr(ct, 'melody', None) is not None:
                m = ct.melody
                if getattr(m, 'stepwise_ratio', None) is not None:
                    stepwise_bias = float(m.stepwise_ratio)
                if getattr(m, 'leap_ratio', None) is not None:
                    leap_bias = float(m.leap_ratio)
            # jazz family → jazzy default unless user picked inspiration
            if (self.inspiration == 'balanced' and ct is not None
                    and str(getattr(ct, 'family', '')).lower() == 'jazz'):
                weights = dict(INSPIRATION_PROFILES['jazzy'])
            if (self.inspiration == 'balanced' and ct is not None
                    and str(getattr(ct, 'family', '')).lower()
                    in ('sacred', 'academic')):
                weights = dict(INSPIRATION_PROFILES['stepwise'])
        except Exception:
            pass

        return AssistContext(
            staff_idx=staff_idx, measure_idx=measure_idx,
            insert_index=insert_index, duration=dur, sharps=sharps,
            mode=self.mode, remaining=remaining, prev=prev, back1=back1,
            prefix_midis=prefix_midis, prefix_durs=prefix_durs,
            chord_pcs=chord_pcs,
            scale_pc_set=sc, tonic_pc=tonic_pc, style_id=self.style_id,
            weights=weights, stepwise_bias=stepwise_bias, leap_bias=leap_bias,
        )

    def _bind_grammar(self, score: Score, meter: str):
        try:
            from .assist_grammar import load_subgrammar
            n = len(score.staves) if score else 1
            packed = load_subgrammar(
                self.style_id, meter=meter, mode=self.mode, n_staves=n)
            if packed:
                self._grammar, _sk, self.sub_id = packed
            else:
                self._grammar = None
        except Exception:
            self._grammar = None

    # ---------- Rank next note ----------

    def _rank_next(self, score: Score, ctx: AssistContext,
                   limit: int) -> List[Candidate]:
        center = (pitch_midi(ctx.prev.pitch) if ctx.prev and not ctx.prev.is_rest
                  else 64)
        names = scale_pitch_names(ctx.sharps)
        pool: List[Pitch] = []
        for octv in range(2, 7):
            for nm, acc in names:
                p = Pitch(nm, octv, acc)
                if abs(pitch_midi(p) - center) <= 14:
                    pool.append(p)
        # 和弦音 / 邻音层
        chord_set, nb_set = _melody_pitch_pool_pcs(ctx.chord_pcs, ctx.scale_pc_set)
        w = ctx.weights
        cands: List[Candidate] = []

        def add(p: Pitch, reason: str, rank: float, reasons: Optional[List[str]] = None):
            for c in cands:
                if pitch_midi(c.pitch) == pitch_midi(p):
                    if rank > c.rank:
                        c.reason, c.rank = reason, rank
                        if reasons:
                            c.reasons = reasons
                    return
            v = self.rules.check_placement(
                score, ctx.staff_idx, ctx.measure_idx, p, ctx.duration,
                ctx.insert_index)
            if v.level == 'error':
                return
            if v.level == 'strict' and self.rules.strict:
                return
            if v.level == 'warn':
                rank *= 0.85
            cands.append(Candidate(
                pitch=p, duration=ctx.duration, reason=reason, rank=rank,
                kind='note', reasons=reasons or [reason], score=rank,
                preview_events=[(p, ctx.duration)],
            ))

        pm = pitch_midi(ctx.prev.pitch) if ctx.prev and not ctx.prev.is_rest else None
        bm = pitch_midi(ctx.back1.pitch) if ctx.back1 and not ctx.back1.is_rest else None

        for p in pool:
            m = pitch_midi(p)
            pc = m % 12
            score_v = 10.0
            reasons: List[str] = []

            # 导音解决
            if pm is not None and pc == ctx.tonic_pc and (m - pm) == 1:
                score_v += 40 * w.get('leading', 1.0)
                reasons.append('导音解决到主音')

            # 级进 / 跳进
            if pm is not None:
                d = abs(m - pm)
                if d in (1, 2):
                    score_v += 28 * w.get('step', 1.0) * (0.5 + ctx.stepwise_bias)
                    reasons.append('级进衔接')
                elif d == 0:
                    score_v -= 8 * w.get('repeat_pen', 1.0)
                    reasons.append('重复前音')
                elif d >= 5:
                    # 落到和弦音的四度/五度/八度是分解和弦，不应被级进档位淹没
                    pen = 12 * w.get('leap_pen', 1.0) * (0.5 + ctx.leap_bias)
                    if pc in chord_set and d in (5, 7, 8, 9, 12):
                        pen *= 0.2
                        score_v += 36 * w.get('chord', 1.0)
                        reasons.append('分解和弦')
                    else:
                        if d <= 9:
                            reasons.append('跳进色彩')
                    score_v -= pen
                elif d in (3, 4) and pc in chord_set:
                    score_v += 18 * w.get('chord', 1.0)
                    reasons.append('分解和弦')
                # 禁钟摆
                if bm is not None and m == bm and m != pm:
                    score_v -= 18
                # 主音后的属音（G5→D5）
                if pc == (ctx.tonic_pc + 7) % 12 and (pm % 12) == ctx.tonic_pc:
                    score_v += 24
                    reasons.append('主→属')

            # 和弦音 / 邻音
            if pc in chord_set:
                score_v += 22 * w.get('chord', 1.0)
                reasons.append('和弦音')
            elif pc in nb_set:
                score_v += 10 * w.get('chord', 1.0)
                reasons.append('调内邻音')

            # 动机回声：前缀里出现过的音程轮廓末端
            if len(ctx.prefix_midis) >= 2:
                last_iv = ctx.prefix_midis[-1] - ctx.prefix_midis[-2]
                if pm is not None and (m - pm) == last_iv:
                    score_v += 14 * w.get('motif', 1.0)
                    reasons.append('动机轮廓呼应')

            # 无前音：主/属/三
            if pm is None:
                iv = (pc - ctx.tonic_pc) % 12
                if iv == 0:
                    score_v += 35
                    reasons.append('从主音起步')
                elif iv == 7:
                    score_v += 25
                    reasons.append('从属音起步')
                elif iv in (3, 4):
                    score_v += 15
                    reasons.append('从中音起步')

            if not reasons:
                reasons.append('调内音')
            # 同和弦跳进时优先近的八度：G5 后 D5 应压过 D6
            if pm is not None:
                score_v -= 0.25 * abs(m - pm)
            add(p, reasons[0], score_v, reasons)

        cands.sort(key=lambda c: -c.rank)
        if self._grammar is not None:
            try:
                from .assist_grammar import rank_next, chord_plan
                from score_learning.leadsheet.analyze import strength as beat_strength
                g = self._grammar
                toks = chord_plan(
                    g, len(score.staves[ctx.staff_idx].measures), self.harmony)
                tok = toks[ctx.measure_idx] if ctx.measure_idx < len(toks) else toks[-1]
                ts = score.staves[ctx.staff_idx].measures[ctx.measure_idx].time_signature \
                    or score.time_signature
                on_ql = max(0.0, (ts.beats_per_measure() / 4.0 - ctx.remaining) * 4.0)
                st = beat_strength(on_ql, float(g.bar_ql), float(g.beat_ql))
                prev_m = (pitch_midi(ctx.prev.pitch)
                          if ctx.prev and not ctx.prev.is_rest else None)
                back_m = (pitch_midi(ctx.back1.pitch)
                          if ctx.back1 and not ctx.back1.is_rest else None)
                ranked = rank_next(
                    g, pool, prev=prev_m, back1=back_m, tok=tok, st=st,
                    tonic_pc=ctx.tonic_pc, limit=max(limit * 2, 8))
                by_midi = {pitch_midi(p): (sc, rs) for p, sc, rs in ranked}
                for c in cands:
                    extra = by_midi.get(pitch_midi(c.pitch))
                    if extra is None:
                        continue
                    sc, rs = extra
                    c.rank = c.rank + 14.0 * max(-1.0, sc + 4.0)
                    merged = list(dict.fromkeys((c.reasons or [c.reason]) + rs))
                    c.reasons = merged
                    prefer = next((x for x in merged if x in (
                        '导音解决到主音', '主→属', '分解和弦') or '导音' in x), None)
                    if prefer:
                        c.reason = prefer
                    c.score = c.rank
                cands.sort(key=lambda c: -c.rank)
            except Exception:
                pass
        return cands[:limit]

    # ---------- 前缀 → 铺满剩余小节 ----------

    def predict_remaining(self, score: Score, ctx: AssistContext,
                          max_bars: Optional[int] = None) -> List[Candidate]:
        """用已输入前缀预测铺满稿。max_bars=1 仅本小节，None 铺到曲末。"""
        grammar_fills = self._predict_with_grammar(score, ctx, max_bars)
        if grammar_fills:
            return grammar_fills
        staff = score.staves[ctx.staff_idx]
        names = scale_pitch_names(ctx.sharps)
        midis = list(ctx.prefix_midis)
        n_pre = len(midis)
        pattern_durs = list(ctx.prefix_durs) or [ctx.duration]
        if n_pre <= 1:
            variants = ['arpeggio', 'echo', 'cadence']
        elif n_pre == 2:
            variants = ['interval', 'arpeggio', 'echo']
        else:
            variants = ['interval', 'echo', 'cadence']
        scope = '本小节' if max_bars == 1 else '全曲'
        out: List[Candidate] = []
        for vi, style in enumerate(variants[: self.phrase_limit]):
            events = self._fill_from_prefix(
                score, ctx, staff, names, midis, pattern_durs, style,
                max_bars=max_bars)
            if len(events) < 1:
                continue
            first = next((e for e in events if e.get('pitch')), None)
            pitch0 = first['pitch'] if first else Pitch(NoteName.G, 4)
            dur0 = first['duration'] if first else ctx.duration
            out.append(Candidate(
                pitch=pitch0, duration=dur0,
                reason=f'前缀{n_pre}音·{scope}（{style}）',
                rank=100 - vi * 8 + min(n_pre, 12) * 3,
                kind='phrase',
                reasons=[f'已用前缀 {n_pre} 音', scope, style],
                score=100 - vi,
                preview_events=events,
            ))
        return out

    def _predict_with_grammar(self, score: Score, ctx: AssistContext,
                              max_bars: Optional[int]) -> List[Candidate]:
        g = self._grammar
        if g is None:
            return []
        try:
            from .assist_grammar import chord_plan, fill_remaining
        except Exception:
            return []
        staff = score.staves[ctx.staff_idx]
        names = scale_pitch_names(ctx.sharps)
        n_bars = len(staff.measures)
        n_pre = len(ctx.prefix_midis)
        remaining_ql = float(ctx.remaining) * 4.0
        durs_ql = [d.value * 4.0 for d in (ctx.prefix_durs or [])]
        toks = chord_plan(g, n_bars, self.harmony)
        scope = '本小节' if max_bars == 1 else '全曲'
        n_var = min(self.phrase_limit, 5)
        out: List[Candidate] = []
        for vi in range(n_var):
            try:
                events, label, rules = fill_remaining(
                    g, n_bars=n_bars, start_mi=ctx.measure_idx,
                    remaining_ql=remaining_ql, prefix_midis=list(ctx.prefix_midis),
                    prefix_durs_ql=durs_ql, toks=toks, tonic_pc=ctx.tonic_pc,
                    max_bars=max_bars, variant=vi, names=names)
            except Exception:
                continue
            notes_only = [e for e in events if not e.get('is_rest') and e.get('pitch')]
            if len(notes_only) < 1:
                continue
            first = notes_only[0]
            rid = next((r for r in rules if r and r != label), '')
            out.append(Candidate(
                pitch=first['pitch'], duration=first['duration'],
                reason=f'前缀{n_pre}音·{scope}（{label}）',
                rank=120 - vi * 7 + min(n_pre, 12) * 3,
                kind='phrase',
                reasons=[f'已用前缀 {n_pre} 音', scope, label, rid or 'grammar'],
                score=120 - vi,
                preview_events=events,
            ))
        return out

    def _fill_from_prefix(self, score, ctx, staff, names, midis, pattern_durs,
                          style: str, max_bars: Optional[int] = None) -> List[dict]:
        events: List[dict] = []
        start_mi = ctx.measure_idx
        ivs = [midis[i] - midis[i - 1] for i in range(1, len(midis))] if len(midis) >= 2 else []
        cur_midi = midis[-1] if midis else 67
        step_i = 0
        n_bars = len(staff.measures)
        end_mi = n_bars
        if max_bars is not None:
            end_mi = min(n_bars, start_mi + max(1, int(max_bars)))
        first_roman = self.harmony_for_bar(1, n_bars)
        first_root = _chord_pcs_from_roman(first_roman, ctx.tonic_pc, ctx.mode)[0]

        for mi in range(start_mi, end_mi):
            measure = staff.measures[mi]
            if mi > start_mi and measure.notes:
                break
            ts = measure.time_signature or score.time_signature
            rem = measure.remaining_beats(ts) if mi == start_mi else (
                ts.beats_per_measure() / 4.0)
            if rem <= 1e-9:
                continue
            roman = self.harmony_for_bar(mi + 1, n_bars)
            chord = _chord_pcs_from_roman(roman, ctx.tonic_pc, self.mode)
            durs = _durations_for_span(rem, pattern_durs)
            bar_off = mi - start_mi
            last_bar = mi == end_mi - 1
            for di, dur in enumerate(durs):
                is_last_evt = last_bar and di == len(durs) - 1
                is_penult = last_bar and di == len(durs) - 2
                dyn = None
                if di == 0 and bar_off == 0 and not events:
                    dyn = 'mf'
                marks: List[str] = []
                # cadence 休止只在「全曲」末尾前才有意义；单小节填满不插休止
                if (style == 'cadence' and max_bars is None and not last_bar
                        and di == len(durs) - 1 and dur.value <= 0.25):
                    events.append({
                        'kind': 'rest', 'pitch': None, 'duration': dur,
                        'marks': [], 'dynamic': dyn, 'bar_offset': bar_off,
                        'is_rest': True,
                    })
                    continue
                if style == 'interval' and ivs:
                    cur_midi = cur_midi + ivs[step_i % len(ivs)]
                elif style == 'echo' and midis:
                    src = midis[step_i % len(midis)]
                    root = chord[0]
                    delta = (root - first_root) % 12
                    if delta > 6:
                        delta -= 12
                    cur_midi = src + delta
                else:
                    last = cur_midi
                    options = []
                    for pc in chord:
                        for k in range(-3, 4):
                            m = last - (last % 12) + (int(pc) % 12) + 12 * k
                            if 48 <= m <= 88 and m != last:
                                options.append(m)
                    if not options:
                        options = [last]
                    def _arp_key(m, last=last):
                        iv = abs(m - last)
                        prefer = 0 if iv in (3, 4, 5, 7, 8, 12) else 2
                        down = 0 if m < last else 1
                        return (prefer, down, iv, m)
                    options.sort(key=_arp_key)
                    cur_midi = options[step_i % len(options)]
                # 全曲末小节才做 V→I；单小节铺满只跟当前和弦
                if max_bars is None:
                    if is_penult:
                        cur_midi = self._pc_to_near((ctx.tonic_pc + 7) % 12, cur_midi)
                    if is_last_evt:
                        cur_midi = self._pc_to_near(ctx.tonic_pc, cur_midi)
                pitch = _snap_midi(
                    cur_midi, names,
                    chord if not (max_bars is None and (is_last_evt or is_penult)) else None)
                if pitch is None:
                    pitch = Pitch(NoteName.G, 4)
                cur_midi = pitch_midi(pitch)
                if style == 'arpeggio' and di % 2 == 1:
                    marks = ['staccato']
                events.append({
                    'kind': 'note', 'pitch': pitch, 'duration': dur,
                    'marks': marks, 'dynamic': dyn, 'bar_offset': bar_off,
                    'is_rest': False,
                })
                step_i += 1
        return events

    @staticmethod
    def _pc_to_near(pc: int, around: int) -> int:
        best = around
        best_d = 99
        for o in range(2, 7):
            m = o * 12 + pc
            d = abs(m - around)
            if d < best_d:
                best, best_d = m, d
        return best


def _durations_for_span(remaining: float, pattern: List[NoteDuration]) -> List[NoteDuration]:
    out: List[NoteDuration] = []
    rem = float(remaining)
    i = 0
    guard = 0
    pat = pattern or [NoteDuration.QUARTER]
    while rem > 1e-9 and guard < 24:
        guard += 1
        d = pat[i % len(pat)]
        i += 1
        while d.value > rem + 1e-9 and d != NoteDuration.THIRTY_SECOND:
            ix = DUR_ORDER.index(d)
            if ix >= len(DUR_ORDER) - 1:
                break
            d = DUR_ORDER[ix + 1]
        if d.value > rem + 1e-9:
            break
        out.append(d)
        rem -= d.value
    return out


def _snap_midi(midi: int, names, chord_pcs=None) -> Optional[Pitch]:
    best = None
    best_d = 99.0
    target = int(round(midi))
    for octv in range(2, 7):
        for nm, acc in names:
            p = Pitch(nm, octv, acc)
            m = pitch_midi(p)
            d = float(abs(m - target))
            if chord_pcs is not None and (m % 12) not in set(int(c) % 12 for c in chord_pcs):
                d += 2.0
            if d < best_d:
                best, best_d = p, d
    return best
