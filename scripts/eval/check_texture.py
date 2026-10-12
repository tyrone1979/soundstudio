# -*- coding: utf-8 -*-
"""生成器织体/旋律/乐句的快速体检（不走盲评包，用于开发迭代）。

对照训练外的真实巴赫作品，检查生成曲在听审提到的三个维度上落在哪里：

* **织体**：``mean_voices_per_attack``——每次起音平均有几个声部在动。
  机器 3.33、人类 2.90（盲评包实测，AUC 0.94），这是「织体单一」的主证据。
* **旋律**：级进占比、动机（3 音）重复率、同音反复率。
* **乐句**：每 8 小节终止式数、终止式间隔的规整度。

用法::

    venv/bin/python check_texture.py                # 现状
    venv/bin/python check_texture.py --ab           # TIE_HOLDS 开/关对照
    venv/bin/python check_texture.py --n 20 --ref 40
"""
from __future__ import annotations

import argparse
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import numpy as np                                                    # noqa: E402

import blind_review_metrics as bm                                     # noqa: E402
from score_learning.grammar import compose as gc                       # noqa: E402
from score_learning.grammar import script as gscript                  # noqa: E402

#: 报告里关心的指标（顺序即报告顺序）
KEYS = ['mean_voices_per_attack', 'attack_sync_rate', 'voice_density_cv',
        'unison_octave_pair_ratio', 'melodic_step_ratio', 'melodic_leap_ratio',
        'motif3_repeat_rate', 'same_pitch_run_ratio', 'contour_span',
        'cadence_per_8bars', 'cadence_gap_cv', 'cadence_on_mod4_rate']
LOWER_IS_MACHINE = {'voice_density_cv', 'melodic_step_ratio', 'contour_span',
                    'cadence_per_8bars', 'cadence_on_mod4_rate'}


def spec_to_sc(res):
    """compose 的 spec → music21 Score（按 events 顺序累加 offset）。"""
    from music21 import note, stream
    sc = stream.Score()
    for p in res['spec']['parts']:
        part = stream.Part()
        part.id = p['name']
        t = 0.0
        for nm, dur, _f in p['events']:
            n = note.Note(nm)
            n.quarterLength = {'quarter': 1.0, 'half': 2.0, 'eighth': 0.5}.get(dur, 1.0)
            part.insert(t, n)
            t += n.quarterLength
        sc.insert(0, part)
    return sc


def gen(n, bars=16):
    script = gscript.load()
    out = []
    for i in range(n):
        mode = 'major' if i % 2 == 0 else 'minor'
        res = gc.compose(script, 0, mode, seed=f'check{i:03d}', total_bars=bars)
        out.append((f'gen{i:02d}', res))
    return out


def gen_ref(n, bars_lo=13, bars_hi=20):
    """训练外的真实巴赫众赞歌作对照。"""
    from music21 import bar, corpus
    from blind_review_export import _pick_human_candidates, strip_partial_bars
    names = _pick_human_candidates()
    random.Random(11).shuffle(names)
    out = []
    for bwv in names:
        if len(out) >= n:
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
        nb = len(sc.parts[0].getElementsByClass('Measure'))
        if bars_lo <= nb <= bars_hi:
            out.append((bwv, sc))
    return out


def measure(rows):
    out = []
    for tag, obj in rows:
        sc = spec_to_sc(obj) if isinstance(obj, dict) else obj
        tp, mode = bm.tonality(sc)
        m = bm.metrics(sc, tp, mode)
        m['tag'] = tag
        out.append(m)
    return out


def report(title, rows, ref):
    print(f'\n===== {title}（n={len(rows)}；人类对照 n={len(ref)}）=====')
    print(f'{"指标":26s} {"本组":>15s} {"人类对照":>15s}   判定')
    for k in KEYS:
        v = np.array([r[k] for r in rows if k in r], dtype=float)
        w = np.array([r[k] for r in ref if k in r], dtype=float)
        if not len(v) or not len(w):
            continue
        vm, vs, wm, ws = v.mean(), v.std(), w.mean(), w.std()
        lo, hi = wm - 2 * ws, wm + 2 * ws
        inside = lo <= vm <= hi
        if inside:
            verdict = '一致'
        elif k in LOWER_IS_MACHINE:
            verdict = '偏高(像机器)' if vm > hi else '偏低'
        else:
            verdict = '偏低(像机器)' if vm < lo else '偏高'
        print(f'{k:26s} {vm:7.3f}±{vs:4.3f} {wm:7.3f}±{ws:4.3f}   {verdict}')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=16)
    ap.add_argument('--ref', type=int, default=30)
    ap.add_argument('--bars', type=int, default=16)
    ap.add_argument('--ab', action='store_true', help='TIE_HOLDS 开/关对照')
    a = ap.parse_args(argv)

    ref = measure(gen_ref(a.ref))
    if a.ab:
        gc.TIE_HOLDS = False
        report('生成曲 · TIE_HOLDS=False（改动前）', measure(gen(a.n, a.bars)), ref)
        gc.TIE_HOLDS = True
    report('生成曲 · TIE_HOLDS=%s' % gc.TIE_HOLDS, measure(gen(a.n, a.bars)), ref)
    return 0


if __name__ == '__main__':
    sys.exit(main())
