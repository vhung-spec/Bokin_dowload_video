import base64
import contextlib
import json
import shutil
import subprocess
import urllib.error
from pathlib import Path

import pytest

import app as web
import spotify
from clipper import Clip, ClipError
from spotify import SpotifyList, SpotifyTrack

TRACK_ID = "4cOdK2wGLETKBW3PvgPWqT"
COVER = {"sources": [{"url": "https://i.scdn.co/image/small", "width": 64},
                     {"url": "https://i.scdn.co/image/big", "width": 640},
                     {"url": "https://i.scdn.co/image/mid", "width": 300}]}


def next_data_page(entity):
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def base64_page(script_id, data):
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    return f'<html><script id="{script_id}" type="text/plain">{encoded}</script></html>'


ALBUM = {
    "type": "album", "name": "Chúng Ta Của Hiện Tại", "coverArt": COVER,
    "trackList": [
        {"title": "Nơi Này Có Anh", "subtitle": "Sơn Tùng M-TP", "duration": 261000, "isPlayable": True},
        {"title": "Bài  hai", "subtitle": "Ca sĩ A, Ca sĩ B", "duration": 200500},
        {"title": "", "subtitle": "Không tên", "duration": 1000},  # mục rỗng: bỏ qua
    ],
}
TRACK = {"type": "track", "name": "Nơi Này Có Anh", "title": "Nơi Này Có Anh", "duration": 261000,
         "artists": [{"name": "Sơn Tùng M-TP"}, {"name": "Khách mời"}], "coverArt": COVER}
PLAYLIST = {"type": "playlist", "name": "Nhạc Việt", "coverArt": COVER,
            "trackList": [{"title": "Song", "subtitle": "Artist", "duration": 180000}]}


@pytest.fixture
def spotify_page(monkeypatch):
    """Giả lập Spotify: trả về trang nhúng đã đặt, ghi lại các link đã mở."""
    pages, opened = {}, []

    def fake_get(url, binary=False, timeout=20):
        opened.append(url)
        if url not in pages:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return url, pages[url]

    monkeypatch.setattr(spotify, "_http_get", fake_get)
    return pages, opened


@pytest.mark.parametrize("url, expected", [
    (f"https://open.spotify.com/track/{TRACK_ID}", ("track", TRACK_ID)),
    (f"https://open.spotify.com/intl-vi/album/{TRACK_ID}?si=abc123", ("album", TRACK_ID)),
    (f"  https://open.spotify.com/playlist/{TRACK_ID}?si=x&pt=y  ", ("playlist", TRACK_ID)),
    (f"https://open.spotify.com/embed/track/{TRACK_ID}", ("track", TRACK_ID)),
    (f"spotify:track:{TRACK_ID}", ("track", TRACK_ID)),
])
def test_resolve_links(url, expected):
    assert spotify.resolve(url) == expected


def test_resolve_link_inside_shared_text():
    text = f"Nghe bài này trên Spotify: https://open.spotify.com/track/{TRACK_ID}?si=1 nhé"
    assert spotify.resolve(text) == ("track", TRACK_ID)


@pytest.mark.parametrize("text", ["https://spotify.link/AbCdEf", "Album hay: https://spotify.link/AbCdEf"])
def test_resolve_short_link(monkeypatch, text):
    opened = []
    monkeypatch.setattr(spotify, "_http_get", lambda url, **kw: (
        opened.append(url), (f"https://open.spotify.com/album/{TRACK_ID}?si=1", "<html></html>"))[1])
    assert spotify.resolve(text) == ("album", TRACK_ID)
    assert opened == ["https://spotify.link/AbCdEf"]


@pytest.mark.parametrize("url, message", [
    (f"https://open.spotify.com/artist/{TRACK_ID}", "nghệ sĩ"),
    (f"https://open.spotify.com/episode/{TRACK_ID}", "podcast"),
    ("https://www.youtube.com/watch?v=abc", "không hợp lệ"),
    ("", "không hợp lệ"),
])
def test_resolve_rejects_other_links(url, message):
    with pytest.raises(ClipError, match=message):
        spotify.resolve(url)


def test_is_spotify_link():
    assert spotify.is_spotify_link(f"https://open.spotify.com/track/{TRACK_ID}")
    assert spotify.is_spotify_link("https://spotify.link/abc")
    assert not spotify.is_spotify_link("https://youtu.be/abc")


