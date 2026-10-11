"""编辑联想：用学到的 grammar.yaml 预测下一音 / 铺满本小节 / 铺满乐章。

只读规则脚本（和 /compose-grammar 同一套），不记具体曲子。
加载失败时调用方退回 AssistEngine 的启发式。
"""
from __future__ import annotations

import math
import os
import random
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .editor_core import pitch_midi
from .models import NoteDuration, NoteName, Pitch, TimeSignature

_PACK_TO_STYLE: Dict[str, str] = {}
_STYLE_PACK: Dict[str, str] = {}


def _index_styles():
    global _PACK_TO_STYLE, _STYLE_PACK
    if _PACK_TO_STYLE:
        return
    from score_learning.leadsheet.analyze import STYLES
    for key, meta in STYLES.items():
        fam, pack = meta['pack']
        path = f'{fam}/{pack}'
        _PACK_TO_STYLE[path] = key
        _PACK_TO_STYLE[pack] = key
        _PACK_TO_STYLE[key] = key
        _STYLE_PACK[key] = path


def infer_style_id(style_id: str, n_staves: int = 1) -> str:
    sid = (style_id or '').strip().lower()
    if sid.startswith('genre:'):
        sid = sid[6:]
    if sid:
        return sid
    if n_staves >= 6:
        return 'classical/symphony'
    if n_staves == 4:
        return 'classical/string-quartet'
    if n_staves == 2:
        return 'classical/piano-sonata'
    return 'folk/british-folk-tune'


def _meter_str(ts: Optional[TimeSignature]) -> str:
    if ts is None:
        return '4/4'
    return f'{int(ts.numerator)}/{int(ts.denominator)}'


def _pick_sub(script: Dict[str, Any], meter: str, mode: str) -> Optional[str]:
    from score_learning.leadsheet.analyze import _METER_SUB
    subs = script.get('subs') or {}
    prefer = _METER_SUB.get(meter)
    order = []
    if prefer and prefer in subs:
        order.append(prefer)
    for name, d in subs.items():
        if str(d.get('meter') or '') == meter:
            order.append(name)
    order.extend(subs.keys())
    seen = set()
    for name in order:
        if name in seen or name not in subs:
            continue
        seen.add(name)
        if mode in (subs[name].get('vocab') or {}):
            return name
    for name in order:
        if name in subs:
            return name
    return None


@lru_cache(maxsize=16)
def _load_script(style_key: str) -> Optional[Dict[str, Any]]:
    from score_learning.leadsheet import script as lscript
    yml, _ = lscript.paths(style_key)
    if not os.path.isfile(yml):
        return None
    return lscript.load(style_key)


def load_subgrammar(style_id: str, *, meter: str = '4/4', mode: str = 'major',
                    n_staves: int = 1):
    """返回 (SubGrammar, style_key, sub) 或 None。"""
    _index_styles()
    sid = infer_style_id(style_id, n_staves)
    style_key = _PACK_TO_STYLE.get(sid) or _PACK_TO_STYLE.get(sid.split('/')[-1])
    if not style_key:
        return None
    script = _load_script(style_key)
    if not script:
        return None
    mode = 'minor' if str(mode).lower().startswith('min') else 'major'
    sub = _pick_sub(script, meter, mode)
    if not sub:
        return None
    sub_modes = (script['subs'][sub].get('vocab') or {})
    if mode not in sub_modes:
        mode = 'major' if 'major' in sub_modes else next(iter(sub_modes), 'major')
    from score_learning.leadsheet.compose import SubGrammar
    try:
        g = SubGrammar(script, sub, mode)
    except Exception:
        return None
    return g, style_key, sub


def roman_to_tok(g, roman_s: str) -> Optional[str]:
    from score_learning.leadsheet.learn import tok_roman
    want = str(roman_s or '').strip()
    if not want:
        return None
    if ':' in want and want in g.vocab:
        return want
    for tok in g.vocab:
        if tok_roman(tok, g.mode) == want:
            return tok
    compact = want.replace('°', 'dim').replace('ø', 'h')
    for tok in g.vocab:
        if tok_roman(tok, g.mode).replace('°', 'dim') == compact:
            return tok
    return None


