"""渲染增强引擎 —— 确定性、可复现、可审计（soundfont / 混响 / 力度）。

设计原则（契合护城河：确定性内核 + 规则校验 + 可复现 + 可审计）：
- 全部处理落在「表演层」：只动音色选择、空间混响、力度曲线，**绝不改动
  符号谱的音高 / 时值 / 和声正确性**（与 groove 同属 performance layer）。
- 渲染确定：相同 MIDI + 相同 SoundFont + 相同 preset → 逐样本一致
  （FluidSynth 二进制渲染本身确定性；混响用 seed 固定的合成脉冲响应）。
- 零幻觉：所有音色 / 混响 / 力度都是显式规则，不调 LLM。

后端策略（自动选择，降级优雅）：
1. python `fluidsynth` 包可用 → 直接用 `pm.fluidsynth()`。
2. 否则用系统 FluidSynth **二进制**（`/opt/homebrew/bin/fluidsynth` 等）渲染 WAV
   再读回 float —— 本仓库已带 `soundfonts/SalamanderGrandPiano.sf2` 与 GM 候选。
3. 二者皆无 → 返回 None（上层降级为只出 MIDI，不崩）。

用法示例：
    import render_enhance as R
    info = R.render_to_wav('scores/llm_compose.mid', 'scores/demo.wav',
                           preset=R.preset(piano=True, reverb=0.3, velocity='soft'))
    print(R.describe(R.preset(piano=True, reverb=0.3, velocity='soft')))
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import wave
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple

import numpy as np

try:
    import pretty_midi
except Exception:  # pragma: no cover
    pretty_midi = None  # 仅当输入需要解析 MIDI 时才需要；纯 audio 路径不依赖


_ROOT = os.path.dirname(os.path.abspath(__file__))

# 最近一次 fluidsynth 二进制失败的尾部诊断（供上层归因；默认 None）
_LAST_BINARY_ERROR: Optional[str] = None
_SOUNDFONT_DIR = os.path.join(_ROOT, 'soundfonts')

# 候选 SoundFont（与 score_studio 保持一致）：纯钢琴优先 Salamander，
# GM 全乐器用于其它编制。用绝对路径（锚定仓库根），不依赖运行时 cwd。
PIANO_SOUNDFONTS = [os.path.join(_SOUNDFONT_DIR, 'SalamanderGrandPiano.sf2')]
GM_SOUNDFONTS = [
    # 当前默认：Timbres of Heaven 4.0（最细腻/最丰满，419MB，4 库综合对比胜出）
    os.path.join(_SOUNDFONT_DIR, 'Timbres_of_Heaven.sf2'),
    # 历史/回退默认：GeneralUser GS（免费 GM，混音口碑佳，轻量）
    os.path.join(_SOUNDFONT_DIR, 'GeneralUser-GS.sf2'),
    # 历史默认 / 回退：MuseScore_General.sf3（旧 headless 产物音色，可用 soundfont='sf3' 复现）
    os.path.join(_SOUNDFONT_DIR, 'MuseScore_General.sf3'),
    # 其它免费高质量 GM 库（可选）
    os.path.join(_SOUNDFONT_DIR, 'FluidR3_GM.sf2'),
    # 历史占位（通常不存在，自动跳过）
    os.path.join(_SOUNDFONT_DIR, 'MuseScore_General.sf2'),
]

# 音色库别名 → 绝对路径（供 preset(soundfont='generaluser') 之类的便捷选择）
_SOUNDFONT_ALIASES = {
    'default':    os.path.join(_SOUNDFONT_DIR, 'Timbres_of_Heaven.sf2'),
    'sf3':        os.path.join(_SOUNDFONT_DIR, 'MuseScore_General.sf3'),
    'musescore':  os.path.join(_SOUNDFONT_DIR, 'MuseScore_General.sf3'),
    'generaluser': os.path.join(_SOUNDFONT_DIR, 'GeneralUser-GS.sf2'),
    'toh':        os.path.join(_SOUNDFONT_DIR, 'Timbres_of_Heaven.sf2'),
    'timbres':    os.path.join(_SOUNDFONT_DIR, 'Timbres_of_Heaven.sf2'),
    'fluidr3':    os.path.join(_SOUNDFONT_DIR, 'FluidR3_GM.sf2'),
    'salamander': os.path.join(_SOUNDFONT_DIR, 'SalamanderGrandPiano.sf2'),
}

# FluidSynth 二进制候选（按优先级）
_BINARY_CANDIDATES = [
    '/opt/homebrew/bin/fluidsynth',
    '/usr/local/bin/fluidsynth',
    '/usr/bin/fluidsynth',
    'fluidsynth',
]


# ---------------------------------------------------------------------------
# 预设（RenderPreset）
# ---------------------------------------------------------------------------
@dataclass
class RenderPreset:
    """渲染预设：音色 + 混响 + 力度，完全确定性。"""
    soundfont: Optional[str] = None          # None = 自动选择
    piano: bool = False                       # 强制钢琴采样音色（优先 Salamander）
    sample_rate: int = 44100
    # 混响
    reverb_on: bool = True
    reverb_wet: float = 0.22                  # 湿声比例 0..0.9
    reverb_tail: float = 1.6                  # 尾音秒数
    reverb_seed: int = 42
    # 力度
    velocity_scale: float = 1.0               # 全局力度缩放（<=2.0）
    velocity_curve: float = 1.0               # 指数曲线：<1 偏柔，>1 偏硬
    velocity_accent: float = 0.0              # 强拍(beat1)额外力度 0..0.4
    velocity_preset: str = 'natural'          # 友好别名

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'RenderPreset':
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)


# 友好力度预设（确定性映射，可审计）
VELOCITY_PRESETS: Dict[str, Dict[str, float]] = {
    'natural': dict(velocity_scale=1.0, velocity_curve=1.0, velocity_accent=0.0),
    'soft':    dict(velocity_scale=0.82, velocity_curve=0.8, velocity_accent=0.0),
    'hard':    dict(velocity_scale=1.18, velocity_curve=1.25, velocity_accent=0.0),
    'flat':    dict(velocity_scale=0.9, velocity_curve=0.0, velocity_accent=0.0),
    'accent':  dict(velocity_scale=1.0, velocity_curve=1.0, velocity_accent=0.22),
    'pianissimo': dict(velocity_scale=0.62, velocity_curve=0.75, velocity_accent=0.0),
    'fortissimo': dict(velocity_scale=1.35, velocity_curve=1.4, velocity_accent=0.1),
}


def preset(*, piano: bool = False, reverb: Optional[float] = None,
           velocity: str = 'natural', soundfont: Optional[str] = None,
           sample_rate: int = 44100) -> RenderPreset:
    """便捷构造预设。

    reverb: 0..0.9 直接设 wet（None 保持默认 0.22）；传 0 表示关混响。
    velocity: 友好别名（natural/soft/hard/flat/accent/pianissimo/fortissimo）。
    """
    p = RenderPreset(piano=piano, soundfont=soundfont, sample_rate=sample_rate)
    if reverb is not None:
        p.reverb_on = reverb > 0.001
        p.reverb_wet = max(0.0, min(0.9, float(reverb)))
    vp = VELOCITY_PRESETS.get(velocity, VELOCITY_PRESETS['natural'])
    p.velocity_preset = velocity
    p.velocity_scale = vp['velocity_scale']
    p.velocity_curve = vp['velocity_curve']
    p.velocity_accent = vp['velocity_accent']
    return p


# ---------------------------------------------------------------------------
# SoundFont / 后端 探测
# ---------------------------------------------------------------------------
def list_soundfonts() -> List[str]:
    """扫描 soundfonts/ 目录下可用的 .sf2/.sf3（返回相对仓库根的路径）。"""
    out: List[str] = []
    if os.path.isdir(_SOUNDFONT_DIR):
        for fn in sorted(os.listdir(_SOUNDFONT_DIR)):
            if fn.lower().endswith(('.sf2', '.sf3')):
                out.append(os.path.join('soundfonts', fn))
    return out


def detect_backend() -> str:
    """返回 'python'（py fluidsynth 包）| 'binary'（系统 fluidsynth）| 'none'。"""
    if pretty_midi is not None:
        try:
            import fluidsynth  # type: ignore  # noqa: F401
            return 'python'
        except Exception:
            pass
    if _find_binary():
        return 'binary'
    return 'none'


def _find_binary() -> Optional[str]:
    for b in _BINARY_CANDIDATES:
        if b == 'fluidsynth':
            # 依赖 PATH
            from shutil import which
            if which('fluidsynth'):
                return 'fluidsynth'
            continue
        if os.path.exists(b):
            return b
    return None


def _resolve_soundfont_alias(name: str) -> str:
    """按别名选择音色库（'generaluser'/'fluidr3'/'sf3'/...）；非别名原样返回。"""
    return _SOUNDFONT_ALIASES.get(name, name)


def _pick_soundfont(p: RenderPreset) -> Optional[str]:
    """按预设选择 SoundFont 绝对路径；不存在则回退到任一可用候选。"""
    def _resolve(c: str) -> str:
        # 用户传入的相对路径按仓库根解析，避免依赖运行时 cwd
        return c if os.path.isabs(c) else os.path.join(_ROOT, c)

    cands: List[str] = []
    if p.soundfont:
        cands.append(_resolve_soundfont_alias(p.soundfont))
    if p.piano:
        cands.extend([c for c in PIANO_SOUNDFONTS if os.path.exists(_resolve(c))])
    cands.extend([c for c in GM_SOUNDFONTS if os.path.exists(_resolve(c))])
    cands.extend([c for c in PIANO_SOUNDFONTS if os.path.exists(_resolve(c))])
    for c in cands:
        rc = _resolve(c)
        if c and os.path.exists(rc):
            return rc
    # 最后兜底：任意存在的 soundfonts/*
    for c in list_soundfonts():
        rc = _resolve(c)
        if os.path.exists(rc):
            return rc
    return None


# ---------------------------------------------------------------------------
# 核心：卷积混响（numpy 实现，避免依赖 scipy；seed 固定→确定性）
# ---------------------------------------------------------------------------
def _fftconvolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    n = len(x) + len(ir) - 1
    nfft = 1 << (n - 1).bit_length()
    X = np.fft.rfft(x, nfft)
    H = np.fft.rfft(ir, nfft)
    return np.fft.irfft(X * H, nfft)[:n]


def apply_reverb(audio: np.ndarray, sr: int = 44100, wet: float = 0.22,
                 tail: float = 1.6, seed: int = 42) -> np.ndarray:
    """轻量卷积混响：给干声加空间感，显著降低「MIDI 味」。确定性。"""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0 or wet <= 0.001:
        return audio
    peak_in = float(np.max(np.abs(audio)))
    if peak_in <= 1e-9:
        return audio
    ir_len = max(int(sr * tail), 64)
    rng = np.random.default_rng(seed)
    t = np.arange(ir_len, dtype=np.float32) / float(sr)
    # 指数衰减噪声脉冲响应（模拟房间反射尾音）
    ir = (rng.standard_normal(ir_len).astype(np.float32)
          * np.exp(-t * 3.2).astype(np.float32))
    pre = max(int(sr * 0.012), 1)
    ir[:pre] = 0.0
    ir /= (float(np.max(np.abs(ir))) + 1e-9)
    wet_sig = _fftconvolve(audio, ir)[: len(audio)].astype(np.float32)
    wet_peak = float(np.max(np.abs(wet_sig)))
    if wet_peak > 1e-9:
        wet_sig = wet_sig / wet_peak * peak_in
    out = audio * (1.0 - wet) + wet_sig * wet
    out_peak = float(np.max(np.abs(out)))
    if out_peak > 0.999:
        out = out / out_peak * 0.999
    return out.astype(np.float32)


# ---------------------------------------------------------------------------
# 力度曲线（确定性；仅动表演层力度，不改音高/时值/和声）
# ---------------------------------------------------------------------------
def apply_velocity_curve(pm, p: RenderPreset, seed: int = 0) -> Any:
    """返回力度被重塑的新 PrettyMIDI（原对象不动）。

    - velocity_scale：整体缩放（<=2.0）。
    - velocity_curve：指数曲线，<1 压低强音/抬弱音（更柔），>1 反向。
      为 0 时趋近常数（flat 预设）。
    - velocity_accent：在小节第 1 拍额外加力（确定性：按 note.start 落在 beat1）。
    """
    if pretty_midi is None:
        return pm
    new = pretty_midi.PrettyMIDI()
    new.resolution = getattr(pm, 'resolution', 480)
    rng = np.random.default_rng(seed)
    # 真实拍长（秒/拍）：按 MIDI tempo 换算，note.start(秒) 除以它得拍位
    tempo = 120.0
    try:
        tcs = pm.get_tempo_changes()
        if len(tcs[1]):
            tempo = float(tcs[1][0])
    except Exception:
        pass
    if tempo <= 0:
        tempo = 120.0
    beat_dur = 60.0 / tempo
    for inst in pm.instruments:
        ni = pretty_midi.Instrument(program=inst.program, is_drum=inst.is_drum,
                                    name=inst.name)
        for note in inst.notes:
            v = float(note.velocity)
            # 归一化到 0..1（MIDI 力度 1..127）
            vn = max(0.0, min(1.0, (v - 1) / 126.0))
            if p.velocity_curve > 0.001:
                vn = vn ** p.velocity_curve
            else:
                # flat：拉向均值 0.6
                vn = 0.6 + (vn - 0.6) * 0.15
            vn *= p.velocity_scale
            # 强拍加力（beat1：start 落在整数拍的起点）
            if p.velocity_accent > 0.0:
                beat_phase = (note.start / beat_dur) % 1.0
                # 以 4/4 默认；落在拍首 ±0.02 视为强拍
                if beat_phase < 0.02 or beat_phase > 0.98:
                    vn += p.velocity_accent
            vout = int(max(1, min(127, round(vn * 126.0 + 1))))
            ni.notes.append(pretty_midi.Note(
                velocity=vout, pitch=note.pitch,
                start=note.start, end=note.end))
        ni.control_changes = list(inst.control_changes)
        new.instruments.append(ni)
    return new


# ---------------------------------------------------------------------------
# 渲染后端
# ---------------------------------------------------------------------------
def _render_via_python(pm, sf2: str, sr: int) -> Optional[np.ndarray]:
    try:
        audio = pm.fluidsynth(sf2_path=sf2, fs=sr)
        if audio is None or len(audio) == 0:
            return None
        return np.asarray(audio, dtype=np.float32)
    except Exception:
        return None


def _render_via_binary(midi_path: str, sf2: str, sr: int,
                       bin_path: str) -> Optional[Tuple[np.ndarray, int]]:
    """用系统 fluidsynth 二进制渲染为 WAV，再读回 float。确定性。"""
    global _LAST_BINARY_ERROR
    import tempfile
    fd, wav_tmp = tempfile.mkstemp(suffix='.wav', prefix='fluid_')
    os.close(fd)
    try:
        cmd = [bin_path, '-ni', '-r', str(sr), '-g', '0.9',
               '-F', wav_tmp, '-T', 'wav', sf2, midi_path]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=300, check=False, text=True)
        if r.returncode != 0:
            _LAST_BINARY_ERROR = (r.stderr or r.stdout or '')[-400:]
            return None
        if not os.path.exists(wav_tmp) or os.path.getsize(wav_tmp) == 0:
            _LAST_BINARY_ERROR = 'fluidsynth 退出码 0 但未产出 WAV'
            return None
        return _read_wav_to_float(wav_tmp)
    except Exception as e:
        _LAST_BINARY_ERROR = str(e)
        return None
    finally:
        try:
            os.remove(wav_tmp)
        except Exception:
            pass


def _read_wav_to_float(path: str) -> Optional[Tuple[np.ndarray, int]]:
    try:
        with wave.open(path, 'rb') as wf:
            n = wf.getnframes()
            raw = wf.readframes(n)
            ch = wf.getnchannels()
            sr = wf.getframerate()
            width = wf.getsampwidth()
        if width == 2:
            sig = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif width == 4:
            # fluidsynth 可能输出 32-bit float 或 32-bit int PCM
            as_f = np.frombuffer(raw, dtype=np.float32)
            if np.max(np.abs(as_f)) <= 8.0:
                sig = as_f.astype(np.float32)
            else:
                sig = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif width == 3:
            # 24-bit PCM：逐样本拼成 int32 再归一化
            a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
            v = (a[:, 0].astype(np.int32)
                 | (a[:, 1].astype(np.int32) << 8)
                 | (a[:, 2].astype(np.int32) << 16))
            v = np.where(v >= 0x800000, v - 0x1000000, v)
            sig = v.astype(np.float32) / 8388608.0
        else:
            sig = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            sig = sig.reshape(-1, ch).mean(axis=1)
        return sig.astype(np.float32), sr
    except Exception:
        return None


def render_raw(midi_path: str, p: RenderPreset,
               backend: Optional[str] = None) -> Optional[Tuple[np.ndarray, int]]:
    """渲染 MIDI → (float音频, 采样率)；失败返回 None。"""
    if not os.path.exists(midi_path):
        return None
    if pretty_midi is None:
        return None
    try:
        pm = pretty_midi.PrettyMIDI(midi_path)
    except Exception:
        return None
    # 力度曲线（表演层）
    if p.velocity_scale != 1.0 or p.velocity_curve != 1.0 or p.velocity_accent > 0:
        pm = apply_velocity_curve(pm, p)
    sf2 = _pick_soundfont(p)
    if not sf2:
        return None
    be = backend or detect_backend()
    audio: Optional[np.ndarray] = None
    if be == 'python':
        audio = _render_via_python(pm, sf2, p.sample_rate)
    if audio is None and be in ('binary', 'python'):
        binp = _find_binary()
        if binp:
            res = _render_via_binary(midi_path, sf2, p.sample_rate, binp)
            if res is not None:
                audio, _sr = res
    if audio is None:
        return None
    if p.reverb_on:
        audio = apply_reverb(audio, p.sample_rate, p.reverb_wet,
                             p.reverb_tail, p.reverb_seed)
    return audio, p.sample_rate


# ---------------------------------------------------------------------------
# 写出 WAV（16-bit PCM）
# ---------------------------------------------------------------------------
def write_wav(path: str, audio: np.ndarray, sr: int) -> str:
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1.0:
        audio = audio / peak * 0.99
    pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sr))
        wf.writeframes(pcm.tobytes())
    return path


def render_to_wav(midi_path: str, wav_path: str, p: Optional[RenderPreset] = None,
                  seed: Optional[int] = None) -> Dict[str, Any]:
    """完整管线：MIDI → 力度 → 合成 → 混响 → 写 WAV。返回信息字典（可审计）。"""
    p = p or RenderPreset()
    info: Dict[str, Any] = {
        'midi': midi_path, 'wav': wav_path, 'backend': detect_backend(),
        'soundfont': _pick_soundfont(p), 'preset': p.to_dict(),
        'ok': False, 'samples': 0, 'duration': 0.0, 'error': None,
    }
    try:
        res = render_raw(midi_path, p)
        if res is None:
            info['error'] = '渲染失败：缺少可用 SoundFont 或合成后端'
            return info
        audio, sr = res
        write_wav(wav_path, audio, sr)
        info['ok'] = True
        info['samples'] = int(len(audio))
        info['duration'] = round(len(audio) / float(sr), 3)
        info['sample_rate'] = sr
    except Exception as e:  # pragma: no cover
        info['error'] = f'{type(e).__name__}: {e}'
    return info


# ---------------------------------------------------------------------------
# 友好描述（透明 / 可审计）
# ---------------------------------------------------------------------------
def describe(p: RenderPreset) -> str:
    sf = _pick_soundfont(p) or '（自动）'
    sf_name = os.path.basename(sf)
    rev = f'混响 {"关" if not p.reverb_on else f"开(wet={p.reverb_wet}, 尾音={p.reverb_tail}s)"}'
    vel = (f'力度={p.velocity_preset}'
           f'(scale={p.velocity_scale}, curve={p.velocity_curve}, '
           f'强拍+{p.velocity_accent})')
    return (f'音色={sf_name}{" [钢琴采样]" if p.piano else ""} ｜ '
            f'{rev} ｜ {vel} ｜ {p.sample_rate}Hz')


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def _selftest() -> int:
    fail = 0
    def check(name, cond, extra=''):
        nonlocal fail
        if cond:
            print(f'  ✓ {name}')
        else:
            fail += 1
            print(f'  ✗ {name} {extra}')

    print('[render_enhance] 自检')
    # 预设构造
    p = preset(piano=True, reverb=0.3, velocity='soft')
    check('preset piano+reverb0.3+soft', p.piano and p.reverb_wet == 0.3
          and p.velocity_preset == 'soft')
    p2 = preset(reverb=0)
    check('reverb=0 关闭混响', (not p2.reverb_on) and p2.reverb_wet == 0.0)
    # 混响确定性：同输入同输出
    rng = np.random.default_rng(0)
    a = rng.standard_normal(2000).astype(np.float32)
    r1 = apply_reverb(a, 44100, 0.3, 1.0, seed=7)
    r2 = apply_reverb(a, 44100, 0.3, 1.0, seed=7)
    check('混响确定性(同seed同输出)', np.array_equal(r1, r2))
    check('混响不改变长度', len(r1) == len(a))
    check('混响 wet=0 原样返回', np.array_equal(apply_reverb(a, 44100, 0.0, 1.0), a))
    # 力度曲线边界
    if pretty_midi is not None:
        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=0)
        inst.notes.append(pretty_midi.Note(velocity=100, pitch=60, start=0.0, end=0.5))
        inst.notes.append(pretty_midi.Note(velocity=40, pitch=64, start=0.5, end=1.0))
        pm.instruments.append(inst)
        psoft = preset(velocity='soft')
        npm = apply_velocity_curve(pm, psoft)
        vs = [n.velocity for n in npm.instruments[0].notes]
        check('力度曲线输出在 1..127', all(1 <= v <= 127 for v in vs), str(vs))
        check('soft 预设压缩力度动态', max(vs) - min(vs) <= max(
            n.velocity for n in pm.instruments[0].notes) - min(
            n.velocity for n in pm.instruments[0].notes) + 1, str(vs))
        # 不改音高/时值
        for n0, n1 in zip(pm.instruments[0].notes, npm.instruments[0].notes):
            check('力度曲线不改音高', n0.pitch == n1.pitch)
            check('力度曲线不改时值', abs(n0.start - n1.start) < 1e-9
                  and abs(n0.end - n1.end) < 1e-9)
    # 后端探测
    check('后端探测非 none 或 graceful', detect_backend() in ('python', 'binary', 'none'))
    # describe 不报错
    check('describe 返回字符串', isinstance(describe(p), str))
    # 写 WAV（合成小段）
    if pretty_midi is not None and detect_backend() != 'none':
        import tempfile
        fd, mp = tempfile.mkstemp(suffix='.mid', prefix='rt_')
        os.close(fd)
        pm.write(mp)
        fd, wp = tempfile.mkstemp(suffix='.wav', prefix='rt_')
        os.close(fd)
        info = render_to_wav(mp, wp, preset(reverb=0.2, velocity='natural'))
        check('render_to_wav 成功', info['ok'], str(info))
        check('WAV 文件存在', os.path.exists(wp) and os.path.getsize(wp) > 0)
        os.remove(mp)
        os.remove(wp)
    else:
        print('  ⚠ 跳过真实渲染（无可用后端/无 pretty_midi，非错误）')
    return fail


if __name__ == '__main__':
    sys.exit(1 if _selftest() else 0)
