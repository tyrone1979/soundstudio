# -*- coding: utf-8 -*-
"""Aggregate per-score features into frequency / confidence statistics."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional


def _conf(freq: float) -> str:
    if freq >= 0.7:
        return 'high'
    if freq >= 0.4:
        return 'medium'
    return 'low'


def _univ(freq: float) -> str:
    if freq >= 0.7:
        return 'core'
    if freq >= 0.4:
        return 'common'
    return 'optional'


def _rule(name: str, evidence: int, total: int, detail: Any = None) -> Dict[str, Any]:
    freq = (evidence / total) if total else 0.0
    return {
        'rule': name,
        'evidence_count': evidence,
        'total_scores': total,
        'frequency': round(freq, 3),
        'confidence': _conf(freq),
        'universality': _univ(freq),
        'detail': detail,
    }


def aggregate_features(features: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(features)
    if total == 0:
        return {'total_scores': 0, 'rules': [], 'means': {}, 'distributions': {}}

    meters = Counter()
    keys = Counter()
    tempos: List[float] = []
    stepwise: List[float] = []
    leap: List[float] = []
    bass_b1: List[float] = []
    beat1_share: List[float] = []
    dens: List[float] = []
    harm_r: List[float] = []

    for f in features:
        g = f.get('global') or {}
        meters[str(g.get('meter') or '?')] += 1
        keys[str(g.get('key') or '?')] += 1
        if g.get('tempo'):
            tempos.append(float(g['tempo']))
        mel = f.get('melody') or {}
        if mel.get('stepwise_ratio') is not None:
            stepwise.append(float(mel['stepwise_ratio']))
        if mel.get('leap_ratio') is not None:
            leap.append(float(mel['leap_ratio']))
        rh = f.get('rhythm') or {}
        if rh.get('bass_beat1_ratio') is not None:
            bass_b1.append(float(rh['bass_beat1_ratio']))
        if rh.get('beat1_onset_share') is not None:
            beat1_share.append(float(rh['beat1_onset_share']))
        if rh.get('density_notes_per_bar') is not None:
            dens.append(float(rh['density_notes_per_bar']))
        har = f.get('harmony') or {}
        if har.get('available') and har.get('harmonic_rhythm_chords_per_bar') is not None:
            harm_r.append(float(har['harmonic_rhythm_chords_per_bar']))

    rules: List[Dict[str, Any]] = []
    if meters:
        top_m, top_c = meters.most_common(1)[0]
        rules.append(_rule(f'meter={top_m}', top_c, total, dict(meters)))

    # keys: only promote if frequency >= 0.25
    for k, c in keys.most_common(6):
        r = _rule(f'key={k}', c, total)
        if r['frequency'] < 0.25:
            r['universality'] = 'optional'
            r['confidence'] = 'low'
        rules.append(r)

    def mean(xs: List[float]) -> Optional[float]:
        return round(sum(xs) / len(xs), 3) if xs else None

    means = {
        'tempo': mean(tempos),
        'stepwise_ratio': mean(stepwise),
        'leap_ratio': mean(leap),
        'bass_beat1_ratio': mean(bass_b1),
        'beat1_onset_share': mean(beat1_share),
        'density_notes_per_bar': mean(dens),
        'harmonic_rhythm_chords_per_bar': mean(harm_r),
    }

    # threshold rules from means
    if means['stepwise_ratio'] is not None and means['stepwise_ratio'] >= 0.55:
        high = sum(1 for x in stepwise if x >= 0.55)
        rules.append(_rule('melody_stepwise_tendency', high, total, means['stepwise_ratio']))
    if means['bass_beat1_ratio'] is not None and means['bass_beat1_ratio'] >= 0.6:
        high = sum(1 for x in bass_b1 if x >= 0.6)
        rules.append(_rule('bass_emphasizes_beat_1', high, total, means['bass_beat1_ratio']))
    if means['beat1_onset_share'] is not None and means['beat1_onset_share'] >= 0.35:
        high = sum(1 for x in beat1_share if x >= 0.35)
        rules.append(_rule('beat1_onset_weight', high, total, means['beat1_onset_share']))

    return {
        'total_scores': total,
        'means': means,
        'distributions': {
            'meter': dict(meters),
            'key': dict(keys),
        },
        'rules': rules,
    }