def test_album_from_next_data(spotify_page):
    pages, opened = spotify_page
    pages[f"https://open.spotify.com/embed/album/{TRACK_ID}"] = next_data_page(ALBUM)
    result = spotify.fetch_list(f"https://open.spotify.com/album/{TRACK_ID}?si=x")

    assert opened == [f"https://open.spotify.com/embed/album/{TRACK_ID}"]
    assert (result.kind, result.name, result.folder) == ("album", "Chúng Ta Của Hiện Tại", "Chúng Ta Của Hiện Tại")
    assert result.tracks == [
        SpotifyTrack("Nơi Này Có Anh", "Sơn Tùng M-TP", 261.0, "Chúng Ta Của Hiện Tại", "https://i.scdn.co/image/big", 1, 3),
        SpotifyTrack("Bài hai", "Ca sĩ A, Ca sĩ B", 200.5, "Chúng Ta Của Hiện Tại", "https://i.scdn.co/image/big", 2, 3),
    ]


def test_track_from_resource_script(spotify_page):
    pages, _ = spotify_page
    pages[f"https://open.spotify.com/embed/track/{TRACK_ID}"] = base64_page("resource", TRACK)
    result = spotify.fetch_list(f"spotify:track:{TRACK_ID}")

    assert (result.kind, result.folder) == ("track", "")  # bài lẻ: lưu thẳng vào thư mục lưu
    assert result.tracks == [SpotifyTrack("Nơi Này Có Anh", "Sơn Tùng M-TP, Khách mời", 261.0, "",
                                          "https://i.scdn.co/image/big")]
    assert result.tracks[0].query == "Sơn Tùng M-TP, Khách mời - Nơi Này Có Anh"


def test_playlist_from_initial_state_script(spotify_page):
    pages, _ = spotify_page
    pages[f"https://open.spotify.com/embed/playlist/{TRACK_ID}"] = base64_page("initial-state",
                                                                               {"data": {"entity": PLAYLIST}})
    result = spotify.fetch_list(f"https://open.spotify.com/playlist/{TRACK_ID}")

    assert (result.kind, result.name, result.folder) == ("playlist", "Nhạc Việt", "Nhạc Việt")
    # Ảnh bìa và tên của playlist không phải của từng bài.
    assert result.tracks == [SpotifyTrack("Song", "Artist", 180.0)]


@pytest.mark.parametrize("html", [
    "<html>Spotify đổi trang</html>",
    '<script id="__NEXT_DATA__">{"props": {}}</script>',
    '<script id="resource">không phải base64</script>',
])
def test_unknown_page_is_reported(spotify_page, html):
    pages, _ = spotify_page
    pages[f"https://open.spotify.com/embed/track/{TRACK_ID}"] = html
    with pytest.raises(ClipError, match="Không đọc được thông tin"):
        spotify.fetch_list(f"https://open.spotify.com/track/{TRACK_ID}")


def test_empty_album_is_reported(spotify_page):
    pages, _ = spotify_page
    pages[f"https://open.spotify.com/embed/album/{TRACK_ID}"] = next_data_page({**ALBUM, "trackList": []})
    with pytest.raises(ClipError, match="Không thấy bài hát nào"):
        spotify.fetch_list(f"https://open.spotify.com/album/{TRACK_ID}")


def test_missing_or_private_playlist(spotify_page):
    with pytest.raises(ClipError, match="riêng tư"):
        spotify.fetch_list(f"https://open.spotify.com/playlist/{TRACK_ID}")


def test_network_error(monkeypatch):
    def offline(url, **kwargs):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(spotify, "_http_get", offline)
    with pytest.raises(ClipError, match="Không kết nối được tới Spotify"):
        spotify.fetch_list(f"https://open.spotify.com/track/{TRACK_ID}")


# --- Chọn đúng bài trên YouTube ------------------------------------------------------------------

SONG = SpotifyTrack("Nơi Này Có Anh", "Sơn Tùng M-TP", 261.0)


def entry(video_id, title, channel, duration):
    return {"id": video_id, "title": title, "channel": channel, "duration": duration}


def test_prefers_the_original_recording():
    entries = [
        entry("live", "Nơi Này Có Anh (Live at Concert)", "Sơn Tùng M-TP Official", 262),
        entry("mv", "NƠI NÀY CÓ ANH | OFFICIAL MUSIC VIDEO | SƠN TÙNG M-TP", "Sơn Tùng M-TP Official", 279),
        entry("cover", "Nơi này có anh - cover by fan", "Fan", 260),
        entry("topic", "Nơi Này Có Anh", "Sơn Tùng M-TP - Topic", 262),
        entry("slow", "Nơi Này Có Anh (Slowed + Reverb)", "Lofi", 300),
    ]
    assert spotify.best_match(SONG, entries) == {
        "url": "https://www.youtube.com/watch?v=topic", "title": "Nơi Này Có Anh", "channel": "Sơn Tùng M-TP - Topic",
    }
    # Không có bản của kênh Topic: lấy MV chính thức (tên khớp, chỉ dài hơn chút) chứ không lấy bản live/cover.
    assert spotify.best_match(SONG, entries[:3])["url"].endswith("=mv")


