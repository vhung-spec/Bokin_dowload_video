"""Tải nhạc từ link Spotify (bài hát, album, playlist).

Nhạc trên Spotify được mã hoá (DRM); app KHÔNG phá khoá và không tải gì từ máy chủ nhạc của
Spotify. Cách làm (giống công cụ mã nguồn mở spotDL):
1. đọc thông tin công khai của bài hát (tên, nghệ sĩ, thời lượng, album, ảnh bìa) từ trang nhúng
   (embed) của Spotify, không cần tài khoản;
2. tìm đúng bài đó trên YouTube: ưu tiên kết quả có thời lượng khớp, kênh "… - Topic" (bản thu
   âm gốc của YouTube Music), tên trùng; tránh bản live, cover, remix, sped up…;
3. tải nhạc MP3 bằng phần tải nhạc sẵn có, rồi ghi tên bài, nghệ sĩ, album, ảnh bìa theo Spotify.

Dùng từ dòng lệnh:

    python spotify.py "https://open.spotify.com/album/..."             # tải cả album
    python spotify.py --list "https://open.spotify.com/playlist/..."   # chỉ xem danh sách bài
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from clipper import ClipError, _youtube_dl, download_clip
from splitter import safe_filename

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
_LINK_RE = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-zA-Z-]+/)?(?:embed/)?|spotify:)"
                      r"(track|album|playlist)[/:]([A-Za-z0-9]{22})")
_SHORT_HOSTS = ("spotify.link", "spotify.app.link")
_COVER_HOSTS = (".scdn.co", ".spotifycdn.com")
EMBED_LIMIT = 100  # trang nhúng của Spotify chỉ liệt kê tối đa chừng này bài của một playlist
SEARCH_RESULTS = 8
MAX_DURATION_GAP = 30  # giây; lệch nhiều hơn thì chắc chắn không phải bản thu này
# Từ trong tên video cho biết đó là phiên bản khác của bài (trừ khi chính tên bài trên Spotify có từ đó).
_OTHER_VERSIONS = ("live", "cover", "karaoke", "remix", "sped up", "speed up", "slowed", "reverb", "nightcore",
                   "8d", "instrumental", "beat", "acoustic", "piano", "lofi", "lo fi", "tiktok", "1 hour",
                   "hour", "loop", "mashup", "dance", "reaction", "tutorial")


@dataclass
class SpotifyTrack:
    title: str
    artists: str
    duration: float  # giây
    album: str = ""
    cover: str = ""
    number: int = 0
    total: int = 0

    @property
    def query(self) -> str:
        return f"{self.artists} - {self.title}" if self.artists else self.title


@dataclass
class SpotifyList:
    kind: str  # "track" | "album" | "playlist"
    name: str
    tracks: list[SpotifyTrack]

    @property
    def folder(self) -> str:
        """Thư mục con để lưu: album/playlist mỗi cái một thư mục, bài lẻ thì lưu thẳng vào thư mục lưu."""
        return "" if self.kind == "track" else safe_filename(self.name) or "Spotify"


def is_spotify_link(text: str) -> bool:
    text = (text or "").strip()
    host = (urlparse(text).hostname or "").lower()
    return text.startswith("spotify:") or host == "open.spotify.com" or host in _SHORT_HOSTS


def _http_get(url: str, binary: bool = False, timeout: float = 20):
    """GET qua HTTPS; trả về (url cuối cùng sau chuyển hướng, nội dung)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        return response.geturl(), data if binary else data.decode("utf-8", "replace")


def resolve(url: str) -> tuple[str, str]:
    """(loại, mã Spotify) của link (dán cả đoạn chia sẻ cũng được); link rút gọn thì mở ra lấy link thật."""
    url = (url or "").strip()
    match = _LINK_RE.search(url)
    short = re.search(r"https?://[^\s\"'<>]+", url)
    if not match and short and (urlparse(short.group()).hostname or "").lower() in _SHORT_HOSTS:
        final_url, body = _http_get(short.group())
        match = _LINK_RE.search(final_url) or _LINK_RE.search(body)
    if match:
        return match.group(1), match.group(2)
    if re.search(r"open\.spotify\.com/(?:intl-[a-zA-Z-]+/)?(?:artist|show|episode|user)/", url):
        raise ClipError("Chỉ hỗ trợ link bài hát, album hoặc playlist của Spotify (chưa hỗ trợ nghệ sĩ, podcast).")
    raise ClipError("Link Spotify không hợp lệ: hãy dán link bài hát, album hoặc playlist "
                    "(https://open.spotify.com/...).")


