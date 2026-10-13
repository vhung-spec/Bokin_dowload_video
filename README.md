# Bokin Clip: tải nhanh 15 giây đầu của video YouTube

Dán link YouTube, bấm nút, nhận file `.mp4` của 15 giây đầu (hoặc bất kỳ đoạn nào bạn chọn).

**Vì sao nhanh?** App không tải cả video rồi mới cắt. [yt-dlp](https://github.com/yt-dlp/yt-dlp)
lấy link stream, app đọc bảng mục lục ở đầu stream rồi chỉ tải đúng các khúc chứa đoạn cần
lấy (vài MB). Sau đó `ffmpeg` ghép hình với tiếng và cắt ngay trên máy, không encode lại. Video
dài 2 phút hay 2 tiếng thì thời gian tải 15 giây vẫn như nhau.

Nếu cách đó bị YouTube chặn, app tự thử lần lượt: để ffmpeg tự đọc đoạn cần từ link stream,
rồi dùng client "android" của YouTube (thường chỉ có 360p).

## Chạy trên máy tính (cách nhanh nhất)

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
- **Chất lượng**: 360p, 480p, 720p hoặc 1080p. Chất lượng thấp hơn thì tải nhanh hơn.
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

## Dùng dòng lệnh

```bash
python clipper.py "https://www.youtube.com/watch?v=VIDEO_ID"                # 15 giây đầu, lưu vào ./downloads
python clipper.py "https://youtu.be/VIDEO_ID" -s 1:30 -d 20 -q 480         # 20 giây từ phút 1:30, 480p
python clipper.py "https://youtu.be/VIDEO_ID" -o ~/Videos --precise
python clipper.py "https://youtu.be/ID_1" "https://youtu.be/ID_2"          # nhiều video
python clipper.py --file danh-sach.txt                                     # link trong file .txt
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

- **`Máy chưa có tkinter nên không mở được hộp thoại chọn thư mục`**: gõ hoặc dán đường dẫn
  vào ô "Lưu vào" thay cho nút **Chọn…**. Python cài từ python.org (Windows, macOS) có sẵn
  tkinter; trên Linux cài thêm gói `python3-tk`.

## Lưu ý

Chỉ tải nội dung mà bạn có quyền sử dụng (video của chính bạn, video có giấy phép Creative
Commons, ...) và tuân thủ [Điều khoản dịch vụ của YouTube](https://www.youtube.com/t/terms).
