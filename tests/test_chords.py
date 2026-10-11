# -*- coding: utf-8 -*-
"""和弦进行顾问（#8 竞品差距）测试：罗马解析/拼写/校验/SATB/平行检测 + /chords 命令端到端。

运行：python tests/test_chords.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chord_advisor as C  # noqa: E402
import llm_compose as L    # noqa: E402

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
# 1. 罗马数字解析
# ============================================================
def test_parse_roman():
    print('[1] 罗马数字解析')
    p = C.parse_roman('I')
    check('I degree=1 major', p['degree'] == 1 and p['quality'] == 'maj')
    p = C.parse_roman('ii')
    check('ii degree=2 minor', p['degree'] == 2 and p['quality'] == 'min')
    p = C.parse_roman('V7')
    check('V7 degree=5 dom7', p['degree'] == 5 and p['seventh'] == 'dom')
    p = C.parse_roman('vii°')
    check('vii° degree=7 dim', p['degree'] == 7 and p['quality'] == 'dim')
    p = C.parse_roman('I64')
    check('I64 fig=64', p['inversion_figure'] == '64')
    p = C.parse_roman('bII')
    check('bII degree=2 accid=-1', p['degree'] == 2 and p['accid'] == -1)
    p = C.parse_roman('ii65')
    check('ii65 fig=65 为小七和弦（ii7 第一转位）', p['inversion_figure'] == '65'
          and p['seventh'] == 'min', str(p))
    p = C.parse_roman('V65')
    check('V65 fig=65 隐含属七', p['inversion_figure'] == '65'
          and p['seventh'] == 'dom', str(p))


# ============================================================
# 2. 和弦拼写（正确性）
# ============================================================
def test_spelling():
    print('[2] 和弦拼写')
    a = C.analyze_progression(['V7'], 0, 'major')
    names = [n for (n, _pc) in a['parsed'][0]['spelling']]
    check('C 大调 V7 = G B D F', set(names) == {'G', 'B', 'D', 'F'}, str(names))
    a = C.analyze_progression(['i'], 9, 'minor')
    names = [n for (n, _pc) in a['parsed'][0]['spelling']]
    check('A 小调 i = A C E', set(names) == {'A', 'C', 'E'}, str(names))
    # 小调降号前缀 → 平行大调借用
    a = C.analyze_progression(['i', 'bVI', 'bIII', 'bVII'], 9, 'minor')
    roots = [SHARP_(p['root_pc']) for p in a['parsed']]
    check('Am bVI 根音 F', roots[1] == 'F', str(roots))
    check('Am bIII 根音 C', roots[2] == 'C', str(roots))
    check('Am bVII 根音 G', roots[3] == 'G', str(roots))
    # 大调降号前缀
    a = C.analyze_progression(['bVII'], 0, 'major')
    check('C 大调 bVII 根音 Bb', a['parsed'][0]['spelling'][0][0] == 'Bb',
          str(a['parsed'][0]))


def SHARP_(pc):
    return C.SHARP[pc % 12]


# ============================================================
# 3. 进行校验（功能圈 / 终止式 / 导音）
# ============================================================
def test_validate():
    print('[3] 进行校验')
    a = C.analyze_progression(['I', 'V', 'vi', 'IV'], 0, 'major')
    check('I V vi IV 解析 4 和弦', len(a['parsed']) == 4)
    check('I 功能主', a['parsed'][0]['function'] == '主(T)')
    check('V 功能属', a['parsed'][1]['function'] == '属(D)')
    check('I V vi IV 提示未以主收束',
          any('未以主和弦' in t for (_l, t) in a['issues']))
    a2 = C.analyze_progression(['ii', 'V', 'I'], 0, 'major')
    check('ii V I 触发正格终止',
          any('正格终止' in t for (_l, t) in a2['issues']))
    # 导音解决检查：V 后不接含主音的和弦 → 应提示
    # iii = E G B，不含主音 C，故 V→iii 触发导音未解决提示
    a3 = C.analyze_progression(['I', 'V', 'iii'], 0, 'major')
    check('V→iii 触发导音解决提示',
          any('导音' in t for (_l, t) in a3['issues']))


# ============================================================
# 4. SATB 实现 + 平行检测（规则引擎）
# ============================================================
def test_satb():
    print('[4] SATB 实现 + 平行检测')
    a = C.analyze_progression(['I', 'IV', 'V', 'I'], 0, 'major')
    real = C.realize_satb(a, 0, 'major')
    check('SATB 生成 4 行', len(real['realizations']) == 4)
    # 每行应有 4 个声部且音名合法
    for r in real['realizations']:
        check(f'{r["chord"]} 四声部齐全',
              len(r['names']) == 4 and all(re.match(r'^[A-G][#b]?\d$', n) for n in r['names']))
    # 平行检测器本身可工作
    out = []
    C._detect_parallels([60, 64, 67, 48], [67, 71, 74, 55], 1, 'I->V', out)
    check('平行五度检测器可触发', any(p['kind'] == '平行五度' for p in out), str(out))


# ============================================================
# 5. advisor 报告（校验模式 + 推荐模式）
# ============================================================
def test_advisor_report():
    print('[5] advisor 报告')
    rep = C.advisor('I V vi IV')
    check('报告含调性', '调性' in rep)
    check('报告含根音运动', '根音运动' in rep)
    check('报告含四声部', '四声部' in rep)
    check('报告含可审计', '可审计' in rep)
    check('报告含功能', '功能' in rep)
    rep2 = C.advisor('jazz')
    check('jazz 推荐含 ii V I', 'ii V I' in rep2)
    check('推荐模式含「常用进行」', '常用进行' in rep2)
    # 小调进行
    rep3 = C.advisor('Am i bVI bIII bVII')
    check('小调报告拼写 A C E', 'A C E' in rep3, rep3[:200])


# ============================================================
# 6. /chords 命令端到端（compose_from_text 抛 CommandOnly 带报告）
# ============================================================
def test_command_e2e():
    print('[6] /chords 命令端到端')
    from llm_compose import ComposeSession, compose_from_text, CommandOnly
    # 校验模式
    try:
        compose_from_text(ComposeSession(), '/chords I V vi IV')
        check('/chords 不抛 CommandOnly', False)
    except CommandOnly as e:
        msg = e.message
        check('/chords 命令返回报告', '根音运动' in msg and '四声部' in msg, msg[:60])
        check('/chords 报告含 I V vi IV', 'I V vi IV' in msg)
    # 推荐模式
    try:
        compose_from_text(ComposeSession(), '/chords jazz')
        check('/chords jazz 不抛 CommandOnly', False)
    except CommandOnly as e:
        check('/chords jazz 报告含常用进行', '常用进行' in e.message)
    # 含调性的进行
    try:
        compose_from_text(ComposeSession(), '/chords Am i bVI bIII bVII')
        check('/chords Am 进行 不抛 CommandOnly', False)
    except CommandOnly as e:
        check('/chords Am 报告含 A 小调', 'A 小调' in e.message, e.message[:60])
    # 无参数 → 仍给出推荐（不崩溃）
    try:
        compose_from_text(ComposeSession(), '/chords')
        check('/chords 无参 不抛 CommandOnly', False)
    except CommandOnly as e:
        check('/chords 无参返回报告', '和弦进行顾问' in e.message)


if __name__ == '__main__':
    test_parse_roman()
    test_spelling()
    test_validate()
    test_satb()
    test_advisor_report()
    test_command_e2e()
    print(f'\n结果：{_PASSED} 通过 / {_FAILED} 失败')
    sys.exit(1 if _FAILED else 0)
