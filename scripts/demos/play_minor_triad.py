"""播放 C 小三和弦（C - 降E - G）"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from music_score.audio_engine import AudioEngine

engine = AudioEngine()
if not engine.initialize():
    sys.exit(1)

engine.set_instrument("piano", 0)

# C 小三和弦：根音 C4，小三度 Eb4，纯五度 G4
C4, Eb4, G4 = 60, 63, 67

print(f"播放 C 小三和弦: MIDI {C4}, {Eb4}, {G4}")
engine.play_chord([C4, Eb4, G4], velocity=90, duration=1.5)

engine.close()
print("完成")
