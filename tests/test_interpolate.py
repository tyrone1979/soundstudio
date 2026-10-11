# -*- coding: utf-8 -*-
"""风格插值原语测试 + **命令退役**回归。

背景 —— `/interpolate` 曾是第二条作曲路径（绕过 /compose-grammar 直接调
`style_interpolate.interpolate_spec`）。现在作曲只保留
**LLM 自然语言 → /compose-grammar**：

  * 命令入口已retire，提示统一指向 /compose-grammar；
  * 自然语言入口也断了 —— `infer_edit_intent` / 风格插值的正则路由不再
    把「插值 A B」「重写」之类路由进旧管线；
  * **底层 `style_interpolate` 原语保留**（仍被 studio 历史会话与
    风格工具引用），故前四组原语测试继续有效。

运行：python -m pytest tests/test_interpolate.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import style_interpolate as I  # noqa: E402
import style_skeleton as S      # noqa: E402

A_ID = 'folk/british-folk-tune'  # tempo 120, 6/8
B_ID = 'jazz/swing-standard'     # tempo 132, 4/4

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
# 1. 原语
# ============================================================
def test_primitives():
    print('[1] 插值原语')
    # 音名 ↔ MIDI 往返
    mid = I._note_to_midi('G4')
    check('音名→MIDI G4=67', mid == 67, f'got {mid}')
    check('MIDI→音名 67=G4', I._midi_to_note(67) == 'G4', I._midi_to_note(67))
    check('音名往返 C#4', I._midi_to_note(I._note_to_midi('C#4')) == 'C#4')
    # 数值 lerp
    check('lerp 数值', abs(I.lerp(60, 100, 0.5) - 80) < 1e-9)
    # 音名插值落在两端之间
    ml = I._note_to_midi(I.interp_scalar('D4', 'C4', 0.5))
    check('音名插值居中 (C4≤mid≤D4)', 60 <= ml <= 62, f'midi={ml}')
    # 离散按阈值选
    check('离散 t=0 取 A', I.interp_scalar('x', 'y', 0.0) == 'x')
    check('离散 t=1 取 B', I.interp_scalar('x', 'y', 1.0) == 'y')
    # 列表合并去重（A 优先）
    check('列表合并去重', I.interp_list(['I', 'V'], ['I', 'vi'], 0.5) == ['I', 'V', 'vi'])
    # 密度混合返回合法密度名
    d = I._dens_blend('transparent', 'dense', 0.5)
    check('密度混合合法', d in I._DENS_MAP, f'got {d}')


# ============================================================
# 2. 结构化字段混合
# ============================================================
def test_struct_fields():
    print('[2] 结构化字段混合')
    mA = S.resolve_genre(A_ID)['profile']
    mB = S.resolve_genre(B_ID)['profile']
    # tempo 数值插值
    tp = I.interp_tempo(mA['tempo'], mB['tempo'], 0.5)
    check('tempo 中点=(120+132)/2=126', tp['default'] == 126, f"got {tp.get('default')}")
    # register 音名插值
    reg = I.interp_register(mA['register'], mB['register'], 0.5)
    ml = I._note_to_midi(reg['melody']['low'])
    check('register.mel.low 居中 (C4≤ ≤D4)', 60 <= ml <= 62, f"{reg['melody']['low']} midi={ml}")
    # meter 离散选（t=0.5 取 A）
    prof5 = I.interp_profile(mA, mB, 0.5)
    check('meter 中点取 A', prof5['meter']['primary'] == mA['meter']['primary'],
          f"{prof5['meter']['primary']} vs {mA['meter']['primary']}")


# ============================================================
# 3. interpolate_genre 端点严格等于原包
# ============================================================
def test_genre_endpoints():
    print('[3] 端点严格等于原包')
    gA = S.resolve_genre(A_ID)['profile']
    gB = S.resolve_genre(B_ID)['profile']
    g0 = I.interpolate_genre(A_ID, B_ID, 0.0)['profile']
    g1 = I.interpolate_genre(A_ID, B_ID, 1.0)['profile']
    check('t=0 tempo==A', g0['tempo']['default'] == gA['tempo']['default'])
    check('t=0 meter==A', g0['meter']['primary'] == gA['meter']['primary'])
    check('t=0 reg.mel.low==A', g0['register']['melody']['low'] == gA['register']['melody']['low'])
    check('t=1 tempo==B', g1['tempo']['default'] == gB['tempo']['default'])
    check('t=1 meter==B', g1['meter']['primary'] == gB['meter']['primary'])
    check('t=1 reg.mel.low==B', g1['register']['melody']['low'] == gB['register']['melody']['low'])
    # 中点数值插值
    g5 = I.interpolate_genre(A_ID, B_ID, 0.5)['profile']
    check('t=0.5 tempo==126', g5['tempo']['default'] == 126, f"got {g5['tempo']['default']}")


# ============================================================
# 4. interpolate_spec 集成 + 确定性
# ============================================================
def test_spec_integration():
    print('[4] spec 集成与确定性')
    sp = I.interpolate_spec(A_ID, B_ID, 0.5, seed='demo', bars=16)
    check('spec tempo==126', sp.get('tempo') == 126, f"got {sp.get('tempo')}")
    check('spec bars==16', sp.get('bars') == 16, f"got {sp.get('bars')}")
    ts = str(sp.get('time_signature') or '')
    check('spec 拍号合法', ts in ('3/4', '4/4', '2/4', '6/8', '12/8', '2/2'), ts)
    # 确定性：同 seed 两次相同
    sp2 = I.interpolate_spec(A_ID, B_ID, 0.5, seed='demo', bars=16)
    check('确定性 同seed同结果',
          sp.get('bars') == sp2.get('bars') and sp.get('tempo') == sp2.get('tempo'))
    # 不同 t 产生不同结果（插值生效）
    sp_lo = I.interpolate_spec(A_ID, B_ID, 0.0, seed='demo', bars=16)
    sp_hi = I.interpolate_spec(A_ID, B_ID, 1.0, seed='demo', bars=16)
    check('t=0 与 t=1 速度不同（插值生效）',
          sp_lo.get('tempo') != sp_hi.get('tempo'),
          f"{sp_lo.get('tempo')} vs {sp_hi.get('tempo')}")
    # dict 分支：resolve_genre(mixed) 返回自身
    mixed = I.interpolate_genre(A_ID, B_ID, 0.5)
    check('resolve_genre(mixed)==mixed', S.resolve_genre(mixed) is mixed)
    # interpolate_calls 返回调用序列
    calls = I.interpolate_calls(A_ID, B_ID, 0.5, seed='demo')
    check('interpolate_calls 含 new_score',
          any(c.get('script') == 'new_score' for c in calls.get('calls', [])))


def test_command_retired():
    """`/interpolate` 已退役：入口必须回一条指向 /compose-grammar 的提示，
    而不是继续跑插值（作曲主路径只保留 LLM → /compose-grammar）。"""
    from llm_compose import ComposeSession, compose_from_text, CommandOnly

    for cmd in ('/interpolate folk/british-folk-tune jazz/swing-standard 0.5',
                '/skeleton 圆舞曲',
                '/rewrite', '/continue', '/apply', '/advice',
                '/transpose D', '/extend 8'):
        try:
            compose_from_text(ComposeSession(), cmd)
            raise AssertionError(f'{cmd} 应已退役，却照常执行')
        except CommandOnly as e:
            msg = str(e)
            assert 'compose-grammar' in msg, f'{cmd} 的提示未指向 /compose-grammar：{msg}'


if __name__ == '__main__':
    test_primitives()
    test_struct_fields()
    test_genre_endpoints()
    test_spec_integration()
    test_command_retired()
    print(f'\n结果：{_PASSED} 通过 / {_FAILED} 失败')
    sys.exit(1 if _FAILED else 0)
