# -*- coding: utf-8 -*-
"""作曲 Skill 库：任务 Skill + 风格 Genre 包（带维度 taxonomy）。"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.join(ROOT, 'skills', 'composition')
INDEX_PATH = os.path.join(SKILL_ROOT, 'index.yaml')
BASE_DIR = os.path.join(SKILL_ROOT, 'base')
SKILLS_DIR = os.path.join(SKILL_ROOT, 'skills')
GENRE_ROOT = os.path.join(SKILL_ROOT, 'genre')
GENRE_TAXONOMY_PATH = os.path.join(GENRE_ROOT, 'taxonomy.yaml')
GENRE_PACKS_DIR = os.path.join(GENRE_ROOT, 'packs')

STAGE_FILES = {
    'translator': 'translator.md',
    'planner': 'planner.md',
    'composer': 'composer.md',
    'selection_measure': 'selection_measure.md',
    'selection_symbol': 'selection_symbol.md',
    'repair': 'repair.md',
    'advice': 'advice.md',
}

# 会真正被注入提示词的阶段（= 每个阶段查 base/<stage>.md + <skill>/<stage>.md）。
# 'translator' 是「LLM 翻译器 + 确定性内核」新创作路径的主入口，历史版本漏登记，
# 导致所有任务 Skill 在这条路径上注入 0 字符 —— 见 tests/test_skill_library.py。
INJECT_STAGES = ('translator', 'planner', 'composer', 'selection_measure',
                 'selection_symbol', 'repair', 'advice')

# genre pack ids are prefixed so they never collide with task skills
GENRE_PREFIX = 'genre:'

_CACHE: Dict[str, Any] = {
    'index_mtime': 0.0,
    'index': None,
    'taxonomy_mtime': 0.0,
    'taxonomy': None,
    'genres_sig': None,
    'genres': None,
    'prompts': {},
}


def _read_text(path: str) -> str:
    if not os.path.isfile(path):
        return ''
    with open(path, encoding='utf-8') as f:
        return f.read().strip()


def _load_yaml(path: str) -> dict:
    raw = _read_text(path)
    if not raw:
        return {}
    if yaml is None:
        raise RuntimeError('需要 PyYAML：pip install pyyaml')
    return yaml.safe_load(raw) or {}


def _dump_yaml(path: str, data: dict):
    if yaml is None:
        raise RuntimeError('需要 PyYAML：pip install pyyaml')
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def invalidate_cache():
    _CACHE['index'] = None
    _CACHE['taxonomy'] = None
    _CACHE['genres'] = None
    _CACHE['genres_sig'] = None
    _CACHE['prompts'] = {}
    try:
        import rule_library as rule_lib
        rule_lib.invalidate_cache()
    except Exception:
        pass


# ---------- task skills (index.yaml) ----------

def _load_index(force: bool = False) -> dict:
    mtime = os.path.getmtime(INDEX_PATH) if os.path.exists(INDEX_PATH) else 0.0
    if not force and _CACHE['index'] is not None and _CACHE['index_mtime'] == mtime:
        return _CACHE['index']
    if not os.path.exists(INDEX_PATH):
        data = {'version': '0', 'skills': []}
    else:
        data = _load_yaml(INDEX_PATH)
    # drop legacy flat genre-* entries if any remain
    skills = [
        s for s in (data.get('skills') or [])
        if not str(s.get('id') or '').startswith('genre-')
        and s.get('pipeline') != 'overlay'
    ]
    data['skills'] = skills
    _CACHE['index'] = data
    _CACHE['index_mtime'] = mtime
    _CACHE['prompts'] = {}
    return data


def list_skills(force: bool = False) -> List[dict]:
    return list(_load_index(force=force).get('skills') or [])


def get_skill(skill_id: str, force: bool = False) -> Optional[dict]:
    if is_genre_id(skill_id):
        return get_genre(skill_id, force=force)
    for s in list_skills(force=force):
        if s.get('id') == skill_id:
            return s
    return None


def set_skill_enabled(skill_id: str, enabled: bool) -> bool:
    if is_genre_id(skill_id):
        return set_genre_enabled(skill_id, enabled)
    idx = _load_index(force=True)
    found = False
    for s in idx.get('skills') or []:
        if s.get('id') == skill_id:
            s['enabled'] = bool(enabled)
            found = True
            break
    if not found:
        return False
    _dump_yaml(INDEX_PATH, idx)
    invalidate_cache()
    return True


def skill_dir(skill_id: str) -> str:
    if is_genre_id(skill_id):
        return genre_pack_dir(skill_id)
    return os.path.join(SKILLS_DIR, skill_id)


def skill_file_paths(skill_id: str) -> Dict[str, str]:
    d = skill_dir(skill_id)
    if is_genre_id(skill_id):
        return {
            'GENRE.md': os.path.join(d, 'GENRE.md'),
            'planner.md': os.path.join(d, 'planner.md'),
            'composer.md': os.path.join(d, 'composer.md'),
            'meta.yaml': os.path.join(d, 'meta.yaml'),
        }
    return {
        'SKILL.md': os.path.join(d, 'SKILL.md'),
        'planner.md': os.path.join(d, 'planner.md'),
        'composer.md': os.path.join(d, 'composer.md'),
    }


def save_skill_text(skill_id: str, filename: str, content: str):
    d = skill_dir(skill_id)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, filename)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    invalidate_cache()


# ---------- genre taxonomy + packs ----------

def is_genre_id(skill_id: str) -> bool:
    return str(skill_id or '').startswith(GENRE_PREFIX)


def genre_ref(family: str, pack_id: str) -> str:
    return f'{GENRE_PREFIX}{family}/{pack_id}'


def parse_genre_ref(skill_id: str) -> Tuple[str, str]:
    """genre:cinematic/pirate-allegro → (cinematic, pirate-allegro)"""
    raw = skill_id[len(GENRE_PREFIX):] if is_genre_id(skill_id) else skill_id
    family, _, pack = raw.partition('/')
    return family, pack


def genre_pack_dir(skill_id: str) -> str:
    family, pack = parse_genre_ref(skill_id)
    return os.path.join(GENRE_PACKS_DIR, family, pack)


def load_taxonomy(force: bool = False) -> dict:
    mtime = os.path.getmtime(GENRE_TAXONOMY_PATH) if os.path.exists(GENRE_TAXONOMY_PATH) else 0.0
    if not force and _CACHE['taxonomy'] is not None and _CACHE['taxonomy_mtime'] == mtime:
        return _CACHE['taxonomy']
    data = _load_yaml(GENRE_TAXONOMY_PATH) if os.path.exists(GENRE_TAXONOMY_PATH) else {}
    _CACHE['taxonomy'] = data
    _CACHE['taxonomy_mtime'] = mtime
    return data


def _packs_signature() -> str:
    if not os.path.isdir(GENRE_PACKS_DIR):
        return ''
    parts = []
    for root, _dirs, files in os.walk(GENRE_PACKS_DIR):
        if 'meta.yaml' in files:
            p = os.path.join(root, 'meta.yaml')
            try:
                parts.append(f'{p}:{os.path.getmtime(p)}')
            except OSError:
                parts.append(p)
    return '|'.join(sorted(parts))


def _validate_dimensions(dims: dict, taxonomy: dict) -> List[str]:
    errors = []
    dim_defs = taxonomy.get('dimensions') or {}
    required = set(taxonomy.get('required_dimensions') or [])
    if not required:
        # backward compat: all non-optional dims required
        required = {
            k for k, d in dim_defs.items()
            if not (isinstance(d, dict) and d.get('optional'))
        }
    for key in required:
        if key not in dim_defs:
            continue
        val = (dims or {}).get(key)
        if not val:
            errors.append(f'缺少维度 {key}')
            continue
        allowed = set((dim_defs[key].get('values') or {}).keys())
        if allowed and val not in allowed:
            errors.append(f'{key}={val} 不在 taxonomy 允许值中')
    # optional / present dims: validate value if set
    for key, val in (dims or {}).items():
        if key in required or not val:
            continue
        defn = dim_defs.get(key)
        if not defn:
            continue
        allowed = set((defn.get('values') or {}).keys())
        if allowed and val not in allowed:
            errors.append(f'{key}={val} 不在 taxonomy 允许值中')
    return errors


# Stable display / full-serialize order (required first, then staged extras)
PROFILE_KEYS = (
    'mode', 'tonal_center', 'tempo', 'meter', 'rhythm',
    'phrase', 'cadence', 'harmony', 'melody', 'motif',
    'texture', 'register', 'dynamics', 'articulation',
    'color', 'instrumentation', 'form', 'constraints',
    'priority', 'generation', 'foundation_score', 'llm_instruction',
)

PROFILE_LABELS_ZH = {
    'mode': '调式',
    'tempo': '速度',
    'meter': '拍号',
    'phrase': '句法',
    'harmony': '和声',
    'melody': '旋律',
    'texture': '织体',
    'color': '色彩',
    'rhythm': '节奏',
    'cadence': '终止',
    'motif': '动机',
    'register': '音区',
    'dynamics': '力度',
    'articulation': '奏法',
    'instrumentation': '编制',
    'form': '曲式',
    'constraints': '约束',
    'generation': '生成',
    'foundation_score': '底座设计',
    'llm_instruction': '生成指令',
    'tonal_center': '调中心',
    'priority': '优先级',
}


def _has_content(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple, set)):
        return len(v) > 0
    if isinstance(v, dict):
        return any(_has_content(x) for x in v.values())
    return True


def _normalize_profile(raw) -> dict:
    """Keep structured profile as dict (strings + nested)."""
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def _validate_profile(profile: dict, taxonomy: dict) -> List[str]:
    errors = []
    required = list(taxonomy.get('profile_required') or [])
    if not required:
        schema = taxonomy.get('profile_schema') or {}
        required = list(schema.keys()) if schema else list(PROFILE_KEYS)
    for k in required:
        if not _has_content((profile or {}).get(k)):
            label = PROFILE_LABELS_ZH.get(k, k)
            errors.append(f'缺少音乐画像字段：{label} ({k})')
    return errors


def _format_profile_value(v, indent: int = 0) -> str:
    """Serialize nested profile value to readable prompt text."""
    pad = '  ' * indent
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        text = ' '.join(v.split())
        return text
    if isinstance(v, (list, tuple)):
        flat = []
        for item in v:
            if isinstance(item, (dict, list, tuple)):
                flat.append(_format_profile_value(item, indent))
            else:
                s = _format_profile_value(item, 0)
                if s:
                    flat.append(s)
        # short list inline
        if all(isinstance(x, (str, int, float, bool)) or x is None for x in v):
            return ', '.join(str(x) for x in flat if x)
        return '\n'.join(f'{pad}- {line}' if not line.startswith(' ') else f'{pad}{line}'
                         for line in flat if line)
    if isinstance(v, dict):
        lines = []
        for kk, vv in v.items():
            if not _has_content(vv):
                continue
            body = _format_profile_value(vv, indent + 1)
            if not body:
                continue
            if '\n' in body:
                lines.append(f'{pad}{kk}:')
                for bl in body.splitlines():
                    lines.append(bl if bl.startswith(' ') else f'{pad}  {bl}')
            else:
                lines.append(f'{pad}{kk}: {body}')
        return '\n'.join(lines)
    return str(v)


def serialize_profile(
    profile: dict,
    stage: str = 'compact',
    taxonomy: Optional[dict] = None,
) -> str:
    """
    Build LLM-facing profile text.
    stage: planner | composer | selection_measure | selection_symbol | compact | full
    """
    if not profile:
        return ''
    tax = taxonomy if taxonomy is not None else load_taxonomy()
    inject = (tax.get('inject') or {})
    keys = list(inject.get(stage) or inject.get('compact') or PROFILE_KEYS)
    if stage == 'full':
        # stable order: required first, then remaining
        keys = list(PROFILE_KEYS)
        for k in profile.keys():
            if k not in keys:
                keys.append(k)

    lines = []
    for k in keys:
        if k not in profile or not _has_content(profile.get(k)):
            continue
        label = PROFILE_LABELS_ZH.get(k, k)
        body = _format_profile_value(profile[k], 0)
        if not body:
            continue
        if '\n' in body:
            lines.append(f'{label}（{k}）:')
            for bl in body.splitlines():
                lines.append(f'  {bl}' if not bl.startswith(' ') else f'  {bl}')
        else:
            lines.append(f'{label}（{k}）: {body}')
    return '\n'.join(lines)


def list_genres(force: bool = False) -> List[dict]:
    sig = _packs_signature()
    if not force and _CACHE['genres'] is not None and _CACHE['genres_sig'] == sig:
        return list(_CACHE['genres'])

    taxonomy = load_taxonomy(force=force)
    family_values = set(((taxonomy.get('dimensions') or {}).get('family') or {}).get('values') or {})
    out: List[dict] = []
    if not os.path.isdir(GENRE_PACKS_DIR):
        _CACHE['genres'] = out
        _CACHE['genres_sig'] = sig
        return out

    for family in sorted(os.listdir(GENRE_PACKS_DIR)):
        fam_path = os.path.join(GENRE_PACKS_DIR, family)
        if not os.path.isdir(fam_path):
            continue
        for pack in sorted(os.listdir(fam_path)):
            meta_path = os.path.join(fam_path, pack, 'meta.yaml')
            if not os.path.isfile(meta_path):
                continue
            try:
                meta = _load_yaml(meta_path)
            except Exception:
                continue
            pack_id = meta.get('id') or pack
            dims = dict(meta.get('dimensions') or {})
            dims['family'] = family
            profile = _normalize_profile(meta.get('profile'))
            # hoist top-level generation helpers into profile for staged inject
            for hoist in ('llm_instruction', 'generation', 'foundation_score'):
                if meta.get(hoist) and not _has_content(profile.get(hoist)):
                    profile[hoist] = meta[hoist]
            errors = _validate_dimensions(dims, taxonomy)
            errors.extend(_validate_profile(profile, taxonomy))
            if family_values and family not in family_values:
                errors.append(f'family 目录 {family} 不在 taxonomy.family.values')
            ref = genre_ref(family, pack_id)
            entry = {
                'id': ref,
                'pack_id': pack_id,
                'name': meta.get('name') or pack_id,
                'name_en': meta.get('name_en') or '',
                'version': meta.get('version') or '',
                'enabled': bool(meta.get('enabled', True)),
                'pipeline': 'overlay',
                'kind': 'genre',
                'dimensions': dims,
                'profile': profile,
                'triggers': meta.get('triggers') or {},
                'summary': meta.get('summary') or '',
                'generation': meta.get('generation') or profile.get('generation') or {},
                'llm_instruction': profile.get('llm_instruction') or meta.get('llm_instruction') or '',
                'path': os.path.join(fam_path, pack),
                'taxonomy_errors': errors,
                'meta': meta,
            }
            out.append(entry)

    _CACHE['genres'] = out
    _CACHE['genres_sig'] = sig
    return list(out)


def get_genre(skill_id: str, force: bool = False) -> Optional[dict]:
    for g in list_genres(force=force):
        if g.get('id') == skill_id:
            return g
    return None


def set_genre_enabled(skill_id: str, enabled: bool) -> bool:
    g = get_genre(skill_id, force=True)
    if not g:
        return False
    meta_path = os.path.join(g['path'], 'meta.yaml')
    meta = _load_yaml(meta_path)
    meta['enabled'] = bool(enabled)
    # keep dimensions.family aligned with folder
    dims = dict(meta.get('dimensions') or {})
    family, pack = parse_genre_ref(skill_id)
    dims['family'] = family
    meta['dimensions'] = dims
    if not meta.get('id'):
        meta['id'] = pack
    _dump_yaml(meta_path, meta)
    invalidate_cache()
    return True


def genres_by_family(force: bool = False) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = {}
    for g in list_genres(force=force):
        fam = (g.get('dimensions') or {}).get('family') or 'other'
        grouped.setdefault(fam, []).append(g)
    return grouped


def dimension_label(dim_key: str, value: str) -> str:
    tax = load_taxonomy()
    dim = ((tax.get('dimensions') or {}).get(dim_key) or {})
    vals = dim.get('values') or {}
    entry = vals.get(value)
    if isinstance(entry, dict):
        return entry.get('label') or value
    return value


def format_genre_dims(g: dict) -> str:
    dims = g.get('dimensions') or {}
    order = [
        'family', 'form', 'tempo_energy', 'mood', 'medium',
        'tradition', 'tonal_language', 'rhythmic_identity',
    ]
    bits = []
    for k in order:
        v = dims.get(k)
        if v:
            bits.append(f'{dimension_label(k, v)}')
    return ' · '.join(bits)


def format_genre_profile(g: dict, compact: bool = False) -> str:
    """Human-readable profile (UI / compact)."""
    profile = g.get('profile') or {}
    stage = 'compact' if compact else 'full'
    text = serialize_profile(profile, stage=stage)
    if compact and len(text) > 400:
        return text[:397] + '…'
    return text


def genre_profile_prompt_block(g: dict, stage: str = 'composer') -> str:
    """Inject into LLM system prompt — stage-aware, deduplicated fields."""
    name = g.get('name') or g.get('pack_id') or g.get('id')
    dims = format_genre_dims(g)
    summary = ' '.join(str(g.get('summary') or '').split())
    body = serialize_profile(g.get('profile') or {}, stage=stage)
    parts = [
        f'Style pack 「{name}」 ({dims}).',
    ]
    if summary:
        parts.append(f'Summary: {summary}')
    parts.append(f'You MUST realize this musical profile in actual notes (stage={stage}):')
    parts.append(body)
    parts.append(
        'Map tempo/meter/key into plan/score JSON. '
        'Style must be audible without relying on the word Waltz/style name in metadata. '
        'Original music only.'
    )
    return '\n'.join(p for p in parts if p)


# ---------- prompts ----------

def _inject_stages(item_id: str) -> Tuple[str, ...]:
    """skill 在 index.yaml 里声明的「SKILL.md 注入到哪些阶段」。

    只有显式声明才回退，避免把 stub 型 SKILL.md 无差别灌进所有阶段。
    """
    if is_genre_id(item_id):
        return ()
    for s in list_skills():
        if s.get('id') == item_id:
            raw = s.get('inject') or []
            if isinstance(raw, str):
                raw = [raw]
            return tuple(str(x) for x in raw if str(x) in INJECT_STAGES)
    return ()


def skill_injection_stages(skill_id: str) -> Dict[str, int]:
    """某个 skill 在各阶段实际注入的字符数（0 = 不注入）。诊断用。"""
    return {st: len(_addon_text(skill_id, st)) for st in INJECT_STAGES}


def injection_matrix() -> Dict[str, Dict[str, int]]:
    """任务 Skill × 阶段的注入矩阵。UI 用它回答「任务 Skill 还起作用吗」。"""
    return {s['id']: skill_injection_stages(s['id'])
            for s in list_skills() if not is_genre_id(s.get('id') or '')}


def live_stages(skill_id: str) -> List[str]:
    """该 skill 真正会注入的阶段（字符数 > 0）。"""
    return [st for st, n in skill_injection_stages(skill_id).items() if n > 0]

def _addon_text(item_id: str, stage: str) -> str:
    fname = STAGE_FILES.get(stage, f'{stage}.md')
    body = _read_text(os.path.join(skill_dir(item_id), fname))
    if body:
        return body
    # 回退：在 index.yaml 里显式声明 `inject: [<stage>]` 的 skill，用 SKILL.md
    # 顶上该阶段的注入正文。没有声明就保持「不注入」—— stub 型 SKILL.md
    # （几十字的导航说明）塞进提示词只会稀释指令。
    if stage in _inject_stages(item_id):
        return _read_text(os.path.join(skill_dir(item_id), 'SKILL.md'))
    return ''


def build_system_prompt(stage: str, skill_ids: List[str]) -> str:
    cache_key = (stage, tuple(skill_ids))
    if cache_key in _CACHE['prompts']:
        return _CACHE['prompts'][cache_key]
    base_name = STAGE_FILES.get(stage, f'{stage}.md')
    parts = [_read_text(os.path.join(BASE_DIR, base_name))]
    # map pipeline stages to profile inject profiles
    # translator 显式列出：它是新创作主路径，不写进白名单就会静默退回 composer
    # 的字段集（taxonomy.inject.composer），语义上不对但不报错。
    inject_stage = stage
    if stage not in (
        'translator', 'planner', 'composer', 'selection_measure',
        'selection_symbol', 'compact', 'full',
    ):
        inject_stage = 'composer'
    for sid in skill_ids:
        if is_genre_id(sid):
            g = get_genre(sid)
            if g and g.get('profile'):
                parts.append(
                    f'\n\n--- Genre profile: {sid} ---\n'
                    f'{genre_profile_prompt_block(g, stage=inject_stage)}'
                )
        addon = _addon_text(sid, stage)
        if addon:
            label = 'Genre notes' if is_genre_id(sid) else 'Skill'
            parts.append(f'\n\n--- {label}: {sid} ---\n{addon}')
    # planner / translator 注入「已学」风格目录：由 LLM 解释意图并写出 genre_id，
    # 不靠关键词表挂包（避免「七和弦→爵士」「弦乐四重奏→奏鸣曲」误判）。
    if stage in ('translator', 'planner'):
        catalog = _genre_catalog_block(learned_only=True)
        if catalog:
            parts.append(
                '\n\n--- 风格目录（仅已学包，LLM 解释命中后写 genre_id）---\n'
                + catalog)
        # 规则库摘要：对位/终止/乐句/声部等可执行要点（与管理器规则库同源）
        try:
            import rule_library as rule_lib
            rules_block = rule_lib.rules_prompt_block(stage)
        except Exception:
            rules_block = ''
        if rules_block:
            parts.append(
                '\n\n--- 规则库摘要（Planner/Translator 必遵）---\n'
                + rules_block)
    text = '\n'.join(p for p in parts if p).strip()
    _CACHE['prompts'][cache_key] = text
    return text


def _genre_catalog_block(*, learned_only: bool = True) -> str:
    """风格包语义目录，供 planner/translator LLM 识别并写出 genre_id。

    默认**只列已学包**（有 grammar.yaml）：未跑 /learn-grammar 的专家包效果差，
    不进目录，避免 LLM 选中后空转。

    每行：`genre_id｜中文名｜英文名｜家族｜默认小节｜默认编制｜简介`
    LLM 必须把命中包的 genre_id（如 jazz/swing-standard）写入 plan.genre_id，
    并在 new_score.style 使用同一路径；技术词（减七、ii-V、弦乐四重奏）
    是作曲约束，不是风格 id。
    """
    lines = []
    genres = learned_genres() if learned_only else _enabled_genres()
    for g in genres:
        fam = str((g.get('dimensions') or {}).get('family') or '')
        name = g.get('name') or ''
        name_en = g.get('name_en') or ''
        pid = g.get('pack_id') or ''
        gid = g.get('id') or ''
        # jazz/swing-standard
        ref = gid.split(':', 1)[-1] if str(gid).startswith(GENRE_PREFIX) else (
            f'{fam}/{pid}' if fam and pid else pid)
        gen = g.get('generation') or {}
        fd = gen.get('foundation') or {}
        bars_cfg = fd.get('bars') if isinstance(fd.get('bars'), dict) else {}
        bars_d = bars_cfg.get('default') or (gen.get('composition') or {}).get(
            'bars_default') or '—'
        insts = fd.get('instruments_default') or []
        inst_str = ','.join(str(x) for x in insts[:5]) if insts else '—'
        summary = ' '.join(str(g.get('summary') or '').split())[:60] or '—'
        lines.append(
            f'{ref}｜{name}｜{name_en or "—"}｜{fam or "—"}｜'
            f'bars≈{bars_d}｜{inst_str}｜{summary}'
        )
    if not lines:
        return '（暂无已学风格包）'
    header = (
        '只用下列已学风格包。输出 plan.genre_id = 左侧路径（如 jazz/swing-standard）；'
        'new_score.style 用同一路径。无匹配则 genre_id 置空，勿发明包名。\n'
    )
    return header + '\n'.join(lines)


# ---------- routing ----------

def _enabled_skills() -> List[dict]:
    return [s for s in list_skills() if s.get('enabled', True)]


def _enabled_genres() -> List[dict]:
    return [
        g for g in list_genres()
        if g.get('enabled', True) and not g.get('taxonomy_errors')
    ]


def _match_keywords(text: str, keywords: List[str]) -> bool:
    if not keywords:
        return False
    low = (text or '').lower()
    for kw in keywords:
        if kw and kw.lower() in low:
            return True
    return False


def match_genre_by_name(
    text: str,
    *,
    learned_only: bool = True,
    allow_prefix: bool = True,
) -> Optional[dict]:
    """只按风格包正式名称命中：中文名 / 英文名 / pack_id / family/pack_id。

    **不用 keywords / style_tags**。默认只在已学包里找。
    用于 /style 前缀剥离，以及 LLM 写出的 genre / genre_id 回绑。
    """
    raw = (text or '').strip()
    if not raw:
        return None
    low = raw.lower().replace('_', '-').replace(' ', '')
    pool = learned_genres() if learned_only else _enabled_genres()
    candidates: List[Tuple[int, dict]] = []
    for g in pool:
        name = str(g.get('name') or '')
        name_en = str(g.get('name_en') or '')
        pack_id = str(g.get('pack_id') or '')
        fam = str((g.get('dimensions') or {}).get('family') or '')
        gid = str(g.get('id') or '')
        ref = gid.split(':', 1)[-1] if gid.startswith(GENRE_PREFIX) else (
            f'{fam}/{pack_id}' if fam and pack_id else pack_id)
        tokens = [
            (name, 100),
            (name_en, 95),
            (ref, 90),
            (pack_id, 85),
            (f'{fam}/{pack_id}' if fam and pack_id else '', 90),
        ]
        for tok, base in tokens:
            if not tok:
                continue
            t = str(tok)
            tl = t.lower().replace('_', '-').replace(' ', '')
            if not tl:
                continue
            if raw == t or low == tl:
                candidates.append((base + 50 + len(t), g))
                continue
            if not allow_prefix:
                continue
            if raw.startswith(t) or low.startswith(tl):
                rest = raw[len(t):] if raw.startswith(t) else ''
                if rest == '' or (rest and rest[0] in ' \t，,。；;：:、|/'):
                    candidates.append((base + 20 + len(t), g))
                    continue
            if len(t) >= 2 and (t in raw or tl in low):
                candidates.append((base + len(t), g))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def match_genre(text: str) -> Optional[dict]:
    """兼容旧调用：优先正式名；无正式名时才弱关键词兜底（主路径应走 LLM 目录）。"""
    g = match_genre_by_name(text, learned_only=False, allow_prefix=True)
    if g:
        return g
    raw = (text or '').strip()
    if not raw:
        return None
    low = raw.lower()
    best = None
    best_score = 0
    for g in _enabled_genres():
        score = 0
        tr = g.get('triggers') or {}
        for kw in list(tr.get('keywords') or []) + list(tr.get('style_tags') or []):
            if kw and str(kw).lower() in low:
                score = max(score, 50 + len(str(kw)))
        if score > best_score:
            best_score = score
            best = g
    # 基分 50 + 关键词长度；中文短词（「交响曲」等）也要能命中
    return best if best_score >= 50 else None


def is_full_score_revision_request(text: str) -> bool:
    """
    Long structured critiques / rewrite briefs must NOT use selection_measure.
    Example: multi-point notes mentioning 全曲 / 第1–32小节 / 高优先级.
    """
    t = text or ''
    body = re.sub(r'^\[选区[^\]]*\]\s*', '', t).strip()
    if re.search(r'/rewrite\b|整曲重写|整曲修改|按.*规则.*改|全面修改', body, re.I):
        return True
    if len(body) < 180:
        return False
    signals = 0
    if re.search(r'全曲|整首|从头到尾', body):
        signals += 2
    if re.search(r'第\s*\d+\s*[–\-~至到]\s*\d+\s*小节', body):
        signals += 2
    if body.count('修改：') + body.count('修改:') >= 2:
        signals += 2
    if body.count('位置：') + body.count('位置:') >= 2:
        signals += 1
    if re.search(r'高优先级|中优先级|最优先改|不改会明显', body):
        signals += 2
    if re.search(r'\n\s*\d+[\.、]', body):
        signals += 1
    return signals >= 3


def resolve_skills(
    user_text: str,
    selection: Optional[Dict[str, Any]] = None,
    edit_intent: Optional[str] = None,
    slash_cmd: Optional[str] = None,
    style_pref: str = '',
    genre_id: str = '',
    llm_available: bool = False,
) -> Tuple[List[str], str]:
    """
    返回 (id 列表, 主 pipeline)。
    任务 Skill 决定 pipeline；Genre pack 仅作 overlay（最多 max_active_packs）。
    """
    skills = _enabled_skills()
    by_id = {s['id']: s for s in skills}
    primary_id = 'compose-full'
    pipeline = 'planner_composer'

    sel = selection or {}
    # Whole-score revision briefs must not be trapped by a leftover measure click
    if is_full_score_revision_request(user_text or ''):
        sel = {}
        if not edit_intent:
            edit_intent = 'rewrite'

    has_sym = bool(sel.get('symbols'))
    has_meas = bool(sel.get('measures'))
    intent = (edit_intent or '').strip().lower()
    cmd = (slash_cmd or '').strip().lower()

    if has_sym and 'edit-symbol' in by_id:
        primary_id = 'edit-symbol'
        pipeline = 'selection_symbol'
    elif has_meas and 'edit-measure' in by_id:
        primary_id = 'edit-measure'
        pipeline = 'selection_measure'
    else:
        # When slash/intent is set, do not let unrelated keyword hits
        # (e.g. 「动机」→ develop-motif) steal a rewrite/complexify route.
        intent_matched = False
        if intent or cmd:
            best_prio = -1
            for s in skills:
                tr = s.get('triggers') or {}
                cmds = [c.lower() for c in (tr.get('commands') or [])]
                intents = [str(i).lower() for i in (tr.get('edit_intents') or [])]
                hit = bool(cmd and cmd in cmds) or bool(intent and intent in intents)
                if hit:
                    prio = int(s.get('priority') or 0)
                    if prio > best_prio:
                        best_prio = prio
                        primary_id = s['id']
                        pipeline = s.get('pipeline') or 'planner_composer'
                        intent_matched = True
        if not intent_matched:
            best_prio = -1
            for s in skills:
                tr = s.get('triggers') or {}
                hit = False
                cmds = [c.lower() for c in (tr.get('commands') or [])]
                if cmd and cmd in cmds:
                    hit = True
                intents = [str(i).lower() for i in (tr.get('edit_intents') or [])]
                if intent and intent in intents:
                    hit = True
                kws = list(tr.get('keywords') or [])
                if not hit and kws and _match_keywords(user_text or '', kws):
                    hit = True
                if not hit and not cmd and not intent and s.get('id') == 'compose-full':
                    hit = True
                if hit:
                    prio = int(s.get('priority') or 0)
                    if prio > best_prio:
                        best_prio = prio
                        primary_id = s['id']
                        pipeline = s.get('pipeline') or 'planner_composer'

    tax = load_taxonomy()
    max_packs = int(((tax.get('routing') or {}).get('max_active_packs') or 2))
    overlays: List[str] = []
    # pinned genre from /style（用户显式指定风格包时始终生效）
    if genre_id and is_genre_id(genre_id) and get_genre(genre_id):
        overlays.append(genre_id)
    # Genre 关键字匹配：仅当 LLM 不可用时作为兜底。有 LLM 时交由 translator
    # 通过注入的风格目录解释风格，避免「技术词误判为风格」——例如「减七和弦」
    # 不应因命中 swing 的 keyword「七和弦」而被判成爵士，「弦乐四重奏」也不应
    # 因命中 classical-sonata 的 keyword 而被判成古典奏鸣曲。
    if not llm_available:
        haystack = ' '.join(x for x in (user_text, style_pref) if x)
        for g in _enabled_genres():
            if g['id'] in overlays:
                continue
            tr = g.get('triggers') or {}
            kws = list(tr.get('keywords') or []) + list(tr.get('style_tags') or [])
            name_bits = [g.get('name') or '', g.get('name_en') or '', g.get('pack_id') or '']
            if _match_keywords(haystack, kws + [b for b in name_bits if b]):
                overlays.append(g['id'])
                if len(overlays) >= max_packs:
                    break

    out: List[str] = []
    for sid in [primary_id] + overlays[:max_packs]:
        if sid not in out:
            if is_genre_id(sid) or sid in by_id:
                out.append(sid)
    return out, pipeline


def format_skill_label(skill_ids: List[str]) -> str:
    names = []
    for sid in skill_ids:
        item = get_skill(sid) or get_genre(sid)
        if item:
            names.append(item.get('name') or sid)
        else:
            names.append(sid)
    try:
        import i18n
        def_label = 'default' if i18n.language() == 'en' else '默认'
    except Exception:
        def_label = '默认'
    return ' · '.join(names) if names else def_label


def format_skill_report(skill_ids: List[str], pipeline: str = '') -> str:
    """Multi-line report of skills used (for chat UI)."""
    try:
        import i18n
        is_en = i18n.language() == 'en'
    except Exception:
        is_en = False
    lines = ['[Call Skill]'] if is_en else ['【调用 Skill】']
    if not skill_ids:
        lines.append('  (default)' if is_en else '  （默认）')
    for sid in skill_ids:
        item = get_skill(sid) or get_genre(sid) or {}
        name = item.get('name') or sid
        if is_en:
            kind = 'Style' if is_genre_id(sid) else 'Task'
        else:
            kind = '风格' if is_genre_id(sid) else '任务'
        pipe = item.get('pipeline') or ''
        lines.append(f'  · [{kind}] {name}')
        lines.append(f'      id: {sid}' + (f'  pipeline: {pipe}' if pipe else ''))
    if pipeline:
        main_flow = 'Main flow: ' if is_en else '主流程: '
        lines.append(f'{main_flow}{pipeline}')
    return '\n'.join(lines)


# ---------- 内核脚本库（只读，供 UI / 测试共用） ----------

def script_library() -> List[dict]:
    """compose_kernel 的确定性脚本清单。导入失败（缺依赖）返回 []。"""
    try:
        import compose_kernel as K
    except Exception:
        return []
    out = []
    for d in getattr(K, 'SCRIPT_DOCS', []) or []:
        out.append({
            'script': str(d.get('script') or ''),
            'desc': str(d.get('desc') or ''),
            'args': str(d.get('args') or ''),
        })
    return out


def script_notes() -> Dict[str, str]:
    """脚本库的通用说明（调用顺序模板 / 段落织体映射）。"""
    try:
        import compose_kernel as K
    except Exception:
        return {'call_order': '', 'texture_map': ''}
    return {
        'call_order': getattr(K, 'SCRIPT_CALL_ORDER', ''),
        'texture_map': getattr(K, 'SCRIPT_TEXTURE_MAP', ''),
    }


# ---------- 风格契约（只读） ----------

def genre_contract_path(skill_id: str):
    import style_contract as SC
    return os.path.join(skill_dir(skill_id), SC.CONTRACT_FILENAME)


def genre_contract(skill_id: str):
    """读风格包的 style_contract.yaml。返回 (StyleContract|None, path)。

    已有 grammar.yaml 的包不再读旧契约（可解释语法取代特征契约）。
    """
    import style_contract as SC
    path = genre_contract_path(skill_id)
    if has_learned_grammar(skill_id):
        return None, path
    if not os.path.isfile(path):
        return None, path
    try:
        return SC.load_contract(path), path
    except Exception:
        return None, path


def genre_feature_count(skill_id: str) -> int:
    """该风格包 features/ 下的逐曲特征文件数（0 = 还没学过）。"""
    d = os.path.join(skill_dir(skill_id), 'features')
    if not os.path.isdir(d):
        return 0
    return sum(1 for n in os.listdir(d) if n.endswith('.json'))


#: 可解释语法文件名（/learn-grammar 写出）；有此文件才算「已学」。
GRAMMAR_FILENAME = 'grammar.yaml'


def grammar_path(skill_id: str) -> str:
    return os.path.join(skill_dir(skill_id), GRAMMAR_FILENAME)


def has_learned_grammar(skill_id: str) -> bool:
    """该包是否已蒸馏出可解释语法（grammar.yaml）。"""
    if not skill_id or not str(skill_id).startswith('genre:'):
        return False
    return os.path.isfile(grammar_path(skill_id))


def is_learned(skill_id: str) -> bool:
    """该风格包是否「学过」：有 /learn-grammar 写出的 grammar.yaml。

    旧的 style_contract.yaml（特征统计契约）不再作为已学依据；UI / 风格目录 /
    Planner 只展示这两天学到的可解释语法包。
    """
    return has_learned_grammar(skill_id)


def learned_genres(force: bool = False) -> List[dict]:
    """只返回已学到可解释语法、且 taxonomy 校验通过的风格包（UI 的可见集合）。"""
    out = []
    for g in list_genres(force=force):
        if not g.get('enabled', True) or g.get('taxonomy_errors'):
            continue
        if not is_learned(g['id']):
            continue
        out.append(g)
    return out
