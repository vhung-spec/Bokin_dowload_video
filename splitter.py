"""Tách một file nhạc dài (vd. video tổng hợp nhiều bài) thành từng bài.

Nhận diện điểm chuyển bài theo thứ tự ưu tiên:
1. chương (chapters) có sẵn trong file: Bokin Clip ghi chương YouTube vào MP3 khi tải, nên
   video tổng hợp có chia chương sẽ được tách đúng và có sẵn tên bài;
2. khoảng lặng giữa các bài: đo độ to (RMS) từng nửa giây bằng ffmpeg, tìm những đoạn nhỏ hơn
   hẳn mức to thường gặp của file và kéo dài đủ lâu, rồi chọn các đoạn "im" nhất sao cho bài nào
   cũng đủ dài (để không cắt nhầm ở đoạn nhạc nhỏ ngay giữa một bài).

Nhạc mix nối liền không có khoảng lặng thì không tự tìm được điểm chuyển bài; khi đó người dùng
thêm điểm cắt bằng tay trên giao diện.

Cắt bằng ffmpeg: file MP3 thì copy nguyên (không mã hoá lại), định dạng khác thì đổi sang MP3.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from clipper import _NO_WINDOW, AUDIO_BITRATE, ClipError, _ffmpeg_location

STEP = 0.5  # giây mỗi lần đo độ to
SILENCE_FLOOR = -90.0  # dB; im lặng tuyệt đối (-inf) được tính như mức này
# Độ nhạy: (khoảng lặng phải nhỏ hơn mức to thường gặp bao nhiêu dB, và dài ít nhất bao nhiêu giây)
SENSITIVITY = {
    "low": (30.0, 2.0),
    "normal": (25.0, 1.0),
    "high": (18.0, 0.5),
}
DEFAULT_MIN_TRACK = 60.0
_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class Track:
    start: float
    end: float
    title: str


@dataclass
class Analysis:
    duration: float
    method: str  # "chapters" | "silence" | "none" (không tìm thấy điểm chuyển bài nào)
    tracks: list[Track]
    title: str = ""
    artist: str = ""


def safe_filename(name: str, limit: int = 100) -> str:
    """Tên file dùng được trên Windows (bỏ ký tự cấm, dấu chấm/cách ở cuối)."""
    name = _BAD_FILENAME_CHARS.sub("", name).strip()
    return name[:limit].rstrip(". ")


def analyze(path: str | Path, min_track: float = DEFAULT_MIN_TRACK, sensitivity: str = "normal") -> Analysis:
    path = Path(path)
    title, artist, duration, chapters = read_tags(path)
    if len(chapters) > 1:
        duration = duration or chapters[-1].end
        # Mỗi bài kéo dài đến đầu bài sau, bài cuối đến hết file (kể cả khi chương không phủ hết).
        for chapter, following in zip(chapters, chapters[1:]):
            chapter.end = following.start
        chapters[-1].end = max(chapters[-1].end, duration)
        return Analysis(duration, "chapters", chapters, title, artist)

    levels = measure_levels(path)
    if not levels:
        raise ClipError("Không đọc được âm thanh trong file này.")
    duration = duration or len(levels) * STEP
    starts = find_starts(levels, STEP, duration, min_track, sensitivity)
    tracks = [Track(start, end, f"Bài {i:02d}")
              for i, (start, end) in enumerate(zip(starts, starts[1:] + [duration]), 1)]
    return Analysis(duration, "silence" if len(tracks) > 1 else "none", tracks, title, artist)


def read_tags(path: Path) -> tuple[str, str, float, list[Track]]:
    """Tên bài, nghệ sĩ, độ dài và các chương có sẵn trong file (MP3: ID3 CHAP; M4A/MP4)."""
    import mutagen
    from mutagen.id3 import ID3

    try:
        media = mutagen.File(path)
    except Exception:  # file hỏng hoặc định dạng mutagen không đọc được: để ffmpeg xử lý
        media = None
    if media is None:
        return "", "", 0.0, []
    duration = float(getattr(media.info, "length", 0) or 0)
    tags = media.tags
    chapters: list[Track] = []
    if isinstance(tags, ID3):
        title, artist = str(tags.get("TIT2", "")), str(tags.get("TPE1", ""))
        for i, chap in enumerate(sorted(tags.getall("CHAP"), key=lambda c: c.start_time), 1):
            name = chap.sub_frames.get("TIT2")
            chapters.append(Track(chap.start_time / 1000, chap.end_time / 1000,
                                  str(name).strip() if name else f"Bài {i:02d}"))
    else:
        def first(*keys):
            for key in keys:
                values = (tags or {}).get(key)
                if values:
                    return str(values[0])
            return ""

        title, artist = first("\xa9nam", "title", "TITLE"), first("\xa9ART", "artist", "ARTIST")
        mp4_chapters = list(getattr(media, "chapters", None) or [])
        for i, chap in enumerate(mp4_chapters, 1):
            end = mp4_chapters[i].start if i < len(mp4_chapters) else duration
            chapters.append(Track(chap.start, end, chap.title or f"Bài {i:02d}"))
    return title, artist, duration, chapters


def measure_levels(path: Path, step: float = STEP) -> list[float]:
    """Độ to (RMS, dB) của từng đoạn ``step`` giây, đo bằng ffmpeg trên bản mono 8 kHz cho nhanh."""
    samples = int(8000 * step)
    audio_filter = (f"aformat=channel_layouts=mono,aresample=8000,asetnsamples=n={samples}:p=0,"
                    "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-")
    cmd = [_ffmpeg_location() or "ffmpeg", "-hide_banner", "-nostats", "-v", "error",
           "-i", str(path), "-vn", "-af", audio_filter, "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **_NO_WINDOW)
    if result.returncode != 0:
        detail = (result.stderr.strip().splitlines() or ["?"])[-1]
        raise ClipError(f"ffmpeg không đọc được file: {detail}")
    levels = []
    for value in re.findall(r"RMS_level=(\S+)", result.stdout):
        try:
            level = float(value)
        except ValueError:
            level = SILENCE_FLOOR
        levels.append(level if level == level else SILENCE_FLOOR)  # NaN -> im lặng
    return levels


def find_starts(levels: list[float], step: float, duration: float,
                min_track: float = DEFAULT_MIN_TRACK, sensitivity: str = "normal") -> list[float]:
    """Thời điểm bắt đầu của từng bài (bài đầu luôn bắt đầu ở 0)."""
    below, min_gap = SENSITIVITY.get(sensitivity, SENSITIVITY["normal"])
    levels = [max(level, SILENCE_FLOOR) for level in levels]
    audible = sorted(level for level in levels if level > SILENCE_FLOOR + 10)
    if not audible:
        return [0.0]
    typical = audible[len(audible) // 2]  # mức to thường gặp (trung vị)
    threshold = min(max(typical - below, -70.0), -30.0)
    min_windows = max(1, round(min_gap / step))

    gaps = []  # (điểm, lúc nhạc vang lên lại, lúc bắt đầu im)
    run_start = None
    for i, level in enumerate(levels + [0.0]):  # thêm một mức "to" ở cuối để khép đoạn im cuối cùng
        if level < threshold:
            if run_start is None:
                run_start = i
            continue
        if run_start is not None and i - run_start >= min_windows:
            quiet = levels[run_start:i]
            depth = threshold - sum(quiet) / len(quiet)  # im càng sâu càng chắc là khoảng nghỉ giữa bài
            gaps.append((depth * len(quiet), i * step, run_start * step))
        run_start = None

    starts: list[float] = []
    for _, sound_back, silence_from in sorted(gaps, reverse=True):
        start = max(silence_from, sound_back - 0.2)  # ngay trước khi nhạc vang lên lại
        if start < min_track or duration - start < min_track:
            continue  # im lặng ở đầu/cuối file, hoặc bài đầu/cuối sẽ quá ngắn
        if all(abs(start - other) >= min_track for other in starts):
            starts.append(start)
    return [0.0] + sorted(starts)


def cut_tracks(source: Path, tracks: list[Track], out_dir: Path, album: str = "", artist: str = "") -> list[Path]:
    """Cắt từng bài ra file MP3 riêng ("01 - Tên bài.mp3"), ghi tên bài, số thứ tự, album, nghệ sĩ."""
    out_dir.mkdir(parents=True, exist_ok=True)
    copy = source.suffix.lower() == ".mp3"
    files = []
    for i, track in enumerate(tracks, 1):
        out = out_dir / f"{i:02d} - {safe_filename(track.title) or f'Bài {i:02d}'}.mp3"
        cmd = [_ffmpeg_location() or "ffmpeg", "-y", "-v", "error",
               "-ss", f"{track.start:.3f}", "-i", str(source), "-t", f"{track.end - track.start:.3f}",
               "-map", "0:a:0", "-map_metadata", "-1", "-map_chapters", "-1"]
        cmd += ["-c:a", "copy"] if copy else ["-c:a", "libmp3lame", "-b:a", AUDIO_BITRATE]
        cmd += ["-id3v2_version", "3", "-metadata", f"title={track.title}", "-metadata", f"track={i}/{len(tracks)}"]
        if album:
            cmd += ["-metadata", f"album={album}"]
        if artist:
            cmd += ["-metadata", f"artist={artist}"]
        cmd.append(str(out))
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                **_NO_WINDOW)
        if result.returncode != 0 or not out.is_file():
            detail = (result.stderr.strip().splitlines() or ["?"])[-1]
            raise ClipError(f"ffmpeg không cắt được bài {i} ({track.title}): {detail}")
        files.append(out)
    return files
