"""Bokin Clip bản cài đặt: chạy trong cửa sổ riêng (pywebview + WebView2 của Windows), không cần
trình duyệt hay cửa sổ dòng lệnh.

Khi mở app:
1. hiện màn hình chờ;
2. cập nhật yt-dlp (tối đa 6 giờ một lần) TRƯỚC khi nạp yt_dlp, để không bao giờ chạy lẫn code cũ/mới;
3. chạy server Flask trên một cổng trống rồi nạp giao diện vào cửa sổ.

Nếu không mở được cửa sổ riêng (vd. máy thiếu WebView2), app mở bằng trình duyệt mặc định và tự
tắt khi trang đã đóng được 3 phút.

Tham số dòng lệnh:
  --no-update          bỏ qua bước cập nhật yt-dlp
  --smoke-test         chạy server, kiểm tra trang chủ rồi thoát (dùng khi build)
  --smoke-test-window  như trên nhưng mở cả cửa sổ, chờ trang nạp xong rồi tự đóng
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from html import escape
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "settings.json"  # cùng file với app.py
LOG_FILE = APP_DIR / "bokin.log"
ICON = APP_DIR / "bokin.ico"
UPDATE_EVERY = 6 * 3600
BROWSER_IDLE_EXIT = 180  # giây
# Python đi kèm bộ cài có file đánh dấu này; chỉ khi đó app mới tự chạy pip để cập nhật yt-dlp.
EMBEDDED_MARKER = Path(sys.executable).with_name("bokin-embedded")
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

SPLASH = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><style>
  :root { color-scheme: light dark; }
  body { margin: 0; height: 100vh; display: grid; place-items: center; background: #f6f7f9; color: #5f6673;
         font: 15px/1.5 system-ui, "Segoe UI", sans-serif; }
  b { display: block; margin-bottom: 4px; font-size: 22px; color: #1d2129; }
  @media (prefers-color-scheme: dark) { body { background: #14161a; color: #9aa0a6; } b { color: #e8eaed; } }
</style></head>
<body><div style="text-align:center"><b>Bokin Clip</b><span id="msg">Đang khởi động…</span></div></body></html>"""

