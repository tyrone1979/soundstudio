"""
【已过期】Main application window for the music score editor.
PyQt5 version with auto-instrument detection.

已被 score_studio.StudioWindow 取代（仅 main.py 引用）；后续版本删除。
"""
import sys
import warnings

warnings.warn('music_score.main_window 已过期，请使用 score_studio.StudioWindow',
              DeprecationWarning, stacklevel=2)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QKeySequence, QIcon, QPainter, QImage
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGraphicsScene, QGraphicsView, QToolBar, QMenuBar, QStatusBar,
    QFileDialog, QMessageBox, QInputDialog, QComboBox, QLabel,
    QSpinBox, QPushButton, QDockWidget, QListWidget, QLineEdit,
    QDialog, QFormLayout, QDialogButtonBox, QAction
)

from .models import (
    Score, Staff, Measure, Note, Pitch,
    ClefType, NoteName, Accidental, NoteDuration, TimeSignature, KeySignature
)
from .renderer import ScoreRenderer
from .interaction import (
    find_staff_and_measure_at, staff_position_to_pitch,
    snap_to_nearest_line, STAFF_LINE_SPACING, STAFF_HEIGHT,
    LEFT_MARGIN, TOP_MARGIN, STAFF_GAP
)
from .musicxml_importer import import_musicxml, HAS_MUSIC21
from .audio_engine import AudioEngine


