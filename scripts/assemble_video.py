#!/usr/bin/env python3
"""
Scenic montage assembler with soundtrack + ducking (MoviePy/Pillow-10 safe)

- Picks MP4 clips from the latest content/assets/broll/YYYY-MM-DD
- Skips 4K/UHD files by default (use --include-uhd to allow)
- Targets ~duration by concatenating random subclips
- Adds soundtrack from content/assets/music (trims or loops with crossfades)
- OPTIONAL: auto-duck music wherever a clip has native audio
- Loud, immediate logging so CI/local doesn't look "stuck"

Examples
  python scripts/assemble_video.py --target-seconds 60 --scan-limit 24 --max-clips 12 --logger bar
  python scripts/assemble_video.py --music content/assets/music --music-volume 0.18 --music-fade 2.0 --music-crossfade 1.0
  python scripts/assemble_video.py --duck-native-audio --duck-dB 12 --duck-attack 0.2 --duck-release 0.6
"""
from __future__ import annotations
import argparse
import glob
import os
import pathlib
import random
import sys
import tempfile
import time
from typing import List, Tuple

VERSION = "assemble-video v2025.08.24"

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

# Lazy MoviePy import so we can print early diagnostics
VideoFileClip = AudioFileClip = concatenate_videoclips = ColorClip = concatenate_audioclips = None  # set by ensure_moviepy()

def ensure_moviepy():
    global VideoFileClip, AudioFileClip, concatenate_videoclips, ColorClip, concatenate_audioclips
    if VideoFileClip is None:
        print("[assemble] Loading MoviePy …", flush=True)
        from moviepy.editor import (
            VideoFileClip as _V, AudioFileClip as _A, concatenate_videoclips as _CV,
            ColorClip as _Col, concatenate_audioclips as _CA
        )
        VideoFileClip, AudioFileClip, concatenate_videoclips, ColorClip, concatenate_audioclips = _V, _A, _CV, _Col, _CA
        print("[assemble] MoviePy loaded", flush=True)


# ------------------------------ CLI ------------------------------------- #

def build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Assemble a scenic montage from downloaded b-roll")
    p.add_argument("--broll-dir", type=pathlib.Path, default=None,
                   help="Folder with mp4 clips (default: latest under content/assets/broll)")
    p.add_argument("--output", type=pathlib.Path, default=pathlib.Path("content/uploads/scenic_montage_001.mp4"))

    # selection/timing
    p.add_argument("--target-seconds", type=int, default=90, help="Target montage length in seconds")
    p.add_argument("--min-clip-seconds", type=float, default=5.0)
    p.add_argument("--max-clip-seconds", type=float, default=8.0)
    p.add_argument("--max-clips", type=int, default=20, help="Upper bound on clips to concatenate")
    p.add_argument("--scan-limit", type=int, default=40, help="Max number of files to probe before enough clips")

    # output quality/speed
    p.add_argument("--max-height", type=int, default=1080, help="Resize each subclip to this height (reduces memory)")
    p.add_argument("--fps", type=int, default=30, help="Output frames per second")
    p.add_argument("--threads", type=int, default=2, help="FFmpeg threads for writing")

    # music
    p.add_argument("--music", dest="music_dir", type=pathlib.Path, default=pathlib.Path("content/assets/music"),
                   help="Folder with music files (mp3/wav/m4a/flac). If empty, video will be silent.")
    p.add_argument("--music-volume", type=float, default=0.15, help="Music gain multiplier (0.0–1.0). Default 0.15")
    p.add_argument("--music-fade", type=float, default=1.5, help="Fade in/out seconds for soundtrack")
    p.add_argument("--music-crossfade", type=float, default=1.0, help="Crossfade seconds when looping track")
    p.add_argument("--no-music", action="store_true", help="Disable soundtrack even if files exist")

    # ducking
    p.add_argument("--duck-native-audio", action="store_true", help="Lower music when a subclip has native audio")
    p.add_argument("--duck-dB", type=float, default=12.0, help="How much to duck the music (dB)")
    p.add_argument("--duck-attack", type=float, default=0.25, help="Ducking attack seconds")
    p.add_argument("--duck-release", type=float, default=0.6, help="Ducking release seconds")

    # misc
    p.add_argument("--logger", choices=["bar", "verbose", "none"], default="bar", help="Progress logger: bar|verbose|none")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--skip-uhd", dest="skip_uhd", action="store_true", help="Skip 4K/uhd files by filename (default)")
    g.add_argument("--include-uhd", dest="skip_uhd", action="store_false", help="Allow 4K/uhd files in the scan")
    p.set_defaults(skip_uhd=True)

    p.add_argument("--dry-run", action="store_true", help="List candidate files and exit (no MoviePy)")
    p.add_argument("--diagnose", action="store_true", help="Print environment and ffmpeg info then continue")
    p.add_argument("--selftest", action="store_true", help="Generate small synthetic clips and run pipeline")
    return p.parse_args()


