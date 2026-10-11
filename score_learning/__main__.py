# -*- coding: utf-8 -*-
"""CLI: python -m score_learning — writes directly into genre style packs."""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _resolve_pack(family: str, pack_id: str, override: str = '') -> str:
    from score_learning.publish import genre_pack_dir
    pack = override or genre_pack_dir(family, pack_id)
    meta = os.path.join(pack, 'meta.yaml')
    if not os.path.isfile(meta):
        raise SystemExit(f'风格包不存在：{meta}\n请先创建 genre/packs/{family}/{pack_id}/')
    return pack


def cmd_analyze(args):
    from score_learning.analyzer import analyze_path
    feat = analyze_path(args.path, genre_hint=args.genre or '')
    text = json.dumps(feat, ensure_ascii=False, indent=2)
    if args.o:
        os.makedirs(os.path.dirname(os.path.abspath(args.o)) or '.', exist_ok=True)
        with open(args.o, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f'Wrote {args.o}')
    else:
        print(text)


def cmd_corpus(args):
    from score_learning.corpus import analyze_corpus
    pack_id = args.pack_id or args.genre
    if not pack_id or not args.family:
        raise SystemExit('corpus 需要 --family 与 --pack-id（或 --genre）')
    pack = _resolve_pack(args.family, pack_id, args.o or '')
    feat_dir = os.path.join(pack, 'features')
    features = analyze_corpus(args.path, genre_hint=args.genre or pack_id, features_dir=feat_dir)
    print(f'Analyzed {len(features)} scores → {feat_dir}')


def _mine_and_update(pack: str, family: str, pack_id: str, genre: str,
                     expert_path: str, no_llm: bool,
                     features_override=None):
    from score_learning.aggregate import aggregate_features
    from score_learning.corpus import load_features_dir
    from score_learning.miner import mine_techniques, techniques_fallback
    from score_learning.publish import publish_to_genre_pack
    from score_learning.synthesizer import build_skill_md

    if features_override is not None:
        features = list(features_override)
    else:
        feat_dir = os.path.join(pack, 'features')
        features = load_features_dir(feat_dir)
    if not features:
        raise SystemExit(f'无特征：{pack}/features')
    stats = aggregate_features(features)
    expert_text = ''
    expert = expert_path or os.path.join(pack, 'meta.yaml')
    if os.path.isfile(expert):
        with open(expert, encoding='utf-8') as f:
            expert_text = f.read()
    if no_llm:
        techniques = techniques_fallback(stats, genre)
    else:
        try:
            techniques = mine_techniques(stats, genre, expert_meta_text=expert_text)
        except Exception as e:
            print(f'LLM miner unavailable ({e}); using statistical fallback.')
            techniques = techniques_fallback(stats, genre)

    # Write EMPIRICAL_SKILL.md into the style pack, then merge meta.yaml
    skill_md = build_skill_md(genre, stats, techniques)
    skill_path = os.path.join(pack, 'EMPIRICAL_SKILL.md')
    with open(skill_path, 'w', encoding='utf-8') as f:
        f.write(skill_md)

    pub = publish_to_genre_pack(
        family, pack_id, stats, techniques, skill_md_path=skill_path,
        features=features,
    )
    return stats, techniques, pub


def cmd_mine(args):
    pack_id = args.pack_id or args.genre
    if not pack_id:
        raise SystemExit('需要 --pack-id 或 --genre')
    pack = _resolve_pack(args.family, pack_id, args.pack or '')
    genre = args.genre or pack_id
    stats, techniques, pub = _mine_and_update(
        pack, args.family, pack_id, genre, args.expert or '', args.no_llm,
    )
    print(f'Updated style skill: {pack} ({stats.get("total_scores")} scores)')
    for k, v in pub.items():
        print(f'  {k}: {v}')


def cmd_learn(args):
    pack_id = args.pack_id or args.genre
    if not pack_id:
        raise SystemExit('需要 --pack-id 或 --genre')
    pack = _resolve_pack(args.family, pack_id, args.o or '')
    from score_learning.corpus import analyze_corpus

    feat_dir = os.path.join(pack, 'features')
    features = analyze_corpus(
        args.path, genre_hint=args.genre or pack_id, features_dir=feat_dir,
    )
    if not features:
        print('No MusicXML/MIDI found.')
        return 1
    genre = args.genre or pack_id
    expert = args.expert or os.path.join(pack, 'meta.yaml')
    stats, techniques, pub = _mine_and_update(
        pack, args.family, pack_id, genre, expert, args.no_llm,
    )
    print(f'Updated style skill: {pack} ({len(features)} scores)')
    for k, v in pub.items():
        print(f'  {k}: {v}')
    return 0


