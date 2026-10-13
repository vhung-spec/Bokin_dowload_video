"""Giao diện web: dán link YouTube, bấm nút, đoạn đã cắt được lưu vào thư mục trên máy.

Thư mục lưu chọn ngay trong app (nút "Chọn…" hoặc gõ đường dẫn) và được nhớ trong
settings.json cạnh file này. Lần đầu dùng Downloads/Bokin Clip, hoặc biến môi trường
BOKIN_OUTPUT_DIR nếu có.

Chạy:  python app.py   rồi mở http://127.0.0.1:8765  (thêm --open để tự mở trình duyệt)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

import yt_dlp.version
from flask import Flask, jsonify, render_template, request, send_file

from clipper import (
    DEFAULT_DURATION,
    DEFAULT_QUALITY,
    MAX_DURATION,
    QUALITIES,
    ClipError,
    download_clip,
    parse_seconds,
    validate_youtube_url,
)

APP_VERSION = "1.0.0"
SETTINGS_FILE = Path(__file__).with_name("settings.json")
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _initial_output_dir() -> Path:
    saved = _load_settings().get("output_dir") or os.environ.get("BOKIN_OUTPUT_DIR")
    if saved:
        return Path(saved)
    downloads = Path.home() / "Downloads"
    return (downloads if downloads.is_dir() else Path.home()) / "Bokin Clip"


OUTPUT_DIR = _initial_output_dir()

app = Flask(__name__)

# Dùng khi app chạy trong trình duyệt thay vì cửa sổ riêng (desktop.py): biết lúc nào trang đã đóng.
_last_request = time.monotonic()
_active_downloads = 0
_activity_lock = threading.Lock()


@app.before_request
def _touch():
    global _last_request
    _last_request = time.monotonic()


def idle_seconds() -> float:
    """Số giây không có request nào (trang mở thì gửi /api/ping 30 giây một lần); 0 khi đang tải."""
    return 0 if _active_downloads else time.monotonic() - _last_request


@app.get("/")
def index():
    return render_template(
        "index.html",
        default_duration=DEFAULT_DURATION,
        max_duration=MAX_DURATION,
        qualities=QUALITIES,
        default_quality=DEFAULT_QUALITY,
        output_dir=str(OUTPUT_DIR),
        desktop=request.args.get("desktop") == "1",
        version=APP_VERSION,
        ytdlp_version=yt_dlp.version.__version__,
    )


@app.get("/api/ping")
def ping():
    return jsonify(ok=True)


def _clip_options(data) -> tuple[str, dict]:
    url = validate_youtube_url(data.get("url", ""))
    try:
        quality = int(data.get("quality") or DEFAULT_QUALITY)
    except (TypeError, ValueError):
        quality = None
    if quality not in QUALITIES:
        raise ClipError(f"Chất lượng phải là một trong: {', '.join(map(str, QUALITIES))}.")
    return url, {
        "start": parse_seconds(data.get("start"), 0),
        "duration": parse_seconds(data.get("duration"), DEFAULT_DURATION),
        "max_height": quality,
        "precise": str(data.get("precise", "")).lower() in ("1", "true", "on", "yes"),
    }


def _download_to_output(data):
    """Tải đoạn video rồi chuyển vào OUTPUT_DIR. Trả về (đường dẫn file, Clip)."""
    global _active_downloads
    url, options = _clip_options(data)
    with _activity_lock:
        _active_downloads += 1
    try:
        return _download_and_move(url, options)
    finally:
        with _activity_lock:
            _active_downloads -= 1


def _download_and_move(url: str, options: dict):
    folder = OUTPUT_DIR
    # Tải trong thư mục tạm rồi mới chuyển sang, để thư mục lưu không dính file dở dang.
    with tempfile.TemporaryDirectory(prefix="bokin-") as tmp:
        result = download_clip(url, tmp, **options)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / result.path.name
            target.unlink(missing_ok=True)
            shutil.move(result.path, target)
        except OSError as e:
            raise ClipError(f"Không lưu được file vào {folder}: {e.strerror or e}") from e
    return target, result


@app.post("/api/clip")
def clip():
    """Tab "Một video": lưu vào thư mục, đồng thời gửi file về để xem trước trên trang."""
    try:
        target, _ = _download_to_output(request.get_json(silent=True) or request.form)
    except ClipError as e:
        return jsonify(error=str(e)), 400
    response = send_file(target, mimetype="video/mp4", as_attachment=True, download_name=target.name)
    response.headers["X-Saved-Path"] = quote(str(target))
    return response


@app.post("/api/save")
def save():
    """Tab "Nhiều video": chỉ lưu vào thư mục, trả về thông tin file."""
    try:
        target, result = _download_to_output(request.get_json(silent=True) or request.form)
    except ClipError as e:
        return jsonify(error=str(e)), 400
    return jsonify(file=target.name, path=str(target), title=result.title, size=target.stat().st_size,
                   folder=str(target.parent))


def _from_this_computer() -> bool:
    # Các thao tác trên ổ đĩa / cửa sổ của máy chạy app chỉ cho phép khi người bấm ngồi ở chính máy đó.
    return request.remote_addr in ("127.0.0.1", "::1")


def _not_local():
    return jsonify(error="Chỉ làm được việc này trên chính máy đang chạy app."), 403


def _set_output_dir(raw: str) -> Path:
    global OUTPUT_DIR
    raw = (raw or "").strip().strip('"').strip()
    if not raw:
        raise ClipError("Hãy nhập đường dẫn thư mục.")
    folder = Path(os.path.expandvars(raw)).expanduser()
    if not folder.is_absolute():
        raise ClipError("Hãy nhập đường dẫn đầy đủ, ví dụ D:\\Video\\Clip hoặc /Users/ten/Movies/Clip.")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".bokin-write-test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise ClipError(f"Không ghi được vào thư mục {folder}: {e.strerror or e}") from e

    OUTPUT_DIR = folder
    try:
        SETTINGS_FILE.write_text(json.dumps({**_load_settings(), "output_dir": str(folder)},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # không nhớ được cho lần sau thì vẫn dùng được trong lần chạy này
    return folder


@app.post("/api/folder")
def change_folder():
    if not _from_this_computer():
        return _not_local()
    data = request.get_json(silent=True) or request.form
    try:
        folder = _set_output_dir(data.get("folder", ""))
    except ClipError as e:
        return jsonify(error=str(e)), 400
    return jsonify(folder=str(folder))


# Hộp thoại chọn thư mục chạy trong tiến trình Python riêng: tkinter phải chạy ở luồng chính
# (macOS bắt buộc), trong khi mỗi request của Flask chạy ở một luồng phụ.
_PICKER = """
import sys, tkinter
from tkinter import filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
print(filedialog.askdirectory(parent=root, initialdir=sys.argv[1], mustexist=False,
                              title="Chọn thư mục lưu video") or "", end="")
