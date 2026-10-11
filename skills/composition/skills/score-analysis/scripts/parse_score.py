# -*- coding: utf-8 -*-
"""Extract score facts for the score-analysis /advice skill.

Usage:
  python parse_score.py path/to/score.xml
  from parse_score import analyze_musicxml, analyze_score_spec, analyze_m21
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))


def _ql_bucket(ql: float) -> str:
    if ql <= 0:
        return '0'
    if abs(ql - 0.25) < 0.02:
        return '16th'
    if abs(ql - 1 / 3) < 0.03 or abs(ql - 2 / 3) < 0.03:
        return 'triplet'
    if abs(ql - 0.5) < 0.02:
        return '8th'
    if abs(ql - 0.75) < 0.02:
        return 'dotted_8th'
    if abs(ql - 1.0) < 0.02:
        return 'quarter'
    if abs(ql - 1.5) < 0.02:
        return 'dotted_quarter'
    if abs(ql - 2.0) < 0.02:
        return 'half'
    if abs(ql - 3.0) < 0.02:
        return 'dotted_half'
    if abs(ql - 4.0) < 0.02:
        return 'whole'
    return f'ql={round(ql, 3)}'


def _is_long(ql: float) -> bool:
    return ql >= 2.0 - 1e-6


def _density_signature(qls: List[float]) -> Tuple[str, ...]:
    return tuple(sorted(_ql_bucket(q) for q in qls))


def _pitch_contour(names: List[str], max_len: int = 4) -> Optional[Tuple[str, ...]]:
    if len(names) < 3:
        return None
    return tuple(names[:max_len])


def analyze_m21(score) -> Dict[str, Any]:
    """Analyze a music21 Score into structured facts + heuristic findings."""
    from music21 import chord as m21chord
    from music21 import key as m21key
    from music21 import note as m21note
    from music21 import tempo as m21tempo

    title = 'Untitled'
    md = getattr(score, 'metadata', None)
    if md:
        for cand in (
            getattr(md, 'bestTitle', None),
            getattr(md, 'title', None),
            getattr(md, 'movementName', None),
        ):
            if cand and str(cand).strip():
                title = str(cand).strip()
                break

    bars = 0
    for p in score.parts:
        bars = max(bars, len(p.getElementsByClass('Measure')))

    ks_by_part: Dict[str, List[int]] = {}
    concert_ks: List[Tuple[int, int]] = []  # (bar, sharps) from non-transposing-looking parts
    tempo_n = None
    mm = score.recurse().getElementsByClass(m21tempo.MetronomeMark).first()
    if mm is not None and getattr(mm, 'number', None):
        tempo_n = int(round(float(mm.number)))

    parts_out: List[Dict[str, Any]] = []
    motif_hits: Counter = Counter()

    for part in score.parts:
        name = part.partName or part.id or 'Part'
        note_qls: List[float] = []
        rest_count = 0
        note_count = 0
        long_count = 0
        sounding_bars = 0
        long_only_bars = 0
        bar_sigs: Dict[int, Tuple[str, ...]] = {}
        bar_pitch_heads: Dict[int, List[str]] = {}
        part_ks: List[int] = []

        for m in part.getElementsByClass('Measure'):
            n = m.number
            if n is None:
                continue
            for ks in m.getElementsByClass(m21key.KeySignature):
                part_ks.append(int(ks.sharps))
                if name.lower() not in ('horn', 'trumpet', 'clarinet', 'english horn'):
                    concert_ks.append((int(n), int(ks.sharps)))
            qls: List[float] = []
            pitches: List[str] = []
            has_sound = False
            all_long = True
            for el in m.notesAndRests:
                if el.isRest:
                    rest_count += 1
                    continue
                has_sound = True
                ql = float(el.quarterLength)
                note_count += 1
                note_qls.append(ql)
                qls.append(ql)
                if _is_long(ql):
                    long_count += 1
                else:
                    all_long = False
                if isinstance(el, m21note.Note):
                    pitches.append(el.nameWithOctave)
                elif isinstance(el, m21chord.Chord):
                    pitches.append('+'.join(p.nameWithOctave for p in el.pitches[:4]))
            if has_sound:
                sounding_bars += 1
                if all_long and qls:
                    long_only_bars += 1
            if qls:
                bar_sigs[int(n)] = _density_signature(qls)
            if pitches:
                bar_pitch_heads[int(n)] = pitches
                contour = _pitch_contour([p.split('+')[0] for p in pitches])
                if contour:
                    motif_hits[contour] += 1

        long_ratio = (long_count / note_count) if note_count else 0.0
        # monotone density streaks
        streaks = []
        if bar_sigs:
            ordered = sorted(bar_sigs.items())
            run_start, run_sig, run_len = ordered[0][0], ordered[0][1], 1
            for bar_n, sig in ordered[1:]:
                if sig == run_sig and bar_n == run_start + run_len:
                    run_len += 1
                else:
                    if run_len >= 4:
                        streaks.append({'start': run_start, 'end': run_start + run_len - 1,
                                        'length': run_len, 'signature': list(run_sig)})
                    run_start, run_sig, run_len = bar_n, sig, 1
            if run_len >= 4:
                streaks.append({'start': run_start, 'end': run_start + run_len - 1,
                                'length': run_len, 'signature': list(run_sig)})

        ql_hist = Counter(_ql_bucket(q) for q in note_qls)
        ks_by_part[name] = part_ks
        parts_out.append({
            'name': name,
            'note_events': note_count,
            'rest_events': rest_count,
            'long_note_ratio': round(long_ratio, 3),
            'sounding_bars': sounding_bars,
            'long_only_bars': long_only_bars,
            'ql_histogram': dict(ql_hist),
            'density_streaks_ge4': streaks,
            'sample_pitch_heads': {
                str(k): v[:6] for k, v in list(sorted(bar_pitch_heads.items()))[:8]
            },
        })

    concert_sharps = sorted({s for _, s in concert_ks}) if concert_ks else sorted({
        s for vals in ks_by_part.values() for s in vals
    })
    key_changes = len(concert_sharps) > 1

    repeated_motifs = [
        {'contour': list(c), 'count': n}
        for c, n in motif_hits.most_common(8)
        if n > 2
    ]

    findings: List[Dict[str, str]] = []
    for p in parts_out:
        if p['note_events'] >= 8 and p['long_note_ratio'] > 0.8:
            findings.append({
                'priority': 'high',
                'rule': 'H1',
                'issue': f"{p['name']} 长音占比 {p['long_note_ratio']:.0%}，疑似声部浪费",
                'location': f"{p['name']} 全曲",
            })
        if p['sounding_bars'] >= 8 and p['long_only_bars'] >= p['sounding_bars'] * 0.9:
            findings.append({
                'priority': 'high',
                'rule': 'H1',
                'issue': f"{p['name']} 几乎全程仅长音，角色单一",
                'location': f"{p['name']} 第1–{bars}小节",
            })
        for st in p['density_streaks_ge4']:
            findings.append({
                'priority': 'medium',
                'rule': 'M1',
                'issue': f"{p['name']} 第{st['start']}–{st['end']}小节节奏密度完全一致",
                'location': f"{p['name']} 第{st['start']}–{st['end']}小节",
            })

    if bars >= 8 and not key_changes:
        mid = max(1, bars // 2 - 2)
        findings.append({
            'priority': 'high',
            'rule': 'H2',
            'issue': '全曲调号未见变化，可能缺乏调性对比',
            'location': f'建议第{mid}–{mid + 6}小节',
        })

    for m in repeated_motifs[:3]:
        findings.append({
            'priority': 'medium',
            'rule': 'M2',
            'issue': f"动机 {'-'.join(m['contour'])} 原样出现 {m['count']} 次",
            'location': '旋律声部（见 pitch heads）',
        })

    return {
        'title': title,
        'bars': bars,
        'tempo': tempo_n,
        'concert_key_sharps_seen': concert_sharps,
        'key_signature_changed': key_changes,
        'parts': parts_out,
        'repeated_motifs': repeated_motifs,
        'heuristic_findings': findings,
    }


def analyze_musicxml(path: str) -> Dict[str, Any]:
    from music21 import converter
    score = converter.parse(path)
    facts = analyze_m21(score)
    facts['source'] = os.path.abspath(path)
    return facts


def analyze_score_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Lightweight analysis from SCORE JSON (no music21 round-trip required)."""
    bars = int(spec.get('bars') or 8)
    parts_out: List[Dict[str, Any]] = []
    motif_hits: Counter = Counter()
    findings: List[Dict[str, str]] = []

    for part in spec.get('parts') or []:
        name = part.get('name') or 'Part'
        events = part.get('events') or []
        note_qls: List[float] = []
        note_count = 0
        long_count = 0
        rest_count = 0
        # expand events into bar buckets by cumulative ql
        ts = str(spec.get('time_signature') or '4/4')
        try:
            num, den = ts.split('/')
            ql_bar = int(num) * (4.0 / int(den))
        except Exception:
            ql_bar = 4.0
        bar_qls: Dict[int, List[float]] = defaultdict(list)
        bar_pitches: Dict[int, List[str]] = defaultdict(list)
        cursor = 0.0
        for ev in events:
            if not isinstance(ev, (list, tuple)) or len(ev) < 2:
                continue
            pitch, dur = ev[0], ev[1]
            # duration token → ql
            dur_map = {
                'whole': 4.0, 'half': 2.0, 'quarter': 1.0, 'eighth': 0.5,
                '16th': 0.25, 'sixteenth': 0.25,
                'dotted_half': 3.0, 'dotted_quarter': 1.5, 'dotted_eighth': 0.75,
                'triplet_eighth': 1 / 3, 'triplet_quarter': 2 / 3,
            }
            if isinstance(dur, (int, float)):
                ql = float(dur)
            else:
                ql = float(dur_map.get(str(dur).lower(), 1.0))
            bar_n = int(cursor // ql_bar) + 1
            if pitch in (None, 'rest', 'r', 'R'):
                rest_count += 1
            else:
                note_count += 1
                note_qls.append(ql)
                bar_qls[bar_n].append(ql)
                pname = pitch if isinstance(pitch, str) else str(pitch)
                if isinstance(pitch, list):
                    pname = '+'.join(str(x) for x in pitch)
                bar_pitches[bar_n].append(pname)
                if _is_long(ql):
                    long_count += 1
            cursor += ql

        long_ratio = (long_count / note_count) if note_count else 0.0
        ql_hist = Counter(_ql_bucket(q) for q in note_qls)
        streaks = []
        if bar_qls:
            ordered = sorted((b, _density_signature(qs)) for b, qs in bar_qls.items())
            run_start, run_sig, run_len = ordered[0][0], ordered[0][1], 1
            for bar_n, sig in ordered[1:]:
                if sig == run_sig and bar_n == run_start + run_len:
                    run_len += 1
                else:
                    if run_len >= 4:
                        streaks.append({'start': run_start, 'end': run_start + run_len - 1,
                                        'length': run_len, 'signature': list(run_sig)})
                    run_start, run_sig, run_len = bar_n, sig, 1
            if run_len >= 4:
                streaks.append({'start': run_start, 'end': run_start + run_len - 1,
                                'length': run_len, 'signature': list(run_sig)})

        for b, pitches in bar_pitches.items():
            contour = _pitch_contour([p.split('+')[0] for p in pitches])
            if contour:
                motif_hits[contour] += 1

        parts_out.append({
            'name': name,
            'note_events': note_count,
            'rest_events': rest_count,
            'long_note_ratio': round(long_ratio, 3),
            'ql_histogram': dict(ql_hist),
            'density_streaks_ge4': streaks,
        })
        if note_count >= 8 and long_ratio > 0.8:
            findings.append({
                'priority': 'high', 'rule': 'H1',
                'issue': f'{name} 长音占比 {long_ratio:.0%}，疑似声部浪费',
                'location': f'{name} 全曲',
            })

    repeated_motifs = [
        {'contour': list(c), 'count': n}
        for c, n in motif_hits.most_common(8) if n > 2
    ]
    for m in repeated_motifs[:3]:
        findings.append({
            'priority': 'medium', 'rule': 'M2',
            'issue': f"动机 {'-'.join(m['contour'])} 原样出现 {m['count']} 次",
            'location': '旋律声部',
        })

    return {
        'title': spec.get('title') or 'Untitled',
        'bars': bars,
        'tempo': spec.get('tempo'),
        'concert_key_sharps_seen': [spec.get('key_sharps')],
        'key_signature_changed': False,
        'parts': parts_out,
        'repeated_motifs': repeated_motifs,
        'heuristic_findings': findings,
        'source': 'score_spec',
    }


def facts_to_prompt_block(facts: Dict[str, Any]) -> str:
    return json.dumps(facts, ensure_ascii=False, indent=2)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print('Usage: parse_score.py <musicxml>', file=sys.stderr)
        return 2
    path = argv[0]
    facts = analyze_musicxml(path)
    print(facts_to_prompt_block(facts))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
