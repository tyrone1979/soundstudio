"""手工先验普查脚本。

口径（四袋，互不重叠）：
  A 规则实例   grammar.yaml 里 subs.<子风格>.hard_rules 的条目，每条含人工阈值
  B 检查器     两个 check.py 里实现的规则号 R0x（注意与 A 的编号体系不完全一致）
  C 先验常量   学习/生成代码里的模块级 UPPERCASE 数值常量（非 ASCII 注释已排除）
  D 内联魔数   生成代码里写死的地板值（math.log(<常数>)）与手工权重名
  E 风格契约   style_contract.yaml 中「无 evidence_n 支撑」的人工项 vs 语料实测项

用法：venv/bin/python _count_priors.py
输出：stdout JSON + 控制台表格
"""
import ast
import glob
import io
import json
import os
import re
import collections

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKS = os.path.join(ROOT, 'skills/composition/genre/packs')
SL = os.path.join(ROOT, 'score_learning')

GEN_FILES = [
    'learn_core.py', 'smoothing.py',
    'grammar/compose.py', 'grammar/learn.py', 'grammar/check.py', 'grammar/theory.py',
    'leadsheet/compose.py', 'leadsheet/learn.py', 'leadsheet/check.py',
]


def load(p):
    with io.open(p, encoding='utf-8') as f:
        return yaml.safe_load(f)


def short(p):
    return os.path.relpath(p, PACKS)


# ---------------------------------------------------------------- A 规则实例
def a_rules():
    packs = {}
    id_of = collections.Counter()
    n_entries = n_param = n_subs = 0
    param_values = collections.Counter()
    for p in sorted(glob.glob(os.path.join(PACKS, '*', '*', 'grammar.yaml'))):
        d = load(p)
        subs = d.get('subs') or {}
        rows = []
        for sname, sub in subs.items():
            if not isinstance(sub, dict):
                continue
            hr = sub.get('hard_rules') or []
            n_subs += 1
            n_entries += len(hr)
            pv = 0
            for r in hr:
                if not isinstance(r, dict):
                    continue
                rid = r.get('id') or '?'
                id_of[rid] += 1
                prm = r.get('param')
                if prm is not None:
                    n_param += 1
                    pv += 1
                    if isinstance(prm, dict):
                        for k, v in prm.items():
                            param_values[f'{rid}.{k}'] = v
                    else:
                        param_values[rid] = prm
            rows.append({'sub': sname, 'n': len(hr), 'n_param': pv})
        packs[short(p)] = rows
    return {'packs': packs, 'n_subs': n_subs, 'n_entries': n_entries,
            'n_param': n_param, 'ids': dict(id_of),
            'n_param_keys': len(param_values), 'param_keys': sorted(param_values)}


# ---------------------------------------------------------------- B 检查器
def b_checker():
    out = {}
    for rel in ['grammar/check.py', 'leadsheet/check.py']:
        src = io.open(os.path.join(SL, rel), encoding='utf-8').read()
        ids = sorted(set(re.findall(r'rule[\'"]?\s*[:=]\s*[\'"]?\s*(\'?R0\d(?:/R0\d)?)', src))
                     | set(re.findall(r"add\(\s*'(R0\d)", src)))
        out[rel] = {'ids': sorted(set(x.split('/')[0] for x in ids)), 'n': len(set(x.split('/')[0] for x in ids))}
    return out


# ---------------------------------------------------------------- C/D 代码常量
UPPER_NUM = re.compile(r'^([A-Z][A-Z0-9_]{2,})\s*=\s*(-?\d+(?:\.\d+)?(?:e-?\d+)?)')


def c_consts():
    out = {}
    for rel in GEN_FILES:
        fp = os.path.join(SL, rel)
        if not os.path.exists(fp):
            continue
        src = io.open(fp, encoding='utf-8').read()
        found = []
        in_doc = False
        for i, ln in enumerate(src.split('\n'), 1):
            s = ln.strip()
            if s.startswith('"""') or s.startswith("'''"):
                q = s[:3]
                if s.count(q) >= 2:
                    continue
                in_doc = not in_doc
                continue
            if in_doc or s.startswith('#'):
                continue
            m = UPPER_NUM.match(s)
            if m:
                found.append({'line': i, 'name': m.group(1), 'value': m.group(2)})
        out[rel] = found
    return out


