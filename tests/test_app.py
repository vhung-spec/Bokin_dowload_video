import json
from pathlib import Path
from urllib.parse import unquote

import pytest

import app as web
from clipper import Clip, ClipError


@pytest.fixture(autouse=True)
def isolated_folders(monkeypatch, tmp_path):
    """Không bao giờ ghi vào thư mục thật của máy (Downloads, settings.json) khi chạy test."""
    folder = tmp_path / "Bokin Clip"
    monkeypatch.setattr(web, "OUTPUT_DIR", folder)
    monkeypatch.setattr(web, "SETTINGS_FILE", tmp_path / "settings.json")
    return folder


@pytest.fixture
def client():
    web.app.config["TESTING"] = True
    return web.app.test_client()


def fake_clip(name="Bài hát [abc] 0s-15s.mp4", content=b"fake-mp4", calls=None):
    def fake_download(url, out_dir, **kwargs):
        if calls is not None:
            calls.append(dict(url=url, out_dir=Path(out_dir), **kwargs))
        path = Path(out_dir) / name
        path.write_bytes(content)
        return Clip(path=path, title="Bài hát", video_id="abc", start=0, end=15)

    return fake_download


def test_index_page(client, isolated_folders):
    page = client.get("/").get_data(as_text=True)
    assert "Link YouTube" in page
    assert "Nhiều video" in page
    assert str(isolated_folders) in page


def test_single_clip_is_saved_and_sent_back(client, monkeypatch, isolated_folders):
    calls = []
    monkeypatch.setattr(web, "download_clip", fake_clip(calls=calls))
    response = client.post("/api/clip", json={
        "url": "https://youtu.be/abc", "start": "1:00", "duration": "20", "quality": "480", "precise": True,
    })

    assert response.status_code == 200
    assert response.data == b"fake-mp4"
    assert response.mimetype == "video/mp4"
    saved = isolated_folders / "Bài hát [abc] 0s-15s.mp4"
    assert unquote(response.headers["X-Saved-Path"]) == str(saved)
    assert saved.read_bytes() == b"fake-mp4"
    call = calls[0]
    assert not call.pop("out_dir").exists()  # tải trong thư mục tạm, đã dọn
    assert call == {"url": "https://youtu.be/abc", "start": 60, "duration": 20, "max_height": 480, "precise": True}


def test_defaults_to_first_15_seconds(client, monkeypatch):
    calls = {}

    def fake_download(url, out_dir, **kwargs):
        calls.update(kwargs)
        raise ClipError("dừng ở đây")

    monkeypatch.setattr(web, "download_clip", fake_download)
    client.post("/api/clip", json={"url": "https://youtu.be/abc"})
    assert calls == {"start": 0, "duration": 15, "max_height": 720, "precise": False}


@pytest.mark.parametrize("body", [
    {"url": "https://example.com/video.mp4"},
    {"url": "https://youtu.be/abc", "quality": "999"},
    {"url": "https://youtu.be/abc", "start": "abc"},
    {"url": "https://youtu.be/abc", "duration": "0"},
])
def test_bad_input_returns_400(client, body):
    response = client.post("/api/clip", json=body)
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_save_puts_clip_in_output_folder(client, monkeypatch, isolated_folders):
    monkeypatch.setattr(web, "download_clip", fake_clip())
    for _ in range(2):  # lần hai ghi đè file cũ, không lỗi
        response = client.post("/api/save", json={"url": "https://youtu.be/abc"})
        assert response.status_code == 200

    saved = isolated_folders / "Bài hát [abc] 0s-15s.mp4"
    assert response.get_json() == {"file": saved.name, "path": str(saved), "title": "Bài hát", "size": 8,
                                   "folder": str(isolated_folders)}
    assert saved.read_bytes() == b"fake-mp4"


