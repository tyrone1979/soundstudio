# -*- coding: utf-8 -*-
"""把编译好的 `form_plan` 注入到各风格包的 grammar.yaml。

## 为什么要注入而不是运行时读

`plan_form`（compose.py:206）只从 `script['subs'][sub]['form']` 取曲式信息。
把 form_plan 写进 yaml 有三个好处：

1. `compose_kernel._s_leadsheet_grammar` 只`lscript.load(style)` 一次即可，
   无需改动 kernel 或 learn 的调用链
2. 规则脚本是项目既有的「规则 = 可读数据」范式，人能直接review
3. `check.py` / 报告链路能自动看到新字段

## 用法

    venv/bin/python -m score_learning.relation.inject_form_plan \
        --plan runs/relation/form_plan.json \
        --packs skills/composition/genre/packs

体裁名（`form_plan.policies` 的键）到包的映射见 `STYLE_ALIASES`。
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional

import yaml

# Weimar 体裁（solo_info.style）→ (包目录, 子风格名)。
# 实测：jazz 的 6 个体裁全在同一个 pack `jazz/swing-standard` 里，各占一个 sub；
# 古典/民谣包的 sub 命名完全不同（allegro/minuet/…），故只映射能对上的。
STYLE_ALIASES = {
    'SWING':('jazz/swing-standard', 'swing'),
    'BEBOP':        ('jazz/swing-standard', 'bebop'),
    'COOL':         ('jazz/swing-standard', 'cool'),
    'HARDBOP':      ('jazz/swing-standard', 'hardbop'),
    'POSTBOP':      ('jazz/swing-standard', 'postbop'),
    'TRADITIONAL':  ('jazz/swing-standard', 'traditional'),
}


def inject(plan_path: str, packs_root: str, backup: bool = True,
           only: Optional[List[str]] = None) -> Dict[str, Any]:
    with open(plan_path, encoding='utf-8') as f:
        plan = json.load(f)
    policies = plan.get('policies') or {}
    report: Dict[str, Any] = {'injected': [], 'skipped': [], 'no_match': []}

    # 同一 pack 可能被多个体裁写入（6 个爵士体裁都在 swing-standard），
    # 因此按 pack 聚合，最后一次性写盘，避免互相覆盖。
    by_pack: Dict[str, Dict[str, Any]] = {}

    for style, (rel, sub) in STYLE_ALIASES.items():
        if only and style not in only:
            continue
        pol = policies.get(style)
        if not pol:
            report['no_match'].append(style)
            continue
        yml = os.path.join(packs_root, rel, 'grammar.yaml')
        if not os.path.exists(yml):
            report['no_match'].append(f'{rel}（无 grammar.yaml）')
            continue
        slot = by_pack.get(rel)
        if slot is None:
            with open(yml, encoding='utf-8') as f:
                slot = {'data': yaml.safe_load(f)}
                by_pack[rel] = slot
        subs = slot['data'].get('subs') or {}
        if sub not in subs:
            report['skipped'].append(f'{rel}: 没有子风格 {sub}')
            continue
        fp = {
            'unit_bars': plan.get('unit_bars', 8),
            'source': f'Weimar Jazz 人工段落标注 / {style}',
            'note': plan.get('note'),
            'policies': {str(k): v for k, v in pol.items()},
            'segment_len': {kk.split('|', 1)[1]: vv
                            for kk, vv in (plan.get('segment_len') or {}).items()
                            if kk.startswith(style + '|')},
            'role_dist': (plan.get('role_dist') or {}).get(style),
            'idea_by_role': plan.get('idea_by_role'),
        }
        subs[sub].setdefault('form', {})['form_plan'] = fp
        report['injected'].append(f'{rel}/{sub} ← {style}（{len(pol)} 种段数）')

    for rel, slot in by_pack.items():
        yml = os.path.join(packs_root, rel, 'grammar.yaml')
        if backup and not os.path.exists(yml + '.bak'):
            with open(yml + '.bak', 'w', encoding='utf-8') as f:
                yaml.safe_dump(slot['data'], f, allow_unicode=True, sort_keys=False)
        with open(yml, 'w', encoding='utf-8') as f:
            yaml.safe_dump(slot['data'], f, allow_unicode=True, sort_keys=False)
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--plan', default='runs/relation/form_plan.json')
    ap.add_argument('--packs', default='skills/composition/genre/packs')
    ap.add_argument('--only', nargs='*', help='只注入指定体裁，如 SWING COOL')
    ap.add_argument('--no-backup', action='store_true')
    a = ap.parse_args()
    rep = inject(a.plan, a.packs, backup=not a.no_backup, only=a.only)
    print(f"注入 {len(rep['injected'])} 个包：")
    for x in rep['injected']:
        print('  ✓', x)
    for x in rep['skipped']:
        print('  !', x)
    for x in rep['no_match']:
        print('  ×', x, '（form_plan 里没有这个体裁）')
