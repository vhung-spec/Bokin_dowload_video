#!/usr/bin/env bash
# Bấm đúp để chạy Bokin Clip trên macOS (Linux: bash chay-app.command).
# Lần đầu sẽ tự cài mọi thứ cần thiết vào thư mục .venv, các lần sau mở ngay.
cd "$(dirname "$0")" || exit 1

fail() { echo; echo "$1"; read -r -p "Nhấn Enter để đóng..."; exit 1; }

if [ ! -x .venv/bin/python ]; then
  PY=""
  for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      PY="$candidate"; break
    fi
  done
  [ -n "$PY" ] || fail "Cần Python 3.10 trở lên. Tải tại https://www.python.org/downloads/ rồi chạy lại file này."
  echo "Lần đầu chạy: đang cài đặt, mất khoảng 1-2 phút..."
  "$PY" -m venv .venv || fail "Không tạo được môi trường Python (.venv)."
fi

echo "Đang kiểm tra cập nhật..."
.venv/bin/python -m pip install --disable-pip-version-check -q -U -r requirements.txt ||
  echo "Cảnh báo: không cập nhật được thư viện (mất mạng?), vẫn thử chạy app..."

.venv/bin/python app.py --open || fail "App bị lỗi, xem thông báo phía trên."