def cmd_list_sources(args):
    from score_learning.sources import CORPUS_ROOT, list_sources, SOURCES
    rows = list_sources()
    print(f'{"id":<18} {"family/pack":<28} {"许可":<28} 需确认')
    for r in rows:
        pack = f'{r["family"]}/{r["pack_id"]}'
        print(f'{r["id"]:<18} {pack:<28} {r["license"]:<28} '
              f'{"是" if r["ack_required"] else "否"}')
    print(f'\n语料根目录：{CORPUS_ROOT}（不进版本控制）')
    print('可用源：' + '、'.join(sorted(SOURCES)))
    return 0


def cmd_fetch(args):
    from score_learning.sources import fetch_source, SOURCES
    targets = sorted(SOURCES) if args.all else list(args.source or [])
    if not targets:
        raise SystemExit('指定 --all 或给出源 id；可用：'
                         + '、'.join(sorted(SOURCES)))
    rc = 0
    for sid in targets:
        try:
            res = fetch_source(
                sid, root=args.out or None, limit=args.limit,
                refresh=args.refresh, accept_license=args.accept_license,
            )
            print(res.summary())
        except PermissionError as e:
            print(f'[skip] {sid}: {e}')
            rc = 1
        except Exception as e:
            print(f'[fail] {sid}: {type(e).__name__}: {e}')
            rc = 1
    return rc


def cmd_list_m21(args):
    from score_learning.music21_corpus import list_configured_genres, search_genre_hits
    rows = list_configured_genres()
    print(f'{"pack_id":<18} {"family":<12} {"queries"}')
    for r in rows:
        print(f"{r['pack_id']:<18} {r['family']:<12} {r['queries']}")
    if getattr(args, 'count', False):
        print('\nCounting music21 hits (may take a minute)…')
        print(f'{"pack_id":<18} hits')
        for r in rows:
            pid = str(r['pack_id'])
            try:
                n = len(search_genre_hits(pid, limit=int(r.get('max') or 40)))
            except Exception as e:
                n = f'err:{e}'
            print(f'{pid:<18} {n}')


