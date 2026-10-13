"""Tải nhanh một đoạn ngắn (mặc định 15 giây đầu) của video YouTube.

App không tải cả video rồi mới cắt. Cách chính: đọc bảng mục lục (box ``sidx``) ở
đầu stream video và âm thanh của YouTube, rồi chỉ tải đúng các khúc chứa đoạn cần
lấy, bằng cùng kiểu request mà bộ tải của yt-dlp dùng (header Range, mỗi lần tối
đa 10MB). Việc ghép và cắt chính xác làm bằng ffmpeg ngay trên máy.

Nếu cách đó không dùng được, app chuyển sang để ffmpeg tự đọc đoạn cần từ link
stream (tính năng ``download_ranges`` của yt-dlp), cuối cùng thử client "android"
của YouTube.

Dùng từ dòng lệnh:

    python clipper.py "https://www.youtube.com/watch?v=..."            # 15s đầu
    python clipper.py URL --start 1:30 --duration 20 --quality 480
    python clipper.py URL1 URL2 URL3                                   # nhiều video
    python clipper.py --file danh-sach.txt                             # link trong file .txt
"""

from __future__ import annotations

import argparse
import contextlib
import math
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import RequestError
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
_OUTTMPL = "%(title).80B [%(id)s] %(section_start)ds-%(section_end)ds.%(ext)s"
_CHUNK_SIZE = 10 << 20  # YouTube chặn request lớn hơn ~10MB; yt-dlp cũng chia nhỏ như vậy
_HEADER_GUESS = 64 << 10  # ftyp + moov + sidx của YouTube thường chỉ vài KB


class ClipError(Exception):
    """Lỗi có thông điệp dễ hiểu, hiển thị thẳng cho người dùng."""

    def __init__(self, message: str, final: bool = False):
        super().__init__(message)
        self.final = final  # True: đổi cách tải cũng vô ích (vd. thời điểm bắt đầu vượt quá video)


class _Unsupported(Exception):
    """Cách tải này không dùng được cho video/định dạng này, chuyển sang cách khác."""


@dataclass
class Clip:
    path: Path
    title: str
    video_id: str
    start: float
    end: float


@dataclass
class _Section:
    start: float
    duration: float
    max_height: int
    precise: bool
    quiet: bool

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class _Segment:
    first_byte: int
    last_byte: int
    start: float
    end: float


def validate_youtube_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in YOUTUBE_HOSTS:
        raise ClipError("Link không hợp lệ: hãy dán link video YouTube (youtube.com hoặc youtu.be).")
    return url


def extract_links(text: str) -> list[str]:
    """Lọc các link http(s) trong một đoạn văn (mỗi dòng một link hoặc lẫn chữ), bỏ link trùng."""
    return list(dict.fromkeys(re.findall(r"https?://[^\s\"'<>,]+", text)))


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


def _ffmpeg_error(code: int) -> str:
    """Giải mã exit code của ffmpeg (AVERROR), vd. 3436169992 trên Windows là HTTP 403."""
    if code > 0x7FFFFFFF:
        code -= 1 << 32
    tag = (-code).to_bytes(4, "little") if code < 0 else b""
    if tag[:1] == b"\xf8" and tag[1:].isdigit():
        return f"HTTP Error {tag[1:].decode()}"
    return f"mã lỗi {code}"


def _clean_message(error: Exception) -> str:
    message = _ANSI_RE.sub("", str(error)).removeprefix("ERROR: ").split("; please report this issue")[0]
    return re.sub(r"ffmpeg exited with code (-?\d+)",
                  lambda m: f"ffmpeg: {_ffmpeg_error(int(m.group(1)))}", message)


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
        raise ClipError("Thời điểm bắt đầu không được âm.", final=True)
    if not 0 < duration <= MAX_DURATION:
        raise ClipError(f"Độ dài đoạn cắt phải lớn hơn 0 và tối đa {MAX_DURATION} giây.", final=True)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    section = _Section(start, duration, max_height, precise, quiet)

    attempts = (
        (_download_by_index, {}),
        (_download_with_ffmpeg, {}),
        # Client "android" (thường chỉ có 360p) vẫn tải được khi các client mặc định bị chặn.
        (_download_with_ffmpeg, {"youtube": {"player_client": ["android"]}}),
    )
    errors = []
    for method, extractor_args in attempts:
        try:
            return method(url, out_dir, section, extractor_args)
        except _Unsupported:
            continue
        except ClipError as e:
            if e.final:
                raise
            errors.append(str(e))

    if not errors:
        raise ClipError("Không tải được đoạn video này.")
    if len(errors) == 1:
        raise ClipError(errors[0])
    details = " | ".join(f"cách {i}: {error}" for i, error in enumerate(errors, 1))
    if any("403" in error for error in errors):
        raise ClipError("YouTube đang chặn tải video này (lỗi 403). Hãy thử lại sau vài phút hoặc chọn "
                        "chất lượng thấp hơn; nếu vẫn lỗi, tắt rồi mở lại app để cập nhật yt-dlp. "
                        f"Chi tiết: {details}")
    raise ClipError(f"Không tải được đoạn video này. Chi tiết: {details}")


