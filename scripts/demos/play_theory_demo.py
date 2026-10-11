"""乐理示例演示：
1. C 大调音阶（全-全-半-全-全-全-半）——明亮、稳定
2. C 自然小调音阶（全-半-全-全-半-全-全）——暗淡、忧伤
3. 调性中心：C 大调的主音是 C，G 大调的主音是 G
4. 功能进行：I → IV → V → I（C → F → G → C）
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from music_score.audio_engine import AudioEngine

engine = AudioEngine()
if not engine.initialize():
    sys.exit(1)

engine.set_instrument("piano", 0)


def play_scale(name, notes, note_dur=0.4, final_hold=1.2):
    """依次播放音阶，最后一个音稍作停留"""
    print(f"--- {name} ---")
    for i, n in enumerate(notes):
        d = final_hold if i == len(notes) - 1 else note_dur
        engine.play_note(n, velocity=85, duration=d)
    print()


# ========== 1. C 大调音阶：全-全-半-全-全-全-半 ==========
# C  D  E  F  G  A  B  C
# 全 全 半 全 全  全  半
c_major = [60, 62, 64, 65, 67, 69, 71, 72]
play_scale("C 大调音阶（明亮、稳定）", c_major)
time.sleep(0.5)

# ========== 2. C 自然小调音阶：全-半-全-全-半-全-全 ==========
# C  D  降E  F  G  降A  降B  C
# 全 半  全 全  半  全   全
c_minor = [60, 62, 63, 65, 67, 68, 70, 72]
play_scale("C 自然小调音阶（暗淡、忧伤）", c_minor)
time.sleep(0.5)

# ========== 3. 调性中心 ==========
# 同样的音阶素材，结束在不同主音上，感受不同的"归宿感"
print("--- 调性中心 ---")
# C 大调：主音是 C
play_scale("C 大调（主音 C）", c_major, note_dur=0.3, final_hold=1.5)
time.sleep(0.5)
# G 大调：主音是 G
g_major = [67, 69, 71, 72, 74, 76, 78, 79]
play_scale("G 大调（主音 G）", g_major, note_dur=0.3, final_hold=1.5)
time.sleep(0.5)

# ========== 4. 功能进行：I → IV → V → I ==========
# C 大调：I=C, IV=F, V=G
I  = [60, 64, 67]   # C 大三和弦（主）
IV = [65, 69, 72]   # F 大三和弦（下属）
V  = [67, 71, 74]   # G 大三和弦（属）

print("--- 功能进行：I → IV → V → I ---")
print("I（主，C）稳定")
engine.play_chord(I, velocity=85, duration=0.8)
time.sleep(0.2)
print("IV（下属，F）离开")
engine.play_chord(IV, velocity=85, duration=0.8)
time.sleep(0.2)
print("V（属，G）制造紧张")
engine.play_chord(V, velocity=85, duration=0.8)
time.sleep(0.2)
print("I（主，C）解决，回家")
engine.play_chord(I, velocity=85, duration=1.5)
print()

engine.close()
print("全部完成")