def test_version_words_in_spotify_title_are_not_penalised():
    remix = SpotifyTrack("Nơi Này Có Anh (Remix)", "Sơn Tùng M-TP", 200.0)
    entries = [entry("orig", "Nơi Này Có Anh", "Sơn Tùng M-TP - Topic", 261),
               entry("remix", "Nơi Này Có Anh Remix", "DJ", 201)]
    assert spotify.best_match(remix, entries)["url"].endswith("=remix")


def test_rejects_results_with_the_wrong_length():
    entries = [entry("mix", "Nơi Này Có Anh - 1 hour", "Loop", 3600), entry("x", "Nơi Này Có Anh", "Ai đó", 120)]
    with pytest.raises(ClipError, match="thời lượng khớp"):
        spotify.best_match(SONG, entries)
    with pytest.raises(ClipError, match="Không tìm thấy"):
        spotify.best_match(SONG, [])


def test_search_uses_artist_and_title(monkeypatch):
    queries = []

    class FakeYDL:
        def extract_info(self, query, download):
            queries.append((query, download))
            return {"entries": [entry("topic", "Nơi Này Có Anh", "Sơn Tùng M-TP - Topic", 261)]}

    @contextlib.contextmanager
    def fake_youtube_dl(quiet, **params):
        assert params["extract_flat"] == "in_playlist"
        yield FakeYDL()

    monkeypatch.setattr(spotify, "_youtube_dl", fake_youtube_dl)
    assert spotify.find_on_youtube(SONG)["url"].endswith("=topic")
    assert queries == [("ytsearch8:Sơn Tùng M-TP - Nơi Này Có Anh", False)]


# --- Tải và ghi thông tin ---------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="cần ffmpeg")


@pytest.fixture(scope="module")
def tone(tmp_path_factory):
    path = tmp_path_factory.mktemp("tone") / "tone.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-metadata", "title=Tên video YouTube", "-metadata", "artist=Kênh YouTube", str(path)], check=True)
    return path


@needs_ffmpeg
def test_download_track_tags_and_names_the_file(monkeypatch, tmp_path, tone):
    from mutagen.id3 import ID3
    from mutagen.mp3 import MP3

    calls = []

    def fake_download(url, out_dir, **kwargs):
        calls.append(dict(url=url, out_dir=Path(out_dir), **kwargs))
        path = Path(out_dir) / "Tên video YouTube [topic].mp3"
        shutil.copy(tone, path)
        return Clip(path=path, title="Tên video YouTube", video_id="topic", start=0, end=2)

    covers = []

    def fake_get(url, binary=False, timeout=20):
        covers.append((url, binary))
        return url, b"\xff\xd8\xff\xe0 anh bia"

    monkeypatch.setattr(spotify, "find_on_youtube", lambda track, quiet=True: {
        "url": "https://www.youtube.com/watch?v=topic", "title": "Nơi Này Có Anh", "channel": "Sơn Tùng M-TP - Topic"})
    monkeypatch.setattr(spotify, "download_clip", fake_download)
    monkeypatch.setattr(spotify, "_http_get", fake_get)

    track = SpotifyTrack("Nơi Này Có Anh", "Sơn Tùng M-TP", 261.0, "Chúng Ta", "https://i.scdn.co/image/big", 3, 12)
    path, source = spotify.download_track(track, tmp_path / "Chúng Ta")

    assert path == tmp_path / "Chúng Ta" / "Sơn Tùng M-TP - Nơi Này Có Anh.mp3"
    assert source["url"] == "https://www.youtube.com/watch?v=topic"
    assert calls[0]["url"] == "https://www.youtube.com/watch?v=topic"
    assert (calls[0]["duration"], calls[0]["audio"]) == (None, True)  # cả bài, MP3
    assert not calls[0]["out_dir"].exists()  # thư mục tạm đã dọn
    assert covers == [("https://i.scdn.co/image/big", True)]

    tags = ID3(path)
    assert str(tags["TIT2"]) == "Nơi Này Có Anh"
    assert str(tags["TPE1"]) == "Sơn Tùng M-TP"
    assert str(tags["TALB"]) == "Chúng Ta"
    assert str(tags["TRCK"]) == "3/12"
    assert tags.getall("APIC")[0].data == b"\xff\xd8\xff\xe0 anh bia"
    assert MP3(path).info.length == pytest.approx(2, abs=0.2)  # file nhạc vẫn nguyên vẹn


def test_cover_only_from_spotify_image_servers(monkeypatch):
    opened = []
    monkeypatch.setattr(spotify, "_http_get", lambda url, **kw: (opened.append(url), (url, b"img"))[1])
    assert spotify._cover_bytes("http://127.0.0.1:8765/api/ping") is None
    assert spotify._cover_bytes("https://evil.example/image.jpg") is None
    assert spotify._cover_bytes("https://i.scdn.co/image/abc") == b"img"
    assert opened == ["https://i.scdn.co/image/abc"]


