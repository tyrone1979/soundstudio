# -*- coding: utf-8 -*-
"""确定性 Groove（P1）测试：humanize 确定性/范围/duration 不变、swing 不改变总时长、文件往返。

运行：python tests/test_groove.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pretty_midi  # noqa: E402
import compose_groove as G  # noqa: E402

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


def _mk():
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(0)
    inst.notes = [
        pretty_midi.Note(velocity=80, pitch=60, start=0.0, end=0.5),
        pretty_midi.Note(velocity=80, pitch=64, start=0.5, end=1.0),
    ]
    pm.instruments.append(inst)
    return pm


def test_humanize():
    print('[1] humanize：确定性 + 范围 + duration 不变')
    pm = _mk()
    G.apply_groove(pm, seed='x', swing=0, humanize=0.5)
    vels = [n.velocity for n in pm.instruments[0].notes]
    durs = [n.end - n.start for n in pm.instruments[0].notes]
    check('velocity 被扰动', vels != [80, 80], str(vels))
    check('velocity ∈ [25,118]', all(25 <= v <= 118 for v in vels), str(vels))
    check('duration 不变', all(abs(d - 0.5) < 1e-9 for d in durs), str(durs))
    # 确定性
    pm2 = _mk()
    G.apply_groove(pm2, seed='x', swing=0, humanize=0.5)
    check('同 seed 结果一致',
          [n.velocity for n in pm2.instruments[0].notes] == vels and
          [n.start for n in pm2.instruments[0].notes] ==
          [n.start for n in pm.instruments[0].notes])


def test_swing():
    print('[2] swing：不改变总时长')
    pm = _mk()
    G.apply_groove(pm, seed='x', swing=0.5, humanize=0)
    maxend = max(n.end for n in pm.instruments[0].notes)
    check('总时长不变 (≤1.0+ε)', maxend <= 1.0 + 1e-6, str(maxend))


def test_noop():
    print('[3] swing=0 humanize=0：原样返回')
    pm = _mk()
    G.apply_groove(pm, seed='x', swing=0, humanize=0)
    check('velocity 不变', [n.velocity for n in pm.instruments[0].notes] == [80, 80])


def test_file_roundtrip():
    print('[4] apply_groove_to_midi：文件往返生效')
    pm = _mk()
    fd, path = tempfile.mkstemp(suffix='.mid')
    os.close(fd)
    try:
        pm.write(path)
        ok = G.apply_groove_to_midi(path, swing=0, humanize=0.5, seed='x')
        check('应用返回 True', ok)
        pm2 = pretty_midi.PrettyMIDI(path)
        vels = [n.velocity for n in pm2.instruments[0].notes]
        check('文件内 velocity 被扰动', vels != [80, 80], str(vels))
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_command():
    print('[5] /groove 命令设置偏好')
    from llm_compose import ComposeSession, apply_command
    s = ComposeSession()
    msg = apply_command(s, '/groove', '0.15 0.4')
    g = s.prefs.get('groove') or {}
    check('cmd 开启 on', g.get('on') is True)
    check('cmd swing=0.15', abs(g.get('swing', -1) - 0.15) < 1e-9, str(g))
    check('cmd humanize=0.4', abs(g.get('humanize', -1) - 0.4) < 1e-9, str(g))
    check('cmd 返回提示 on', 'Groove → on' in msg)
    msg2 = apply_command(s, '/groove', 'off')
    check('cmd off 关闭', (s.prefs['groove'] or {}).get('on') is False)
    check('cmd off 提示', 'off' in msg2)


if __name__ == '__main__':
    test_humanize()
    test_swing()
    test_noop()
    test_file_roundtrip()
    test_command()
    print(f'\n结果：{_PASSED} 通过 / {_FAILED} 失败')
    sys.exit(1 if _FAILED else 0)
