"""A/B 对比三个 GM 音色库：MuseScore_General.sf3（现状默认） vs GeneralUser GS vs FluidR3_GM。

用项目自有的 fluidsynth 二进制后端（_render_via_binary）渲染若干代表性 MIDI，
输出 峰值/RMS 表格 + 波形对比图。仅作评估，不改任何默认。
"""
import os
import sys
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import render_enhance as re  # noqa: E402
from render_enhance import _render_via_binary, _find_binary  # noqa: E402
binp = _find_binary()
assert binp, "未找到 fluidsynth 二进制"

SAMPLES = {
    'bach_prelude':   'scores/bach_bwv846_prelude_fugue.mid',
    'jazz_swing':     'papers/eswa/supp/scores/jazz_swing.mid',
    'strings_minuet': 'scores/grammar/strings_minuet.mid',
    'chorale':        'scores/grammar/chorale_G_pickup_s3.mid',
}
SFS = {
    'sf3 (default)':    'MuseScore_General.sf3',
    'generaluser':      'GeneralUser-GS.sf2',
    'fluidr3':          'FluidR3_GM.sf2',
}

rows = []
wave = {}  # (sample, alias) -> 前 3s 归一化波形
for sname, mid in SAMPLES.items():
    if not os.path.exists(mid):
        print('跳过缺失样本:', mid); continue
    for alias, fn in SFS.items():
        sfpath = os.path.join('soundfonts', fn)
        res = _render_via_binary(mid, sfpath, 44100, binp)
        if res is None:
            print('渲染失败:', sname, alias); rows.append((sname, alias, None, None)); continue
        audio, sr = res
        peak = float(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(audio ** 2)))
        rows.append((sname, alias, peak, rms))
        if sname == 'bach_prelude':
            n = min(int(sr * 3), len(audio))
            seg = audio[:n]
            wave[alias] = seg / (peak + 1e-9)

# 表格
print('\n=== 峰值 / RMS 对比（峰值、RMS 均为 0..1 归一化幅度）===')
hdr = 'sample'
for a in SFS:
    hdr += f' | {a:>16}'
print(hdr)
for sname in SAMPLES:
    if not os.path.exists(SAMPLES[sname]):
        continue
    line = f'{sname:>14}'
    for alias in SFS:
        r = next((x for x in rows if x[0] == sname and x[1] == alias), None)
        if r and r[2] is not None:
            line += f' | peak={r[2]:.3f} rms={r[3]:.4f}'
        else:
            line += f' | {"FAIL":>16}'
    print(line)

# 波形对比图（bach 前 3 秒，三个库叠放）
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    if wave:
        fig, axes = plt.subplots(len(wave), 1, figsize=(10, 2.4 * len(wave)), sharex=True)
        if len(wave) == 1:
            axes = [axes]
        for ax, (alias, seg) in zip(axes, wave.items()):
            t = np.arange(len(seg)) / 44100
            ax.plot(t, seg, lw=0.4, color='#2b6cb0')
            ax.set_ylabel(alias, fontsize=9)
            ax.set_ylim(-1.05, 1.05)
            ax.grid(alpha=0.3)
        axes[-1].set_xlabel('时间 (s)')
        fig.suptitle('bach_bwv846 first 3s waveform (peak-normalized per library)', fontsize=11)
        fig.tight_layout()
        out = os.path.join(ROOT, 'docs', 'soundfont_compare', 'ab_waveform_compare.png')
        fig.savefig(out, dpi=110)
        print('\n波形对比图已保存:', out)
except Exception as e:
    print('绘图跳过:', e)

print('\n完成。')
