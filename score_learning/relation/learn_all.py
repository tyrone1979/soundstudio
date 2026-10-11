# -*- coding: utf-8 -*-
"""统一学习入口：把三条学习流水线跑一遍，编译成统一规则入库。

## 三条流水线

| # | 流水线 | 入口 | 产出 |
|---|---|---|---|
| 1 | **chorale**（巴赫众赞歌） | `grammar.learn.learn(pieces)` | `sacred/chorale/grammar.yaml` |
| 2 | **leadsheet**（旋律+和弦，8 体裁） | `leadsheet.learn.learn(pieces, style)` | 8 个风格包的 `grammar.yaml` |
| 3 | **关系**（人工段落标注） | `relation.extract` → `compile_form_plan` → `inject_form_plan` | 各包的 `form.form_plan` |

第3 条是前两者的补充：前两者产出「和弦/旋律/节奏规则」，第 3 条产出
「段落结构 + 段间关系（引用/承接/对比/递进）」，正好是 `plan_form` 缺的���。

## 为什么要有这个编排层

- 原来 `/learn-grammar chorale` 与 `/learn-grammar jazz` 是分开跑的，
  关系学习更是完全独立（本轮才补上），容易出现「规则学了但form_plan 没更新」
- 统一入口保证**每次全量学习后三条流水线同步入库**，
  `/compose-grammar` 读到的grammar.yaml 一定是完整的
- 各阶段可单独跳过（`--skip chorale` 等），便于增量补某一条

## 用法

```bash
# 全量（chorale + 8 体裁 leadsheet + 关系）
venv/bin/python -m score_learning.relation.learn_all

# 只跑关系（补 form_plan）
venv/bin/python -m score_learning.relation.learn_all --skip chorale --skip leadsheet

# 只跑爵士的 leadsheet
venv/bin/python -m score_learning.relation.learn_all \
    --skip chorale --skip relation --style jazz

# 干跑（只看会发生什么，不写盘）
venv/bin/python -m score_learning.relation.learn_all --dry-run
```
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import traceback
from contextlib import redirect_stdout
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

LEADSHOT_STYLES = ['folk', 'jazz', 'pop', 'madrigal',
                   'sonata', 'strings', 'symphony', 'chopin']


# ---------------------------------------------------------------- 工具
def _fmt(n: Any) -> str:
    """大数字加千分位，None 显示 -。"""
    if n is None:
        return '-'
    if isinstance(n, int) and n >= 1000:
        return f'{n:,}'
    return str(n)


def _fmt_pct(x: Optional[float], digits: int = 4) -> str:
    return '-' if x is None else f'{x:.{digits}f}'


class Stage:
    """一个学习阶段的执行与计时。"""

    def __init__(self, name: str, desc: str):
        self.name, self.desc = name, desc
        self.detail: Dict[str, Any] = {}
        self.ok: Optional[bool] = None
        self.secs = 0.0
        self.error = ''

    def as_dict(self) -> Dict[str, Any]:
        return {'stage': self.name, 'desc': self.desc, 'ok': self.ok,
                'secs': round(self.secs, 2), 'detail': self.detail,
                'error': self.error}


# ---------------------------------------------------------------- 阶段 1 chorale
def run_chorale(dry: bool = False, smoother: str = 'wb') -> Stage:
    """``smoother`` 选拍层和弦转移的估计器，默认 ``wb`` 保持历史产出不变。

    10 折GroupKFold 实测（``docs/实验重设计与消融_2026-10-04.md`` E1，major调式、
    留出困惑度）：``wb`` 13.46 / ``jm`` **12.68** / ``ad`` 14.01 / ``kn`` 14.40。
    ``jm``（Jelinek-Mercer）最优，可改用 ``--smoother jm`` 切换。
    """
    st = Stage('chorale', '巴赫众赞歌 → sacred/chorale 规则')
    t0 = time.time()
    try:
        from score_learning.grammar import analyze as ga
        from score_learning.grammar import learn as gl
        from score_learning.grammar import script as gs
        pieces = ga.analyze_corpus(
            cache=os.path.join(REPO, 'runs', 'grammar_cache',
                               'bach_chorales.json'), progress=False)
        st.detail['pieces'] = len(pieces)
        st.detail['smoother'] = smoother
        sc = None
        if not dry:
            sc = gl.learn(pieces, smoother=smoother)
            gs.save(sc)
            st.detail['yaml'] = gs.DEFAULT_YAML
        ev = (sc.get('evaluation') if not dry else None) or {}
        st.detail['perplexity'] = {
            m: {'script': (d or {}).get('perplexity_script'),
                'unigram': (d or {}).get('perplexity_unigram')}
            for m, d in (ev.get('evaluation') or ev).items()
            if isinstance(d, dict)}
        st.ok = True
    except Exception as e:                                # noqa: BLE001
        st.ok = False
        st.error = f'{type(e).__name__}: {e}'
        if not dry:
            st.detail['trace'] = traceback.format_exc()[-800:]
    st.secs = time.time() - t0
    return st


# ---------------------------------------------------------------- 阶段 2 leadsheet
def run_leadsheet(styles: Optional[List[str]] = None,
                  dry: bool = False) -> List[Stage]:
    out: List[Stage] = []
    styles = styles or LEADSHOT_STYLES
    for style in styles:
        st = Stage(f'leadsheet:{style}', f'{style} → 风格包规则')
        t0 = time.time()
        try:
            from score_learning.leadsheet import analyze as la
            from score_learning.leadsheet import learn as ll
            from score_learning.leadsheet import script as ls
            pieces = la.analyze_style(style, progress=False)
            st.detail['pieces'] = len(pieces)
            if not dry:
                sc = ll.learn(pieces, style)
                path = ls.save(sc, style)
                st.detail['yaml'] = str(path)
                subs = sc.get('subs') or {}
                # 注意：learn_core 重构后元数据在 sub['pieces']={'train','heldout'}，
                # 不再是 sub['meta']['train']（旧字段会静默取到 0）
                st.detail['subs'] = {k: {
                    'train': (v.get('pieces') or {}).get('train'),
                    'heldout': (v.get('pieces') or {}).get('heldout'),
                    'modes': v.get('modes') or {},
                } for k, v in subs.items()}
                ev_rows = []
                for sname, sv in subs.items():
                    for mode, d in (sv.get('evaluation') or {}).items():
                        ev_rows.append({
                            'sub': sname, 'mode': mode,
                            'script': d.get('perplexity_script'),
                            'unigram': d.get('perplexity_unigram'),
                            'prune_kept': d.get('prune_kept'),
                            'nopos': d.get('perplexity_no_position'),
                        })
                st.detail['evaluation'] = ev_rows
                worse = [r for r in ev_rows
                         if r['script'] and r['unigram'] and r['script'] > r['unigram']]
                st.detail['worse_than_unigram'] = len(worse)
                #剪枝被回退的（prune_kept=False）—— 说明剪枝负收益，已自动恢复
                st.detail['prune_reverted'] = sum(
                    1 for r in ev_rows if r.get('prune_kept') is False)
            st.ok = True
        except Exception as e:                            # noqa: BLE001
            st.ok = False
            st.error = f'{type(e).__name__}: {e}'
            if not dry:
                st.detail['trace'] = traceback.format_exc()[-800:]
        st.secs = time.time() - t0
        out.append(st)
    return out


# ---------------------------------------------------------------- 阶段 3 关系
def run_relation(dry: bool = False,
                 relations_out: str = 'runs/relation',
                 plan_out: Optional[str] = None,
                 packs_root: str = 'skills/composition/genre/packs',
                 ) -> Stage:
    """小节关系学习：extract → compile_form_plan → inject_form_plan。"""
    st = Stage('relation', '人工段落标注 → form_plan（段落结构与关系）')
    t0 = time.time()
    try:
        from score_learning.relation.extract import RelationExtractor, DEFAULT_DB
        from score_learning.relation import compile_form_plan as cfp
        from score_learning.relation import inject_form_plan as ifp

        if not os.path.exists(DEFAULT_DB):
            st.ok = False
            st.error = f'标注库不存在：{DEFAULT_DB}（跳过关系学习）'
            st.secs = time.time() - t0
            return st

        # 3a. 提取
        ex = RelationExtractor(DEFAULT_DB)
        mids = [r[0] for r in ex.conn.execute(
            "select distinct melid from sections where type='FORM' order by melid")]
        results, skipped = [], []
        for mid in mids:
            solo = ex.extract_solo(mid)
            if solo is None:
                skipped.append(mid)
                continue
            results.append({'solo': solo, 'relations': ex.relations(solo),
                            'hierarchy': ex.hierarchy(solo)})
        os.makedirs(relations_out, exist_ok=True)
        rj = os.path.join(relations_out, 'bar_relations.json')
        if not dry:
            with open(rj, 'w', encoding='utf-8') as f:
                json.dump({'source': os.path.basename(DEFAULT_DB),
                           'index_semantics': ('sections.start/end = melid 内'
                                               '按 eventid 排序的 0-based 行偏移'),
                           'n_solos': len(results), 'skipped': skipped,
                           'results': results}, f, ensure_ascii=False,
                          separators=(',', ':'))
        st.detail['solos'] = len(results)
        st.detail['bars'] = sum(r['solo']['nbar'] for r in results)
        st.detail['edges'] = sum(len(r['relations']) for r in results)
        from collections import Counter
        st.detail['rel_kinds'] = dict(
            Counter(x['rel'] for r in results for x in r['relations']))

        # 3b. 编译 form_plan
        plan = cfp.compile_corpus(rj)
        pj = plan_out or os.path.join(relations_out, 'form_plan.json')
        if not dry:
            with open(pj, 'w', encoding='utf-8') as f:
                json.dump(plan, f, ensure_ascii=False, indent=1)
        st.detail['styles'] = len(plan.get('policies') or {})

        # 3c. 注入风格包
        if not dry:
            rep = ifp.inject(pj, packs_root, backup=True)
            st.detail['injected'] = rep['injected']
            st.detail['no_match'] = rep['no_match']
        st.ok = True
    except Exception as e:                                # noqa: BLE001
        st.ok = False
        st.error = f'{type(e).__name__}: {e}'
        if not dry:
            st.detail['trace'] = traceback.format_exc()[-800:]
    st.secs = time.time() - t0
    return st


# ---------------------------------------------------------------- 编排
def learn_all(skip: Optional[List[str]] = None, styles: Optional[List[str]] = None,
              dry: bool = False, relations_out: str = 'runs/relation',
              packs_root: str = 'skills/composition/genre/packs',
              smoother: str = 'wb',
              ) -> List[Stage]:
    """跑全部学习阶段。返回每个阶段的执行记录。"""
    # argparse 的 action='append' + nargs='*' 会产生嵌套列表，两种写法都要兼容：
    #   --skip leadsheet relation      → [['leadsheet','relation']]
    #   --skip leadsheet --skip relation → [['leadsheet'], ['relation']]
    flat: List[str] = []
    for grp in (skip or []):
        flat.extend(grp if isinstance(grp, (list, tuple)) else [grp])
    skip = set(flat)
    stages: List[Stage] = []
    if 'chorale' not in skip:
        stages.append(run_chorale(dry=dry, smoother=smoother))
    if 'leadsheet' not in skip:
        stages.extend(run_leadsheet(styles, dry=dry))
    if 'relation' not in skip:
        stages.append(run_relation(dry=dry, relations_out=relations_out,
                                   packs_root=packs_root))
    return stages


# ---------------------------------------------------------------- 报告
def render_report(stages: List[Stage], dry: bool = False) -> str:
    ok = [s for s in stages if s.ok]
    bad = [s for s in stages if s.ok is False]
    L = ['# 统一学习报告', '']
    L.append(f'**{len(ok)}/{len(stages)} 阶段成功**'
             + (f'，{len(bad)} 个失败' if bad else '')
             + ('（干跑，未写盘）' if dry else '')
             + f'，总耗时 {sum(s.secs for s in stages):.1f}s')
    L += ['', '| 阶段 | 说明 | 结果 | 耗时 |', '|---|---|---|---|']
    for s in stages:
        mark = '✅' if s.ok else ('⏭️' if s.ok is None else '❌')
        L.append(f'| `{s.name}` | {s.desc} | {mark} | {s.secs:.1f}s |')
    L.append('')

    for s in stages:
        L.append(f'## `{s.name}` — {s.desc}')
        L.append('')
        if s.ok is False:
            L += [f'**❌ 失败**：{s.error}', '', '```', (s.detail.get('trace') or ''), '```', '']
            continue
        d = s.detail
        if not d:
            L += ['（干跑，无数据）', '']
            continue
        if 'pieces' in d:
            L.append(f'- 语料：**{_fmt(d["pieces"])}** 首')
        if 'solos' in d:
            L += [f'- 曲目 **{_fmt(d["solos"])}** 首 / 小节 **{_fmt(d["bars"])}** / '
                  f'关系边 **{_fmt(d["edges"])}**','']
            if d.get('rel_kinds'):
                L.append('  关系类型：' + '、'.join(
                    f'{k} {v:,}' for k, v in sorted(d['rel_kinds'].items(),
                                                  key=lambda kv: -kv[1])))
                L.append('')
        if 'styles' in d:
            L.append(f'- 体裁覆盖：**{d["styles"]}**')
        if d.get('yaml'):
            L.append(f'- 规则脚本：`{d["yaml"]}`')
        if d.get('subs'):
            tot_t = sum((v or {}).get('train') or 0 for v in d['subs'].values())
            tot_h = sum((v or {}).get('heldout') or 0 for v in d['subs'].values())
            L.append(f'- 子风格 **{len(d["subs"])}** 个，训练 {tot_t} / 留出 {tot_h}')
        if d.get('evaluation'):
            L += ['', '| 子风格 | 调式 | 规则困惑度 | 无位置 | 一元基线 | 剪枝 |',
                  '|---|---|---|---|---|---|']
            for r in d['evaluation'][:12]:
                flag = ' ⚠️' if (r['script'] and r['unigram']
                                  and r['script'] > r['unigram']) else ''
                pk = r.get('prune_kept')
                pks = '保留' if pk is True else ('**已回退**' if pk is False else '-')
                L.append(f'| {r["sub"]} | {r["mode"]} | {_fmt_pct(r["script"])}{flag}'
                         f' | {_fmt_pct(r.get("nopos"))} | {_fmt_pct(r["unigram"])}'
                         f' | {pks} |')
            if d.get('worse_than_unigram'):
                L += ['', f'> ⚠️ **{d["worse_than_unigram"]}** 个调式的规则模型'
                          f'在留出集上差于一元基线。']
            if d.get('prune_reverted'):
                L += ['', f'> ℹ️ **{d["prune_reverted"]}** 个调式的上下文剪枝'
                          f'在留出集上负收益，已由 `prune_with_regression` 自动回退。']
        if d.get('injected'):
            L += ['', '**已注入风格包**：']
            L += [f'- {x}' for x in d['injected']]
        if d.get('no_match'):
            L += ['', f'**无对应 form_plan 的体裁**（{len(d["no_match"])}）：'
                  + '、'.join(d['no_match'])]
        L.append('')
    if bad:
        L += ['---', '', '## 失败汇总', '']
        for s in bad:
            L.append(f'- `{s.name}`：{s.error}')
        L.append('')
    return '\n'.join(L)


def main() -> None:
    ap = argparse.ArgumentParser(
        description='统一学习入口：chorale + leadsheet + 关系，编译成统一规则入库')
    ap.add_argument('--skip', nargs='*', action='append', default=[],
                    choices=['chorale', 'leadsheet', 'relation'],
                    help='跳过的流水线（可重复：--skip leadsheet --skip relation）')
    ap.add_argument('--style', nargs='*', default=None,
                    help=f'只学这些体裁（默认全部：{" ".join(LEADSHOT_STYLES)}）')
    ap.add_argument('--dry-run', action='store_true', help='只看会发生什么，不写盘')
    ap.add_argument('--smoother', default='wb',
                    choices=['wb', 'jm', 'ad', 'kn', 'mkn'],
                    help='chorale 拍层平滑器。实测 jm 优于默认 wb（留出困惑度 '
                         '14.13 vs 15.23，major）；详见 docs/实验重设计与消融_2026-10-04.md')
    ap.add_argument('--relations-out', default='runs/relation')
    ap.add_argument('--packs', default='skills/composition/genre/packs')
    a = ap.parse_args()

    stages = learn_all(skip=a.skip, styles=a.style, dry=a.dry_run,
                       relations_out=a.relations_out, packs_root=a.packs,
                       smoother=a.smoother)
    rep = render_report(stages, dry=a.dry_run)
    out_dir = a.relations_out
    os.makedirs(out_dir, exist_ok=True)
    rp = os.path.join(out_dir, 'learn_all_report.md')
    with open(rp, 'w', encoding='utf-8') as f:
        f.write(rep)
    with open(os.path.join(out_dir, 'learn_all_stages.json'), 'w', encoding='utf-8') as f:
        json.dump([s.as_dict() for s in stages], f, ensure_ascii=False, indent=1)

    print(rep)
    nbad = sum(1 for s in stages if s.ok is False)
    print(f'\n报告 → {rp}')
    raise SystemExit(1 if nbad else 0)


if __name__ == '__main__':
    main()