class ScoreGraphicsView(QGraphicsView):
    """Custom graphics view with zoom support and note input."""

    note_clicked = None

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self._scale = 1.0
        self._panning = False
        self._last_mouse_pos = None

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        zoom_factor = 1.1 if delta > 0 else 0.9
        self._scale *= zoom_factor
        self.scale(zoom_factor, zoom_factor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.modifiers() == Qt.ControlModifier:
            self._panning = True
            self._last_mouse_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if event.button() == Qt.LeftButton and self.note_clicked:
            scene_pos = self.mapToScene(event.pos())
            self.note_clicked(scene_pos.x(), scene_pos.y())
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.pos() - self._last_mouse_pos
            self._last_mouse_pos = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            return
        super().mouseReleaseEvent(event)


class NewScoreDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建乐谱")
        self.setMinimumWidth(300)
        layout = QFormLayout(self)

        self.title_edit = QLineEdit("Untitled")
        self.composer_edit = QLineEdit("")
        self.tempo_spin = QSpinBox()
        self.tempo_spin.setRange(20, 300)
        self.tempo_spin.setValue(120)
        self.ts_num = QSpinBox()
        self.ts_num.setRange(1, 32)
        self.ts_num.setValue(4)
        self.ts_den = QSpinBox()
        self.ts_den.setRange(1, 32)
        self.ts_den.setValue(4)
        self.key_combo = QComboBox()
        self.key_combo.addItems([
            "C Major / A minor", "G Major / E minor (1#)",
            "D Major / B minor (2#)", "A Major / F# minor (3#)",
            "E Major / C# minor (4#)", "B Major / G# minor (5#)",
            "F# Major / D# minor (6#)", "C# Major / A# minor (7#)",
            "F Major / D minor (1b)", "Bb Major / G minor (2b)",
            "Eb Major / C minor (3b)", "Ab Major / F minor (4b)",
            "Db Major / Bb minor (5b)", "Gb Major / Eb minor (6b)",
            "Cb Major / Ab minor (7b)",
        ])
        self.clef_combo = QComboBox()
        self.clef_combo.addItems(["高音谱号 (Treble)", "低音谱号 (Bass)", "中音谱号 (Alto)"])

        layout.addRow("标题:", self.title_edit)
        layout.addRow("作曲家:", self.composer_edit)
        layout.addRow("速度 (BPM):", self.tempo_spin)
        layout.addRow("拍号 分子:", self.ts_num)
        layout.addRow("拍号 分母:", self.ts_den)
        layout.addRow("调号:", self.key_combo)
        layout.addRow("谱号:", self.clef_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_score(self) -> Score:
        title = self.title_edit.text()
        composer = self.composer_edit.text()
        tempo = self.tempo_spin.value()
        ts = TimeSignature(self.ts_num.value(), self.ts_den.value())
        key_idx = self.key_combo.currentIndex()
        sharps = [0, 1, 2, 3, 4, 5, 6, 7, -1, -2, -3, -4, -5, -6, -7][key_idx]
        ks = KeySignature(sharps)
        clef = [ClefType.TREBLE, ClefType.BASS, ClefType.ALTO][self.clef_combo.currentIndex()]

        score = Score(title=title, composer=composer, tempo=tempo,
                      time_signature=ts, key_signature=ks)
        score.staves[0].clef = clef
        score.staves[0].measures.append(Measure())
        return score


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("乐谱编辑器 - 智能音色版")
        self.setGeometry(100, 100, 1200, 800)

        self.score = Score()
        self.score.staves[0].measures.append(Measure())
        self.current_duration = NoteDuration.QUARTER
        self.current_accidental = Accidental.NATURAL

        self._init_ui()
        self._create_sample_score()
        self.renderer.render_score(self.score, self.width())

        # 初始化音频引擎
        self.audio_engine = AudioEngine()
        if not self.audio_engine.initialize():
            QMessageBox.warning(
                self, "音频引擎初始化失败",
                "请确保已安装: pip install numpy sounddevice"
            )

        self.view.note_clicked = self._on_note_clicked

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self.scene = QGraphicsScene()
        self.view = ScoreGraphicsView(self.scene)
        layout.addWidget(self.view)

        self.renderer = ScoreRenderer(self.scene)

        # Menu bar
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        new_action = QAction("新建", self)
        new_action.setShortcut(QKeySequence.New)
        new_action.triggered.connect(self.new_score)
        file_menu.addAction(new_action)

        open_action = QAction("打开", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self.open_score)
        file_menu.addAction(open_action)

        import_xml_action = QAction("导入 MusicXML", self)
        import_xml_action.triggered.connect(self.import_musicxml)
        file_menu.addAction(import_xml_action)

        export_action = QAction("导出为图片", self)
        export_action.triggered.connect(self.export_image)
        file_menu.addAction(export_action)

        file_menu.addSeparator()
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        edit_menu = menubar.addMenu("编辑")
        add_measure_action = QAction("添加小节", self)
        add_measure_action.triggered.connect(self.add_measure)
        edit_menu.addAction(add_measure_action)

        # 播放菜单 - 简化，只有一个播放按钮
        play_menu = menubar.addMenu("播放")
        play_action = QAction("▶ 播放", self)
        play_action.setShortcut("Space")
        play_action.triggered.connect(self.play_score)
        play_menu.addAction(play_action)

        stop_action = QAction("⏹ 停止", self)
        stop_action.triggered.connect(self.stop_playback)
        play_menu.addAction(stop_action)

        # Toolbar - 简化
        toolbar = QToolBar()
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel("音符时值:"))
        self.duration_combo = QComboBox()
        self.duration_combo.addItems([
            "全音符", "二分音符", "四分音符", "八分音符", "十六分音符"
        ])
        self.duration_combo.setCurrentIndex(2)
        self.duration_combo.currentIndexChanged.connect(self._on_duration_changed)
        toolbar.addWidget(self.duration_combo)

        toolbar.addWidget(QLabel("变音记号:"))
        self.accidental_combo = QComboBox()
        self.accidental_combo.addItems(["自然", "升号", "降号", "重升", "重降"])
        self.accidental_combo.currentIndexChanged.connect(self._on_accidental_changed)
        toolbar.addWidget(self.accidental_combo)

        toolbar.addSeparator()
        add_measure_btn = QPushButton("+ 小节")
        add_measure_btn.clicked.connect(self.add_measure)
        toolbar.addWidget(add_measure_btn)

        play_btn = QPushButton("▶ 播放")
        play_btn.clicked.connect(self.play_score)
        toolbar.addWidget(play_btn)

        stop_btn = QPushButton("⏹ 停止")
        stop_btn.clicked.connect(self.stop_playback)
        toolbar.addWidget(stop_btn)

        self.statusBar().showMessage("就绪 - 点击播放自动识别乐器")

    def _create_sample_score(self):
        staff = self.score.staves[0]
        staff.clef = ClefType.TREBLE

        measure = staff.measures[0]
        measure.notes = [
            Note(Pitch(NoteName.C, 4), NoteDuration.QUARTER),
            Note(Pitch(NoteName.D, 4), NoteDuration.QUARTER),
            Note(Pitch(NoteName.E, 4), NoteDuration.QUARTER),
            Note(Pitch(NoteName.F, 4), NoteDuration.QUARTER),
        ]

        measure2 = Measure()
        measure2.notes = [
            Note(Pitch(NoteName.G, 4), NoteDuration.QUARTER),
            Note(Pitch(NoteName.A, 4), NoteDuration.QUARTER),
            Note(Pitch(NoteName.B, 4), NoteDuration.QUARTER),
            Note(Pitch(NoteName.C, 5), NoteDuration.QUARTER),
        ]
        staff.measures.append(measure2)

        measure3 = Measure()
        measure3.notes = [
            Note(Pitch(NoteName.E, 4), NoteDuration.HALF),
            Note(Pitch(NoteName.G, 4), NoteDuration.HALF),
        ]
        staff.measures.append(measure3)

        measure4 = Measure()
        measure4.notes = [
            Note(Pitch(NoteName.C, 4), NoteDuration.WHOLE),
        ]
        staff.measures.append(measure4)

    def _on_duration_changed(self, index):
        durations = [NoteDuration.WHOLE, NoteDuration.HALF, NoteDuration.QUARTER,
                     NoteDuration.EIGHTH, NoteDuration.SIXTEENTH]
        self.current_duration = durations[index]

    def _on_accidental_changed(self, index):
        accidentals = [Accidental.NATURAL, Accidental.SHARP, Accidental.FLAT,
                       Accidental.DOUBLE_SHARP, Accidental.DOUBLE_FLAT]
        self.current_accidental = accidentals[index]

    def new_score(self):
        dlg = NewScoreDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            self.score = dlg.get_score()
            self.renderer.render_score(self.score, self.width())
            self.statusBar().showMessage(f"新建乐谱: {self.score.title}")

    def open_score(self):
        QMessageBox.information(self, "提示", "文件打开功能待实现")

    def import_musicxml(self):
        if not HAS_MUSIC21:
            QMessageBox.warning(self, "缺少依赖", "请先安装 music21: pip install music21")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 MusicXML", "",
            "MusicXML files (*.xml *.mxl);;All Files (*)"
        )
        if path:
            try:
                self.score = import_musicxml(path)
                self.renderer.render_score(self.score, self.width())

                # 自动检测并显示乐器
                detected = self.audio_engine._detect_instrument(self.score)
                self.statusBar().showMessage(
                    f"已导入: {self.score.title} | 检测到乐器: {detected}"
                )
            except Exception as e:
                QMessageBox.critical(self, "导入失败", str(e))

    def export_image(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出图片", "score.png", "PNG Images (*.png);;All Files (*)"
        )
        if path:
            rect = self.scene.sceneRect()
            image = QImage(int(rect.width()), int(rect.height()), QImage.Format_ARGB32)
            image.fill(Qt.white)
            painter = QPainter(image)
            self.scene.render(painter)
            painter.end()
            image.save(path)
            self.statusBar().showMessage(f"已导出到: {path}")

    def add_measure(self):
        staff = self.score.staves[0]
        new_measure = Measure(number=len(staff.measures) + 1)
        ts = self.score.time_signature
        # NoteDuration.value 以全音符为单位，beats_per_measure() 以四分音符
        # 为单位：先换算成全音符，再按四分休止符填满（4/4 → 4 个，不是 16 个）
        remaining = ts.beats_per_measure() / 4.0
        while remaining >= NoteDuration.QUARTER.value - 1e-9:
            new_measure.notes.append(
                Note(Pitch(NoteName.C, 4), NoteDuration.QUARTER, is_rest=True)
            )
            remaining -= NoteDuration.QUARTER.value
        staff.measures.append(new_measure)
        self.renderer.render_score(self.score, self.width())
        self.statusBar().showMessage(f"已添加小节，共 {len(staff.measures)} 小节")

    def play_score(self):
        """播放 - 自动检测乐器"""
        if not self.audio_engine.is_available():
            QMessageBox.warning(self, "音频引擎不可用", "无法播放")
            return

        # 自动检测并播放
        self.audio_engine.play_score(self.score, instrument=None)

        # 显示检测到的乐器
        detected = self.audio_engine._detect_instrument(self.score)
        preset = self.audio_engine.ORCHESTRA_PRESETS.get(detected)
        # orchestra/quartet 不在 ORCHESTRA_PRESETS 里，给出可读名称
        inst_name = preset.name if preset else {
            'orchestra': '管弦乐', 'quartet': '四重奏'}.get(detected, '钢琴')
        self.statusBar().showMessage(f"正在播放... 乐器: {inst_name}")

    def stop_playback(self):
        self.audio_engine.stop()
        self.statusBar().showMessage("已停止")

    def _on_note_clicked(self, x: float, y: float):
        result = find_staff_and_measure_at(y, x, self.score, self.width())
        if result is None:
            return
        staff_idx, measure_idx, measure_x, measure_width, staff_y = result
        staff = self.score.staves[staff_idx]
        measure = staff.measures[measure_idx]
        clef = staff.clef

        relative_y = y - staff_y
        # renderer 的 staff position：0 = 底线，每 STAFF_LINE_SPACING 一个
        # 谱表位置（线到相邻间）；screen y = staff_y + STAFF_HEIGHT - pos*spacing
        position = (STAFF_HEIGHT - relative_y) / STAFF_LINE_SPACING
        position = snap_to_nearest_line(position)

        pitch = staff_position_to_pitch(position, clef)
        pitch.accidental = self.current_accidental

        note = Note(pitch, self.current_duration)
        measure.notes.append(note)

        self.renderer.render_score(self.score, self.width())
        self.statusBar().showMessage(
            f"添加音符: {pitch.name.name}{pitch.octave} ({self.current_duration.name})"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'renderer') and self.score:
            self.renderer.render_score(self.score, self.width())

    def closeEvent(self, event):
        self.audio_engine.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())