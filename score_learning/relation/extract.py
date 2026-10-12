# -*- coding: utf-8 -*-
"""从 Weimar Jazz Database 提取小节级主题与关系网络。

数据源：`scores/corpus/weimar-jazz/_source/wjazzd.db`（只读）
标注表：`sections`（type= FORM / PHRASE / IDEA / CHORUS / CHORD），共 58,560 行人工标注。

## 索引语义（务必先读）

`sections.start/end` **不是** `melody.eventid`，也**不是** `melody.bar`，
而是「该 melid 内按 eventid 排序后的 0-based 行偏移」。

误判会**静默返回空结果**：melid=3 的 eventid 范围是 1197-1569，
而标注范围是 0-372 —— 按 eventid join 得到「0 首可用」的假象。

## 输出的关系类型

层次（层级结构）：
    parent      段落（FORM）→ 乐句（PHRASE）→ 小节，三级 containment
    sibling     同级并置的段落（FORM 标签字母不同但同层）

横向（逻辑关联）：
    quote       引用：同字母的第二次出现（A2/A3 引用 A1）
    continue    承接：同字母相邻出现且中间无其他 FORM 段
    contrast    对比：字母族不同（Bvs A）
    cause       因果：终止式 → 下一段起始（由 CHORD 表的 || 边界佐证）
    unclear     无法确定（标注缺失 / 越界 / 单小节段）
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

# IDEA 词里嵌的编号前缀，如 7lick / 10lick / 3theme
_IDEA_NUM_RE = re.compile(r'^\d+')

DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'scores', 'corpus', 'weimar-jazz', '_source', 'wjazzd.db')

# FORM 标签的字母族：'A' / 'A2' / "A'2" →族 'A'，序号 2
def form_family(label: str) -> str:
    """'A1'->'A'  "A'2"->'A'  'B2'->'B'  'I1'->'I'  'C1'->'C'。"""
    s = str(label).strip().lstrip('+-=')
    return s[0].upper() if s and s[0].isalpha() else '?'


def form_index(label: str) -> Optional[int]:
    """'A1'->1  'A2'->2  'A'->None（无编号）。解析失败返回 None。"""
    digits = ''.join(ch for ch in str(label) if ch.isdigit())
    return int(digits) if digits else None


# IDEA 词表归类：把 615 个自由词映射到可读的主题类
IDEA_THEME = {
    'lick': '即兴乐句', 'line_w': 'Bebop 线', 'line_w_d': 'Bebop 线',
    'line_w_a': 'Bebop 线', 'line_a': 'AABA 线', 'line_d': 'D 线',
    'line_i_dd': '线条', 'line_t_alds': '主题线', 'line_t_asdl': '主题线',
    'line_cx': 'cx 线',
    'melody': '旋律陈述', 'expressive': '自由表情', 'fragment': '动机片段',
    'oscillation': ' oscillating 摆动',
    'rhythm_mi': '节奏型·切分', 'rhythm_mr': '节奏型·重复',
    'rhythm_si': '节奏型·突进', 'rhythm_sr': '节奏型·回旋',
    'void': '留白→起句',
    'theme': '主题引用',   # theme:t1 / theme:t3-4 = 引用第 1 / 3-4 主题
    'quote': '动机引用',   # quote:? = 引用动机
    'lick_blues': '布鲁斯lick',
}


def idea_theme(idea: str) -> str:
    """把原始 IDEA 词映射到主题类；# ~ * 前缀是修饰标记，保留在主题标签里。

    词表共 615 种，绝大多数是 `<类别><编号>` 形式（如 rhythm_mr / #7lick / theme:t1），
    因此按「剥修饰前缀 → 最长前缀匹配」归类，未命中者归「其他」并保留原词。
    """
    s = str(idea or '').strip()
    core = s.lstrip('#~*+-')
    # 部分词自带编号前缀：#7lick / #3lick / #10lick → 剥掉「数字+类别名」里的数字再匹配。
    # 注意不能对core 直接 rstrip('0123456789')：那会把 '10lick' 削成 '1'。
    core_key = _IDEA_NUM_RE.sub('', core, count=1)
    theme = None
    for k, v in sorted(IDEA_THEME.items(), key=lambda kv: -len(kv[0])):
        if core.startswith(k) or core_key.startswith(k):
            theme = v
            break
    if theme is None:
        theme = '其他'
    # 变体标记
    tags = []
    if '#' in s:
        tags.append('装饰')
    if '~' in s:
        tags.append('变形')
    if '*' in s:
        tags.append('强调')
    label = theme + (('·' + '/'.join(tags)) if tags else '')
    # theme:t1 → 主题引用(t1)；quote:? → 动机引用(?)
    if theme in ('主题引用', '动机引用') and ':' in core:
        label = f'{theme}({core.split(":", 1)[1]})'
    return label


def bar_theme(form_label: Optional[str], ideas: List[str],
              chorus: Optional[str]) -> Dict[str, Any]:
    """小节的主题描述：曲式角色 + 手法 + 声部归属。"""
    return {
        'form_label': form_label or '未标注',
        'form_family': form_family(form_label) if form_label else None,
        'form_index': form_index(form_label) if form_label else None,
        'idea_raw': ideas,
        'idea_themes': [idea_theme(i) for i in ideas],
        'chorus': chorus,
    }


class RelationExtractor:
    def __init__(self, db_path: str = DEFAULT_DB):
        if not os.path.exists(db_path):
            raise FileNotFoundError(f'标注库不存在：{db_path}')
        self.conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        self.db_path = db_path

    # ---------- 基础读取 ----------

    def _rows(self, mid: int) -> List[Tuple[int, int]]:
        """该solo 的 (eventid, bar) 行序列 —— 建立行偏移 → bar 号的映射。"""
        return list(self.conn.execute(
            'select eventid, bar from melody where melid=? order by eventid', (mid,)))

    def _meta(self, mid: int) -> Dict[str, Any]:
        cols = [d[1] for d in self.conn.execute('pragma table_info(solo_info)')]
        r = self.conn.execute('select * from solo_info where melid=?', (mid,)).fetchone()
        return dict(zip(cols, r)) if r else {}

    def _sections(self, mid: int, stype: str) -> List[Tuple[int, int, str]]:
        return list(self.conn.execute(
            'select start, end, value from sections where type=? and melid=? order by start',
            (stype, mid)))

    # ---------- 主体：小节 → 标签 ----------

    def extract_solo(self, mid: int) -> Optional[Dict[str, Any]]:
        """提取单首的全部小节标签与段结构。无法映射时返回 None。"""
        rows = self._rows(mid)
        if not rows:
            return None
        bar_of = {i: b for i, (e, b) in enumerate(rows)}     # 行偏移 → bar
        nbar = max(bar_of.values()) + 1

        # --- FORM 段 ---
        form_segs: List[Dict[str, Any]] = []
        form_err = 0
        for s, e, v in self._sections(mid, 'FORM'):
            if s not in bar_of or e not in bar_of:
                form_err += 1
                continue
            bs = bar_of[s]
            be = min(bar_of[e], nbar - 1)
            form_segs.append({'bar_from': bs, 'bar_to': max(bs, be), 'label': v,
                              'family': form_family(v), 'index': form_index(v)})
        if not form_segs:
            return None

        # --- PHRASE 段（乐句，嵌在 FORM 内）---
        phrase_segs: List[Dict[str, Any]] = []
        for s, e, v in self._sections(mid, 'PHRASE'):
            if s not in bar_of or e not in bar_of:
                continue
            bs = bar_of[s]
            be = min(bar_of[e], nbar - 1)
            phrase_segs.append({'bar_from': bs, 'bar_to': max(bs, be), 'phrase_no': v})

        # --- IDEA（按行偏移→ bar，覆盖区间）---
        idea_by_bar: Dict[int, List[str]] = defaultdict(list)
        for s, e, v in self._sections(mid, 'IDEA'):
            if s not in bar_of or e not in bar_of:
                continue
            for b in range(bar_of[s], min(bar_of[e], nbar - 1) + 1):
                idea_by_bar[b].append(v)

        # --- CHORUS（副歌标记）---
        chorus_by_bar: Dict[int, str] = {}
        for s, e, v in self._sections(mid, 'CHORUS'):
            if s not in bar_of or e not in bar_of:
                continue
            for b in range(bar_of[s], min(bar_of[e], nbar - 1) + 1):
                chorus_by_bar[b] = v

        # --- 逐小节展开 ---
        form_at: Dict[int, str] = {}
        for seg in form_segs:
            for b in range(seg['bar_from'], seg['bar_to'] + 1):
                form_at[b] = seg['label']
        phrase_at: Dict[int, str] = {}
        for seg in phrase_segs:
            for b in range(seg['bar_from'], seg['bar_to'] + 1):
                phrase_at[b] = seg['phrase_no']

        meta = self._meta(mid)
        bars: List[Dict[str, Any]] = []
        for b in range(nbar):
            labs = idea_by_bar.get(b, [])
            bars.append({
                'bar_id': f'{mid:04d}-b{b + 1:03d}',       # 全局唯一：melid + 小节号
                'bar_no': b + 1,
                'theme': bar_theme(form_at.get(b), labs, chorus_by_bar.get(b)),
                'phrase_no': phrase_at.get(b),
                'form_label': form_at.get(b),
            })

        # --- 未标注小节（标注覆盖不到的前导/尾声）---
        for bar in bars:
            if bar['form_label'] is None:
                bar['theme']['form_label'] = '未标注'

        return {
            'melid': mid,
            'title': meta.get('title') or f'melid_{mid}',
            'titleaddon': meta.get('titleaddon') or '',
            'performer': meta.get('performer') or '',
            'style': meta.get('style') or '',
            'instrument': meta.get('instrument') or '',
            'key': meta.get('key') or '',
            'signature': meta.get('signature') or '',
            'avgtempo': meta.get('avgtempo'),
            'form_harmony': meta.get('chord_changes') or '',
            'nbar': nbar,
            'form_segs': form_segs,
            'phrase_segs': phrase_segs,
            'bars': bars,
            'unmapped_form_rows': form_err,
        }

    # ---------- 关系推导 ----------

    def relations(self, solo: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 FORM 段序列推导横向逻辑关系。返回边列表。"""
        segs = solo['form_segs']
        rels: List[Dict[str, Any]] = []
        by_letter: Dict[str, List[Dict]] = defaultdict(list)
        for i, s in enumerate(segs):
            by_letter[s['family']].append({**s, 'seq': i})

        for i, s in enumerate(segs):
            cur = s
            prev = segs[i - 1] if i else None
            nxt = segs[i + 1] if i + 1 < len(segs) else None
            label = cur['label']

            # 首次出现（A1 / B1）→ 引入
            same = by_letter[cur['family']]
            first = same[0]
            if i > first['seq']:
                src = segs[first['seq']]
                # 引用 or 承接：看中间隔了什么
                gap = [g['family'] for g in
                       segs[first['seq'] + 1: i]]
                interrupted = any(g != cur['family'] for g in gap)
                rels.append({
                    'src_bar_range': [src['bar_from'] + 1, src['bar_to'] + 1],
                    'src_label': src['label'],
                    'rel': 'quote' if interrupted else 'continue',
                    'tgt_bar_range': [cur['bar_from'] + 1, cur['bar_to'] + 1],
                    'tgt_label': label,
                    'confidence': 'high' if interrupted else 'high',
                    'evidence': f'同为{cur["family"]}族；'
                                + ('中间插入 ' + '/'.join(gap) if interrupted else '紧邻再现'),
                })
            else:
                rels.append({
                    'src_bar_range': [cur['bar_from'] + 1, cur['bar_to'] + 1],
                    'src_label': label,
                    'rel': 'introduce',
                    'tgt_bar_range': None, 'tgt_label': None,
                    'confidence': 'high',
                    'evidence': f'{cur["family"]}族首次出现（引入段）',
                })

            # 对比：与前一段字母族不同
            if prev and prev['family'] != cur['family']:
                rels.append({
                    'src_bar_range': [prev['bar_from'] + 1, prev['bar_to'] + 1],
                    'src_label': prev['label'],
                    'rel': 'contrast',
                    'tgt_bar_range': [cur['bar_from'] + 1, cur['bar_to'] + 1],
                    'tgt_label': label,
                    'confidence': 'high',
                    'evidence': f'字母族 {prev["family"]}→{cur["family"]}，'
                                f'和声配置改变（chord_changes 有独立和声行）',
                })

            # 递进：同族序号递增（A1→A2→A3）
            if prev and prev['family'] == cur['family'] and \
                    (cur['index'] or 0) > (prev['index'] or 0):
                rels.append({
                    'src_bar_range': [prev['bar_from'] + 1, prev['bar_to'] + 1],
                    'src_label': prev['label'],
                    'rel': 'sequence',
                    'tgt_bar_range': [cur['bar_from'] + 1, cur['bar_to'] + 1],
                    'tgt_label': label,
                    'confidence': 'high',
                    'evidence': f'同族序号递进 {prev["label"]}→{label}'
                                '（同材料变体，非原样重复）',
                })

            # 因果：段落结束 → 下一段开始（终止式在 form_harmony 的 || 处）
            if nxt:
                rels.append({
                    'src_bar_range': [cur['bar_from'] + 1, cur['bar_to'] + 1],
                    'src_label': label,
                    'rel': 'cause',
                    'tgt_bar_range': [nxt['bar_from'] + 1, nxt['bar_to'] + 1],
                    'tgt_label': nxt['label'],
                    'confidence': 'medium' if solo['form_harmony'] else 'low',
                    'evidence': '段落终止处（form_harmony 以 || 标记）→ 下一段起始；'
                                + ('有终止式和声记录' if solo['form_harmony']
                                   else '无 form_harmony 佐证，仅按相邻推断'),
                })
        # 未标注小节：层级归属与横向关系均无法判定，显式记为 unclear
        unannotated = [b['bar_no'] for b in solo['bars']
                       if b['theme']['form_label'] == '未标注']
        for b in solo['bars']:
            if b['bar_no'] in unannotated:
                rels.append({
                    'src_bar_range': [b['bar_no'], b['bar_no']],
                    'src_label': '未标注',
                    'rel': 'unclear',
                    'tgt_bar_range': None, 'tgt_label': None,
                    'confidence': 'none',
                    'evidence': f"第{b['bar_no']}小节不在任何 FORM 段范围内"
                                '（前导/间奏/尾声），层级归属与逻辑关系均不可判定',
                })
        return rels

    def hierarchy(self, solo: Dict[str, Any]) -> List[Dict[str, Any]]:
        """层级结构：段落（FORM）→ 乐句（PHRASE）→ 小节。

        注意：原始 PHRASE 标注会跨 FORM 边界（同一乐句含两种form 标签的音符），
        因此按**中点归属**唯一分配给一个 FORM 段，避免同一乐句被挂到两个父节点下。
        """
        out: List[Dict[str, Any]] = []
        mid_tag = solo['melid']
        for seg in solo['form_segs']:
            lo, hi = seg['bar_from'], seg['bar_to']
            kids: List[Dict[str, Any]] = []
            for k in solo['phrase_segs']:
                pk_lo, pk_hi = k['bar_from'], k['bar_to']
                # 中点落在本段内 → 归属本段
                if lo <= (pk_lo + pk_hi) / 2 <= hi:
                    # PHRASE 标注可能越过最后一个小节（收尾音符），钳到合法范围
                    nb = solo['nbar']
                    pk_lo_c = max(0, min(pk_lo, nb - 1))
                    pk_hi_c = max(pk_lo_c, min(pk_hi, nb - 1))
                    seg_of_bar = {b: s['label']
                                   for s in solo['form_segs']
                                   for b in range(s['bar_from'], s['bar_to'] + 1)}
                    kids.append({
                        'node_id': f"{mid_tag}-ph{k['phrase_no']}",
                        'kind': 'PHRASE', 'label': f"乐句{k['phrase_no']}",
                        'bar_range': [pk_lo + 1, pk_hi + 1],
                        'clamped': (pk_lo_c, pk_hi_c) != (pk_lo, pk_hi),
                        'form_mix': sorted({seg_of_bar.get(b, '未标注')
                                            for b in range(pk_lo_c, pk_hi_c + 1)}),
                        'parent': f"{mid_tag}-seg[{seg['label']}]",
                        'children': [
                            {'node_id': f"{mid_tag}-b{b + 1:03d}",
                             'kind': 'BAR', 'label': f"第{b + 1}小节",
                             'bar_range': [b + 1, b + 1],
                             'theme': solo['bars'][b]['theme'],
                             'parent': f"{mid_tag}-ph{k['phrase_no']}"}
                            for b in range(pk_lo_c, pk_hi_c + 1)
                        ]})
            out.append({
                'level': 1,
                'node_id': f"{mid_tag}-seg[{seg['label']}]",
                'kind': 'FORM',
                'label': seg['label'],
                'bar_range': [lo + 1, hi + 1],
                'parent': None,
                'children': kids,
            })
        return out


