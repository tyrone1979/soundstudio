# -*- coding: utf-8 -*-
"""把「小节关系」编译成 grammar.yaml 可消费的曲式规则。

## 为什么需要这一步

现有 `form.templates` 只是一串字母（如 `['AABA', 0.4286, 24]`），
`plan_form`（compose.py:206）从这些串里挑一个，但**不知道**：

- 每段多长（恒为 8 小节UNIT）
- `A2` 是`A1` 的引用还是变体
- 哪一段是「引入」、哪一段是「终止」
- 段落间的和声配置差异

这些信息在 Weimar 的人工标注里都有（`runs/relation/bar_relations.json`），
本模块把它编译成 `form.form_plan` 字段，让 `plan_form` 能生成**带关系标注**的
段落序列。

## 产物

    form.form_plan = {
      'unit_bars': 8,
      'n_segments': 2,
      'policies': [                # 按 n_segments 索引的段落序列模板
        {'form':'A1A2B1A2', 'p':0.43,'n':24,
         'segments': [
           {'label':'A1','role':'introduce','bars':8,
            'ref':None,'idea':'主题引用(t1)'},
           {'label':'A2','role':'sequence','bars':8,
            'ref':'A1','idea':'即兴乐句·变形'},   # ← 引用 A1
           ...
         ],'relations':[{'rel':'sequence','from':'A1','to':'A2'}, ...]
        }, ...
      ],
      'segment_len': {...},# 段落长度分布
      'role_dist': {...},        # 各角色占比
      'idea_by_role': {...}      # 每角色的典型手法
    }

`plan_form` 优先读`form_plan`；读不到则退回原有 `templates` 逻辑（向后兼容）。
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

# 段落角色（由关系类型推导）
ROLE_INTRODUCE = 'introduce'# 该族首次出现
ROLE_SEQUENCE = 'sequence'      # 同族序号递进（A1→A2）
ROLE_QUOTE = 'quote'            # 同族材料再现（引用首次出现）
ROLE_CONTRAST = 'contrast'      # 字母族改变
ROLE_CODA = 'coda'              # 末段（最后一个族且索引>1）

# 角色 → 关系类型中文名（供报告）
ROLE_ZH = {'introduce': '引入', 'sequence': '递进变体', 'quote': '引用',
           'contrast': '对比', 'plain': '延续', 'coda': '尾声'}


def _seg_role(fam: str, idx: int, first_pos: int, seq_pos: int,
              last_pos: int) -> str:
    """由「字母族/序号/位置」决定段落角色。"""
    if first_pos == seq_pos:
        return ROLE_INTRODUCE
    if seq_pos == last_pos and idx and idx > 1:
        return ROLE_CODA
    if idx and idx > 1:
        return ROLE_SEQUENCE
    return ROLE_QUOTE


def compile_solo(solo: Dict[str, Any], relations: List[Dict[str, Any]],
                 unit_bars: int = 8) -> Optional[Dict[str, Any]]:
    """把一首曲子的 FORM 段 + 关系边编译成一个段落序列模板。"""
    segs = solo['form_segs']
    if len(segs) < 2:
        return None
    bars = solo['bars']

    # 字母族 → 首次出现位置
    first_pos: Dict[str, int] = {}
    for i, s in enumerate(segs):
        first_pos.setdefault(s['family'], i)
    last_pos = len(segs) - 1

    # 段内小节 → 主题（取该段覆盖小节里最常见的手法）
    idea_by_bar: Dict[int, List[str]] = defaultdict(list)
    for b in bars:
        if b['form_label']:
            idea_by_bar[b['bar_no']].extend(b['theme']['idea_themes'])

    out_segs: List[Dict[str, Any]] = []
    for i, s in enumerate(segs):
        span = range(s['bar_from'] + 1, s['bar_to'] + 2)
        ideas = [x for b in span for x in idea_by_bar.get(b, [])]
        idea = Counter(ideas).most_common(1)[0][0] if ideas else None
        role = _seg_role(s['family'], s['index'], first_pos[s['family']], i, last_pos)
        out_segs.append({
            'label': s['label'],
            'family': s['family'],
            'index': s['index'],
            'role': role,
            'role_zh': ROLE_ZH.get(role, role),
            # 真实长度（人工标注），而不是恒定 8
            'bars': s['bar_to'] - s['bar_from'] + 1,
            'ref': None,                # 下面填
            'idea': idea,
        })

    # 填 ref：该段引用谁。**排除自引用**（同名标签在多段中重复出现，
    # 例如 A1 既是首次出现也是后续段的标签名，不能把 ref 指向自己）
    for x in relations:
        if x['rel'] in ('quote', 'sequence', 'continue') and x['tgt_label']:
            for seg in out_segs:
                if seg['label'] != x['tgt_label']:
                    continue
                if x['src_label'] == seg['label']:
                    continue                      # ← 自引用，跳过
                seg['ref'] = x['src_label']
                break

    # 段间关系（相邻）
    rels = []
    for i in range(1, len(out_segs)):
        a, b_ = out_segs[i - 1], out_segs[i]
        rels.append({
            'rel': 'contrast' if a['family'] != b_['family']
                   else ('sequence' if (b_['index'] or 0) > (a['index'] or 0)
                         else 'quote'),
            'from': a['label'], 'to': b_['label'],
            'zh': {'contrast': '对比', 'sequence': '递进', 'quote': '引用'}[
                'contrast' if a['family'] != b_['family']
                else ('sequence' if (b_['index'] or 0) > (a['index'] or 0)
                      else 'quote')],
        })

    return {
        'form': ''.join(s['label'] for s in out_segs),
        'n_segments': len(out_segs),
        'bars': out_segs[-1]['bar_from_end'] if 'bar_from_end' in out_segs[-1]
                else sum(s['bars'] for s in out_segs),
        'segments': out_segs,
        'relations': rels,
    }


def compile_corpus(relations_json: str, style_map: Optional[Dict[str, str]] = None,
                   min_n: int = 4, max_n: int = 12,
                   unit_bars: int = 8) -> Dict[str, Any]:
    """从全量关系结果编译出按 (体裁, 段数) 聚合的 form_plan。

    只保留 `min_n` 例以上的模式（避免长尾噪声），段数上限 `max_n`。
    """
    with open(relations_json, encoding='utf-8') as f:
        data = json.load(f)
    R = data['results']

    # (style, n_seg) -> Counter(form_string)
    buckets: Dict[Tuple[str, int], Counter] = defaultdict(Counter)
    # (style, n_seg) -> form -> Counter(段落签名 -> 次数)
    variants: Dict[Tuple[str, int], Dict[str, Counter]] = defaultdict(
        lambda: defaultdict(Counter))
    detail_by_sig: Dict[Tuple[str, int], Dict[Any, Any]] = defaultdict(dict)
    # 段落长度分布
    len_dist: Dict[Tuple[str, int], Counter] = defaultdict(Counter)
    role_dist: Dict[str, Counter] = defaultdict(Counter)
    idea_by_role: Dict[str, Counter] = defaultdict(Counter)

    for r in R:
        solo = r['solo']
        style = solo['style'] or 'UNKNOWN'
        plan = compile_solo(solo, r['relations'], unit_bars)
        if not plan:
            continue
        n = plan['n_segments']
        if not (min_n <= n <= max_n):
            continue
        key = (style, n)
        form = plan['form']
        buckets[key][form] += 1
        # 逐模式的段落细节：取**首个**实例（同一 form 字符串可能来自不同曲子，
        # 段落长度不同，故用 (form, 长度元组) 归并，报告时展示最常见的长度组合）
        sig = tuple((s['label'], s['role'], s['bars']) for s in plan['segments'])
        variants[key].setdefault(form, Counter())[sig] += 1
        detail_by_sig[key][sig] = plan
        for seg in plan['segments']:
            role_dist[style][seg['role']] += 1
            if seg['idea']:
                idea_by_role[seg['role']][seg['idea']] += 1
            len_dist[key][seg['bars']] += 1

    # 组装 policies
    policies: Dict[str, Dict[int, List[Dict[str, Any]]]] = defaultdict(dict)
    for (style, n), cnt in sorted(buckets.items()):
        tot = sum(cnt.values())
        items = []
        for form, k in cnt.most_common(8):
            # 取该 form 下最常见的段落长度组合
            sigs = variants[(style, n)].get(form) or Counter()
            plan = None
            if sigs:
                best_sig = sigs.most_common(1)[0][0]
                plan = detail_by_sig[(style, n)].get(best_sig)
            items.append({
                'form': form, 'p': round(k / tot, 4), 'n': k,
                'segments': plan['segments'] if plan else [],
                'relations': plan['relations'] if plan else [],
                'n_variants': len(sigs),
            })
        policies[style][n] = items

    return {
        'unit_bars': unit_bars,
        'note': ('段落长度/角色/引用关系来自 Weimar Jazz人工标注；'
                 'form 字符串为人工 FORM 标签拼接（可能是 A1A2B1A2 而非 AABA）'),
        'policies': {k: dict(v) for k, v in policies.items()},
        'segment_len': {f'{s}|{n}': dict(c)
                        for (s, n), c in sorted(len_dist.items())},
        'role_dist': {k: dict(v.most_common()) for k, v in role_dist.items()},
        'idea_by_role': {k: dict(v.most_common(6)) for k, v in idea_by_role.items()},
    }


def summary_lines(plan: Dict[str, Any], style: str, top: int = 3) -> List[str]:
    """给报告用的人读摘要。"""
    L: List[str] = []
    pol = plan.get('policies', {}).get(style) or {}
    if not pol:
        return [f'- {style}：无满足段数条件的模式']
    for n in sorted(pol)[:top]:
        for it in pol[n][:2]:
            segs = ' → '.join(
                f"{s['label']}({s['role_zh']}"
                f"{'←' + s['ref'] if s.get('ref') else ''}"
                f",{s['bars']}小节)" for s in it['segments'][:8])
            L.append(f'- {style} {n} 段 (p={it["p"]:.0%}, n={it["n"]}): {segs}')
    return L


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--relations', default='runs/relation/bar_relations.json')
    ap.add_argument('--out', default='runs/relation/form_plan.json')
    a = ap.parse_args()
    plan = compile_corpus(a.relations)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    print(f'编译完成 → {a.out}')
    print(f'  体裁: {", ".join(plan["policies"])}')
    for st in list(plan['policies'])[:3]:
        for ln in summary_lines(plan, st):
            print('  ' + ln)
