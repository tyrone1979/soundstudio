# -*- coding: utf-8 -*-
"""Skill Validator stub — compare generated scores' features to skill predictions."""
from __future__ import annotations

from typing import Any, Dict, List


def compare_generated_to_skill(
    generated_features: List[Dict[str, Any]],
    skill_stats: Dict[str, Any],
) -> Dict[str, Any]:
    """Return mismatch report for closed-loop Skill vN → generate → analyze → update."""
    from .aggregate import aggregate_features

    gen_stats = aggregate_features(generated_features)
    mismatches = []
    skill_means = skill_stats.get('means') or {}
    gen_means = gen_stats.get('means') or {}
    for key in skill_means:
        s = skill_means.get(key)
        g = gen_means.get(key)
        if s is None or g is None:
            continue
        if abs(float(s) - float(g)) > 0.15 * max(abs(float(s)), 1e-6):
            mismatches.append({
                'metric': key,
                'skill_expected': s,
                'generated_observed': g,
                'delta': round(float(g) - float(s), 3),
            })

    skill_rules = {
        r['rule']: r for r in (skill_stats.get('rules') or [])
        if isinstance(r, dict) and 'rule' in r
    }
    gen_rules = {
        r['rule']: r for r in (gen_stats.get('rules') or [])
        if isinstance(r, dict) and 'rule' in r
    }
    for name, sr in skill_rules.items():
        if sr.get('universality') != 'core':
            continue
        gr = gen_rules.get(name)
        if not gr or (gr.get('frequency') or 0) < 0.7:
            mismatches.append({
                'metric': f'core_rule:{name}',
                'skill_expected_freq': sr.get('frequency'),
                'generated_observed_freq': (gr or {}).get('frequency'),
            })

    return {
        'generated_count': len(generated_features),
        'skill_total_scores': skill_stats.get('total_scores'),
        'mismatches': mismatches,
        'generated_stats': gen_stats,
        'ok': len(mismatches) == 0,
    }
