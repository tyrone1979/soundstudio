# -*- coding: utf-8 -*-
"""终止式「其他终止」成因复核。

背景
----
``learn.py::_cadence_kind`` 用一本教科书判定表给 287 条终止式打类型，
其中 35 条被打成「其他终止」（判定表判不出来）。本脚本检验一个具体假设：

    这 35 条的成因只有两种——
    (A) **调性分析错误**：语料分析阶段 ``analyze.py`` 对每首曲子只做一次
        全曲级 ``analyze('key')``，之后所有和弦都按\ **这一个主音**\ 转调内坐标。
        乐句若转调/离调，其和弦罗马数字整体错位，判定表自然认不出。
    (B) **半终止判定过窄**：判定表的 HC 分支写作
        ``fin[0] == 7 and fin[1] == 'M'``，只认原位大三和弦 V，
        漏掉 V7、小调自然小属 v、以及带转位的属和弦。

两条都是**判定表的可修复缺陷**，不是"只能人工标注"的语义歧义。

方法
----
对 35 条的每一次真实出现（共 85 次），做三轨独立检验：

1. **轨 A 局部调性**：用 music21 对乐句窗口重新估调，与主调比较；
2. **轨 B 候选调枚举**：枚举 12 个主音，看终和弦能否解释为该调的 I 或 V
   （不依赖 music21 的短窗分析，纯和声功能推理）；
3. **轨 C 放宽的半终止**：在原调下把 HC 判据放宽到 V / V7 / v 及转位。

任一轨给出明确类型即视为**可自动解决**。

用法
----
::

    venv/bin/python -m score_learning.grammar.cadence_recheck
    venv/bin/python -m score_learning.grammar.cadence_recheck --json
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_YAML = os.path.join(ROOT, 'skills', 'composition', 'genre', 'packs',
                            'sacred', 'chorale', 'grammar.yaml')
CACHE = os.path.join(ROOT, 'runs', 'grammar_cache', 'bach_chorales.json')
OCC = os.path.join(ROOT, 'runs', 'experiments', 'cadence_occurrences.json')
OUT = os.path.join(ROOT, 'runs', 'experiments', 'cadence_recheck.json')


# ------------------------------------------------------------------ 工具
def _parse(s: str) -> Tuple[int, str, int]:
    from score_learning.grammar.theory import parse_chord_key
    return parse_chord_key(s)


def _rel_of(ch: Tuple[int, str, int], tonic_pc: int, global_pc: int) -> Tuple[int, str, int]:
    """把调内坐标的和弦换到另一主音下（和弦存储是 rel，先还原绝对再转）。"""
    r, q, inv = ch
    return ((r + global_pc - tonic_pc) % 12, q, inv)


def _roman(ch: Tuple[int, str, int], mode: str):
    from score_learning.grammar.theory import roman
    return roman(ch, mode)


def _dominant_of(ch: Tuple[int, str, int], mode: str) -> bool:
    """该和弦是不是某调的属（V / V7 / v / 属转位）。"""
    r, q, _inv = ch
    if r != 7:
        return False
    return q in ('M', '7', 'm', 'M7')


def _tonic_of(ch: Tuple[int, str, int], mode: str) -> bool:
    r, q, _inv = ch
    if r != 0:
        return False
    return q in ('M', 'm')


def _is_secondary_dominant(ch: Tuple[int, str, int], mode: str) -> bool:
    """该和弦是不是本调的副属 V/X 或副导 vii°/X。

    判据与 ``theory.roman`` 标注副属的判据保持一致：非调内的大三／属七
    和弦，且其"下五度目标"落在本调音阶内。这样判据可复现、不靠枚举。
    """
    from score_learning.grammar.theory import _is_diatonic_chord, scale_of
    r, q, _inv = ch
    if q not in ('M', '7'):
        return False
    if r == 0 and q != '7':
        return False
    if _is_diatonic_chord(r, q, mode):
        return False
    return (r + 5) % 12 in scale_of(mode)


def _sd_target(ch: Tuple[int, str, int]) -> int:
    return (ch[0] + 5) % 12


def cadence_kind_v2(chs: List[str], sop: List[int], mode: str,
                    deg_name=None) -> str:
    """修复版判定表。相对 ``learn._cadence_kind`` 只增三处，且都可证伪：

    **A 半终止放宽**：原判定 ``fin[0]==7 and fin[1]=='M'`` 只认原位大三 V，
    漏掉 V7 与小调自然小属 v。放宽为 ``q in ('M','7','m')``。

    **B 副属半终止**：终和弦本身是副属 V/X（非调内大三／属七）时，乐句停在
    X 的属上，是**向 X 的半终止**。判据复用 ``theory.roman`` 的副属判据，
    不枚举候选调。

    **C 副调正格终止**：若把坐标平移到某个调内音级 T 后，终和弦成为该调的
    主和弦，且前两拍之一成为该调的属或副导，则乐句**已在 T 上完全终止**。
    要求属—主关系真实存在（不是任意平移），故不会像枚举那样乱命中。
    """
    from score_learning.grammar.theory import scale_of
    chs3 = chs[-3:]
    pen = _parse(chs3[1])
    fin = _parse(chs3[2])
    pre = _parse(chs3[0])
    pen_dom = (pen[0] - fin[0]) % 12 == 7 and pen[1] in ('M', '7')
    pen_lt = (pen[0] - fin[0]) % 12 == 11 and pen[1] in ('d', 'o7', 'h7')

    # ---- 原有：主调内以主和弦收束
    if fin[0] == 0 and fin[1] in ('M', 'm'):
        if pen_dom and fin[2] == 0 and pen[2] == 0 and sop[1] == 0:
            return '完全正格终止 PAC'
        if pen_dom or pen_lt:
            return '不完全正格终止 IAC'
        if (pen[0] - fin[0]) % 12 == 5:
            return '变格终止 PC'
        return '主和弦终止'
    # ---- 修复 A：半终止放宽（V / V7 / 小调 v）
    if fin[0] == 7 and fin[1] in ('M', '7', 'm'):
        return '半终止 HC'
    if pen[0] == 7 and pen[1] in ('M', '7') and fin[0] in (8, 9):
        return '阻碍终止 DC'
    if pen_dom or pen_lt:
        return '离调终止（副属→目标）'
    # ---- 修复 B：停在副属上 = 向该副调的半终止
    if _is_secondary_dominant(fin, mode):
        return '离调半终止（停于副属）'
    # ---- 修复 C：向副调的正格终止（真实属—主关系）
    for cand in scale_of(mode):
        if cand == 0:
            continue
        fin_r = (fin[0] - cand) % 12
        if fin_r != 0 or fin[1] not in ('M', 'm'):
            continue
        for m2 in (mode, 'minor' if mode == 'major' else 'major'):
            for src in (pen, pre):
                sr = (src[0] - cand) % 12
                is_dom = (sr == 7 and src[1] in ('M', '7'))
                is_lt = (sr == 11 and src[1] in ('d', 'o7', 'h7'))
                if is_dom or is_lt:
                    return '转调终止（离调后完全终止）'
    return '其他终止'


def kind_relaxed(chs: List[str], sop: List[int], mode: str) -> str:
    """放宽 HC 判据后的判定表（在原调坐标下）。

    与原版唯一差别：HC 分支接受 V / V7 / v 及任意转位。
    """
    from score_learning.grammar.learn import _cadence_kind
    base = _cadence_kind(chs, sop, mode)
    if base != '其他终止':
        return base
    fin = _parse(chs[2])
    pen = _parse(chs[1])
    if _dominant_of(fin, mode):
        # 属和弦收束：半终止（不论七和弦、小属、转位）
        if _tonic_of(pen, mode):
            return '半终止 HC'
        return '半终止 HC'
    # 属七未解决落别处：仍算半终止方向的属收束
    if fin[1] in ('7', 'M7') and (fin[0] - pen[0]) % 12 in (5, 7):
        return '半终止 HC'
    return '其他终止'


def kind_in_key(chs: List[str], sop: List[int], new_tonic_rel: int,
                mode: str) -> Optional[str]:
    """把三和弦平移到「以 new_tonic_rel 为主音」的坐标系后重新判定。

    new_tonic_rel：新主音在**原调坐标**下的 rel 值。
    """
    from score_learning.grammar.learn import _cadence_kind
    from score_learning.grammar.theory import chord_key
    moved = [chord_key((( _parse(c)[0] - new_tonic_rel) % 12,
                        _parse(c)[1], _parse(c)[2])) for c in chs]
    sop_m = [(s - new_tonic_rel) % 12 for s in sop]
    try:
        return _cadence_kind(moved, sop_m, mode)
    except Exception:                                     # noqa: BLE001
        return None


# ------------------------------------------------------------------ 出现定位
_PIECES = None
_VSET: Dict[str, set] = {}


def _corpus():
    """（缓存）加载分析缓存，并按 learn.py:162-193 的同条件算出各调式词表。"""
    global _PIECES
    if _PIECES is None:
        from score_learning.learn_core import is_heldout
        _PIECES = json.load(open(CACHE, encoding='utf-8'))['pieces']
        for mode in ('major', 'minor'):
            tr = [p for p in _PIECES
                  if p['mode'] == mode and not is_heldout(p['id'])]
            cc: Counter = Counter(b['chord'] for p in tr for b in p['beats']
                                  if b['chord'])
            _VSET[mode] = {c for c, k in cc.items() if k >= 8}
    return _PIECES


def locate_occurrences(chs: List[str], sop: List[int], mode: str
                       ) -> List[Dict[str, Any]]:
    """按 learn.py 训练分支的**完全相同的过滤条件**复算出现位置。

    返回 [{'piece','start','end','phrase_kth','phrase_total'}]。
    """
    from score_learning.learn_core import is_heldout
    pieces = _corpus()
    vset = _VSET.get(mode, set())
    want_chs = tuple(chs)
    want_sop = tuple(sop)
    out = []
    for p in pieces:
        if p['mode'] != mode or is_heldout(p['id']):
            continue
        b = p['beats']
        tpc = p['tonic_pc']
        nph = len(p['phrases'])
        for k, ph in enumerate(p['phrases']):
            e = ph['end']
            if e - 2 < ph['start']:
                continue
            got = tuple(b[i]['chord'] for i in (e - 2, e - 1, e))
            if got != want_chs or not all(c in vset for c in got):
                continue
            sp = tuple((b[i]['midi'][0] - tpc) % 12 for i in (e - 1, e))
            if sp != want_sop:
                continue
            out.append({'piece': p['id'], 'start': ph['start'], 'end': e,
                        'phrase_kth': k + 1, 'phrase_total': nph})
    return out


# ------------------------------------------------------------------ 轨 A：局部调性
def local_key_of_window(piece: Dict[str, Any], start: int, end: int
                        ) -> Optional[Tuple[str, str, int]]:
    """用 music21 对 [start, end] 拍窗口估调 → (tonic名, mode, tonic_pc)。"""
    from music21 import stream as m21stream, note as m21note, duration as m21dur
    beats = piece['beats'][start:end + 1]
    sc = m21stream.Score()
    for vi in range(4):
        part = m21stream.Part()
        prev = None
        for b in beats:
            m = b['midi'][vi]
            if m is None:
                prev = None
                continue
            if prev is not None and prev['midi'] == m:
                prev['len'] += 1.0
                continue
            prev = {'midi': m, 'len': 1.0}
            n = m21note.Note(m)
            n.duration = m21dur.Duration(prev['len'])
            part.append(n)
        sc.insert(0, part)
    try:
        k = sc.analyze('key')
        from score_learning.grammar.theory import tonic_letter_pc
        _, pc = tonic_letter_pc(k.tonic.name.replace('-', 'b'))
        return k.tonic.name.replace('-', 'b'), ('minor' if k.mode == 'minor'
                                                else 'major'), pc
    except Exception:                                     # noqa: BLE001
        return None


# ------------------------------------------------------------------ 主流程
def recheck(save: bool = True) -> Dict[str, Any]:
    import yaml
    from score_learning.grammar.learn import _cadence_kind
    from score_learning.grammar.theory import roman

    d = yaml.safe_load(open(DEFAULT_YAML, encoding='utf-8'))
    cad = d.get('cadences') or {}
    occ_all = json.load(open(OCC, encoding='utf-8'))
    cache = json.load(open(CACHE, encoding='utf-8'))
    pmap = {p['id']: p for p in cache['pieces']}

    # 35 条：现有标注为「其他终止」的 C 规则
    unknown: List[Dict[str, Any]] = []
    for mode, targets in cad.items():
        for tgt, blk in targets.items():
            for f in (blk.get('forms') or []):
                if (f.get('type') or '').strip() == '其他终止':
                    unknown.append({'id': f['id'], 'mode': mode, 'target': tgt,
                                    'chords': f['chords'], 'soprano': f['soprano'],
                                    'n': f['n']})

    rows = []
    for u in unknown:
        occs = (occ_all.get(u['id']) or {}).get('occurrences') or []
        per_occ = []
        for o in occs:
            p = pmap.get(o['piece'])
            if p is None:
                continue
            gmode = p['mode']
            gpc = p['tonic_pc']
            s, e = o['start'], o['end']
            entry = {'piece': o['piece'], 'start': s, 'end': e,
                     'phrase_kth': o.get('phrase_kth'),
                     'phrase_total': o.get('phrase_total')}

            # 轨 A：music21 局部调性（终止三拍前的 6 拍 + 终止三拍）
            w0 = max(s, e - 5)
            lk = local_key_of_window(p, w0, e)
            if lk is None:
                lk = local_key_of_window(p, s, e)
            entry['local_key'] = lk[0] if lk else None
            entry['local_mode'] = lk[1] if lk else None
            entry['local_rel'] = (lk[2] - gpc) % 12 if lk else None
            entry['modulates'] = bool(lk and (lk[2] - gpc) % 12 != 0)

            # 轨 A′：在新调下重新判定
            if lk and entry['local_rel'] != 0:
                kk = kind_in_key(u['chords'], u['soprano'], entry['local_rel'],
                                 lk[1])
                entry['kind_in_local'] = kk
            else:
                entry['kind_in_local'] = None

            # 轨 B：枚举候选主音（纯和声推理，不看音级分布）
            best = None
            for cand in range(12):
                if cand == 0:
                    continue
                kmode = gmode                      # 先只试同调式；下面再试另一调式
                for mm in (gmode, 'minor' if gmode == 'major' else 'major'):
                    kk = kind_in_key(u['chords'], u['soprano'], cand, mm)
                    if kk and kk not in ('其他终止',):
                        # 只接受「明确终止类型」，不接受「主和弦终止」这种弱结论
                        if kk in ('完全正格终止 PAC', '半终止 HC',
                                  '不完全正格终止 IAC', '变格终止 PC',
                                  '阻碍终止 DC'):
                            best = (cand, mm, kk)
                            break
                if best:
                    break
            entry['enum_key'] = best

            # 轨 C：原调下放宽 HC
            entry['kind_relaxed'] = kind_relaxed(u['chords'], u['soprano'], gmode)
            per_occ.append(entry)

        # 汇总到规则级：任一次出现被解决即算可解决
        n_occ = len(per_occ)
        n_mod = sum(1 for x in per_occ if x['modulates'])
        n_relax_hc = sum(1 for x in per_occ
                         if x['kind_relaxed'] != '其他终止')
        n_enum = sum(1 for x in per_occ if x['enum_key'])
        n_local_fixed = sum(1 for x in per_occ
                            if x['kind_in_local'] and
                            x['kind_in_local'] != '其他终止')
        rows.append({
            'id': u['id'], 'mode': u['mode'], 'target': u['target'],
            'chords': u['chords'], 'soprano': u['soprano'], 'n': u['n'],
            'roman': ' – '.join(roman(_parse(c), u['mode']) for c in u['chords']),
            'n_occ': n_occ, 'n_modulates': n_mod,
            'n_local_fixed': n_local_fixed, 'n_enum_fixed': n_enum,
            'n_relaxed_hc': n_relax_hc,
            'resolved': bool(n_local_fixed or n_enum or n_relax_hc),
            'occ': per_occ,
        })

    n_res = sum(1 for r in rows if r['resolved'])
    n_hc = sum(1 for r in rows if r['n_relaxed_hc'])
    n_ton = sum(1 for r in rows if r['n_local_fixed'] or r['n_enum_fixed'])
    out = {
        'n_unknown_rules': len(rows),
        'n_occurrences': sum(r['n_occ'] for r in rows),
        'n_occ_modulating': sum(r['n_modulates'] for r in rows),
        'n_resolved': n_res,
        'n_by_tonality': n_ton,
        'n_by_relaxed_hc': n_hc,
        'n_still_unknown': len(rows) - n_res,
        'rows': rows,
    }
    if save:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump(out, open(OUT, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description='终止式「其他终止」成因复核')
    ap.add_argument('--json', action='store_true', help='输出完整 JSON')
    a = ap.parse_args(argv)
    r = recheck()
    print('=== 终止式「其他终止」成因复核 ===')
    print('  待复核规则：%d 条  真实出现：%d 次'
          % (r['n_unknown_rules'], r['n_occurrences']))
    print('  其中局部调性 ≠ 主调的出现：%d 次' % r['n_occ_modulating'])
    print()
    print('  可自动解决：%d / %d' % (r['n_resolved'], r['n_unknown_rules']))
    print('    由「调性分析错误」解释：%d 条' % r['n_by_tonality'])
    print('    由「半终止判定过窄」解释：%d 条' % r['n_by_relaxed_hc'])
    print('    仍无法解释：%d 条' % r['n_still_unknown'])
    print()
    for x in r['rows']:
        flag = '✓' if x['resolved'] else '?'
        print('  %s %-5s %-5s %-26s n=%-3d occ=%-3d 转调=%-3d 局部=%-3d 枚举=%-3d 宽HC=%-3d'
              % (flag, x['id'], x['mode'], x['roman'], x['n'], x['n_occ'],
                 x['n_modulates'], x['n_local_fixed'], x['n_enum_fixed'],
                 x['n_relaxed_hc']))
    print()
    print('  产物 → %s' % os.path.relpath(OUT, ROOT))
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != 'rows'},
                         ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
