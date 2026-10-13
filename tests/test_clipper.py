import http.server
import json
import os
import re
import shutil
import subprocess
import threading

import pytest

import clipper
from clipper import (ClipError, Clip, download_clip, extract_links, format_selector, main, parse_seconds,
                     validate_youtube_url)

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                  reason="cần ffmpeg và ffprobe")


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?t=10",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/abc123",
    "  https://youtube.com/watch?v=x  ",
])
def test_accepts_youtube_links(url):
    assert validate_youtube_url(url) == url.strip()


@pytest.mark.parametrize("url", [
    "",
    "dQw4w9WgXcQ",
    "ftp://www.youtube.com/watch?v=x",
    "https://example.com/watch?v=x",
    "https://youtube.com.evil.example/watch?v=x",
    "http://127.0.0.1/video.mp4",
])
def test_rejects_other_links(url):
    with pytest.raises(ClipError):
        validate_youtube_url(url)


@pytest.mark.parametrize("value, expected", [
    (None, 0), ("", 0), ("15", 15), ("2.5", 2.5), ("1:30", 90), ("1:02:03", 3723), (7, 7),
])
def test_parse_seconds(value, expected):
    assert parse_seconds(value) == expected


@pytest.mark.parametrize("value", ["abc", "1:x", "nan", "inf"])
def test_parse_seconds_rejects_garbage(value):
    with pytest.raises(ClipError):
        parse_seconds(value)


def test_extract_links_from_pasted_text():
    text = """Danh sách:
    1. https://www.youtube.com/watch?v=aaa
    2. https://youtu.be/bbb?t=5, https://youtu.be/ccc
    https://www.youtube.com/watch?v=aaa   (trùng)
    không phải link: youtube.com/watch?v=ddd
    """
    assert extract_links(text) == [
        "https://www.youtube.com/watch?v=aaa", "https://youtu.be/bbb?t=5", "https://youtu.be/ccc",
    ]


def test_cli_downloads_every_link_and_reports_failures(tmp_path, monkeypatch, capsys):
    listing = tmp_path / "links.txt"
    listing.write_text("https://youtu.be/b\nhttps://youtu.be/a\nhttps://example.com/x\n", encoding="utf-8")
    calls = []

    def fake_download(url, out_dir, **kwargs):
        calls.append((url, kwargs["start"], kwargs["duration"]))
        if url.endswith("/b"):
            raise ClipError("HTTP Error 403")
        return Clip(path=tmp_path / f"{url[-1]}.mp4", title=url, video_id=url[-1], start=0, end=15)

    monkeypatch.setattr(clipper, "download_clip", fake_download)
    code = main(["https://youtu.be/a", "--file", str(listing), "-o", str(tmp_path), "-s", "1:00"])

    out, err = capsys.readouterr()
    assert code == 1
    assert calls == [("https://youtu.be/a", 60, 15), ("https://youtu.be/b", 60, 15)]  # bỏ link trùng
    assert "[1/3] Đã lưu" in out and "Xong 1/3 video." in out
    assert "[2/3] Lỗi (https://youtu.be/b): HTTP Error 403" in err
    assert "[3/3] Lỗi (https://example.com/x)" in err


def test_format_selector_limits_height():
    assert "[height<=480]" in format_selector(480)


@pytest.mark.parametrize("start, duration", [(-1, 15), (0, 0), (0, -5), (0, 10_000)])
def test_download_rejects_bad_range(tmp_path, start, duration):
    with pytest.raises(ClipError):
        download_clip("https://youtu.be/x", tmp_path, start=start, duration=duration)


def test_decodes_ffmpeg_http_403():
    # Trên Windows ffmpeg báo HTTP 403 bằng exit code 3436169992 (AVERROR_HTTP_FORBIDDEN).
    assert clipper._ffmpeg_error(3436169992) == "HTTP Error 403"
    assert clipper._ffmpeg_error(-858797304) == "HTTP Error 403"
    assert clipper._ffmpeg_error(8) == "mã lỗi 8"
    assert "HTTP Error 403" in clipper._clean_message(Exception("ERROR: ffmpeg exited with code 3436169992"))


def test_falls_back_through_every_method_and_explains_403(tmp_path, monkeypatch):
    calls = []

    def blocked(url, out_dir, section, extractor_args):
        calls.append(extractor_args)
        raise ClipError("Không tải được video: HTTP Error 403: Forbidden")

    monkeypatch.setattr(clipper, "_download_by_index", blocked)
    monkeypatch.setattr(clipper, "_download_with_ffmpeg", blocked)
    with pytest.raises(ClipError, match="YouTube đang chặn"):
        download_clip("https://youtu.be/x", tmp_path)
    assert calls[-1] == {"youtube": {"player_client": ["android"]}}
    assert len(calls) == 3


