#!/usr/bin/env python3
"""
Thumbnail generator (Pillow 10+ compatible)

- Replaces deprecated ImageDraw.textsize() with textbbox()-based measurement.
- Centers title and brand text, adds subtle frame lines.
- Defaults to 1280x720 (YouTube recommended), configurable via --size.
- Adds --title/--brand/--output flags and a --selftest.

Examples:
  python scripts/generate_thumbnail.py
  python scripts/generate_thumbnail.py --title "Downwind Dream" --brand "Blue Horizon Kitesurf"
  python scripts/generate_thumbnail.py --size 1280x720 --output content/uploads/thumb.jpg
  python scripts/generate_thumbnail.py --selftest
"""
from __future__ import annotations
from PIL import Image, ImageDraw, ImageFont
import argparse
import pathlib
import re

DEFAULT_SIZE = (1280, 720)  # YouTube recommended
DEFAULT_TITLE = "Trade Winds On"
DEFAULT_BRAND = "Blue Horizon Kitesurf"
DEFAULT_OUT = pathlib.Path("content/uploads/thumbnail_001.jpg")


def parse_size(s: str) -> tuple[int, int]:
    m = re.match(r"^(\d+)x(\d+)$", s)
    if not m:
        raise argparse.ArgumentTypeError("--size must look like 1280x720")
    return int(m.group(1)), int(m.group(2))


def load_font(size: int) -> ImageFont.ImageFont:
    # Try a few common system fonts; fall back to default if not found
    for name in ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    """Return (width, height) of text for current Pillow.
    Pillow ≥10: use textbbox; older: fall back to textsize.
    """
    if hasattr(draw, "textbbox"):
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        return (r - l, b - t)
    # Fallback for very old Pillow
    return draw.textsize(text, font=font)  # type: ignore[attr-defined]


def make_thumbnail(size=DEFAULT_SIZE, title=DEFAULT_TITLE, brand=DEFAULT_BRAND) -> Image.Image:
    W, H = size
    bg = Image.new("RGB", (W, H), (13, 27, 42))  # ink
    d = ImageDraw.Draw(bg)

    # Frame lines
    d.rounded_rectangle((20, 20, W - 20, H - 20), radius=28, outline=(15, 181, 186), width=14)
    d.rounded_rectangle((40, 40, W - 40, H - 40), radius=24, outline=(228, 210, 184), width=10)

    # Title (centered)
    title_font = load_font(max(44, int(min(W, H) * 0.09)))
    tw, th = measure(d, title, title_font)
    tx, ty = (W - tw) // 2, (H - th) // 2
    # Add a slight stroke for readability
    d.text((tx, ty), title, fill=(255, 255, 255), font=title_font, stroke_width=2, stroke_fill=(0, 0, 0))

    # Brand (bottom-right)
    brand_font = load_font(max(22, int(min(W, H) * 0.03)))
    bw, bh = measure(d, brand, brand_font)
    d.text((W - 24 - bw, H - 24 - bh), brand, fill=(228, 210, 184), font=brand_font)

    return bg


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate a branded thumbnail (Pillow 10+ compatible)")
    p.add_argument("--size", type=parse_size, default=f"{DEFAULT_SIZE[0]}x{DEFAULT_SIZE[1]}")
    p.add_argument("--title", default=DEFAULT_TITLE)
    p.add_argument("--brand", default=DEFAULT_BRAND)
    p.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUT)
    p.add_argument("--selftest", action="store_true", help="Run basic generation tests and exit")
    return p.parse_args()


def selftest() -> None:
    import tempfile

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thumb_test_"))
    # Case 1: default
    img1 = make_thumbnail()
    out1 = tmp / "thumb1.jpg"
    img1.save(out1, "JPEG", quality=92)
    assert out1.exists() and out1.stat().st_size > 0, "default thumbnail not created"

    # Case 2: custom size/title/brand
    img2 = make_thumbnail(size=(640, 360), title="Downwind Dream", brand="Blue Horizon Kitesurf")
    out2 = tmp / "thumb2.jpg"
    img2.save(out2, "JPEG", quality=90)
    assert out2.exists() and out2.stat().st_size > 0, "custom thumbnail not created"

    print("[selftest] OK — thumbnails written to:", tmp)


def main():
    args = parse_args()
    if args.selftest:
        selftest()
        return

    W, H = args.size if isinstance(args.size, tuple) else parse_size(args.size)
    img = make_thumbnail(size=(W, H), title=args.title, brand=args.brand)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    img.save(args.output, "JPEG", quality=92)
    print("Saved", args.output)


if __name__ == "__main__":
    main()
