"""离屏自测：管弦乐章 MusicXML / 可视化 / 报告"""
import os
import sys

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer
import score_studio

app = QApplication(sys.argv)
window = score_studio.StudioWindow()

deadline = 90
while window.audio is None and deadline > 0:
    app.processEvents()
    import time
    time.sleep(0.2)
    deadline -= 0.2

assert window.audio is not None, "音频合成失败"
assert window.score.tempo == 126
assert len(window.score.staves) >= 6
assert len(window.score.staves[0].measures) == 32
assert os.path.exists(score_studio.MUSICXML_PATH)
assert os.path.exists(score_studio.MIDI_PATH)
assert os.path.exists(score_studio.WAV_PATH)
assert os.path.exists(score_studio.REPORT_PATH)

with open(score_studio.REPORT_PATH, encoding='utf-8') as f:
    report = f.read()
assert '晨光快板' in report
assert '使用了调外音' not in report
assert '| 声部进行 | ❌' not in report
assert 'G 大调' in report
assert window.score.key_signature.sharps == 1
assert '主音（完全终止感）' in report
print(f"audio {len(window.audio)/44100:.1f}s, parts {len(window.score.staves)}, bars 32, tempo {window.score.tempo}")
print("ok")
QTimer.singleShot(0, app.quit)
app.exec_()
