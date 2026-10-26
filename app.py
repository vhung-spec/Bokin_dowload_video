"""Giao diện web: dán link YouTube, bấm nút, đoạn đã cắt được lưu vào thư mục trên máy.

Thư mục lưu chọn ngay trong app (nút "Chọn…" hoặc gõ đường dẫn) và được nhớ trong
settings.json cạnh file này. Lần đầu dùng Downloads/Bokin Clip, hoặc biến môi trường
BOKIN_OUTPUT_DIR nếu có.

Chạy:  python app.py   rồi mở http://127.0.0.1:8765  (thêm --open để tự mở trình duyệt)
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import mimetypes
import os
import secrets
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

import splitter
import spotify

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

APP_VERSION = "1.3.0"
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


def _flag(value) -> bool:
    return str(value or "").lower() in ("1", "true", "on", "yes")


def _clip_options(data) -> tuple[str, dict]:
    if spotify.is_spotify_link(data.get("url", "")):
        raise ClipError("Đây là link Spotify: hãy dán vào tab Spotify.")
    url = validate_youtube_url(data.get("url", ""))
    try:
        quality = int(data.get("quality") or DEFAULT_QUALITY)
    except (TypeError, ValueError):
        quality = None
    if quality not in QUALITIES:
        raise ClipError(f"Chất lượng phải là một trong: {', '.join(map(str, QUALITIES))}.")
    audio = data.get("kind") == "audio"  # "video" (mặc định) hoặc "audio" (nhạc MP3)
    full = audio and _flag(data.get("full"))  # nhạc: lấy đến hết video
    return url, {
        "start": parse_seconds(data.get("start"), 0),
        "duration": None if full else parse_seconds(data.get("duration"), DEFAULT_DURATION),
        "max_height": quality,
        "precise": _flag(data.get("precise")),
        "audio": audio,
    }


@contextlib.contextmanager
def _downloading():
    """Đánh dấu đang tải, để app chạy trong trình duyệt không tự tắt giữa chừng."""
    global _active_downloads
    with _activity_lock:
        _active_downloads += 1
    try:
        yield
    finally:
        with _activity_lock:
            _active_downloads -= 1


def _download_to_output(data):
    """Tải đoạn video rồi chuyển vào OUTPUT_DIR. Trả về (đường dẫn file, Clip)."""
    url, options = _clip_options(data)
    with _downloading():
        return _download_and_move(url, options)


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
    mimetype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    response = send_file(target, mimetype=mimetype, as_attachment=True, download_name=target.name)
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
    folder = OUTPUT_DIR
    wanted = (request.get_json(silent=True) or {}).get("path")
    if wanted:  # vd. thư mục chứa các bài vừa tách; chỉ cho mở thư mục nằm trong thư mục lưu
        folder = Path(wanted)
        try:
            folder.resolve().relative_to(OUTPUT_DIR.resolve())
        except (OSError, ValueError):
            return jsonify(error="Chỉ mở được thư mục nằm trong thư mục lưu."), 400
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _reveal(folder)
    except OSError as e:
        return jsonify(error=f"Không mở được thư mục {folder}: {e}"), 500
    return jsonify(folder=str(folder))


# --- Tab "Tách bài": phân tích một file nhạc dài, xem lại rồi cắt thành từng bài ---------------

_SPLITS: dict[str, dict] = {}  # mã phân tích -> {"path", "name", "analysis", "uploaded"}
_UPLOAD_DIR = Path(tempfile.gettempdir()) / f"bokin-split-{os.getpid()}"


def _prune_old_uploads(max_age: float = 6 * 3600) -> None:
    """Xoá file tải lên còn sót của các lần chạy trước (app tắt đột ngột thì không kịp dọn)."""
    for folder in Path(tempfile.gettempdir()).glob("bokin-split-*"):
        try:
            if folder != _UPLOAD_DIR and time.time() - folder.stat().st_mtime > max_age:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


_prune_old_uploads()


def _split_options(data) -> dict:
    try:
        min_track = float(data.get("min_track") or splitter.DEFAULT_MIN_TRACK)
    except (TypeError, ValueError):
        raise ClipError("Độ dài tối thiểu mỗi bài không hợp lệ.") from None
    if not 5 <= min_track <= 3600:
        raise ClipError("Độ dài tối thiểu mỗi bài phải từ 5 giây đến 60 phút.")
    sensitivity = data.get("sensitivity") or "normal"
    if sensitivity not in splitter.SENSITIVITY:
        raise ClipError("Độ nhạy không hợp lệ.")
    return {"min_track": min_track, "sensitivity": sensitivity}


def _store_upload(upload) -> tuple[Path, str]:
    """Lưu file người dùng tải lên vào thư mục tạm. Chỉ giữ file mới nhất cho đỡ tốn ổ đĩa."""
    name = Path(upload.filename or "nhac.mp3").name
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for token in [token for token, entry in _SPLITS.items() if entry["uploaded"]]:
        del _SPLITS[token]
    for old in _UPLOAD_DIR.iterdir():
        old.unlink(missing_ok=True)
    path = _UPLOAD_DIR / f"{secrets.token_hex(4)}{Path(name).suffix.lower() or '.mp3'}"
    upload.save(path)
    return path, name


@app.post("/api/split/analyze")
def split_analyze():
    """Nhận một file (đường dẫn trên máy này, hoặc tải lên) và trả về danh sách bài nhận diện được."""
    upload = request.files.get("file")
    data = request.form if upload else (request.get_json(silent=True) or {})
    if not upload and not _from_this_computer():
        return _not_local()
    try:
        options = _split_options(data)
        if upload:
            path, name = _store_upload(upload)
        else:
            path = Path(str(data.get("path") or "").strip().strip('"')).expanduser()
            if not path.is_absolute():
                raise ClipError("Hãy chọn file, hoặc dán đường dẫn đầy đủ của file nhạc.")
            if not path.is_file():
                raise ClipError(f"Không tìm thấy file: {path}")
            name = path.name
        analysis = splitter.analyze(path, **options)
    except ClipError as e:
        return jsonify(error=str(e)), 400
    except OSError as e:
        return jsonify(error=f"Không đọc được file: {e}"), 400

    token = secrets.token_urlsafe(9)
    _SPLITS[token] = {"path": path, "name": name, "analysis": analysis, "uploaded": bool(upload)}
    return jsonify(id=token, name=name, duration=analysis.duration, method=analysis.method,
                   tracks=[{"start": track.start, "title": track.title} for track in analysis.tracks])


def _split_entry(token: str):
    entry = _SPLITS.get(token)
    if entry is None:
        return None, (jsonify(error="Phiên tách bài đã hết hạn, hãy chọn lại file và phân tích lại."), 404)
    return entry, None


@app.get("/api/split/<token>/audio")
def split_audio(token):
    """Phát file đang tách để người dùng nghe thử các điểm cắt (hỗ trợ tua)."""
    entry, error = _split_entry(token)
    if error:
        return error
    mimetype = mimetypes.guess_type(entry["name"])[0] or "audio/mpeg"
    return send_file(entry["path"], mimetype=mimetype, conditional=True)


def _tracks_from_request(rows, duration: float) -> list[splitter.Track]:
    """Danh sách bài người dùng đã xem lại: mỗi bài kéo dài đến đầu bài sau (kể cả bài bị bỏ chọn)."""
    if not isinstance(rows, list) or not rows:
        raise ClipError("Chưa có bài nào để cắt.")
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            raise ClipError("Danh sách bài không hợp lệ.")
        start = parse_seconds(row.get("start"), 0)
        if not 0 <= start < duration:
            raise ClipError(f"Thời điểm {row.get('start')} nằm ngoài file (file dài {duration:.0f} giây).")
        parsed.append((start, str(row.get("title") or "").strip(), row.get("include", True) is not False))
    parsed.sort(key=lambda row: row[0])

    tracks = []
    for i, (start, title, include) in enumerate(parsed):
        end = parsed[i + 1][0] if i + 1 < len(parsed) else duration
        if include and end - start >= 0.5:
            tracks.append(splitter.Track(start, end, title or f"Bài {len(tracks) + 1:02d}"))
    if not tracks:
        raise ClipError("Hãy chọn ít nhất một bài để cắt.")
    return tracks


@app.post("/api/split/<token>/cut")
def split_cut(token):
    entry, error = _split_entry(token)
    if error:
        return error
    analysis = entry["analysis"]
    stem = Path(entry["name"]).stem
    out_dir = OUTPUT_DIR / (splitter.safe_filename(f"{stem} (tách bài)") or "Tách bài")
    try:
        tracks = _tracks_from_request((request.get_json(silent=True) or {}).get("tracks"), analysis.duration)
        files = splitter.cut_tracks(entry["path"], tracks, out_dir, album=analysis.title or stem,
                                    artist=analysis.artist)
    except ClipError as e:
        return jsonify(error=str(e)), 400
    except OSError as e:
        return jsonify(error=f"Không lưu được vào {out_dir}: {e.strerror or e}"), 400
    return jsonify(folder=str(out_dir), files=[file.name for file in files])


# --- Tab "Spotify": đọc danh sách bài từ link Spotify, rồi tải từng bài (nguồn nhạc: YouTube) ----------

@app.post("/api/spotify/list")
def spotify_list():
    try:
        playlist = spotify.fetch_list((request.get_json(silent=True) or {}).get("url", ""))
    except ClipError as e:
        return jsonify(error=str(e)), 400
    return jsonify(kind=playlist.kind, name=playlist.name, folder=playlist.folder,
                   limited=playlist.kind == "playlist" and len(playlist.tracks) >= spotify.EMBED_LIMIT,
                   tracks=[dataclasses.asdict(track) for track in playlist.tracks])


@app.post("/api/spotify/track")
def spotify_track():
    data = request.get_json(silent=True) or {}
    # Thư mục con của album/playlist: chỉ lấy tên (không cho đi ra ngoài thư mục lưu).
    out_dir = OUTPUT_DIR / splitter.safe_filename(str(data.get("folder") or ""))
    try:
        track = spotify.track_from_dict(data.get("track"))
        with _downloading():
            target, source = spotify.download_track(track, out_dir)
    except ClipError as e:
        return jsonify(error=str(e)), 400
    except OSError as e:
        return jsonify(error=f"Không lưu được file vào {out_dir}: {e.strerror or e}"), 400
    return jsonify(file=target.name, path=str(target), folder=str(target.parent), size=target.stat().st_size,
                   youtube_url=source["url"], youtube_title=source["title"], youtube_channel=source["channel"])


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    if "--open" in sys.argv:  # dùng bởi file chạy nhanh: tự mở trình duyệt khi server sẵn sàng
        threading.Timer(1.0, webbrowser.open, [f"http://127.0.0.1:{port}"]).start()
    print(f"Bokin Clip đang chạy tại http://127.0.0.1:{port}  (đóng cửa sổ này để tắt)")
    print(f"Video được lưu vào: {OUTPUT_DIR}")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # app chạy trên máy cá nhân, bỏ log từng request
    app.run(host=host, port=port)
