"""
加载一段音频并绘制 Mel 频谱图。

用法:
    python mel_spectrogram.py                 # 合成巴赫《C大调前奏曲》并画 Mel 频谱
    python mel_spectrogram.py some.wav        # 加载 WAV 文件并画 Mel 频谱
    python mel_spectrogram.py in.wav -o out.png -n 128

不依赖 librosa/scipy：STFT 与 Mel 滤波器组均用 numpy 实现。

仅命令行使用（export_acc_sample.py）；工作室内的频谱改由
score_studio.log_mel_spectrogram 计算，两处修改需保持一致。
"""
import argparse
import os
import sys
import wave

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ---------------------------------------------------------------- 音频加载

def load_wav(path: str) -> tuple:
    """读取 16-bit PCM WAV，返回 (mono_float32, sample_rate)"""
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n_channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        raw = w.readframes(w.getnframes())

    if sampwidth == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sampwidth == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"不支持的采样宽度: {sampwidth} 字节")

    if n_channels > 1:
        data = data.reshape(-1, n_channels).mean(axis=1)
    return data, sr


def synthesize_from_musicxml(path: str) -> tuple:
    """解析 MusicXML，用项目自带的合成器离线渲染为波形"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from music_score.audio_engine import AudioEngine, InstrumentConfig

    import music21
    score = music21.converter.parse(path)

    # 速度（BPM）
    tempo = 100.0
    try:
        first_mm = score.flatten().getElementsByClass(music21.tempo.MetronomeMark)
        if len(first_mm) > 0:
            tempo = float(first_mm[0].number)
    except Exception:
        pass
    spb = 60.0 / tempo          # 每拍秒数
    quarter_per_beat = 1.0      # MetronomeMark 通常以四分音符为一拍

    sr = AudioEngine.SAMPLE_RATE
    engine = AudioEngine()
    config = engine.ORCHESTRA_PRESETS["piano"]

    # 收集音符事件 (起始秒, 时长秒, midi, 力度)
    events = []
    for el in score.flatten().notes:
        offset_q = float(el.offset)
        dur_q = float(el.duration.quarterLength)
        if dur_q <= 0:
            continue
        start = offset_q * quarter_per_beat * spb
        dur = dur_q * quarter_per_beat * spb
        vel = int(getattr(el, "volume", None) and el.volume.velocity or 80)
        if vel is None or vel <= 0:
            vel = 80
        if el.isChord:
            for p in el.pitches:
                events.append((start, dur, p.midi, vel))
        else:
            events.append((start, dur, el.pitch.midi, vel))

    if not events:
        raise ValueError(f"乐谱中没有音符: {path}")

    total = int(max(s + d for s, d, _, _ in events) * sr) + sr
    mix = np.zeros(total, dtype=np.float32)

    for start, dur, midi, vel in events:
        freq = 440.0 * (2 ** ((midi - 69) / 12.0))
        wave = engine._generate_wave(freq, max(dur, 0.05), config, vel)
        i0 = int(start * sr)
        i1 = min(i0 + len(wave), total)
        mix[i0:i1] += wave[: i1 - i0]

    peak = np.max(np.abs(mix))
    if peak > 0:
        mix = mix / peak * 0.9
    print(f"[audio] 已从乐谱合成音频: {len(mix) / sr:.1f} 秒, {sr} Hz, 速度 {tempo:.0f} BPM")
    return mix, sr


# ---------------------------------------------------------------- Mel 频谱

def stft_power(y: np.ndarray, n_fft: int = 2048, hop_length: int = 512) -> np.ndarray:
    """短时傅里叶变换，返回功率谱 (n_freqs, n_frames)"""
    window = np.hanning(n_fft)
    pad = n_fft // 2
    y = np.pad(y, (pad, pad))
    n_frames = 1 + (len(y) - n_fft) // hop_length
    frames = np.lib.stride_tricks.as_strided(
        y,
        shape=(n_frames, n_fft),
        strides=(y.strides[0] * hop_length, y.strides[0]),
    )
    spec = np.fft.rfft(frames * window, axis=1)
    return np.abs(spec) ** 2


def hz_to_mel(f: np.ndarray) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + np.asarray(f, dtype=float) / 700.0)


def mel_to_hz(m: np.ndarray) -> np.ndarray:
    return 700.0 * (10 ** (np.asarray(m, dtype=float) / 2595.0) - 1.0)


def mel_filterbank(sr: int, n_fft: int, n_mels: int,
                   fmin: float = 20.0, fmax: float = None) -> np.ndarray:
    """构建 Mel 三角滤波器组，返回 (n_mels, n_freqs)"""
    if fmax is None:
        fmax = sr / 2
    n_freqs = n_fft // 2 + 1
    freqs = np.linspace(0, sr / 2, n_freqs)

    mel_pts = np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2)
    hz_pts = mel_to_hz(mel_pts)                       # n_mels + 2 个频点
    bin_pts = np.floor((n_fft + 1) * hz_pts / sr).astype(int)
    bin_pts = np.clip(bin_pts, 0, n_freqs - 1)

    fb = np.zeros((n_mels, n_freqs), dtype=np.float32)
    for m in range(n_mels):
        left, center, right = bin_pts[m], bin_pts[m + 1], bin_pts[m + 2]
        if center <= left:
            center = left + 1
        if right <= center:
            right = center + 1
        up = np.arange(left, center)
        down = np.arange(center, right)
        fb[m, up] = (up - left) / (center - left)
        fb[m, down] = (right - down) / (right - center)
    return fb


def mel_spectrogram(y: np.ndarray, sr: int, n_fft: int = 2048,
                    hop_length: int = 512, n_mels: int = 128,
                    fmin: float = 20.0) -> np.ndarray:
    """log-Mel 频谱 (dB)，返回 (n_mels, n_frames)"""
    power = stft_power(y, n_fft, hop_length)
    fb = mel_filterbank(sr, n_fft, n_mels, fmin)
    mel_power = fb @ power.T
    log_mel = 10.0 * np.log10(mel_power + 1e-10)
    return log_mel


# ---------------------------------------------------------------- 绘图

def plot_mel(log_mel: np.ndarray, sr: int, hop_length: int,
             out_path: str, title: str = "Mel Spectrogram"):
    n_mels, n_frames = log_mel.shape
    duration = n_frames * hop_length / sr
    fig, ax = plt.subplots(figsize=(12, 5), dpi=120)
    img = ax.imshow(
        log_mel, origin="lower", aspect="auto", cmap="magma",
        extent=[0, duration, 0, n_mels],
    )
    # Y 轴刻度换算成 Hz（Mel bin -> Hz）
    mel_bins = np.linspace(0, n_mels, 6)
    hz_ticks = mel_to_hz(mel_bins / n_mels * hz_to_mel(sr / 2))
    ax.set_yticks(mel_bins)
    ax.set_yticklabels([f"{h:.0f}" for h in hz_ticks])
    ax.set_ylabel("Frequency (Hz)")
    ax.set_xlabel("Time (s)")
    ax.set_title(title)
    fig.colorbar(img, ax=ax, format="%+2.0f dB", label="Magnitude (dB)")
    fig.tight_layout()
    fig.savefig(out_path)
    print(f"[plot] 已保存: {out_path}")


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="加载音频并绘制 Mel 频谱图")
    ap.add_argument("input", nargs="?", default=None,
                    help="WAV 文件路径；缺省时自动合成 scores/bach_prelude_c_major.xml")
    ap.add_argument("-o", "--output", default="mel_spectrogram.png", help="输出图片路径")
    ap.add_argument("-n", "--n-mels", type=int, default=128, help="Mel 频带数")
    ap.add_argument("--n-fft", type=int, default=2048, help="FFT 窗口大小")
    ap.add_argument("--hop", type=int, default=512, help="帧移")
    args = ap.parse_args()

    if args.input:
        y, sr = load_wav(args.input)
        title = f"Mel Spectrogram - {os.path.basename(args.input)}"
    else:
        default_score = os.path.join("scores", "bach_prelude_c_major.xml")
        y, sr = synthesize_from_musicxml(default_score)
        title = "Mel Spectrogram - Bach Prelude in C Major"

    log_mel = mel_spectrogram(y, sr, n_fft=args.n_fft,
                              hop_length=args.hop, n_mels=args.n_mels)
    print(f"[mel] log-Mel 频谱形状: {log_mel.shape} "
          f"({log_mel.shape[1] * args.hop / sr:.1f} 秒)")
    plot_mel(log_mel, sr, args.hop, args.output, title)


if __name__ == "__main__":
    main()
