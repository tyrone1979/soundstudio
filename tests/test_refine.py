# -*- coding: utf-8 -*-
"""风格保真度闭环精炼（P1）测试：register_step / 诊断微调方向 / 确定性 / 审计 / /refine 命令端到端。

运行：python tests/test_refine.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import refine_style as R  # noqa: E402

_PASSED = 0
_FAILED = 0


def check(name, cond, detail=''):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print(f'  ✅ {name}')
    else:
        _FAILED += 1
        print(f'  ❌ {name}  {detail}')


# ============================================================
# 1. register_step 音域档位移动
# ============================================================
def test_register_step():
    print('[1] register_step 音域档位')
    check("mid +1 = high", R.register_step('mid', +1) == 'high')
    check("mid -1 = low", R.register_step('mid', -1) == 'low')
    check("high +1 = very_high", R.register_step('high', +1) == 'very_high')
    check("very_high +1 = None", R.register_step('very_high', +1) is None)
    check("low -1 = None", R.register_step('low', -1) is None)
    check("None 默认 mid +1 = high", R.register_step(None, +1) == 'high')


# ============================================================
# 2. _diagnose_nudges 方向正确性（纯函数，无需内核）
# ============================================================
def _vec(mel=0.0, dens=0.0, vc=0.0, bars=0.0):
    v = [0.0] * 16
    v[0] = mel
    v[5] = dens
    v[12] = vc
    v[13] = bars
    return v


def test_diagnose_nudges():
    print('[2] _diagnose_nudges 微调方向')
    tgt = _vec(mel=70.0, dens=1.0, vc=3.0, bars=24.0)

    # melody 偏低 → reg+（当前 None 默认 mid，升高到 high）
    n = R._diagnose_nudges(_vec(mel=60.0), tgt, {})
    regup = [x for x in n if x[0] == 'reg+']
    check('melody 偏低 → reg+', bool(regup) and regup[0][1].get('register_override') == 'high', str(n))
    # melody 偏高 → reg-
    n = R._diagnose_nudges(_vec(mel=80.0), tgt, {})
    regdn = [x for x in n if x[0] == 'reg-']
    check('melody 偏高 → reg-', bool(regdn) and regdn[0][1].get('register_override') == 'low', str(n))

    # density 偏低 → dens+（2.0）
    n = R._diagnose_nudges(_vec(dens=0.3), tgt, {})
    dns = [x for x in n if x[0] == 'dens+']
    check('density 偏低 → dens+', bool(dns) and dns[0][1].get('density_override') == 2.0, str(n))
    # density 偏高 → dens-（0.5）
    n = R._diagnose_nudges(_vec(dens=1.8), tgt, {})
    dns = [x for x in n if x[0] == 'dens-']
    check('density 偏高 → dens-', bool(dns) and dns[0][1].get('density_override') == 0.5, str(n))

    # voices 偏低（kw voices=2）→ vc+ 到 3
    n = R._diagnose_nudges(_vec(vc=2.0), tgt, {'voices': 2})
    vc = [x for x in n if x[0] == 'vc+']
    check('voices 偏低 → vc+ 到 3', bool(vc) and vc[0][1].get('voices') == 3, str(n))
    # voices 偏高（kw voices=3）→ vc- 到 2
    n = R._diagnose_nudges(_vec(vc=4.0), tgt, {'voices': 3})
    vc = [x for x in n if x[0] == 'vc-']
    check('voices 偏高 → vc- 到 2', bool(vc) and vc[0][1].get('voices') == 2, str(n))

    # bars 偏低（kw bars=16）→ bars+ 到 20
    n = R._diagnose_nudges(_vec(bars=16.0), tgt, {'bars': 16})
    bs = [x for x in n if x[0] == 'bars+']
    check('bars 偏低 → bars+ 到 20', bool(bs) and bs[0][1].get('bars') == 20, str(n))
    # bars 偏高（kw bars=40）→ bars- 到 36
    n = R._diagnose_nudges(_vec(bars=40.0), tgt, {'bars': 40})
    bs = [x for x in n if x[0] == 'bars-']
    check('bars 偏高 → bars- 到 36', bool(bs) and bs[0][1].get('bars') == 36, str(n))

    # 无偏离 → 空列表（收敛）
    n = R._diagnose_nudges(tgt, tgt, {'voices': 3, 'bars': 24})
    check('完全一致 → 无微调（收敛）', n == [], str(n))


# ============================================================
# 3. refine_style 确定性 + 审计（用缓存指纹的 fugue 包，避免重新生成）
# ============================================================
def test_refine_determinism():
    print('[3] refine_style 确定性 + 审计')
    r1 = R.refine_style('fugue', rounds=2, branch_seeds=1)
    r2 = R.refine_style('fugue', rounds=2, branch_seeds=1)
    check('同参数重叠率一致（确定性）', r1['overlap'] == r2['overlap'],
          f"{r1['overlap']} vs {r2['overlap']}")
    check('同参数 spec 标题一致（确定性）',
          (r1['spec'] or {}).get('title') == (r2['spec'] or {}).get('title'))
    # 审计结构
    check('返回 rounds 列表', isinstance(r1.get('rounds'), list) and len(r1['rounds']) >= 2)
    check('返回 overlap 为 float', isinstance(r1.get('overlap'), float))
    check('seed-branch 阶段存在', any(rd.get('phase') == 'seed-branch' for rd in r1['rounds']))
    check('target_vector 16 维', len(r1.get('target_vector') or []) == 16)
    # spec 可用
    spec = r1['spec'] or {}
    check('spec 含 title/parts', bool(spec.get('title')) and bool(spec.get('parts')))


def test_refine_report():
    print('[4] refine_report 审计摘要')
    r = R.refine_style('fugue', rounds=2, branch_seeds=1)
    rep = R.refine_report(r)
    check('报告含「风格重叠率」', '风格重叠率' in rep)
    check('报告含「迭代轨迹」', '迭代轨迹' in rep)
    check('报告含「可审计」', '可审计' in rep)


# ============================================================
# 5. /refine 命令端到端（用缓存指纹的 fugue 包）
# ============================================================
def test_command_e2e():
    print('[5] /refine 命令端到端')
    from llm_compose import ComposeSession, compose_from_text, CommandOnly

    s = ComposeSession()
    try:
        score, spec, note = compose_from_text(s, '/refine fugue')
    except CommandOnly as e:
        check('/refine 命令未抛 CommandOnly', False, str(e))
        return
    check('命令返回 score', score is not None)
    check('命令返回 spec', spec is not None and bool(spec.get('title')))
    check('note 含「风格重叠率」', '风格重叠率' in note, note[:60])
    check('note 含 refine 标识', 'refine' in note.lower())
    check('session.score_spec 已更新', s.score_spec is not None)
    check('session 锁定 genre_id=fugue', s.prefs.get('genre_id') == 'fugue',
          str(s.prefs.get('genre_id')))
    # 用法错误：未指定风格且无 session 风格
    try:
        compose_from_text(ComposeSession(), '/refine')
        check('无风格抛 CommandOnly', False)
    except CommandOnly:
        check('无风格抛 CommandOnly', True)


if __name__ == '__main__':
    test_register_step()
    test_diagnose_nudges()
    test_refine_determinism()
    test_refine_report()
    test_command_e2e()
    print(f'\n结果：{_PASSED} 通过 / {_FAILED} 失败')
    sys.exit(1 if _FAILED else 0)
