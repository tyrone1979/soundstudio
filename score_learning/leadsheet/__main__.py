# -*- coding: utf-8 -*-
"""命令行：

    python -m score_learning.leadsheet learn [folk|jazz|pop|madrigal|all]
    python -m score_learning.leadsheet compose folk jig --key 1 --bars 32 --seed 0 \
        --out scores/grammar/folk_jig.musicxml --trace scores/grammar/folk_jig_trace.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import script as S
from .analyze import ROOT, STYLES


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='score_learning.leadsheet')
    sp = ap.add_subparsers(dest='cmd', required=True)
    a = sp.add_parser('learn')
    a.add_argument('style', nargs='?', default='all', choices=(*STYLES, 'all'))
    a.add_argument('--no-cache', action='store_true')
    lm = sp.add_parser('learn-memory', help='只重算跨段动机回忆统计，写回已有 grammar.yaml')
    lm.add_argument('style', nargs='?', default='all', choices=(*STYLES, 'all'))
    c = sp.add_parser('compose')
    c.add_argument('style', choices=tuple(STYLES))
    c.add_argument('sub', nargs='?', default='')
    c.add_argument('--key', type=int, default=0, help='调号升降数（G 大调 = 1，F 大调 = -1）')
    c.add_argument('--mode', default='', choices=('', 'major', 'minor'))
    c.add_argument('--bars', type=int, default=32)
    c.add_argument('--seed', default='0')
    c.add_argument('--temperature', type=float, default=1.0)
    c.add_argument('--layout', choices=('auto', 'pickup', 'downbeat'), default='auto')
    c.add_argument('--instrument', default='')
    c.add_argument('--out', default='')
    c.add_argument('--trace', default='')
    args = ap.parse_args(argv)

    if args.cmd == 'learn':
        from .analyze import analyze_style
        from .learn import learn
        for st in (STYLES if args.style == 'all' else [args.style]):
            pieces = analyze_style(st, cache=not args.no_cache)
            sc = learn(pieces, st)
            path = S.save(sc, st)
            print(f'{st}：{len(pieces)} 首 → {os.path.relpath(path, ROOT)}')
            for sub, d in sc['subs'].items():
                ev = '；'.join(f'{m} 困惑度 {e["perplexity_script"]}（只用频率 {e["perplexity_unigram"]}）'
                              for m, e in d['evaluation'].items())
                print(f'  {sub}：{ev}；节奏 {d["rhythm"]["mode"]}')
        return 0

    if args.cmd == 'learn-memory':
        from .analyze import analyze_style
        from .learn import relearn_memory
        for st in (S.available() if args.style == 'all' else [args.style]):
            if not S.round_trips(st):
                print(f'{st}：grammar.yaml 含注入字段、重写会改变格式，跳过')
                continue
            sc = relearn_memory(S.load(st), analyze_style(st), st)
            S.save(sc, st)
            print(f'{st}：' + '；'.join(
                f'{sub} 原样 {d["rhythm"]["motif_recall"]["p_same"]:.1%} '
                f'模进 {d["rhythm"]["motif_recall"]["p_shift"]:.1%}'
                for sub, d in sc['subs'].items() if 'motif_recall' in d['rhythm']))
        return 0

    from .compose import compose
    sc = S.load(args.style)
    sub = args.sub or next(iter(sc['subs']))
    mode = args.mode or ('major' if 'major' in sc['subs'][sub]['vocab'] else 'minor')
    res = compose(sc, sub, mode, args.key, args.bars, seed=args.seed,
                  temperature=args.temperature, layout=args.layout,
                  instrument=args.instrument or None)
    p = res['plan']
    print(f'{sc["subs"][sub]["name"]} · 曲式 {p["form"]}（{p["form_rule"]}）· '
          f'{"弱起" if p["layout"] == "pickup" else "强拍起"} · 违例 {len(res["violations"])} · '
          f'种子分支 {res["seed_used"]}')
    for u in res['units']:
        mem = u.get('memory') or ''
        src = f'（{mem}）' if mem and mem != '新材料' else ''
        print(f'第{u["unit"]}段 {u.get("label", u["letter"])}{src} '
              f'[{u["cadence"]} {u["cadence_say"]}] {u["chords"]}')
    if args.out:
        sys.path.insert(0, ROOT)
        from llm_compose import spec_to_music21
        s = spec_to_music21(res['spec'])
        s.write('midi' if args.out.endswith('.mid') else 'musicxml', fp=args.out)
        print(f'已写入 {args.out}')
    if args.trace:
        with open(args.trace, 'w', encoding='utf-8') as f:
            json.dump({k: res[k] for k in ('plan', 'units', 'slots', 'seed_used', 'range')},
                      f, ensure_ascii=False, indent=1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
