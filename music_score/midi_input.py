"""跨平台 MIDI 键盘输入（macOS / Windows / Linux）。

依赖：``mido`` + ``python-rtmidi``（CoreMIDI / Windows MM / ALSA）。
未安装或无设备时优雅降级，不阻塞启动。
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

_BACKEND = 'mido.backends.rtmidi'


@dataclass(frozen=True)
class MidiNoteEvent:
    """从硬件键盘收到的音符事件。"""
    midi: int
    velocity: int
    is_on: bool
    channel: int = 0
    timestamp: float = 0.0


def midi_backend_available() -> Tuple[bool, str]:
    """返回 (可用, 说明)。"""
    try:
        import mido  # noqa: F401
    except Exception as e:
        return False, f'缺少 mido：{e}（pip install mido）'
    try:
        import rtmidi  # noqa: F401
    except Exception as e:
        return False, (
            f'缺少 python-rtmidi：{e}\n'
            '请执行：pip install python-rtmidi\n'
            'macOS 还需系统授权「麦克风/MIDI」时按提示允许。'
        )
    try:
        import mido
        mido.set_backend(_BACKEND)
        # 触发后端加载
        _ = mido.get_input_names()
    except Exception as e:
        return False, f'MIDI 后端初始化失败：{e}'
    return True, 'mido + python-rtmidi（macOS CoreMIDI / Windows MM）'


def list_midi_input_ports() -> List[str]:
    """枚举可用 MIDI 输入端口名。"""
    ok, msg = midi_backend_available()
    if not ok:
        return []
    import mido
    mido.set_backend(_BACKEND)
    try:
        return list(mido.get_input_names() or [])
    except Exception:
        return []


class MidiInputService:
    """后台线程读 MIDI，主线程 ``poll()`` 取事件（Qt 友好）。"""

    def __init__(self):
        self._port = None
        self._port_name: Optional[str] = None
        self._queue: queue.Queue = queue.Queue(maxsize=2048)
        self._lock = threading.Lock()
        self._octave_offset = 0  # 半音偏移（±12 的倍数常用）
        self._enabled = False

    @property
    def port_name(self) -> Optional[str]:
        return self._port_name

    @property
    def enabled(self) -> bool:
        return self._enabled and self._port is not None

    @property
    def octave_offset(self) -> int:
        return int(self._octave_offset)

    def set_octave_offset(self, semitones: int) -> None:
        self._octave_offset = int(semitones)

    def open(self, port_name: Optional[str] = None) -> str:
        """打开指定（或第一个）输入端口。返回实际端口名。"""
        ok, msg = midi_backend_available()
        if not ok:
            raise RuntimeError(msg)
        import mido
        mido.set_backend(_BACKEND)
        names = list(mido.get_input_names() or [])
        if not names:
            raise RuntimeError(
                '未检测到 MIDI 输入设备。请连接 MIDI 键盘后重试'
                '（macOS：系统设置 → 隐私与安全性；Windows：检查驱动）。')
        target = port_name
        if target and target not in names:
            # 模糊匹配
            low = target.lower()
            hit = next((n for n in names if low in n.lower()), None)
            if hit is None:
                raise RuntimeError(
                    f'找不到端口 {target!r}。可用：{", ".join(names)}')
            target = hit
        if not target:
            target = names[0]
        self.close()
        # callback=True：mido 在后台线程投递
        port = mido.open_input(target, callback=self._on_message)
        with self._lock:
            self._port = port
            self._port_name = target
            self._enabled = True
        return target

    def close(self) -> None:
        with self._lock:
            port = self._port
            self._port = None
            self._port_name = None
            self._enabled = False
        if port is not None:
            try:
                port.close()
            except Exception:
                pass
        # 清空队列
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def _on_message(self, message) -> None:
        try:
            t = str(getattr(message, 'type', '') or '')
            if t not in ('note_on', 'note_off'):
                return
            note = int(getattr(message, 'note', 0) or 0)
            vel = int(getattr(message, 'velocity', 0) or 0)
            ch = int(getattr(message, 'channel', 0) or 0)
            is_on = (t == 'note_on' and vel > 0)
            # note_on vel=0 ≡ note_off
            if t == 'note_on' and vel == 0:
                is_on = False
            midi = max(0, min(127, note + self._octave_offset))
            ev = MidiNoteEvent(
                midi=midi, velocity=vel, is_on=is_on, channel=ch,
                timestamp=time.monotonic())
            try:
                self._queue.put_nowait(ev)
            except queue.Full:
                pass
        except Exception:
            pass

    def poll(self, max_n: int = 64) -> List[MidiNoteEvent]:
        """非阻塞取出最多 max_n 条事件。"""
        out: List[MidiNoteEvent] = []
        for _ in range(max(1, int(max_n))):
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out


class StepInputAccumulator:
    """步进输入：单音立即提交；短时多键视为和弦。

    chord_window_ms 内连续 note_on 且尚未全部抬起 → 收成和弦。
    """

    def __init__(self, chord_window_ms: float = 80.0):
        self.chord_window_ms = float(chord_window_ms)
        self._held: dict = {}  # midi -> velocity
        self._pending: List[Tuple[int, int]] = []  # (midi, vel)
        self._last_on_t = 0.0
        self.chord_mode = False  # True：等全部抬起再提交

    def reset(self) -> None:
        self._held.clear()
        self._pending.clear()
        self._last_on_t = 0.0

    def preview(self) -> List[int]:
        """已按下但尚未提交的音（高→低），供五线谱实时显示。"""
        bag = {m for m, _v in self._pending}
        if self.chord_mode:
            bag.update(self._held)
        return sorted(bag, reverse=True)

    def feed(self, ev: MidiNoteEvent
             ) -> Optional[List[Tuple[int, int]]]:
        """喂入事件；若应落笔则返回 [(midi, vel), ...]（高→低）。"""
        now = float(ev.timestamp or time.monotonic())
        if ev.is_on:
            self._held[ev.midi] = ev.velocity
            self._pending.append((ev.midi, ev.velocity))
            self._last_on_t = now
            if self.chord_mode:
                return None
            # 非和弦模式：若窗口内又有新键，暂缓；否则立即出单音
            return None
        # note off
        self._held.pop(ev.midi, None)
        if self.chord_mode:
            if self._held:
                return None
            return self._flush()
        return None

    def tick(self, now: Optional[float] = None
             ) -> Optional[List[Tuple[int, int]]]:
        """定时器调用：非和弦模式下，窗口过后提交堆积的音。"""
        if self.chord_mode:
            return None
        if not self._pending:
            return None
        t = float(now if now is not None else time.monotonic())
        if (t - self._last_on_t) * 1000.0 < self.chord_window_ms:
            return None
        # 若仍有键按着且 pending>1，等抬起或再等一拍——简化：直接提交当前 pending 的并集
        return self._flush()

    def _flush(self) -> Optional[List[Tuple[int, int]]]:
        if not self._pending and not self._held:
            return None
        # 合并 held + pending 去重，按音高高→低
        bag = {}
        for m, v in self._pending:
            bag[m] = v
        for m, v in self._held.items():
            bag[m] = v
        self._pending.clear()
        if not bag:
            return None
        ordered = sorted(bag.items(), key=lambda x: -x[0])
        return ordered
