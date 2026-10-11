import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

import i18n


@pytest.fixture
def en():
    i18n.set_language('en', persist=False)
    yield
    i18n.set_language('zh', persist=False)


def test_zh_is_identity():
    i18n.set_language('zh', persist=False)
    assert i18n.tr('文件') == '文件'


def test_exact_pattern_and_phrases(en):
    assert i18n.tr('文件') == 'File'
    assert i18n.tr('G 大调 / e 小调') == 'G major / e minor'
    assert i18n.tr('速度 92 BPM') == 'Tempo 92 BPM'
    assert i18n.tr('乐谱工作室 - 流行歌（流行歌语法）') == 'Score Studio - Pop song (Pop song grammar)'
    assert i18n.tr('已导出 MIDI: /tmp/a.mid') == 'Exported MIDI: /tmp/a.mid'


def test_untranslatable_keeps_original(en):
    assert i18n.tr('雨夜琴声') == '雨夜琴声'
    assert i18n.tr('已打开乐谱: 雨夜琴声.xml') == '已打开乐谱: 雨夜琴声.xml'


def test_pack_names_from_meta(en):
    assert i18n.tr('摇摆标准曲（爵士）') == 'Swing Standard (Jazz)'


def test_widget_roundtrip():
    from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QComboBox, QLineEdit, QWidget, QVBoxLayout
    app = QApplication.instance() or QApplication([])
    win = QMainWindow()
    win.setWindowTitle('乐谱工作室')
    menu = win.menuBar().addMenu('文件')
    act = menu.addAction('退出')
    body = QWidget()
    lay = QVBoxLayout(body)
    label = QLabel('发送')
    combo = QComboBox()
    combo.addItems(['波形', '雨夜琴声'])
    edit = QLineEdit('文件')
    for w in (label, combo, edit):
        lay.addWidget(w)
    win.setCentralWidget(body)
    rt = i18n.Retranslator(app)
    try:
        rt.apply('en', persist=False)
        assert win.windowTitle() == 'Score Studio'
        assert menu.title() == 'File' and act.text() == 'Quit'
        assert label.text() == 'Send'
        assert [combo.itemText(i) for i in range(2)] == ['Waveform', '雨夜琴声']
        assert edit.text() == '文件'
        label.setText('停止')
        rt.apply('en', persist=False)
        assert label.text() == 'Stop'
        rt.apply('zh', persist=False)
        assert win.windowTitle() == '乐谱工作室'
        assert menu.title() == '文件' and act.text() == '退出'
        assert label.text() == '停止'
        assert combo.itemText(0) == '波形'
    finally:
        i18n.set_language('zh', persist=False)
        rt._timer.stop()
        win.deleteLater()