def d_magic():
    out = {}
    for rel in ['grammar/compose.py', 'leadsheet/compose.py']:
        src = io.open(os.path.join(SL, rel), encoding='utf-8').read()
        tree = ast.parse(src)
        floors = collections.Counter()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fn = getattr(n.func, 'attr', None) or getattr(n.func, 'id', None)
                if fn == 'log' and n.args and isinstance(n.args[0], ast.Constant) \
                        and isinstance(n.args[0].value, (int, float)):
                    floors[repr(n.args[0].value)] += 1
        weights = sorted(set(re.findall(
            r'\b([A-Z][A-Z0-9_]*(?:_WEIGHT|_BIAS|_PEN|_TARGETS?|_BONUS))\b', src)))
        out[rel] = {'log_floors': dict(floors), 'n_log_calls': sum(floors.values()),
                    'weight_names': weights, 'n_weights': len(weights)}
    return out


# ---------------------------------------------------------------- E 风格契约
NUMKEY = re.compile(r'^(\s{2,})([a-z_][a-z0-9_]*):\s*(-?\d+\.?\d*)\s*(?:#.*)?$')


def e_contract():
    rows = []
    for p in sorted(glob.glob(os.path.join(PACKS, '*', '*', 'style_contract.yaml'))):
        lines = io.open(p, encoding='utf-8').read().split('\n')
        measured = hand = 0
        block_has_evidence = False
        block_indent = 0
        pending = []
        for ln in lines:
            m = NUMKEY.match(ln)
            if not m:
                continue
            indent, key, val = len(m.group(1)), m.group(2), m.group(3)
            if indent <= block_indent or not block_has_evidence:
                pending.append((key, val))
            if key == 'evidence_n':
                block_has_evidence = True
                block_indent = indent
                measured += len(pending)
                pending = []
        hand += len(pending)
        rows.append({'pack': short(p), 'measured': measured, 'unmeasured_tail': hand})
    return rows


def main():
    A = a_rules()
    C = c_consts()
    res = {
        'A_rule_instances': {k: A[k] for k in ('n_subs', 'n_entries', 'n_param', 'ids', 'n_param_keys')},
        'A_per_pack': A['packs'],
        'B_checker': b_checker(),
        'C_consts': C,
        'D_magic': d_magic(),
        'E_contract': e_contract(),
    }
    n_const = sum(len(v) for v in C.values())
    print('== A 规则实例 ==')
    print('  子风格 %d 个；hard_rules 条目 %d 条；含 param 阈值 %d 条；不同阈值键 %d 个'
          % (A['n_subs'], A['n_entries'], A['n_param'], A['n_param_keys']))
    print('  id 分布：', dict(A['ids']))
    for pack, rows in A['packs'].items():
        tot = sum(r['n'] for r in rows)
        tp = sum(r['n_param'] for r in rows)
        print('   %-42s %3d 条 / %2d 子风格 (param %d)' % (pack, tot, len(rows), tp))
    print('== B 检查器 ==')
    for k, v in res['B_checker'].items():
        print('  %-24s %d 条 %s' % (k, v['n'], v['ids']))
    print('== C 模块级先验常量 ==  %d 个' % n_const)
    for f, lst in C.items():
        if lst:
            print('   %-28s %s' % (f, ', '.join('%s=%s' % (x['name'], x['value']) for x in lst)))
    print('== D 内联地板值与权重 ==')
    for f, v in res['D_magic'].items():
        print('  %-24s log 调用 %d 次，取值 %s' % (f, v['n_log_calls'], v['log_floors']))
        print('   %-24s 权重 %d 个 %s' % ('', v['n_weights'], v['weight_names']))
    print('== E 风格契约 ==')
    tm = sum(r['measured'] for r in res['E_contract'])
    th = sum(r['unmeasured_tail'] for r in res['E_contract'])
    print('  语料实测数值 %d 个；块前无 evidence 的数值 %d 个' % (tm, th))
    with io.open(os.path.join(ROOT, 'runs/prior_census.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print('\n产物 → runs/prior_census.json')


if __name__ == '__main__':
    main()