def embed_entity(html: str) -> dict:
    """Dữ liệu bài hát/album/playlist nằm trong trang nhúng của Spotify (một trong ba dạng)."""
    for script_id, base64_encoded, path in (
        ("__NEXT_DATA__", False, ("props", "pageProps", "state", "data", "entity")),
        ("resource", True, ()),
        ("initial-state", True, ("data", "entity")),
    ):
        match = re.search(rf'<script[^>]*\bid="{script_id}"[^>]*>(.*?)</script>', html, re.S)
        if not match:
            continue
        try:
            data = json.loads(base64.b64decode(match.group(1)) if base64_encoded else match.group(1))
            for key in path:
                data = data[key]
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(data, dict) and (data.get("name") or data.get("title")):
            return data
    raise ClipError("Không đọc được thông tin bài hát từ Spotify (có thể Spotify vừa đổi trang). "
                    "Hãy cập nhật Bokin Clip lên bản mới, hoặc báo lỗi.")


def _text(value) -> str:
    return " ".join(str(value or "").split())  # gộp khoảng trắng (Spotify hay dùng dấu cách không ngắt)


def _artists(item: dict) -> str:
    names = [_text(artist.get("name")) for artist in item.get("artists") or []
             if isinstance(artist, dict) and artist.get("name")]
    return ", ".join(names) or _text(item.get("subtitle"))


def _duration(item: dict) -> float:
    value = item.get("duration") or 0
    if isinstance(value, dict):
        value = value.get("totalMilliseconds") or 0
    return float(value) / 1000


def _cover(entity: dict) -> str:
    images = ((entity.get("coverArt") or {}).get("sources") or entity.get("images")
              or (entity.get("visualIdentity") or {}).get("image") or [])
    best = max((image for image in images if isinstance(image, dict) and image.get("url")),
               key=lambda image: image.get("width") or image.get("maxWidth") or 0, default=None)
    return best["url"] if best else ""


def fetch_list(url: str) -> SpotifyList:
    try:
        kind, spotify_id = resolve(url)
        _, html = _http_get(f"https://open.spotify.com/embed/{kind}/{spotify_id}")
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            raise ClipError("Spotify không tìm thấy bài/album/playlist này (link sai, đã bị xoá, "
                            "hoặc playlist đang để riêng tư).") from e
        raise ClipError(f"Spotify báo lỗi {e.code}, hãy thử lại sau.") from e
    except OSError as e:
        raise ClipError(f"Không kết nối được tới Spotify, hãy kiểm tra mạng: {getattr(e, 'reason', e)}") from e
    entity = embed_entity(html)
    name = _text(entity.get("name") or entity.get("title"))
    cover = _cover(entity)
    items = [entity] if kind == "track" else [item for item in entity.get("trackList") or [] if isinstance(item, dict)]
    tracks = []
    for number, item in enumerate(items, 1):
        title = _text(item.get("title") or item.get("name"))
        if not title:
            continue
        tracks.append(SpotifyTrack(
            title=title,
            artists=_artists(item),
            duration=_duration(item),
            album=name if kind == "album" else "",
            cover=cover if kind != "playlist" else "",  # playlist: ảnh bìa là của playlist, không phải của bài
            number=number if kind == "album" else 0,
            total=len(items) if kind == "album" else 0,
        ))
    if not tracks:
        raise ClipError("Không thấy bài hát nào trong link Spotify này.")
    return SpotifyList(kind, name, tracks)


def track_from_dict(data) -> SpotifyTrack:
    """Bài hát do trang web gửi lên (lấy từ fetch_list trước đó)."""
    if not isinstance(data, dict) or not _text(data.get("title")):
        raise ClipError("Thông tin bài hát không hợp lệ.")
    try:
        return SpotifyTrack(
            title=_text(data["title"]),
            artists=_text(data.get("artists")),
            duration=max(0.0, float(data.get("duration") or 0)),
            album=_text(data.get("album")),
            cover=str(data.get("cover") or ""),
            number=int(data.get("number") or 0),
            total=int(data.get("total") or 0),
        )
    except (TypeError, ValueError):
        raise ClipError("Thông tin bài hát không hợp lệ.") from None


def _words(text: str) -> set[str]:
    """Các từ (bỏ dấu tiếng Việt, chữ thường) để so tên bài với tên video."""
    text = unicodedata.normalize("NFKD", (text or "").lower().replace("đ", "d"))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return set(re.findall(r"[a-z0-9]+", text))


def _score(track: SpotifyTrack, entry: dict, rank: int) -> float:
    title = entry.get("title") or ""
    channel = entry.get("channel") or entry.get("uploader") or ""
    score = 10.0 - rank  # YouTube xếp trên thì nhỉnh hơn một chút
    duration = entry.get("duration")
    if track.duration and duration:
        gap = abs(float(duration) - track.duration)
        score += -1000 if gap > MAX_DURATION_GAP else -gap

    wanted, found = _words(track.title), _words(title)
    if wanted:
        score += 40 * len(wanted & found) / len(wanted)
    main_artist = _words(track.artists.split(",")[0])
    if main_artist and main_artist <= (found | _words(channel)):
        score += 15
    if channel.endswith(" - Topic"):
        score += 25  # kênh tự động của YouTube Music: đúng bản thu âm gốc
    for version in _OTHER_VERSIONS:
        version_words = set(version.split())
        if version_words <= found and not version_words <= wanted:
            score -= 40
    return score