def test_track_from_dict():
    data = {"title": " Song ", "artists": "A", "duration": "180.5", "album": "X", "cover": "", "number": 2, "total": 9}
    assert spotify.track_from_dict(data) == SpotifyTrack("Song", "A", 180.5, "X", "", 2, 9)
    for bad in (None, "song", {}, {"title": ""}, {"title": "x", "duration": "abc"}):
        with pytest.raises(ClipError, match="không hợp lệ"):
            spotify.track_from_dict(bad)


def test_cli_lists_tracks(monkeypatch, capsys):
    monkeypatch.setattr(spotify, "fetch_list", lambda url: SpotifyList("album", "Album", [SONG]))
    assert spotify.main(["--list", "https://open.spotify.com/album/x"]) == 0
    assert "Sơn Tùng M-TP - Nơi Này Có Anh [4:21]" in capsys.readouterr().out


# --- API của app ------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "OUTPUT_DIR", tmp_path / "Bokin Clip")
    monkeypatch.setattr(web, "SETTINGS_FILE", tmp_path / "settings.json")
    web.app.config["TESTING"] = True
    return web.app.test_client()


def test_spotify_link_in_youtube_tab_points_to_spotify_tab(client):
    response = client.post("/api/save", json={"url": f"https://open.spotify.com/track/{TRACK_ID}"})
    assert response.status_code == 400
    assert "tab Spotify" in response.get_json()["error"]


def test_index_has_spotify_tab(client):
    page = client.get("/").get_data(as_text=True)
    assert 'id="tab-spotify"' in page and "Link Spotify" in page


def test_list_api(client, monkeypatch):
    album = SpotifyList("album", "Album: Hay/Nhất", [SpotifyTrack("Song", "A", 180.0, "Album", "https://i.scdn.co/x", 1, 1)])
    monkeypatch.setattr(spotify, "fetch_list", lambda url: album)
    data = client.post("/api/spotify/list", json={"url": "https://open.spotify.com/album/x"}).get_json()
    assert data == {"kind": "album", "name": "Album: Hay/Nhất", "folder": "Album HayNhất", "limited": False,
                    "tracks": [{"title": "Song", "artists": "A", "duration": 180.0, "album": "Album",
                                "cover": "https://i.scdn.co/x", "number": 1, "total": 1}]}


def test_list_api_flags_long_playlists(client, monkeypatch):
    long_list = SpotifyList("playlist", "Dài", [SONG] * spotify.EMBED_LIMIT)
    monkeypatch.setattr(spotify, "fetch_list", lambda url: long_list)
    assert client.post("/api/spotify/list", json={"url": "x"}).get_json()["limited"] is True


def test_list_api_reports_errors(client):
    response = client.post("/api/spotify/list", json={"url": "https://www.youtube.com/watch?v=abc"})
    assert response.status_code == 400
    assert "Link Spotify không hợp lệ" in response.get_json()["error"]


@pytest.mark.parametrize("folder, expected", [("Album", "Album"), ("", ""), ("..", ""), ("../../Windows", "....Windows")])
def test_track_api_saves_inside_output_folder(client, monkeypatch, tmp_path, folder, expected):
    calls = []

    def fake_download(track, out_dir):
        calls.append((track, out_dir))
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "A - Song.mp3"
        path.write_bytes(b"mp3")
        return path, {"url": "https://www.youtube.com/watch?v=v", "title": "Song (Official)", "channel": "A - Topic"}

    monkeypatch.setattr(spotify, "download_track", fake_download)
    response = client.post("/api/spotify/track", json={
        "folder": folder, "track": {"title": "Song", "artists": "A", "duration": 180, "number": 1, "total": 2}})

    assert response.status_code == 200
    out_dir = tmp_path / "Bokin Clip" / expected if expected else tmp_path / "Bokin Clip"
    assert calls == [(SpotifyTrack("Song", "A", 180.0, "", "", 1, 2), out_dir)]
    assert response.get_json() == {
        "file": "A - Song.mp3", "path": str(out_dir / "A - Song.mp3"), "folder": str(out_dir), "size": 3,
        "youtube_url": "https://www.youtube.com/watch?v=v", "youtube_title": "Song (Official)",
        "youtube_channel": "A - Topic",
    }


def test_track_api_reports_errors(client, monkeypatch):
    assert client.post("/api/spotify/track", json={"track": {"artists": "A"}}).status_code == 400

    def not_found(track, out_dir):
        raise ClipError('Không tìm thấy "A - Song" trên YouTube.')

    monkeypatch.setattr(spotify, "download_track", not_found)
    response = client.post("/api/spotify/track", json={"track": {"title": "Song", "artists": "A"}})
    assert response.status_code == 400
    assert response.get_json() == {"error": 'Không tìm thấy "A - Song" trên YouTube.'}
