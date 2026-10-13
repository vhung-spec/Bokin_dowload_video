import io
import subprocess
import sys
import time
from pathlib import Path

import pytest

import app as web
import desktop
from clipper import Clip


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(web, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(web, "OUTPUT_DIR", tmp_path / "out")


def test_reads_ytdlp_requirement_from_requirements_txt():
    assert desktop._ytdlp_requirement().startswith("yt-dlp[default]>=")


def test_update_only_runs_inside_bundled_python(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "EMBEDDED_MARKER", tmp_path / "missing")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("không được chạy pip"))
    assert desktop.update_ytdlp().startswith("bỏ qua")


def test_update_runs_pip_then_waits_before_checking_again(monkeypatch, tmp_path):
    marker = tmp_path / "bokin-embedded"
    marker.touch()
    monkeypatch.setattr(desktop, "EMBEDDED_MARKER", marker)
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert desktop.update_ytdlp() == "ok"
    assert commands[0][1:6] == ["-m", "pip", "install", "-q", "-U"]
    assert commands[0][-1] == desktop._ytdlp_requirement()
    assert desktop.update_ytdlp() == "bỏ qua (vừa kiểm tra gần đây)"
    assert len(commands) == 1


def test_failed_update_is_retried_next_time(monkeypatch, tmp_path):
    marker = tmp_path / "bokin-embedded"
    marker.touch()
    monkeypatch.setattr(desktop, "EMBEDDED_MARKER", marker)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", "No network"))
    assert desktop.update_ytdlp() == "lỗi: No network"
    assert "last_update_check" not in desktop._load_settings()


def test_ping_and_idle_tracking(monkeypatch):
    client = web.app.test_client()
    assert client.get("/api/ping").get_json() == {"ok": True}
    assert web.idle_seconds() < 1

    seen_while_downloading = []

    def fake_download(url, out_dir, **kwargs):
        monkeypatch.setattr(web, "_last_request", time.monotonic() - 999)
        seen_while_downloading.append(web.idle_seconds())
        path = Path(out_dir) / "a.mp4"
        path.write_bytes(b"x")
        return Clip(path=path, title="a", video_id="a", start=0, end=15)

    monkeypatch.setattr(web, "download_clip", fake_download)
    assert client.post("/api/save", json={"url": "https://youtu.be/a"}).status_code == 200
    assert seen_while_downloading == [0]  # đang tải thì không bao giờ coi là "trang đã đóng"
    monkeypatch.setattr(web, "_last_request", time.monotonic() - 999)
    assert web.idle_seconds() >= 999


def test_desktop_page_hides_browser_download_and_shows_versions():
    page = web.app.test_client().get("/?desktop=1").get_data(as_text=True)
    assert 'id="download" href="#" hidden' in page
    assert f"Bokin Clip {web.APP_VERSION}" in page
    assert 'id="download" href="#" hidden' not in web.app.test_client().get("/").get_data(as_text=True)


def test_smoke_test_starts_server():
    assert desktop.smoke_test() == 0


def test_log_never_crashes_on_non_utf8_console(monkeypatch):
    # Như khi chạy python.exe với đầu ra bị chuyển hướng trên Windows: bảng mã cp1252.
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    desktop.log("app chạy tại http://127.0.0.1")  # không được ném lỗi
    desktop._redirect_output()
    desktop.log("đã chuyển sang UTF-8")
    stream.flush()
    assert "đã chuyển sang UTF-8".encode() in stream.buffer.getvalue()
