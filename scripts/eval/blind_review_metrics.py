# -*- coding: utf-8 -*-
"""「机器味」的客观度量：把听审的主观判断拆成可检验的指标。

背景
----
第一轮盲评里，听审员的定性反馈是「机器作品织体单一、乐句乱、旋律没有记忆点」。
这三条都是可以量化的。本脚本把它们各自拆成 4–5 个指标，用项目自己的
``score_learning.grammar.theory.recognize_chord`` 识别和声，测机器组与人类组的差异，
并给出三样东西：

1. **每首的指标值**（可直接对着谱子核验）；
2. **AUC + 标签置换检验 p**：机器组 12 首 vs 盲评人组 8 首（n 小，只作方向性判断）；
3. **参考分布定位**：把机器组放进**全部训练外的真实巴赫众赞歌**（几十首）里，
   看它落在什么分位。这一步是为了绕开 12 vs 8 的样本量问题——「比 95% 的真实
   作品更极端」比「12 比 8 大」有统计力得多。

四条规矩
--------
* 指标一律用**率/跨度**，不用绝对数（两组小节数 13–22 不等）。
* 终止式用项目自己的和声识别（属/导音 → 本调主三和弦），不用「最后一小节是不是
  主和弦」这种糊判据。
* 主音一律取 ``analyze('key')``，**不**从 KeySignature 升号数反推
  （music21 语料的升号数与实际调不符，实测有 E 大调标着 3 个升号）。
* 人类组 = 未进训练的曲子；另建更大的「参考池」只用于整体分布，不参与胜负判定。

用法::

    venv/bin/python blind_review_metrics.py            # 全量（参考池 60 首）
    venv/bin/python blind_review_metrics.py --quick    # 参考池 24 首
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import numpy as np                                                    # noqa: E402

from score_learning.grammar import theory                              # noqa: E402
from score_learning.stats import bootstrap_ci                          # noqa: E402

PKG = os.path.join(ROOT, 'runs', 'exploratory', 'blind_review')
MERGED = os.path.join(PKG, '生成质量盲评乐谱.musicxml')
KEY_JSON = os.path.join(PKG, '_答案_评审后开封.json')
OUT_JSON = os.path.join(PKG, '_机器味指标.json')
OUT_MD = os.path.join(PKG, '机器味指标报告.md')

BEAT = 1.0          # 终止式识别的分析粒度（整拍）。半拍太密：巴赫大量二分/全
                    # 音符使半拍窗口的 onset 常为空，实测 24 小节只认出 1 个终止式。
VOICE_NAMES = ('Soprano', 'Alto', 'Tenor', 'Bass')
CAD_MIN_CONF = 0.25   # 和声识别置信度下限（0.35 时 24 小节只认出 1 个终止式）

#: 指标分组：与听审反馈的三条一一对应
GROUPS = {
    '织体单一': ['mean_voices_per_attack', 'attack_sync_rate',
                 'voice_density_cv', 'unison_octave_pair_ratio', 'breath_ratio'],
    '乐句乱': ['cadence_per_8bars', 'cadence_gap_cv',
               'cadence_on_mod4_rate', 'phrase_pair_similarity'],
    '旋律没记忆点': ['motif3_repeat_rate', 'melodic_step_ratio',
                     'melodic_leap_ratio', 'same_pitch_run_ratio', 'contour_span'],
}
#: 方向对齐：+1 表示「原始值越大越像机器」
DIRECTION = {
    'mean_voices_per_attack': +1,      # 齐奏越多越像机器
    'attack_sync_rate': +1,
    'voice_density_cv': -1,            # 各声部密度越平均越像机器
    'unison_octave_pair_ratio': +1,
    'breath_ratio': -1,                # 缺少呼吸空隙像机器
    'cadence_per_8bars': -1,           # 终止式密度低像机器
    'cadence_gap_cv': +1,              # 终止式间隔不规整像机器
    'cadence_on_mod4_rate': -1,        # 终止式不落在 4 小节倍数像机器
    'phrase_pair_similarity': -1,      # 乐句之间不重复像机器
    'motif3_repeat_rate': -1,          # 动机不重复像机器
    'melodic_step_ratio': -1,          # 级进少、跳进多像机器
    'melodic_leap_ratio': +1,
    'same_pitch_run_ratio': +1,        # 同音反复多像机器
    'contour_span': -1,                # 音域窄像机器
}


# ----------------------------------------------------------------- 基础
def tonality(sc):
    """主音 pc + 调式。调号不可信，一律问 analyze('key')。"""
    k = sc.analyze('key')
    return k.tonic.pitchClass, k.mode


def _notes(p):
    """(offset, midi, ql) 列表。

    **不能**用 ``p.recurse().notes`` 的 ``offset``：music21 的 bach 语料里
    offset 语义不可靠（实测 bwv269 全部音符 offset≈0，而 part.highestTime=63），
    照它算节奏会把整首曲子压成几毫秒。改为按小节累加。
    """
    out = []
    ms = list(p.getElementsByClass('Measure'))
    if not ms:
        # 没有小节容器（例如由 spec 直接搭出的 Score）：退回直属音符
        base = float(p.offset or 0.0)
        return [(round(base + float(n.offset), 4), n.pitch.midi,
                 float(n.quarterLength)) for n in p.notes]
    t = 0.0
    for m in ms:
        for n in m.notes:
            out.append((round(t + float(n.offset), 4), n.pitch.midi,
                        float(n.quarterLength)))
        t += float(m.duration.quarterLength)
    return out


def _bars(sc):
    from music21 import meter
    ts = sc.recurse().getElementsByClass(meter.TimeSignature)
    exp = ts[0].barDuration.quarterLength if ts else 4.0
    return [(m.offset, m.number, exp)
            for m in sc.parts[0].getElementsByClass('Measure')]


# ----------------------------------------------------------------- 终止式
def cadences(sc, tonic_pc, mode):
    """逐半拍识别和弦，返回 [{'bar': 小节号, 'offset': 解决时刻}, ...]。

    判据（可证伪、不枚举调）：相邻两个半拍窗口，前者根音为属（rel 7）或导音
    （rel 11 且为减/半减），后者是本调主三和弦（rel 根 0、大小三和弦均可，
    因此容忍 Picardy third）。
    """
    parts = list(sc.parts)
    notes = [_notes(p) for p in parts]
    if not any(notes):
        return []
    total = max(o + d for lst in notes for o, _, d in lst)
    bars = _bars(sc)

    def to_bar(off):
        num = bars[0][1] if bars else 1
        for mo, mnum, _ in bars:
            if off >= mo - 1e-6:
                num = mnum
            else:
                break
        return num

    seq = []
    t = 0.0
    while t < total - 1e-6:
        onset, off_b, bass_rel = {}, {}, None
        for i, lst in enumerate(notes):
            for o, midi, d in lst:
                if not (t - 1e-6 <= o < t + BEAT - 1e-6):
                    continue
                rel = (midi - tonic_pc) % 12
                if i == len(parts) - 1:
                    bass_rel = rel if bass_rel is None else bass_rel
                    w = 2.0
                else:
                    w = 1.0
                onset[rel] = onset.get(rel, 0.0) + w
            for o, midi, d in lst:
                if t - 1e-6 <= o < t + BEAT - 1e-6 - 1e-9:
                    continue
                if t + BEAT - 1e-6 <= o < t + 2 * BEAT - 1e-6:
                    rel = (midi - tonic_pc) % 12
                    off_b[rel] = off_b.get(rel, 0.0) + 1.0
        if not onset:
            seq.append((t, None, None, 0.0))
        else:
            ch, conf = theory.recognize_chord(onset, bass_rel, mode, off_b)
            seq.append((t, None if ch is None else ch[0],
                        None if ch is None else ch[1], conf))
        t += BEAT

    out = []
    for i in range(len(seq) - 1):
        r0, q0, _ = seq[i][1], seq[i][2], seq[i][3]
        r1, q1, c1 = seq[i + 1][1], seq[i + 1][2], seq[i + 1][3]
        if r0 is None or r1 is None or c1 < CAD_MIN_CONF:
            continue
        # 必须是**属功能**和弦：V / V7（根音 7），或 vii° / vii°7（根音 11）。
        # 只看「根音 ∈ {7, 11}」会把 ii、IV、vi 等一切含导音的和弦算成属功能，
        # 实测在生成曲上造成每小节一个的假阳性（16 小节报 16 个终止式）。
        dominant = (r0 == 7 and q0 in ('M', '7')) or \
                   (r0 == 11 and q0 in ('d', 'o7', 'h7'))
        tonic = (r1 == 0)                # 本调主和弦（容忍 Picardy 与转位）
        if not (dominant and tonic):
            continue
        item = {'bar': to_bar(seq[i + 1][0]), 'offset': seq[i][0]}
        if not out or out[-1]['bar'] != item['bar']:
            out.append(item)
    return out


# ----------------------------------------------------------------- 指标
def _motion_kind(a0: int, a1: int, b0: int, b1: int) -> str:
    """两个声部在相邻两拍之间的运动关系（与 learn 的口径一致）。"""
    da, db = a1 - a0, b1 - b0
    if da == 0 and db == 0:
        return 'static'
    if da == 0 or db == 0:
        return 'oblique'
    return 'similar' if (da > 0) == (db > 0) else 'contrary'


def _beat_series(lst):
    """每拍取该拍**首个音**（与 ``analyze.py`` 建 beats 的口径一致），返回 {拍号: 音高}。"""
    cells = {}
    for o, m, d in lst:
        k = int(round(o))
        if k not in cells:
            cells[k] = m
    return cells


def pair_motion_profile(parts):
    """四个声部对的运动关系分布——直接对应「织体是不是齐奏」。

    比 ``mean_voices_per_attack`` 更直接：后者只数「每次起音几个声部」，
    这里直接给出 similar / contrary / oblique / static 的构成。**口径与
    ``learn._learn_voicing`` 的 ``pair_motion`` 完全一致**（整拍、取拍内首音），
    所以可以直接和 grammar.yaml 里的语料基线对比。基线（大调）：
    女高-女低 35/16/40/9，外声部 29/43/23/5。
    """
    pairs = {('Soprano', 'Alto'): 'SA', ('Alto', 'Tenor'): 'AT',
             ('Tenor', 'Bass'): 'TB', ('Soprano', 'Bass'): 'SB'}
    lst = [sorted(p) for p in parts]
    out = {}
    for (n1, n2), tag in pairs.items():
        i, j = VOICE_NAMES.index(n1), VOICE_NAMES.index(n2)
        d1, d2 = _beat_series(lst[i]), _beat_series(lst[j])
        cnt = {'similar': 0, 'contrary': 0, 'oblique': 0, 'static': 0}
        for k in sorted(set(d1) & set(d2)):
            if (k + 1) in d1 and (k + 1) in d2:
                cnt[_motion_kind(d1[k], d1[k + 1], d2[k], d2[k + 1])] += 1
        tot = sum(cnt.values()) or 1
        out[tag] = {k: round(v / tot, 3) for k, v in cnt.items()}
    return out


def metrics(sc, tonic_pc, mode):
    parts = list(sc.parts)
    nlist = [_notes(p) for p in parts]
    n_bars = max(1, len(_bars(sc)))

    # ---------- 织体 ----------
    onsets = {}
    counts = []
    for i, lst in enumerate(nlist):
        counts.append(len(lst))
        for o, _, _ in lst:
            onsets.setdefault(o, {})[i] = True
    sizes = [len(v) for v in onsets.values()] or [1]
    ivs = []
    for o, v in onsets.items():
        hs = [dict(((n_off, midi) for n_off, midi, _ in nlist[i])).get(o)
              for i in v]
        hs = [h for h in hs if h is not None]
        for a in range(len(hs)):
            for b in range(a + 1, len(hs)):
                ivs.append(abs(hs[a] - hs[b]))
    uo = [x for x in ivs if x % 12 == 0]

    total = max((o + d for lst in nlist for o, _, d in lst), default=BEAT)
    n_win = max(1, int(round(total / BEAT)))
    busy = set()
    for lst in nlist:
        for o, _, d in lst:
            k = int(round(o / BEAT))
            for dd in range(max(1, int(round(d / BEAT)))):
                busy.add(k + dd)

    # ---------- 旋律（女高） ----------
    mel = sorted(nlist[0])
    midi = [m for _, m, _ in mel]
    durs = [d for _, _, d in mel]
    steps = [abs(midi[i + 1] - midi[i]) for i in range(len(midi) - 1)] or [0]
    tri = [tuple((midi[i], durs[i], midi[i + 1], durs[i + 1], midi[i + 2],
                  durs[i + 2])) for i in range(len(midi) - 2)]
    rep = 1 - len(set(tri)) / len(tri) if tri else 0.0

    # ---------- 乐句（按终止式切段，算相邻段旋律重合率） ----------
    cad = cadences(sc, tonic_pc, mode)
    cbars = [c['bar'] for c in cad]
    gaps = [cbars[i + 1] - cbars[i] for i in range(len(cbars) - 1)] or [4.0]
    gmean = float(np.mean(gaps))
    cuts = sorted({c['offset'] for c in cad})
    segs, prev = [], 0.0
    for o in cuts + [None]:
        end = o if o is not None else total + 1
        seg = [m for off, m, _ in mel if prev <= off < end]
        if len(seg) >= 4:
            segs.append(seg)
        prev = end
    sims = []
    for i in range(len(segs) - 1):
        a, b = set(segs[i]), set(segs[i + 1])
        if a and b:
            sims.append(len(a & b) / len(a | b))

    mean_counts = float(np.mean(counts)) or 1.0
    return {
        'mean_voices_per_attack': float(np.mean(sizes)),
        'attack_sync_rate': float(np.mean([s >= 2 for s in sizes])),
        'voice_density_cv': float(np.std(counts) / mean_counts),
        'unison_octave_pair_ratio': (len(uo) / len(ivs)) if ivs else 0.0,
        'breath_ratio': 1.0 - len(busy & set(range(n_win))) / n_win,
        'cadence_per_8bars': 8.0 * len(cad) / n_bars,
        'cadence_gap_cv': float(np.std(gaps) / gmean) if gmean else 0.0,
        'cadence_on_mod4_rate': float(np.mean([m % 4 == 0 for m in cbars]))
        if cbars else 0.0,
        'phrase_pair_similarity': float(np.mean(sims)) if sims else 0.0,
        'motif3_repeat_rate': rep,
        'melodic_step_ratio': float(np.mean([s <= 2 for s in steps])),
        'melodic_leap_ratio': float(np.mean([s > 7 for s in steps])),
        'same_pitch_run_ratio': float(np.mean([s == 0 for s in steps])),
        'contour_span': float(max(midi) - min(midi)) if midi else 0.0,
        'n_bars': n_bars,
        'n_cadences': len(cad),
    }


# ----------------------------------------------------------------- 数据集
def split_merged(path):
    """把合并 musicxml 拆成 {code: Score}。

    编号在 ``partName`` 上（MusicXML 导出写 partName，不写 part id），
    形如 ``A01_Soprano``。
    """
    from music21 import converter, stream
    sc = converter.parse(path)
    groups = {}
    for p in sc.parts:
        code = (p.partName or p.id or '?').split('_')[0]
        groups.setdefault(code, []).append(p)
    out = {}
    for code, ps in groups.items():
        s = stream.Score()
        for i, p in enumerate(ps):
            p.id = VOICE_NAMES[i]
            p.partName = VOICE_NAMES[i]
            s.insert(0, p)
        out[code] = s
    return out


def reference_pool(limit=60):
    """训练外的真实巴赫众赞歌池：只用于整体分布参照，不参与胜负判定。"""
    from music21 import bar, corpus
    from blind_review_export import BAR_MAX, BAR_MIN, _pick_human_candidates, \
        strip_partial_bars
    names = _pick_human_candidates()
    random.Random(7).shuffle(names)
    out = []
    for bwv in names:
        if len(out) >= limit:
            break
        try:
            raw = corpus.parse(f'bach/{bwv}')
        except Exception:
            continue
        if len(list(raw.parts)) != 4:
            continue
        if len(list(raw.recurse().getElementsByClass(bar.Repeat))) > 0:
            continue
        sc = strip_partial_bars(raw)
        if not (BAR_MIN <= len(_bars(sc)) <= BAR_MAX):
            continue
        out.append((bwv, sc))
    return out


# ----------------------------------------------------------------- 统计
def auc(pos, neg):
    """AUC = P(正样本 > 负样本) + 0.5 P(=)。方向由调用方预先对齐。"""
    pos, neg = [float(x) for x in pos], [float(x) for x in neg]
    if not pos or not neg:
        return float('nan')
    w = t = 0
    for a in pos:
        for b in neg:
            w += a > b
            t += a == b
    return (w + 0.5 * t) / (len(pos) * len(neg))


def perm_p(pos, neg, n_perm=20000, seed=11):
    """标签置换检验：打乱分界，看观测 AUC 在零分布中的位置（单侧）。"""
    obs = auc(pos, neg)
    pool = [float(x) for x in pos] + [float(x) for x in neg]
    n1 = len(pos)
    rng = random.Random(seed)
    ge = 0
    for _ in range(n_perm):
        rng.shuffle(pool)
        ge += auc(pool[:n1], pool[n1:]) >= obs
    return (ge + 1) / (n_perm + 1)


def ref_pvalue(vals, ref):
    """单侧经验 p：参考池里有多大比例不小于机器组的中位数。"""
    med = float(np.median(vals))
    return sum(1 for r in ref if r >= med) / len(ref)


# ----------------------------------------------------------------- 主流程
def main(argv=None):
    ap = argparse.ArgumentParser(description='机器味客观度量')
    ap.add_argument('--quick', action='store_true', help='参考池缩到 24 首')
    a = ap.parse_args(argv)

    key = json.load(open(KEY_JSON, encoding='utf-8'))
    scores = split_merged(MERGED)
    rows = []
    for it in key['items']:
        sc = scores.get(it['code'])
        if sc is None:
            print('  ! 缺 %s' % it['code'])
            continue
        tp, mode = tonality(sc)
        m = metrics(sc, tp, mode)
        m.update(code=it['code'], truth=it['truth'], mode=mode,
                 sharps=it.get('sharps'), tonic_pc=tp)
        rows.append(m)
    rows.sort(key=lambda r: r['code'])
    mach = [r for r in rows if r['truth'] == 'machine']
    hum = [r for r in rows if r['truth'] == 'human']
    print('盲评包：机器 %d 首 / 人工 %d 首' % (len(mach), len(hum)))

    print('扫参考池（训练外真实巴赫）…')
    ref_rows = []
    for bwv, sc in reference_pool(24 if a.quick else 60):
        tp, mode = tonality(sc)
        m = metrics(sc, tp, mode)
        m['source'] = bwv
        ref_rows.append(m)
    print('  参考池 %d 首' % len(ref_rows))

    report = {'round': '第 2 轮（两组均为完整曲）', 'reference_pool_size': len(ref_rows),
              'items': rows, 'metrics': {}}
    for grp, names in GROUPS.items():
        report['metrics'][grp] = {}
        for k in names:
            d = DIRECTION[k]
            mp = [r[k] * d for r in mach]
            hp = [r[k] * d for r in hum]
            rp = [r[k] * d for r in ref_rows]
            point, lo, hi = bootstrap_ci([a_ - b_ for a_ in mp for b_ in hp],
                                         n_boot=2000, random_state=3)
            report['metrics'][grp][k] = {
                'direction': d, 'raw_name': k,
                'machine': [round(r[k], 4) for r in mach],
                'human_blind': [round(r[k], 4) for r in hum],
                'machine_mean': round(float(np.mean(mp)), 4),
                'human_mean': round(float(np.mean(hp)), 4),
                'ref_mean': round(float(np.mean(rp)), 4) if rp else None,
                'ref_median': round(float(np.median(rp)), 4) if rp else None,
                'auc_blind': round(auc(mp, hp), 3),
                'p_perm': round(perm_p(mp, hp), 4),
                'auc_vs_ref': round(auc(mp, rp), 3) if rp else None,
                'ref_p_median': round(ref_pvalue(mp, rp), 4) if rp else None,
                'diff_ci95': [round(lo, 4), round(point, 4), round(hi, 4)],
            }

    json.dump(report, open(OUT_JSON, 'w'), ensure_ascii=False, indent=1)

    lines = ['# 机器味指标报告（第 2 轮：两组均为完整曲）', '',
             '指标方向已统一为「越大越像机器」。`AUC(12v8)` = 机器组 vs 盲评人组 8 首；'
             '`p` = 标签置换检验（单侧，20000 次）；`AUC(机 vs 池)` 与 `p池` = '
             '与 %d 首训练外真实巴赫作品的比较（参考池只作分布参照，不参与胜负判定）。'
             % len(ref_rows), '']
    for grp, names in GROUPS.items():
        lines += ['## %s' % grp, '',
                  '| 指标 | 机器均值 | 盲评人组均值 | 参考池均值 | AUC(12v8) | p | AUC(机vs池) | p池 |',
                  '|---|---|---|---|---|---|---|---|']
        for k in names:
            m = report['metrics'][grp][k]
            fmt = lambda x: ('%.3f' % x) if x is not None else '—'   # noqa: E731
            lines.append('| `%s` | %.3f | %.3f | %s | %.2f | %.4f | %s | %s |'
                         % (k, m['machine_mean'], m['human_mean'],
                            fmt(m['ref_mean']), m['auc_blind'], m['p_perm'],
                            fmt(m['auc_vs_ref']), fmt(m['ref_p_median'])))
        lines.append('')
    md = '\n'.join(lines)
    open(OUT_MD, 'w').write(md)
    print(md)
    print('→ %s\n→ %s' % (OUT_JSON, OUT_MD))
    return report


if __name__ == '__main__':
    main()
