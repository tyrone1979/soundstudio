# -*- coding: utf-8 -*-
"""LLM Technique Miner — interprets aggregated STATISTICS, never raw scores."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

TECHNIQUE_MINER_SYSTEM = """You are a musicology Technique Miner for a composition skill system.

You receive AGGREGATED quantitative statistics from many scores of one genre.
Your job: extract transferable COMPOSITIONAL TECHNIQUES — not melodies, not note lists.

Rules:
- Prefer patterns with high frequency / high confidence / core universality.
- Rare traits (frequency < 0.25) are optional flavor, never core rules.
- Never invent specific note sequences from famous works.
- Output ONE YAML document only (no markdown fences) with this shape:

genre: <name>
techniques:
  rhythmic:
    - name: snake_case_id
      description: short technique description
      frequency: 0.0-1.0
      confidence: high|medium|low
      universality: core|common|optional
  melodic: []
  harmonic: []
  phrase: []
  accompaniment: []
  orchestration: []
  development: []
  cadence: []
  stylistic_constraints:
    must: []
    should: []
    avoid: []

Use the measured means/distributions as evidence. If expert_meta is provided, reconcile
data-driven rules with expert skill — data wins on conflicts when frequency >= 0.7.
"""


def _extract_yaml(text: str) -> str:
    t = (text or '').strip()
    if t.startswith('```'):
        t = re.sub(r'^```(?:yaml|yml)?\s*', '', t)
        t = re.sub(r'\s*```$', '', t)
    return t.strip()


def mine_techniques(
    statistics: Dict[str, Any],
    genre_name: str,
    expert_meta_text: str = '',
) -> Dict[str, Any]:
    try:
        from llm_compose import chat_completion
    except Exception as e:
        raise RuntimeError(f'需要可导入的 llm_compose.chat_completion：{e}') from e

    payload = {
        'genre': genre_name,
        'statistics': statistics,
        'expert_meta_excerpt': (expert_meta_text or '')[:8000],
    }
    raw = chat_completion([
        {'role': 'system', 'content': TECHNIQUE_MINER_SYSTEM},
        {'role': 'user', 'content':
         'Extract techniques from these corpus statistics:\n\n'
         + json.dumps(payload, ensure_ascii=False, indent=2)},
    ], timeout=180)
    yaml_text = _extract_yaml(raw)
    try:
        import yaml
        data = yaml.safe_load(yaml_text) or {}
    except Exception:
        data = {'genre': genre_name, 'raw_yaml': yaml_text, 'parse_error': True}
    if not isinstance(data, dict):
        data = {'genre': genre_name, 'raw': data}
    data.setdefault('genre', genre_name)
    data['_source_stats_total'] = statistics.get('total_scores')
    return data


def techniques_fallback(statistics: Dict[str, Any], genre_name: str) -> Dict[str, Any]:
    """Rule-based techniques when LLM unavailable."""
    rules = statistics.get('rules') or []
    means = statistics.get('means') or {}
    rhythmic, melodic, accompaniment, constraints_must, constraints_avoid = [], [], [], [], []
    for r in rules:
        name = r.get('rule', '')
        entry = {
            'name': name.replace('=', '_').replace(' ', '_'),
            'description': name,
            'frequency': r.get('frequency'),
            'confidence': r.get('confidence'),
            'universality': r.get('universality'),
        }
        if name.startswith('meter='):
            rhythmic.append(entry)
            if r.get('universality') == 'core':
                constraints_must.append(f"Use meter {name.split('=', 1)[-1]}")
        elif 'bass_emphasizes' in name or 'beat1' in name:
            accompaniment.append(entry)
        elif 'stepwise' in name:
            melodic.append(entry)
        elif name.startswith('key=') and r.get('universality') == 'optional':
            pass
        else:
            rhythmic.append(entry)
    if means.get('stepwise_ratio'):
        melodic.append({
            'name': 'stepwise_lyrical_motion',
            'description': f"Mean stepwise_ratio={means['stepwise_ratio']}",
            'frequency': None,
            'confidence': 'medium',
            'universality': 'common',
        })
    return {
        'genre': genre_name,
        'techniques': {
            'rhythmic': rhythmic,
            'melodic': melodic,
            'harmonic': [],
            'phrase': [],
            'accompaniment': accompaniment,
            'orchestration': [],
            'development': [],
            'cadence': [],
            'stylistic_constraints': {
                'must': constraints_must,
                'should': [],
                'avoid': constraints_avoid,
            },
        },
        '_fallback': True,
        '_source_stats_total': statistics.get('total_scores'),
    }
