# -*- coding: utf-8 -*-
"""E8：自动和弦标注与人工罗马数字分析的一致率。

被测对象是 ``analyze.py`` 给每拍打的和弦标签 ``root:quality:inv``（根音相对主音）。
规则归纳、检查器（和弦成员、导音、七音）都建立在这些标签上，标签错了，错误就会被
写进学到的规则里。这里用公开的人工分析做参照：

    When in Rome（Gotham et al.）的巴赫众赞歌 RomanText 分析，
    https://github.com/MarkGotham/When-in-Rome  ``Corpus/Early_Choral/Bach,_Johann_Sebastian/Chorales``

对齐方式：每首按 ``remote.json`` 里的 music21 文件名对上本项目的作品 ID；
逐拍按（小节号, 拍位）取人工分析当时生效的罗马数字，比较
根音（绝对音级）、和弦性质（大/小/减/增 × 有无七音）、转位。
只比较拍点，半拍上的经过和弦不计；乐谱带反复的作品，本项目的拍序列可能已展开
反复而人工分析没有，因此只纳入 ``has_repeat`` 为假的作品（另报带反复作品的结果作参考）。

数据下载到 ``scores/corpus/when_in_rome/``（不入库，与其它外部语料一致）。

用法::

    venv/bin/python -m score_learning.grammar.chord_agreement
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CACHE = os.path.join(ROOT, 'runs', 'grammar_cache', 'bach_chorales.json')
WIR_DIR = os.path.join(ROOT, 'scores', 'corpus', 'when_in_rome')
OUT = os.path.join(ROOT, 'runs', 'experiments')
RAW = ('https://raw.githubusercontent.com/MarkGotham/When-in-Rome/master/Corpus/'
       'Early_Choral/Bach%2C_Johann_Sebastian/Chorales/{n:03d}/{f}')
N_CHORALES = 371

#: 本项目的和弦性质 → (三和弦类型, 是否七和弦)
OUR_Q = {'M': ('M', False), 'm': ('m', False), 'd': ('d', False), 'A': ('A', False),
         '7': ('M', True), 'M7': ('M', True), 'm7': ('m', True),
         'h7': ('d', True), 'o7': ('d', True)}
M21_TRIAD = {'major': 'M', 'minor': 'm', 'diminished': 'd', 'augmented': 'A'}


# ------------------------------------------------------------------ 数据
def fetch(n_max: int = N_CHORALES) -> int:
    got = 0
    for n in range(1, n_max + 1):
        d = os.path.join(WIR_DIR, f'{n:03d}')
        os.makedirs(d, exist_ok=True)
        for f in ('analysis.txt', 'remote.json'):
            p = os.path.join(d, f)
            if os.path.isfile(p) and os.path.getsize(p) > 0:
                continue
            try:
                with urllib.request.urlopen(RAW.format(n=n, f=urllib.parse.quote(f)),
                                            timeout=30) as r:
                    open(p, 'wb').write(r.read())
            except Exception:  # noqa: BLE001
                continue
        if os.path.isfile(os.path.join(d, 'analysis.txt')):
            got += 1
    return got


def _wir_index() -> Dict[str, str]:
    """music21 作品 ID（如 'bwv269'）→ 分析文件路径。"""
    idx = {}
    if not os.path.isdir(WIR_DIR):
        return idx
    for n in sorted(os.listdir(WIR_DIR)):
        rj = os.path.join(WIR_DIR, n, 'remote.json')
        an = os.path.join(WIR_DIR, n, 'analysis.txt')
        if not (os.path.isfile(rj) and os.path.isfile(an)):
            continue
        for f in json.load(open(rj, encoding='utf-8')).get('music21') or []:
            idx.setdefault(f.rsplit('.', 1)[0], an)
    return idx


def _rn_events(path: str) -> List[Tuple[int, float, Any]]:
    """[(小节号, 拍位(1 起), RomanNumeral)]，按时间排序。"""
    from music21 import converter, roman
    s = converter.parse(path, format='romanText')
    ev = []
    for m in s.recurse().getElementsByClass('Measure'):
        for rn in m.recurse().getElementsByClass(roman.RomanNumeral):
            ev.append((m.number, float(rn.beat), rn))
    ev.sort(key=lambda x: (x[0], x[1]))
    return ev


def _measure_beats(piece: Dict[str, Any]) -> List[Tuple[int, float]]:
    out = []
    mnum = 1 if piece['beats'][0]['downbeat'] else 0
    for i, b in enumerate(piece['beats']):
        if b['downbeat'] and i > 0:
            mnum += 1
        out.append((mnum, float(b['pos']) + 1.0))
    return out


def _human(rn) -> Optional[Tuple[int, str, bool, int]]:
    try:
        root = rn.root().pitchClass
        triad = M21_TRIAD.get(rn.quality)
        if triad is None:
            # 不完整/其它（如 It6、Ger65）
            return None
        seventh = rn.seventh is not None
        return root, triad, seventh, int(rn.inversion())
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ 比较
def compare_piece(piece: Dict[str, Any], events, shift: int = 0,
                  transpose: int = 0) -> List[Dict[str, Any]]:
    """``shift``：人工分析的小节号相对本项目小节号的偏移（弱起记法不同的版本）；
    ``transpose``：人工分析所据版本相对 music21 谱面的整首移调（半音，模 12）。
    """
    from score_learning.grammar.theory import parse_chord_key
    keys = [(m, b) for m, b, _ in events]
    rows = []
    j = -1
    for k, (m, bt) in enumerate(_measure_beats(piece)):
        m += shift
        while j + 1 < len(keys) and keys[j + 1] <= (m, bt + 1e-6):
            j += 1
        if j < 0:
            continue
        h = _human(events[j][2])
        if h is not None:
            h = ((h[0] - transpose) % 12,) + h[1:]
        ch = piece['beats'][k].get('chord')
        row = {'k': k, 'human': h, 'ours': ch}
        if h is None:
            row['cat'] = 'human_unparsed'
        elif not ch:
            row['cat'] = 'ours_unlabelled'
        else:
            r, q, inv = parse_chord_key(ch)
            our_root = (r + piece['tonic_pc']) % 12
            triad, sev = OUR_Q[q]
            row['root'] = our_root == h[0]
            row['triad'] = row['root'] and triad == h[1]
            row['seventh'] = sev == h[2]
            row['full'] = row['triad'] and row['seventh'] and inv == h[3]
            if not row['root']:
                # 根音错：区分「三度关系」（如 vi 与 I6 共两音）与其它
                d = (our_root - h[0]) % 12
                row['cat'] = 'root_third_rel' if d in (3, 4, 8, 9) else 'root_other'
            elif triad != h[1]:
                row['cat'] = 'quality'
            elif sev != h[2]:
                row['cat'] = 'missing_seventh' if h[2] else 'extra_seventh'
            elif inv != h[3]:
                row['cat'] = 'inversion'
            else:
                row['cat'] = 'match'
        rows.append(row)
    return rows


def _summ(rows: List[Dict[str, Any]], n_pieces: int) -> Dict[str, Any]:
    lab = [r for r in rows if 'root' in r]
    n = len(lab) or 1
    cats = Counter(r['cat'] for r in rows)
    return {
        'pieces': n_pieces, 'beats': len(rows), 'compared': len(lab),
        'ours_unlabelled': cats['ours_unlabelled'], 'human_unparsed': cats['human_unparsed'],
        'root': round(sum(r['root'] for r in lab) / n, 4),
        'root_quality': round(sum(r['triad'] for r in lab) / n, 4),
        'root_quality_seventh': round(sum(r['triad'] and r['seventh'] for r in lab) / n, 4),
        'full': round(sum(r['full'] for r in lab) / n, 4),
        'errors': {c: v for c, v in cats.items()
                   if c not in ('match', 'human_unparsed', 'ours_unlabelled')},
    }


def run(save: bool = True, do_fetch: bool = True) -> Dict[str, Any]:
    from score_learning.grammar.analyze import analyze_corpus
    from score_learning.grammar.learn import is_heldout
    if do_fetch:
        fetch()
    idx = _wir_index()
    pieces = analyze_corpus(cache=CACHE, progress=False)
    groups: Dict[str, List[Dict[str, Any]]] = {'no_repeat': [], 'with_repeat': [],
                                               'heldout_no_repeat': []}
    counts = Counter()
    per_piece = []
    for p in pieces:
        path = idx.get(p['id'])
        if not path or not p['meter'].endswith('/4'):
            counts['unmatched'] += 1
            continue
        try:
            ev = _rn_events(path)
        except Exception:  # noqa: BLE001
            counts['parse_failed'] += 1
            continue
        # 版本对齐：小节偏移 −1/0/+1 × 整首移调 0–11，取根音一致率最高者。
        # 这只决定两份谱如何对位，不改任何一方的标签；偏移/移调非零的作品数另报。
        best = None
        for tr in range(12):
            for sh in (0, -1, 1):
                rs = compare_piece(p, ev, sh, tr)
                lab = [r for r in rs if 'root' in r]
                sc = sum(r['root'] for r in lab) / max(len(lab), 1)
                if best is None or sc > best[0] + 1e-9:
                    best = (sc, sh, tr, rs)
        _, shift, transpose, rows = best
        if shift:
            counts['measure_shifted'] += 1
        if transpose:
            counts['transposed'] += 1
        g = 'with_repeat' if p['has_repeat'] else 'no_repeat'
        groups[g] += rows
        counts[g] += 1
        if g == 'no_repeat' and is_heldout(p['id']):
            groups['heldout_no_repeat'] += rows
            counts['heldout_no_repeat'] += 1
        lab = [r for r in rows if 'root' in r]
        per_piece.append({'id': p['id'], 'repeat': p['has_repeat'], 'shift': shift,
                          'transpose': transpose,
                          'root': round(sum(r['root'] for r in lab) / max(len(lab), 1), 3),
                          'n': len(lab)})
    out = {
        'question': 'Agreement of the automatic beat-level chord labels with the '
                    'When-in-Rome human Roman-numeral analyses of the Bach chorales.',
        'reference': 'https://github.com/MarkGotham/When-in-Rome',
        'n_wir_files': len(idx), 'counts': dict(counts),
        'summary': {g: _summ(rs, counts[g]) for g, rs in groups.items()},
        'per_piece': per_piece,
    }
    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e8_chord_agreement.json'), 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='E8 自动和弦标注 vs 人工罗马数字分析')
    ap.add_argument('--no-fetch', action='store_true')
    a = ap.parse_args(argv)
    r = run(do_fetch=not a.no_fetch)
    print('When-in-Rome 文件 %d；匹配 %s' % (r['n_wir_files'], r['counts']))
    for g, s in r['summary'].items():
        print('  %-18s 曲 %3d 拍 %5d 比较 %5d | 根音 %.3f 根音+性质 %.3f +七音 %.3f 全一致 %.3f'
              % (g, s['pieces'], s['beats'], s['compared'], s['root'], s['root_quality'],
                 s['root_quality_seventh'], s['full']))
        print('    错误类型', s['errors'], '我方未标', s['ours_unlabelled'],
              '人工无法解析', s['human_unparsed'])
    print('产物 → runs/experiments/e8_chord_agreement.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
