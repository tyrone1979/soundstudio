# -*- coding: utf-8 -*-
"""硬规则复核（R01–R09）：直接从输出的 spec 解码旋律，与作曲器内部状态无关。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .analyze import strength
from .learn import bar_cells, note_class, tok_chord

_DUR = {'whole': 4.0, 'dotted_half': 3.0, 'half': 2.0, 'dotted_quarter': 1.5, 'quarter': 1.0,
        'dotted_eighth': 0.75, 'eighth': 0.5, '16th': 0.25, '32nd': 0.125}


def _midi(name) -> Optional[int]:
    if name is None:
        return None
    from music21 import pitch
    return int(pitch.Pitch(str(name)).midi)


def decode_melody(events, pickup: float) -> List[List]:
    """spec 事件 → [t, dur, midi|None]，t 以第 1 小节强拍为 0（弱起音符为负）。"""
    out, t = [], -pickup
    for ev in events:
        d = _DUR[str(ev[1])]
        out.append([round(t, 4), d, _midi(ev[0])])
        t += d
    return out


def decode_part(events, pickup: float) -> List[List]:
    """spec 事件 → [t, dur, [midi…]]（只含发声事件；和弦事件展开成多个音高）。"""
    out, t = [], -pickup
    for ev in events:
        d = _DUR[str(ev[1])]
        if ev[0] is not None:
            ps = ev[0] if isinstance(ev[0], (list, tuple)) else [ev[0]]
            out.append([round(t, 4), d, [_midi(p) for p in ps]])
        t += d
    return out


def _sounding(line, t: float) -> List[int]:
    return [m for on, d, ms in line if on <= t + 1e-6 < on + d for m in ms]


def match_voices(before: List[int], after: List[int]):
    """按音高就近配对（与工作室报告的平行检测同一口径）。"""
    remaining = list(after)
    pairs = []
    for src in sorted(before):
        if not remaining:
            break
        dst = min(remaining, key=lambda n: (abs(n - src), n))
        remaining.remove(dst)
        pairs.append((src, dst))
    return pairs


def parallel_motion(a0: List[int], a1: List[int], b0: List[int], b1: List[int]) -> Optional[str]:
    for x0, x1 in match_voices(a0, a1):
        for y0, y1 in match_voices(b0, b1):
            if x1 == x0 or y1 == y0 or (x1 - x0) * (y1 - y0) <= 0:
                continue
            i0, i1 = (x0 - y0) % 12, (x1 - y1) % 12
            if i0 == i1 and i0 in (0, 7):
                return '八' if i0 == 0 else '五'
    return None


def timeline_parallels(li, lj, octaves: bool = True) -> List[float]:
    """两声部在相邻起音点之间同向进入纯五 / 纯八度的位置（拍）。"""
    out = []
    onsets = sorted({on for on, _d, _m in li} | {on for on, _d, _m in lj})
    for t0, t1 in zip(onsets, onsets[1:]):
        ni = [m for on, _d, ms in li if abs(on - t1) < 1e-6 for m in ms]
        nj = [m for on, _d, ms in lj if abs(on - t1) < 1e-6 for m in ms]
        if not ni or not nj:
            continue
        kind = parallel_motion(_sounding(li, t0), ni, _sounding(lj, t0), nj)
        if kind and (octaves or kind == '五'):
            out.append(t1)
    return out


def split_streams(line, max_voices: int = 2) -> List[List]:
    """钢琴谱表 → 高→低最多两条单声部流（与工作室报告拆钢琴叠写的口径一致）。"""
    n = max(1, min(max_voices, max((len(ms) for _o, _d, ms in line), default=1)))
    out = [[] for _ in range(n)]
    for on, d, ms in line:
        o = sorted(ms, reverse=True)
        for i in range(min(n, len(o))):
            out[i].append([on, d, [o[i]]])
    return out


def stream_parallels(lines: List[List], staff_of: List[int]) -> List[tuple]:
    """逻辑声部两两平行：跨谱表查五 + 八度，同谱表只查五度（八度加强不算）。
    → [(t, i, j)]"""
    out = []
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            for t in timeline_parallels(lines[i], lines[j], octaves=staff_of[i] != staff_of[j]):
                out.append((t, i, j))
    return out


TEXTURE_PARTS = {
    'melody_piano': ('旋律', '右手', '左手'),
    'piano': ('右手', '左手'),
    'quartet': ('第一小提琴', '第二小提琴', '中提琴', '大提琴'),
    'orchestra': ('长笛', '双簧管', '圆号', '大管', '定音鼓',
                  '第一小提琴', '第二小提琴', '中提琴', '大提琴'),
}
MELODY_PART = {'orchestra': 5}
# R08 只查独立声部：管弦编制里长笛 / 双簧管 / 大管是旋律或低音的加倍，定音鼓是打击乐
INDEPENDENT_PARTS = {'orchestra': (5, 6, 7, 8, 2)}
# 交叉检查的自上而下次序
STACK = {'orchestra': (5, 6, 7, 8)}


def melody_index(texture: str) -> int:
    return MELODY_PART.get(texture, 0)


def _rule_param(sub: Dict[str, Any], rule_id: str, key: str, default):
    """从 ``hard_rules[*].param`` 取阈值，取不到才退回默认值。

    这些阈值由 ``leadsheet/learn.py`` 随语料一起写进 YAML。**必须**优先读
    YAML：本文件早先把 0.70/0.85 各写了一份字面量，于是「只改 learn.py」
    对判定行为毫无影响。现默认常量只作旧 YAML 的兼容兜底。
    """
    for r in sub.get('hard_rules') or []:
        if r.get('id') != rule_id:
            continue
        p = r.get('param')
        if isinstance(p, dict) and key in p and p[key] is not None:
            return p[key]
        break
    return default


def _has_param(sub: Dict[str, Any], rule_id: str) -> bool:
    """该规则条目是否带机器可读 ``param``（旧 YAML 没有）。"""
    for r in sub.get('hard_rules') or []:
        if r.get('id') == rule_id:
            return isinstance(r.get('param'), dict)
    return False


def _notes_only(pat: str) -> str:
    return ' '.join(x for x in pat.split() if x.endswith('n'))


def check_piece(sub: Dict[str, Any], mode: str, spec: Dict[str, Any], slots: List[Dict],
                tonic_pc: int, rng_lo: int, rng_hi: int, attested) -> List[Dict[str, Any]]:
    """slots: [{'t', 'bar', 'tok', 'mid': 是否小节中途, 'bass': midi}]（t 同上）。"""
    v: List[Dict[str, Any]] = []
    bar_ql, beat_ql = sub['bar_ql'], sub['beat_ql']
    mel = sub['melody']
    hard = {r['id']: r['rule'] for r in sub['hard_rules']}
    cs = dict((k, p) for k, p, _n in mel['class_by_strength'].get('S', []))
    # 阈值来自 YAML（learn 侧写入），旧 YAML 无 param 时才退回常量
    strict_r01 = bool(_rule_param(sub, 'R01', 'strict', None)) if _has_param(
        sub, 'R01') else cs.get('CT', 0) >= _rule_param(
        sub, 'R01', 'strict_ct_threshold', 0.70)
    strict_r02 = bool(_rule_param(sub, 'R02', 'strict', None)) if _has_param(
        sub, 'R02') else mel['nct_step_either'] >= _rule_param(
        sub, 'R02', 'strict_step_threshold', 0.85)
    max_leap = _rule_param(sub, 'R03', 'max_leap', mel['max_leap'])
    forbidden = _rule_param(sub, 'R04', 'forbidden_leaps',
                            mel.get('forbidden_leaps') or [])
    pickup = float(spec.get('pickup_ql') or 0.0)
    texture = sub.get('texture', 'melody_piano')
    line = decode_melody(spec['parts'][melody_index(texture)]['events'], pickup)
    notes = [x for x in line if x[2] is not None]
    vocab = set(sub['vocab'][mode])

    def chord_at(t):
        cur = slots[0]['tok']
        for s in slots:
            if s['t'] <= t + 1e-6:
                cur = s['tok']
        return cur

    def add(rule, t, msg):
        v.append({'rule': rule, 'bar': int(t // bar_ql) + 1 if t >= 0 else 0, 'msg': msg})

    info = []
    for t, d, m in notes:
        tok = chord_at(t)
        rel = (m - tonic_pc) % 12
        st = strength(t % bar_ql, bar_ql, beat_ql) if t >= 0 else 'W'
        info.append((t, m, note_class(rel, tok, mode), st))
    for k, (t, m, c, st) in enumerate(info):
        prv = info[k - 1][1] if k else None
        nxt = info[k + 1][1] if k + 1 < len(info) else None
        step_in = prv is not None and 1 <= abs(m - prv) <= 2
        step_out = nxt is not None and 1 <= abs(nxt - m) <= 2
        if st == 'S' and c != 'CT':
            need = (c != 'CT') if strict_r01 else (c == 'CH')
            if need and not step_out:
                add('R01', t, f'强拍非和弦音 {c} 未级进解决')
        if (c != 'CT' if strict_r02 else c == 'CH') and not (step_in or step_out):
            add('R02', t, f'非和弦音 {c} 既非级进进入也非级进离开')
        if prv is not None:
            iv = abs(m - prv)
            if iv > max_leap:
                add('R03', t, f'跳进 {iv} 半音 > {max_leap}')
            if iv in forbidden:
                add('R04', t, f'罕见跳进 {iv} 半音')
        if not (rng_lo <= m <= rng_hi):
            add('R05', t, f'MIDI {m} 超出音域 {rng_lo}–{rng_hi}')
    if not notes or (notes[-1][2] - tonic_pc) % 12 != 0:
        add('R06', notes[-1][0] if notes else 0, '旋律末音不是主音')
    r, q = tok_chord(slots[-1]['tok'])
    if r != 0 or q not in ('M', 'm', 'M7', 'm7'):
        add('R06', slots[-1]['t'], '末和弦不是主和弦')
    for k, s in enumerate(slots):
        if s['tok'] not in vocab:
            add('R07', s['t'], f'和弦 {s["tok"]} 不在词表')
        if k and not attested(slots[k - 1]['tok'], s['tok']):
            add('R07', s['t'], f'和弦转移 {slots[k - 1]["tok"]} → {s["tok"]} 语料未见')
        if k and s['mid'] and slots[k - 1]['tok'] == s['tok']:
            add('R07', s['t'], '小节中途换和弦却与前一个和弦相同')

    max_run = _rule_param(sub, 'R10', 'max_same_chord_bars',
                          sub['form'].get('max_same_chord_bars'))
    if max_run:
        per_bar: Dict[int, List[str]] = {}
        for s in slots:
            per_bar.setdefault(s['bar'], []).append(s['tok'])
        run, prev = 0, None
        for bar in sorted(per_bar):
            toks = per_bar[bar]
            run = run + 1 if len(set(toks)) == 1 and prev == toks[0] else 1
            prev = toks[-1]
            if run > max_run:
                add('R10', (bar - 1) * bar_ql, f'同一和弦连续 {run} 小节 > {max_run}')

    # R08：各声部两两之间无平行五八度（钢琴谱表拆成高低两条流），伴奏不高过旋律
    names = TEXTURE_PARTS.get(texture, TEXTURE_PARTS['melody_piano'])
    lines = [decode_part(p['events'], pickup) for p in spec['parts']]
    streams, staff_of, label = [], [], []
    for pi in INDEPENDENT_PARTS.get(texture, range(len(lines))):
        ln = lines[pi]
        parts = split_streams(ln) if texture == 'piano' else [ln]
        for k, s_ in enumerate(parts):
            streams.append(s_)
            staff_of.append(pi)
            label.append(names[pi] + ('' if len(parts) == 1 else ('上声部', '下声部')[k]))
    for t, i, j in stream_parallels(streams, staff_of):
        add('R08', t, f'{label[i]}与{label[j]}平行五/八度')
    stack = list(STACK.get(texture, range(len(lines))))
    if texture == 'melody_piano':
        stack = stack[:2]
    if len(stack) >= 2:
        onsets = sorted({on for ln in lines for on, _d, _m in ln})
        for n in range(1, len(stack)):
            hi_i, lo_i = stack[n - 1], stack[n]
            # 伴奏首行（右手 / 第二小提琴）允许高出旋律大二度以内
            tol = 2 if n == 1 and texture != 'piano' else 0
            for t in onsets:
                hi_, lo_ = _sounding(lines[hi_i], t), _sounding(lines[lo_i], t)
                if hi_ and lo_ and min(hi_) + tol < max(lo_):
                    add('R08', t, f'{names[lo_i]}高过{names[hi_i]}')
                    break
    for s in slots:
        mb = next((m for nt, d, m in line if m is not None and nt <= s['t'] + 1e-6 < nt + d), None)
        if mb is not None and mb <= s['bass']:
            add('R08', s['t'], '低音高过旋律')
    # R09 节奏：只比较起音（时值无法记谱时作曲器会把尾巴写成休止）
    rh = sub['rhythm']
    n_bars = int(spec['bars'])
    by_bar = [[] for _ in range(n_bars)]
    for t, d, m in line:
        if t >= 0:
            bi = int(t // bar_ql + 1e-9)
            if bi < n_bars:
                by_bar[bi].append([abs(round(t - bi * bar_ql, 4)), d, 0, m])
    if rh['mode'] == 'bar':
        ok = {_notes_only(it['pattern']) for cls in rh['templates'].values() for it in cls['items']}
        for bi, ns in enumerate(by_bar):
            pat = _notes_only(' '.join(f'{on:g}{"r" if m is None else "n"}' for on, _d, _r, m in ns))
            if pat and pat not in ok:
                add('R09', bi * bar_ql, f'小节节奏 {pat} 不在模板表')
    else:
        ok_cells: Dict[str, set] = {}
        for key, dist in rh['cells']['by_beat'].items():
            ok_cells.setdefault(key.lstrip('F'), set()).update(_notes_only(c) for c, _p, _n in dist)
        for r_ in rh['cells']['by_prev']:
            ok_cells.setdefault(r_['beat'].lstrip('F'), set()).update(
                _notes_only(c) for c, _p, _n in r_['then'])
        for bi, ns in enumerate(by_bar):
            for k, cell in enumerate(bar_cells({'notes': ns}, bar_ql, beat_ql)):
                cn = _notes_only(cell)
                if cn and cn not in ok_cells.get(str(k), set()):
                    add('R09', bi * bar_ql + k * beat_ql, f'节奏单元 {cn} 不在转移表')
    for x in v:
        x['say'] = hard.get(x['rule'], '')
    return v
