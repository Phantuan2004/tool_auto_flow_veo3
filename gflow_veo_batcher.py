"""Desktop batch runner for gflow-cli / Google Flow Veo generation.

This application deliberately shells out to the installed ``gflow`` executable.
That keeps it compatible with gflow-cli authentication and future CLI releases.
"""

from __future__ import annotations

import csv
import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "settings.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov"}


def new_media_file(folder: Path, before: set[Path], extensions: set[str]) -> Path | None:
    """Return the newest file created by one gflow command in its output folder."""
    candidates = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in extensions and p not in before]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def json_from_cli(lines: list[str], required_key: str | None = None) -> dict[str, Any] | None:
    """Read a gflow --json result, ignoring unrelated structured log events."""
    decoder = json.JSONDecoder()
    for index in range(len(lines) - 1, -1, -1):
        if not lines[index].lstrip().startswith("{"):
            continue
        try:
            value, _ = decoder.raw_decode("\n".join(lines[index:]).lstrip())
            if isinstance(value, dict) and (required_key is None or required_key in value):
                return value
        except json.JSONDecodeError:
            continue
    return None


@dataclass
class Scene:
    id: str
    image_prompt: str
    video_prompt: str
    aspect: str = ""
    project: str = ""
    status: str = "Chờ tạo ảnh"
    image_output: str = ""
    video_output: str = ""
    error: str = ""
    retry_selected: bool = False


def read_scenes(path: Path) -> tuple[list[Scene], dict[str, Any]]:
    """Load JSON, JSONL, CSV, TSV, or a simple one-prompt-per-line text file."""
    suffix = path.suffix.lower()
    raw: list[dict[str, Any]]
    defaults: dict[str, Any] = {}
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, list):
            raw = payload
        elif isinstance(payload, dict):
            raw = payload.get("scenes", [])
            defaults = payload.get("defaults", {}) or {}
            if payload.get("project"):
                defaults["project"] = payload["project"]
        else:
            raise ValueError("JSON phải là danh sách cảnh hoặc đối tượng có trường 'scenes'.")
    elif suffix in {".jsonl", ".ndjson"}:
        raw = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    elif suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            raw = list(csv.DictReader(fh, delimiter=delimiter))
    else:
        raw = [{"prompt": line.strip()} for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]

    scenes: list[Scene] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict) or not str(item.get("prompt") or item.get("image_prompt") or item.get("video_prompt") or "").strip():
            raise ValueError(f"Cảnh dòng/vị trí {index} thiếu 'prompt', 'image_prompt' hoặc 'video_prompt'.")
        merged = {**defaults, **item}
        image_prompt = str(merged.get("image_prompt") or merged.get("prompt") or "").strip()
        video_prompt = str(merged.get("video_prompt") or merged.get("prompt") or image_prompt).strip()
        scenes.append(Scene(
            id=str(merged.get("id") or f"scene-{index:03d}"), image_prompt=image_prompt, video_prompt=video_prompt,
            aspect=str(merged.get("aspect", "") or ""),
            project=str(merged.get("project", "") or ""),
        ))
    if not scenes:
        raise ValueError("Tệp không có cảnh nào.")
    return scenes, defaults


