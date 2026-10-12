"""生成原创管弦乐章《晨光快板》，导出 MusicXML / MIDI / WAV。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from score_studio import (
    build_music21_score, write_score_bundle, render_midi_to_audio,
    synthesize_software, m21_to_model, export_wav, export_analysis_report,
    MIDI_PATH, MUSICXML_PATH, WAV_PATH, REPORT_PATH, SAMPLE_RATE, SOUNDFONT,
)

os.makedirs('scores', exist_ok=True)
m21 = build_music21_score()
paths = write_score_bundle(m21)
model = m21_to_model(m21, '晨光快板', 'Score Studio（原创）')
export_analysis_report(model, REPORT_PATH)

audio = render_midi_to_audio(paths['midi'], SOUNDFONT, SAMPLE_RATE)
engine = 'MIDI→FluidSynth'
if audio is None:
    audio = synthesize_software(model, SAMPLE_RATE)
    engine = '软件合成'
paths['wav'] = export_wav(audio, SAMPLE_RATE, WAV_PATH)

print("管弦乐章导出完成：")
print("  标题: 晨光快板 | G major | 4/4 | BPM=126 | 32 bars")
print(f"  MusicXML: {os.path.abspath(paths['musicxml'])}")
print(f"  MIDI:     {os.path.abspath(paths['midi'])}")
print(f"  WAV:      {os.path.abspath(paths['wav'])} ({engine})")
print(f"  Report:   {os.path.abspath(REPORT_PATH)}")
print(f"  Duration: {len(audio) / SAMPLE_RATE:.1f}s")
