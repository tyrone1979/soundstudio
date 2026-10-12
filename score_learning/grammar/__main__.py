# -*- coding: utf-8 -*-
"""命令行：

    python -m score_learning.grammar learn            # 学习巴赫众赞歌 → grammar.yaml / grammar.md
    python -m score_learning.grammar compose --key 1 --mode major --phrases 6 --seed 0 \
        --layout pickup --out scores/grammar_chorale.musicxml
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import script as gscript


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='score_learning.grammar')
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('learn')
    a.add_argument('--out', default=gscript.DEFAULT_YAML)
    a.add_argument('--no-cache', action='store_true')
    a.add_argument('--smoother', default='wb',
                   choices=('wb', 'jm', 'ad', 'kn', 'mkn'),
                   help='拍层和弦转移的估计器。**发布版用 jm**（论文 E1：'
                        '在 wb 之上再降 12–14%%），默认 wb 是为了兼容历史产出；'
                        '若要让 grammar.yaml 与论文数字一致，必须显式 --smoother jm')
    a.add_argument('--lam', type=float, default=None,
                   help='插值系数（jm/ad/kn 有效），缺省用各类默认（jm 为 0.5）')
    c = sub.add_parser('compose')
    c.add_argument('--grammar', default=gscript.DEFAULT_YAML)
    c.add_argument('--key', type=int, default=0, help='调号升降数（G 大调 = 1，F 大调 = -1）')
    c.add_argument('--mode', default='major', choices=('major', 'minor'))
    c.add_argument('--phrases', type=int, default=0)
    c.add_argument('--seed', default='0')
    c.add_argument('--barform', choices=('auto', 'yes', 'no'), default='auto')
    c.add_argument('--layout', choices=('auto', 'pickup', 'downbeat'), default='auto',
                   help='弱起 / 强拍起；auto 按语料比例抽签')
    c.add_argument('--bars', type=int, default=0, help='完整小节数（弱起时另加一拍弱起小节）')
    c.add_argument('--out', default='')
    c.add_argument('--trace', default='', help='追溯 JSON 输出路径')
    args = ap.parse_args(argv)

    if args.cmd == 'learn':
        from .analyze import analyze_corpus
        from .learn import learn
        pieces = analyze_corpus(cache=None if args.no_cache else
                                os.path.join(gscript.ROOT, 'runs', 'grammar_cache',
                                             'bach_chorales.json'))
        sc = learn(pieces, smoother=args.smoother,
                   smoother_kw=({'lam': args.lam} if args.lam is not None else None))
        md = os.path.splitext(args.out)[0] + '.md'
        gscript.save(sc, args.out, md)
        print(f'已写入 {args.out} 与 {md}（估计器 {args.smoother}'
              f'{"" if args.lam is None else f", lam={args.lam}"}）')
        print(json.dumps(sc['evaluation'], ensure_ascii=False, indent=1))
        return 0

    from .compose import compose
    sc = gscript.load(args.grammar)
    bf = None if args.barform == 'auto' else args.barform == 'yes'
    res = compose(sc, args.key, args.mode, args.phrases or None, args.seed, bf,
                  total_bars=args.bars or None, layout=args.layout)
    print('弱起（一拍弱起小节）' if res['plan']['layout'] == 'pickup' else '强拍起')
    for p in res['phrases']:
        print(f'第{p["phrase"]}句 [{p["plan_rule"]} → 终止于 {p["target"]}，{p["formula"]}，'
              f'{p["contour"]} {p["contour_name"]}] {p["harmony"]}')
    print(f'违例 {len(res["violations"])}，种子分支 {res["seed_used"]}')
    if args.out:
        from music21 import converter  # noqa: F401
        sys.path.insert(0, gscript.ROOT)
        from llm_compose import spec_to_music21
        s = spec_to_music21(res['spec'])
        fmt = 'midi' if args.out.endswith('.mid') else 'musicxml'
        s.write(fmt, fp=args.out)
        print(f'已写入 {args.out}')
    if args.trace:
        with open(args.trace, 'w', encoding='utf-8') as f:
            json.dump({k: res[k] for k in ('plan', 'phrases', 'seed_used')} |
                      {'beats': [{k: b[k] for k in ('bar', 'beat', 'roman', 'midi', 'eighth',
                                                    'rule', 'harmony_rule', 'pos')}
                                 for b in res['beats']]},
                      f, ensure_ascii=False, indent=1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