"""


def _ask_folder(initial: Path) -> str | None:
    try:
        result = subprocess.run(
            [sys.executable, "-c", _PICKER, str(initial if initial.is_dir() else Path.home())],
            capture_output=True, text=True, encoding="utf-8", timeout=600,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ClipError(f"Không mở được hộp thoại chọn thư mục: {e}") from e
    if result.returncode != 0:
        raise ClipError("Máy chưa có tkinter nên không mở được hộp thoại chọn thư mục. "
                        "Hãy gõ hoặc dán đường dẫn vào ô \"Lưu vào\".")
    return result.stdout.strip() or None


@app.post("/api/choose-folder")
def choose_folder():
    if not _from_this_computer():
        return _not_local()
    try:
        chosen = _ask_folder(OUTPUT_DIR)
        if not chosen:
            return jsonify(cancelled=True, folder=str(OUTPUT_DIR))
        folder = _set_output_dir(chosen)
    except ClipError as e:
        return jsonify(error=str(e)), 400
    return jsonify(folder=str(folder))


def _reveal(folder: Path) -> None:
    if sys.platform == "win32":
        os.startfile(folder)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])


@app.post("/api/open-folder")
def open_folder():
    if not _from_this_computer():
        return _not_local()
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        _reveal(OUTPUT_DIR)
    except OSError as e:
        return jsonify(error=f"Không mở được thư mục {OUTPUT_DIR}: {e}"), 500
    return jsonify(folder=str(OUTPUT_DIR))


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    if "--open" in sys.argv:  # dùng bởi file chạy nhanh: tự mở trình duyệt khi server sẵn sàng
        threading.Timer(1.0, webbrowser.open, [f"http://127.0.0.1:{port}"]).start()
    print(f"Bokin Clip đang chạy tại http://127.0.0.1:{port}  (đóng cửa sổ này để tắt)")
    print(f"Video được lưu vào: {OUTPUT_DIR}")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # app chạy trên máy cá nhân, bỏ log từng request
    app.run(host=host, port=port)
