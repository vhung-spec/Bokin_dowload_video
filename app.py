"""Giao diện web: dán link YouTube, bấm nút, nhận file mp4 của đoạn đã cắt.

Tab "Nhiều video" lưu thẳng từng file vào thư mục trên máy (mặc định Downloads/Bokin Clip,
đổi bằng biến môi trường BOKIN_OUTPUT_DIR).

Chạy:  python app.py   rồi mở http://127.0.0.1:8765  (thêm --open để tự mở trình duyệt)
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

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

def _default_output_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return (downloads if downloads.is_dir() else Path.home()) / "Bokin Clip"


OUTPUT_DIR = Path(os.environ.get("BOKIN_OUTPUT_DIR") or _default_output_dir())

app = Flask(__name__)


@app.get("/")
def index():
    return render_template(
        "index.html",
        default_duration=DEFAULT_DURATION,
        max_duration=MAX_DURATION,
        qualities=QUALITIES,
        default_quality=DEFAULT_QUALITY,
        output_dir=str(OUTPUT_DIR),
    )


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


@app.post("/api/clip")
def clip():
    data = request.get_json(silent=True) or request.form
    try:
        url, options = _clip_options(data)
        # File chỉ vài MB nên đọc hết vào RAM rồi xoá thư mục tạm ngay.
        with tempfile.TemporaryDirectory(prefix="bokin-") as tmp:
            result = download_clip(url, tmp, **options)
            payload = io.BytesIO(result.path.read_bytes())
            filename = result.path.name
    except ClipError as e:
        return jsonify(error=str(e)), 400

    return send_file(payload, mimetype="video/mp4", as_attachment=True, download_name=filename)


@app.post("/api/save")
def save():
    """Cho tab "Nhiều video": lưu thẳng vào OUTPUT_DIR thay vì gửi file về trình duyệt."""
    data = request.get_json(silent=True) or request.form
    try:
        url, options = _clip_options(data)
        # Tải trong thư mục tạm rồi mới chuyển sang, để thư mục lưu không dính file dở dang.
        with tempfile.TemporaryDirectory(prefix="bokin-") as tmp:
            result = download_clip(url, tmp, **options)
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            target = OUTPUT_DIR / result.path.name
            target.unlink(missing_ok=True)
            shutil.move(result.path, target)
    except ClipError as e:
        return jsonify(error=str(e)), 400
    except OSError as e:
        return jsonify(error=f"Không lưu được file vào {OUTPUT_DIR}: {e}"), 500

    return jsonify(file=target.name, title=result.title, size=target.stat().st_size, folder=str(OUTPUT_DIR))


def _reveal(folder: Path) -> None:
    if sys.platform == "win32":
        os.startfile(folder)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])


@app.post("/api/open-folder")
def open_folder():
    # Mở cửa sổ thư mục trên máy chạy app: chỉ cho phép khi người bấm ngồi ở chính máy đó.
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify(error="Chỉ mở được thư mục trên chính máy đang chạy app."), 403
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
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # app chạy trên máy cá nhân, bỏ log từng request
    app.run(host=host, port=port)
