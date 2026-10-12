"""用 pretty_midi + fluidsynth 播放 scores/g_major_strings_horn.mid"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(sys.path[0])

# 让 ctypes 能找到项目自带的 libfluidsynth DLL
FLUIDSYNTH_BIN = os.path.abspath('fluidsynth/bin')
os.environ['PATH'] = FLUIDSYNTH_BIN + os.pathsep + os.environ['PATH']

# fluidsynth.py 包里有写死的路径 C:\tools\fluidsynth\bin（本机不存在），
# 导入前临时把 add_dll_directory 包一层，忽略不存在的路径
_orig_add_dll_directory = getattr(os, 'add_dll_directory', None)  # 仅 Windows 有
if _orig_add_dll_directory is not None:
    def _safe_add_dll_directory(path):
        try:
            return _orig_add_dll_directory(path)
        except (FileNotFoundError, OSError):
            return None
    os.add_dll_directory = _safe_add_dll_directory

import sounddevice as sd
import pretty_midi

MIDI_PATH = os.path.join('scores', 'g_major_strings_horn.mid')
SF_PATH = os.path.join('soundfonts', 'GeneralUser-GS.sf2')
FS_DLL = os.path.join(FLUIDSYNTH_BIN, 'libfluidsynth-3.dll')

print(f"加载 MIDI: {MIDI_PATH}")
pm = pretty_midi.PrettyMIDI(MIDI_PATH)

print("用 FluidSynth 合成音频 ...")
audio = pm.fluidsynth(sf2_path=SF_PATH)

print(f"播放中 ... 时长 {pm.get_end_time():.1f} 秒")
sd.play(audio, 44100)
sd.wait()

print("完成")
