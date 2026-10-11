# -*- coding: utf-8 -*-
"""风格插值 / 参数化风格合成（P0，弥补 Magenta Interpolate）。

在两个（或任意多个，两两递归）风格包的 profile 之间做**确定性混合**，
派生一个「中间风格」，然后直接复用 ``style_skeleton`` 的确定性骨架生成乐谱。

设计原则
--------
* 全程**无 LLM**、**无随机**（插值本身不含随机源），同一对包 + 同一 ``t`` +
  同一 ``seed`` → 同一首曲子（种子化可复现，契合护城河）。
* 可连续化的字段（速度、音区、力度、段落长度、织体密度）做**线性插值**；
  离散字段（拍号、节奏身份、伴奏型、调性偏好文本）按阈值在 A/B 间切换，
  主风格 A 占 ``t<=0.5``，副风格 B 占 ``t>0.5``，端点 ``t=0``/``t=1`` 严格等于原包。
* 文本画像（mode/color/summary/constraints）保留主风格 A 的内容，仅在
  ``llm_instruction`` 标注融合关系（确定性骨架不消费这些散文）。

典型用法
--------
    from style_interpolate import interpolate_spec, interpolate_genre
    spec = interpolate_spec('dance/waltz', 'intimate/nocturne', t=0.5, seed='demo')
    mixed = interpolate_genre('dance/waltz', 'intimate/nocturne', t=0.3)  # 偏向华尔兹
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional

from style_skeleton import (
    DYN_LADDER, resolve_genre, skeleton_spec, build_skeleton_calls, SkeletonError,
)

_DYN_SET = set(DYN_LADDER)
_NOTE_RE = re.compile(r'^([A-G])([#b]?)(-?\d+)$')
_PC = {'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3, 'E': 4,
       'F': 5, 'F#': 6, 'Gb': 6, 'G': 7, 'G#': 8, 'Ab': 8, 'A': 9,
       'A#': 10, 'Bb': 10, 'B': 11}
_NAME_BY_PC = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# 织体密度散文 → 数值（用于线性插值后再反向映射）
_DENS_MAP = {'transparent': 0.5, 'thin': 0.4, 'sparse': 0.3,
             'medium': 1.0, 'normal': 1.0, 'dense': 2.0, 'thick': 2.0}


# ============================================================
# 基础插值原语
# ============================================================

def clamp_t(t: float) -> float:
    return max(0.0, min(1.0, float(t)))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_note(s: Any) -> bool:
    return isinstance(s, str) and bool(_NOTE_RE.match(s.strip()))


def _note_to_midi(s: str) -> Optional[int]:
    m = _NOTE_RE.match(str(s).strip())
    if not m:
        return None
    letter, acc, octv = m.group(1), m.group(2), int(m.group(3))
    key = letter + acc
    pc = _PC[key] if key in _PC else _PC[letter]
    return pc + (octv + 1) * 12


def _midi_to_note(m: float) -> str:
    m = int(round(m))
    pc = m % 12
    octv = m // 12 - 1
    return f'{_NAME_BY_PC[pc]}{octv}'


def interp_scalar(a: Any, b: Any, t: float) -> Any:
    """数值→lerp；音名→音高 lerp；其余离散按 t 选 A/B。"""
    if _is_num(a) and _is_num(b):
        return lerp(a, b, t)
    if _is_note(a) and _is_note(b):
        return _midi_to_note(lerp(_note_to_midi(a), _note_to_midi(b), t))
    return a if t <= 0.5 else b


def interp_list(a: Optional[List], b: Optional[List], t: float, limit: int = 12) -> List:
    """合并去重，A 优先；B 的差异项补在后面（体现「融合」）。"""
    out: List = []
    seen = set()
    for x in (list(a or []) + list(b or [])):
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out[:limit]


def _dens_blend(a: Any, b: Any, t: float) -> Any:
    va = _DENS_MAP.get(str(a).lower()) if a is not None else None
    vb = _DENS_MAP.get(str(b).lower()) if b is not None else None
    if va is not None and vb is not None:
        v = lerp(va, vb, t)
        inv = {v2: k for k, v2 in _DENS_MAP.items()}  # {数值: 密度名}
        return min(inv.items(), key=lambda kv: abs(kv[0] - v))[1]
    return a if t <= 0.5 else b


# ============================================================
# 结构化字段混合（保持 profile 原始 schema）
# ============================================================

def interp_tempo(tp_a: Any, tp_b: Any, t: float) -> Dict[str, Any]:
    tp_a = tp_a if isinstance(tp_a, dict) else {}
    tp_b = tp_b if isinstance(tp_b, dict) else {}
    out: Dict[str, Any] = {}
    for k in ('default', 'min', 'max'):
        a, b = tp_a.get(k), tp_b.get(k)
        if _is_num(a) and _is_num(b):
            out[k] = int(round(lerp(a, b, t)))
        else:
            out[k] = a if t <= 0.5 else b
    for k in set(tp_a) | set(tp_b):
        if k not in out:
            out[k] = tp_a.get(k) if t <= 0.5 else tp_b.get(k)
    return out


def interp_dyn_block(dyn_a: Any, dyn_b: Any, t: float) -> Dict[str, Any]:
    dyn_a = dyn_a if isinstance(dyn_a, dict) else {}
    dyn_b = dyn_b if isinstance(dyn_b, dict) else {}
    out: Dict[str, Any] = {}
    da, db = dyn_a.get('default') or {}, dyn_b.get('default') or {}
    if isinstance(da, dict) and isinstance(db, dict):
        od: Dict[str, Any] = {}
        for k in ('min', 'max'):
            a, b = da.get(k), db.get(k)
            if a in _DYN_SET and b in _DYN_SET:
                od[k] = DYN_LADDER[int(round(
                    lerp(DYN_LADDER.index(a), DYN_LADDER.index(b), t)))]
            else:
                od[k] = a if t <= 0.5 else b
        out['default'] = od
    for k in set(dyn_a) | set(dyn_b):
        if k != 'default':
            out[k] = dyn_a.get(k) if t <= 0.5 else dyn_b.get(k)
    return out


def interp_register(reg_a: Any, reg_b: Any, t: float) -> Dict[str, Any]:
    reg_a = reg_a if isinstance(reg_a, dict) else {}
    reg_b = reg_b if isinstance(reg_b, dict) else {}
    out: Dict[str, Any] = {}
    for k in set(reg_a) | set(reg_b):
        a, b = reg_a.get(k), reg_b.get(k)
        if isinstance(a, dict) and isinstance(b, dict):
            sub: Dict[str, Any] = {}
            for kk in set(a) | set(b):
                av, bv = a.get(kk), b.get(kk)
                if _is_note(av) and _is_note(bv):
                    sub[kk] = _midi_to_note(lerp(_note_to_midi(av), _note_to_midi(bv), t))
                else:
                    sub[kk] = av if t <= 0.5 else bv
            out[k] = sub
        elif _is_note(a) and _is_note(b):
            out[k] = _midi_to_note(lerp(_note_to_midi(a), _note_to_midi(b), t))
        else:
            out[k] = a if t <= 0.5 else b
    return out


def interp_profile(prof_a: Any, prof_b: Any, t: float) -> Dict[str, Any]:
    """混合两个 profile（深拷贝 A 为基底，再覆盖可插值字段）。"""
    t = clamp_t(t)
    prof_a = prof_a if isinstance(prof_a, dict) else {}
    prof_b = prof_b if isinstance(prof_b, dict) else {}
    out: Dict[str, Any] = copy.deepcopy(prof_a)  # 文本字段（mode/color/summary）保留 A

    if 'tempo' in prof_a or 'tempo' in prof_b:
        out['tempo'] = interp_tempo(prof_a.get('tempo'), prof_b.get('tempo'), t)
    if 'dynamics' in prof_a or 'dynamics' in prof_b:
        out['dynamics'] = interp_dyn_block(prof_a.get('dynamics'), prof_b.get('dynamics'), t)
    if 'register' in prof_a or 'register' in prof_b:
        out['register'] = interp_register(prof_a.get('register'), prof_b.get('register'), t)

    # 段落长度（form.foundation_bars / full_bars_default）
    fa, fb = (prof_a.get('form') or {}), (prof_b.get('form') or {})
    if isinstance(fa, dict):
        of = copy.deepcopy(fa)
        for k in ('foundation_bars', 'full_bars_default'):
            a, b = fa.get(k), fb.get(k)
            if _is_num(a) and _is_num(b):
                of[k] = int(round(lerp(a, b, t)))
        out['form'] = of

    # generation.foundation.{bars,min,max,complexity}
    ga, gb = (prof_a.get('generation') or {}), (prof_b.get('generation') or {})
    if isinstance(ga, dict) and isinstance(gb, dict):
        gfa, gfb = (ga.get('foundation') or {}), (gb.get('foundation') or {})
        if isinstance(gfa, dict) and isinstance(gfb, dict):
            og = copy.deepcopy(ga)
            ogf = copy.deepcopy(gfa)
            for k in ('bars', 'min', 'max', 'complexity'):
                a, b = gfa.get(k), gfb.get(k)
                if _is_num(a) and _is_num(b):
                    ogf[k] = int(round(lerp(a, b, t)))
            og['foundation'] = ogf
            out['generation'] = og

    # harmony：合并 lists（A 优先）
    ha, hb = (prof_a.get('harmony') or {}), (prof_b.get('harmony') or {})
    if isinstance(ha, dict):
        oh = copy.deepcopy(ha)
        for k in ('vocabulary_basic', 'vocabulary_extended', 'progressions', 'forbidden'):
            if k in ha or k in hb:
                oh[k] = interp_list(ha.get(k), hb.get(k), t)
        out['harmony'] = oh

    # tonal_center：合并 preferred/secondary
    tca, tcb = (prof_a.get('tonal_center') or {}), (prof_b.get('tonal_center') or {})
    if isinstance(tca, dict):
        otc = copy.deepcopy(tca)
        for k in ('preferred', 'secondary'):
            if k in tca or k in tcb:
                otc[k] = interp_list(tca.get(k), tcb.get(k), t)
        out['tonal_center'] = otc

    # 离散字段：按 t 选 A/B
    if 'meter' in prof_a or 'meter' in prof_b:
        out['meter'] = (prof_a.get('meter') if t <= 0.5
                        else (prof_b.get('meter') or prof_a.get('meter')))

    ra, rb = (prof_a.get('rhythm') or {}), (prof_b.get('rhythm') or {})
    if isinstance(ra, dict):
        orh = copy.deepcopy(ra)
        orh['identity'] = ra.get('identity') if t <= 0.5 else rb.get('identity')
        aa, ab = (ra.get('accompaniment') or {}), (rb.get('accompaniment') or {})
        if isinstance(aa, dict):
            oacc = copy.deepcopy(aa)
            oacc['default'] = (aa.get('default') if t <= 0.5
                               else (ab.get('default') or aa.get('default')))
            orh['accompaniment'] = oacc
        out['rhythm'] = orh

    ta, tb = (prof_a.get('texture') or {}), (prof_b.get('texture') or {})
    if isinstance(ta, dict):
        otex = copy.deepcopy(ta)
        otex['density'] = _dens_blend(ta.get('density'), tb.get('density'), t)
        otex['primary'] = (ta.get('primary') if t <= 0.5
                           else (tb.get('primary') or ta.get('primary')))
        out['texture'] = otex

    return out


def interp_dimensions(d_a: Any, d_b: Any, t: float) -> Dict[str, Any]:
    """混合维度；离散维度按 t 选 A/B，数值维度 lerp。"""
    t = clamp_t(t)
    d_a = d_a if isinstance(d_a, dict) else {}
    d_b = d_b if isinstance(d_b, dict) else {}
    out: Dict[str, Any] = copy.deepcopy(d_a)
    for k in set(d_a) | set(d_b):
        a, b = d_a.get(k), d_b.get(k)
        if _is_num(a) and _is_num(b):
            out[k] = lerp(a, b, t)
        else:
            out[k] = a if t <= 0.5 else b
    return out


# ============================================================
# 对外 API
# ============================================================

def interpolate_genre(id_a: Any, id_b: Any, t: float = 0.5,
                      label: Optional[str] = None) -> Dict[str, Any]:
    """构造混合风格 dict（含 profile/dimensions/generation），可直接喂给骨架。

    id_a / id_b 可为风格包 id（如 ``'dance/waltz'``）、中文名或英文（走路由）。
    ``t=0`` → 纯 A；``t=1`` → 纯 B。
    """
    g_a = resolve_genre(id_a)
    g_b = resolve_genre(id_b)
    if g_a is None or g_b is None:
        missing = [x for x, g in ((id_a, g_a), (id_b, g_b)) if g is None]
        raise SkeletonError(f'风格插值失败，未找到风格包：{missing}')
    t = clamp_t(t)
    dims = interp_dimensions(g_a.get('dimensions'), g_b.get('dimensions'), t)
    prof = interp_profile(g_a.get('profile'), g_b.get('profile'), t)
    name_a = g_a.get('name') or g_a.get('pack_id') or str(id_a)
    name_b = g_b.get('name') or g_b.get('pack_id') or str(id_b)
    mixed: Dict[str, Any] = {
        'id': f"{g_a.get('id') or id_a}~{g_b.get('id') or id_b}",
        'pack_id': f"{g_a.get('pack_id') or ''}~{g_b.get('pack_id') or ''}",
        'name': label or f'{name_a}×{name_b}',
        'name_en': f"{(g_a.get('name_en') or '')}x{(g_b.get('name_en') or '')}",
        'dimensions': dims,
        'profile': prof,
        'generation': copy.deepcopy(g_a.get('generation') or prof.get('generation') or {}),
        'llm_instruction': (
            f"混合风格：以 {name_a} 为主体，融合 {name_b}（t={t:.2f}）。"
            f"已确定性混合 tempo/register/dynamics/音区/和声池/段落长度；"
            f"文本画像保留 {name_a}。"
        ),
    }
    return mixed


def interpolate_spec(id_a: Any, id_b: Any, t: float = 0.5, **kw) -> Dict[str, Any]:
    """混合风格 → spec（跑确定性内核，可传入 seed/bars/mood/tries/trace）。"""
    return skeleton_spec(interpolate_genre(id_a, id_b, t=t), **kw)


def interpolate_calls(id_a: Any, id_b: Any, t: float = 0.5, **kw) -> Dict[str, Any]:
    """混合风格 → 内核调用序列（不跑内核）。"""
    return build_skeleton_calls(interpolate_genre(id_a, id_b, t=t), **kw)
