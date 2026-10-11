# -*- coding: utf-8 -*-
"""注入故障实验：检查器能否按规则 ID 检出被注入的声部进行错误。

对应论文 §5.6 Experiment 6(iii) / RQ4 的后半句「recover injected voice-leading
faults by rule identifier」。原稿只写了协议（"should ship with the camera-ready
code"）而没有执行，这里补上。

设计
----
每类故障在**同一批**合法草稿上做**单点**程序化变异，一次只注入一处，观察共享
检查器 :func:`score_learning.grammar.check.check_satb` 是否报出对应规则 ID。

三类注入对应论文 §3.2.4 的三个示例：

======  ===================================  ==========================
规则    变异方式                              论文原文示例
======  ===================================  ==========================
R01     把次中音锁到女高音下方纯五/纯八        alto locked a perfect fifth below soprano
R03     交换中音与次中音造成声部交叉            inner-voice crossing
R06     外声部导音不解决（改为下行/保持）unresolved outer leading tone on V→I
======  ===================================  ==========================

指标
----
* **precision** = 检出数 / 应检数
* **recall**    = 报出目标规则的比例（同一 draft 被别的规则报出不��）
* **specificity**：未注入的合法草稿被误报为该规则的比例（应为 0）
* 合法草稿的**基线误报率**（draft 未经注入时是否本就干净）

**为什么不逐拍随机注入**：逐拍随机变异无法保证变异后仍是一个 musically
plausible 的 SATB 网格（可能同时触发 R04 间距、R08 旋律音程），检出该规则会被
其他规则"淹没"。单点、有目标的变异才能把「检查器能否识别这一类错误」与
「错误之间是否互相耦合」分开。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 本文件在 score_learning/grammar/ 下，需上溯三层才是项目根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from score_learning.grammar.check import check_satb  # noqa: E402
from score_learning.grammar.theory import (  # noqa: E402
    QUALITIES, SEVENTHS, chord_members, parse_chord_key,
)

OUT = os.path.join(ROOT, 'runs', 'experiments')
#: 论文 §5.6 用的字幅；与grammar/compose.py 的搜索约束一致
TB_MAX = 19


# --------------------------------------------------------------- 草稿生成
def _build_info(beats: List[Dict[str, Any]], tpc: int,
                mode: str = 'major') -> List[Dict[str, Any]]:
    """把 SATB 拍网格转成 check_satb 需要的 ``beats`` 记录（与 evaluate.py 同构）。"""
    out = []
    for b in beats:
        chord = b.get('chord')
        if chord:
            root, q, _inv = parse_chord_key(chord)
            mem = chord_members(root, q)
            sev = (root + QUALITIES[q][3]) % 12 if q in SEVENTHS else None
        else:
            root, q, mem, sev = -1, '', {}, None
        out.append({
            't': b['t'], 'midi': list(b['midi']),
            'rel': [(m - tpc) % 12 for m in b['midi']],
            'members': mem,
            'lt_pc': 11 if (q and root in (7, 11) and 11 in mem) else None,
            'tonic_chord': root == 0,
            'seventh_pc': sev,
            'is_seventh': q in SEVENTHS,
            'phrase_start': bool(b.get('phrase_start')),
            'chord': chord, 'root': root if chord else None, 'quality': q or None,
            'mode': mode,
        })
    return out


def _to_grid(info: Sequence[Dict[str, Any]]) -> List[Tuple[float, List[int]]]:
    return [(x['t'], list(x['midi'])) for x in info]


def gen_drafts(n: int = 20, key: int = 1, mode: str = 'major',
               bars: int = 12, seed_prefix: str = 'inject') -> List[Dict[str, Any]]:
    """用生产合成器生成 ``n`` 首合法 SATB 草稿（因此基线应为0 违例）。"""
    from score_learning.grammar import script as gs
    from score_learning.grammar.compose import compose
    from score_learning.grammar.theory import tonic_letter_pc, tonic_from_sharps
    sc = gs.load()
    tpc = tonic_letter_pc(tonic_from_sharps(key, mode))[1]
    drafts = []
    for i in range(n):
        res = compose(sc, key, mode, None, f'{seed_prefix}{i}',
                      total_bars=bars)
        beats = []
        for k, b in enumerate(res['beats']):
            beats.append({'t': float(b['t']), 'midi': list(b['midi']),
                          'chord': b.get('chord'),
                          'phrase_start': k == 0 or b.get('phrase', 0) !=
                          res['beats'][k - 1].get('phrase', 0)})
        drafts.append({'beats': beats, 'tpc': tpc, 'mode': mode, 'tpc_name': tonic_letter_pc(
            tonic_from_sharps(key, mode))[0]})
    return drafts


# --------------------------------------------------------------- 变异算子
def inject_R01(info: List[Dict[str, Any]], tpc: int, ctx=None) -> Optional[Dict[str, Any]]:
    """把某拍的次中音（T）改到与女高音构成纯五度或纯八度。

    判据（R01）要求相邻八分点或相邻拍点上，同一对声部**连续**构成纯五/纯八。
    因此仅把单拍的 T 改成纯五度还不够——必须与**相邻拍**的 T 一起改，
    否则只有孤立的一次纯五，R01 不会触发（它检查的是两次连续）。

    实现：找一对相邻拍 k-1,k，把两拍的 T 都设到各自 S 的下方纯五（S-7）
    或同度（S）。这样构成两次连续的纯五/八度。
    """
    for k in range(1, len(info)):
        prev, cur = info[k - 1], info[k]
        sp, sc_ = prev['midi'][0], cur['midi'][0]
        tp, tc = prev['midi'][2], cur['midi'][2]
        if (sp - tp) % 12 in (0, 7) and (sc_ - tc) % 12 in (0, 7):
            continue                      # 已经是连续纯五/八
        for off in (-7, 0, 7):
            ntp, ntc = sp + off, sc_ + off
            if ntp == tp or ntc == tc:
                continue
            pv = list(prev['midi']); cv = list(cur['midi'])
            pv[2], cv[2] = ntp, ntc
            # 保持声部序 S>=A>=T>=B，否则会附带触发 R03/R04 掩盖信号
            if not (pv[0] >= pv[1] >= pv[2] >= pv[3]):
                continue
            if not (cv[0] >= cv[1] >= cv[2] >= cv[3]):
                continue
            # 两拍都必须与各自 S 构成纯五或纯八
            if (pv[0] - pv[2]) % 12 not in (0, 7):
                continue
            if (cv[0] - cv[2]) % 12 not in (0, 7):
                continue
            _put(info, k - 1, pv, tpc)
            _put(info, k, cv, tpc)
            kind = '五' if (pv[0] - pv[2]) % 12 == 7 else '八'
            return {'rule': 'R01', 'index': k, 't': cur['t'],
                    'msg': f'注入：S/T 在连续两拍构成纯{kind}度 '
                           f'({pv[0]}/{pv[2]} → {cv[0]}/{cv[2]})'}
    return None


def inject_R03(info: List[Dict[str, Any]], tpc: int, ctx=None) -> Optional[Dict[str, Any]]:
    """把某一拍的中音（A）降到次中音（T）之下，造成声部交叉。

    判据（R03+R04 的spacing 谓词）要求 ``S>=A>=T>=B``，因此只需让 A<T
    即可触发。做法：把 A 换成 ``T - 1``（同时保持 A 低于 S、T 不低于 B）。
    合法草稿本身满足 A>=T，所以注入必然成功。
    """
    for k in range(1, len(info)):
        cur = info[k]
        s, a, t, b = cur['midi']
        if a < t:
            continue                      # 已经是交叉（合法草稿不应出现）
        new_a = t - 1
        if new_a < b:                    # 会连带破坏 S>=A>=T>=B 的另一侧
            new_a = min(t - 1, max(b + 1, s - 1))
        if new_a >= t or new_a > s or new_a < b:
            continue
        _put(info, k, [s, new_a, t, b], tpc)
        return {'rule': 'R03', 'index': k, 't': cur['t'],
                'msg': f'注入：声部交叉 A={new_a} < T={t}（原 A={a}）'}
    return None


def inject_R06(info: List[Dict[str, Any]], tpc: int, ctx=None) -> Optional[Dict[str, Any]]:
    """把外声部（女高音或低音）在 V→I 处的导音改成下行或同度保持。

    R06 的判据是：``prev`` 有 ``lt_pc`` 且当前是主和弦，则外声部若等于导音
    音级却没有 ``+1`` 的上行即报违规。
    """
    for k in range(1, len(info)):
        prev, cur = info[k - 1], info[k]
        if prev.get('lt_pc') is None or not cur.get('tonic_chord'):
            continue
        for vi in (0, 3):
            if prev['rel'][vi] != prev['lt_pc']:
                continue
            old = cur['midi'][vi]
            # 下行（或保持）—— 制造未上行解决
            new_v = old - 1
            m = list(cur['midi'])
            m[vi] = new_v
            _put(info, k, m, tpc)
            return {'rule': 'R06', 'index': k, 't': cur['t'],
                    'msg': f'注入：{"女高音" if vi==0 else "低音"}导音未上行解决 '
                           f'({old}→{new_v})'}
    return None


# ----------------------------------------------- 其余硬规则的变异算子
# 每个算子只按教科书定义构造错误，不调用检查器挑选位置——否则检出率是循环的。
# 构造时尽量保持声部序与间距（S>=A>=T>=B，S-A/A-T<=12，T-B<=TB_MAX），
# 让附带违例尽量少；仍然出现的附带违例照实记入 extra_rules。

def _ordered(v: Sequence[int]) -> bool:
    s, a, t, b = v
    return s >= a >= t >= b and s - a <= 12 and a - t <= 12 and t - b <= TB_MAX


def _put(info: List[Dict[str, Any]], k: int, midi: List[int], tpc: int) -> None:
    info[k] = dict(info[k], midi=list(midi), rel=[(m - tpc) % 12 for m in midi])


def _melodic_bad(d: int) -> bool:
    d = abs(d)
    return d == 6 or d in (10, 11) or d > 12


def inject_R02(info, tpc, ctx=None):
    """外声部同向进入纯五/纯八且女高音跳进（>2 半音）：只改第 k 拍的女高音。"""
    for k in range(1, len(info)):
        prev, cur = info[k - 1], info[k]
        if cur.get('phrase_start'):
            continue
        s1, b1 = prev['midi'][0], prev['midi'][3]
        b2 = cur['midi'][3]
        db = b2 - b1
        if db == 0:
            continue
        sign = 1 if db > 0 else -1
        for step in range(3, 10):
            s2 = s1 + sign * step
            if (s2 - b2) % 12 not in (0, 7) or (s1 - b1) % 12 == (s2 - b2) % 12:
                continue
            if (s2 - tpc) % 12 not in cur['members'] or _melodic_bad(step):
                continue
            m = list(cur['midi']); m[0] = s2
            if not _ordered(m):
                continue
            if k + 1 < len(info) and _melodic_bad(info[k + 1]['midi'][0] - s2):
                continue
            _put(info, k, m, tpc)
            return {'rule': 'R02', 'index': k, 't': cur['t'],
                    'msg': f'注入：外声部同向进入纯{"五" if (s2 - b2) % 12 == 7 else "八"}度，'
                           f'女高音跳 {sign * step:+d}'}
    return None


def inject_R04(info, tpc, ctx=None):
    """间距过大：低音下移八度，使 T-B 超过 TB_MAX（和弦成员不变）。"""
    for k in range(1, len(info) - 1):
        m = list(info[k]['midi'])
        nb = m[3] - 12
        if m[2] - nb <= TB_MAX:
            continue
        if _melodic_bad(nb - info[k - 1]['midi'][3]) or \
                _melodic_bad(info[k + 1]['midi'][3] - nb):
            continue
        m[3] = nb
        _put(info, k, m, tpc)
        return {'rule': 'R04', 'index': k, 't': info[k]['t'],
                'msg': f'注入：次中音-低音间距 {m[2] - nb} > {TB_MAX}'}
    return None


def inject_R05(info, tpc, ctx=None):
    """出习得音域：某声部八度移位到音域外（音级不变），优先不破坏声部序。"""
    rg = (ctx or {}).get('ranges')
    if not rg:
        return None
    for k in range(1, len(info) - 1):
        for vi, nm in ((0, 'S'), (3, 'B'), (1, 'A'), (2, 'T')):
            m = list(info[k]['midi'])
            for nv in (m[vi] + 12, m[vi] - 12):
                if rg[nm]['low'] <= nv <= rg[nm]['high']:
                    continue
                m2 = list(m); m2[vi] = nv
                if not (m2[0] >= m2[1] >= m2[2] >= m2[3]):
                    continue
                _put(info, k, m2, tpc)
                return {'rule': 'R05', 'index': k, 't': info[k]['t'],
                        'msg': f'注入：{nm} {m[vi]}→{nv} 出音域 '
                               f'[{rg[nm]["low"]},{rg[nm]["high"]}]'}
    return None


def inject_R07(info, tpc, ctx=None):
    """七音不下行解决：前一拍七音所在声部改为上行级进（优先落在和弦音上）。"""
    for k in range(1, len(info)):
        prev, cur = info[k - 1], info[k]
        if prev.get('seventh_pc') is None or cur.get('phrase_start'):
            continue
        for vi in range(4):
            if prev['rel'][vi] != prev['seventh_pc']:
                continue
            p = prev['midi'][vi]
            cands = [p + d for d in (1, 2) if ((p + d - tpc) % 12) in cur['members']]
            cands += [p + 1, p + 2]
            for nv in cands:
                m = list(cur['midi']); m[vi] = nv
                if not _ordered(m):
                    continue
                _put(info, k, m, tpc)
                return {'rule': 'R07', 'index': k, 't': cur['t'],
                        'msg': f'注入：七音 {p}→{nv} 上行'}
    return None


def inject_R08(info, tpc, ctx=None):
    """不良旋律音程：某声部改为三全音或七度跳进，落点为本拍和弦音。"""
    for k in range(1, len(info) - 1):
        prev, cur = info[k - 1], info[k]
        if cur.get('phrase_start'):
            continue
        for vi in (1, 2, 0, 3):
            p = prev['midi'][vi]
            for d in (6, -6, 10, -10, 11, -11):
                nv = p + d
                if ((nv - tpc) % 12) not in cur['members']:
                    continue
                m = list(cur['midi']); m[vi] = nv
                if not _ordered(m):
                    continue
                _put(info, k, m, tpc)
                return {'rule': 'R08', 'index': k, 't': cur['t'],
                        'msg': f'注入：声部 {vi} 跳进 {d:+d}'}
    return None


def inject_R09(info, tpc, ctx=None):
    """和弦不完整：把唯一承载三音的内声部改成根音或五音（就近）。"""
    for k in range(1, len(info)):
        cur = info[k]
        mem = cur['members']
        if not mem:
            continue
        roles = [mem.get(r) for r in cur['rel']]
        if roles.count('3') != 1:
            continue
        vi = roles.index('3')
        if vi not in (1, 2):
            continue
        old = cur['midi'][vi]
        for d in sorted(range(-4, 5), key=abs):
            nv = old + d
            if d == 0 or mem.get((nv - tpc) % 12) not in ('R', '5'):
                continue
            m = list(cur['midi']); m[vi] = nv
            if not _ordered(m):
                continue
            _put(info, k, m, tpc)
            return {'rule': 'R09', 'index': k, 't': cur['t'],
                    'msg': f'注入：去掉三音（声部 {vi}: {old}→{nv}）'}
    return None


INJECTORS = {
    'R01': inject_R01,
    'R02': inject_R02,
    'R03': inject_R03,
    'R04': inject_R04,
    'R05': inject_R05,
    'R06': inject_R06,
    'R07': inject_R07,
    'R08': inject_R08,
    'R09': inject_R09,
}
ALL_RULES = tuple(INJECTORS)
#: R03 交叉与 R04 间距由同一个 spacing 谓词报出，标签为 'R03/R04'
ACCEPT = {'R03': {'R03', 'R03/R04'}, 'R04': {'R03/R04'}}


# --------------------------------------------------------------- 单次实验
def run_draft(draft: Dict[str, Any], rule: str, ranges: Dict[str, Dict[str, int]],
              tb_max: int = TB_MAX, refined: bool = True) -> Dict[str, Any]:
    """在一首草稿上注入 ``rule`` 类故障并检查。"""
    info = _build_info(draft['beats'], draft['tpc'], draft.get('mode', 'major'))
    grid = _to_grid(info)
    base = check_satb(grid, info, ranges, tb_max=tb_max, refined=refined)
    base_rules = {v['rule'] for v in base}

    work = [dict(x) for x in info]
    placed = INJECTORS[rule](work, draft['tpc'], {'ranges': ranges})
    if placed is None:
        return {'placed': False, 'rule': rule,
                'base_clean': not base, 'base_rules': sorted(base_rules),
                'detected': False, 'hit_rule': None,
                'extra_rules': []}
    after = check_satb(_to_grid(work), work, ranges, tb_max=tb_max, refined=refined)
    hit = {v['rule'] for v in after}
    accept = ACCEPT.get(rule, {rule})
    found = hit & accept
    return {
        'placed': True, 'rule': rule, 'site': placed,
        'base_clean': not base, 'base_rules': sorted(base_rules),
        'detected': bool(found),
        'hit_rule': sorted(found)[0] if found else None,
        'all_rules': sorted(hit),
        'extra_rules': sorted(hit - accept),
        'n_viol_before': len(base), 'n_viol_after': len(after),
        'violations': [{'rule': v['rule'], 't': v['t'], 'msg': v['msg']} for v in after],
    }


def _learned_order_loo(det: Sequence[Dict[str, Any]]) -> Optional[int]:
    """对照：从数据学规则先后（某规则号出现时恰为注入规则的比例，加一平滑），
    留一首草稿交叉验证，返回 top-1 命中数。"""
    drafts = sorted({r.get('draft') for r in det if r.get('draft') is not None})
    if len(drafts) < 2:
        return None
    hit = 0
    for d in drafts:
        app, good = Counter(), Counter()
        for r in det:
            if r.get('draft') == d:
                continue
            accept = ACCEPT.get(r['rule'], {r['rule']})
            for lab in {v['rule'] for v in r['violations']}:
                app[lab] += 1
                good[lab] += lab in accept
        score = {lab: (good[lab] + 1) / (app[lab] + 2) for lab in app}
        for r in det:
            if r.get('draft') != d:
                continue
            vs = r['violations']
            top = min(range(len(vs)), key=lambda i: (-score.get(vs[i]['rule'], 0.5), i))
            hit += vs[top]['rule'] in ACCEPT.get(r['rule'], {r['rule']})
    return hit


def attribution(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """一次注入、多条报错时，排在第一位的规则是否就是被注入的那条。

    * ``emission``：检查器原始输出顺序；
    * ``ranked``：:func:`rank_violations` 的支撑序（先验设定，未在本数据上拟合）；
    * ``chance``：从报出的不同规则号里随机挑一个的期望命中数。
    """
    from score_learning.grammar.check import rank_violations
    det = [r for r in rows if r.get('placed') and r.get('detected')]
    out: Dict[str, Any] = {'n': len(det), 'per_rule': {}}
    tot = Counter()
    for r in det:
        accept = ACCEPT.get(r['rule'], {r['rule']})
        vs = r['violations']
        ranked = rank_violations(vs)
        labels = []
        for v in ranked:
            if v['rule'] not in labels:
                labels.append(v['rule'])
        hit = {
            'emission': vs[0]['rule'] in accept,
            'ranked': ranked[0]['rule'] in accept,
            'ranked_top2': bool(set(labels[:2]) & accept),
        }
        chance = len(accept & set(labels)) / len(labels)
        pr = out['per_rule'].setdefault(r['rule'], Counter())
        for k, v in hit.items():
            pr[k] += int(v)
            tot[k] += int(v)
        pr['n'] += 1
        tot['chance'] += chance
        tot['distinct_labels'] += len(labels)
    out['per_rule'] = {k: dict(v) for k, v in out['per_rule'].items()}
    out.update({k: tot[k] for k in ('emission', 'ranked', 'ranked_top2')})
    out['learned_loo'] = _learned_order_loo(det)
    out['chance'] = round(tot['chance'], 1)
    out['mean_distinct_labels'] = round(tot['distinct_labels'] / max(len(det), 1), 2)
    return out


def run(n_drafts: int = 20, rules: Sequence[str] = ALL_RULES,
        save: bool = True, refined: bool = True) -> Dict[str, Any]:
    from score_learning.grammar import script as gs
    sc = gs.load()
    ranges = {k: dict(v) for k, v in sc['voicing']['ranges'].items()}
    drafts = gen_drafts(n_drafts)

    rows: List[Dict[str, Any]] = []
    for rule in rules:
        for i, d in enumerate(drafts):
            r = run_draft(d, rule, ranges, refined=refined)
            r['draft'] = i
            rows.append(r)

    # ---------- 汇总 ----------
    per_rule: Dict[str, Dict[str, Any]] = {}
    for rule in rules:
        rs = [r for r in rows if r['rule'] == rule]
        placed = [r for r in rs if r['placed']]
        det = [r for r in placed if r['detected']]
        accept = ACCEPT.get(rule, {rule})
        # 注入前本就干净的草稿里，误报该规则的个数（构造上应为 0）
        clean = [r for r in placed if r['base_clean']]
        fp = [r for r in placed if set(r['base_rules']) & accept]
        rec = len(det) / len(placed) if placed else float('nan')
        per_rule[rule] = {
            'n_drafts': len(rs), 'n_placed': len(placed),
            'n_detected': len(det),
            'recall': round(rec, 4),
            'isolated': sum(1 for r in det if not r['extra_rules']),
            'base_clean': len(clean),
            'false_positive_on_clean': len(fp),
            'extra_rules': dict(Counter(x for r in placed
                                        for x in r['extra_rules'])),
            'misses': [r['site'] for r in placed if not r['detected']],
        }
    # 总基线：未注入的合法草稿是否本就干净
    base_clean = sum(1 for r in rows if r['base_clean'])
    total = len(rows)
    summary = {
        'per_rule': per_rule,
        'baseline_clean_rate': round(base_clean / total, 4) if total else None,
        'n_trials': total, 'n_drafts': len(drafts),
        'overall': {
            'placed': sum(v['n_placed'] for v in per_rule.values()),
            'detected': sum(v['n_detected'] for v in per_rule.values()),
        },
    }
    summary['overall']['recall'] = round(
        summary['overall']['detected'] / max(summary['overall']['placed'], 1), 4)
    summary['attribution'] = attribution(rows)
    # 混淆矩阵：检出 vs 附带
    summary['confusion'] = {
        'true_positive': summary['overall']['detected'],
        'false_negative': summary['overall']['placed'] - summary['overall']['detected'],
        'unplaceable': total - summary['overall']['placed'],
    }
    if save:
        os.makedirs(OUT, exist_ok=True)
        name = 'e6_fault_injection.json' if refined else 'e6_fault_injection_original.json'
        with open(os.path.join(OUT, name), 'w',
                  encoding='utf-8') as f:
            json.dump({'summary': summary, 'rows': rows}, f,
                      ensure_ascii=False, indent=1)
    return {'summary': summary, 'rows': rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='注入故障检出实验（Experiment 6(iii)）')
    ap.add_argument('--n', type=int, default=20, help='草稿数')
    ap.add_argument('--rule', nargs='*', default=list(ALL_RULES))
    ap.add_argument('--original', action='store_true', help='用专家评审前的原检查器（另存产物）')
    a = ap.parse_args(argv)
    r = run(n_drafts=a.n, rules=a.rule, refined=not a.original)
    s = r['summary']
    print('=== 注入故障检出（Experiment 6(iii)）===')
    print('  草稿 %d 首，注入尝试 %d 次，检出 %d 次'
          % (s['n_drafts'], s['overall']['placed'], s['overall']['detected']))
    print('  合法草稿基线干净率 %.3f' % s['baseline_clean_rate'])
    print()
    print('| 规则 | 注入成功 | 检出 | recall | 仅目标规则 | 干净草稿误报 |')
    print('|---|---|---|---|---|---|')
    for rule, v in s['per_rule'].items():
        print('| %s | %d | %d | %.3f | %d | %d |'
              % (rule, v['n_placed'], v['n_detected'], v['recall'],
                 v['isolated'], v['false_positive_on_clean']))
    print()
    print('  混淆：TP=%d  FN=%d  无法注入=%d' % (
        s['confusion']['true_positive'], s['confusion']['false_negative'],
        s['confusion']['unplaceable']))
    extra = {k: v['extra_rules'] for k, v in s['per_rule'].items() if v['extra_rules']}
    if extra:
        print('  附带触发的其他规则:', extra)
    at = s['attribution']
    print('  根因归因 top-1（%d 次检出）：原始顺序 %d，支撑排序 %d，支撑排序 top-2 %d，'
          '随机期望 %.1f；数据学序（留一草稿）%s；平均报出 %.2f 个不同规则号'
          % (at['n'], at['emission'], at['ranked'], at['ranked_top2'], at['chance'],
             at['learned_loo'], at['mean_distinct_labels']))
    print()
    print('  产物 → runs/experiments/%s' % ('e6_fault_injection_original.json' if a.original
                                          else 'e6_fault_injection.json'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
