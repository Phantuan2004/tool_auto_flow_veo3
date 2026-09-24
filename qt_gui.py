from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from gflow_veo_batcher import APP_DIR, GflowRunner, Scene, read_scenes


class RunWorker(QObject):
    log = Signal(str, str)
    scene_changed = Signal(int)
    project_changed = Signal(str)
    finished = Signal(str, int, int)

    def __init__(self, app: "FlowStudio", indexes: list[int], phase: str, output: Path, values: dict[str, str]):
        super().__init__()
        self.app = app
        self.indexes = indexes
        self.phase = phase
        self.output = output
        self.values = values
        self.cancelled = False
        self.runner: GflowRunner | None = None

    @Slot()
    def run(self):
        error_log = self.output / "_logs" / "gflow_errors.log"
        self.runner = GflowRunner(self._emit_log, lambda: self.cancelled, error_log)
        ready, reason = self._authenticate(create_project=self.phase == "image")
        if not ready:
            self.log.emit("ERROR", reason)
            self._write_error_log("ERROR", f"Batch {self.phase}: {reason}")
            self.finished.emit(self.phase, 0, len(self.indexes))
            return
        for index in self.indexes:
            if self.cancelled:
                break
            if self.phase == "image":
                self._run_image(index)
            else:
                self._run_video(index)
            self.scene_changed.emit(index)
        success = sum(self._is_success(self.app.scenes[index]) for index in self.indexes)
        self.finished.emit(self.phase, success, len(self.indexes))

    def stop(self):
        self.cancelled = True
        if self.runner:
            self.runner.cancel()

    def _emit_log(self, level: str, message: str):
        self.log.emit(level, message)

    def _authenticate(self, create_project: bool) -> tuple[bool, str]:
        assert self.runner
        authenticated, error = self.runner.ensure_authenticated()
        if not authenticated:
            return False, error or "Không thể đăng nhập Google Flow."
        if self.values["project"]:
            return True, ""
        if not create_project:
            return False, "Thiếu Project ID. Hãy tạo ảnh trước hoặc nhập Project ID."
        project_id, error = self.runner.create_project("GFlow Veo Batch")
        if not project_id:
            return False, error or "Không tạo được Flow Project."
        self.values["project"] = project_id
        self.project_changed.emit(project_id)
        return True, ""

    def _run_image(self, index: int):
        scene = self.app.scenes[index]
        scene.status, scene.error = "Đang tạo ảnh", ""
        self.log.emit("INFO", f"{scene.id}: đang tạo ảnh")
        self._write_error_log("INFO", f"{scene.id}: bắt đầu tạo ảnh")
        work_dir = Path(self.app.temp_dir(index, "image"))
        image_dir = work_dir / "image"
        image_dir.mkdir(parents=True, exist_ok=True)
        try:
            command = self.runner.image_command(scene, image_dir, self.values)
            ok, error, lines = self.runner.run_command(command)
        except Exception as exc:
            error = f"Lỗi nội bộ khi tạo ảnh: {exc}"
            scene.status = "Lỗi"
            self._write_error_log("ERROR", f"{scene.id}: {error}\n{traceback.format_exc()}")
            scene.error = error
            self.log.emit("ERROR", f"{scene.id}: {error}")
            return
        from gflow_veo_batcher import json_from_cli
        result = json_from_cli(lines, "images")
        images = result.get("images", []) if result else []
        local = Path(images[0]["local_path"]) if images and isinstance(images[0], dict) and images[0].get("local_path") else None
        if ok and local and local.is_file():
            target_dir = self.output / scene.id / "image"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / local.name
            shutil.copy2(local, target)
            scene.image_output, scene.status = str(target), "Ảnh đã tạo"
        elif ok:
            scene.status, error = "Lỗi", "gflow không trả về ảnh local."
        else:
            scene.status = "Đã dừng" if self.cancelled else "Lỗi"
        scene.error = error or ""
        if scene.error:
            self._write_error_log("ERROR", f"{scene.id}: {scene.error}")
        self.log.emit("INFO" if scene.status == "Ảnh đã tạo" else "ERROR", f"{scene.id}: {scene.status} {error}")

    def _run_video(self, index: int):
        scene = self.app.scenes[index]
        scene.status, scene.error = "Đang tạo video", ""
        work_dir = Path(self.app.temp_dir(index, "video"))
        video_dir = work_dir / "video"
        video_dir.mkdir(parents=True, exist_ok=True)
        input_image = work_dir / Path(scene.image_output).name
        try:
            shutil.copy2(scene.image_output, input_image)
        except OSError as exc:
            scene.status, scene.error = "Lỗi", str(exc)
            self._write_error_log("ERROR", f"{scene.id}: không thể chuẩn bị ảnh video: {exc}")
            return
        self.log.emit("INFO", f"{scene.id}: dùng ảnh làm initial frame và tạo video")
        try:
            command = self.runner.video_command(scene, str(input_image), video_dir, self.values)
            ok, error, _ = self.runner.run_command(command)
        except Exception as exc:
            error = f"Lỗi nội bộ khi tạo video: {exc}"
            scene.status = "Lỗi"
            self._write_error_log("ERROR", f"{scene.id}: {error}\n{traceback.format_exc()}")
            scene.error = error
            self.log.emit("ERROR", f"{scene.id}: {error}")
            return
        from gflow_veo_batcher import VIDEO_EXTENSIONS, faststart_video, is_valid_video_file, new_media_file
        video = new_media_file(video_dir, set(), VIDEO_EXTENSIONS) if ok else None
        if ok and video:
            faststart_video(video, self._write_error_log)
            final_dir = self.output / scene.id / "video"
            shutil.copytree(video_dir, final_dir, dirs_exist_ok=True)
            scene.video_output, scene.status = str(final_dir / video.relative_to(video_dir)), "Thành công"
        else:
            scene.status = "Đã dừng" if self.cancelled else "Lỗi"
            error = error or "Không tìm thấy video hợp lệ trong output (file có thể chưa tải xong hoặc bị lỗi mã hóa)."
            if ok:
                invalid_files = [
                    str(path) for path in video_dir.rglob("*")
                    if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS and not is_valid_video_file(path)
                ]
                if invalid_files:
                    error += " File không hợp lệ: " + ", ".join(invalid_files)
        scene.error = error or ""
        if scene.error:
            self._write_error_log("ERROR", f"{scene.id}: {scene.error}")
        self.log.emit("INFO" if scene.status == "Thành công" else "ERROR", f"{scene.id}: {scene.status} {error or ''}")

    def _write_error_log(self, level: str, message: str):
        if self.runner:
            self.runner._write_error_log(level, message)

    @staticmethod
    def _is_success(scene: Scene) -> bool:
        return scene.status in {"Ảnh đã tạo", "Thành công"}


