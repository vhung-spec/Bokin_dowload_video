"""Tải nhanh một đoạn ngắn (mặc định 15 giây đầu) của video YouTube.

Thay vì tải cả video rồi mới cắt, yt-dlp chỉ lấy link stream trực tiếp rồi giao
cho ffmpeg đọc đúng khoảng thời gian cần (``-ss``/``-t`` ở phía input). Nhờ vậy
chỉ phần dữ liệu của đoạn đó được tải về, video dài 2 tiếng hay 2 phút cũng
nhanh như nhau.

Dùng từ dòng lệnh:

    python clipper.py "https://www.youtube.com/watch?v=..."            # 15s đầu
    python clipper.py URL --start 1:30 --duration 20 --quality 480
"""

from __future__ import annotations

import argparse
import math
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor

DEFAULT_DURATION = 15
MAX_DURATION = 300
QUALITIES = (360, 480, 720, 1080)
DEFAULT_QUALITY = 720

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class ClipError(Exception):
    """Lỗi có thông điệp dễ hiểu, hiển thị thẳng cho người dùng."""


@dataclass
class Clip:
    path: Path
    title: str
    video_id: str
    start: float
    end: float


def validate_youtube_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in YOUTUBE_HOSTS:
        raise ClipError("Link không hợp lệ: hãy dán link video YouTube (youtube.com hoặc youtu.be).")
    return url


def parse_seconds(value: str | float | int | None, default: float = 0) -> float:
    """Đổi "90", "1:30" hoặc "1:02:03" thành số giây."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return float(default)
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        try:
            seconds = 0.0
            for part in value.strip().split(":"):
                seconds = seconds * 60 + float(part)
        except ValueError:
            raise ClipError(f"Thời gian không hợp lệ: {value!r} (ví dụ hợp lệ: 90, 1:30, 1:02:03).") from None
    if not math.isfinite(seconds):
        raise ClipError(f"Thời gian không hợp lệ: {value!r}.")
    return seconds


def _ffmpeg_location() -> str | None:
    """ffmpeg của máy nếu có, không thì dùng bản đi kèm gói imageio-ffmpeg."""
    if shutil.which("ffmpeg"):
        return None
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


def _js_runtimes() -> dict:
    """YouTube cần một JavaScript runtime để giải mã link stream; dùng Deno/Node của máy
    hoặc Deno đi kèm gói pip ``deno``."""
    runtimes = {"deno": {}, "node": {}}
    if not shutil.which("deno"):
        try:
            import deno

            runtimes["deno"] = {"path": deno.find_deno_bin()}
        except (ImportError, OSError, RuntimeError):
            pass
    return runtimes


def format_selector(max_height: int) -> str:
    """Chọn stream nhỏ nhất đủ dùng: ưu tiên H.264 + AAC để file phát được ở mọi nơi."""
    h = f"[height<={max_height}]"
    return f"bv*{h}[vcodec^=avc1]+ba[ext=m4a]/bv*{h}+ba/b{h}/bv*+ba/b"


def download_clip(
    url: str,
    out_dir: str | Path,
    start: float = 0,
    duration: float = DEFAULT_DURATION,
    max_height: int = DEFAULT_QUALITY,
    precise: bool = False,
    quiet: bool = True,
) -> Clip:
    """Tải đoạn [start, start + duration) của video về ``out_dir``.

    Hàm không giới hạn trang nguồn; nơi nhận link từ người dùng (web, CLI) cần
    gọi ``validate_youtube_url`` trước.

    ``precise=True`` sẽ encode lại để cắt đúng từng khung hình (chậm hơn). Mặc
    định chỉ copy stream nên rất nhanh; khi ``start`` > 0 điểm đầu có thể lệch
    về keyframe gần nhất.
    """
    if start < 0:
        raise ClipError("Thời điểm bắt đầu không được âm.")
    if not 0 < duration <= MAX_DURATION:
        raise ClipError(f"Độ dài đoạn cắt phải lớn hơn 0 và tối đa {MAX_DURATION} giây.")

    def ranges(info, ydl):
        total = info.get("duration")
        if total and start >= total:
            return  # không có gì để tải, báo lỗi bên dưới
        yield {"start_time": start, "end_time": start + duration}

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg_location()
    opts = {
        "format": format_selector(max_height),
        "merge_output_format": "mp4",
        "download_ranges": ranges,
        "force_keyframes_at_cuts": precise,
        "outtmpl": str(out_dir / "%(title).80B [%(id)s] %(section_start)ds-%(section_end)ds.%(ext)s"),
        "noplaylist": True,
        "playlist_items": "1",
        "overwrites": True,
        "js_runtimes": _js_runtimes(),
        "ffmpeg_location": ffmpeg,
        "quiet": quiet,
        "no_warnings": quiet,
        "noprogress": quiet,
    }

    # Bộ tải của yt-dlp chỉ đọc vị trí ffmpeg từ biến này (CLI của yt-dlp cũng đặt nó như vậy).
    ffmpeg_token = FFmpegPostProcessor._ffmpeg_location.set(ffmpeg)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        message = _ANSI_RE.sub("", str(e)).removeprefix("ERROR: ").split("; please report this issue")[0]
        raise ClipError(f"Không tải được video: {message}") from e
    finally:
        FFmpegPostProcessor._ffmpeg_location.reset(ffmpeg_token)

    if info and info.get("entries") is not None:  # link playlist: lấy video đầu tiên
        info = next((entry for entry in info["entries"] if entry), None)
    if not info:
        raise ClipError("Không tìm thấy video nào ở link này.")

    downloads = info.get("requested_downloads") or []
    if not downloads:
        total = info.get("duration")
        if total and start >= total:
            raise ClipError(f"Video chỉ dài {total:g} giây, thời điểm bắt đầu {start:g}s đã vượt quá.")
        raise ClipError("Không tải được đoạn video này.")

    done = downloads[-1]
    path = Path(done["filepath"])
    if not path.is_file() or path.stat().st_size == 0:
        raise ClipError("ffmpeg không tạo ra được file, hãy thử lại hoặc chọn chất lượng khác.")
    return Clip(
        path=path,
        title=info.get("title") or info.get("id") or "video",
        video_id=info.get("id") or "",
        start=done.get("section_start") or start,
        end=done.get("section_end") or start + duration,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tải nhanh một đoạn ngắn (mặc định 15 giây đầu) của video YouTube.")
    parser.add_argument("url", help="link video YouTube")
    parser.add_argument("-s", "--start", default="0", help="bắt đầu từ (giây hoặc mm:ss), mặc định 0")
    parser.add_argument("-d", "--duration", default=str(DEFAULT_DURATION),
                        help=f"độ dài đoạn cắt (giây), mặc định {DEFAULT_DURATION}")
    parser.add_argument("-q", "--quality", type=int, choices=QUALITIES, default=DEFAULT_QUALITY,
                        help=f"độ phân giải tối đa, mặc định {DEFAULT_QUALITY}p")
    parser.add_argument("-o", "--output", default="downloads", help="thư mục lưu, mặc định ./downloads")
    parser.add_argument("--precise", action="store_true", help="cắt chính xác từng khung hình (encode lại, chậm hơn)")
    args = parser.parse_args(argv)

    try:
        url = validate_youtube_url(args.url)
        clip = download_clip(
            url,
            args.output,
            start=parse_seconds(args.start),
            duration=parse_seconds(args.duration, DEFAULT_DURATION),
            max_height=args.quality,
            precise=args.precise,
            quiet=False,
        )
    except ClipError as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        return 1
    print(f"Đã lưu: {clip.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
