"""render_enhance 测试：soundfont/混响/力度（确定性、表演层、可审计）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import render_enhance as R  # noqa: E402


def check(name, cond, extra=''):
    if cond:
        print(f'  ✓ {name}')
        return True
    print(f'  ✗ {name} {extra}')
    return False


def _selftest():
    fail = 0

    # 预设构造
    p = R.preset(piano=True, reverb=0.3, velocity='soft')
    fail += 0 if check('preset(piano,reverb0.3,soft)',
                        p.piano and p.reverb_wet == 0.3
                        and p.velocity_preset == 'soft') else 1
    p2 = R.preset(reverb=0)
    fail += 0 if check('reverb=0 → 关混响', (not p2.reverb_on)
                       and p2.reverb_wet == 0.0) else 1

    # 混响确定性
    rng = np.random.default_rng(0)
    a = rng.standard_normal(2000).astype(np.float32)
    r1 = R.apply_reverb(a, 44100, 0.3, 1.0, seed=7)
    r2 = R.apply_reverb(a, 44100, 0.3, 1.0, seed=7)
    fail += 0 if check('混响确定性(同seed同输出)', np.array_equal(r1, r2)) else 1
    fail += 0 if check('混响不改长度', len(r1) == len(a)) else 1
    fail += 0 if check('混响 wet=0 原样返回', np.array_equal(
        R.apply_reverb(a, 44100, 0.0, 1.0), a)) else 1

    # 力度曲线：仅动表演层
    if R.pretty_midi is not None:
        pm = R.pretty_midi.PrettyMIDI()
        inst = R.pretty_midi.Instrument(program=0)
        inst.notes.append(R.pretty_midi.Note(velocity=100, pitch=60, start=0.0, end=0.5))
        inst.notes.append(R.pretty_midi.Note(velocity=40, pitch=64, start=0.5, end=1.0))
        pm.instruments.append(inst)
        psoft = R.preset(velocity='soft')
        npm = R.apply_velocity_curve(pm, psoft)
        vs = [n.velocity for n in npm.instruments[0].notes]
        fail += 0 if check('力度输出在 1..127', all(1 <= v <= 127 for v in vs), str(vs)) else 1
        for n0, n1 in zip(pm.instruments[0].notes, npm.instruments[0].notes):
            fail += 0 if check('力度不改音高', n0.pitch == n1.pitch) else 1
            fail += 0 if check('力度不改时值', abs(n0.start - n1.start) < 1e-9
                               and abs(n0.end - n1.end) < 1e-9) else 1
        # flat 预设趋近常数
        pflat = R.preset(velocity='flat')
        nfl = R.apply_velocity_curve(pm, pflat)
        fvs = [n.velocity for n in nfl.instruments[0].notes]
        fail += 0 if check('flat 预设压低动态', max(fvs) - min(fvs) < 40, str(fvs)) else 1

    # 后端探测 & 描述
    fail += 0 if check('后端探测值合法', R.detect_backend()
                       in ('python', 'binary', 'none')) else 1
    fail += 0 if check('describe 返回字符串', isinstance(R.describe(p), str)) else 1

    # 真实渲染（若有可用后端 + pretty_midi）
    if R.pretty_midi is not None and R.detect_backend() != 'none':
        fd, mp = tempfile.mkstemp(suffix='.mid', prefix='rt_')
        os.close(fd)
        pm.write(mp)
        fd, wp = tempfile.mkstemp(suffix='.wav', prefix='rt_')
        os.close(fd)
        info = R.render_to_wav(mp, wp, R.preset(reverb=0.2, velocity='natural'))
        fail += 0 if check('render_to_wav 成功', info['ok'], str(info)) else 1
        fail += 0 if check('WAV 文件非空', os.path.exists(wp)
                           and os.path.getsize(wp) > 0) else 1
        os.remove(mp)
        os.remove(wp)
    else:
        print('  ⚠ 跳过真实渲染（无可用后端/无 pretty_midi，非错误）')

    # 写 WAV 基本功能（无需后端）
    fd, w2 = tempfile.mkstemp(suffix='.wav', prefix='w_')
    os.close(fd)
    R.write_wav(w2, a, 44100)
    fail += 0 if check('write_wav 写出有效文件', os.path.getsize(w2) > 0) else 1
    os.remove(w2)
    return fail


if __name__ == '__main__':
    sys.exit(1 if _selftest() else 0)
