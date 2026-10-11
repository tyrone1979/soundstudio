# -*- coding: utf-8 -*-
"""Write learned skill pack files from statistics + techniques."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:
    yaml = None


def _dump_yaml(path: str, data: dict):
    if yaml is None:
        raise RuntimeError('需要 PyYAML：pip install pyyaml')
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _read(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ''
    with open(path, encoding='utf-8') as f:
        return f.read()


def _tech_list(techniques: dict, key: str) -> list:
    t = (techniques.get('techniques') or techniques) if techniques else {}
    if not isinstance(t, dict):
        return []
    return list(t.get(key) or [])


def build_skill_md(genre_name: str, statistics: dict, techniques: dict) -> str:
    means = statistics.get('means') or {}
    total = statistics.get('total_scores') or 0
    lines = [
        f'# {genre_name} Composition Skill (Empirical)',
        '',
        f'> Data-driven skill from {total} analyzed scores. '
        'Learn TECHNIQUES, not note sequences.',
        '',
        '## Identity',
        '',
        f'Empirical composition skill for **{genre_name}**, '
        'derived from corpus measurements + technique mining.',
        '',
        '## Measured means',
        '',
    ]
    for k, v in means.items():
        if v is not None:
            lines.append(f'- `{k}`: {v}')
    lines += ['', '## Core rules (high frequency)', '']
    for r in statistics.get('rules') or []:
        if r.get('universality') != 'core':
            continue
        lines.append(
            f"- **{r['rule']}** — freq {r['frequency']:.0%} "
            f"({r['evidence_count']}/{r['total_scores']}), {r['confidence']}"
        )
    lines += ['', '## Techniques', '']
    for section in (
        'rhythmic', 'melodic', 'harmonic', 'phrase', 'accompaniment',
        'orchestration', 'development', 'cadence',
    ):
        items = _tech_list(techniques, section)
        if not items:
            continue
        lines.append(f'### {section}')
        lines.append('')
        for it in items:
            if not isinstance(it, dict):
                lines.append(f'- {it}')
                continue
            name = it.get('name') or 'technique'
            desc = it.get('description') or ''
            freq = it.get('frequency')
            conf = it.get('confidence') or ''
            univ = it.get('universality') or ''
            meta = ', '.join(x for x in [
                f'freq={freq}' if freq is not None else '',
                conf, univ,
            ] if x)
            lines.append(f'- **{name}**: {desc}' + (f' ({meta})' if meta else ''))
        lines.append('')
    cons = (_tech_list(techniques, 'stylistic_constraints') if False else None)
    tech_root = techniques.get('techniques') or techniques
    cons = (tech_root.get('stylistic_constraints') or {}) if isinstance(tech_root, dict) else {}
    if cons:
        lines.append('## Constraints')
        lines.append('')
        for kind in ('must', 'should', 'avoid'):
            items = cons.get(kind) or []
            if not items:
                continue
            lines.append(f'### {kind}')
            for x in items:
                lines.append(f'- {x}')
            lines.append('')
    lines += [
        '## Avoid',
        '',
        '- Treating rare keys/meters as core rules',
        '- Memorizing melodies from the training corpus',
        '- Promoting optional traits (freq < 0.25) to must-have',
        '',
    ]
    return '\n'.join(lines)


def build_profile_yaml(
    genre_name: str,
    pack_id: str,
    family: str,
    statistics: dict,
    techniques: dict,
    expert_meta: Optional[dict] = None,
) -> dict:
    means = statistics.get('means') or {}
    dist = statistics.get('distributions') or {}
    meters = dist.get('meter') or {}
    primary_meter = max(meters, key=meters.get) if meters else '4/4'
    tempo = means.get('tempo') or 72

    profile = {
        'mode': f'Empirical profile for {genre_name} (corpus-driven).',
        'tempo': {
            'unit': 'quarter_note',
            'default': int(round(tempo)) if tempo else 72,
            'min': max(40, int(round((tempo or 72) * 0.85))),
            'max': min(200, int(round((tempo or 72) * 1.15))),
        },
        'meter': {
            'primary': primary_meter,
            'allowed': list(meters.keys()) or [primary_meter],
        },
        'melody': {
            'stepwise_ratio_target': means.get('stepwise_ratio'),
            'leap_ratio_target': means.get('leap_ratio'),
        },
        'rhythm': {
            'beat1_onset_share': means.get('beat1_onset_share'),
            'bass_beat1_ratio': means.get('bass_beat1_ratio'),
        },
        'harmony': {
            'harmonic_rhythm_chords_per_bar': means.get('harmonic_rhythm_chords_per_bar'),
        },
        'constraints': {
            'must': [],
            'should': [],
            'avoid': [],
        },
    }
    tech_root = techniques.get('techniques') or techniques
    cons = (tech_root.get('stylistic_constraints') or {}) if isinstance(tech_root, dict) else {}
    for kind in ('must', 'should', 'avoid'):
        profile['constraints'][kind] = list(cons.get(kind) or [])

    # merge expert profile keys if present (expert as base, empirical overrides measured fields)
    if expert_meta and isinstance(expert_meta.get('profile'), dict):
        merged = dict(expert_meta['profile'])
        # overlay empirical meter/tempo/rhythm/melody targets
        merged['meter'] = profile['meter']
        et = merged.get('tempo') if isinstance(merged.get('tempo'), dict) else {}
        merged['tempo'] = {**et, **profile['tempo']}
        merged['_empirical'] = {
            'melody': profile['melody'],
            'rhythm': profile['rhythm'],
            'harmony': profile['harmony'],
        }
        profile = merged

    return {
        'id': pack_id,
        'name': genre_name,
        'enabled': True,
        'version': '0.1.0-empirical',
        'source': 'score_learning',
        'dimensions': {
            'family': family,
            'form': pack_id,
        },
        'profile': profile,
    }


def synthesize(
    pack_dir: str,
    pack_id: str,
    genre_name: str,
    statistics: dict,
    techniques: dict,
    expert_meta_path: Optional[str] = None,
    family: str = 'dance',
) -> Dict[str, str]:
    os.makedirs(pack_dir, exist_ok=True)
    os.makedirs(os.path.join(pack_dir, 'features'), exist_ok=True)
    os.makedirs(os.path.join(pack_dir, 'examples'), exist_ok=True)

    expert_meta = None
    expert_text = ''
    if expert_meta_path and os.path.isfile(expert_meta_path):
        expert_text = _read(expert_meta_path)
        if yaml:
            expert_meta = yaml.safe_load(expert_text) or {}

    paths = {}
    skill_md = build_skill_md(genre_name, statistics, techniques)
    paths['skill.md'] = os.path.join(pack_dir, 'skill.md')
    with open(paths['skill.md'], 'w', encoding='utf-8') as f:
        f.write(skill_md)

    profile = build_profile_yaml(
        genre_name, pack_id, family, statistics, techniques, expert_meta=expert_meta,
    )
    paths['profile.yaml'] = os.path.join(pack_dir, 'profile.yaml')
    _dump_yaml(paths['profile.yaml'], profile)

    rules = {
        'genre': genre_name,
        'pack_id': pack_id,
        'rules': statistics.get('rules') or [],
        'techniques': techniques.get('techniques') or techniques,
    }
    paths['rules.yaml'] = os.path.join(pack_dir, 'rules.yaml')
    _dump_yaml(paths['rules.yaml'], rules)

    paths['statistics.json'] = os.path.join(pack_dir, 'statistics.json')
    with open(paths['statistics.json'], 'w', encoding='utf-8') as f:
        json.dump(statistics, f, ensure_ascii=False, indent=2)

    paths['techniques.json'] = os.path.join(pack_dir, 'techniques.json')
    with open(paths['techniques.json'], 'w', encoding='utf-8') as f:
        json.dump(techniques, f, ensure_ascii=False, indent=2)

    # changelog
    cl_path = os.path.join(pack_dir, 'changelog.md')
    stamp = datetime.now().isoformat(timespec='seconds')
    entry = (
        f'\n## {stamp} — empirical synthesize\n\n'
        f'- scores: {statistics.get("total_scores")}\n'
        f'- expert: {expert_meta_path or "(none)"}\n'
        f'- fallback_miner: {bool(techniques.get("_fallback"))}\n'
    )
    prev = _read(cl_path)
    if not prev:
        prev = f'# Changelog — {genre_name}\n'
    with open(cl_path, 'w', encoding='utf-8') as f:
        f.write(prev.rstrip() + '\n' + entry)
    paths['changelog.md'] = cl_path
    return paths