# ---------------------------------------------------------------- CLI

REL_ZH = {'introduce': '引入', 'quote': '引用', 'continue': '承接',
          'contrast': '对比', 'sequence': '递进', 'cause': '因果', 'unclear': '不确定'}


def render_markdown(results: List[Dict[str, Any]], limit: int = 12) -> str:
    L: List[str] = ['# 小节关系提取结果（Weimar Jazz Database 人工标注）', '']
    tot_bars = sum(r['solo']['nbar'] for r in results)
    allrels = Counter(x['rel'] for r in results for x in r['relations'])
    L += [f'- 曲目数：**{len(results)}**', f'- 小节总数：**{tot_bars}**',
          f'- 关系边总数：**{sum(allrels.values())}**', '',
          '## 关系类型分布', '',
          '| 关系类型 | 边数 | 含义 |', '|---|---|---|']
    meaning = {'introduce': '该段首次出现（引入段）', 'quote': '同族材料的再现，中间隔其他段',
               'continue': '同族材料紧邻再现（承接）', 'contrast': '字母族改变，和声配置随之改变',
               'sequence': '同族序号递进（同材料变体）', 'cause': '段落终止 → 下一段起始'}
    for k, n in allrels.most_common():
        L.append(f'| {REL_ZH.get(k, k)}（{k}） | {n} | {meaning.get(k, "")} |')
    L += ['', '---', '']

    for r in results[:limit]:
        s = r['solo']
        title = f"{s['title']} {s['titleaddon']}".strip()
        L += [f"## {s['melid']:04d} — {title}",
              f"*{s['performer']} · {s['style']} · {s['instrument']} · "
              f"{s['key']} · {s['signature']} · {s['avgtempo']}bpm · {s['nbar']}小节*", '',
              '### 层级结构（段落 → 乐句 → 小节）', '',
              '```']
        for node in r['hierarchy']:
            L.append(f"{node['label']}  [小节 {node['bar_range'][0]}-{node['bar_range'][1]}]")
            for ph in node['children']:
                themes = Counter(b['theme']['form_label'] for b in ph['children'])
                th = Counter()
                for b in ph['children']:
                    th.update(b['theme']['idea_themes'])
                ths = '、'.join(f'{k}×{v}' for k, v in th.most_common(3)) or '无标注手法'
                L.append(f"  └ {ph['label']}  [小节 {ph['bar_range'][0]}-{ph['bar_range'][1]}]"
                         f"  手法: {ths}")
                for b in ph['children']:
                    t = b['theme']
                    idea = '、'.join(t['idea_themes'][:2]) or '—'
                    L.append(f"      · {b['label']}  {t['form_label']}  {idea}")
        L += ['```', '', '### 关系表', '',
              '| 源小节 | 源段 | 关系类型 | 目标小节 | 目标段 | 置信度 | 依据 |',
              '|---|---|---|---|---|---|---|']
        for x in r['relations']:
            s0 = f"{x['src_bar_range'][0]}-{x['src_bar_range'][1]}" if x['src_bar_range'] else '—'
            t0 = (f"{x['tgt_bar_range'][0]}-{x['tgt_bar_range'][1]}"
                  if x['tgt_bar_range'] else '—')
            L.append(f"| {s0} | {x['src_label']} | **{REL_ZH.get(x['rel'], x['rel'])}**"
                     f"（{x['rel']}） | {t0} | {x['tgt_label'] or '—'} | "
                     f"{x['confidence']} | {x['evidence']} |")
        # 未标注小节
        un = [b['bar_no'] for b in s['bars']
              if b['theme']['form_label'] == '未标注']
        if un:
            rng = _ranges(un)
            L += ['', f'>⚠️ **未标注小节**（{len(un)} 个）：第 ' +
                  '、'.join(rng) + ' 小节 —— 位于FORM 段之外，'
                  '无法判定层级归属与横向关系。']
        L.append('')
    if len(results) > limit:
        L.append(f'（仅展示前 {limit} 首，完整结果见 JSON）')
    return '\n'.join(L)


