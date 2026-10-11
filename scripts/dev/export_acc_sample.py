# -*- coding: utf-8 -*-
"""把验收测试里的「摇摆标准曲」样例导出成可看可听的乐谱文件。

复刻 tests/test_style_acceptance.py 中爵士摇摆标准曲的生成参数：
    jazz/swing-standard, bars=16, voices=3, seed='notes', fugue='off'
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import style_skeleton as sk                 # noqa: E402
import score_studio as SS                   # noqa: E402
from llm_compose import spec_to_music21     # noqa: E402

OUT_DIR = os.path.join(ROOT, 'scores')
os.makedirs(OUT_DIR, exist_ok=True)

genre_id = 'jazz/swing-standard'
spec = sk.skeleton_spec(genre_id, bars=16, voices=3, seed='notes', fugue='off')
m21 = spec_to_music21(spec)
title = spec.get('title') or '摇摆标准曲（验收样例）'
if m21.metadata is None:
    from music21 import metadata as _m
    m21.metadata = _m.Metadata()
m21.metadata.title = title

xml_path = os.path.join(OUT_DIR, 'acc_swing_standard.xml')
mid_path = os.path.join(OUT_DIR, 'acc_swing_standard.mid')

paths = SS.write_score_bundle(m21, musicxml_path=xml_path, midi_path=mid_path)
print('MusicXML:', paths.get('musicxml'))
print('MIDI    :', paths.get('midi'))

# 尝试合成 WAV（依赖 fluidsynth + soundfont，缺失则跳过）
wav_path = os.path.join(OUT_DIR, 'acc_swing_standard.wav')
try:
    sys.path.insert(0, os.path.join(ROOT, 'scripts', 'eval'))
    from mel_spectrogram import synthesize_from_musicxml
    import soundfile as sf
    y, sr = synthesize_from_musicxml(xml_path)
    if y is not None and len(y):
        sf.write(wav_path, y, sr)
        print('WAV     :', wav_path, f'({len(y)/sr:.1f}s)')
    else:
        print('WAV     : 合成返回空，跳过')
except Exception as e:                       # noqa: BLE001
    print(f'WAV     : 跳过（{type(e).__name__}: {e}）')

print('done')
