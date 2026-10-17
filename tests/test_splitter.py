import json
import shutil
import subprocess
from pathlib import Path

import pytest

import app as web
import clipper
import splitter
from splitter import Track, find_starts

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                  reason="cần ffmpeg và ffprobe")

LOUD, GAP = -12.0, float("-inf")


def levels(*parts):
    """Dựng chuỗi độ to (mỗi phần tử 0.5 giây) từ các cặp (giây, mức dB)."""
    result = []
    for seconds, level in parts:
        result += [level] * round(seconds / splitter.STEP)
    return result


def test_finds_songs_separated_by_silence():
    curve = levels((70, LOUD), (2, GAP), (90, LOUD), (2, GAP), (80, LOUD))
    starts = find_starts(curve, splitter.STEP, len(curve) * splitter.STEP)
    assert starts == pytest.approx([0, 71.8, 163.8])  # ngay trước khi bài sau vang lên


def test_quiet_passage_inside_a_song_is_not_a_split():
    # Bài 2 có đoạn nhỏ tiếng 4 giây (-45 dB) ngay giữa, dài hơn cả khoảng lặng thật (2 giây).
    curve = levels((70, LOUD), (2, GAP), (40, LOUD), (4, -45.0), (40, LOUD), (2, GAP), (80, LOUD))
    starts = find_starts(curve, splitter.STEP, len(curve) * splitter.STEP)
    assert starts == pytest.approx([0, 71.8, 157.8])


def test_respects_minimum_track_length():
    curve = levels((40, LOUD), (2, GAP), (40, LOUD), (2, GAP), (90, LOUD))
    total = len(curve) * splitter.STEP
    assert find_starts(curve, splitter.STEP, total, min_track=60) == pytest.approx([0, 83.8])
    assert find_starts(curve, splitter.STEP, total, min_track=30) == pytest.approx([0, 41.8, 83.8])


def test_ignores_silence_at_start_and_end_of_file():
    curve = levels((5, GAP), (100, LOUD), (2, GAP), (100, LOUD), (5, GAP))
    assert find_starts(curve, splitter.STEP, len(curve) * splitter.STEP) == pytest.approx([0, 106.8])


def test_continuous_mix_and_silent_file_give_one_track():
    assert find_starts(levels((300, LOUD)), splitter.STEP, 300) == [0.0]
    assert find_starts(levels((300, GAP)), splitter.STEP, 300) == [0.0]


def test_sensitivity_changes_what_counts_as_a_gap():
    curve = levels((80, LOUD), (0.5, -32.0), (80, LOUD))  # chỉ nhỏ đi 20 dB, nửa giây
    total = len(curve) * splitter.STEP
    assert find_starts(curve, splitter.STEP, total, sensitivity="normal") == [0.0]
    assert len(find_starts(curve, splitter.STEP, total, sensitivity="high")) == 2


@pytest.mark.parametrize("name, expected", [
    ('Bài: "Một"/Hai?', "Bài MộtHai"),  # bỏ ký tự Windows không cho dùng trong tên file
    ("  tên. ", "tên"),
    ("a" * 150, "a" * 100),
])
def test_safe_filename(name, expected):
    assert splitter.safe_filename(name) == expected


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:format_tags=title,track,album,artist", "-of", "json", str(path)],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)["format"]


@pytest.fixture(scope="module")
def compilation(tmp_path_factory):
    """MP3 gồm 3 "bài" (65 s, 80 s, 70 s) cách nhau 2 giây im lặng; bài 2 có đoạn nhỏ tiếng 3 giây."""
    if not shutil.which("ffmpeg"):
        pytest.skip("cần ffmpeg")
    root = tmp_path_factory.mktemp("compilation")
    parts = {
        "a.wav": ["-f", "lavfi", "-i", "sine=frequency=440:duration=65,volume=0.5"],
        "b.wav": ["-f", "lavfi", "-i", "sine=frequency=330:duration=40,volume=0.5",
                  "-f", "lavfi", "-i", "sine=frequency=330:duration=3,volume=0.004",
                  "-f", "lavfi", "-i", "sine=frequency=330:duration=37,volume=0.5",
                  "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1"],
        "c.wav": ["-f", "lavfi", "-i", "sine=frequency=550:duration=70,volume=0.5"],
        "gap.wav": ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2"],
    }
    for name, args in parts.items():
        ffmpeg(*args, "-ar", "44100", "-ac", "1", str(root / name))
    (root / "list.txt").write_text("".join(f"file '{name}'\n" for name in
                                           ["a.wav", "gap.wav", "b.wav", "gap.wav", "c.wav"]))
    mix = root / "Tuyển tập.mp3"
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(root / "list.txt"), "-c:a", "libmp3lame", "-b:a", "128k",
           "-metadata", "title=Tuyển tập thử", "-metadata", "artist=Kênh thử", str(mix))
    return mix