@contextlib.contextmanager
def _youtube_dl(quiet: bool, **params):
    ffmpeg = _ffmpeg_location()
    # Bộ tải của yt-dlp chỉ đọc vị trí ffmpeg từ biến này (CLI của yt-dlp cũng đặt nó như vậy).
    token = FFmpegPostProcessor._ffmpeg_location.set(ffmpeg)
    try:
        with yt_dlp.YoutubeDL({
            "noplaylist": True,
            "playlist_items": "1",
            "overwrites": True,
            "js_runtimes": _js_runtimes(),
            "ffmpeg_location": ffmpeg,
            "quiet": quiet,
            "no_warnings": quiet,
            "noprogress": quiet,
            **params,
        }) as ydl:
            yield ydl
    except yt_dlp.utils.DownloadError as e:
        raise ClipError(f"Không tải được video: {_clean_message(e)}") from e
    finally:
        FFmpegPostProcessor._ffmpeg_location.reset(token)


def _single_video(info: dict | None) -> dict:
    if info and info.get("entries") is not None:  # link playlist: lấy video đầu tiên
        info = next((entry for entry in info["entries"] if entry), None)
    if not info:
        raise ClipError("Không tìm thấy video nào ở link này.", final=True)
    return info


def _check_start(info: dict, start: float) -> None:
    total = info.get("duration")
    if total and start >= total:
        raise ClipError(f"Video chỉ dài {total:g} giây, thời điểm bắt đầu {start:g}s đã vượt quá.", final=True)


# --- Cách 1: tự tải đúng các khúc cần dựa vào mục lục sidx ---------------------------------

def _download_by_index(url: str, out_dir: Path, section: _Section, extractor_args: dict) -> Clip:
    h = f"[height<={section.max_height}]"
    selector = (f"bv*{h}[vcodec^=avc1][container=mp4_dash]+ba[container=m4a_dash]"
                f"/bv*{h}[container=mp4_dash]+ba[container=m4a_dash]/b")
    with _youtube_dl(section.quiet, format=selector, extractor_args=extractor_args) as ydl:
        info = _single_video(ydl.extract_info(url, download=False))
        _check_start(info, section.start)
        formats = info.get("requested_formats") or []
        if len(formats) != 2 or any(f.get("protocol") not in ("https", "http") for f in formats):
            raise _Unsupported("không có stream DASH mp4 riêng cho hình và tiếng")

        end = min(section.end, info.get("duration") or section.end)
        out_path = Path(ydl.prepare_filename(
            {**info, "ext": "mp4", "section_start": section.start, "section_end": end},
            outtmpl=str(out_dir / _OUTTMPL)))
        parts = [out_path.with_name(f"{out_path.stem}.part{i}.{f['ext']}") for i, f in enumerate(formats)]
        try:
            starts = [_fetch_section(ydl, f, section.start, end, part) for f, part in zip(formats, parts)]
            _cut(list(zip(parts, starts)), out_path, section)
        except RequestError as e:
            raise ClipError(f"Không tải được video: {_clean_message(e)}") from e
        finally:
            for part in parts:
                part.unlink(missing_ok=True)

    return Clip(path=out_path, title=info.get("title") or info.get("id") or "video",
                video_id=info.get("id") or "", start=section.start, end=end)


