# -*- coding: utf-8 -*-
"""评测：每个子风格 × 调式生成 N 首，统计成功率 / 违例，并与语料对比几项可解释的统计量。

    python -m score_learning.leadsheet.evaluate [N]

对比项（生成 vs 语料留出集）：
    ct_strong   强拍音是和弦音的比例
    nct_step    非和弦音级进进入或离开的比例
    abs_iv      平均音程（半音，八度以内；超过八度的比例另列 over_octave）
    leap        跳进（≥ 5 半音）比例
    harm_ppl    和声进行在规则模型下的困惑度（生成 vs 留出集；生成的应不高于留出集）
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Any, Dict, List

from score_learning.grammar.learn import is_heldout
from score_learning.grammar.theory import tonic_from_sharps, tonic_letter_pc

from . import script as S
from .analyze import ROOT, analyze_style, strength
from .check import decode_melody, melody_index
from .compose import SubGrammar, compose
from .learn import chord_at, harmony_events, last_chord_idx, note_class

OUT = os.path.join(ROOT, 'runs', 'grammar_eval', 'leadsheet_summary.json')


def _mel_stats(seq) -> Dict[str, float]:
    """seq: [(midi, class, strength)]（休止为 None）。"""
    ct = [c == 'CT' for m, c, st in (x for x in seq if x) if st == 'S']
    ivs, nct, huge = [], [], []
    for k in range(1, len(seq)):
        a, b = seq[k - 1], seq[k]
        if a is None or b is None:
            continue
        # 超过八度的"跳进"多是乐句 / 声部切换（流行语料约 10%），单列，不计入平均音程
        huge.append(abs(b[0] - a[0]) > 12)
        if not huge[-1]:
            ivs.append(abs(b[0] - a[0]))
        if b[1] != 'CT':
            nxt = seq[k + 1] if k + 1 < len(seq) else None
            out = nxt is not None and 1 <= abs(nxt[0] - b[0]) <= 2
            nct.append(1 <= abs(b[0] - a[0]) <= 2 or out)
    f = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    return {'ct_strong': f(ct), 'nct_step': f(nct), 'abs_iv': f(ivs),
            'leap': f([i >= 5 for i in ivs]), 'over_octave': f(huge)}


def corpus_stats(pieces, sub, mode) -> Dict[str, Any]:
    seqs = []
    for p in pieces:
        if p['sub'] != sub or p['mode'] != mode or not is_heldout(p['id']):
            continue
        prev, last = None, last_chord_idx(p)
        for i, b in enumerate(p['bars'][:last + 1]):
            for on, _d, rel, m in b['notes']:
                if m is None:
                    seqs.append(None)
                    continue
                tok = chord_at(b, on, prev)
                seqs.append((m, note_class(rel, tok, mode), strength(on, p['bar_ql'], p['beat_ql'])))
            if b['chords']:
                prev = b['chords'][-1][1]
        seqs.append(None)
    return _mel_stats(seqs)


def gen_stats(g: SubGrammar, res) -> List:
    spec = res['spec']
    line = decode_melody(spec['parts'][melody_index(g.texture)]['events'],
                         float(spec.get('pickup_ql') or 0.0))
    slots = res['slots']
    bq = g.bar_ql
    t_of = [((s['bar'] - 1) * bq + (s['beat'] - 1) * g.beat_ql, s['tok']) for s in slots]
    seq = []
    for t, _d, m in line:
        if m is None:
            seq.append(None)
            continue
        tok = t_of[0][1]
        for ts, tk in t_of:
            if ts <= t + 1e-6:
                tok = tk
        st = strength(t % bq, bq, g.beat_ql) if t >= 0 else 'W'
        seq.append((m, note_class((m - g.tonic_pc) % 12, tok, g.mode), st))
    return seq


def _harm_ppl(g: SubGrammar, events) -> float:
    lp = [g.trigram(a, b, pos, c) for a, b, pos, c in events]
    return round(math.exp(-sum(lp) / max(len(lp), 1)), 2)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    n = int(argv[0]) if argv else 10
    summary: Dict[str, Any] = {}
    for style in S.available():
        sc = S.load(style)
        pieces = analyze_style(style, progress=False)
        for sub, d in sc['subs'].items():
            for mode in d['vocab']:
                g = SubGrammar(sc, sub, mode)
                ok, att, secs, seqs, ev_gen = 0, [], [], [], []
                fails = []
                for seed in range(n):
                    t = time.time()
                    try:
                        res = compose(sc, sub, mode, 0, 32 if style != 'madrigal' else 24,
                                      seed=f'eval{seed}')
                    except RuntimeError as e:
                        fails.append(str(e)[:200])
                        continue
                    secs.append(time.time() - t)
                    ok += 1
                    att.append(res['attempts'])
                    g.tonic_pc = tonic_letter_pc(tonic_from_sharps(0, mode))[1]
                    seqs += gen_stats(g, res) + [None]
                    p2, p1 = 'BOS', 'BOS'
                    for s in res['slots']:
                        ev_gen.append((p2, p1, s['pos'], s['tok']))
                        p2, p1 = p1, s['tok']
                vset = set(g.vocab)
                held = [e for p in pieces if p['sub'] == sub and p['mode'] == mode
                        and p['meter'] == d['meter'] and is_heldout(p['id'])
                        for e in harmony_events(p, vset)]
                row = {
                    'ok': ok, 'n': n, 'attempts_mean': round(sum(att) / max(len(att), 1), 2),
                    'sec_mean': round(sum(secs) / max(len(secs), 1), 3),
                    'generated': _mel_stats(seqs), 'corpus_heldout': corpus_stats(pieces, sub, mode),
                    'harm_ppl_generated': _harm_ppl(g, ev_gen) if ev_gen else None,
                    'harm_ppl_heldout': _harm_ppl(g, held) if held else None,
                    'fails': fails[:3],
                }
                summary[f'{style}/{sub}/{mode}'] = row
                gs, cs = row['generated'], row['corpus_heldout']
                print(f'{style}/{sub}/{mode}: {ok}/{n} 成功，平均尝试 {row["attempts_mean"]}，'
                      f'{row["sec_mean"]}s/首 | 强拍和弦音 {gs["ct_strong"]} vs {cs["ct_strong"]} | '
                      f'非和弦音级进 {gs["nct_step"]} vs {cs["nct_step"]} | 平均音程 {gs["abs_iv"]} vs '
                      f'{cs["abs_iv"]} | 跳进 {gs["leap"]} vs {cs["leap"]} | 和声困惑度 '
                      f'{row["harm_ppl_generated"]} vs {row["harm_ppl_heldout"]}')
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f'已写入 {os.path.relpath(OUT, ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