def tok_to_roman(g, tok: str) -> str:
    from score_learning.leadsheet.learn import tok_roman
    try:
        return tok_roman(tok, g.mode)
    except Exception:
        return tok


def tok_to_pcs(g, tok: str, tonic_pc: int) -> List[int]:
    from score_learning.leadsheet.learn import tok_pcs
    try:
        return [(tonic_pc + pc) % 12 for pc in tok_pcs(tok)]
    except Exception:
        return [tonic_pc, (tonic_pc + 4) % 12, (tonic_pc + 7) % 12]


def first_tok(g) -> str:
    fc = ((g.s.get('first_chord') or {}).get(g.mode) or [])
    if fc:
        return fc[0][0]
    return g.vocab[0]


def next_tok(g, p2: str, p1: str, bar_idx: int, n_bars: int) -> Tuple[str, str]:
    from score_learning.leadsheet.learn import pos_label
    pos = pos_label(bar_idx, bar_idx >= n_bars - 1, 0)
    best, best_lp, rid = g.vocab[0], -1e18, '-'
    for c in g.vocab:
        lp = g.trigram(p2, p1, pos, c)
        if lp > best_lp:
            best, best_lp = c, lp
    rid = g.rule_id(p2, p1, pos)
    return best, rid


def chord_plan(g, n_bars: int, explicit: Optional[Dict[int, str]] = None) -> List[str]:
    """1-based 小节 → 和弦 tok。显式罗马数字优先，其余用 H 规则贪心。"""
    n_bars = max(1, int(n_bars))
    toks: List[str] = []
    p2, p1 = 'BOS', 'BOS'
    explicit = explicit or {}
    for i in range(n_bars):
        forced = roman_to_tok(g, explicit.get(i + 1) or '')
        if forced:
            tok = forced
        elif i == 0:
            tok = first_tok(g)
        else:
            tok, _rid = next_tok(g, p2, p1, i, n_bars)
        toks.append(tok)
        p2, p1 = p1, tok
    return toks


def _ql_to_duration(ql: float) -> NoteDuration:
    target = float(ql) / 4.0
    best, best_d = NoteDuration.QUARTER, 1e9
    for d in NoteDuration:
        diff = abs(d.value - target)
        if diff < best_d:
            best, best_d = d, diff
    return best


def _strength_at(on_ql: float, bar_ql: float, beat_ql: float) -> str:
    from score_learning.leadsheet.analyze import strength
    return strength(on_ql, bar_ql, beat_ql)


def _leap_state(prev: Optional[int], cur: Optional[int]) -> int:
    if prev is None or cur is None:
        return 0
    d = cur - prev
    if d >= 5:
        return 1
    if d <= -5:
        return 2
    return 0


def score_pitch(g, midi: int, *, prev: Optional[int], back1: Optional[int],
                tok: str, st: str, tonic_pc: int, target: Optional[float] = None
                ) -> Tuple[float, List[str]]:
    """单音得分（越高越好）+ 中文理由。硬禁跳进返回 (-inf, …)。"""
    from score_learning.leadsheet.learn import note_class
    from score_learning.leadsheet.compose import leap_ok, CONTOUR_SIGMA

    reasons: List[str] = []
    if prev is not None and not leap_ok(g, prev, midi):
        return -1e9, ['禁用跳进']
    rel = int((midi - tonic_pc) % 12)
    cls = note_class(rel, tok, g.mode)
    lp = g.cls_lp.get(st) or g.cls_lp.get('W') or {}
    sc = float(lp.get(cls, math.log(1e-3)))
    if cls == 'CT':
        reasons.append('和弦音')
    elif cls == 'ST':
        reasons.append('调内邻音')
    else:
        reasons.append('半音经过')

    if prev is not None:
        iv = midi - prev
        sc += float(g.iv_lp.get(int(iv), -8.0))
        d = abs(iv)
        if d in (1, 2):
            reasons.append('级进衔接')
        elif d == 0:
            reasons.append('重复前音')
        elif d >= 5:
            reasons.append('跳进色彩')
        ls = _leap_state(back1, prev)
        if ls == 1 and iv < 0:
            sc += math.log(max(g.p_rec, 0.02))
            reasons.append('大跳回补')
        elif ls == 2 and iv > 0:
            sc += math.log(max(g.p_rec, 0.02))
            reasons.append('大跳回补')
        if rel == 0 and (midi - prev) == 1:
            sc += 1.2
            reasons.append('导音解决到主音')
        if rel == 0 and prev is not None and (prev % 12) == (tonic_pc + 11) % 12:
            sc += 0.8
            reasons.append('导音解决到主音')
    else:
        iv = rel
        if iv == 0:
            sc += 1.4
            reasons.append('从主音起步')
        elif iv == 7:
            sc += 0.9
            reasons.append('从属音起步')
        elif iv in (3, 4):
            sc += 0.5
            reasons.append('从中音起步')
        fd = {int(k): p for k, p, _n in (g.s.get('melody') or {}).get('first_degree') or []}
        sc += float(fd.get(rel, 0.0))

    if target is not None:
        sc -= 0.5 * ((midi - float(target)) / CONTOUR_SIGMA) ** 2
    if not reasons:
        reasons.append('语法规则')
    return sc, reasons