# ---------------------------- File helpers ------------------------------- #

def latest_broll_dir(root: pathlib.Path) -> pathlib.Path | None:
    candidates = [p for p in root.glob("*") if p.is_dir()]
    if not candidates:
        return None
    def key(p: pathlib.Path):
        try:
            from datetime import datetime
            return datetime.strptime(p.name, "%Y-%m-%d")
        except Exception:
            return p.stat().st_mtime
    return max(candidates, key=key)


def is_uhd_filename(name: str) -> bool:
    n = name.lower()
    return any(tok in n for tok in ["3840_2160", "4096_", "uhd"])  # simple heuristic


# ---------------------------- Music helpers ------------------------------ #

def list_music_files(music_dir: pathlib.Path) -> List[pathlib.Path]:
    if not music_dir or not music_dir.exists():
        return []
    exts = ("*.mp3", "*.wav", "*.m4a", "*.flac", "*.aac")
    files: List[pathlib.Path] = []
    for pat in exts:
        files.extend(music_dir.glob(pat))
    return files


def build_soundtrack(duration: float, args) :
    """Return an AudioClip matching duration, or None if no music available.
    Strategy: pick one random file; if longer → random trim; if shorter → loop with crossfades.
    """
    ensure_moviepy()
    files = list_music_files(args.music_dir)
    if args.no_music or not files:
        return None
    src = random.choice(files)
    try:
        base = AudioFileClip(str(src))
        fade = max(0.0, float(args.music_fade))
        xfade = max(0.0, float(args.music_crossfade))
        vol = max(0.0, float(args.music_volume))

        if base.duration >= duration + 0.25:
            start = random.uniform(0, max(0.0, base.duration - duration - 0.25))
            song = base.subclip(start, start + duration)
        else:
            pieces = []
            t = 0.0
            while t < duration + 0.1:
                end = min(base.duration, duration - t + xfade)
                part = base.subclip(0, end)
                if pieces and xfade > 0:
                    last = pieces[-1].audio_fadeout(xfade)
                    pieces[-1] = last
                pieces.append(part)
                t += (end - (xfade if xfade > 0 else 0))
            song = concatenate_audioclips(pieces).subclip(0, duration)

        if fade > 0:
            song = song.audio_fadein(fade).audio_fadeout(fade)
        if vol != 1.0:
            song = song.volumex(vol)
        return song
    except Exception as e:
        print("[assemble] Music load error:", src.name, "=>", e, flush=True)
        return None


# ------------------------ Ducking helpers -------------------------------- #

def clip_has_audio_stream(path: str) -> bool:
    ensure_moviepy()
    c = None
    try:
        c = VideoFileClip(path, audio=True)
        return (getattr(c, "audio", None) is not None) and (float(getattr(c.audio, "duration", 0) or 0) > 0.05)
    except Exception:
        return False
    finally:
        try:
            if c is not None:
                c.close()
        except Exception:
            pass


def _db_to_lin(db: float) -> float:
    return 10 ** (-float(db) / 20.0)


def make_duck_envelope(intervals: List[Tuple[float,float]], duck_db: float, attack: float, release: float):
    low = _db_to_lin(duck_db)
    def env(t: float) -> float:
        v = 1.0
        for s, e in intervals:
            if t < s - attack or t > e + release:
                continue
            if s - attack <= t < s:       # ramp down
                v = min(v, low + (1 - low) * ((s - t) / attack))
            elif e < t <= e + release:    # ramp up
                v = min(v, low + (1 - low) * ((t - e) / release))
            else:                          # fully ducked
                v = min(v, low)
        return v
    return env


# ---------------------------- Clip logic --------------------------------- #

def safe_subclip(path: str, args: argparse.Namespace):
    ensure_moviepy()
    clip = None
    try:
        clip = VideoFileClip(path, audio=False)
        dur = float(getattr(clip, "duration", 0) or 0)
        if dur < args.min_clip_seconds + 0.5:
            return None
        start = random.uniform(0, max(0.0, dur - args.max_clip_seconds - 0.1))
        end = min(dur, start + random.uniform(args.min_clip_seconds, args.max_clip_seconds))
        sub = clip.subclip(start, end)
        if args.max_height and getattr(sub, "h", 0) > args.max_height:
            sub = sub.resize(height=args.max_height)
        return sub
    except Exception as e:
        print("  ↳ Skip:", os.path.basename(path), "=>", e, flush=True)
        try:
            if clip is not None:
                clip.reader.close()
                if getattr(clip, "audio", None):
                    clip.audio.reader.close_proc()
        except Exception:
            pass
        return None


