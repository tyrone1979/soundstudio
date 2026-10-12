# -*- coding: utf-8 -*-
"""作曲规则库：对位规则 + 教学规则 + 内核确定性脚本的统一登记。

真相源
------
- 手工规则：``skills/composition/rules/catalog.yaml`` + 各分类下的 ``*.md``
- 内核脚本：``compose_kernel.SCRIPT_DOCS`` / ``SCRIPT_IMPL``（自动发现并合并）
- 对位权威条文：``music_score.two_part_species.get_counterpoint_rules_doc()``

管理器「规则库」页通过本模块列出条目，并同时展示文档与实现源码。
"""
from __future__ import annotations

import inspect
import os
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

ROOT = os.path.dirname(os.path.abspath(__file__))
RULES_ROOT = os.path.join(ROOT, 'skills', 'composition', 'rules')
CATALOG_PATH = os.path.join(RULES_ROOT, 'catalog.yaml')

_cache: Optional[Dict[str, Any]] = None


def invalidate_cache() -> None:
    global _cache
    _cache = None


def _load_yaml(path: str) -> dict:
    if yaml is None:
        raise RuntimeError('需要 PyYAML：pip install pyyaml')
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _read_text(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ''
    with open(path, encoding='utf-8') as f:
        return f.read()


def _repo_path(rel: str) -> str:
    rel = (rel or '').replace('\\', '/').lstrip('/')
    return os.path.join(ROOT, *rel.split('/'))


def _resolve_code_ref(ref: str) -> Tuple[str, str, str]:
    """解析 ``path.py`` 或 ``path.py#name`` → (display, filepath, source)."""
    ref = (ref or '').strip()
    if not ref:
        return ('', '', '')
    if '#' in ref:
        path_part, name = ref.split('#', 1)
    else:
        path_part, name = ref, ''
    path_part = path_part.strip()
    name = name.strip()
    abs_path = _repo_path(path_part) if path_part else ''

    # 优先：compose_kernel.SCRIPT_IMPL[name]
    if name and (path_part.endswith('compose_kernel.py') or path_part == 'compose_kernel.py'):
        try:
            import compose_kernel as K
            fn = (getattr(K, 'SCRIPT_IMPL', {}) or {}).get(name)
            if fn is None:
                # 也允许直接函数名 _s_cadence
                fn = getattr(K, name, None)
            if callable(fn):
                src = inspect.getsource(fn)
                return (f'{path_part} · {getattr(fn, "__name__", name)}',
                        inspect.getsourcefile(fn) or abs_path, src)
        except Exception:
            pass

    if name and abs_path.endswith('.py'):
        src = _extract_def_source(abs_path, name)
        if src:
            return (f'{path_part} · {name}', abs_path, src)

    # 模块级常量等 AST 取不到的：尝试 import 后取属性
    if name and abs_path.endswith('.py') and os.path.isfile(abs_path):
        try:
            mod_name = path_part[:-3].replace('/', '.').replace('\\', '.')
            if mod_name.startswith('music_score.'):
                import importlib
                mod = importlib.import_module(mod_name)
                obj = getattr(mod, name, None)
                if obj is not None:
                    if callable(obj) and not isinstance(obj, type):
                        return (f'{path_part} · {name}', abs_path, inspect.getsource(obj))
                    if isinstance(obj, str):
                        return (f'{path_part} · {name}', abs_path, obj)
                    try:
                        return (f'{path_part} · {name}', abs_path, inspect.getsource(obj))
                    except (TypeError, OSError):
                        return (f'{path_part} · {name}', abs_path, _read_text(abs_path))
        except Exception:
            pass

    # 整文件
    if abs_path and os.path.isfile(abs_path):
        return (path_part or abs_path, abs_path, _read_text(abs_path))
    return (ref, abs_path, f'# 找不到代码引用：{ref}\n')


def _extract_def_source(path: str, name: str) -> str:
    """不导入模块，按 AST 截取顶层 def/class 源码（过期模块导入会告警或拉起 Qt）。"""
    text = _read_text(path)
    if not text:
        return ''
    try:
        import ast
        tree = ast.parse(text)
    except SyntaxError:
        return ''
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name == name:
            return ast.get_source_segment(text, node) or ''
    return ''


def _enrich_counterpoint_doc(doc: str) -> str:
    """手工摘要后追加权威 COUNTERPOINT_RULES_DOC。"""
    try:
        from music_score.two_part_species import get_counterpoint_rules_doc
        auth = (get_counterpoint_rules_doc() or '').strip()
    except Exception:
        auth = ''
    if not auth:
        return doc
    if doc.strip():
        return doc.rstrip() + '\n\n---\n\n## 权威条文（运行时注入）\n\n' + auth + '\n'
    return auth + '\n'


def _manual_rules(catalog: dict) -> List[Dict[str, Any]]:
    cats = {c['id']: c for c in (catalog.get('categories') or []) if c.get('id')}
    out: List[Dict[str, Any]] = []
    for raw in catalog.get('rules') or []:
        if not isinstance(raw, dict) or not raw.get('id'):
            continue
        rid = str(raw['id'])
        cat_id = str(raw.get('category') or 'other')
        cat = cats.get(cat_id) or {'id': cat_id, 'label': cat_id, 'desc': ''}
        doc_rel = str(raw.get('doc') or '')
        doc_path = os.path.join(RULES_ROOT, doc_rel) if doc_rel else ''
        doc_text = _read_text(doc_path)
        if rid == 'species_first_two_part':
            doc_text = _enrich_counterpoint_doc(doc_text)

        code_blobs: List[Dict[str, str]] = []
        for ref in raw.get('code') or []:
            disp, fpath, src = _resolve_code_ref(str(ref))
            code_blobs.append({
                'ref': str(ref),
                'display': disp,
                'path': fpath,
                'source': src,
            })

        sources = []
        for s in raw.get('sources') or []:
            if isinstance(s, dict):
                sources.append({
                    'title': str(s.get('title') or ''),
                    'url': str(s.get('url') or ''),
                })

        out.append({
            'id': rid,
            'kind': 'manual',
            'category': cat_id,
            'category_label': str(cat.get('label') or cat_id),
            'category_desc': str(cat.get('desc') or ''),
            'title': str(raw.get('title') or rid),
            'summary': str(raw.get('summary') or ''),
            'tags': [str(t) for t in (raw.get('tags') or [])],
            'doc_path': doc_path,
            'doc': doc_text,
            'code': code_blobs,
            'sources': sources,
        })
    return out


def _script_rules() -> List[Dict[str, Any]]:
    """把每个内核脚本登记为规则条目（文档=SCRIPT_DOCS，代码=SCRIPT_IMPL）。"""
    try:
        import compose_kernel as K
    except Exception:
        return []

    notes_order = getattr(K, 'SCRIPT_CALL_ORDER', '') or ''
    notes_tex = getattr(K, 'SCRIPT_TEXTURE_MAP', '') or ''
    impl = getattr(K, 'SCRIPT_IMPL', {}) or {}
    docs = getattr(K, 'SCRIPT_DOCS', []) or []
    out: List[Dict[str, Any]] = []

    for d in docs:
        name = str(d.get('script') or '')
        if not name:
            continue
        desc = str(d.get('desc') or '')
        args = str(d.get('args') or '')
        doc_md = '\n'.join([
            f'# 内核脚本：`{name}`',
            '',
            '确定性脚本：LLM 只选脚本与参数，音符由 `compose_kernel` 写出。',
            '',
            '## 说明',
            desc or '—',
            '',
            '## 参数',
            args or '—',
            '',
            '## 通用调用顺序',
            notes_order or '—',
            '',
            '## 段落织体映射参考',
            notes_tex or '—',
        ])

        code_blobs: List[Dict[str, str]] = []
        fn = impl.get(name)
        if callable(fn):
            try:
                src = inspect.getsource(fn)
                fpath = inspect.getsourcefile(fn) or _repo_path('compose_kernel.py')
                code_blobs.append({
                    'ref': f'compose_kernel.py#{name}',
                    'display': f'compose_kernel.py · {fn.__name__}',
                    'path': fpath,
                    'source': src,
                })
            except (OSError, TypeError):
                pass
        if not code_blobs:
            disp, fpath, src = _resolve_code_ref(f'compose_kernel.py#{name}')
            code_blobs.append({
                'ref': f'compose_kernel.py#{name}',
                'display': disp or name,
                'path': fpath,
                'source': src or '# （无实现源码）\n',
            })

        out.append({
            'id': f'kernel_script_{name}',
            'kind': 'kernel_script',
            'category': 'kernel_script',
            'category_label': '内核确定性脚本',
            'category_desc': 'compose_kernel 脚本（LLM 只选脚本+参数，音符由内核写）',
            'title': f'脚本 · {name}',
            'summary': desc[:120] + ('…' if len(desc) > 120 else ''),
            'tags': ['script', name, 'kernel'],
            'doc_path': '',
            'doc': doc_md,
            'code': code_blobs,
            'sources': [{
                'title': 'compose_kernel.SCRIPT_DOCS / SCRIPT_IMPL',
                'url': '',
            }],
            'script_name': name,
        })
    return out


# 已过期的确定性代码：仍在仓库里（兼容/参考），但不再被任何流程调用。
# 管理器单独成类展示；不注入 Planner/Translator 提示词。
DEPRECATED_CODE: List[Dict[str, str]] = [
    {
        'id': 'kernel_fugue_counterpoint',
        'title': '内核辅助 · _fugue_counterpoint（旧对题生成）',
        'code': 'compose_kernel.py#_fugue_counterpoint',
        'reason': '无调用方；赋格对题/间插已改由主题头模进生成。',
        'replacement': 'compose_kernel._fugue_sequence',
    },
    {
        'id': 'reverse_build_regeneration_prompt',
        'title': '逆向 · build_regeneration_prompt（旧名）',
        'code': 'score_reverse.py#build_regeneration_prompt',
        'reason': '兼容旧名的薄包装，全仓库无调用。',
        'replacement': 'score_reverse.build_score_description',
    },
    {
        'id': 'reverse_compile_reverse_prompt',
        'title': '逆向 · compile_reverse_prompt（旧 JSON 块解析）',
        'code': 'score_reverse.py#compile_reverse_prompt',
        'reason': '旧版带 JSON 块的提示词格式已不再产生，全仓库无调用。',
        'replacement': 'score_reverse.text_to_spec',
    },
    {
        'id': 'reverse_replay_program',
        'title': '逆向 · replay_program',
        'code': 'score_reverse.py#replay_program',
        'reason': '只是 run_program 的别名，全仓库无调用。',
        'replacement': 'compose_kernel.run_program',
    },
    {
        'id': 'reverse_golden_g_major_string_program',
        'title': '逆向 · golden_g_major_string_program（旧验收样例）',
        'code': 'score_reverse.py#golden_g_major_string_program',
        'reason': '逆向验收已改用莫扎特小夜曲实谱，全仓库无调用。',
        'replacement': 'score_reverse.load_mozart_nachtmusik_spec',
    },
    {
        'id': 'legacy_main_window',
        'title': '旧版编辑器 · main.py + music_score/main_window.py',
        'code': 'main.py',
        'reason': '仅 main.py 引用；功能已全部迁到作曲工作室。',
        'replacement': 'score_studio.py（StudioWindow）',
    },
    {
        'id': 'legacy_musicxml_importer',
        'title': '旧版导入 · music_score/musicxml_importer.py',
        'code': 'music_score/musicxml_importer.py#import_musicxml',
        'reason': '仅旧版编辑器使用；丢延音线/力度/多声部，不再维护。',
        'replacement': 'score_studio.m21_to_model',
    },
    {
        'id': 'oneoff_rebuild_session_trace',
        'title': '一次性迁移 · scripts/dev/_rebuild_session_trace.py',
        'code': 'scripts/dev/_rebuild_session_trace.py',
        'reason': '为旧会话 2030c62765 补 compose_trace 的一次性脚本，迁移已完成。',
        'replacement': '（无需替代）',
    },
]


def _deprecated_rules() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in DEPRECATED_CODE:
        disp, fpath, src = _resolve_code_ref(d['code'])
        doc = '\n'.join([
            f"# ⚠️ 已过期：{d['title']}",
            '',
            f"原因：{d['reason']}",
            f"替代：{d['replacement']}",
            '',
            '调用时会发出 DeprecationWarning；后续版本删除。',
        ])
        out.append({
            'id': f"deprecated_{d['id']}",
            'kind': 'deprecated',
            'status': 'deprecated',
            'category': 'deprecated',
            'category_label': '已过期代码',
            'category_desc': '不再被流程调用的确定性代码（保留兼容/参考，后续删除）',
            'title': d['title'],
            'summary': d['reason'],
            'tags': ['deprecated'],
            'doc_path': '',
            'doc': doc,
            'code': [{'ref': d['code'], 'display': disp or d['code'],
                      'path': fpath, 'source': src}],
            'sources': [],
            'replacement': d['replacement'],
        })
    return out


_GRAMMAR_CODE = (
    'score_learning/grammar/learn.py#learn',
    'score_learning/grammar/compose.py#compose',
    'score_learning/grammar/compose.py#compose_phrase_candidates',
    'score_learning/grammar/compose.py#compose_bass',
    'score_learning/grammar/compose.py#compose_inner',
    'score_learning/grammar/check.py#check_satb',
)

_LEADSHEET_CODE = (
    'score_learning/leadsheet/learn.py#learn',
    'score_learning/leadsheet/compose.py#compose',
    'score_learning/leadsheet/compose.py#harmony_unit',
    'score_learning/leadsheet/compose.py#melody_dp',
    'score_learning/leadsheet/compose.py#accompaniment',
    'score_learning/leadsheet/check.py#check_piece',
)


def _leadsheet_pack_style(pack_dir: str) -> Optional[Dict[str, Any]]:
    try:
        from score_learning.leadsheet.analyze import STYLES
    except Exception:
        return None
    rel = tuple(os.path.normpath(pack_dir).split(os.sep)[-2:])
    for st in STYLES.values():
        if tuple(st['pack']) == rel:
            return st
    return None


def _grammar_rules() -> List[Dict[str, Any]]:
    """从语料学到的可解释语法（grammar.yaml + grammar.md），每个风格包一条。"""
    import glob
    out: List[Dict[str, Any]] = []
    pattern = os.path.join(ROOT, 'skills', 'composition', 'genre', 'packs', '*', '*',
                           'grammar.yaml')
    for path in sorted(glob.glob(pattern)):
        pack = os.path.basename(os.path.dirname(path))
        md_path = os.path.join(os.path.dirname(path), 'grammar.md')
        doc = _read_text(md_path) if os.path.isfile(md_path) else f'# {pack} 语法\n'
        style = _leadsheet_pack_style(os.path.dirname(path))
        source = style['source'] if style else 'music21 巴赫众赞歌语料（Riemenschneider）'
        code = []
        for ref in (_LEADSHEET_CODE if style else _GRAMMAR_CODE):
            disp, fpath, src = _resolve_code_ref(ref)
            code.append({'ref': ref, 'display': disp or ref, 'path': fpath, 'source': src})
        out.append({
            'id': f'learned_grammar_{pack}',
            'kind': 'learned_grammar',
            'status': 'active',
            'category': 'learned_grammar',
            'category_label': '学到的作曲语法',
            'category_desc': '从乐谱语料学习、蒸馏成可解释规则脚本的作曲语法（确定性执行）',
            'title': f'学到的语法 · {pack}',
            'summary': doc.split('\n', 3)[2][:120] if doc.count('\n') >= 3 else pack,
            'tags': ['grammar', 'learned', pack],
            'doc_path': os.path.relpath(md_path, ROOT),
            'doc': doc,
            'code': code,
            'sources': [{'title': source, 'url': ''}],
        })
    return out


def load_catalog() -> dict:
    if not os.path.isfile(CATALOG_PATH):
        return {'categories': [], 'rules': [], 'title': '规则库', 'schema': 0}
    return _load_yaml(CATALOG_PATH)


def list_rules(*, include_scripts: bool = True,
               include_deprecated: Optional[bool] = None) -> List[Dict[str, Any]]:
    """返回统一规则列表（手工 + 可选内核脚本 + 可选已过期代码）。

    include_deprecated 默认跟随 include_scripts（管理器全量展示；提示词注入不含）。
    """
    if include_deprecated is None:
        include_deprecated = include_scripts
    catalog = load_catalog()
    items = _manual_rules(catalog)
    if include_scripts:
        items.extend(_script_rules())
        items.extend(_grammar_rules())
    for r in items:
        r.setdefault('status', 'active')
    if include_deprecated:
        items.extend(_deprecated_rules())
    return items


def categories() -> List[Dict[str, str]]:
    catalog = load_catalog()
    cats = []
    for c in catalog.get('categories') or []:
        if not isinstance(c, dict) or not c.get('id'):
            continue
        cats.append({
            'id': str(c['id']),
            'label': str(c.get('label') or c['id']),
            'desc': str(c.get('desc') or ''),
        })
    # 保证内核脚本分类存在
    if not any(c['id'] == 'kernel_script' for c in cats):
        cats.append({
            'id': 'kernel_script',
            'label': '内核确定性脚本',
            'desc': 'compose_kernel 脚本',
        })
    if not any(c['id'] == 'learned_grammar' for c in cats):
        cats.append({
            'id': 'learned_grammar',
            'label': '学到的作曲语法',
            'desc': '从语料学习并蒸馏成可解释规则脚本',
        })
    if not any(c['id'] == 'deprecated' for c in cats):
        cats.append({
            'id': 'deprecated',
            'label': '已过期代码',
            'desc': '不再被流程调用（保留兼容/参考，后续删除）',
        })
    return cats


def get_rule(rule_id: str) -> Optional[Dict[str, Any]]:
    rid = (rule_id or '').strip()
    if not rid:
        return None
    for r in list_rules(include_scripts=True):
        if r['id'] == rid:
            return r
    return None


def rule_library_snapshot() -> Dict[str, Any]:
    """供诊断页 / 测试：计数与分类分布。"""
    rules = list_rules(include_scripts=True)
    by_cat: Dict[str, int] = {}
    for r in rules:
        by_cat[r['category']] = by_cat.get(r['category'], 0) + 1
    return {
        'catalog_path': CATALOG_PATH,
        'rules_root': RULES_ROOT,
        'total': len(rules),
        'manual': sum(1 for r in rules if r['kind'] == 'manual'),
        'kernel_script': sum(1 for r in rules if r['kind'] == 'kernel_script'),
        'deprecated': sum(1 for r in rules if r.get('status') == 'deprecated'),
        'by_category': by_cat,
        'ids': [r['id'] for r in rules],
    }


def format_rule_doc(rule: Dict[str, Any]) -> str:
    """拼装右侧「文档」面板文本。"""
    lines = [
        f"# {rule.get('title') or rule.get('id')}",
        '',
        f"分类：{rule.get('category_label') or rule.get('category')}"
        f"（{rule.get('kind')}）",
        f"id：`{rule.get('id')}`",
    ]
    if rule.get('status') == 'deprecated':
        lines.append(f"状态：⚠️ 已过期（替代：{rule.get('replacement') or '—'}）")
    summary = (rule.get('summary') or '').strip()
    if summary:
        lines += ['', '## 摘要', summary]
    tags = rule.get('tags') or []
    if tags:
        lines += ['', '标签：' + ' · '.join(str(t) for t in tags)]
    sources = rule.get('sources') or []
    if sources:
        lines += ['', '## 来源']
        for s in sources:
            title = s.get('title') or ''
            url = s.get('url') or ''
            if url:
                lines.append(f'- {title}  \n  {url}')
            elif title:
                lines.append(f'- {title}')
    doc = (rule.get('doc') or '').strip()
    lines += ['', '─' * 72, '', doc or '（无文档）']
    return '\n'.join(lines)


def format_rule_code(rule: Dict[str, Any]) -> str:
    """拼装「代码」面板文本（可多文件拼接）。"""
    blobs = rule.get('code') or []
    if not blobs:
        return '（本规则未绑定实现代码）\n'
    parts: List[str] = []
    for i, b in enumerate(blobs):
        head = b.get('display') or b.get('ref') or f'code[{i}]'
        path = b.get('path') or ''
        parts.append(f'# ▶ {head}')
        if path:
            parts.append(f'# path: {path}')
        parts.append('')
        parts.append((b.get('source') or '').rstrip() or '# （空）')
        parts.append('')
        parts.append('─' * 72)
        parts.append('')
    return '\n'.join(parts).rstrip() + '\n'


# ---------- Planner / Translator 注入用摘要 ----------

# 手工规则在提示词里的「怎么用」（比 catalog.summary 更可执行）
_PLANNER_HINTS: Dict[str, str] = {
    'species_first_two_part':
        '用户要 1:1/第一类/对位法且非赋格 → species=true, want_fugue=false, '
        'texture=first-species, instrumentation=["piano","piano"], genre_id=""',
    'cadence_types':
        '乐段前句倾向 HC（停 V），末句倾向 PAC（V→I、高声部落主音）；'
        '小调属和弦须含导音',
    'phrase_model_functions':
        '乐句和声按 T→PD→D（→T）规划，勿无故功能倒退；半终止停在 D',
    'common_progressions':
        '可用 ii–V–I、I–vi–IV–V、圈进行等套路填 harmony.language / 段落骨架',
    'satb_voice_leading':
        '四部/众赞：注意音域与倍音；外声部禁平行五八；倾向音解决',
    'parallel_fifths_octaves':
        '众赞/严格四部严禁平行五八；管弦外声部慎用',
    'phrase_period_sentence':
        'period≈前句 HC + 后句 PAC；sentence≈2+2+4；form/bars 对齐句末终止',
    'nonharmonic_tones':
        '强拍优先和弦音；严格对位第一类不写非和声音',
    'melody_contour':
        '级进为主、大跳回收；motif.development 可用重复/模进/倒影等',
    'meter_rhythm_basics':
        'meter 决定强弱位；教学对位用均分长时值，华丽风格可提高密度',
    'accompaniment_patterns':
        '对位第二声部≠配乐；普通主调可规划柱式/分解/阿尔贝蒂等织体角色',
}

_TRANSLATOR_HINTS: Dict[str, str] = {
    'species_first_two_part':
        'plan.species → new_score voices=2 instruments=["piano","piano"]；'
        '禁止 fugue；遵守注入的对位权威条文；音符常由确定性对位路径写',
    'cadence_types':
        '末段调用 cadence；PAC 用 authentic 收束；半终止感勿强行 cadence 到 I',
    'phrase_model_functions':
        'set_harmony 按 T–PD–D 排布罗马数字；段末导向 V 或 V→I',
    'common_progressions':
        'set_harmony(progression=[...]) 选用 ii–V–I / I–vi–IV–V 等教学进行',
    'satb_voice_leading':
        '四部时 voices=4 或 instruments 含 soprano..bass；旋律音区贴声部音域',
    'parallel_fifths_octaves':
        '勿人为写死外声部平行五八；finalize 会做基础修正（preserve 除外）',
    'phrase_period_sentence':
        'set_sections 按乐句切段；前句可弱收、末段加 cadence',
    'nonharmonic_tones':
        'melody 可用经过/邻音装饰；species 路径关闭装饰',
    'melody_contour':
        'melody(contour/rhythm/motif)；不要赋格/模仿时 narrative(appearances=false)',
    'meter_rhythm_basics':
        'new_score.meter 与节奏型一致；每小节时值须填满',
    'accompaniment_patterns':
        'accompaniment(pattern=sustained|alberti|block_chords|broken_arpeggio|…)；'
        '对位任务不要用配乐型盖过对位声部',
}


def rules_prompt_block(stage: str, *, include_scripts: bool = False) -> str:
    """生成注入 Planner / Translator 的规则摘要块。

    - planner：侧重何时置 species / 终止 / 乐句功能 / 曲式
    - translator：侧重选哪些脚本与参数落实规则
    - 默认不罗列全部内核脚本文档（translator 另有 kernel_docs）
    """
    stage = (stage or '').strip().lower()
    if stage not in ('planner', 'translator'):
        return ''

    hints = _PLANNER_HINTS if stage == 'planner' else _TRANSLATOR_HINTS
    manuals = list_rules(include_scripts=False)
    if not manuals and not include_scripts:
        return ''

    lines: List[str] = [
        '## 规则库摘要（必须遵守；细节见规则库管理器）',
        '',
    ]
    if stage == 'planner':
        lines += [
            '用途：把用户意图落到 plan 字段（species / form / harmony / texture 等）。',
            '不要 invent 音符；对位≠赋格。',
            '',
        ]
    else:
        lines += [
            '用途：选确定性脚本与参数落实下列规则；音符由内核写。',
            '完整脚本文档见下方「可用脚本」；此处只给规则→脚本对照。',
            '',
        ]

    # 按 catalog 分类顺序
    cat_order = [c['id'] for c in categories() if c['id'] != 'kernel_script']
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for r in manuals:
        by_cat.setdefault(r.get('category') or 'other', []).append(r)
    for cid in list(by_cat.keys()):
        if cid not in cat_order:
            cat_order.append(cid)

    cat_labels = {c['id']: c.get('label') or c['id'] for c in categories()}
    for cid in cat_order:
        rows = by_cat.get(cid) or []
        if not rows:
            continue
        lines.append(f'### {cat_labels.get(cid) or cid}')
        for r in rows:
            rid = r.get('id') or ''
            title = r.get('title') or rid
            summary = (r.get('summary') or '').strip()
            hint = (hints.get(rid) or '').strip()
            lines.append(f'- **{title}** (`{rid}`)')
            if summary:
                lines.append(f'  - 摘要：{summary}')
            if hint:
                lines.append(f'  - 本阶段：{hint}')
        lines.append('')

    if include_scripts:
        scripts = [r for r in list_rules(include_scripts=True)
                   if r.get('kind') == 'kernel_script']
        if scripts:
            lines.append('### 内核脚本（可调用名）')
            names = [r.get('script_name') or r['id'].replace('kernel_script_', '')
                     for r in scripts]
            lines.append('- ' + ', '.join(names))
            lines.append('')

    if stage == 'planner':
        lines += [
            '快速对照：',
            '- 1:1/第一类/对位法 → species=true（非赋格）',
            '- 赋格/卡农/创意曲 → want_fugue=true',
            '- 乐段问答 → 前弱后强终止（HC→PAC）',
            '- 主调织体 → texture=homophonic；对位第一类 → first-species',
        ]
    else:
        lines += [
            '快速对照：',
            '- species → 禁 fugue；钢琴双声部；对位权威条文优先',
            '- 收束 → cadence；戏剧弧 → narrative；收尾 → finalize',
            '- 和声骨架 → set_harmony；旋律 → melody；低音/伴奏 → accompaniment',
        ]
    return '\n'.join(lines).rstrip() + '\n'