def _ranges(nums: List[int]) -> List[str]:
    out, s, p = [], None, None
    for n in nums:
        if s is None:
            s = p = n
        elif n == p + 1:
            p = n
        else:
            out.append(f'{s}' if s == p else f'{s}-{p}'); s = p = n
    if s is not None:
        out.append(f'{s}' if s == p else f'{s}-{p}')
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=DEFAULT_DB)
    ap.add_argument('--limit', type=int, default=60)
    ap.add_argument('--out', default='runs/relation')
    a = ap.parse_args()

    ex = RelationExtractor(a.db)
    mids = [r[0] for r in ex.conn.execute(
        "select distinct melid from sections where type='FORM' order by melid")]

    results, skipped = [], []
    for mid in mids:
        s = ex.extract_solo(mid)
        if s is None:
            skipped.append((mid, '行偏移无法映射为小节'))
            continue
        results.append({'solo': s, 'relations': ex.relations(s),
                        'hierarchy': ex.hierarchy(s)})
        if len(results) >= a.limit:
            break

    os.makedirs(a.out, exist_ok=True)
    payload = {'source': os.path.basename(a.db),
               'index_semantics': 'sections.start/end = melid 内按 eventid 排序的 0-based 行偏移',
               'n_solos': len(results), 'skipped': skipped,
               'results': results}

    # 主文件：关系 + 段落层汇总（去掉逐小节 children，否则单文件 37MB）
    compact = {'source': payload['source'],
               'index_semantics': payload['index_semantics'],
               'n_solos': payload['n_solos'],
               'results': [{'solo': r['solo'], 'relations': r['relations'],
                            'hierarchy': [{**{k: v for k, v in h.items()
                                              if k != 'children'},
                                           'n_phrase': len(h['children'])}
                                          for h in r['hierarchy']]}
                           for r in results]}
    with open(os.path.join(a.out, 'bar_relations.json'), 'w', encoding='utf-8') as f:
        json.dump(compact, f, ensure_ascii=False, separators=(',', ':'))

    # 完整层级树（含逐小节主题）单独 gzip
    hier = {'index_semantics': payload['index_semantics'],
            'results': [{'melid': r['solo']['melid'], 'title': r['solo']['title'],
                        'hierarchy': r['hierarchy']} for r in results]}
    with gzip.open(os.path.join(a.out, 'hierarchy.json.gz'), 'wt', encoding='utf-8') as f:
        json.dump(hier, f, ensure_ascii=False, separators=(',', ':'))

    # 汇总量（供报告与下游模型直接读取）
    st = {'n_solos': len(results),
          'n_bars': sum(r['solo']['nbar'] for r in results),
          'n_edges': sum(len(r['relations']) for r in results),
          'rel': dict(Counter(x['rel'] for r in results for x in r['relations'])),
          'conf': dict(Counter(x['confidence'] for r in results
                               for x in r['relations'])),
          'styles': dict(Counter(r['solo']['style'] for r in results).most_common()),
          'unannotated': sum(1 for r in results for b in r['solo']['bars']
                             if b['theme']['form_label'] == '未标注')}
    with open(os.path.join(a.out, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=1)

    with open(os.path.join(a.out, 'bar_relations.md'), 'w', encoding='utf-8') as f:
        f.write(render_markdown(results))

    print(f'提取 {st["n_solos"]} 首 / {st["n_bars"]} 小节 / {st["n_edges"]} 条关系')
    print(f'  JSON     → {a.out}/bar_relations.json')
    print(f'  层级树   → {a.out}/hierarchy.json.gz')
    print(f'  汇总     → {a.out}/summary.json')
    print(f'  报告     → {a.out}/bar_relations.md')
    if skipped:
        print(f'  跳过 {len(skipped)} 首')


if __name__ == '__main__':
    main()