@needs_ffmpeg
def test_analyze_and_cut_a_compilation(compilation, tmp_path):
    analysis = splitter.analyze(compilation)
    assert analysis.method == "silence"
    assert (analysis.title, analysis.artist) == ("Tuyển tập thử", "Kênh thử")
    assert [t.start for t in analysis.tracks] == pytest.approx([0, 66.8, 148.8], abs=0.3)

    files = splitter.cut_tracks(compilation, analysis.tracks, tmp_path / "out", album="Tuyển tập thử", artist="Kênh")
    assert [f.name for f in files] == ["01 - Bài 01.mp3", "02 - Bài 02.mp3", "03 - Bài 03.mp3"]
    info = probe(files[1])
    assert float(info["duration"]) == pytest.approx(82, abs=0.3)
    assert info["tags"] == {"title": "Bài 02", "track": "2/3", "album": "Tuyển tập thử", "artist": "Kênh"}


@needs_ffmpeg
def test_chapters_written_on_download_are_used_for_splitting(compilation, tmp_path):
    # Như khi tải nhạc từ video có chia chương: clipper ghi chương vào MP3.
    out = tmp_path / "Có chương [x].mp3"
    section = clipper._Section(0, None, 720, False, True, audio=True)
    chapters = clipper._chapters_within([
        {"start_time": 0, "end_time": 66.8, "title": "Mở đầu"},
        {"start_time": 66.8, "end_time": 148.8, "title": "Bài = hai; #2"},
        {"start_time": 148.8, "end_time": 300, "title": "Kết"},
    ], 0, 218.8)
    clipper._cut([(compilation, 0.0)], out, section, {"title": "Có chương", "artist": "Kênh"}, chapters)
    assert sorted(p.name for p in tmp_path.iterdir()) == [out.name]  # file chương tạm đã xoá

    analysis = splitter.analyze(out)
    assert analysis.method == "chapters"
    assert [(round(t.start, 1), t.title) for t in analysis.tracks] == [
        (0, "Mở đầu"), (66.8, "Bài = hai; #2"), (148.8, "Kết")]
    assert analysis.tracks[-1].end == pytest.approx(analysis.duration)


def test_chapters_within_a_section_are_shifted_and_clipped():
    chapters = [{"start_time": 0, "end_time": 100, "title": "A"}, {"start_time": 100, "end_time": 200, "title": "B"},
                {"start_time": 200, "end_time": 300, "title": "C"}]
    assert clipper._chapters_within(chapters, 90, 210) == [(0, 10, "A"), (10, 110, "B"), (110, 120, "C")]
    assert clipper._chapters_within(None, 0, 10) == []


# --- API của tab "Tách bài" -----------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "OUTPUT_DIR", tmp_path / "Bokin Clip")
    monkeypatch.setattr(web, "_UPLOAD_DIR", tmp_path / "uploads")
    web.app.config["TESTING"] = True
    return web.app.test_client()