def rank_next(g, pool: Sequence[Pitch], *, prev: Optional[int], back1: Optional[int],
              tok: str, st: str, tonic_pc: int, limit: int) -> List[Tuple[Pitch, float, List[str]]]:
    scored = []
    for p in pool:
        sc, reasons = score_pitch(
            g, pitch_midi(p), prev=prev, back1=back1, tok=tok, st=st,
            tonic_pc=tonic_pc)
        if sc < -1e8:
            continue
        scored.append((p, sc, reasons))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]


def _pick_rhythm(g, rng: random.Random, bar_i: int, final: bool, remaining_ql: float) -> Tuple[str, str]:
    from score_learning.leadsheet.learn import rhythm_class
    from score_learning.leadsheet.compose import parse_pattern
    rh = g.s.get('rhythm') or {}
    cls = rhythm_class(bar_i % 8, final)
    items = ((rh.get('templates') or {}).get(cls) or {}).get('items') or []
    if not items:
        items = ((rh.get('templates') or {}).get('mid') or {}).get('items') or []
    bar_ql = float(g.bar_ql)
    span = min(max(remaining_ql, 0.25), bar_ql)
    ranked = []
    for it in items:
        pat = it.get('pattern') or '0n'
        evs = parse_pattern(pat, bar_ql)
        n_notes = sum(1 for _on, d, r in evs if not r and d <= span + 1e-6)
        if 1 <= n_notes <= 10:
            ranked.append((it.get('p', 0.01), it))
    if not ranked:
        ranked = [(it.get('p', 0.01), it) for it in items[:12]]
    if not ranked:
        return '0n', 'R-default'
    tot = sum(w for w, _ in ranked) or 1.0
    r = rng.random() * tot
    for w, it in ranked:
        r -= w
        if r <= 0:
            return it.get('pattern') or '0n', it.get('id') or 'R'
    return ranked[-1][1].get('pattern') or '0n', ranked[-1][1].get('id') or 'R'


def _contour_target(g, vi: int, bar_frac: float, center: float) -> float:
    tpls = (g.s.get('melody') or {}).get('contour_templates') or []
    if not tpls:
        return center
    t = tpls[vi % len(tpls)]
    shape = t.get('shape') or [0.0] * 8
    pos = max(0.0, min(1.0, bar_frac)) * (len(shape) - 1)
    i0 = int(pos)
    i1 = min(i0 + 1, len(shape) - 1)
    return center + shape[i0] + (shape[i1] - shape[i0]) * (pos - i0)


def _events_from_pattern(g, pattern: str, remaining_ql: float, start_on: float) -> List[Tuple[float, NoteDuration, bool]]:
    from score_learning.leadsheet.compose import parse_pattern
    bar_ql = float(g.bar_ql)
    out = []
    for on, d, rest in parse_pattern(pattern, bar_ql):
        if on + 1e-6 < start_on:
            continue
        if on >= start_on + remaining_ql - 1e-6:
            break
        take = min(d, start_on + remaining_ql - on)
        if take <= 1e-6:
            continue
        out.append((on - start_on, _ql_to_duration(take), bool(rest)))
    if not out:
        out.append((0.0, _ql_to_duration(min(remaining_ql, 1.0)), False))
    cap = 8
    notes = [x for x in out if not x[2]]
    if len(notes) < 2 and remaining_ql >= 1.0:
        step = 1.0 if remaining_ql >= 2 else 0.5
        out = []
        t = 0.0
        while t < remaining_ql - 1e-6 and len(out) < cap:
            take = min(step, remaining_ql - t)
            out.append((t, _ql_to_duration(take), False))
            t += take
    return out[:cap]


