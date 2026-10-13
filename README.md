# Bokin Clip: tải nhanh 15 giây đầu của video YouTube

Dán link YouTube, bấm nút, nhận file `.mp4` của 15 giây đầu (hoặc bất kỳ đoạn nào bạn chọn).

**Vì sao nhanh?** App không tải cả video rồi mới cắt. [yt-dlp](https://github.com/yt-dlp/yt-dlp)
lấy link stream trực tiếp, rồi `ffmpeg` chỉ đọc đúng khoảng thời gian cần và copy nguyên
stream (không encode lại). Video dài 2 phút hay 2 tiếng thì thời gian tải 15 giây vẫn như nhau.

## Cài đặt

Cần có:

- Python 3.10 trở lên
- [ffmpeg](https://ffmpeg.org/download.html) (`sudo apt install ffmpeg`, `brew install ffmpeg`, hoặc `winget install ffmpeg`)
- Một JavaScript runtime để yt-dlp giải mã link YouTube: [Deno](https://deno.com/) (khuyên dùng) hoặc Node.js

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Dùng giao diện web

```bash
python app.py
```

Mở <http://127.0.0.1:5000>, dán link rồi bấm **Tải 15 giây đầu**. File sẽ tự tải về, kèm
bản xem trước ngay trên trang.

Các tuỳ chọn:

- **Bắt đầu**: thời điểm bắt đầu, nhập số giây (`90`) hoặc `mm:ss` (`1:30`). Mặc định `0`.
- **Độ dài**: số giây cần lấy. Mặc định `15`, tối đa `300`.
- **Chất lượng**: 360p, 480p, 720p hoặc 1080p. Chất lượng thấp hơn thì tải nhanh hơn.
- **Cắt chính xác từng khung hình**: encode lại để điểm cắt chính xác tuyệt đối (chậm hơn).
  Khi bắt đầu từ giây 0 thì không cần bật tuỳ chọn này.

Đổi cổng hoặc địa chỉ: `PORT=8080 HOST=0.0.0.0 python app.py`.

## Dùng dòng lệnh

```bash
python clipper.py "https://www.youtube.com/watch?v=VIDEO_ID"                # 15 giây đầu, lưu vào ./downloads
python clipper.py "https://youtu.be/VIDEO_ID" -s 1:30 -d 20 -q 480         # 20 giây từ phút 1:30, 480p
python clipper.py "https://youtu.be/VIDEO_ID" -o ~/Videos --precise
```

## Chạy test

```bash
pip install -r requirements-dev.txt
pytest
```

Test không cần mạng: thay vì YouTube, test tạo một video mẫu bằng ffmpeg và phục vụ qua HTTP
cục bộ.

## Xử lý sự cố

- **`Không tải được video: ... Sign in to confirm you're not a bot`**: YouTube đang chặn IP
  của bạn. Thử lại sau, hoặc chạy app ở mạng khác.
- **Lỗi về JavaScript runtime / `n challenge`**: cài Deno hoặc Node.js, rồi cập nhật yt-dlp:
  `pip install -U "yt-dlp[default]"`.
- **Bất kỳ lỗi lạ nào khác từ YouTube**: yt-dlp cập nhật rất thường xuyên để theo kịp YouTube,
  hãy chạy `pip install -U "yt-dlp[default]"` trước tiên.

## Lưu ý

Chỉ tải nội dung mà bạn có quyền sử dụng (video của chính bạn, video có giấy phép Creative
Commons, ...) và tuân thủ [Điều khoản dịch vụ của YouTube](https://www.youtube.com/t/terms).
