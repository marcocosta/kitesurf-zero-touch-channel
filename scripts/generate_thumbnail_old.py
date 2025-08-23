
from PIL import Image, ImageDraw, ImageFont
import pathlib

W, H = 1280, 720
bg = Image.new("RGB", (W, H), (13, 27, 42))  # ink
d = ImageDraw.Draw(bg)
d.rounded_rectangle((20, 20, W - 20, H - 20), radius=28, outline=(15, 181, 186), width=14)
d.rounded_rectangle((40, 40, W - 40, H - 40), radius=24, outline=(228, 210, 184), width=10)
title = "Trade Winds On"
try:
    font = ImageFont.truetype("arial.ttf", 88)
except Exception:
    font = ImageFont.load_default()
tw, th = d.textsize(title, font=font)
d.text(((W - tw) // 2, (H - th) // 2), title, fill=(255, 255, 255), font=font)
brand = "Blue Horizon Kitesurf"
try:
    font2 = ImageFont.truetype("arial.ttf", 28)
except Exception:
    font2 = ImageFont.load_default()
d.text((W - 20, H - 40), brand, fill=(228, 210, 184), font=font2, anchor="ra")
out = pathlib.Path("content/uploads/thumbnail_001.jpg")
out.parent.mkdir(parents=True, exist_ok=True)
bg.save(out, "JPEG", quality=92)
print("Saved", out)
