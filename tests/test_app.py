from pathlib import Path

import pytest

import app as web
from clipper import Clip, ClipError


@pytest.fixture
def client():
    web.app.config["TESTING"] = True
    return web.app.test_client()


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Link YouTube" in response.get_data(as_text=True)


def test_returns_clip_as_attachment(client, monkeypatch):
    calls = {}

    def fake_download(url, out_dir, **kwargs):
        calls.update(url=url, **kwargs)
        path = Path(out_dir) / "Bài hát [abc] 0s-15s.mp4"
        path.write_bytes(b"fake-mp4")
        return Clip(path=path, title="Bài hát", video_id="abc", start=0, end=15)

    monkeypatch.setattr(web, "download_clip", fake_download)
    response = client.post("/api/clip", json={
        "url": "https://youtu.be/abc", "start": "1:00", "duration": "20", "quality": "480", "precise": True,
    })

    assert response.status_code == 200
    assert response.data == b"fake-mp4"
    assert response.mimetype == "video/mp4"
    assert "attachment" in response.headers["Content-Disposition"]
    assert calls == {"url": "https://youtu.be/abc", "start": 60, "duration": 20, "max_height": 480, "precise": True}


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


def test_index_has_batch_tab(client):
    page = client.get("/").get_data(as_text=True)
    assert "Nhiều video" in page
    assert str(web.OUTPUT_DIR) in page


def test_save_puts_clip_in_output_folder(client, monkeypatch, tmp_path):
    folder = tmp_path / "Bokin Clip"
    monkeypatch.setattr(web, "OUTPUT_DIR", folder)
    temp_dirs = []

    def fake_download(url, out_dir, **kwargs):
        temp_dirs.append(Path(out_dir))
        path = Path(out_dir) / "Bài hát [abc] 0s-15s.mp4"
        path.write_bytes(b"fake-mp4")
        return Clip(path=path, title="Bài hát", video_id="abc", start=0, end=15)

    monkeypatch.setattr(web, "download_clip", fake_download)
    for _ in range(2):  # lần hai ghi đè file cũ, không lỗi
        response = client.post("/api/save", json={"url": "https://youtu.be/abc"})
        assert response.status_code == 200

    data = response.get_json()
    assert data == {"file": "Bài hát [abc] 0s-15s.mp4", "title": "Bài hát", "size": 8, "folder": str(folder)}
    assert (folder / data["file"]).read_bytes() == b"fake-mp4"
    assert not any(d.exists() for d in temp_dirs)  # thư mục tạm đã được dọn


def test_save_reports_errors_as_json(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "OUTPUT_DIR", tmp_path / "out")

    def blocked(url, out_dir, **kwargs):
        raise ClipError("YouTube đang chặn tải video này (lỗi 403).")

    monkeypatch.setattr(web, "download_clip", blocked)
    response = client.post("/api/save", json={"url": "https://youtu.be/abc"})
    assert response.status_code == 400
    assert "403" in response.get_json()["error"]
    assert client.post("/api/save", json={"url": "https://example.com/x"}).status_code == 400


def test_open_folder_only_from_this_computer(client, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(web, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(web, "_reveal", opened.append)

    assert client.post("/api/open-folder", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert opened == []
    response = client.post("/api/open-folder", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == 200
    assert opened == [tmp_path / "out"]