def cmd_learn_m21(args):
    """Filter music21 corpus by genre → export → learn into genre pack."""
    from score_learning.music21_corpus import (
        GENRE_CORPUS_QUERIES, export_hits, list_configured_genres,
        resolve_pack_ids, search_genre_hits,
    )
    from score_learning.corpus import analyze_corpus

    targets = []
    if args.all:
        seen_packs = set()
        for r in list_configured_genres():
            pid = str(r['pack_id'])
            if pid in seen_packs:
                continue
            seen_packs.add(pid)
            targets.append(pid)
    else:
        pid = args.pack_id or args.genre
        if not pid:
            raise SystemExit('指定 --pack-id / --genre，或使用 --all')
        targets.append(pid)

    root_export = args.export_dir or os.path.join(ROOT, 'scores', 'corpus', 'm21_by_genre')
    min_scores = int(args.min_scores or 3)

    for raw_id in targets:
        if raw_id not in GENRE_CORPUS_QUERIES and raw_id not in {
            str(resolve_pack_ids(k)[1]) for k in GENRE_CORPUS_QUERIES
        }:
            # allow pack_id that is the resolved id
            key = raw_id
            for k, cfg in GENRE_CORPUS_QUERIES.items():
                if cfg.get('pack_id') == raw_id or k == raw_id:
                    key = k
                    break
            else:
                print(f'[skip] 未配置曲风查询: {raw_id}')
                continue
        else:
            key = raw_id

        try:
            family, pack_id = resolve_pack_ids(key)
        except KeyError:
            print(f'[skip] 未知: {raw_id}')
            continue
        if args.family:
            family = args.family

        print(f'\n=== {family}/{pack_id} (query key={key}) ===')
        hits = search_genre_hits(key, limit=args.limit)
        print(f'corpus hits: {len(hits)}')
        if len(hits) < min_scores:
            print(f'[skip] 命中 {len(hits)} < min_scores={min_scores}')
            continue

        out_dir = os.path.join(root_export, pack_id)
        if args.replace_features and os.path.isdir(out_dir):
            for name in os.listdir(out_dir):
                fp = os.path.join(out_dir, name)
                if os.path.isfile(fp):
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
        written = export_hits(hits, out_dir)
        print(f'exported: {len(written)} → {out_dir}')
        if len(written) < min_scores:
            print(f'[skip] 成功导出 {len(written)} < {min_scores}')
            continue

        try:
            pack = _resolve_pack(family, pack_id)
        except SystemExit as e:
            print(f'[skip] {e}')
            continue

        # Optional: clear old features if --replace-features
        feat_dir = os.path.join(pack, 'features')
        if args.replace_features and os.path.isdir(feat_dir):
            for name in os.listdir(feat_dir):
                if name.endswith('.json'):
                    try:
                        os.remove(os.path.join(feat_dir, name))
                    except OSError:
                        pass

        features = analyze_corpus(
            out_dir, genre_hint=pack_id, features_dir=feat_dir,
        )
        ok = [f for f in features if not (f.get('metadata') or {}).get('error')]
        print(f'analyzed ok: {len(ok)}/{len(features)}')
        if len(ok) < min_scores:
            print(f'[skip] 有效分析不足')
            continue

        # Mine from THIS run's feature list only (not leftover files on disk)
        genre_name = args.genre or pack_id
        stats, techniques, pub = _mine_and_update(
            pack, family, pack_id, genre_name, '', args.no_llm,
            features_override=ok,
        )
        print(f'Updated style skill {family}/{pack_id} '
              f'({stats.get("total_scores")} scores)')
        for k, v in pub.items():
            print(f'  {k}: {os.path.relpath(v, ROOT)}')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog='score_learning',
        description='Score → Technique → 直接更新 genre/packs 风格 Skill',
    )
    sub = p.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('analyze', help='Analyze one score → feature JSON')
    a.add_argument('path')
    a.add_argument('-o', help='output JSON path')
    a.add_argument('--genre', default='')
    a.set_defaults(func=cmd_analyze)

    c = sub.add_parser('corpus', help='Analyze folder → genre pack features/')
    c.add_argument('path')
    c.add_argument('--family', required=True)
    c.add_argument('--genre', default='')
    c.add_argument('--pack-id', default='')
    c.add_argument('-o', help='override pack directory')
    c.set_defaults(func=cmd_corpus)

    m = sub.add_parser('mine', help='Mine features/ in genre pack → update meta.yaml')
    m.add_argument('--family', required=True)
    m.add_argument('--pack-id', default='')
    m.add_argument('--genre', default='')
    m.add_argument('--pack', default='', help='override pack directory')
    m.add_argument('--expert', default='', help='meta.yaml path (default: pack meta)')
    m.add_argument('--no-llm', action='store_true')
    m.set_defaults(func=cmd_mine)

    l = sub.add_parser('learn', help='语料文件夹 → 分析 → 挖掘 → 更新风格包')
    l.add_argument('path', help='folder of MusicXML/MIDI')
    l.add_argument('--family', required=True)
    l.add_argument('--genre', default='')
    l.add_argument('--pack-id', default='')
    l.add_argument('--expert', default='')
    l.add_argument('-o', help='override pack directory')
    l.add_argument('--no-llm', action='store_true')
    l.set_defaults(func=cmd_learn)

    lm = sub.add_parser(
        'learn-m21',
        help='按曲风筛选 music21 corpus → 导出 → 更新对应风格包（非全库混学）',
    )
    lm.add_argument('--pack-id', default='', help='e.g. chorale / fugue / minuet')
    lm.add_argument('--genre', default='')
    lm.add_argument('--family', default='', help='override family')
    lm.add_argument('--all', action='store_true', help='学习所有已配置曲风')
    lm.add_argument(
        '--limit', type=int, default=None,
        help='每曲风最多导出多少首（默认不封顶=该曲风全 corpus 命中）',
    )
    lm.add_argument('--min-scores', type=int, default=3)
    lm.add_argument('--export-dir', default='')
    lm.add_argument('--replace-features', action='store_true',
                    help='清空该风格包旧 features 再写入')
    lm.add_argument('--no-llm', action='store_true')
    lm.set_defaults(func=cmd_learn_m21)

    ls = sub.add_parser('list-m21', help='列出曲风查询配置（加 --count 统计命中）')
    ls.add_argument('--count', action='store_true')
    ls.set_defaults(func=cmd_list_m21)

    lsrc = sub.add_parser('list-sources', help='列出可用外部语料库及其许可')
    lsrc.set_defaults(func=cmd_list_sources)

    fet = sub.add_parser(
        'fetch', help='下载并整理外部语料库到 scores/corpus/<源>/')
    fet.add_argument('source', nargs='*', help='语料源 id，或 --all')
    fet.add_argument('--all', action='store_true', help='采集全部已配置的源')
    fet.add_argument(
        '--accept-license', action='store_true',
        help='确认已阅读并接受相关条款（部分源必需）')
    fet.add_argument('--limit', type=int, default=None,
                     help='只取前 N 份（调试用；不指定则取全库）')
    fet.add_argument('--refresh', action='store_true',
                     help='已有文件也重新采集覆盖')
    fet.add_argument('--out', default='', help='覆盖语料根目录')
    fet.set_defaults(func=cmd_fetch)

    args = p.parse_args(argv)
    rc = args.func(args)
    return 0 if rc is None else rc


if __name__ == '__main__':
    raise SystemExit(main())