@needs_ffmpeg
def test_split_api_by_path(client, compilation, tmp_path):
    response = client.post("/api/split/analyze", json={"path": str(compilation)})
    assert response.status_code == 200
    data = response.get_json()
    assert data["method"] == "silence" and data["name"] == "Tuyển tập.mp3"
    assert [round(t["start"]) for t in data["tracks"]] == [0, 67, 149]

    audio = client.get(f"/api/split/{data['id']}/audio", headers={"Range": "bytes=0-99"})
    assert audio.status_code == 206 and len(audio.data) == 100  # nghe thử, tua được

    # Người dùng sửa: đặt tên, bỏ bài 2, thêm một điểm cắt ở 1:40 giữa bài 2 (bài này được lấy).
    tracks = [{"start": 0, "title": "Một", "include": True},
              {"start": data["tracks"][1]["start"], "title": "Hai", "include": False},
              {"start": "1:40", "title": "Nửa sau bài hai", "include": True},
              {"start": data["tracks"][2]["start"], "title": "", "include": True}]
    cut = client.post(f"/api/split/{data['id']}/cut", json={"tracks": tracks})
    assert cut.status_code == 200
    result = cut.get_json()
    folder = Path(result["folder"])
    assert folder == tmp_path / "Bokin Clip" / "Tuyển tập (tách bài)"
    assert result["files"] == ["01 - Một.mp3", "02 - Nửa sau bài hai.mp3", "03 - Bài 03.mp3"]
    assert float(probe(folder / "02 - Nửa sau bài hai.mp3")["duration"]) == pytest.approx(148.8 - 100, abs=0.3)


@needs_ffmpeg
def test_split_api_by_upload(client, compilation):
    with open(compilation, "rb") as f:
        response = client.post("/api/split/analyze", data={"file": (f, "Tải lên.mp3"), "min_track": "60"},
                               content_type="multipart/form-data")
    assert response.status_code == 200
    data = response.get_json()
    assert data["name"] == "Tải lên.mp3" and len(data["tracks"]) == 3
    cut = client.post(f"/api/split/{data['id']}/cut", json={"tracks": data["tracks"]})
    assert Path(cut.get_json()["folder"]).name == "Tải lên (tách bài)"


@pytest.mark.parametrize("body, message", [
    ({"path": "nhac.mp3"}, "đường dẫn đầy đủ"),
    ({"path": "MISSING"}, "Không tìm thấy file"),  # đường dẫn đầy đủ (có ổ đĩa trên Windows) nhưng không có file
    ({"path": "/x.mp3", "sensitivity": "cực cao"}, "Độ nhạy"),
    ({"path": "/x.mp3", "min_track": "1"}, "từ 5 giây"),
])
def test_split_analyze_rejects_bad_input(client, tmp_path, body, message):
    if body["path"] == "MISSING":
        body = {"path": str(tmp_path / "khong-co.mp3")}
    response = client.post("/api/split/analyze", json=body)
    assert response.status_code == 400
    assert message in response.get_json()["error"]


def test_split_by_path_only_from_this_computer(client, tmp_path):
    response = client.post("/api/split/analyze", json={"path": str(tmp_path)},
                           environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert response.status_code == 403


def test_split_unknown_session(client):
    assert client.post("/api/split/khong-co/cut", json={"tracks": []}).status_code == 404
    assert client.get("/api/split/khong-co/audio").status_code == 404


def test_cut_rejects_bad_track_lists(client, monkeypatch, tmp_path):
    token = "phien-thu"
    analysis = splitter.Analysis(100.0, "silence", [Track(0, 100, "Bài 01")])
    monkeypatch.setitem(web._SPLITS, token, {"path": tmp_path / "x.mp3", "name": "x.mp3",
                                             "analysis": analysis, "uploaded": False})
    for tracks, message in [([], "Chưa có bài"), ([{"start": 0, "include": False}], "ít nhất một bài"),
                            ([{"start": "5:00"}], "nằm ngoài file"), ([{"start": "abc"}], "không hợp lệ")]:
        response = client.post(f"/api/split/{token}/cut", json={"tracks": tracks})
        assert response.status_code == 400 and message in response.get_json()["error"], tracks


def test_open_folder_only_inside_output_folder(client, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(web, "_reveal", opened.append)
    inside = tmp_path / "Bokin Clip" / "Tuyển tập (tách bài)"
    assert client.post("/api/open-folder", json={"path": str(inside)}).status_code == 200
    assert client.post("/api/open-folder", json={"path": str(tmp_path)}).status_code == 400
    assert opened == [inside]