class AuthWorker(QObject):
    log = Signal(str, str)
    finished = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.cancelled = False
        self.runner: GflowRunner | None = None

    @Slot()
    def run(self):
        self.runner = GflowRunner(self._emit_log, lambda: self.cancelled)
        success, error = self.runner.login()
        self.finished.emit(success, error)

    def stop(self):
        self.cancelled = True
        if self.runner:
            self.runner.cancel()

    def _emit_log(self, level: str, message: str):
        self.log.emit(level, message)


class MediaPane(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("mediaPane")
        layout = QVBoxLayout(self)
        title = QLabel("MEDIA GALLERY")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.scene_title = QLabel("Chọn một scene")
        self.scene_title.setObjectName("mediaTitle")
        layout.addWidget(self.scene_title)
        self.stack = QStackedWidget()
        self.image_label = QLabel("Chưa có ảnh local")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumHeight(300)
        self.image_label.setObjectName("imagePreview")
        self.stack.addWidget(self.image_label)
        layout.addWidget(self.stack, 1)
        self.media_info = QLabel("Ảnh và video của scene được hiển thị tại đây")
        self.media_info.setWordWrap(True)
        self.media_info.setObjectName("muted")
        layout.addWidget(self.media_info)
        buttons = QHBoxLayout()
        self.open_image = QPushButton("Mở ảnh")
        self.open_video = QPushButton("Mở video")
        self.open_image.clicked.connect(lambda: self._open(self.image_path))
        self.open_video.clicked.connect(lambda: self._open(self.video_path))
        buttons.addWidget(self.open_image)
        buttons.addWidget(self.open_video)
        layout.addLayout(buttons)
        self.image_path = ""
        self.video_path = ""

    def show_scene(self, scene: Scene):
        self.scene_title.setText(scene.id)
        self.image_path, self.video_path = scene.image_output, scene.video_output
        if self.image_path and Path(self.image_path).is_file():
            pixmap = QPixmap(self.image_path)
            self.image_label.setPixmap(pixmap.scaled(720, 500, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.image_label.setText("")
        else:
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText("Chưa có ảnh local")
        video_text = Path(self.video_path).name if self.video_path else "Chưa có video"
        self.media_info.setText(f"Trạng thái: {scene.status}\nVideo: {video_text}\n{scene.error}".strip())
        self.open_image.setEnabled(bool(self.image_path and Path(self.image_path).exists()))
        self.open_video.setEnabled(bool(self.video_path and Path(self.video_path).exists()))

    @staticmethod
    def _open(path: str):
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class FlowStudio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.scenes: list[Scene] = []
        self.defaults: dict[str, str] = {}
        self.worker: RunWorker | None = None
        self.thread: QThread | None = None
        self.events = queue.Queue()
        self.setWindowTitle("GFlow Veo Batch Studio")
        self.resize(1440, 900)
        self._build_ui()
        self._apply_theme()
        self.load_scene_file(str(APP_DIR / "scene_template.json"))

    def _build_ui(self):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        open_action = QAction("Mở kịch bản", self)
        open_action.triggered.connect(self.choose_scene_file)
        toolbar.addAction(open_action)
        toolbar.addSeparator()
        self.check_action = QAction("Kiểm tra gflow", self)
        self.check_action.triggered.connect(self.check_gflow)
        toolbar.addAction(self.check_action)
        self.login_action = QAction("Đăng nhập Flow", self)
        self.login_action.triggered.connect(self.login_flow)
        toolbar.addAction(self.login_action)
        toolbar.addSeparator()
        self.run_images_action = QAction("Tạo ảnh", self)
        self.run_images_action.triggered.connect(lambda: self.start_run("image"))
        toolbar.addAction(self.run_images_action)
        self.run_videos_action = QAction("Tạo video", self)
        self.run_videos_action.triggered.connect(lambda: self.start_run("video"))
        toolbar.addAction(self.run_videos_action)
        self.stop_action = QAction("Dừng", self)
        self.stop_action.setEnabled(False)
        self.stop_action.triggered.connect(self.stop_run)
        toolbar.addAction(self.stop_action)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        splitter = QSplitter(Qt.Horizontal)
        left_splitter = QSplitter(Qt.Vertical)
        self.media = MediaPane()
        left_splitter.addWidget(self.media)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(160)
        self.log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log.setPlaceholderText("Nhật ký thực thi sẽ xuất hiện ở đây...")
        left_splitter.addWidget(self.log)
        left_splitter.setStretchFactor(0, 3)
        left_splitter.setStretchFactor(1, 2)
        splitter.addWidget(left_splitter)
        splitter.addWidget(self._build_config_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)
        self.status = QStatusBar()
        self.setStatusBar(self.status)

    def _build_config_panel(self):
        panel = QFrame()
        panel.setObjectName("configPane")
        layout = QVBoxLayout(panel)
        title = QLabel("BATCH CONFIGURATION")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        form = QFormLayout()
        self.file_edit = QLineEdit()
        self.output_edit = QLineEdit(str(APP_DIR / "output"))
        self.aspect_edit = QLineEdit("16:9")
        self.project_edit = QLineEdit()
        form.addRow("Kịch bản", self._with_button(self.file_edit, "Chọn", self.choose_scene_file))
        form.addRow("Thư mục output", self._with_button(self.output_edit, "Chọn", self.choose_output))
        form.addRow("Tỷ lệ", self.aspect_edit)
        form.addRow("Project ID", self.project_edit)
        layout.addLayout(form)
        self.dry_run = QCheckBox("Dry-run (không tốn credit)")
        self.dry_run.setChecked(True)
        layout.addWidget(self.dry_run)
        self.scene_count = QLabel("Danh sách phân cảnh")
        self.scene_count.setObjectName("sceneCount")
        layout.addWidget(self.scene_count, 0, Qt.AlignLeft)
        self.scene_list = QListWidget()
        self.scene_list.setMinimumHeight(360)
        self.scene_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scene_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scene_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scene_list.setWordWrap(True)
        self.scene_list.currentRowChanged.connect(self.select_scene)
        layout.addWidget(self.scene_list, 1)
        self.prompt_tabs = QTabWidget()
        self.image_prompt = QPlainTextEdit()
        self.video_prompt = QPlainTextEdit()
        self.prompt_tabs.addTab(self.image_prompt, "Prompt ảnh")
        self.prompt_tabs.addTab(self.video_prompt, "Prompt video")
        layout.addWidget(self.prompt_tabs)
        layout.addWidget(QLabel("Ảnh tham chiếu (nhân vật, đồ vật, bối cảnh)"), 0, Qt.AlignLeft)
        self.reference_list = QListWidget()
        self.reference_list.setMinimumHeight(190)
        self.reference_list.setMaximumHeight(260)
        self.reference_list.setViewMode(QListWidget.IconMode)
        self.reference_list.setResizeMode(QListWidget.Adjust)
        self.reference_list.setMovement(QListWidget.Static)
        self.reference_list.setGridSize(QSize(96, 108))
        self.reference_list.setIconSize(QSize(78, 78))
        self.reference_list.setSpacing(2)
        self.reference_list.setWrapping(True)
        self.reference_list.setWordWrap(True)
        self.reference_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.reference_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.reference_list)
        reference_buttons = QHBoxLayout()
        add_reference = QPushButton("+ Thêm ảnh")
        remove_reference = QPushButton("Xóa ảnh chọn")
        add_reference.clicked.connect(self.add_references)
        remove_reference.clicked.connect(self.remove_reference)
        reference_buttons.addWidget(add_reference)
        reference_buttons.addWidget(remove_reference)
        layout.addLayout(reference_buttons)
        self.save_prompt = QPushButton("Lưu prompt cho scene")
        self.save_prompt.clicked.connect(self.save_current_prompts)
        layout.addWidget(self.save_prompt)
        return panel

    def _with_button(self, widget, text, callback):
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(widget, 1)
        button = QPushButton(text)
        button.clicked.connect(callback)
        row.addWidget(button)
        return container

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #10161f; color: #e7edf4; font-family: 'Segoe UI'; font-size: 10pt; }
            QToolBar { background: #18212c; border: 0; spacing: 8px; padding: 8px 12px; }
            QToolButton, QPushButton { background: #253242; color: #e7edf4; border: 1px solid #34465a; border-radius: 6px; padding: 7px 12px; }
            QToolButton:hover, QPushButton:hover { background: #315265; }
            QLineEdit, QPlainTextEdit, QListWidget { background: #0c121a; border: 1px solid #2b3a4c; border-radius: 6px; padding: 7px; color: #e7edf4; }
            QLabel#sectionTitle { color: #57d6a2; font-weight: 700; letter-spacing: 1px; padding-bottom: 6px; }
            QLabel#sceneCount { color: #b8c8d8; font-weight: 650; padding-top: 6px; }
            QLabel#mediaTitle { font-size: 17pt; font-weight: 650; color: #ffffff; }
            QLabel#muted { color: #95a5b7; }
            QFrame#mediaPane, QFrame#configPane { background: #18212c; border: 1px solid #263647; border-radius: 10px; padding: 8px; }
            QLabel#imagePreview { background: #0b1118; border: 1px dashed #3a5068; border-radius: 8px; color: #75879a; }
            QListWidget::item { padding: 10px; border-bottom: 1px solid #1f2c3a; }
            QListWidget::item:selected { background: #245d67; border-radius: 5px; }
            QTabBar::tab { background: #1c2937; color: #9fb0c2; padding: 8px 14px; }
            QTabBar::tab:selected { background: #2d5964; color: white; }
            QStatusBar { color: #95a5b7; }
            QCheckBox { color: #c8d5e2; padding: 6px 0; }
        """)

    def choose_scene_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn tệp phân cảnh", str(APP_DIR), "Scene files (*.json *.jsonl *.ndjson *.csv *.tsv *.txt)")
        if path:
            self.load_scene_file(path)

    def load_scene_file(self, path: str):
        try:
            self.scenes, self.defaults = read_scenes(Path(path))
            self.file_edit.setText(path)
            if self.defaults.get("aspect"):
                self.aspect_edit.setText(str(self.defaults["aspect"]))
            if self.defaults.get("project"):
                self.project_edit.setText(str(self.defaults["project"]))
            self.scene_list.clear()
            for scene in self.scenes:
                item = QListWidgetItem(self._scene_item_text(scene))
                self.scene_list.addItem(item)
            self.scene_count.setText(f"Danh sách phân cảnh ({len(self.scenes)})")
            if self.scenes:
                self.scene_list.setCurrentRow(0)
            self.log_message("INFO", f"Đã nạp {len(self.scenes)} scene từ {Path(path).name}")
        except Exception as exc:
            QMessageBox.critical(self, "Không thể đọc kịch bản", str(exc))

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục output", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    @Slot(int)
    def select_scene(self, index: int):
        if index < 0 or index >= len(self.scenes):
            return
        scene = self.scenes[index]
        self.image_prompt.setPlainText(scene.image_prompt)
        self.video_prompt.setPlainText(scene.video_prompt)
        self.reference_list.clear()
        for reference in scene.references:
            self._add_reference_item(reference)
        self.media.show_scene(scene)

    def save_current_prompts(self):
        index = self.scene_list.currentRow()
        if index >= 0:
            self.scenes[index].image_prompt = self.image_prompt.toPlainText().strip()
            self.scenes[index].video_prompt = self.video_prompt.toPlainText().strip()
            references = [
                self.reference_list.item(row).data(Qt.UserRole) or self.reference_list.item(row).text()
                for row in range(self.reference_list.count())
            ]
            for scene in self.scenes:
                scene.references = references.copy()
            self.log_message("INFO", f"Đã cập nhật prompt cho {self.scenes[index].id}")

    def _add_reference_item(self, path: str):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, path)
        item.setToolTip(path)
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            item.setIcon(QIcon(pixmap.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            item.setText(Path(path).name[:16])
            item.setSizeHint(QSize(92, 104))
        else:
            item.setText(f"[Không đọc được ảnh] {path}")
            item.setSizeHint(QSize(92, 104))
        self.reference_list.addItem(item)

    def add_references(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Chọn toàn bộ ảnh tham chiếu",
            str(APP_DIR),
            "Images (*.png *.jpg *.jpeg *.webp);;All files (*.*)",
        )
        for path in paths:
            if not any(self.reference_list.item(row).data(Qt.UserRole) == path for row in range(self.reference_list.count())):
                self._add_reference_item(path)
        self.save_current_prompts()

    def remove_reference(self):
        for item in self.reference_list.selectedItems():
            self.reference_list.takeItem(self.reference_list.row(item))
        self.save_current_prompts()

    def check_gflow(self):
        result = subprocess.run([shutil.which("gflow") or "gflow", "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.log_message("INFO" if result.returncode == 0 else "ERROR", (result.stdout or result.stderr).strip())

    def login_flow(self):
        if self.worker:
            return
        self.worker = AuthWorker()
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.log_message)
        self.worker.finished.connect(self.login_finished)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()
        self._set_actions_enabled(False)
        self.stop_action.setEnabled(True)
        self.status.showMessage("Đang chờ đăng nhập Google Flow...")

    def start_run(self, phase: str):
        if self.worker or not self.scenes:
            return
        if phase == "video":
            indexes = [i for i, scene in enumerate(self.scenes) if scene.image_output and Path(scene.image_output).is_file()]
            if not indexes:
                QMessageBox.warning(self, "Chưa có ảnh", "Hãy tạo ảnh trước khi tạo video.")
                return
            if not self.project_edit.text().strip():
                QMessageBox.warning(self, "Thiếu Project ID", "Hãy tạo ảnh trước hoặc nhập Project ID.")
                return
        else:
            indexes = list(range(len(self.scenes)))
        output = Path(self.output_edit.text().strip() or APP_DIR / "output").resolve()
        output.mkdir(parents=True, exist_ok=True)
        values = {"aspect": self.aspect_edit.text().strip(), "project": self.project_edit.text().strip()}
        self.worker = RunWorker(self, indexes, phase, output, values)
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.log_message)
        self.worker.scene_changed.connect(self.refresh_scene)
        self.worker.project_changed.connect(self.project_edit.setText)
        self.worker.finished.connect(self.run_finished)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()
        self._set_actions_enabled(False)
        self.stop_action.setEnabled(True)
        self.status.showMessage(f"Đang chạy pha {phase}...")

    def stop_run(self):
        if self.worker:
            self.worker.stop()
            self.status.showMessage("Đang dừng sau cảnh hiện tại...")

    def _set_actions_enabled(self, enabled: bool):
        self.check_action.setEnabled(enabled)
        self.login_action.setEnabled(enabled)
        self.run_images_action.setEnabled(enabled)
        self.run_videos_action.setEnabled(enabled)

    @Slot(bool, str)
    def login_finished(self, success: bool, error: str):
        if success:
            self.log_message("INFO", "Đăng nhập Google Flow thành công.")
            self.status.showMessage("Đã đăng nhập Google Flow")
        else:
            self.log_message("ERROR", error or "Đăng nhập Google Flow thất bại.")
            self.status.showMessage("Đăng nhập thất bại")
        self.worker = None
        self.thread = None
        self.stop_action.setEnabled(False)
        self._set_actions_enabled(True)

    @Slot(str, int, int)
    def run_finished(self, phase: str, success: int, total: int):
        self.log_message("INFO", f"Hoàn tất {phase}: {success}/{total}")
        self.worker = None
        self.thread = None
        self.stop_action.setEnabled(False)
        self._set_actions_enabled(True)
        self.status.showMessage(f"Hoàn tất: {success}/{total} scene")
        self.refresh_all()

    def refresh_scene(self, index: int):
        if 0 <= index < self.scene_list.count():
            item = self.scene_list.item(index)
            item.setText(self._scene_item_text(self.scenes[index]))
            if index == self.scene_list.currentRow():
                self.media.show_scene(self.scenes[index])

    @staticmethod
    def _scene_item_text(scene: Scene) -> str:
        summary = " ".join(scene.image_prompt.split())
        if len(summary) > 140:
            summary = summary[:137].rstrip() + "..."
        return f"{scene.id}  |  {scene.status}\n{summary}"

    def refresh_all(self):
        for index in range(len(self.scenes)):
            self.refresh_scene(index)

    def log_message(self, level: str, message: str):
        self.log.appendPlainText(f"[{level}] {message}")

    def temp_dir(self, index: int, prefix: str) -> str:
        import tempfile
        return tempfile.mkdtemp(prefix=f"gflow-{prefix}-{index:03d}-")


def main() -> int:
    app = QApplication([])
    app.setApplicationName("GFlow Veo Batch Studio")
    window = FlowStudio()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
