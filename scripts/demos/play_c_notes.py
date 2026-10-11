"""播放 C 音和下一个 C 音（高八度）"""
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from music_score.audio_engine import AudioEngine

engine = AudioEngine()
if not engine.initialize():
    sys.exit(1)

engine.set_instrument("piano", 0)

C4 = 60   # 中央 C，261.63 Hz
C5 = 72   # 下一个 C（高八度），523.25 Hz

print(f"播放 C4 (MIDI {C4}) ...")
engine.play_note(C4, velocity=90, duration=0.8)
time.sleep(0.2)

print(f"播放 C5 (MIDI {C5}) ...")
engine.play_note(C5, velocity=90, duration=0.8)

engine.close()
print("完成")
