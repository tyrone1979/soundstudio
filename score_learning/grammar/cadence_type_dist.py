# -*- coding: utf-8 -*-
"""终止式**类型分布**——论文 §5.3 里被点名但一直没跑的那个替代指标。

为什么需要这个脚本
------------------
论文实验 3 报告「终止式公式匹配率」在大调/小调都是 1.00，而留出巴赫只有
0.57/0.59。这个数字**不能**当质量证据：R11 把每句末三拍锁在一条学到的
终止公式上，匹配率因此被约束层钉死。论文里写明替代指标是「终止式类型分布」，
并声称它**不受 R11 支配**。这个脚本就是去检验那句话。

三件事
------
1. **实测分布**：对留出巴赫的每个乐句句末、以及生成草稿的每个乐句句末，
   用 ``learn._cadence_kind``（即给 287 条公式打类型的那本判定表）分类，
   统计 PAC / IAC / PC / HC / DC / 主和弦终止 / 离调 / 转调 / 其他 的比例。
2. **和 R11 的关系**：R11 锁定句末三拍，所以草稿的类型分布很可能只是
   「C 规则池按频次抽样」的重述。为此额外报告 **C 规则池自身的类型分布**，
   以及一条 **R11 关闭**的消融臂（把公式内容换成自由末三拍，其余一字不动）。
3. **是否独立于 R11**：比较 draft / draft_r11off / C 池 三者的分布。
   若 r11off 明显向巴赫靠拢，则类型分布**不是**独立证据，论文必须如实写。

术语循环性（必须一起交代）
------------------------
类型标签本身就是 ``_cadence_kind`` 的输出，而 287 条公式的 ``type`` 字段
也由同一函数生成。所以「类型分布」度量的是**判定表的输出分布**，
不是外部权威的听感判定。它能回答的是「约束层把句末摆成了什么形状」，
不能回答「这些终止式是否好听/是否正确」。

用法
----
::

    venv/bin/python -m score_learning.grammar.cadence_type_dist
    venv/bin/python -m score_learning.grammar.cadence_type_dist --json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CACHE = os.path.join(ROOT, 'runs', 'grammar_cache', 'bach_chorales.json')
OUT = os.path.join(ROOT, 'runs', 'experiments', 'e3_cadence_type.json')

# 判定表可能返回的类型（learn._cadence_kind），顺序即报告顺序
ORDER = ['完全正格终止 PAC', '不完全正格终止 IAC', '变格终止 PC', '半终止 HC',
         '阻碍终止 DC', '主和弦终止', '离调终止（副属→目标）',
         '离调半终止（停于副属）', '转调终止（离调后完全终止）',
         '转调终止（离调后变格终止）', '其他终止']
SHORT = {'完全正格终止 PAC': 'PAC', '不完全正格终止 IAC': 'IAC', '变格终止 PC': 'PC',
         '半终止 HC': 'HC', '阻碍终止 DC': 'DC', '主和弦终止': '主和弦终止',
         '离调终止（副属→目标）': '离调终止', '离调半终止（停于副属）': '离调半终止',
         '转调终止（离调后完全终止）': '转调完全', '转调终止（离调后变格终止）': '转调变格',
         '其他终止': '其他'}

KEYS = {'major': 1, 'minor': -2}          # 与论文实验 2/3 同一主调
N_DRAFT = {'major': 20, 'minor': 16}      # 与论文实验 2 同样本量
BARS = 12                                 # 与 e2_attempts.json 同


# ------------------------------------------------------------------ 工具
def _kind(chs: List[str], sop: List[int], mode: str) -> str:
    from score_learning.grammar.learn import _cadence_kind
    return _cadence_kind(chs, sop, mode)


def _tvd(a: Counter, b: Counter) -> float:
    """两个分布的总变差距离（0 = 相同，1 = 完全不重叠）。"""
    na, nb = sum(a.values()), sum(b.values())
    if not na or not nb:
        return float('nan')
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0) / na - b.get(k, 0) / nb) for k in keys)


def _dist(c: Counter) -> Dict[str, float]:
    n = sum(c.values()) or 1
    return {SHORT.get(k, k): round(v / n, 4) for k, v in sorted(c.items(), key=lambda kv: -kv[1])}


def _boot_tvd_ci(pieces_a: List[Counter], pieces_b: List[Counter],
                 iters: int = 2000, seed: int = 20261006) -> Tuple[float, float, float]:
    """按**曲目**重抽样的 TVD 自助 CI。

    必须按曲目而不是按句抽样：同一首里多句共享调性与风格，
    按句抽样会把有效样本量算大（这是本项目此前踩过的坑）。
    """
    rng = random.Random(seed)
    point = _tvd(sum((Counter(x) for x in pieces_a), Counter()),
                 sum((Counter(x) for x in pieces_b), Counter()))
    vals: List[float] = []
    for _ in range(iters):
        a = Counter()
        for _i in pieces_a:
            a.update(pieces_a[rng.randrange(len(pieces_a))])
        b = Counter()
        for _i in pieces_b:
            b.update(pieces_b[rng.randrange(len(pieces_b))])
        v = _tvd(a, b)
        if v == v:                      # 跳过空重抽样的 nan
            vals.append(v)
    vals.sort()
    if not vals:
        return point, float('nan'), float('nan')
    lo = vals[int(0.025 * len(vals))]
    hi = vals[min(len(vals) - 1, int(0.975 * len(vals)))]
    return point, lo, hi


# ------------------------------------------------------- 留出巴赫的句末
def _corpus():
    with open(CACHE, encoding='utf-8') as f:
        blob = json.load(f)
    return blob['pieces'] if isinstance(blob, dict) else blob


def bach_phrase_ends(mode: str) -> Tuple[List[Counter], Counter, Counter, Dict[str, int]]:
    """留出巴赫（is_heldout）每个乐句句末三和弦 + 末两拍女高内旋。

    句末分两类分别计数：**内部句**与**全曲末句**。原因是 F 层强制末句落主
    目标，若不分开，末句会掩盖 R11 关闭臂的真实行为。
    """
    from score_learning.learn_core import is_heldout
    per_piece: List[Counter] = []
    total = Counter()
    interior = Counter()
    n_phrase = 0
    skipped_short = 0
    for p in _corpus():
        if p['mode'] != mode or not is_heldout(p['id']):
            continue
        b, tpc = p['beats'], p['tonic_pc']
        c: Counter = Counter()
        nph = len(p['phrases'])
        for k, ph in enumerate(p['phrases']):
            e = ph['end']
            if e - 2 < ph['start']:
                skipped_short += 1
                continue
            chs = [b[i]['chord'] for i in (e - 2, e - 1, e)]
            sop = [(b[i]['midi'][0] - tpc) % 12 for i in (e - 1, e)]
            kind = _kind(chs, sop, mode)
            c[kind] += 1
            if k < nph - 1:
                interior[kind] += 1
            n_phrase += 1
        if c:
            per_piece.append(c)
            total.update(c)
    return per_piece, total, interior, {'phrases': n_phrase, 'pieces': len(per_piece),
                                       'skipped_short': skipped_short}


# --------------------------------------------------------- 生成草稿的句末
def _draft_phrase_ends(mode: str, n: int, r11_off: bool):
    """生成 n 首草稿并给每个句末分类。

    ``r11_off=True`` 时把 R11 的内容约束摘掉：仍然抽同一条 C 规则（保留
    F 层给定的终止目标），但把末三拍的和弦与女高交给 DP 自由决定。
    实现上不需要改 ``compose.py``——``_phrase_states`` 接受
    ``chords=[]`` 与 ``soprano=(None, None)``，此时固定位全为 ``None``。

    返回 (每稿 Counter, 合计 Counter, 内部句 Counter, info)。
    """
    from score_learning.grammar import script as gs
    from score_learning.grammar import compose as C

    sc = gs.load()
    key = KEYS[mode]
    old = C.compose_phrase_candidates
    if r11_off:
        FREE = {'chords': [], 'soprano': (None, None), 'id': 'R11-off'}

        def patched(g, tonic_pc, meta, formula, *a, **kw):
            return old(g, tonic_pc, meta, FREE, *a, **kw)

        C.compose_phrase_candidates = patched
    per_piece: List[Counter] = []
    total = Counter()
    interior = Counter()
    fails = 0
    seeds: List[str] = []
    n_phrase = 0
    try:
        for i in range(n):
            # 两臂用**完全相同的种子**，故这是配对比较；max_tries 用默认 12，
            # 仍然只接受零违例草稿，与论文实验 2 的接受判据一致。
            try:
                res = C.compose(sc, key, mode, None, f'cad{i}', total_bars=BARS)
            except Exception:                       # noqa: BLE001 - 记录并继续
                fails += 1
                continue
            seeds.append(str(res.get('seed_used', f'cad{i}')))
            tonic_pc = C.tonic_letter_pc(C.tonic_from_sharps(key, mode))[1]
            ends = [t for t, b in enumerate(res['beats']) if b.get('cadence')]
            c: Counter = Counter()
            for j, e in enumerate(ends):
                chs = [res['beats'][i]['chord'] for i in (e - 2, e - 1, e)]
                sop = [(res['beats'][i]['midi'][0] - tonic_pc) % 12 for i in (e - 1, e)]
                kind = _kind(chs, sop, mode)
                c[kind] += 1
                if j < len(ends) - 1:
                    interior[kind] += 1
                n_phrase += 1
            if c:
                per_piece.append(c)
                total.update(c)
    finally:
        C.compose_phrase_candidates = old
    info = {'drafts_ok': len(per_piece), 'drafts_failed': fails,
            'phrases': n_phrase, 'interior_phrases': sum(interior.values()),
            'seeds_distinct': len(set(seeds)), 'seed_prefix': 'cad'}
    return per_piece, total, interior, info


# ------------------------------------------------------- C 规则池的类型分布
def formula_pool(mode: str) -> Counter:
    """C 规则池按**语料出现次数**加权的类型分布 = R11 抽样的母分布。

    权重用公式的 ``n``（该公式在语料里出现多少次）而不是条目数，因为 R11 是
    按 ``f['p']`` 加权抽的，``p`` 与 ``n`` 成正比。
    """
    from score_learning.grammar import script as gs
    sc = gs.load()
    c: Counter = Counter()
    for _tgt, bundle in sc['cadences'][mode].items():
        for f in bundle['forms']:
            # 草稿与巴赫句末由判定表分类，判定表不会产出专家裁定的类型；
            # 公式池须用同一张表的标签，裁定过的公式退回判定表原标签。
            t = '其他终止' if f.get('adjudication') else f.get('type', '其他终止')
            c[t] += int(f.get('n', 0) or 0)
    return c


# ------------------------------------------------------------------ 主流程
def run(n_boots: int = 2000) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        'question': '终止式类型分布能否作为不受 R11 支配的替代证据？',
        'method': ('句末三和弦 + 末两拍女高内旋 → learn._cadence_kind；'
                   '巴赫取 is_heldout 曲目的每个乐句句末，生成取每首草稿的每个句末；'
                   'TVD 的自助 CI 按曲目重抽样（同一首内多句不独立）'),
        'caveat': ('类型标签与 287 条公式的 type 字段同出于 _cadence_kind，'
                   '故本度量刻画的是判定表的输出分布，不是听感判定'),
        'setting': {'key_sharps': KEYS, 'bars': BARS, 'n_draft': N_DRAFT},
        'arms': {},
    }
    for mode in ('major', 'minor'):
        bp, bt, bint, binfo = bach_phrase_ends(mode)
        dp, dt, dint, dinfo = _draft_phrase_ends(mode, N_DRAFT[mode], r11_off=False)
        rp, rt, rint, rinfo = _draft_phrase_ends(mode, N_DRAFT[mode], r11_off=True)
        pool = formula_pool(mode)

        p_d, lo_d, hi_d = _boot_tvd_ci(dp, bp, n_boots)
        p_r, lo_r, hi_r = _boot_tvd_ci(rp, bp, n_boots)
        p_rd, lo_rd, hi_rd = _boot_tvd_ci(rp, dp, n_boots)

        out['arms'][mode] = {
            'bach_heldout': {'info': binfo, 'counts': dict(bt), 'dist': _dist(bt),
                             'interior_counts': dict(bint), 'interior_dist': _dist(bint)},
            'grammar_drafts': {'info': dinfo, 'counts': dict(dt), 'dist': _dist(dt),
                               'interior_counts': dict(dint), 'interior_dist': _dist(dint)},
            'grammar_drafts_r11_off': {'info': rinfo, 'counts': dict(rt), 'dist': _dist(rt),
                                       'interior_counts': dict(rint),
                                       'interior_dist': _dist(rint)},
            'formula_pool_c_rules': {'counts': dict(pool), 'dist': _dist(pool)},
            'tvd': {
                'drafts_vs_bach': [round(p_d, 4), round(lo_d, 4), round(hi_d, 4)],
                'r11off_vs_bach': [round(p_r, 4), round(lo_r, 4), round(hi_r, 4)],
                'r11off_vs_drafts': [round(p_rd, 4), round(lo_rd, 4), round(hi_rd, 4)],
                'drafts_vs_pool': round(_tvd(dt, pool), 4),
                'r11off_vs_pool': round(_tvd(rt, pool), 4),
                'drafts_interior_vs_bach_interior': [
                    round(_tvd(dint, bint), 4)],
                'r11off_interior_vs_bach_interior': [
                    round(_tvd(rint, bint), 4)],
            },
        }
        # 逐类型对照（大调/小调分开，避免混合计数掩盖差异）
        rows = []
        nb, nd, nr = sum(bt.values()), sum(dt.values()), sum(rt.values())
        ib, idr, ir = sum(bint.values()), sum(dint.values()), sum(rint.values())
        for k in ORDER:
            cb, cd, cr = bt.get(k, 0), dt.get(k, 0), rt.get(k, 0)
            if not (cb or cd or cr):
                continue
            rows.append({
                'type': SHORT[k], 'bach': cb, 'drafts': cd, 'r11off': cr,
                'p_bach': round(cb / nb, 4) if nb else 0.0,
                'p_drafts': round(cd / nd, 4) if nd else 0.0,
                'p_r11off': round(cr / nr, 4) if nr else 0.0,
                'interior': {'bach': bint.get(k, 0), 'drafts': dint.get(k, 0),
                             'r11off': rint.get(k, 0),
                             'p_bach': round(bint.get(k, 0) / ib, 4) if ib else 0.0,
                             'p_drafts': round(dint.get(k, 0) / idr, 4) if idr else 0.0,
                             'p_r11off': round(rint.get(k, 0) / ir, 4) if ir else 0.0},
            })
        out['arms'][mode]['per_type'] = rows
    return out


def _fmt(res: Dict[str, Any]) -> None:
    for mode, a in res['arms'].items():
        print(f'\n=== {mode} ===')
        for k in ('bach_heldout', 'grammar_drafts', 'grammar_drafts_r11_off'):
            i = a[k]['info']
            print(f'  {k:26s} n={i.get("phrases", 0):4d} 句'
                  f'（曲/稿 {i.get("pieces", i.get("drafts_ok", 0))}）'
                  f'{"  失败 " + str(i["drafts_failed"]) if i.get("drafts_failed") else ""}')
        t = a['tvd']
        print(f'  TVD 草稿 vs 巴赫      {t["drafts_vs_bach"]}')
        print(f'  TVD R11关 vs 巴赫     {t["r11off_vs_bach"]}')
        print(f'  TVD R11关 vs 草稿     {t["r11off_vs_drafts"]}')
        print(f'  TVD 草稿 vs C规则池   {t["drafts_vs_pool"]}')
        print(f'  TVD R11关 vs C规则池  {t["r11off_vs_pool"]}')
        print(f'  仅内部句 TVD 草稿/巴赫 {t["drafts_interior_vs_bach_interior"][0]}'
              f'   R11关/巴赫 {t["r11off_interior_vs_bach_interior"][0]}')
        print(f'  {"类型":12s}{"巴赫":>10s}{"草稿":>10s}{"R11关":>10s}'
              f'{"内-巴":>9s}{"内-草":>9s}{"内-关":>9s}')
        for r in a['per_type']:
            it = r['interior']
            print(f'  {r["type"]:12s}{r["p_bach"]:>10.3f}{r["p_drafts"]:>10.3f}'
                  f'{r["p_r11off"]:>10.3f}{it["p_bach"]:>9.3f}{it["p_drafts"]:>9.3f}'
                  f'{it["p_r11off"]:>9.3f}')


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog='score_learning.grammar.cadence_type_dist')
    ap.add_argument('--json', action='store_true', help='只写 JSON，不打印')
    ap.add_argument('--boots', type=int, default=2000, help='自助重抽样次数')
    args = ap.parse_args(argv)
    res = run(n_boots=args.boots)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    if not args.json:
        _fmt(res)
    print(f'\n已写入 {OUT}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
