# Bokin Clip: tải nhanh video và nhạc YouTube

Dán link YouTube, bấm nút, nhận file `.mp4` của 15 giây đầu (hoặc bất kỳ đoạn nào bạn chọn), hoặc
file nhạc `.mp3` của cả bài / một đoạn. Dán link bài hát, album hay playlist Spotify để lấy MP3
của từng bài.

**Vì sao nhanh?** App không tải cả video rồi mới cắt. [yt-dlp](https://github.com/yt-dlp/yt-dlp)
lấy link stream, app đọc bảng mục lục ở đầu stream rồi chỉ tải đúng các khúc chứa đoạn cần
lấy (vài MB). Sau đó `ffmpeg` ghép hình với tiếng và cắt ngay trên máy, không encode lại. Video
dài 2 phút hay 2 tiếng thì thời gian tải 15 giây vẫn như nhau.

Nếu cách đó bị YouTube chặn, app tự thử lần lượt: để ffmpeg tự đọc đoạn cần từ link stream,
rồi dùng client "android" của YouTube (thường chỉ có 360p).

## Cài đặt trên Windows (khuyên dùng)

1. Tải file **`BokinClip-Setup-<phiên bản>.exe`**: trong mục **Releases** của repo, hoặc mục
   **Actions › Windows app › (lần chạy mới nhất) › Artifacts**.
2. Bấm đúp để cài. Không cần quyền admin, không cần cài Python hay gì khác: Python, ffmpeg và
   Deno đều đi kèm. Bộ cài chưa được ký số nên lần đầu Windows có thể hiện **"Windows protected
   your PC"**: bấm **More info › Run anyway**.
3. Mở **Bokin Clip** từ biểu tượng ở Desktop hoặc Start Menu. App chạy trong cửa sổ riêng, không
   cần trình duyệt, không có cửa sổ dòng lệnh.

- Mỗi lần mở (tối đa 6 giờ một lần), app tự cập nhật yt-dlp để theo kịp các thay đổi của YouTube.
- Gỡ cài đặt: **Settings › Apps › Bokin Clip › Uninstall**. Video đã tải không bị xoá.
- Cài bản mới: chạy bộ cài mới đè lên, thư mục lưu đã chọn vẫn được giữ.
- App cần WebView2 (có sẵn trên Windows 11 và Windows 10 đã cập nhật). Máy nào thiếu thì app tự
  mở bằng trình duyệt thay cho cửa sổ riêng.
- Lỗi khi khởi động: xem file `%LOCALAPPDATA%\Programs\Bokin Clip\bokin.log`.

### Build bộ cài

GitHub Actions (`.github/workflows/windows.yml`) tự build mỗi lần đẩy code: chạy test trên
Windows, tạo Python nhúng kèm thư viện (`packaging/windows/build.ps1`), đóng gói bằng Inno Setup
(`packaging/windows/installer.iss`), rồi thử cài / chạy / gỡ bộ cài. Đẩy tag `v<phiên bản>`
(khớp `APP_VERSION` trong `app.py`) thì tạo thêm một GitHub Release kèm bộ cài.

## Chạy từ mã nguồn (Windows, macOS, Linux)

Chỉ cần cài **Python 3.10 trở lên** (<https://www.python.org/downloads/>; trên Windows nhớ tick
**"Add python.exe to PATH"** khi cài). ffmpeg và Deno đã đi kèm qua pip, không cần cài riêng.

1. Giải nén thư mục app vào chỗ bạn muốn.
2. Bấm đúp file chạy:
   - **Windows:** `chay-app.bat`
   - **macOS:** `chay-app.command`. Lần đầu macOS có thể chặn; khi đó bấm chuột phải, chọn
     **Open**, rồi chọn **Open** lần nữa. Nếu báo không có quyền chạy, mở Terminal trong thư mục
     app và gõ `chmod +x chay-app.command`.
   - **Linux:** `bash chay-app.command`
3. Lần đầu sẽ mất 1-2 phút để tự cài thư viện vào thư mục `.venv`. Sau đó trình duyệt tự mở
   <http://127.0.0.1:8765>. Các lần sau mở gần như ngay lập tức.

Giữ cửa sổ dòng lệnh mở trong lúc dùng app; đóng cửa sổ đó là tắt app. Mỗi lần mở, file chạy
cũng tự cập nhật yt-dlp để theo kịp các thay đổi của YouTube.

## Cài đặt thủ công

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Nếu máy đã có sẵn `ffmpeg` hoặc `deno`/`node` trong PATH, app sẽ ưu tiên dùng bản đó.

## Dùng giao diện web

```bash
python app.py
```

Mở <http://127.0.0.1:8765>, dán link rồi bấm **Tải 15 giây đầu**. File được lưu vào thư mục
ở ô **Lưu vào**, kèm bản xem trước ngay trên trang.

Các tuỳ chọn:

- **Bắt đầu**: thời điểm bắt đầu, nhập số giây (`90`) hoặc `mm:ss` (`1:30`). Mặc định `0`.
- **Độ dài**: số giây cần lấy. Mặc định `15`, tối đa `300`.
- **Tải về**: **Video (MP4)** hoặc **Nhạc (MP3)**.
  - Nhạc mặc định tải **cả bài** (tick "Tải cả bài"). Bỏ tick để chỉ lấy một đoạn theo "Bắt đầu" và
    "Độ dài".
  - Nhạc chỉ tải stream âm thanh (không tải hình), lưu MP3 192 kbps, có sẵn tên bài và kênh trong
    thông tin file.
- **Chất lượng** (chỉ với video): 360p, 480p, 720p hoặc 1080p. Chất lượng thấp hơn thì tải nhanh hơn.
- **Cắt chính xác từng khung hình**: encode lại để điểm cắt chính xác tuyệt đối (chậm hơn).
  Khi bắt đầu từ giây 0 thì không cần bật tuỳ chọn này.
- **Lưu vào**: thư mục lưu video, dùng chung cho cả hai tab. Mặc định là `Downloads/Bokin Clip`.
  - Bấm **Chọn…** để mở hộp thoại chọn thư mục. Hộp thoại có thể nằm sau cửa sổ trình duyệt,
    nhìn xuống thanh tác vụ nếu không thấy.
  - Hoặc gõ / dán đường dẫn đầy đủ vào ô rồi nhấn Enter, ví dụ `D:\Video\Clip`. Thư mục chưa
    có sẽ được tạo.
  - Bấm **Mở** để mở thư mục.
  - App nhớ thư mục đã chọn cho các lần sau (trong file `settings.json` cạnh `app.py`).

### Tải nhiều video một lúc

Chọn tab **Nhiều video**, dán danh sách link (mỗi dòng một link; dán cả đoạn văn có lẫn chữ
cũng được, app tự lọc ra link và bỏ link trùng), rồi bấm **Tải 15 giây đầu của N video**.

- App tải 3 video cùng lúc và hiện trạng thái từng link: đang chờ, đang tải, xong hoặc lỗi.
- File được **lưu thẳng vào thư mục ở ô "Lưu vào"**, không cần bấm lưu từng file. Bấm
  **Mở thư mục** để xem.
- **Dừng**: các video đang tải dở sẽ tải xong, các link còn lại thì dừng.
- **Tải lại link lỗi**: chỉ tải lại các link bị lỗi hoặc đã dừng.

Đổi cổng hoặc địa chỉ: `PORT=8080 HOST=0.0.0.0 python app.py`.

### Tách bài trong một file nhạc dài

Tab **Tách bài** cắt một file nhạc dài (vd. video tổng hợp nhiều bài đã tải về dạng MP3) thành
từng bài:

1. Bấm **Chọn file…** (hoặc dán đường dẫn file), rồi bấm **Nhận diện các bài hát**.
2. App nhận diện điểm chuyển bài:
   - **theo chương có trong file**: nhạc tải bằng app từ video YouTube có chia chương sẽ có sẵn
     chương và tên từng bài, nên tách rất chính xác;
   - không có chương thì **theo khoảng lặng giữa các bài**, bỏ qua những đoạn nhỏ tiếng ngắn ngay
     trong một bài. Chỉnh **Mỗi bài dài ít nhất** và **Độ nhạy** nếu tách thiếu hoặc thừa.
3. Xem lại danh sách: bấm ▶ để nghe từ đầu mỗi bài, sửa giờ bắt đầu (phút:giây) và tên bài,
   bỏ tick bài không muốn lấy, bấm × để nối một bài vào bài trước. Muốn thêm điểm cắt: phát nhạc
   đến chỗ chuyển bài rồi bấm **Thêm điểm cắt**.
4. Bấm **Cắt và lưu**: mỗi bài thành một file MP3 (`01 - Tên bài.mp3`, ...) trong thư mục
   `<tên file> (tách bài)` bên trong thư mục lưu. File MP3 được cắt nguyên vẹn, không mã hoá lại;
   mỗi bài có sẵn tên bài, số thứ tự, album và nghệ sĩ.

Sau khi tải nhạc ở tab **Một video**, nút **Tách bài trong file này** mở ngay file vừa tải.

Nhạc mix nối liền không có khoảng lặng giữa các bài (kiểu DJ mix) thì app không tự tìm được điểm
chuyển bài; khi đó hãy thêm điểm cắt bằng tay như ở bước 3.

### Tải nhạc từ link Spotify

Tab **Spotify** nhận link bài hát, album hoặc playlist Spotify (bấm **Chia sẻ → Sao chép liên kết**
trong Spotify, kể cả link rút gọn `spotify.link`):

1. Dán link rồi bấm **Lấy danh sách bài**. Link một bài thì app tải luôn.
2. Album/playlist: bỏ tick các bài không cần (hoặc **Chọn tất cả**), rồi bấm **Tải N bài đã
   chọn (MP3)**. App tải 3 bài cùng lúc; bấm **Dừng** hoặc **Tải lại bài lỗi** như tab Nhiều video.
3. Mỗi bài được lưu thành `Nghệ sĩ - Tên bài.mp3`, có sẵn tên bài, nghệ sĩ, album, số thứ tự và
   ảnh bìa theo Spotify. Album/playlist được lưu vào thư mục con mang tên album/playlist bên
   trong thư mục lưu.

**Nhạc lấy từ đâu?** Nhạc trên Spotify được mã hoá (DRM) nên app không tải từ Spotify và không
phá khoá. App chỉ đọc thông tin công khai của bài (tên, nghệ sĩ, thời lượng, ảnh bìa) từ trang
nhúng của Spotify, rồi tìm đúng bài đó trên YouTube (giống công cụ mã nguồn mở
[spotDL](https://github.com/spotDL/spotify-downloader)): ưu tiên bản thu âm gốc (kênh
"… - Topic" của YouTube Music), thời lượng khớp với Spotify, tên bài trùng; tránh bản live,
cover, remix, sped up, karaoke… (trừ khi chính bài trên Spotify là bản đó). Mỗi bài trong danh
sách ghi rõ video YouTube đã dùng để bạn kiểm tra; bản tìm được có thể khác bản trên Spotify một
chút (vd. MV có đoạn mở đầu).

Giới hạn: không cần tài khoản Spotify, nhưng playlist riêng tư thì không đọc được, và với playlist
dài Spotify chỉ cho xem 100 bài đầu. Chưa hỗ trợ link nghệ sĩ và podcast.

## Dùng dòng lệnh

```bash
python clipper.py "https://www.youtube.com/watch?v=VIDEO_ID"                # 15 giây đầu, lưu vào ./downloads
python clipper.py "https://youtu.be/VIDEO_ID" -s 1:30 -d 20 -q 480         # 20 giây từ phút 1:30, 480p
python clipper.py "https://youtu.be/VIDEO_ID" -o ~/Videos --precise
python clipper.py "https://youtu.be/ID_1" "https://youtu.be/ID_2"          # nhiều video
python clipper.py --file danh-sach.txt                                     # link trong file .txt
python clipper.py "https://youtu.be/VIDEO_ID" --audio                       # nhạc MP3 cả bài
python clipper.py "https://youtu.be/VIDEO_ID" --audio -s 1:00 -d 30         # nhạc 30 giây từ phút 1:00
python spotify.py "https://open.spotify.com/album/ALBUM_ID"                 # MP3 từng bài của album Spotify
python spotify.py --find "https://open.spotify.com/playlist/PLAYLIST_ID"    # chỉ xem bài YouTube tìm được
```

## Chạy test

```bash
pip install -r requirements-dev.txt
pytest
```

Test không cần mạng: thay vì YouTube, test tạo một video mẫu bằng ffmpeg và phục vụ qua HTTP
cục bộ.

## Xử lý sự cố

- **`YouTube đang chặn tải video này (lỗi 403)`**: YouTube từ chối cả ba cách tải. Thường là
  tạm thời: thử lại sau vài phút, hoặc chọn chất lượng thấp hơn. Tắt rồi mở lại app bằng file
  chạy nhanh để cập nhật yt-dlp, vì yt-dlp hay ra bản sửa lỗi 403 mới.
- **`Không tải được video: ... Sign in to confirm you're not a bot`**: YouTube đang chặn IP
  của bạn. Thử lại sau, hoặc chạy app ở mạng khác.
- **Bất kỳ lỗi lạ nào khác từ YouTube** (ví dụ lỗi `n challenge`, `Requested format is not
  available`): yt-dlp cập nhật rất thường xuyên để theo kịp YouTube. Mở lại app bằng file chạy
  nhanh để tự cập nhật, hoặc chạy `pip install -U -r requirements.txt`.
- **`Address already in use` / cổng 8765 bị chiếm**: chạy `PORT=8766 python app.py`, hoặc trên
  Windows `set PORT=8766` rồi `python app.py`.

- **Spotify: `Không đọc được thông tin bài hát từ Spotify`**: Spotify vừa đổi trang nhúng; hãy
  cập nhật app lên bản mới. **`Spotify không tìm thấy...`**: link sai, bài đã bị gỡ, hoặc playlist
  đang để riêng tư (hãy chuyển playlist sang công khai).
- **Spotify: bài tải về không đúng bản** (vd. bản live, MV có lời dẫn): xem dòng "nhạc lấy từ
  YouTube" của bài đó, rồi tải bản đúng bằng tab **Một video** (chọn Nhạc MP3) với link YouTube
  bạn muốn.

- **`Máy chưa có tkinter nên không mở được hộp thoại chọn thư mục`**: gõ hoặc dán đường dẫn
  vào ô "Lưu vào" thay cho nút **Chọn…**. Python cài từ python.org (Windows, macOS) có sẵn
  tkinter; trên Linux cài thêm gói `python3-tk`.

## Lưu ý

Chỉ tải nội dung mà bạn có quyền sử dụng (video của chính bạn, video có giấy phép Creative
Commons, ...) và tuân thủ [Điều khoản dịch vụ của YouTube](https://www.youtube.com/t/terms).
Tab Spotify cũng vậy: app chỉ dùng thông tin công khai của Spotify để tìm bài, nhạc vẫn tải từ
YouTube; hãy chỉ tải nhạc bạn được phép dùng.
