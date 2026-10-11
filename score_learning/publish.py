# -*- coding: utf-8 -*-
"""Publish empirical learning into genre pack meta.yaml (style skill)."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKS_ROOT = os.path.join(ROOT, 'skills', 'composition', 'genre', 'packs')

# 可执行风格契约文件名（真正打通「学 → 用」的那份产物）
CONTRACT_FILENAME = 'style_contract.yaml'


def genre_pack_dir(family: str, pack_id: str) -> str:
    return os.path.join(PACKS_ROOT, family, pack_id)


def _load_yaml(path: str) -> dict:
    if yaml is None:
        raise RuntimeError('需要 PyYAML')
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def _dump_yaml(path: str, data: dict):
    if yaml is None:
        raise RuntimeError('需要 PyYAML')
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _bump_version(v: Any) -> str:
    s = str(v or '1.0').strip().strip("'\"")
    parts = s.split('.')
    try:
        nums = [int(x) for x in parts]
        while len(nums) < 2:
            nums.append(0)
        nums[-1] += 1
        return '.'.join(str(x) for x in nums)
    except Exception:
        return s + '+empirical'


def _stats_fingerprint(statistics: dict) -> str:
    """语料统计内容的稳定指纹 —— 判断这次 learn 到底学到新东西没有。

    没有它的话，对着同一份语料反复 learn 也会一路顶版本号、往 changelog
    里灌重复条目（实测众赞歌被连续顶到 2.10，而数值一个字没变）。
    """
    import hashlib
    blob = json.dumps({
        'total_scores': statistics.get('total_scores'),
        'means': statistics.get('means'),
        'distributions': statistics.get('distributions'),
        'rules': statistics.get('rules'),
    }, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode('utf-8')).hexdigest()[:12]


def merge_empirical_into_profile(profile: dict, statistics: dict) -> dict:
    """Apply high-confidence corpus measurements onto a genre profile."""
    profile = dict(profile or {})
    means = statistics.get('means') or {}
    dist = statistics.get('distributions') or {}
    rules = statistics.get('rules') or []

    # meter: core meters only
    meters = dist.get('meter') or {}
    total = max(int(statistics.get('total_scores') or 0), 1)
    core_meters = [m for m, c in meters.items() if c / total >= 0.7]
    if core_meters:
        primary = max(core_meters, key=lambda m: meters[m])
        meter = dict(profile.get('meter') or {}) if isinstance(profile.get('meter'), dict) else {}
        meter['primary'] = primary
        # keep prior allowed, but ensure core meters listed first
        allowed = list(meter.get('allowed') or [])
        for m in core_meters:
            if m not in allowed:
                allowed.insert(0, m)
        # if one meter is 100%, tighten allowed to that meter (+ existing rare opts removed if 100%)
        if meters.get(primary, 0) / total >= 0.95:
            meter['allowed'] = [primary]
        else:
            meter['allowed'] = allowed
        meter['empirical_note'] = (
            f"From {total} scores: " + ', '.join(f'{m}={meters[m]/total:.0%}' for m in meters)
        )
        profile['meter'] = meter

    # melody targets
    mel = dict(profile.get('melody') or {}) if isinstance(profile.get('melody'), dict) else {}
    if means.get('stepwise_ratio') is not None:
        mel['stepwise_ratio_target'] = means['stepwise_ratio']
        mel.setdefault('contour_preferred', ['stepwise', 'balanced'])
        if 'stepwise' not in (mel.get('contour_preferred') or []):
            mel['contour_preferred'] = ['stepwise'] + list(mel.get('contour_preferred') or [])
        old_rules = str(mel.get('rules') or '')
        tip = (
            f"语料级进比约 {means['stepwise_ratio']:.0%}；"
            f"大跳比约 {means.get('leap_ratio') or 0:.0%}。以级进为主。"
        )
        # 这句话每次 learn 都会重算。历史实现直接前置，于是每跑一次就多一份，
        # 众赞歌的 rules 里同一个句子曾累加到 6 遍。先剥掉同源句，再放最新值。
        cleaned = re.sub(
            r'语料级进比约\s*\d+%；大跳比约\s*\d+%。以级进为主。\s*', '', old_rules
        ).strip()
        mel['rules'] = (tip + ' ' + cleaned).strip() if cleaned else tip
    if means.get('leap_ratio') is not None:
        mel['leap_ratio_target'] = means['leap_ratio']
    profile['melody'] = mel

    # harmony rhythm
    har = dict(profile.get('harmony') or {}) if isinstance(profile.get('harmony'), dict) else {}
    if means.get('harmonic_rhythm_chords_per_bar') is not None:
        har['harmonic_rhythm_empirical'] = (
            f"语料约 {means['harmonic_rhythm_chords_per_bar']:.2f} 和弦/小节"
        )
    profile['harmony'] = har

    # tonal center: promote common keys (freq>=0.4) into preferred/secondary
    keys = dist.get('key') or {}
    common_keys = [k for k, c in keys.items() if c / total >= 0.4]
    tc = dict(profile.get('tonal_center') or {}) if isinstance(profile.get('tonal_center'), dict) else {}
    preferred = list(tc.get('preferred') or [])
    secondary = list(tc.get('secondary') or [])
    for k in common_keys:
        short = k.replace(' major', '').replace(' minor', ' minor')
        # keep readable labels
        label = k
        if label not in preferred and label not in secondary:
            secondary.insert(0, label)
    tc['secondary'] = secondary
    if preferred:
        tc['preferred'] = preferred
    tc['empirical_key_dist'] = {k: round(c / total, 3) for k, c in keys.items()}
    profile['tonal_center'] = tc

    # constraints from core rules
    cons = dict(profile.get('constraints') or {}) if isinstance(profile.get('constraints'), dict) else {}
    must = list(cons.get('must') or [])
    for r in rules:
        if r.get('universality') != 'core':
            continue
        rule = r.get('rule') or ''
        if rule.startswith('meter='):
            msg = f"拍号以 {rule.split('=', 1)[1]} 为主（语料 {r['frequency']:.0%}）。"
            if msg not in must:
                must.append(msg)
        if rule == 'melody_stepwise_tendency':
            msg = f"旋律以级进为主（语料 {r['frequency']:.0%}，均值 stepwise≈{means.get('stepwise_ratio')}）。"
            if msg not in must:
                must.append(msg)
    cons['must'] = must
    profile['constraints'] = cons

    profile['empirical'] = {
        'source': 'score_learning',
        'total_scores': statistics.get('total_scores'),
        'means': means,
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'core_rules': [
            r for r in rules if r.get('universality') == 'core'
        ],
    }
    return profile


def write_style_contract(
    pack_dir: str,
    statistics: dict,
    features: Optional[List[Dict[str, Any]]] = None,
    expert_profile: Optional[dict] = None,
    pack_id: str = '',
    family: str = '',
) -> Optional[str]:
    """产出 style_contract.yaml —— 学习成果的**可执行**形态。

    这一步是「学了能用」的关键。此前 learning 只把统计量写进
    empirical_statistics.json 侧车文件，再往 meta.yaml profile 塞几个数值；
    而 profile 最终只被 serialize_profile() 序列化成 prompt 文本交给 LLM
    自行领会。于是「学」与「用」之间没有任何机器可读通道。

    style_contract.yaml 走的正是那条断掉的链路：compose_kernel 的
    new_score(style=...) 会确定性读取它并真实约束和声品质等可测量维度。
    """
    try:
        from style_contract import (
            build_contract_from_features, CONTRACT_FILENAME,
        )
    except Exception as e:
        print(f'  [skip] 无法产出风格契约：{e}')
        return None

    feats = [f for f in (features or [])
             if isinstance(f, dict) and not (f.get('metadata') or {}).get('error')]
    if not feats:
        # 没有逐首 features 就无法产出「有证据」的契约 —— 宁缺毋滥，
        # 不写一个假装实证的空契约
        print('  [skip] 无有效 features，不产出风格契约')
        return None

    contract = build_contract_from_features(
        feats, pack_id=pack_id, family=family,
        expert_profile=expert_profile,
    )
    dest = os.path.join(pack_dir, CONTRACT_FILENAME)
    contract.save(dest)
    active = contract.active_sections()
    if active:
        print(f'  style_contract: 生效分区 {active}')
    else:
        print(f'  style_contract: 证据不足，所有分区均未生效（n={contract.evidence_n}）')
    return dest


def publish_to_genre_pack(
    family: str,
    pack_id: str,
    statistics: dict,
    techniques: Optional[dict] = None,
    skill_md_path: Optional[str] = None,
    learned_dir: Optional[str] = None,  # deprecated alias
    features: Optional[List[Dict[str, Any]]] = None,   # 逐首 features（契约所需）
) -> Dict[str, str]:
    """
    Merge empirical stats into skills/composition/genre/packs/<family>/<pack_id>/meta.yaml
    Sidecars (features/, empirical_*.json, EMPIRICAL_SKILL.md) live in the same pack.
    """
    pack_dir = genre_pack_dir(family, pack_id)
    meta_path = os.path.join(pack_dir, 'meta.yaml')
    if not os.path.isfile(meta_path):
        raise FileNotFoundError(f'风格包不存在：{meta_path}')

    meta = _load_yaml(meta_path)
    prev_prof = meta.get('profile') if isinstance(meta.get('profile'), dict) else {}
    prev_emp = prev_prof.get('empirical') if isinstance(prev_prof.get('empirical'), dict) else {}
    fp = _stats_fingerprint(statistics)
    changed = (prev_emp.get('fingerprint') != fp)

    meta['profile'] = merge_empirical_into_profile(meta.get('profile') or {}, statistics)
    emp = meta['profile'].get('empirical')
    if isinstance(emp, dict):
        emp['fingerprint'] = fp
        # 语料没变就别刷时间戳 —— 否则 meta.yaml 每次 learn 都会产生无意义 diff
        if not changed and prev_emp.get('updated_at'):
            emp['updated_at'] = prev_emp['updated_at']
    if changed:
        meta['version'] = _bump_version(meta.get('version'))
    instr = str(meta.get('llm_instruction') or '')
    if '语料实证' not in instr:
        meta['llm_instruction'] = (
            (instr + ' ').strip()
            + ' 落实语料实证：级进为主、核心拍号、功能和声终止；勿背诵训练曲旋律。'
        ).strip()

    _dump_yaml(meta_path, meta)
    written = {'meta.yaml': meta_path}

    stats_path = os.path.join(pack_dir, 'empirical_statistics.json')
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(statistics, f, ensure_ascii=False, indent=2)
    written['empirical_statistics.json'] = stats_path

    # 可执行契约：learning 的成果必须落到一条机器可读的通道上，
    # 否则学到的数值只会变成 prompt 里的文字（见 write_style_contract 文档）
    try:
        prof = meta.get('profile') if isinstance(meta.get('profile'), dict) else None
        cp = write_style_contract(
            pack_dir, statistics, features=features,
            expert_profile=prof, pack_id=pack_id, family=family,
        )
        if cp:
            written[CONTRACT_FILENAME] = cp
    except Exception as e:
        print(f'  [warn] 风格契约产出失败：{e}')

    if techniques:
        tech_path = os.path.join(pack_dir, 'empirical_techniques.json')
        with open(tech_path, 'w', encoding='utf-8') as f:
            json.dump(techniques, f, ensure_ascii=False, indent=2)
        written['empirical_techniques.json'] = tech_path

    # EMPIRICAL_SKILL.md
    dest = os.path.join(pack_dir, 'EMPIRICAL_SKILL.md')
    body = ''
    if skill_md_path and os.path.isfile(skill_md_path):
        with open(skill_md_path, encoding='utf-8') as f:
            body = f.read()
    elif learned_dir:
        for name in ('EMPIRICAL_SKILL.md', 'skill.md'):
            src = os.path.join(learned_dir, name)
            if os.path.isfile(src):
                with open(src, encoding='utf-8') as f:
                    body = f.read()
                break
    if body:
        with open(dest, 'w', encoding='utf-8') as f:
            f.write(body)
        written['EMPIRICAL_SKILL.md'] = dest
    elif os.path.isfile(dest):
        written['EMPIRICAL_SKILL.md'] = dest

    cl = os.path.join(pack_dir, 'changelog.md')
    stamp = datetime.now().isoformat(timespec='seconds')
    prev = ''
    if os.path.isfile(cl):
        with open(cl, encoding='utf-8') as f:
            prev = f.read()
    if not prev:
        prev = f'# Changelog — {pack_id}\n'
    if not changed:
        # 语料统计与上次一致：不顶版本、不追加条目（changelog 只记真实变化）
        print(f'  [skip] {family}/{pack_id}: 语料统计未变化（{fp}），版本与 changelog 不动')
    else:
        entry = (
            f'\n## {stamp} — empirical update\n\n'
            f'- scores: {statistics.get("total_scores")}\n'
            f'- version → {meta.get("version")}\n'
            f'- core rules: '
            + ', '.join(
                r.get('rule', '') for r in (statistics.get('rules') or [])
                if r.get('universality') == 'core'
            )
            + '\n'
        )
        with open(cl, 'w', encoding='utf-8') as f:
            f.write(prev.rstrip() + '\n' + entry)
        written['changelog.md'] = cl

    genre_md = os.path.join(pack_dir, 'GENRE.md')
    if os.path.isfile(genre_md):
        with open(genre_md, encoding='utf-8') as f:
            gbody = f.read()
        if 'empirical_statistics.json' not in gbody:
            gbody = gbody.rstrip() + (
                '\n\n## 语料实证\n\n'
                '由 `score_learning` 直接更新本风格包：`features/` + '
                '`empirical_statistics.json` → 合并入 `meta.yaml`。\n'
            )
            with open(genre_md, 'w', encoding='utf-8') as f:
                f.write(gbody)
            written['GENRE.md'] = genre_md

    try:
        import composition_skills as skill_lib
        skill_lib.invalidate_cache()
    except Exception:
        pass

    return written
