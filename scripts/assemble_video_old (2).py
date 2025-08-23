#!/usr/bin/env python3
"""
Memory‑safe video assembler for scenic kitesurf montages.

Fixes & improvements over previous version:
- ✅ Works with Pillow 10+ by monkey‑patching removed constants (ANTIALIAS/BILINEAR/BICUBIC).
- ✅ **Default output is 1080p** (set via --max-height; change if you need 720p for RAM‑constrained machines).
- ✅ CLI flags for max height, FPS, clip lengths, max clips, and threads.
- ✅ Robust closing of clips to release RAM/handles.
- ✅ Optional `--selftest` generates tiny synthetic clips and runs the pipeline.

Usage examples (PowerShell / CMD):
  python scripts/assemble_video.py
  python scripts/assemble_video.py --max-height 1080 --fps 30 --target-seconds 90 --max-clips 18
  python scripts/assemble_video.py --broll-dir content/assets/broll/2025-08-22 --output content/uploads/montage.mp4
  python scripts/assemble_video.py --selftest
"""
from __future__ import annotations
import argparse
import glob
import os
import pathlib
import random
import sys
import tempfile

# --- Pillow 10+ compatibility: provide deprecated resample constants for MoviePy ---
try:
    from PIL import Image as _PIL_Image
    if not hasattr(_PIL_Image, "ANTIALIAS"):
        from PIL import Image
        Image.ANTIALIAS = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
        Image.BICUBIC = Image.Resampling.BICUBIC    # type: ignore[attr-defined]
        Image.BILINEAR = Image.Resampling.BILINEAR  # type: ignore[attr-defined]
except Exception:
    pass

from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips, ColorClip


def latest_broll_dir(root: pathlib.Path) -> pathlib.Path | None:
    candidates = [p for p in root.glob("*") if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


def build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Assemble a scenic montage from downloaded b-roll")
    p.add_argument("--broll-dir", type=pathlib.Path, default=None,
                   help="Folder with mp4 clips (default: latest under content/assets/broll)")
    p.add_argument("--output", type=pathlib.Path, default=pathlib.Path("content/uploads/scenic_montage_001.mp4"))
    p.add_argument("--target-seconds", type=int, default=90, help="Target montage length in seconds")
    p.add_argument("--min-clip-seconds", type=float, default=5.0)
    p.add_argument("--max-clip-seconds", type=float, default=8.0)
    p.add_argument("--max-clips", type=int, default=20, help="Upper bound on clips to concatenate")
    p.add_argument("--max-height", type=int, default=1080, help="Resize each subclip to this height (reduces memory)")
    p.add_argument("--fps", type=int, default=30, help="Output frames per second")
    p.add_argument("--threads", type=int, default=2, help="FFmpeg threads for writing")
    p.add_argument("--music", type=pathlib.Path, default=pathlib.Path("content/assets/music"), help="Folder with .mp3 (optional)")
    p.add_argument("--selftest", action="store_true", help="Generate small synthetic clips and run pipeline")
    return p.parse_args()


def safe_subclip(path: str, args: argparse.Namespace):
    """Open video, take a short subclip, resize, and return it. Caller must .close() later."""
    clip = None
    try:
        clip = VideoFileClip(path, audio=False)
        # Skip tiny clips
        dur = float(getattr(clip, "duration", 0) or 0)
        if dur < args.min_clip_seconds + 0.5:
            return None
        # Choose a random window
        start = random.uniform(0, max(0.0, dur - args.max_clip_seconds - 0.1))
        end = min(dur, start + random.uniform(args.min_clip_seconds, args.max_clip_seconds))
        sub = clip.subclip(start, end)
        # Downscale to reduce memory
        if args.max_height and getattr(sub, "h", 0) > args.max_height:
            sub = sub.resize(height=args.max_height)
        return sub
    except Exception as e:
        print("Skip", path, e)
        try:
            if clip is not None:
                clip.reader.close()
                if getattr(clip, "audio", None):
                    clip.audio.reader.close_proc()
        except Exception:
            pass
        return None


def write_video(selected, args: argparse.Namespace):
    video = concatenate_videoclips(selected, method="compose")

    # Optional music
    music_files = []
    try:
        if args.music and args.music.exists():
            music_files = [p for p in args.music.glob("*.mp3")]
    except Exception:
        music_files = []
    if music_files:
        try:
            music = AudioFileClip(str(random.choice(music_files))).volumex(0.15)
            video = video.set_audio(music.set_duration(video.duration))
        except Exception as e:
            print("Music load error:", e)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    video.write_videofile(
        str(args.output),
        codec="libx264",
        audio_codec="aac",
        fps=args.fps,
        threads=max(1, int(args.threads)),
        preset="veryfast",
        remove_temp=True,
    )
    print("Saved", args.output)


def selftest_run():
    """Create 3 tiny color clips, run through the same pipeline, and write a temp mp4."""
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="assemble_selftest_"))
    try:
        colors = [(255, 80, 80), (80, 255, 80), (80, 80, 255)]
        srcs = []
        for i, col in enumerate(colors, 1):
            clip = ColorClip(size=(640, 360), color=col, duration=2).set_fps(24)
            p = tmpdir / f"test_{i}.mp4"
            clip.write_videofile(str(p), codec="libx264", audio=False, fps=24, preset="ultrafast", threads=1)
            srcs.append(str(p))
            clip.close()

        class DummyArgs:
            min_clip_seconds = 1.0
            max_clip_seconds = 1.5
            max_height = 480
            fps = 24
            threads = 1
            music = pathlib.Path("./no_music")
            output = tmpdir / "out.mp4"

        args = DummyArgs()
        selected = []
        for s in srcs:
            sc = safe_subclip(s, args)
            if sc:
                selected.append(sc)
        if not selected:
            raise SystemExit("selftest: no subclips")
        write_video(selected, args)
        for c in selected:
            c.close()
        print("[selftest] OK =>", args.output)
    finally:
        pass


def main():
    args = build_args()

    if args.selftest:
        selftest_run()
        return

    broll_root = pathlib.Path("content/assets/broll")
    broll_dir = args.broll_dir or latest_broll_dir(broll_root)
    if not broll_dir or not broll_dir.exists():
        raise SystemExit("No b-roll directory found. Run scripts/fetch_assets.py first.")

    mp4s = sorted(glob.glob(str(broll_dir / "*.mp4")))
    if not mp4s:
        raise SystemExit(f"No mp4 files found in {broll_dir}")

    random.shuffle(mp4s)

    selected = []
    total = 0.0
    for path in mp4s:
        if len(selected) >= args.max_clips or total >= args.target_seconds:
            break
        sub = safe_subclip(path, args)
        if sub is None:
            continue
        selected.append(sub)
        total += float(sub.duration or 0)

    if not selected:
        raise SystemExit("No suitable clips to assemble.")

    try:
        write_video(selected, args)
    finally:
        # Close all subclips to free memory
        for c in selected:
            try:
                c.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