def test_final_errors_are_not_retried(tmp_path, monkeypatch):
    def too_short(*args):
        raise ClipError("Video chỉ dài 10 giây", final=True)

    def unexpected(*args):
        raise AssertionError("không được thử cách khác")

    monkeypatch.setattr(clipper, "_download_by_index", too_short)
    monkeypatch.setattr(clipper, "_download_with_ffmpeg", unexpected)
    with pytest.raises(ClipError, match="chỉ dài"):
        download_clip("https://youtu.be/x", tmp_path, start=20)


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,start_time,duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def probe_duration(path):
    return float(probe(path)["format"]["duration"])


MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT60S"
     minBufferTime="PT2S" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011">
  <Period>
    <AdaptationSet mimeType="video/mp4">
      <Representation id="v" codecs="avc1.64001e" width="640" height="360" bandwidth="2000000">
        <BaseURL>video.mp4</BaseURL>
      </Representation>
    </AdaptationSet>
    <AdaptationSet mimeType="audio/mp4">
      <Representation id="a" codecs="mp4a.40.2" bandwidth="128000" audioSamplingRate="44100">
        <BaseURL>audio.m4a</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """Máy chủ HTTP cục bộ (hỗ trợ Range) thay cho YouTube khi test:
    - sample.mp4: video thường 40 giây (hình + tiếng chung một file)
    - manifest.mpd: video DASH 60 giây giống YouTube, hình và tiếng là hai file fMP4 có mục lục sidx
    """
    if not shutil.which("ffmpeg"):
        pytest.skip("cần ffmpeg")
    root = tmp_path_factory.mktemp("site")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=40",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=40",
           "-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-c:a", "aac", "-shortest",
           "-movflags", "+faststart", str(root / "sample.mp4"))
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=60",
           "-c:v", "libx264", "-preset", "ultrafast", "-g", "150", "-sc_threshold", "0",
           "-movflags", "+dash+global_sidx", str(root / "video.mp4"))
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=60", "-c:a", "aac", "-frag_duration", "10000000",
           "-movflags", "+dash+global_sidx", str(root / "audio.m4a"))
    (root / "manifest.mpd").write_text(MANIFEST)

    served = []

    class RangeHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = root / self.path.split("?")[0].lstrip("/")
            size = path.stat().st_size
            first, last = 0, size - 1
            match = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
            if match:
                first = int(match.group(1))
                last = min(int(match.group(2) or size - 1), size - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", "application/dash+xml" if path.suffix == ".mpd" else "video/mp4")
            self.send_header("Content-Length", str(last - first + 1))
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(first)
                data = f.read(last - first + 1)
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            served.append((path.name, len(data)))

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{server.server_port}", "root": root, "served": served}
    server.shutdown()


@needs_ffmpeg
def test_downloads_first_15_seconds(site, tmp_path):
    clip = download_clip(f"{site['base']}/sample.mp4", tmp_path)
    assert clip.path.suffix == ".mp4"
    assert (clip.start, clip.end) == (0, 15)
    assert probe_duration(clip.path) == pytest.approx(15, abs=0.5)


@needs_ffmpeg
def test_downloads_middle_section(site, tmp_path):
    clip = download_clip(f"{site['base']}/sample.mp4", tmp_path, start=10, duration=5, precise=True)
    assert probe_duration(clip.path) == pytest.approx(5, abs=0.5)


@pytest.fixture
def no_ffmpeg_fallback(monkeypatch):
    def fail(*args):
        raise AssertionError("cách tải theo mục lục sidx lẽ ra phải chạy được")

    monkeypatch.setattr(clipper, "_download_with_ffmpeg", fail)


@needs_ffmpeg
@pytest.mark.parametrize("start, duration, precise", [(0, 15, False), (37, 10, False), (37.5, 4, True)])
def test_dash_downloads_only_needed_segments(site, tmp_path, no_ffmpeg_fallback, start, duration, precise):
    site["served"].clear()
    clip = download_clip(f"{site['base']}/manifest.mpd", tmp_path, start=start, duration=duration, precise=precise)

    info = probe(clip.path)
    assert float(info["format"]["duration"]) == pytest.approx(duration, abs=0.1)
    streams = {s["codec_type"]: s for s in info["streams"]}
    assert set(streams) == {"video", "audio"}
    for stream in streams.values():  # hình và tiếng cùng bắt đầu từ 0, đủ độ dài: không lệch tiếng
        assert float(stream["start_time"]) == pytest.approx(0, abs=0.05)
        assert float(stream["duration"]) == pytest.approx(duration, abs=0.1)

    media = sum(size for name, size in site["served"] if name != "manifest.mpd")
    total = (site["root"] / "video.mp4").stat().st_size + (site["root"] / "audio.m4a").stat().st_size
    assert media < total * 0.4
    assert os.listdir(tmp_path) == [clip.path.name]  # không để lại file tạm
