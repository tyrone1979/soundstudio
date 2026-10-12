# -*- coding: utf-8 -*-
"""评测：用同一个分析器读谱，在同一套指标下比较 巴赫留出集 / 语法作曲器 / 其它来源。

指标
- harmony_ppl：在学到的和声规则下的逐拍困惑度（越低越像巴赫）
- out_of_grammar：语料里从未出现过的和弦二元组比例
- chord_repeat：相邻两拍同一和弦的比例
- sop_repeat / sop_step / sop_leap：女高音同音 / 级进（≤2 半音）/ 跳进（≥5 半音）比例
- cadence_match：乐句结尾三拍命中学到的终止公式的比例
- violations_per_100：每 100 拍硬规则违例数（R01–R09，与作曲器同一检查器）
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .check import check_satb
from .learn import BackoffModel, harmony_events, is_heldout
from .theory import QUALITIES, SEVENTHS, chord_members, parse_chord_key

LT = 11


def _model(script, mode) -> BackoffModel:
    m = BackoffModel(list(script['vocab'][mode]))
    keys = {'L3': ('prev2', 'prev', 'pos'), 'L2': ('prev', 'pos'), 'L1': ('prev',),
            'L0': ('pos',)}
    for lv, rules in script['harmony'][mode].items():
        for r in rules:
            ctx = tuple(r['when'][k] for k in keys[lv])
            for c, _p, n in r['then']:
                m.t[lv][ctx][c] = n
    return m


def piece_metrics(piece: Dict[str, Any], script: Dict[str, Any],
                  ranges: Optional[Dict[str, Dict[str, int]]] = None,
                  tb_max: int = 24,
                  exclude_t: Optional[set] = None,
                  refined: bool = True) -> Dict[str, Any]:
    """``ranges`` / ``tb_max`` 是**评测配置**，不是被测对象的属性。

    默认值（``ranges=None`` → 全 MIDI 0--127，``tb_max=24``）是论文已发表的
    口径，即「宽松 harness」：它把习得的音域谓词（R05）整条关掉，并把
    tenor--bass 间距上界从搜索用的 19 放宽到 24。``range_ablation.py``
    用这两个参数把同一套谓词在四种配置下重跑，从而把
    「改存储阈值即改判决」变成可复现的数字，而不是一句断言。
    """
    mode = piece['mode']
    vset = set(script['vocab'][mode])
    model = _model(script, mode)
    ev = harmony_events(piece, None)
    ll, n, oog = 0.0, 0, 0
    #: 词表外和弦另记一条log-loss：用模型**实际给出的**概率（经unigram 兜底），
    #: 而不是固定的 log(1e-4) 惩罚。生成器被约束在attested bigram 内，其
    #: out-of-vocabulary 率恒为 0，而 Bach 约 0.13--0.15；两者共用同一惩罚项时，
    #: 困惑度差距会被人为放大。见论文 §7 "Measurement artefacts"。
    ll_inv, n_inv = 0.0, 0
    for p2, p1, pos, c in ev:
        if c not in vset:
            ll += math.log(1e-4)
            oog += 1
            ll_inv += math.log(max(model.unigram(c), 1e-6))
            n_inv += 1
        else:
            lp = math.log(max(model.prob(p2 if p2 in vset else None,
                                         p1 if p1 in vset else 'BOS', pos, c), 1e-6))
            ll += lp
            ll_inv += lp
            if p1 != 'BOS' and not model.attested(p1, c):
                oog += 1
        n += 1
    b = piece['beats']
    tpc = piece['tonic_pc']
    inner_pairs = [(i - 1, i) for ph in piece['phrases'] for i in range(ph['start'] + 1, ph['end'] + 1)]
    sop_d = [b[i]['midi'][0] - b[h]['midi'][0] for h, i in inner_pairs]
    ch_rep = sum(1 for h, i in inner_pairs if b[i]['chord'] and b[i]['chord'] == b[h]['chord'])
    forms = {tuple(f['chords']) for c in script['cadences'][mode].values() for f in c['forms']}
    cad_hit = sum(1 for ph in piece['phrases'] if ph['end'] - 2 >= ph['start'] and
                  tuple(b[i]['chord'] for i in range(ph['end'] - 2, ph['end'] + 1)) in forms)
    grid, info = [], []
    starts = {ph['start'] for ph in piece['phrases']}
    keep = sorted({i for ph in piece['phrases'] for i in range(ph['start'], ph['end'] + 1)})
    for i in keep:
        x = b[i]
        grid.append((x['t'], list(x['midi'])))
        half = [x['eighth'][v] if x['eighth'][v] is not None else x['midi'][v] for v in range(4)]
        if half != x['midi']:
            grid.append((x['t'] + 0.5, half))
        if x['chord']:
            root, q, _inv = parse_chord_key(x['chord'])
            mem = chord_members(root, q)
            sev = (root + QUALITIES[q][3]) % 12 if q in SEVENTHS else None
        else:
            root, q, mem, sev = -1, '', {}, None
        info.append({'t': x['t'], 'midi': x['midi'],
                     'rel': [(m - tpc) % 12 for m in x['midi']], 'members': mem,
                     'lt_pc': LT if (root in (7, 11) and LT in mem) else None,
                     'tonic_chord': root == 0, 'seventh_pc': sev, 'is_seventh': q in SEVENTHS,
                     'phrase_start': i in starts, 'chord': x['chord'],
                     'root': root if x['chord'] else None, 'quality': q or None,
                     'mode': piece['mode']})
    if ranges is None:
        #: 评测默认把 R05（习得音域）整条关掉：把可允许范围放宽到全 MIDI 跨度。
        #: 这是论文已发表口径的**一部分**，也是 ``range_ablation.py`` 要量化的东西。
        ranges = {k: {'low': 0, 'high': 127} for k in 'SATB'}
    # 和弦识别不出的拍不做和声类检查，但其后一拍要当作新起点，避免跨拍误报
    beats_ok = []
    gap = False
    for x in info:
        if not x['members']:
            gap = True
            continue
        if gap:
            x = dict(x, phrase_start=True)
            gap = False
        beats_ok.append(x)
    viol = check_satb(grid, beats_ok, ranges, tb_max=tb_max, refined=refined)
    if exclude_t:
        viol = [v for v in viol if v['t'] not in exclude_t]
    nd = max(len(sop_d), 1)
    return {
        'beats': len(keep), 'slots': n, 'harmony_ll': ll,
        'harmony_ll_in_vocab': ll_inv, 'n_in_vocab': n_inv,
        'out_of_grammar': oog, 'chord_repeat': ch_rep,
        'sop_repeat': sum(1 for d in sop_d if d == 0) / nd,
        'sop_step': sum(1 for d in sop_d if 0 < abs(d) <= 2) / nd,
        'sop_leap': sum(1 for d in sop_d if abs(d) >= 5) / nd,
        'cadences': len(piece['phrases']), 'cadence_hit': cad_hit,
        'violations': len(viol), 'violation_rules': [v['rule'] for v in viol],
        'violation_detail': [(v['rule'], v['t'], v['msg']) for v in viol],
    }


def aggregate(rows: List[Dict[str, float]]) -> Dict[str, float]:
    if not rows:
        return {}
    slots = sum(r['slots'] for r in rows) or 1
    beats = sum(r['beats'] for r in rows) or 1
    w = lambda k: sum(r[k] * r['beats'] for r in rows) / beats  # noqa: E731
    return {
        'pieces': len(rows),
        'harmony_ppl': round(math.exp(-sum(r['harmony_ll'] for r in rows) / slots), 2),
        # 词表外和弦按模型实际概率计（见 piece_metrics 注释），用于剥离
        # 固定 log(1e-4) 惩罚对「生成 vs 语料」对比的影响。
        'harmony_ppl_model_penalty': round(
            math.exp(-sum(r.get('harmony_ll_in_vocab', r['harmony_ll'])
                          for r in rows) / slots), 2),
        'out_of_grammar': round(sum(r['out_of_grammar'] for r in rows) / slots, 3),
        'chord_repeat': round(sum(r['chord_repeat'] for r in rows) / beats, 3),
        'sop_repeat': round(w('sop_repeat'), 3),
        'sop_step': round(w('sop_step'), 3),
        'sop_leap': round(w('sop_leap'), 3),
        'cadence_match': round(sum(r['cadence_hit'] for r in rows) /
                               max(sum(r['cadences'] for r in rows), 1), 3),
        'violations_per_100': round(100 * sum(r['violations'] for r in rows) / beats, 2),
    }


def heldout_pieces(pieces):
    return [p for p in pieces if is_heldout(p['id'])]