def fill_remaining(g, *, n_bars: int, start_mi: int, remaining_ql: float,
                   prefix_midis: List[int], prefix_durs_ql: List[float],
                   toks: List[str], tonic_pc: int, max_bars: Optional[int],
                   variant: int, names) -> Tuple[List[dict], str, List[str]]:
    """铺满从当前小节剩余拍到 max_bars（None=曲末）。"""
    rng = random.Random(17 + 91 * variant + 3 * len(prefix_midis) + start_mi)
    end_mi = n_bars if max_bars is None else min(n_bars, start_mi + max(1, int(max_bars)))
    center = prefix_midis[-1] if prefix_midis else 67
    cur = prefix_midis[-1] if prefix_midis else None
    back = prefix_midis[-2] if len(prefix_midis) >= 2 else None
    tpls = (g.s.get('melody') or {}).get('contour_templates') or []
    tname = (tpls[variant % len(tpls)]['name'] if tpls else '语法')
    rid_r = '-'
    events: List[dict] = []
    used_rules = [tname]

    for mi in range(start_mi, end_mi):
        rem = remaining_ql if mi == start_mi else float(g.bar_ql)
        if rem <= 1e-6:
            continue
        filled = float(g.bar_ql) - rem if mi == start_mi else 0.0
        final = mi == n_bars - 1
        pat, rid_r = _pick_rhythm(g, rng, mi, final, rem)
        used_rules.append(rid_r)
        tok = toks[mi] if mi < len(toks) else toks[-1]
        for on_rel, dur, is_rest in _events_from_pattern(g, pat, rem, filled):
            bar_off = mi - start_mi
            if is_rest:
                events.append({
                    'kind': 'rest', 'pitch': None, 'duration': dur,
                    'marks': [], 'dynamic': None, 'bar_offset': bar_off,
                    'is_rest': True,
                })
                continue
            on_ql = filled + on_rel
            st = _strength_at(on_ql, float(g.bar_ql), float(g.beat_ql))
            frac = (mi - start_mi + on_ql / float(g.bar_ql)) / max(1, end_mi - start_mi)
            target = _contour_target(g, variant, frac, float(center))
            best_p, best_sc = None, -1e18
            lo, hi = max(48, int(center) - 14), min(88, int(center) + 14)
            for m in range(lo, hi + 1):
                sc, _rs = score_pitch(
                    g, m, prev=cur, back1=back, tok=tok, st=st,
                    tonic_pc=tonic_pc, target=target)
                if max_bars is None and mi == end_mi - 1 and m % 12 == tonic_pc % 12:
                    sc += 0.6
                if sc > best_sc:
                    best_sc, best_p = sc, m
            if best_p is None:
                best_p = int(round(target))
            pitch = _snap(best_p, names)
            back, cur = cur, pitch_midi(pitch)
            events.append({
                'kind': 'note', 'pitch': pitch, 'duration': dur,
                'marks': [], 'dynamic': None, 'bar_offset': bar_off,
                'is_rest': False,
            })
        if max_bars == 1:
            break
    label = tname or 'grammar'
    return events, label, used_rules


def _snap(midi: int, names) -> Pitch:
    best = None
    best_d = 99.0
    target = int(round(midi))
    for octv in range(2, 7):
        for nm, acc in names:
            p = Pitch(nm, octv, acc)
            d = float(abs(pitch_midi(p) - target))
            if d < best_d:
                best, best_d = p, d
    return best or Pitch(NoteName.G, 4)


def complexity_scale(g) -> float:
    c = (g.s.get('complexity') or {})
    try:
        return float(c.get('scale') or 1.0)
    except Exception:
        return 1.0
