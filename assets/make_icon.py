"""Vẽ biểu tượng Bokin Clip: ô vuông bo góc màu chàm, nút play trắng giữa hai dấu ngoặc "cắt đoạn".

Chạy lại: python assets/make_icon.py .   (cần Pillow)
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

out = Path(sys.argv[1])
N = 1024
img = Image.new("RGBA", (N, N), (0, 0, 0, 0))

# nền: gradient chàm từ trên xuống, bo góc
grad = Image.new("RGBA", (N, N))
top, bottom = (99, 102, 241), (67, 56, 202)
gd = ImageDraw.Draw(grad)
for y in range(N):
    t = y / (N - 1)
    gd.line([(0, y), (N, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
mask = Image.new("L", (N, N), 0)
ImageDraw.Draw(mask).rounded_rectangle([40, 40, N - 40, N - 40], radius=220, fill=255)
img.paste(grad, (0, 0), mask)

d = ImageDraw.Draw(img)
white = (255, 255, 255, 255)
# nút play
cx, cy = N // 2 + 30, N // 2
d.polygon([(cx - 150, cy - 190), (cx - 150, cy + 190), (cx + 180, cy)], fill=white)
# hai dấu ngoặc [ ] hai bên: ý "cắt đoạn"
w, h, arm = 60, 460, 120
for x0, direction in ((200, 1), (N - 200 - w, -1)):
    y0 = cy - h // 2
    d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=24, fill=white)
    ax0 = x0 if direction == 1 else x0 + w - arm
    d.rounded_rectangle([ax0, y0, ax0 + arm, y0 + w], radius=24, fill=white)
    d.rounded_rectangle([ax0, y0 + h - w, ax0 + arm, y0 + h], radius=24, fill=white)

sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
img.save(out / "assets" / "bokin.ico", sizes=[(s, s) for s in sizes])
img.resize((256, 256), Image.LANCZOS).save(out / "assets" / "bokin-256.png")
img.resize((64, 64), Image.LANCZOS).save(out / "static" / "icon.png")
img.resize((128, 128), Image.LANCZOS).save(out / "static" / "icon-128.png")
print("ok")