class GflowRunner:
    def __init__(self, emit: Callable[[str, str], None], is_cancelled: Callable[[], bool]) -> None:
        self.emit, self.is_cancelled = emit, is_cancelled
        self.process: subprocess.Popen[str] | None = None

    def image_command(self, scene: Scene, output_dir: Path, global_values: dict[str, str]) -> list[str]:
        executable = shutil.which("gflow") or "gflow"
        command = [executable, "image", "t2i", scene.image_prompt, "--model", "nano2", "--aspect", scene.aspect or global_values["aspect"], "-n", "1"]
        # The batch owns one Flow project, ensuring the generated UUID stays selectable.
        project = global_values["project"]
        if project:
            command += ["--project", project]
        command += ["--out", str(output_dir), "--json"]
        return command

    def video_command(self, scene: Scene, initial_frame: str, output_dir: Path, global_values: dict[str, str]) -> list[str]:
        executable = shutil.which("gflow") or "gflow"
        command = [executable, "video", "i2v", "--initial-frame", initial_frame, scene.video_prompt,
                   "--model", "omni-flash", "--duration", "8", "--aspect", scene.aspect or global_values["aspect"], "--count", "1"]
        project = global_values["project"]
        if project:
            command += ["--project", project]
        return command + ["--out-dir", str(output_dir), "--json"]

    def project_command(self, title: str) -> list[str]:
        executable = shutil.which("gflow") or "gflow"
        return [executable, "project", "create", "--title", title, "--json"]

    def run_command(self, cmd: list[str]) -> tuple[bool, str, list[str]]:
        self.emit("INFO", "Lệnh: " + subprocess.list2cmdline(cmd))
        output_lines: list[str] = []
        try:
            # gflow-cli currently has Windows builds that cannot encode non-ASCII CWD/output paths.
            # The batch runner uses an ASCII-friendly temp workspace for the CLI, then copies results.
            self.process = subprocess.Popen(cmd, cwd=Path(tempfile.gettempdir()), text=True, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return False, "Không tìm thấy lệnh gflow. Hãy cài: python -m pip install -U gflow-cli", output_lines
        assert self.process.stdout
        for line in self.process.stdout:
            output_lines.append(line.rstrip())
            self.emit("CLI", line.rstrip())
            if self.is_cancelled() and self.process.poll() is None:
                self.process.terminate()
        code = self.process.wait()
        self.process = None
        if self.is_cancelled():
            return False, "Đã dừng bởi người dùng", output_lines
        return code == 0, "" if code == 0 else f"gflow kết thúc với mã {code}", output_lines

    def ensure_authenticated(self) -> tuple[bool, str]:
        """Check the saved Flow session and launch the official gflow login flow if needed."""
        executable = shutil.which("gflow") or "gflow"
        self.emit("INFO", "Kiểm tra profile đăng nhập Google Flow…")
        authenticated, _, _ = self.run_command([executable, "auth", "status"])
        if authenticated:
            self.emit("INFO", "Đã tìm thấy profile gflow hợp lệ.")
            return True, ""
        if self.is_cancelled():
            return False, "Đã dừng bởi người dùng"
        self.emit("INFO", "Chưa có profile. Đang mở trình duyệt để đăng nhập Google Flow…")
        self.emit("INFO", "Trong cửa sổ trình duyệt, đăng nhập tài khoản có quyền Flow/Veo rồi hoàn tất bước xác nhận.")
        logged_in, error, _ = self.run_command([executable, "auth", "login"])
        return logged_in, error

    def create_project(self, title: str) -> tuple[str | None, str]:
        ok, error, lines = self.run_command(self.project_command(title))
        result = json_from_cli(lines, "project_id")
        project_id = result.get("project_id") if result else None
        if ok and isinstance(project_id, str) and project_id:
            return project_id, ""
        return None, error or "gflow không trả về Project ID sau khi tạo dự án."

    def cancel(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=12)
        self.master = master
        self.scenes: list[Scene] = []
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.running = False
        self.cancel_requested = False
        self.runner: GflowRunner | None = None
        self.file_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(APP_DIR / "output"))
        self.image_model_var = tk.StringVar(value="Nano Banana 2 × 1")
        self.video_model_var = tk.StringVar(value="Omni Flash × 1")
        self.video_settings_var = tk.StringVar(value="Native Flow · 8 giây")
        self.aspect_var = tk.StringVar(value="16:9")
        self.project_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Sẵn sàng")
        self._build()
        self.pack(fill="both", expand=True)
        self.after(100, self._drain_events)

    def _build(self) -> None:
        self.master.title("GFlow Veo Batch Studio")
        self.master.minsize(940, 650)
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)
        top = ttk.LabelFrame(self, text="Tệp phân cảnh", padding=8)
        top.pack(fill="x")
        ttk.Entry(top, textvariable=self.file_var).pack(side="left", fill="x", expand=True)
        ttk.Button(top, text="Chọn tệp…", command=self.load_file).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Kiểm tra gflow", command=self.check_gflow).pack(side="left", padx=(8, 0))

        opts = ttk.LabelFrame(self, text="Thiết lập pipeline cố định", padding=8)
        opts.pack(fill="x", pady=(8, 0))
        fields = [("Thư mục xuất", self.output_var, 34, "normal"), ("Ảnh", self.image_model_var, 20, "readonly"),
                  ("Video", self.video_model_var, 18, "readonly"), ("Thiết lập video", self.video_settings_var, 18, "readonly"),
                  ("Tỷ lệ", self.aspect_var, 8, "normal"), ("Project ID", self.project_var, 20, "normal")]
        for col, (label, variable, width, state) in enumerate(fields):
            ttk.Label(opts, text=label).grid(row=0, column=col, sticky="w", padx=3)
            ttk.Entry(opts, textvariable=variable, width=width, state=state).grid(row=1, column=col, sticky="ew", padx=3)
        ttk.Button(opts, text="Chọn…", command=self.pick_output).grid(row=1, column=6, padx=4)

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=8)
        self.image_btn = ttk.Button(actions, text="1. Tạo tất cả ảnh", command=self.start_images)
        self.image_btn.pack(side="left")
        self.retry_btn = ttk.Button(actions, text="↻ Tạo lại ảnh đã tick", command=lambda: self.start_images(only_selected=True))
        self.retry_btn.pack(side="left", padx=6)
        self.video_btn = ttk.Button(actions, text="2. Tạo video từ ảnh", command=self.start_videos)
        self.video_btn.pack(side="left")
        self.stop_btn = ttk.Button(actions, text="■ Dừng sau cảnh hiện tại", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(actions, text="Xóa log", command=self.clear_log).pack(side="left")
        ttk.Label(actions, textvariable=self.status_var).pack(side="right")

        table_box = ttk.LabelFrame(self, text="Hàng đợi phân cảnh", padding=6)
        table_box.pack(fill="both", expand=True)
        cols = ("retry", "id", "status", "image", "video", "prompt", "error")
        self.table = ttk.Treeview(table_box, columns=cols, show="headings", height=12)
        for col, label, width in [("retry", "Tạo lại", 65), ("id", "ID", 105), ("status", "Trạng thái", 115), ("image", "Ảnh Nano Banana", 180),
                                  ("video", "Video Omni Flash", 180), ("prompt", "Prompt video", 320), ("error", "Lỗi", 180)]:
            self.table.heading(col, text=label); self.table.column(col, width=width, stretch=(col == "prompt"))
        scroll = ttk.Scrollbar(table_box, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True); scroll.pack(side="right", fill="y")
        self.table.bind("<Button-1>", self.toggle_retry)
        self.table.bind("<<TreeviewSelect>>", self.show_preview)
        self.table.tag_configure("success", foreground="#157a32")
        self.table.tag_configure("failed", foreground="#b42318")
        preview_box = ttk.LabelFrame(self, text="Xem trước ảnh — chọn một scene trong bảng", padding=6)
        preview_box.pack(fill="x", pady=(8, 0))
        self.preview_label = ttk.Label(preview_box, text="Chưa có ảnh được chọn", anchor="center")
        self.preview_label.pack(fill="x", ipady=4)
        self.preview_image: ImageTk.PhotoImage | None = None
        log_box = ttk.LabelFrame(self, text="Nhật ký thực thi", padding=6)
        log_box.pack(fill="both", expand=True, pady=(8, 0))
        self.log = tk.Text(log_box, height=10, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

    def log_line(self, level: str, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal"); self.log.insert("end", f"[{stamp}] {level:<5} {message}\n")
        self.log.see("end"); self.log.configure(state="disabled")

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def load_file(self) -> None:
        chosen = filedialog.askopenfilename(title="Chọn tệp phân cảnh", filetypes=[("Scene files", "*.json *.jsonl *.ndjson *.csv *.tsv *.txt"), ("All files", "*.*")])
        if not chosen: return
        try:
            self.scenes, defaults = read_scenes(Path(chosen))
            self.file_var.set(chosen)
            for key, var in [("aspect", self.aspect_var), ("project", self.project_var)]:
                if defaults.get(key) not in (None, ""): var.set(str(defaults[key]))
            self.refresh_table(); self.log_line("INFO", f"Đã nạp {len(self.scenes)} cảnh từ {Path(chosen).name}")
        except Exception as exc:
            messagebox.showerror("Không thể đọc tệp", str(exc)); self.log_line("ERROR", str(exc))

    def pick_output(self) -> None:
        chosen = filedialog.askdirectory(title="Chọn thư mục xuất")
        if chosen: self.output_var.set(chosen)

    def refresh_table(self) -> None:
        self.table.delete(*self.table.get_children())
        for idx, scene in enumerate(self.scenes):
            tag = "success" if scene.status == "Thành công" else "failed" if scene.status in {"Lỗi", "Đã dừng"} else ""
            tick = "☑" if scene.retry_selected else "☐"
            self.table.insert("", "end", iid=str(idx), values=(tick, scene.id, scene.status, scene.image_output, scene.video_output, scene.video_prompt, scene.error), tags=(tag,))

    def toggle_retry(self, event: tk.Event) -> None:
        if self.running or self.table.identify_column(event.x) != "#1": return
        item = self.table.identify_row(event.y)
        if item:
            scene = self.scenes[int(item)]
            scene.retry_selected = not scene.retry_selected
            self.refresh_table()
            self.table.selection_set(item)
            return "break"

    def show_preview(self, _event: tk.Event | None = None) -> None:
        selected = self.table.selection()
        if not selected: return
        scene = self.scenes[int(selected[0])]
        image_path = Path(scene.image_output)
        if not scene.image_output or not image_path.is_file():
            self.preview_image = None
            self.preview_label.configure(image="", text="Scene này chưa có ảnh local để xem trước.")
            return
        try:
            with Image.open(image_path) as source:
                preview = source.copy()
            preview.thumbnail((320, 180))
            self.preview_image = ImageTk.PhotoImage(preview)
            self.preview_label.configure(image=self.preview_image, text="")
        except Exception as exc:
            self.preview_image = None
            self.preview_label.configure(image="", text=f"Không thể mở ảnh: {exc}")

    def check_gflow(self) -> None:
        def worker() -> None:
            try:
                result = subprocess.run([shutil.which("gflow") or "gflow", "--version"], text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=20)
                self.events.put(("log", ("INFO" if result.returncode == 0 else "ERROR", (result.stdout or result.stderr).strip())))
            except Exception as exc: self.events.put(("log", ("ERROR", f"Không kiểm tra được gflow: {exc}")))
        threading.Thread(target=worker, daemon=True).start()

    def _prepare_run(self) -> tuple[Path, dict[str, str]] | None:
        if self.running: return None
        if not self.scenes:
            messagebox.showwarning("Chưa có cảnh", "Hãy chọn tệp phân cảnh trước."); return None
        output = Path(self.output_var.get().strip() or APP_DIR / "output").resolve()
        try: output.mkdir(parents=True, exist_ok=True)
        except OSError as exc: messagebox.showerror("Lỗi thư mục output", str(exc)); return None
        if self.aspect_var.get().strip() not in {"16:9", "9:16"}:
            messagebox.showerror("Tỷ lệ không hợp lệ", "Chỉ dùng 16:9 hoặc 9:16."); return None
        self.running, self.cancel_requested = True, False
        self.image_btn.configure(state="disabled"); self.retry_btn.configure(state="disabled"); self.video_btn.configure(state="disabled"); self.stop_btn.configure(state="normal")
        return output, {"aspect": self.aspect_var.get().strip(), "project": self.project_var.get().strip()}

    def start_images(self, only_selected: bool = False) -> None:
        prepared = self._prepare_run()
        if not prepared: return
        indexes = [i for i, scene in enumerate(self.scenes) if scene.retry_selected] if only_selected else list(range(len(self.scenes)))
        if not indexes:
            self.running = False; self.image_btn.configure(state="normal"); self.retry_btn.configure(state="normal"); self.video_btn.configure(state="normal"); self.stop_btn.configure(state="disabled")
            messagebox.showinfo("Chưa chọn scene", "Hãy tick cột 'Tạo lại' cho ảnh cần tạo lại."); return
        output, values = prepared
        threading.Thread(target=self._run_images, args=(indexes, output, values), daemon=True).start()

    def start_videos(self) -> None:
        prepared = self._prepare_run()
        if not prepared: return
        indexes = [i for i, scene in enumerate(self.scenes) if scene.image_output and Path(scene.image_output).is_file()]
        if not indexes:
            self.running = False; self.image_btn.configure(state="normal"); self.retry_btn.configure(state="normal"); self.video_btn.configure(state="normal"); self.stop_btn.configure(state="disabled")
            messagebox.showwarning("Chưa có ảnh", "Hãy tạo và duyệt ảnh trước khi tạo video."); return
        output, values = prepared
        if not values["project"]:
            self.running = False; self.image_btn.configure(state="normal"); self.retry_btn.configure(state="normal"); self.video_btn.configure(state="normal"); self.stop_btn.configure(state="disabled")
            messagebox.showerror("Thiếu Project ID", "Hãy tạo ảnh bằng app trước hoặc nhập Google Flow Project ID tương ứng."); return
        threading.Thread(target=self._run_videos, args=(indexes, output, values), daemon=True).start()

    def _authenticate_and_project(self, values: dict[str, str], create_project: bool) -> tuple[bool, str]:
        assert self.runner
        authenticated, error = self.runner.ensure_authenticated()
        if not authenticated: return False, error or "Không thể đăng nhập Google Flow."
        if values["project"]: return True, ""
        if not create_project: return False, "Thiếu Google Flow Project ID."
        title = f"GFlow Veo Batch {datetime.now():%Y-%m-%d %H-%M-%S}"
        self.events.put(("log", ("INFO", f"Tạo một Flow Project dùng chung: {title}")))
        project_id, error = self.runner.create_project(title)
        if not project_id: return False, error or "Không thể tạo Flow Project."
        values["project"] = project_id; self.events.put(("project", project_id))
        self.events.put(("log", ("INFO", f"Flow Project đã tạo: {project_id}")))
        return True, ""

    def _run_images(self, indexes: list[int], output: Path, values: dict[str, str]) -> None:
        self.runner = GflowRunner(lambda level, msg: self.events.put(("log", (level, msg))), lambda: self.cancel_requested)
        ready, reason = self._authenticate_and_project(values, create_project=True)
        if not ready:
            self.events.put(("log", ("ERROR", reason))); self.events.put(("done", "ảnh")); return
        for idx in indexes:
            if self.cancel_requested: break
            scene = self.scenes[idx]; scene.status, scene.error = "Đang tạo ảnh", ""; scene.image_output = ""; scene.video_output = ""; scene.retry_selected = False
            self.events.put(("table", None)); self.events.put(("log", ("INFO", f"{scene.id}: tạo 1 ảnh Nano Banana 2")))
            work_dir = Path(tempfile.mkdtemp(prefix=f"gflow-image-{idx:03d}-")); image_dir = work_dir / "image"; image_dir.mkdir()
            ok, error, lines = self.runner.run_command(self.runner.image_command(scene, image_dir, values))
            result = json_from_cli(lines, "images"); images = result.get("images", []) if result else []
            local_path = Path(images[0]["local_path"]) if len(images) == 1 and isinstance(images[0], dict) and images[0].get("local_path") else None
            if ok and local_path and local_path.is_file():
                try:
                    target_dir = output / scene.id / "image"; target_dir.mkdir(parents=True, exist_ok=True)
                    target = target_dir / local_path.name; shutil.copy2(local_path, target)
                    scene.image_output, scene.status = str(target), "Ảnh đã tạo"
                except OSError as exc: ok, error = False, f"Không thể lưu ảnh: {exc}"
            elif ok: ok, error = False, "gflow không trả về ảnh local để duyệt."
            if not ok: scene.status, scene.error = ("Đã dừng" if self.cancel_requested else "Lỗi"), error
            self.events.put(("table", None)); self.events.put(("log", ("INFO" if ok else "ERROR", f"{scene.id}: {scene.status}" + (f" — {error}" if error else ""))))
        self.events.put(("done", "ảnh"))

    def _run_videos(self, indexes: list[int], output: Path, values: dict[str, str]) -> None:
        self.runner = GflowRunner(lambda level, msg: self.events.put(("log", (level, msg))), lambda: self.cancel_requested)
        ready, reason = self._authenticate_and_project(values, create_project=False)
        if not ready:
            self.events.put(("log", ("ERROR", reason))); self.events.put(("done", "video")); return
        for idx in indexes:
            if self.cancel_requested: break
            scene = self.scenes[idx]; scene.status, scene.error = "Đang tạo video", ""; self.events.put(("table", None))
            work_dir = Path(tempfile.mkdtemp(prefix=f"gflow-video-{idx:03d}-")); video_dir = work_dir / "video"; video_dir.mkdir()
            input_image = work_dir / Path(scene.image_output).name
            try:
                shutil.copy2(scene.image_output, input_image)
            except OSError as exc:
                scene.status, scene.error = "Lỗi", f"Không thể đọc ảnh đã duyệt: {exc}"
                self.events.put(("table", None)); self.events.put(("log", ("ERROR", f"{scene.id}: {scene.error}")))
                continue
            before = {p for p in video_dir.rglob("*") if p.is_file()}
            self.events.put(("log", ("INFO", f"{scene.id}: upload ảnh vào Flow Project và tạo Omni Flash, 8 giây")))
            ok, error, _ = self.runner.run_command(self.runner.video_command(scene, str(input_image), video_dir, values))
            video = new_media_file(video_dir, before, VIDEO_EXTENSIONS) if ok else None
            if ok and video:
                try:
                    final_dir = output / scene.id / "video"; shutil.copytree(video_dir, final_dir, dirs_exist_ok=True)
                    scene.video_output, scene.status = str(final_dir / video.relative_to(video_dir)), "Thành công"
                except OSError as exc: ok, error = False, f"Không thể lưu video: {exc}"
            elif ok: ok, error = False, "gflow không tìm thấy video mới trong output."
            if not ok: scene.status, scene.error = ("Đã dừng" if self.cancel_requested else "Lỗi"), error
            self.events.put(("table", None)); self.events.put(("log", ("INFO" if ok else "ERROR", f"{scene.id}: {scene.status}" + (f" — {error}" if error else ""))))
        self.events.put(("done", "video"))

    def stop(self) -> None:
        self.cancel_requested = True
        self.status_var.set("Đang yêu cầu dừng…")
        if self.runner: self.runner.cancel()

    def _drain_events(self) -> None:
        try:
            while True:
                event, data = self.events.get_nowait()
                if event == "log": self.log_line(*data)
                elif event == "table": self.refresh_table()
                elif event == "project": self.project_var.set(data)
                elif event == "done":
                    self.running = False; self.runner = None
                    self.image_btn.configure(state="normal"); self.retry_btn.configure(state="normal"); self.video_btn.configure(state="normal"); self.stop_btn.configure(state="disabled")
                    succeeded = sum(s.status == ("Ảnh đã tạo" if data == "ảnh" else "Thành công") for s in self.scenes)
                    self.status_var.set(f"Hoàn tất pha {data}: {succeeded}/{len(self.scenes)} thành công")
                    self.log_line("INFO", self.status_var.get())
        except queue.Empty: pass
        self.after(100, self._drain_events)

    def _on_close(self) -> None:
        if self.running and not messagebox.askyesno("Đang chạy", "Dừng tiến trình và đóng ứng dụng?"): return
        self.stop(); self.master.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