def best_match(track: SpotifyTrack, entries: list[dict]) -> dict:
    candidates = [(_score(track, entry, rank), entry) for rank, entry in enumerate(entries) if entry.get("id")]
    if not candidates:
        raise ClipError("Không tìm thấy bài này trên YouTube.")
    score, entry = max(candidates, key=lambda candidate: candidate[0])
    if score < -500:
        raise ClipError("Không tìm thấy bản nào trên YouTube có thời lượng khớp với bài trên Spotify.")
    return {
        "url": f"https://www.youtube.com/watch?v={entry['id']}",
        "title": entry.get("title") or "",
        "channel": entry.get("channel") or entry.get("uploader") or "",
    }


def find_on_youtube(track: SpotifyTrack, quiet: bool = True) -> dict:
    with _youtube_dl(quiet, extract_flat="in_playlist", noplaylist=False,
                     playlist_items=f"1-{SEARCH_RESULTS}") as ydl:
        info = ydl.extract_info(f"ytsearch{SEARCH_RESULTS}:{track.query}", download=False)
    return best_match(track, list((info or {}).get("entries") or []))


def _cover_bytes(url: str) -> bytes | None:
    host = (urlparse(url).hostname or "").lower()
    if urlparse(url).scheme != "https" or not host.endswith(_COVER_HOSTS):
        return None  # chỉ lấy ảnh bìa từ máy chủ ảnh của Spotify
    try:
        return _http_get(url, binary=True)[1]
    except OSError:
        return None  # thiếu ảnh bìa thì vẫn giữ bài hát


def tag(path: Path, track: SpotifyTrack) -> None:
    """Ghi lại tên bài, nghệ sĩ, album, số thứ tự, ảnh bìa theo Spotify (thay cho thông tin của video)."""
    from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TRCK, ID3NoHeaderError

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    for frame in ("TIT2", "TPE1", "TALB", "TRCK", "APIC", "CHAP", "CTOC"):
        tags.delall(frame)
    tags.add(TIT2(encoding=3, text=track.title))
    if track.artists:
        tags.add(TPE1(encoding=3, text=track.artists))
    if track.album:
        tags.add(TALB(encoding=3, text=track.album))
    if track.number:
        tags.add(TRCK(encoding=3, text=f"{track.number}/{track.total}" if track.total else str(track.number)))
    cover = _cover_bytes(track.cover) if track.cover else None
    if cover:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
    tags.save(path, v2_version=3)


def download_track(track: SpotifyTrack, out_dir: Path, quiet: bool = True) -> tuple[Path, dict]:
    """Tìm bài trên YouTube, tải MP3 cả bài, ghi thông tin Spotify; trả về (file, video YouTube đã dùng)."""
    source = find_on_youtube(track, quiet)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bokin-spotify-") as tmp:
        clip = download_clip(source["url"], tmp, duration=None, audio=True, quiet=quiet)
        tag(clip.path, track)
        target = out_dir / f"{safe_filename(track.query) or clip.path.stem}.mp3"
        target.unlink(missing_ok=True)
        shutil.move(clip.path, target)
    return target, source


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Tải nhạc MP3 cho các bài trong link Spotify (nguồn nhạc: YouTube).")
    parser.add_argument("urls", nargs="+", help="link bài hát, album hoặc playlist Spotify")
    parser.add_argument("-o", "--output", default="downloads", help="thư mục lưu (mặc định: downloads)")
    parser.add_argument("--list", action="store_true", help="chỉ in danh sách bài, không tải")
    parser.add_argument("--find", action="store_true", help="in bài YouTube tìm được cho từng bài, không tải")
    args = parser.parse_args(argv)

    failed = 0
    for url in args.urls:
        try:
            playlist = fetch_list(url)
        except ClipError as e:
            print(f"Lỗi ({url}): {e}", file=sys.stderr)
            failed += 1
            continue
        print(f"{playlist.kind}: {playlist.name} ({len(playlist.tracks)} bài)")
        for track in playlist.tracks:
            minutes, seconds = divmod(round(track.duration), 60)
            print(f"  {track.query} [{minutes}:{seconds:02d}]")
            if args.list:
                continue
            try:
                if args.find:
                    source = find_on_youtube(track)
                    print(f"    -> {source['title']} ({source['channel']}) {source['url']}")
                else:
                    path, source = download_track(track, Path(args.output) / playlist.folder)
                    print(f"    -> {path} (từ {source['url']})")
            except ClipError as e:
                print(f"    Lỗi: {e}", file=sys.stderr)
                failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