def write_video(selected, args: argparse.Namespace, duck_intervals: List[Tuple[float,float]]):
    ensure_moviepy()
    total_dur = sum(float(c.duration or 0) for c in selected)
    print(f"[assemble] Concatenating {len(selected)} clips (~{total_dur:.1f}s)…", flush=True)
    t0 = time.time()
    video = concatenate_videoclips(selected, method="compose")
    print(f"[assemble] Concatenated in {time.time()-t0:.1f}s. Writing to {args.output} …", flush=True)

    # Optional music soundtrack
    music_clip = None
    try:
        music_clip = build_soundtrack(video.duration, args)
        if music_clip is not None:
            if args.duck_native_audio and duck_intervals:
                print(f"[assemble] Ducking music over {len(duck_intervals)} interval(s)…", flush=True)
                env = make_duck_envelope(
                    duck_intervals,
                    duck_db=float(args.duck_dB),
                    attack=float(args.duck_attack),
                    release=float(args.duck_release),
                )
                music_clip = music_clip.volumex(lambda t: env(t))
            video = video.set_audio(music_clip.set_duration(video.duration))
            print("[assemble] Added soundtrack:", args.music_dir, flush=True)
        else:
            print("[assemble] No soundtrack added (none found or disabled).", flush=True)
    except Exception as e:
        print("[assemble] Soundtrack error:", e, flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    logger = None if args.logger == "none" else args.logger
    video.write_videofile(
        str(args.output),
        codec="libx264",
        audio_codec="aac",
        fps=args.fps,
        threads=max(1, int(args.threads)),
        preset="veryfast",
        remove_temp=True,
        ffmpeg_params=["-movflags", "faststart"],
        logger=logger,
    )
    print("[assemble] Saved", args.output, flush=True)


# ----------------------------- Main -------------------------------------- #

def main():
    args = build_args()

    # Startup banner before any heavy imports
    print(VERSION, flush=True)
    print(f"[assemble] Python: {sys.version.splitlines()[0]}", flush=True)
    print(f"[assemble] CWD: {os.getcwd()}", flush=True)

    if args.diagnose:
        try:
            import imageio_ffmpeg
            print(f"[assemble] ffmpeg: {imageio_ffmpeg.get_ffmpeg_exe()}", flush=True)
        except Exception as e:
            print(f"[assemble] ffmpeg path detection error: {e}", flush=True)

    # Find b-roll directory
    broll_root = pathlib.Path("content/assets/broll")
    broll_dir = args.broll_dir or latest_broll_dir(broll_root)
    print(f"[assemble] broll_dir: {broll_dir}", flush=True)
    if not broll_dir or not broll_dir.exists():
        raise SystemExit("No b-roll directory found. Run scripts/fetch_assets.py first.")

    # Gather candidate files
    mp4s = sorted(glob.glob(str(broll_dir / "*.mp4")))
    print(f"[assemble] found {len(mp4s)} mp4 files", flush=True)
    if not mp4s:
        raise SystemExit(f"No mp4 files found in {broll_dir}")

    # Filter out UHD unless overridden
    filtered = [p for p in mp4s if (not args.skip_uhd) or (not is_uhd_filename(os.path.basename(p)))]
    if not filtered:
        filtered = mp4s  # fall back if our heuristic removed everything

    if args.dry_run:
        print("[assemble] DRY RUN — first 20 files:")
        for i, p in enumerate(filtered[:20], 1):
            print(f"  {i:02d}. {os.path.basename(p)}")
        return

    random.shuffle(filtered)

    scan_total = min(args.scan_limit, len(filtered))
    print(f"[assemble] Scanning up to {scan_total} of {len(filtered)} files in {broll_dir} …", flush=True)

    selected = []
    duck_intervals: List[Tuple[float,float]] = []
    total = 0.0
    scanned = 0
    for path in filtered:
        if scanned >= args.scan_limit:
            print("[assemble] Reached scan limit; proceeding with what we have…", flush=True)
            break
        if len(selected) >= args.max_clips or total >= args.target_seconds:
            break
        scanned += 1
        print(f"[assemble] [{scanned}/{scan_total}] Probing: {os.path.basename(path)}", flush=True)
        sub = safe_subclip(path, args)
        if sub is None:
            continue
        selected.append(sub)
        if args.duck_native_audio and clip_has_audio_stream(path):
            duck_intervals.append((total, total + float(sub.duration or 0)))
        total += float(sub.duration or 0)

    if not selected:
        raise SystemExit("No suitable clips to assemble.")

    try:
        write_video(selected, args, duck_intervals)
    finally:
        # Close all subclips to free memory
        for c in selected:
            try:
                c.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
