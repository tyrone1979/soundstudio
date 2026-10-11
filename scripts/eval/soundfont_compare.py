#!/usr/bin/env python3
"""四库综合对比：渲染 + 客观指标 + 听感文件。

声库：sf3(现状回退) / generaluser(平衡) / fluidr3(最全) / toh(Timbres of Heaven 4.0, 最大最细)
样本：orchestral(管弦) / piano(钢琴独奏) / jazz(钢琴+铜管)
渲染：系统 fluidsynth，-r 44100 -g 0.85（统一增益，差异即为声库特征）
产物：runs/soundfont_compare/cmp_<sf>__<sample>.wav（听感对比）
      docs/soundfont_compare_2026-10-05.md（指标表）
      compare_fig_waveforms.png / compare_fig_spectro.png / compare_fig_metrics.png
"""
import os, subprocess, json, textwrap
import numpy as np
import soundfile as sf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SF_DIR = os.path.join(ROOT, 'soundfonts')
OUT_DIR = os.path.join(ROOT, 'runs', 'soundfont_compare')
FIG_DIR = os.path.join(ROOT, 'docs', 'soundfont_compare')
DOC_DIR = os.path.join(ROOT, 'docs')
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(FIG_DIR, exist_ok=True)

FLU = '/opt/homebrew/bin/fluidsynth'
GAIN = 0.85

SOUNDFONTS = {
    'sf3':         'MuseScore_General.sf3',
    'generaluser': 'GeneralUser-GS.sf2',
    'fluidr3':     'FluidR3_GM.sf2',
    'toh':         'Timbres_of_Heaven.sf2',
}
SAMPLES = {
    'orchestral': 'scores/pirate_storm_allegro.mid',
    'piano':      'scores/grammar/sonata_allegro.mid',
    'jazz':       'scores/grammar/jazz_swing.mid',
}
LABEL = {
    'sf3': 'sf3 (MuseScore General)',
    'generaluser': 'GeneralUser GS',
    'fluidr3': 'FluidR3 GM',
    'toh': 'Timbres of Heaven 4.0',
}


def render(sf_file, midi, out_wav):
    sf_path = os.path.join(SF_DIR, sf_file)
    cmd = [FLU, '-ni', '-r', '44100', '-g', str(GAIN),
           '-F', out_wav, '-T', 'wav', sf_path, midi]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return r.returncode
    # 峰值归一化：消除各库原始响度差（仅比音色，不比音量）。
    # 项目后端 render_enhance 本身也会做同样归一化，故在此对齐。
    data, sr = sf.read(out_wav)
    peak = float(np.max(np.abs(data))) if len(data) else 0.0
    if peak > 1e-6:
        data = data / peak * 0.99
    sf.write(out_wav, data.astype(np.float32), sr, subtype='PCM_16')
    return 0


