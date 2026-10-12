"""
Audio engine using pure software synthesis.
No FluidSynth, no MIDI hardware, no external DLLs.
Auto-detects instrument from score metadata.
"""
import threading
import time
import os
import sys
import math
from typing import List, Optional, Dict
from dataclasses import dataclass

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False


@dataclass
class InstrumentConfig:
    """乐器音色配置"""
    name: str
    harmonics: List[float]
    attack: float = 0.02
    decay: float = 0.1
    sustain: float = 0.7
    release: float = 0.3
    brightness: float = 1.0
    volume: float = 1.0


class AudioEngine:
    """
    纯软件合成器音频引擎
    自动检测乐器类型，无需手动选择
    """

    SAMPLE_RATE = 44100

    # 乐器音色配置
    ORCHESTRA_PRESETS: Dict[str, InstrumentConfig] = {
        "piano": InstrumentConfig(
            "钢琴", [1.0, 0.6, 0.4, 0.3, 0.2, 0.15, 0.1],
            attack=0.005, decay=0.3, sustain=0.4, release=0.4,
            brightness=1.0, volume=0.9
        ),
        "violin": InstrumentConfig(
            "小提琴", [1.0, 0.7, 0.5, 0.4, 0.2, 0.1, 0.05],
            attack=0.08, decay=0.15, sustain=0.8, release=0.3,
            brightness=1.2, volume=0.85
        ),
        "viola": InstrumentConfig(
            "中提琴", [1.0, 0.6, 0.4, 0.3, 0.15, 0.08, 0.04],
            attack=0.1, decay=0.2, sustain=0.75, release=0.35,
            brightness=0.9, volume=0.8
        ),
        "cello": InstrumentConfig(
            "大提琴", [1.0, 0.5, 0.3, 0.2, 0.1, 0.05, 0.02],
            attack=0.12, decay=0.25, sustain=0.8, release=0.4,
            brightness=0.7, volume=0.9
        ),
        "contrabass": InstrumentConfig(
            "低音提琴", [1.0, 0.4, 0.2, 0.1, 0.05, 0.02, 0.01],
            attack=0.15, decay=0.3, sustain=0.8, release=0.5,
            brightness=0.5, volume=0.85
        ),
        "strings": InstrumentConfig(
            "弦乐合奏", [1.0, 0.65, 0.45, 0.35, 0.2, 0.12, 0.08],
            attack=0.1, decay=0.2, sustain=0.85, release=0.35,
            brightness=1.0, volume=0.9
        ),
        "strings_slow": InstrumentConfig(
            "慢速弦乐", [1.0, 0.5, 0.3, 0.2, 0.1, 0.05, 0.03],
            attack=0.2, decay=0.3, sustain=0.9, release=0.5,
            brightness=0.8, volume=0.85
        ),
        "flute": InstrumentConfig(
            "长笛", [1.0, 0.2, 0.1, 0.05, 0.02, 0.01, 0.0],
            attack=0.06, decay=0.1, sustain=0.85, release=0.2,
            brightness=1.3, volume=0.75
        ),
        "piccolo": InstrumentConfig(
            "短笛", [1.0, 0.3, 0.15, 0.08, 0.04, 0.02, 0.0],
            attack=0.05, decay=0.08, sustain=0.85, release=0.15,
            brightness=1.5, volume=0.7
        ),
        "oboe": InstrumentConfig(
            "双簧管", [1.0, 0.1, 0.6, 0.05, 0.3, 0.02, 0.15],
            attack=0.08, decay=0.15, sustain=0.8, release=0.3,
            brightness=1.0, volume=0.8
        ),
        "clarinet": InstrumentConfig(
            "单簧管", [1.0, 0.05, 0.5, 0.03, 0.25, 0.02, 0.1],
            attack=0.07, decay=0.12, sustain=0.85, release=0.25,
            brightness=0.9, volume=0.8
        ),
        "bassoon": InstrumentConfig(
            "巴松", [1.0, 0.3, 0.2, 0.15, 0.1, 0.05, 0.02],
            attack=0.12, decay=0.2, sustain=0.8, release=0.4,
            brightness=0.6, volume=0.8
        ),
        "trumpet": InstrumentConfig(
            "小号", [1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2],
            attack=0.03, decay=0.1, sustain=0.85, release=0.2,
            brightness=1.4, volume=0.9
        ),
        "trombone": InstrumentConfig(
            "长号", [1.0, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1],
            attack=0.05, decay=0.12, sustain=0.85, release=0.25,
            brightness=1.1, volume=0.85
        ),
        "horn": InstrumentConfig(
            "圆号", [1.0, 0.5, 0.4, 0.3, 0.2, 0.15, 0.1],
            attack=0.1, decay=0.2, sustain=0.9, release=0.35,
            brightness=0.8, volume=0.85
        ),
        "tuba": InstrumentConfig(
            "大号", [1.0, 0.4, 0.25, 0.15, 0.08, 0.04, 0.02],
            attack=0.15, decay=0.25, sustain=0.85, release=0.4,
            brightness=0.5, volume=0.8
        ),
        "harp": InstrumentConfig(
            "竖琴", [1.0, 0.5, 0.3, 0.2, 0.1, 0.05, 0.02],
            attack=0.005, decay=0.4, sustain=0.3, release=0.5,
            brightness=1.3, volume=0.8
        ),
        "timpani": InstrumentConfig(
            "定音鼓", [1.0, 0.3, 0.5, 0.2, 0.4, 0.15, 0.1],
            attack=0.005, decay=0.3, sustain=0.2, release=0.4,
            brightness=0.8, volume=0.9
        ),
        "choir": InstrumentConfig(
            "合唱", [1.0, 0.4, 0.3, 0.2, 0.15, 0.1, 0.08],
            attack=0.3, decay=0.3, sustain=0.9, release=0.5,
            brightness=0.7, volume=0.85
        ),
        "harpsichord": InstrumentConfig(
            "羽管键琴", [1.0, 0.7, 0.5, 0.4, 0.3, 0.2, 0.15],
            attack=0.005, decay=0.2, sustain=0.5, release=0.3,
            brightness=1.5, volume=0.7
        ),
        "glockenspiel": InstrumentConfig(
            "钟琴", [1.0, 0.6, 0.4, 0.3, 0.2, 0.15, 0.1],
            attack=0.005, decay=0.5, sustain=0.2, release=0.4,
            brightness=2.0, volume=0.7
        ),
        "marimba": InstrumentConfig(
            "木琴", [1.0, 0.5, 0.3, 0.2, 0.1, 0.05, 0.02],
            attack=0.005, decay=0.4, sustain=0.3, release=0.3,
            brightness=1.0, volume=0.75
        ),
    }

    def __init__(self):
        self._ready = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._play_thread = None
        self._channels: Dict[int, str] = {}
        self._audio_buffer = []
        self._buffer_lock = threading.Lock()

    def initialize(self) -> bool:
        """初始化音频引擎"""
        if not HAS_NUMPY:
            print("[AudioEngine] ❌ 未安装 numpy")
            return False

        if not HAS_SOUNDDEVICE:
            print("[AudioEngine] ❌ 未安装 sounddevice")
            return False

        try:
            # 测试音频输出
            test = np.zeros(1000, dtype=np.float32)
            sd.play(test, self.SAMPLE_RATE)
            sd.stop()

            self._ready = True
            print("[AudioEngine] ✅ 软件合成器初始化成功")
            return True

        except Exception as e:
            print(f"[AudioEngine] ❌ 音频设备初始化失败: {e}")
            return False

    def is_available(self) -> bool:
        return self._ready

    def _detect_instrument(self, score) -> str:
        """
        自动检测乐器类型
        从 score 的元数据或 part-name 判断
        """
        # 1. 检查 score 的 instrument 字段
        if hasattr(score, 'instrument') and score.instrument:
            inst = score.instrument.lower()
            if 'piano' in inst or 'pno' in inst:
                return "piano"
            if 'violin' in inst:
                return "violin"
            if 'cello' in inst:
                return "cello"
            if 'flute' in inst:
                return "flute"
            if 'trumpet' in inst:
                return "trumpet"
            if 'horn' in inst:
                return "horn"
            if 'harp' in inst:
                return "harp"

        # 2. 检查 staves 数量推断
        n_staves = len(getattr(score, 'staves', []))
        if n_staves >= 4:
            return "orchestra"  # 多 staff 默认管弦乐
        if n_staves == 2:
            # 双 staff 可能是钢琴（高音+低音谱号）
            return "piano"

        # 3. 检查 title/composer 中的关键词
        title = getattr(score, 'title', '').lower()
        composer = getattr(score, 'composer', '').lower()

        piano_keywords = ['piano', 'sonata', 'nocturne', 'étude', 'prelude',
                          'waltz', 'mazurka', 'polonaise', 'ballade', 'scherzo',
                          'fugue', 'invention', 'toccat']
        for kw in piano_keywords:
            if kw in title or kw in composer:
                return "piano"

        string_keywords = ['string', 'quartet', 'quintet', 'trio', 'violin', 'cello']
        for kw in string_keywords:
            if kw in title or kw in composer:
                return "quartet"

        orch_keywords = ['symphony', 'overture', 'concerto', 'suite', 'orchestra']
        for kw in orch_keywords:
            if kw in title or kw in composer:
                return "orchestra"

        # 默认钢琴
        return "piano"

    def set_instrument(self, name: str, channel: int = 0):
        """设置通道乐器"""
        if name in self.ORCHESTRA_PRESETS:
            self._channels[channel] = name
            print(f"[AudioEngine] 通道 {channel}: {self.ORCHESTRA_PRESETS[name].name}")

    def _freq_from_midi(self, midi_note: int) -> float:
        """MIDI 音符转频率"""
        return 440.0 * (2 ** ((midi_note - 69) / 12.0))

    def _generate_wave(self, freq: float, duration: float,
                       config: InstrumentConfig, velocity: int = 80) -> np.ndarray:
        """生成乐器波形 - 修复版本"""
        samples = max(int(self.SAMPLE_RATE * duration), 100)  # 最少100样本
        t = np.linspace(0, duration, samples, False)

        # 生成泛音
        wave = np.zeros(samples)
        for i, amp in enumerate(config.harmonics):
            if amp > 0:
                harmonic_freq = freq * (i + 1)
                high_freq_damp = 1.0 / (1.0 + (harmonic_freq / 8000) ** 2)
                wave += amp * high_freq_damp * np.sin(2 * np.pi * harmonic_freq * t)

        # ADSR 包络 - 安全版本
        total = len(wave)
        attack_s = min(int(config.attack * self.SAMPLE_RATE), total)
        decay_s = min(int(config.decay * self.SAMPLE_RATE), total - attack_s)
        release_s = min(int(config.release * self.SAMPLE_RATE), total - attack_s - decay_s)
        sustain_s = total - attack_s - decay_s - release_s

        envelope = np.zeros(total)
        pos = 0

        # Attack: 0 -> 1
        if attack_s > 0:
            envelope[pos:pos+attack_s] = np.linspace(0, 1, attack_s)
            pos += attack_s

        # Decay: 1 -> sustain
        if decay_s > 0:
            envelope[pos:pos+decay_s] = np.linspace(1, config.sustain, decay_s)
            pos += decay_s

        # Sustain: 保持
        if sustain_s > 0:
            envelope[pos:pos+sustain_s] = config.sustain
            pos += sustain_s

        # Release: sustain -> 0
        if release_s > 0 and pos < total:
            envelope[pos:] = np.linspace(config.sustain, 0, total - pos)

        # 应用包络
        wave *= envelope

        # 音量缩放
        wave *= config.volume * (velocity / 127.0) * 0.5  # 增大音量

        # 亮度调整
        if config.brightness > 1.0:
            wave = np.diff(wave, prepend=wave[0]) * (config.brightness - 1.0) * 0.3 + wave

        return wave.astype(np.float32)

    def play_note(self, note: int, velocity: int = 80,
                  duration: float = 0.5, channel: int = 0):
        """播放音符 - 使用阻塞方式确保声音发出"""
        if not self.is_available():
            return

        instrument = self._channels.get(channel, "piano")
        config = self.ORCHESTRA_PRESETS.get(instrument, self.ORCHESTRA_PRESETS["piano"])

        freq = self._freq_from_midi(note)
        wave = self._generate_wave(freq, duration, config, velocity)

        if len(wave) == 0:
            return

        # 立体声
        stereo = np.column_stack((wave * 0.8, wave * 0.8))

        # 播放并等待完成（阻塞，确保声音发出）
        try:
            sd.play(stereo, self.SAMPLE_RATE)
            sd.wait()  # 等待播放完成
        except Exception as e:
            print(f"[AudioEngine] 播放错误: {e}")

    def play_chord(self, notes: List[int], velocity: int = 80,
                    duration: float = 1.0, channel: int = 0):
        """播放和弦"""
        if not self.is_available():
            return

        instrument = self._channels.get(channel, "piano")
        config = self.ORCHESTRA_PRESETS.get(instrument, self.ORCHESTRA_PRESETS["piano"])

        # 生成所有音符并混合
        samples = max(int(self.SAMPLE_RATE * duration), 100)
        mixed = np.zeros(samples)

        for note in notes:
            freq = self._freq_from_midi(note)
            wave = self._generate_wave(freq, duration, config, velocity)
            if len(wave) > 0:
                # 对齐长度
                if len(wave) < samples:
                    wave = np.pad(wave, (0, samples - len(wave)))
                else:
                    wave = wave[:samples]
                mixed += wave * 0.7  # 和弦衰减

        # 防止削波
        max_val = np.max(np.abs(mixed))
        if max_val > 1.0:
            mixed = mixed / max_val * 0.9

        stereo = np.column_stack((mixed * 0.8, mixed * 0.8))

        try:
            sd.play(stereo, self.SAMPLE_RATE)
            sd.wait()
        except Exception as e:
            print(f"[AudioEngine] 和弦播放错误: {e}")

    def stop(self):
        """停止播放"""
        self._stop.set()
        if self._play_thread and self._play_thread.is_alive():
            self._play_thread.join(timeout=1)
        try:
            sd.stop()
        except:
            pass

    def close(self):
        """释放资源"""
        self.stop()
        self._ready = False

    def __del__(self):
        self.close()

    # ==================== 乐谱播放（自动检测乐器）====================

    def play_score(self, score, instrument: str = None):
        """
        播放完整乐谱
        instrument: 如果为 None，自动检测乐器
        """
        if not self.is_available():
            return

        # 自动检测乐器
        if instrument is None:
            instrument = self._detect_instrument(score)
            print(f"[AudioEngine] 自动检测乐器: {instrument}")

        self.stop()
        self._stop.clear()

        self._play_thread = threading.Thread(
            target=self._do_play_score,
            args=(score, instrument),
            daemon=True
        )
        self._play_thread.start()

    def _do_play_score(self, score, instrument: str):
        """播放线程"""
        # 配置乐器
        if instrument == "orchestra":
            self._setup_orchestra()
        elif instrument == "quartet":
            self._setup_quartet()
        elif instrument == "strings":
            self._setup_strings()
        else:
            self.set_instrument(instrument, 0)

        tempo = getattr(score, 'tempo', 120)
        if not tempo or tempo <= 0:
            tempo = 120   # 模型层无校验，tempo=0 时兜底避免除零
        spb = 60.0 / tempo

        for staff_idx, staff in enumerate(getattr(score, 'staves', [])):
            ch = min(staff_idx, 15)
            if ch == 9:
                ch = 15

            for measure in getattr(staff, 'measures', []):
                if self._stop.is_set():
                    return

                for note in getattr(measure, 'notes', []):
                    if self._stop.is_set():
                        return

                    if getattr(note, 'is_rest', False):
                        dur = note.duration_value() * spb * 4
                        time.sleep(dur)
                        continue

                    try:
                        midi = note.pitch.to_midi()
                    except:
                        continue
                    vel = getattr(note, 'velocity', 80)
                    dur = note.duration_value() * spb * 4

                    # 确保最小时长
                    dur = max(dur, 0.05)

                    self.play_note(midi, vel, dur, ch)

    def _setup_orchestra(self):
        mapping = [
            ("strings", 0), ("flute", 1), ("oboe", 2),
            ("clarinet", 3), ("trumpet", 4), ("horn", 5),
            ("trombone", 6), ("timpani", 7), ("harpsichord", 8),
        ]
        for name, ch in mapping:
            self.set_instrument(name, ch)

    def _setup_quartet(self):
        self.set_instrument("violin", 0)
        self.set_instrument("violin", 1)
        self.set_instrument("viola", 2)
        self.set_instrument("cello", 3)

    def _setup_strings(self):
        self.set_instrument("strings", 0)
        self.set_instrument("strings_slow", 1)
        self.set_instrument("violin", 2)
        self.set_instrument("cello", 3)