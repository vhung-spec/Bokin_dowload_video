import functools
import http.server
import json
import shutil
import subprocess
import threading

import pytest

from clipper import ClipError, download_clip, format_selector, parse_seconds, validate_youtube_url

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


def test_format_selector_limits_height():
    assert "[height<=480]" in format_selector(480)


@pytest.mark.parametrize("start, duration", [(-1, 15), (0, 0), (0, -5), (0, 10_000)])
def test_download_rejects_bad_range(tmp_path, start, duration):
    with pytest.raises(ClipError):
        download_clip("https://youtu.be/x", tmp_path, start=start, duration=duration)


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


@pytest.fixture(scope="module")
def sample_url(tmp_path_factory):
    """Phục vụ một video thử 40 giây qua HTTP cục bộ, thay cho YouTube khi test."""
    root = tmp_path_factory.mktemp("site")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=40",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=40",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-c:a", "aac", "-shortest",
         "-movflags", "+faststart", str(root / "sample.mp4")],
        check=True,
    )

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/sample.mp4"
    server.shutdown()


@needs_ffmpeg
def test_downloads_first_15_seconds(sample_url, tmp_path):
    clip = download_clip(sample_url, tmp_path)
    assert clip.path.suffix == ".mp4"
    assert (clip.start, clip.end) == (0, 15)
    assert probe_duration(clip.path) == pytest.approx(15, abs=0.5)


@needs_ffmpeg
def test_downloads_middle_section(sample_url, tmp_path):
    clip = download_clip(sample_url, tmp_path, start=10, duration=5, precise=True)
    assert probe_duration(clip.path) == pytest.approx(5, abs=0.5)