def _iter_bytes(ydl, fmt: dict, first: int, last: int):
    """Đọc byte [first, last] giống bộ tải gốc của yt-dlp: header Range, mỗi request tối đa 10MB."""
    headers = {"Accept-Encoding": "identity", **(fmt.get("http_headers") or {})}
    pos = first
    while pos <= last:
        stop = min(last, pos + _CHUNK_SIZE - 1)
        with ydl.urlopen(Request(fmt["url"], headers={**headers, "Range": f"bytes={pos}-{stop}"})) as response:
            if response.status != 206 and pos != 0:
                raise _Unsupported("máy chủ không hỗ trợ tải từng phần")
            while pos <= stop:
                chunk = response.read(min(1 << 20, stop - pos + 1))
                if not chunk:
                    return  # file ngắn hơn dự kiến
                pos += len(chunk)
                yield chunk


def _fetch_section(ydl, fmt: dict, start: float, end: float, path: Path) -> float:
    """Tải phần khởi tạo (ftyp + moov) và các khúc phủ [start, end] của một stream fMP4 về
    ``path``. Trả về thời điểm (giây) bắt đầu khúc đầu tiên đã tải."""
    head = b"".join(_iter_bytes(ydl, fmt, 0, _HEADER_GUESS - 1))
    while isinstance(parsed := _parse_header(head), int):  # mục lục dài hơn phần đã đọc
        more = b"".join(_iter_bytes(ydl, fmt, len(head), parsed - 1))
        if not more:
            raise _Unsupported("file kết thúc trước khi đọc xong mục lục")
        head += more
    init, segments = parsed

    first = max((i for i, s in enumerate(segments) if s.start <= start), default=0)
    last = first
    while last + 1 < len(segments) and segments[last].end < end:
        last += 1

    with open(path, "wb") as f:
        f.write(init)
        for chunk in _iter_bytes(ydl, fmt, segments[first].first_byte, segments[last].last_byte):
            f.write(chunk)
    return segments[first].start


def _parse_header(data: bytes) -> int | tuple[bytes, list[_Segment]]:
    """Tách các box trước sidx (phần khởi tạo, thường là ftyp + moov) và mục lục sidx.
    Trả về số byte cần đọc nếu ``data`` chưa đủ dài."""
    pos = 0
    while True:
        if len(data) < pos + 16:
            return pos + _HEADER_GUESS
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            size, header = struct.unpack_from(">Q", data, pos + 8)[0], 16
        if size < header or kind in (b"moof", b"mdat"):
            raise _Unsupported("stream không có mục lục sidx")
        if len(data) < pos + size:
            return pos + size
        if kind == b"sidx":
            return data[:pos], _parse_sidx(data, pos, header, size)
        pos += size


def _parse_sidx(data: bytes, pos: int, header: int, size: int) -> list[_Segment]:
    """Đọc box sidx (ISO/IEC 14496-12): vị trí byte và thời gian của từng khúc."""
    p = pos + header
    version = data[p]
    timescale = struct.unpack_from(">I", data, p + 8)[0]
    if version == 0:
        time, offset = struct.unpack_from(">II", data, p + 12)
        p += 20
    else:
        time, offset = struct.unpack_from(">QQ", data, p + 12)
        p += 28
    count = struct.unpack_from(">H", data, p + 2)[0]
    p += 4
    if not timescale or not count:
        raise _Unsupported("mục lục sidx trống")

    byte = pos + size + offset
    segments = []
    for _ in range(count):
        reference, duration, _sap = struct.unpack_from(">III", data, p)
        p += 12
        if reference >> 31:
            raise _Unsupported("mục lục sidx lồng nhau")
        length = reference & 0x7FFFFFFF
        segments.append(_Segment(byte, byte + length - 1, time / timescale, (time + duration) / timescale))
        byte += length
        time += duration
    return segments


def _cut(parts: list[tuple[Path, float]], out_path: Path, section: _Section) -> None:
    """Ghép các stream đã tải rồi cắt đúng [start, start + duration] bằng ffmpeg trên máy."""
    cmd = [_ffmpeg_location() or "ffmpeg", "-y", "-v", "error"]
    for part, part_start in parts:
        if section.start > part_start:
            cmd += ["-ss", f"{section.start - part_start:.3f}"]
        cmd += ["-i", str(part)]
    for i in range(len(parts)):
        cmd += ["-map", str(i)]
    cmd += ["-t", f"{section.duration:.3f}"]
    if section.precise:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-c", "copy"]
    cmd += ["-movflags", "+faststart", str(out_path)]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
        detail = (result.stderr.strip().splitlines() or [_ffmpeg_error(result.returncode)])[-1]
        raise ClipError(f"ffmpeg không cắt được video: {detail}")


