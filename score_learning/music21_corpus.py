# -*- coding: utf-8 -*-
"""
Select music21 corpus scores by style (genre pack) and export MusicXML for learning.

Principle: never dump the whole corpus into one skill — filter by genre queries first.
Also skip multi-tune anthologies (airdsAirs books, Essen chunks, etc.).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# Maps our genre pack_id → music21 corpus.search queries (OR-ed, de-duplicated).
# Principle: use the FULL corpus slice for that genre (max=None), never mix genres.
# Cap only via CLI --limit when needed.
#
# Stock music21 is rich in folk ABC (ryansMammoth / oneills) and Bach chorales;
# classical dances/nocturnes are sparse — those packs simply skip when < min_scores.
GENRE_CORPUS_QUERIES: Dict[str, Dict[str, object]] = {
    'chorale': {
        'family': 'sacred',
        'queries': ['chorale'],
        'max': None,
        'description': 'Chorale scores in music21 corpus',
    },
    'hymn': {
        'family': 'sacred',
        'queries': ['hymn'],
        'max': None,
        'description': 'Hymn-tagged corpus entries',
    },
    'fugue': {
        'family': 'academic',
        # stock 几乎无 WTC；主路径用 classical-kern/bach_wtc_fugues
        'queries': ['fugue', 'bach'],
        'path_regex': r'fugue|fuga|wtc',
        'max': None,
        'description': 'Fugues (prefer kern WTC; stock bach is mostly chorales)',
    },
    'minuet': {
        'family': 'dance',
        'queries': ['minuet', 'menuet', 'menuett'],
        'max': None,
        'description': 'Minuets (sparse in stock corpus)',
    },
    'waltz': {
        'family': 'dance',
        'queries': ['waltz', 'walzer', 'valse'],
        'max': None,
        'description': 'Waltzes (stock sparse; prefer Nottingham waltzes via learn)',
    },
    'mazurka': {
        'family': 'dance',
        'queries': ['mazurka', 'chopin'],
        'path_regex': r'mazurka|chopin/',
        'max': None,
        'description': 'Mazurkas (Chopin in stock corpus)',
    },
    'march': {
        'family': 'dance',
        'queries': ['march', 'marsch'],
        'max': None,
        'description': 'Marches (single-tune folk ABC preferred)',
    },
    'nocturne': {
        'family': 'intimate',
        'queries': ['nocturne'],
        'max': None,
        'description': 'Nocturnes',
    },
    'prelude': {
        'family': 'academic',
        'pack_id': 'piano-prelude',
        'queries': ['prelude', 'praeludium'],
        'max': None,
        'description': 'Preludes',
    },
    'piano-prelude': {
        'family': 'academic',
        'queries': ['prelude', 'praeludium'],
        'max': None,
        'description': 'Preludes',
    },
    'jig': {
        'family': 'folk',
        'pack_id': 'celtic-jig',
        'queries': ['jig'],
        'max': None,
        'description': 'Jigs (ryansMammoth / oneills single-tune ABC)',
    },
    'celtic-jig': {
        'family': 'folk',
        'queries': ['jig'],
        'max': None,
        'description': 'Jigs (ryansMammoth / oneills single-tune ABC)',
    },
    'air': {
        'family': 'folk',
        'pack_id': 'celtic-air',
        'queries': ['air'],
        'max': None,
        'description': 'Airs (single-tune ABC; anthologies excluded)',
    },
    'celtic-air': {
        'family': 'folk',
        'queries': ['air'],
        'max': None,
        'description': 'Airs (single-tune ABC; anthologies excluded)',
    },
    'polka': {
        'family': 'dance',
        'queries': ['polka'],
        'max': None,
        'description': 'Polkas (stock sparse; prefer Nottingham polka filenames)',
    },
    'scherzo': {
        'family': 'academic',
        'queries': ['scherzo'],
        'max': None,
        'description': 'Scherzos',
    },
    'adagio-cantabile': {
        'family': 'academic',
        'queries': ['adagio', 'beethoven', 'cpebach'],
        'path_regex': r'(beethoven/.*/movement2|cpebach/)',
        'max': None,
        'description': 'Adagio cantabile proxies (Beethoven slow mvmt / C.P.E. Bach)',
    },
    'sonata-allegro': {
        'family': 'academic',
        'queries': ['beethoven'],
        'path_regex': r'beethoven/opus18.*/movement[14]',
        'max': None,
        'description': 'Sonata-allegro proxies (Beethoven Op.18 outer movements)',
    },
}

# Multi-tune books / folk databases — never treat as one "score" for a genre skill.
_BULK_PATH_SUBSTR = (
    'airdsairs',
    'essenfolksong',
    'trecento',
    'americanfifeopus',
    'northumbrian',
)
# airds bookN.abc / *opus.*; numeric ranges handled in is_bulk_anthology
_BULK_BASENAME_RE = re.compile(
    r'^(book\d+|.*opus)\.(abc|xml|musicxml|mxl)$',
    re.I,
)
_RANGE_BASENAME_RE = re.compile(
    r'^0*(\d{3,4})-0*(\d{3,4})(?:_[a-z]+)?\.(abc|xml|musicxml|mxl)$',
    re.I,
)
# 导出大小上限。原来是 120 小节，**这会把交响乐/奏鸣曲乐章整体排除在外**
# （海顿、莫扎特交响曲一个快板乐章常见 200~300 小节），等于原生 Corpus 里
# 的古典大型作品永远学不到。放宽到 400；仍超限的走分段取样（见 _slice_long）。
_MAX_MEASURES_EXPORT = 400


def is_bulk_anthology(path: str) -> bool:
    """True if path looks like a multi-tune book / database chunk."""
    key = path.replace('\\', '/').lower()
    base = os.path.basename(key)
    if any(s in key for s in _BULK_PATH_SUBSTR):
        return True
    if _BULK_BASENAME_RE.match(base):
        return True
    m = _RANGE_BASENAME_RE.match(base)
    if m and int(m.group(1)) != int(m.group(2)):
        # 1001-1031.abc = multi-tune; 0731-0731.abc = single tune (keep)
        return True
    return False


@dataclass
class CorpusHit:
    corpus_path: str  # music21 relative path or absolute
    source_path: str
    title: str = ''


def _entry_path(entry) -> str:
    for attr in ('sourcePath', 'corpusPath', 'path'):
        v = getattr(entry, attr, None)
        if v:
            return str(v)
    return str(entry)


def _entry_title(entry) -> str:
    for attr in ('title', 'movementName'):
        v = getattr(entry, attr, None)
        if v:
            return str(v)
    sp = _entry_path(entry)
    return os.path.basename(sp)


def search_genre_hits(pack_id: str, limit: Optional[int] = None) -> List[CorpusHit]:
    """Search music21 metadata bundle for a genre pack_id (single-score files only)."""
    from music21 import corpus

    cfg = GENRE_CORPUS_QUERIES.get(pack_id)
    if not cfg:
        raise KeyError(
            f'未配置曲风查询：{pack_id}。可选：{", ".join(sorted(GENRE_CORPUS_QUERIES))}'
        )
    queries = list(cfg.get('queries') or [])
    path_re = cfg.get('path_regex')
    title_re = cfg.get('title_regex')
    rx = re.compile(str(path_re), re.I) if path_re else None
    trx = re.compile(str(title_re), re.I) if title_re else None
    # Full genre slice by default (max=None). CLI --limit caps when set (>0).
    cfg_max = cfg.get('max')
    if limit is not None and int(limit) > 0:
        max_n: Optional[int] = int(limit)
    elif cfg_max is None or int(cfg_max) <= 0:
        max_n = None
    else:
        max_n = int(cfg_max)

    seen = set()
    hits: List[CorpusHit] = []
    for q in queries:
        try:
            results = list(corpus.search(q))
        except Exception:
            continue
        for entry in results:
            path = _entry_path(entry)
            key = path.replace('\\', '/').lower()
            if key in seen:
                continue
            if is_bulk_anthology(key):
                continue
            if key.endswith(('.py', '.txt', '.md')):
                continue
            title = _entry_title(entry)
            if rx is not None and not (rx.search(key) or rx.search(title)):
                continue
            if trx is not None and not (trx.search(key) or trx.search(title)):
                continue
            seen.add(key)
            hits.append(CorpusHit(
                corpus_path=key,
                source_path=path,
                title=title,
            ))
            if max_n is not None and len(hits) >= max_n:
                return hits
    return hits


def _slice_measures(sc, n_bars: int):
    """截取前 n_bars 小节 —— 用于超出分析上限的大型乐章。

    交响曲快板乐章动辄 200~300+ 小节，整首解析既慢又让统计量被个别超长曲
    主导。取呈示部窗口（通常正是体裁特征最鲜明处）是务实的折中。
    """
    from music21 import stream as m21stream
    try:
        sliced = sc.measures(1, n_bars)
        if sliced is not None:
            out = sliced
            # measures() 可能返回 Part/Score；统一包装成 Score 便于写盘
            if not isinstance(out, m21stream.Score):
                wrap = m21stream.Score()
                wrap.insert(0, out)
                out = wrap
            # 原作成序列的对象（拍号/速度/乐器名）在切片后可能丢失，
            # 补回一部分以保持导出文件的完整性
            try:
                if not out.metadata and sc.metadata:
                    out.insert(0, sc.metadata)
            except Exception:
                pass
            return out
    except Exception:
        pass
    return sc


def export_hits(
    hits: Sequence[CorpusHit],
    out_dir: str,
    *,
    parse_via_corpus: bool = True,
    max_measures: int = _MAX_MEASURES_EXPORT,
) -> List[str]:
    """Parse each hit and write MusicXML into out_dir. Returns written paths."""
    from music21 import converter, corpus

    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []
    for i, hit in enumerate(hits):
        if is_bulk_anthology(hit.corpus_path):
            print(f'  skip [{i}] anthology: {os.path.basename(hit.corpus_path)}')
            continue
        sc = None
        errors = []
        candidates = []
        rel = hit.corpus_path.replace('\\', '/')
        if '/corpus/' in rel:
            rel = rel.split('/corpus/', 1)[1]
        idx = hit.source_path.replace('\\', '/').lower().find('music21/corpus/')
        if idx >= 0:
            rel2 = hit.source_path.replace('\\', '/')[idx + len('music21/corpus/'):]
            candidates.append(rel2)
        candidates.append(rel)
        candidates.append(hit.source_path)
        seen_c = set()
        uniq = []
        for c in candidates:
            if c and c not in seen_c:
                seen_c.add(c)
                uniq.append(c)

        for cand in uniq:
            try:
                if parse_via_corpus and not os.path.isabs(cand):
                    sc = corpus.parse(cand)
                else:
                    sc = converter.parse(cand)
                break
            except Exception as e:
                errors.append(f'{cand}: {e}')
                sc = None
        if sc is None:
            print(f'  skip [{i}] {hit.corpus_path}: {errors[-1] if errors else "parse fail"}')
            continue

        try:
            n_meas = len(list(sc.parts[0].getElementsByClass('Measure'))) if sc.parts else 0
            if n_meas == 0:
                n_meas = len(list(sc.getElementsByClass('Measure')))
        except Exception:
            n_meas = 0
        if max_measures and n_meas > max_measures:
            # 旧行为是「跳过」——那等于把大型古典作品整体排除。改为截取。
            sc = _slice_measures(sc, max_measures)
            print(f'  slice [{i}] {n_meas}→{max_measures} bars: '
                  f'{os.path.basename(hit.corpus_path)}')

        stem = re.sub(r'[^\w.\-]+', '_', os.path.splitext(os.path.basename(hit.corpus_path))[0])
        if not stem:
            stem = f'score_{i:03d}'
        dest = os.path.join(out_dir, f'{stem}.musicxml')
        if os.path.exists(dest):
            dest = os.path.join(out_dir, f'{stem}_{i:03d}.musicxml')
        try:
            sc.write('musicxml', fp=dest)
            written.append(dest)
            print(f'  export {len(written)}: {os.path.basename(dest)}')
        except Exception as e:
            print(f'  write fail {stem}: {e}')
    return written


def resolve_pack_ids(pack_id: str) -> Tuple[str, str]:
    """Return (family, pack_id) for GENRE_CORPUS_QUERIES entry."""
    cfg = GENRE_CORPUS_QUERIES.get(pack_id)
    if not cfg:
        raise KeyError(pack_id)
    family = str(cfg.get('family') or '')
    real_id = str(cfg.get('pack_id') or pack_id)
    return family, real_id


def list_configured_genres() -> List[Dict[str, object]]:
    out = []
    for pid, cfg in sorted(GENRE_CORPUS_QUERIES.items()):
        family, real = resolve_pack_ids(pid)
        if pid != real and pid in ('prelude', 'jig', 'air'):
            continue
        out.append({
            'pack_id': real,
            'family': family,
            'queries': cfg.get('queries'),
            'max': cfg.get('max'),
            'description': cfg.get('description'),
        })
    return out