ERROR_PAGE = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><style>
  body {{ margin: 0; padding: 32px; font: 15px/1.5 system-ui, "Segoe UI", sans-serif; }}
  pre {{ white-space: pre-wrap; font-size: 12px; background: #f1f2f4; padding: 12px; border-radius: 8px; }}
</style></head>
<body><h2>Bokin Clip không khởi động được</h2>
<p>Hãy tắt rồi mở lại app. Nếu vẫn lỗi, gửi nội dung dưới đây (hoặc file <code>{log}</code>) cho người hỗ trợ.</p>
<pre>{error}</pre></body></html>"""


def log(message: str) -> None:
    try:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)
    except Exception:
        pass  # ghi log không bao giờ được làm hỏng app


def _redirect_output() -> None:
    """pythonw không có cửa sổ dòng lệnh (sys.stdout là None): ghi log ra file để còn tra lỗi."""
    if sys.stdout is not None and sys.stderr is not None:
        # Chạy bằng python.exe với đầu ra bị chuyển hướng (vd. khi build): bảng mã mặc định của
        # Windows (cp1252) không ghi được chữ tiếng Việt.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
        return
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1 << 20:
            LOG_FILE.unlink()
        sys.stdout = sys.stderr = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    except OSError:
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _set_app_id() -> None:
    # Gắn cửa sổ với shortcut "Bokin Clip" (cùng AppUserModelID) để thanh tác vụ hiện đúng biểu tượng.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Bokin.Clip")
        except (AttributeError, OSError):
            pass


def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_settings(**values) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps({**_load_settings(), **values}, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except OSError:
        pass


def _ytdlp_requirement() -> str:
    for line in (APP_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("yt-dlp"):
            return line.strip()
    return "yt-dlp[default]"


def update_ytdlp() -> str:
    """Cập nhật yt-dlp trong Python đi kèm bằng pip. Trả về mô tả ngắn để ghi log."""
    if not EMBEDDED_MARKER.exists():
        return "bỏ qua (không chạy trong Python đi kèm bộ cài)"
    if time.time() - _load_settings().get("last_update_check", 0) < UPDATE_EVERY:
        return "bỏ qua (vừa kiểm tra gần đây)"
    python = Path(sys.executable).with_name("python.exe")  # bản có console để pip chạy bình thường
    # --only-binary: Python nhúng không dựng được gói từ mã nguồn, đừng để pip thử.
    cmd = [str(python if python.exists() else sys.executable), "-m", "pip", "install", "-q", "-U",
           "--only-binary", ":all:", "--disable-pip-version-check", "--no-warn-script-location",
           _ytdlp_requirement()]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=180, **NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"lỗi: {e}"
    if result.returncode != 0:
        return "lỗi: " + " ".join(result.stderr.strip().splitlines()[-3:])
    _save_settings(last_update_check=time.time())
    return "ok"


def start_server():
    """Nạp app (và yt_dlp) rồi chạy Flask trên một cổng trống. Trả về (module app, url)."""
    import app as web
    from werkzeug.serving import make_server

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, web.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return web, f"http://127.0.0.1:{server.server_port}/"


def smoke_test() -> int:
    import imageio_ffmpeg
    import yt_dlp

    import clipper

    web, url = start_server()
    with urllib.request.urlopen(url, timeout=15) as response:
        page = response.read().decode("utf-8")
    if "Bokin Clip" not in page or response.status != 200:
        print("smoke test FAILED: trang chủ không đúng")
        return 1
    ffmpeg = clipper._ffmpeg_location() or "ffmpeg"
    encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", **NO_WINDOW).stdout
    missing = [name for name in ("libmp3lame", "libx264") if name not in encoders]  # nhạc MP3, cắt chính xác
    if missing:
        print(f"smoke test FAILED: ffmpeg ({ffmpeg}) thiếu bộ mã hoá {', '.join(missing)}")
        return 1
    print(f"smoke test ok: {url} | app {web.APP_VERSION} | yt-dlp {yt_dlp.version.__version__}"
          f" | ffmpeg {clipper._ffmpeg_location() or 'PATH'} | bundled ffmpeg {imageio_ffmpeg.get_ffmpeg_exe()}"
          f" | js {clipper._js_runtimes()}")
    return 0


class Api:
    """Hàm Python gọi được từ trang web trong cửa sổ app (window.pywebview.api...)."""

    def __init__(self):
        self._window = None  # tên bắt đầu bằng "_" để pywebview không đưa ra cho JavaScript

    def choose_folder(self, current: str = "") -> str | None:
        import webview

        result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=current or "")
        return result[0] if result else None


def _window_size(webview) -> tuple[int, int]:
    try:
        screen = webview.screens[0]
        return 660, max(560, min(900, screen.height - 80))
    except Exception:
        return 660, 860


def run_in_window(args: set[str]) -> int:
    import webview

    stage = ["tạo cửa sổ"]
    if "--smoke-test-window" in args:
        logging.basicConfig(level=logging.INFO)  # để log của pywebview hiện ra khi build

        def watchdog():  # đừng treo mãi khi build: báo đang kẹt ở bước nào rồi thoát
            time.sleep(120)
            try:
                log(f"smoke test window TIMEOUT, đang ở bước: {stage[0]}")
            finally:
                os._exit(2)

        threading.Thread(target=watchdog, daemon=True).start()

    api = Api()
    width, height = _window_size(webview)
    window = webview.create_window("Bokin Clip", html=SPLASH, js_api=api, width=width, height=height,
                                   min_size=(420, 560), background_color="#F6F7F9")
    api._window = window
    failed = []

    def status(text: str) -> None:
        try:
            window.evaluate_js(f"document.getElementById('msg').textContent = {json.dumps(text)}")
        except Exception:
            pass

    def boot():
        stage[0] = "cửa sổ đã mở, chạy boot()"
        try:
            if "--no-update" not in args:
                status("Đang kiểm tra bản cập nhật của yt-dlp…")
                log(f"cập nhật yt-dlp: {update_ytdlp()}")
            status("Đang mở…")
            web, url = start_server()
            log(f"app {web.APP_VERSION} chạy tại {url}")
            stage[0] = f"nạp giao diện {url}"
            window.load_url(url + "?desktop=1")
            if "--smoke-test-window" in args:
                stage[0] = "chờ giao diện hiện ra"
                for _ in range(60):  # chờ giao diện thật (có ô "Lưu vào") thay cho màn hình chờ
                    time.sleep(1)
                    if window.evaluate_js("!!document.getElementById('folder')"):
                        print(f"smoke test window ok: {window.evaluate_js('document.title')}", flush=True)
                        break
                else:
                    failed.append("giao diện không nạp được trong 60 giây")
                    print("smoke test window FAILED", flush=True)
                window.destroy()
        except Exception:
            error = traceback.format_exc()
            failed.append(error)
            log(error)
            window.load_html(ERROR_PAGE.format(log=escape(str(LOG_FILE)), error=escape(error)))
            if "--smoke-test-window" in args:
                window.destroy()

    stage[0] = "khởi động WebView2 (webview.start)"
    webview.start(boot, gui="edgechromium" if sys.platform == "win32" else None, icon=str(ICON),
                  private_mode=False, storage_path=str(APP_DIR / "webview-data"))
    return 1 if failed else 0


def run_in_browser(args: set[str]) -> int:
    if "--no-update" not in args:
        log(f"cập nhật yt-dlp: {update_ytdlp()}")
    web, url = start_server()
    log(f"không mở được cửa sổ riêng, dùng trình duyệt: {url}")
    webbrowser.open(url)
    # Không có cửa sổ riêng để biết khi nào người dùng tắt app: thoát khi trang đã đóng một lúc.
    while web.idle_seconds() < BROWSER_IDLE_EXIT:
        time.sleep(15)
    return 0


def main() -> int:
    _redirect_output()
    _set_app_id()
    args = set(sys.argv[1:])
    if "--smoke-test" in args:
        return smoke_test()
    try:
        return run_in_window(args)
    except Exception:
        log("không mở được cửa sổ riêng:\n" + traceback.format_exc())
        if "--smoke-test-window" in args:
            return 1
        return run_in_browser(args)


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)  # dừng luôn server và các luồng tải đang chạy nền