# --- Cách 2: để ffmpeg tự đọc đoạn cần từ link stream -------------------------------------

def _download_with_ffmpeg(url: str, out_dir: Path, section: _Section, extractor_args: dict) -> Clip:
    def ranges(info, ydl):
        total = info.get("duration")
        if total and section.start >= total:
            return  # không có gì để tải, báo lỗi bên dưới
        yield {"start_time": section.start, "end_time": section.end}

    h = f"[height<={section.max_height}]"
    with _youtube_dl(
        section.quiet,
        # Ưu tiên HLS: ffmpeg tải từng segment nhỏ thay vì một link https lớn.
        format=f"b{h}[protocol^=m3u8]/{format_selector(section.max_height)}",
        merge_output_format="mp4",
        download_ranges=ranges,
        force_keyframes_at_cuts=section.precise,
        outtmpl=str(out_dir / _OUTTMPL),
        extractor_args=extractor_args,
    ) as ydl:
        info = _single_video(ydl.extract_info(url, download=True))

    downloads = info.get("requested_downloads") or []
    if not downloads:
        _check_start(info, section.start)
        raise ClipError("Không tải được đoạn video này.")

    done = downloads[-1]
    path = Path(done["filepath"])
    if not path.is_file() or path.stat().st_size == 0:
        raise ClipError("ffmpeg không tạo ra được file, hãy thử lại hoặc chọn chất lượng khác.")
    return Clip(
        path=path,
        title=info.get("title") or info.get("id") or "video",
        video_id=info.get("id") or "",
        start=done.get("section_start") or section.start,
        end=done.get("section_end") or section.end,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tải nhanh một đoạn ngắn (mặc định 15 giây đầu) của video YouTube.")
    parser.add_argument("urls", nargs="*", metavar="url", help="một hoặc nhiều link video YouTube")
    parser.add_argument("-f", "--file", help="file .txt chứa danh sách link (mỗi dòng một link)")
    parser.add_argument("-s", "--start", default="0", help="bắt đầu từ (giây hoặc mm:ss), mặc định 0")
    parser.add_argument("-d", "--duration", default=str(DEFAULT_DURATION),
                        help=f"độ dài đoạn cắt (giây), mặc định {DEFAULT_DURATION}")
    parser.add_argument("-q", "--quality", type=int, choices=QUALITIES, default=DEFAULT_QUALITY,
                        help=f"độ phân giải tối đa, mặc định {DEFAULT_QUALITY}p")
    parser.add_argument("-o", "--output", default="downloads", help="thư mục lưu, mặc định ./downloads")
    parser.add_argument("--precise", action="store_true", help="cắt chính xác từng khung hình (encode lại, chậm hơn)")
    args = parser.parse_args(argv)

    urls = list(args.urls)
    if args.file:
        try:
            urls += extract_links(Path(args.file).read_text(encoding="utf-8-sig"))
        except OSError as e:
            print(f"Lỗi: không đọc được file {args.file}: {e}", file=sys.stderr)
            return 1
    urls = list(dict.fromkeys(urls))
    if not urls:
        parser.error("cần ít nhất một link, hoặc --file chứa danh sách link")
    try:
        options = {
            "start": parse_seconds(args.start),
            "duration": parse_seconds(args.duration, DEFAULT_DURATION),
            "max_height": args.quality,
            "precise": args.precise,
        }
    except ClipError as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        return 1

    failures = 0
    for i, url in enumerate(urls, 1):
        prefix = f"[{i}/{len(urls)}] " if len(urls) > 1 else ""
        try:
            clip = download_clip(validate_youtube_url(url), args.output, quiet=False, **options)
        except ClipError as e:
            failures += 1
            print(f"{prefix}Lỗi ({url}): {e}", file=sys.stderr)
        else:
            print(f"{prefix}Đã lưu: {clip.path}")
    if len(urls) > 1:
        print(f"Xong {len(urls) - failures}/{len(urls)} video.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