def metrics(wav):
    data, sr = sf.read(wav)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data.astype(np.float64)
    n = len(data)
    peak = float(np.max(np.abs(data))) if n else 0.0
    rms = float(np.sqrt(np.mean(data ** 2))) if n else 0.0
    clip = int(np.sum(np.abs(data) >= 0.999))
    crest_db = 20 * np.log10(peak / rms) if (rms > 1e-9 and peak > 0) else 0.0
    rms_db = 20 * np.log10(rms) if rms > 1e-9 else -120.0
    # 频谱（帧平均、能量加权）
    win = np.hanning(2048)
    hop = 1024
    cents, flats, bass, mid, treb = [], [], [], [], []
    freqs = np.fft.rfftfreq(win.size, 1.0 / sr)
    i_bass = freqs < 250
    i_mid = (freqs >= 250) & (freqs < 4000)
    i_treb = freqs >= 4000
    for s in range(0, n - win.size, hop):
        frame = data[s:s + win.size] * win
        mag = np.abs(np.fft.rfft(frame))
        mag = mag[:win.size // 2 + 1]
        energy = np.sum(mag ** 2)
        if energy < 1e-12:
            continue
        cents.append(np.sum(freqs * mag ** 2) / energy)
        m = mag + 1e-12
        geo = np.exp(np.mean(np.log(m)))
        flats.append(geo / np.mean(m))
        bass.append(np.sum(mag[i_bass] ** 2))
        mid.append(np.sum(mag[i_mid] ** 2))
        treb.append(np.sum(mag[i_treb] ** 2))
    centroid = float(np.mean(cents)) if cents else 0.0
    flatness = float(np.mean(flats)) if flats else 0.0
    b, m_, t = (sum(bass), sum(mid), sum(treb))
    tot = b + m_ + t + 1e-12
    return {
        'peak': peak, 'rms_db': rms_db, 'crest_db': crest_db,
        'clip': clip, 'centroid_hz': centroid, 'flatness': flatness,
        'bass_ratio': b / tot, 'mid_ratio': m_ / tot, 'treb_ratio': t / tot,
        'sr': sr, 'samples': n,
    }


def main():
    results = {}
    # 1) 渲染 + 指标
    for sk, sf_file in SOUNDFONTS.items():
        results[sk] = {}
        for samp, midi in SAMPLES.items():
            out = os.path.join(OUT_DIR, f'cmp_{sk}__{samp}.wav')
            rc = render(sf_file, os.path.join(ROOT, midi), out)
            if rc != 0:
                print(f'!! render failed {sk}/{samp} rc={rc}')
                results[sk][samp] = None
                continue
            results[sk][samp] = metrics(out)
            print(f'   {sk:11s}/{samp:11s} rc=0 peak={results[sk][samp]["peak"]:.3f} '
                  f'rms={results[sk][samp]["rms_db"]:6.1f}dB crest={results[sk][samp]["crest_db"]:5.1f}dB '
                  f'cent={results[sk][samp]["centroid_hz"]:5.0f}Hz clip={results[sk][samp]["clip"]}')

    # 2) 平均表（跨样本）
    avg = {}
    keys = ['peak', 'rms_db', 'crest_db', 'centroid_hz', 'flatness', 'bass_ratio', 'mid_ratio', 'treb_ratio', 'clip']
    for sk in SOUNDFONTS:
        avg[sk] = {}
        for k in keys:
            vals = [results[sk][s][k] for s in SAMPLES if results[sk][s]]
            avg[sk][k] = float(np.mean(vals)) if vals else 0.0

    # 3) Markdown 表
    md = ['# SoundFont 四库综合对比（2026-10-05）', '',
          f'渲染：fluidsynth `-r 44100 -g {GAIN}`，渲染后统一峰值归一化到 0.99（仅比音色）。',
          '峰值/RMS 反映响度与动态；crest 越大动态越大；centroid 越大越"亮"；'
          'flatness 越大越"噪/有气声"；bass/mid/treb 为频段能量占比。', '',
          '## 跨样本平均', '',
          '| 声库 | 峰值 | RMS(dB) | crest(dB) | 频谱质心(Hz) | 平坦度 | 低频% | 中频% | 高频% | 削波样本 |',
          '|---|---|---|---|---|---|---|---|---|---|']
    for sk in SOUNDFONTS:
        a = avg[sk]
        md.append(f'| {LABEL[sk]} | {a["peak"]:.3f} | {a["rms_db"]:.1f} | {a["crest_db"]:.1f} | '
                  f'{a["centroid_hz"]:.0f} | {a["flatness"]:.3f} | {a["bass_ratio"]*100:.0f} | '
                  f'{a["mid_ratio"]*100:.0f} | {a["treb_ratio"]*100:.0f} | {int(a["clip"])} |')
    md += ['', '## 逐样本明细', '']
    for samp in SAMPLES:
        md.append(f'### {samp} ({SAMPLES[samp]})')
        md.append('| 声库 | 峰值 | RMS(dB) | crest(dB) | 质心(Hz) | 平坦度 | 削波 |')
        md.append('|---|---|---|---|---|---|---|')
        for sk in SOUNDFONTS:
            r = results[sk][samp]
            if not r:
                md.append(f'| {LABEL[sk]} | - | - | - | - | - | - |')
                continue
            md.append(f'| {LABEL[sk]} | {r["peak"]:.3f} | {r["rms_db"]:.1f} | {r["crest_db"]:.1f} | '
                      f'{r["centroid_hz"]:.0f} | {r["flatness"]:.3f} | {r["clip"]} |')
        md.append('')
    os.makedirs(DOC_DIR, exist_ok=True)
    with open(os.path.join(DOC_DIR, 'soundfont_compare_2026-10-05.md'), 'w') as f:
        f.write('\n'.join(md))
    with open(os.path.join(FIG_DIR, 'metrics.json'), 'w') as f:
        json.dump({'avg': avg, 'raw': results, 'gain': GAIN}, f, indent=2)

    # 4) 图形：4 库波形（orchestral）+ 频谱图 + 指标柱状
    plot_waveforms(results)
    plot_spectrograms(results)
    plot_metric_bars(avg)

    print('\nDone. Listen files in runs/soundfont_compare/ ; table in docs/soundfont_compare_2026-10-05.md')


def _load_mono(wav):
    d, sr = sf.read(wav)
    if d.ndim > 1:
        d = d.mean(axis=1)
    return d.astype(np.float64), sr


def plot_waveforms(results):
    fig, axes = plt.subplots(4, 1, figsize=(11, 8), sharex=False)
    for ax, sk in zip(axes, SOUNDFONTS):
        wav = os.path.join(OUT_DIR, f'cmp_{sk}__orchestral.wav')
        d, sr = _load_mono(wav)
        t = np.linspace(0, len(d) / sr, len(d))
        ax.plot(t, d, lw=0.4, color='#3a6ea5')
        ax.set_ylabel(LABEL[sk].split(' ')[0], fontsize=9)
        ax.set_ylim(-1.05, 1.05)
        ax.set_xlim(0, len(d) / sr)
    axes[-1].set_xlabel('Time (s)')
    fig.suptitle('Waveforms - orchestral sample (pirate_storm_allegro)', fontsize=11)
    fig.tight_layout()
    p = os.path.join(FIG_DIR, 'compare_fig_waveforms.png')
    fig.savefig(p, dpi=110)
    plt.close(fig)


def plot_spectrograms(results):
    fig, axes = plt.subplots(4, 1, figsize=(11, 8), sharex=False)
    for ax, sk in zip(axes, SOUNDFONTS):
        wav = os.path.join(OUT_DIR, f'cmp_{sk}__orchestral.wav')
        d, sr = _load_mono(wav)
        # 降采样到 16k 加速
        d16 = d[::3]
        sr16 = sr // 3
        ax.specgram(d16, NFFT=1024, Fs=sr16, noverlap=512, cmap='magma',
                    vmin=-70, vmax=0)
        ax.set_ylabel(LABEL[sk].split(' ')[0], fontsize=9)
        ax.set_ylim(0, 8000)
    axes[-1].set_xlabel('Time (s)')
    fig.suptitle('Spectrograms - orchestral sample (0-8kHz)', fontsize=11)
    fig.tight_layout()
    p = os.path.join(FIG_DIR, 'compare_fig_spectro.png')
    fig.savefig(p, dpi=110)
    plt.close(fig)


def plot_metric_bars(avg):
    metrics_def = [
        ('rms_db', 'RMS (dB)', False),
        ('centroid_hz', 'Spectral centroid (Hz)', False),
        ('crest_db', 'Crest factor (dB)', False),
        ('flatness', 'Spectral flatness', False),
        ('treb_ratio', 'Treble energy %', True),
    ]
    fig, axes = plt.subplots(1, len(metrics_def), figsize=(14, 3.2))
    sks = list(SOUNDFONTS)
    x = np.arange(len(sks))
    colors = ['#888', '#4c9', '#49f', '#e55']
    for ax, (key, title, pct) in zip(axes, metrics_def):
        vals = [avg[sk][key] for sk in sks]
        ax.bar(x, vals, color=colors)
        ax.set_title(title, fontsize=9)
        ax.set_xticks(x)
        ax.set_xticklabels([LABEL[sk].split(' ')[0] for sk in sks], rotation=20, fontsize=8)
        for i, v in enumerate(vals):
            ax.text(i, v, f'{v:.0f}' if abs(v) > 1 else f'{v:.2f}',
                    ha='center', va='bottom', fontsize=7)
        if pct:
            ax.set_ylim(0, max(vals) * 1.3)
    fig.suptitle('Averaged objective metrics (higher = the direction labeled)', fontsize=10)
    fig.tight_layout()
    p = os.path.join(FIG_DIR, 'compare_fig_metrics.png')
    fig.savefig(p, dpi=110)
    plt.close(fig)


if __name__ == '__main__':
    main()