def test_save_reports_errors_as_json(client, monkeypatch):
    def blocked(url, out_dir, **kwargs):
        raise ClipError("YouTube đang chặn tải video này (lỗi 403).")

    monkeypatch.setattr(web, "download_clip", blocked)
    response = client.post("/api/save", json={"url": "https://youtu.be/abc"})
    assert response.status_code == 400
    assert "403" in response.get_json()["error"]
    assert client.post("/api/save", json={"url": "https://example.com/x"}).status_code == 400


def test_save_reports_unwritable_folder(client, monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setattr(web, "OUTPUT_DIR", blocker / "sub")
    monkeypatch.setattr(web, "download_clip", fake_clip())
    response = client.post("/api/save", json={"url": "https://youtu.be/abc"})
    assert response.status_code == 400
    assert "Không lưu được file vào" in response.get_json()["error"]


def test_change_folder_is_used_and_remembered(client, monkeypatch, tmp_path):
    new_folder = tmp_path / "Ổ D" / "Clip mới"
    response = client.post("/api/folder", json={"folder": f'  "{new_folder}"  '})  # dán kèm dấu ngoặc kép

    assert response.status_code == 200
    assert response.get_json() == {"folder": str(new_folder)}
    assert new_folder.is_dir() and not any(new_folder.iterdir())  # tạo thư mục, xoá file thử ghi
    assert json.loads(web.SETTINGS_FILE.read_text(encoding="utf-8")) == {"output_dir": str(new_folder)}
    assert web._initial_output_dir() == new_folder  # lần chạy sau vẫn nhớ

    monkeypatch.setattr(web, "download_clip", fake_clip())
    assert client.post("/api/save", json={"url": "https://youtu.be/abc"}).get_json()["folder"] == str(new_folder)
    assert str(new_folder) in client.get("/").get_data(as_text=True)


@pytest.mark.parametrize("folder", ["", "Clip", "../Clip"])
def test_change_folder_needs_full_path(client, folder, isolated_folders):
    response = client.post("/api/folder", json={"folder": folder})
    assert response.status_code == 400
    assert web.OUTPUT_DIR == isolated_folders


def test_change_folder_rejects_unwritable_place(client, tmp_path, isolated_folders):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    response = client.post("/api/folder", json={"folder": str(blocker / "sub")})
    assert response.status_code == 400
    assert "Không ghi được" in response.get_json()["error"]
    assert web.OUTPUT_DIR == isolated_folders


def test_choose_folder_uses_picker(client, monkeypatch, tmp_path, isolated_folders):
    picked = tmp_path / "Picked"
    answers = [str(picked), None]
    monkeypatch.setattr(web, "_ask_folder", lambda initial: answers.pop(0))

    assert client.post("/api/choose-folder").get_json() == {"folder": str(picked)}
    assert web.OUTPUT_DIR == picked
    assert client.post("/api/choose-folder").get_json() == {"cancelled": True, "folder": str(picked)}


def test_picker_failure_suggests_typing_path(client, monkeypatch):
    def no_tk(initial):
        raise ClipError("Máy chưa có tkinter")

    monkeypatch.setattr(web, "_ask_folder", no_tk)
    response = client.post("/api/choose-folder")
    assert response.status_code == 400
    assert "tkinter" in response.get_json()["error"]


@pytest.mark.parametrize("path", ["/api/folder", "/api/choose-folder", "/api/open-folder"])
def test_folder_actions_only_from_this_computer(client, monkeypatch, path, tmp_path, isolated_folders):
    monkeypatch.setattr(web, "_reveal", lambda folder: pytest.fail("không được mở thư mục"))
    monkeypatch.setattr(web, "_ask_folder", lambda initial: pytest.fail("không được mở hộp thoại"))
    response = client.post(path, json={"folder": str(tmp_path / "x")}, environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert response.status_code == 403
    assert web.OUTPUT_DIR == isolated_folders


def test_open_folder(client, monkeypatch, isolated_folders):
    opened = []
    monkeypatch.setattr(web, "_reveal", opened.append)
    response = client.post("/api/open-folder", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == 200
    assert opened == [isolated_folders]
