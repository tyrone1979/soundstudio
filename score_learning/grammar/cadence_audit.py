# -*- coding: utf-8 -*-
"""终止式规则的音乐学审计。

对应论文 §7「Musical validity of the induced rules」承认的缺口：
287 条归纳出的终止式公式里，有 35 条被标为 ``other cadence``、
11 条完全没有类型标注，而硬规则 R11 仍接受它们作为合法乐句结束。

**这个脚本能做什么、不能做什么**
---------------------------------
能：用 :func:`score_learning.grammar.learn._cadence_kind`（一本
和声教材式的判定表：PAC / IAC / PC / HC / DC / 离调 / 主和弦终止）
对**每一条** C 规则重新判定，并

1. 报告现有标注与判定表的一致率；
2. 列出分歧条目（现有标注为 ``other cadence`` 或空、但判定表给出
   明确类型者）——这些是**可以自动纠正**的；
3. 列出两者都判不出类型者——这些**必须留���人工标注**。

不能：替代音乐学家的判断。判定表是教科书规则的编码，
不是标注；本脚本的输出是**待审清单**，不是金标准。
论文因此把该节标题从 knowledge *acquisition* 降为 knowledge *aggregation*。

**用法**
----
::

    venv/bin/python -m score_learning.grammar.cadence_audit
    venv/bin/python -m score_learning.grammar.cadence_audit --yaml <path>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_YAML = os.path.join(ROOT, 'skills', 'composition', 'genre', 'packs',
                            'sacred', 'chorale', 'grammar.yaml')
OUT = os.path.join(ROOT, 'runs', 'experiments')

#: 现有标注里被视为「未定型」的取值
UNCLASSIFIED = ('其他终止', '其他 cadence', 'other cadence', '')


def load_cadences(path: str = DEFAULT_YAML) -> List[Dict[str, Any]]:
    """从发布的 YAML 读出全部 C 规则（扁平化）。"""
    import yaml
    with open(path, encoding='utf-8') as f:
        d = yaml.safe_load(f)
    rows: List[Dict[str, Any]] = []
    for mode, targets in (d.get('cadences') or {}).items():
        for target, blk in targets.items():
            for form in (blk.get('forms') or []):
                rows.append({
                    'id': form.get('id'),
                    'mode': mode,
                    'target': target,
                    'chords': list(form.get('chords') or []),
                    'soprano': list(form.get('soprano') or []),
                    'p': form.get('p'),
                    'n': form.get('n'),
                    # YAML 里字段名是 'type'（不是 'kind'）
                    'kind': (form.get('type') or form.get('kind') or ''),
                })
    return rows


def classify(row: Dict[str, Any]) -> str:
    """用判定表重新分类。输入需 3 个和弦 + 2 个女高音音级。"""
    from score_learning.grammar.learn import _cadence_kind
    chs, sop = row['chords'], row['soprano']
    if len(chs) < 3 or len(sop) < 2:
        return '无法判定（数据不足）'
    try:
        return _cadence_kind(chs[-3:], sop[-2:], row['mode'])
    except Exception as e:                      # noqa: BLE001
        return f'无法判定（{type(e).__name__}）'


def audit(path: str = DEFAULT_YAML, save: bool = True) -> Dict[str, Any]:
    rows = load_cadences(path)
    for r in rows:
        r['judged'] = classify(r)
        r['was_unclassified'] = (str(r['kind']).strip() in UNCLASSIFIED
                                 or not str(r['kind']).strip())

    total = len(rows)
    unclassified = [r for r in rows if r['was_unclassified']]
    # 判定表能给出明确类型的（排除「无法判定」与「其他终止」）
    RESOLVABLE = ('完全正格终止', '不完全正格终止', '变格终止', '半终止',
                  '阻碍终止', '离调终止（副属→目标）', '主和弦终止',
                  # 2026-10-04 补的三类副调终止（见 learn._cadence_kind 文档串）
                  '离调半终止（停于副属）', '转调终止（离调后完全终止）',
                  '转调终止（离调后变格终止）')
    resolvable = [r for r in unclassified
                  if r['judged'] in RESOLVABLE and r['judged'] != '其他终止']
    still_unknown = [r for r in unclassified if r not in resolvable]

    # 一致性：现有标注 vs 判定表（仅在现有标注有类型时可比）
    both = [r for r in rows if not r['was_unclassified']]
    agree = sum(1 for r in both if _norm(r['kind']) == _norm(r['judged']))

    out = {
        'source': os.path.relpath(path, ROOT),
        'n_rules': total,
        'existing_kind_hist': dict(Counter(
            (str(r['kind']).strip() or '(空)') for r in rows).most_common()),
        'judged_hist': dict(Counter(r['judged'] for r in rows).most_common()),
        'n_existing_unclassified': len(unclassified),
        'n_resolvable_by_rule_table': len(resolvable),
        'n_still_unknown': len(still_unknown),
        'agreement_on_labelled': {
            'n': len(both), 'agree': agree,
            'rate': round(agree / len(both), 4) if both else None,
        },
        'resolvable': [{k: r[k] for k in ('id', 'mode', 'chords', 'soprano',
                                          'kind', 'judged', 'n')}
                       for r in resolvable],
        'still_unknown': [{k: r[k] for k in ('id', 'mode', 'chords', 'soprano',
                                             'kind', 'judged', 'n')}
                          for r in still_unknown],
    }
    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'cadence_audit.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def _norm(k: str) -> str:
    """归一化类型标签，便于比较（去掉空白与修饰）。"""
    s = re.sub(r'\s+', '', str(k or ''))
    for pre in ('完全', '不完全'):
        if s.startswith(pre):
            s = s[len(pre):]
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='终止式规则的 musicological 审计')
    ap.add_argument('--yaml', default=DEFAULT_YAML)
    a = ap.parse_args(argv)
    r = audit(a.yaml)
    print('=== 终止式规则审计 ===')
    print('  来源：%s' % r['source'])
    print('  规则总数：%d' % r['n_rules'])
    print()
    print('  现有标注分布（前 8）:')
    for k, v in list(r['existing_kind_hist'].items())[:8]:
        print('    %-22s %d' % (k, v))
    print()
    print('  判定表分布（前 8）:')
    for k, v in list(r['judged_hist'].items())[:8]:
        print('    %-22s %d' % (k, v))
    print()
    ag = r['agreement_on_labelled']
    print('  两者都有类型时的���致率：%d/%d = %.1f%%'
          % (ag['agree'], ag['n'], 100 * ag['rate']))
    print('  现有未分类：%d 条；判定表可解决：%d 条；仍需人工：%d 条'
          % (r['n_existing_unclassified'], r['n_resolvable_by_rule_table'],
             r['n_still_unknown']))
    if r['resolvable']:
        print()
        print('  可自动纠正的前 10 条:')
        for x in r['resolvable'][:10]:
            print('    %-6s %-22s %s→ %s' % (
                x['id'], ' '.join(x['chords']),
                (x['kind'] or '(空)').strip(), x['judged']))
    if r['still_unknown']:
        print()
        print('  **必须人工标注** 的条目:')
        for x in r['still_unknown']:
            print('    %-6s %-22s 判定表→ %s' % (
                x['id'], ' '.join(x['chords']), x['judged']))
    print()
    print('  产物 → runs/experiments/cadence_audit.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
