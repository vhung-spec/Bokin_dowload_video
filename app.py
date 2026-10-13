"""Giao diện web: dán link YouTube, bấm nút, nhận file mp4 của đoạn đã cắt.

Chạy:  python app.py   rồi mở http://127.0.0.1:5000
"""

from __future__ import annotations

import io
import os
import tempfile

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
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")))
