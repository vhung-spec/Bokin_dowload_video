"""Giao diện web: dán link YouTube, bấm nút, nhận file mp4 của đoạn đã cắt.

Chạy:  python app.py   rồi mở http://127.0.0.1:8765  (thêm --open để tự mở trình duyệt)
"""

from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
import threading
import webbrowser

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

app = Flask(__name__)


@app.get("/")
def index():
    return render_template(
        "index.html",
        default_duration=DEFAULT_DURATION,
        max_duration=MAX_DURATION,
        qualities=QUALITIES,
        default_quality=DEFAULT_QUALITY,
    )


@app.post("/api/clip")
def clip():
    data = request.get_json(silent=True) or request.form
    try:
        url = validate_youtube_url(data.get("url", ""))
        start = parse_seconds(data.get("start"), 0)
        duration = parse_seconds(data.get("duration"), DEFAULT_DURATION)
        try:
            quality = int(data.get("quality") or DEFAULT_QUALITY)
        except (TypeError, ValueError):
            quality = None
        if quality not in QUALITIES:
            raise ClipError(f"Chất lượng phải là một trong: {', '.join(map(str, QUALITIES))}.")
        precise = str(data.get("precise", "")).lower() in ("1", "true", "on", "yes")

        # File chỉ vài MB nên đọc hết vào RAM rồi xoá thư mục tạm ngay.
        with tempfile.TemporaryDirectory(prefix="bokin-") as tmp:
            result = download_clip(url, tmp, start=start, duration=duration, max_height=quality, precise=precise)
            payload = io.BytesIO(result.path.read_bytes())
            filename = result.path.name
    except ClipError as e:
        return jsonify(error=str(e)), 400

    return send_file(payload, mimetype="video/mp4", as_attachment=True, download_name=filename)


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    if "--open" in sys.argv:  # dùng bởi file chạy nhanh: tự mở trình duyệt khi server sẵn sàng
        threading.Timer(1.0, webbrowser.open, [f"http://127.0.0.1:{port}"]).start()
    print(f"Bokin Clip đang chạy tại http://127.0.0.1:{port}  (đóng cửa sổ này để tắt)")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # app chạy trên máy cá nhân, bỏ log từng request
    app.run(host=host, port=port)
