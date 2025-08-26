#!/usr/bin/env python3
"""
Scenic montage assembler with soundtrack + ducking
Compatible with MoviePy v1.x and v2.x (no editor facade)
"""
from __future__ import annotations
import argparse, glob, os, pathlib, random, sys, time
from typing import List, Tuple

VERSION = "assemble-video v2025.08.25"

# --- Pillow 10+ compatibility: remap deprecated constants so MoviePy v1 code won't crash
try:
    from PIL import Image as _PIL_Image
    if not hasattr(_PIL_Image, "ANTIALIAS"):
        from PIL import Image
        Image.ANTIALIAS = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
        Image.BICUBIC   = Image.Resampling.BICUBIC   # type: ignore[attr-defined]
        Image.BILINEAR  = Image.Resampling.BILINEAR  # type: ignore[attr-defined]
except Exception:
    pass

# Lazy MoviePy imports (filled by ensure_moviepy)
VideoFileClip = AudioFileClip = concatenate_videoclips = ColorClip = None
concatenate_audioclips = CompositeAudioClip = None
AudioLoop = AudioFadeIn = AudioFadeOut = MultiplyVolume = None

def ensure_moviepy():
    """
    Make this file run on MoviePy v2.x (preferred) and v1.x (fallback).
    """
    global VideoFileClip, AudioFileClip, concatenate_videoclips, ColorClip
    global concatenate_audioclips, CompositeAudioClip
    global AudioLoop, AudioFadeIn, AudioFadeOut, MultiplyVolume

    if VideoFileClip is not None:
        return

    print("[assemble] Loading MoviePy …", flush=True)

    # ---- Preferred: MoviePy v2.x root imports
    try:
        # Core classes/functions available from root in v2
        from moviepy import (
            VideoFileClip as _V, AudioFileClip as _A, ColorClip as _Col,
            concatenate_videoclips as _CV,
        )
        VideoFileClip, AudioFileClip, ColorClip, concatenate_videoclips = _V, _A, _Col, _CV

        # Audio helpers: root (if exported), otherwise module paths
        try:
            from moviepy import concatenate_audioclips as _CA
        except Exception:
            from moviepy.audio.AudioClip import concatenate_audioclips as _CA
        concatenate_audioclips = _CA

        try:
            from moviepy import CompositeAudioClip as _Comp
        except Exception:
            from moviepy.audio.AudioClip import CompositeAudioClip as _Comp
        CompositeAudioClip = _Comp

        # v2 effect classes (used via with_effects)
        try:
            from moviepy.audio.fx.AudioLoop import AudioLoop as _AL
        except Exception:
            _AL = None
        try:
            from moviepy.audio.fx.AudioFadeIn import AudioFadeIn as _AFI
            from moviepy.audio.fx.AudioFadeOut import AudioFadeOut as _AFO
        except Exception:
            _AFI = _AFO = None
        try:
            from moviepy.audio.fx.MultiplyVolume import MultiplyVolume as _MV
        except Exception:
            _MV = None

        AudioLoop, AudioFadeIn, AudioFadeOut, MultiplyVolume = _AL, _AFI, _AFO, _MV
        print("[assemble] MoviePy loaded (v2.x)", flush=True)
        return
    except Exception:
        pass

    # ---- Fallback: MoviePy v1.x editor facade
    try:
        from moviepy.editor import (
            VideoFileClip as _V, AudioFileClip as _A, ColorClip as _Col,
            concatenate_videoclips as _CV, concatenate_audioclips as _CA,
            CompositeAudioClip as _Comp,
        )
        VideoFileClip, AudioFileClip, ColorClip = _V, _A, _Col
        concatenate_videoclips, concatenate_audioclips, CompositeAudioClip = _CV, _CA, _Comp
        # v1 has function-style fx; we rely on methods like .audio_fadein and .volumex
        AudioLoop = AudioFadeIn = AudioFadeOut = MultiplyVolume = None
        print("[assemble] MoviePy loaded (v1.x editor)", flush=True)
        return
    except Exception as e:
        print("[assemble] FATAL: cannot import MoviePy primitives:", e, flush=True)
        raise

# ---------- small cross-version helpers ----------

def subclip_safe(clip, start, end):
    """v1: .subclip(); v2: .subclipped()"""
    try:
        return clip.subclip(start, end)          # v1.x
    except Exception:
        try:
            return clip.subclipped(start, end)   # v2.x
        except Exception as e:
            raise AttributeError("No subclip/subclipped on clip") from e

def set_audio_safe(video, audio):
    try:
        return video.set_audio(audio)           # v1
    except Exception:
        try:
            return video.with_audio(audio)      # v2
        except Exception:
            return video

def resize_safe(clip, height: int):
    if not height:
        return clip
    try:
        return clip.resize(height=height)       # v1
    except Exception:
        try:
            from moviepy.video.fx.Resize import Resize
            return clip.with_effects([Resize(height=height)])  # v2
        except Exception:
            return clip

def fade_audio_safe(clip, fade: float):
    if fade <= 0:
        return clip
    # v1 methods
    try:
        return clip.audio_fadein(fade).audio_fadeout(fade)
    except Exception:
        pass
    # v2 effects
    try:
        if AudioFadeIn and AudioFadeOut:
            return clip.with_effects([AudioFadeIn(fade), AudioFadeOut(fade)])
    except Exception:
        pass
    return clip

def apply_volume(clip, factor_or_intervals):
    """
    v1: volumex supports numbers and callables; v2: use MultiplyVolume on intervals.
    factor_or_intervals:
      - float -> uniform multiplier
      - list[(start,end,low_factor)] -> apply MultiplyVolume per interval (v2)
    """
    # uniform factor (both versions)
    if isinstance(factor_or_intervals, (int, float)):
        try:
            return clip.volumex(float(factor_or_intervals))  # v1
        except Exception:
            if MultiplyVolume:
                return clip.with_effects([MultiplyVolume(float(factor_or_intervals))])  # v2
            return clip

    # intervals (for ducking on v2)
    if isinstance(factor_or_intervals, list) and MultiplyVolume:
        effects = []
        for (s, e, low) in factor_or_intervals:
            effects.append(MultiplyVolume(low, start_time=float(s), end_time=float(e)))
        return clip.with_effects(effects)
    return clip

def loop_audio_safe(base, duration: float, crossfade: float):
    # v2 effect
    try:
        if AudioLoop:
            return base.with_effects([AudioLoop(duration=duration)])
    except Exception:
        pass
    # manual concat
    pieces = []
    t = 0.0
    while t < duration + 0.1:
        end = min(base.duration, duration - t + max(0.0, crossfade))
        part = subclip_safe(base, 0, end)
        if pieces and crossfade > 0:
            pieces[-1] = pieces[-1].audio_fadeout(crossfade)
        pieces.append(part)
        t += (end - (crossfade if crossfade > 0 else 0))
    if concatenate_audioclips:
        return concatenate_audioclips(pieces).subclip(0, duration)
    # emergency: stack with CompositeAudioClip (no crossfades)
    if CompositeAudioClip:
        acc, t = [], 0.0
        while t < duration + 0.1:
            acc.append(base.set_start(t))
            t += max(0.1, base.duration - 0.01)
        return CompositeAudioClip(acc).set_duration(duration)
    return subclip_safe(base, 0, min(base.duration, duration))

# -------------- CLI & utilities --------------

def build_args():
    p = argparse.ArgumentParser(description="Assemble a scenic montage from b-roll")
    p.add_argument("--broll-dir", type=pathlib.Path, default=None)
    p.add_argument("--output", type=pathlib.Path, default=pathlib.Path("content/uploads/scenic_montage_001.mp4"))
    p.add_argument("--target-seconds", type=int, default=90)
    p.add_argument("--min-clip-seconds", type=float, default=5.0)
    p.add_argument("--max-clip-seconds", type=float, default=8.0)
    p.add_argument("--max-clips", type=int, default=20)
    p.add_argument("--scan-limit", type=int, default=40)
    p.add_argument("--max-height", type=int, default=1080)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--threads", type=int, default=2)
    # music
    p.add_argument("--music", dest="music_dir", type=pathlib.Path, default=pathlib.Path("content/assets/music"))
    p.add_argument("--music-volume", type=float, default=0.15)
    p.add_argument("--music-fade", type=float, default=1.5)
    p.add_argument("--music-crossfade", type=float, default=1.0)
    p.add_argument("--no-music", action="store_true")
    # ducking
    p.add_argument("--duck-native-audio", action="store_true")
    p.add_argument("--duck-dB", type=float, default=12.0)
    p.add_argument("--duck-attack", type=float, default=0.25)
    p.add_argument("--duck-release", type=float, default=0.6)
    # misc
    p.add_argument("--logger", choices=["bar", "verbose", "none"], default="bar")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--skip-uhd", dest="skip_uhd", action="store_true")
    g.add_argument("--include-uhd", dest="skip_uhd", action="store_false")
    p.set_defaults(skip_uhd=True)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()

def latest_broll_dir(root: pathlib.Path) -> pathlib.Path | None:
    kids = [p for p in root.glob("*") if p.is_dir()]
    if not kids: return None
    def key(p: pathlib.Path):
        from datetime import datetime
        try: return datetime.strptime(p.name, "%Y-%m-%d")
        except Exception: return p.stat().st_mtime
    return max(kids, key=key)

def is_uhd_filename(name: str) -> bool:
    n = name.lower()
    return any(tok in n for tok in ["3840_2160", "4096_", "uhd"])

def list_music_files(music_dir: pathlib.Path) -> List[pathlib.Path]:
    if not music_dir or not music_dir.exists():
        return []
    files: List[pathlib.Path] = []
    for pat in ("*.mp3","*.wav","*.m4a","*.flac","*.aac","*.ogg"):
        files.extend(music_dir.glob(pat))
    return files

# -------------- music / ducking --------------

def build_soundtrack(duration: float, args):
    ensure_moviepy()
    files = list_music_files(args.music_dir)
    if args.no_music or not files:
        return None
    src = random.choice(files)
    try:
        base = AudioFileClip(str(src))
        fade = max(0.0, float(args.music_fade))
        xfade = max(0.0, float(args.music_crossfade))
        vol  = max(0.0, float(args.music_volume))

        # trim or loop
        if base.duration >= duration + 0.25:
            start = random.uniform(0, max(0.0, base.duration - duration - 0.25))
            song = subclip_safe(base, start, start + duration)
        else:
            song = loop_audio_safe(base, duration, xfade)

    except Exception as e:
        print("[assemble] Music load error:", src.name, "=>", e, flush=True)
        return None

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
            if c is not None: c.close()
        except Exception:
            pass

def _db_to_lin(db: float) -> float:
    return 10 ** (-float(db) / 20.0)


def safe_subclip(path: str, args):
    ensure_moviepy()
    clip = None
    try:
        clip = VideoFileClip(path, audio=False)
        dur = float(getattr(clip, "duration", 0) or 0)
        if dur < args.min_clip_seconds + 0.5:
            return None
        start_max = max(0.0, dur - args.max_clip_seconds - 0.1)
        start = random.uniform(0, start_max)
        end   = min(dur, start + random.uniform(args.min_clip_seconds, args.max_clip_seconds))
        sub = subclip_safe(clip, start, end)
        if args.max_height and getattr(sub, "h", 0) > args.max_height:
            sub = resize_safe(sub, args.max_height)
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


def write_video(selected, args, duck_intervals: List[Tuple[float,float]]):
    ensure_moviepy()
    total_dur = sum(float(c.duration or 0) for c in selected)
    print(f"[assemble] Concatenating {len(selected)} clips (~{total_dur:.1f}s)…", flush=True)
    t0 = time.time()
    video = concatenate_videoclips(selected, method="compose")
    print(f"[assemble] Concatenated in {time.time()-t0:.1f}s. Writing to {args.output} …", flush=True)

    # soundtrack
    music_clip = None
    try:
        music_clip = build_soundtrack(video.duration, args)
        if music_clip is not None:
            if args.duck_native_audio and duck_intervals:
                low = _db_to_lin(args.duck_dB)
                # v1: continuous envelope via volumex(lambda t: f(t))
                try:
                    def env(t: float) -> float:
                        v = 1.0
                        for s, e in duck_intervals:
                            if s <= t <= e:
                                v = min(v, low)
                        return v
                    music_clip = music_clip.volumex(lambda t: env(t))  # v1 path
                except Exception:
                    # v2: approximate ducking with MultiplyVolume in each interval
                    triples = [(s, e, float(low)) for (s, e) in duck_intervals]
                    music_clip = apply_volume(music_clip, triples)
                    print("[assemble] (v2) ducking applied as stepped intervals", flush=True)

            video = set_audio_safe(video, music_clip.set_duration(video.duration))
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

# -------------- main --------------

def main():
    args = build_args()
    print(VERSION, flush=True)
    print(f"[assemble] Python: {sys.version.splitlines()[0]}", flush=True)
    print(f"[assemble] CWD: {os.getcwd()}", flush=True)

    broll_root = pathlib.Path("content/assets/broll")
    broll_dir = args.broll_dir or latest_broll_dir(broll_root)
    print(f"[assemble] broll_dir: {broll_dir}", flush=True)
    if not broll_dir or not broll_dir.exists():
        raise SystemExit("No b-roll directory found. Run scripts/fetch_assets.py first.")

    mp4s = sorted(glob.glob(str(broll_dir / "*.mp4")))
    print(f"[assemble] found {len(mp4s)} mp4 files", flush=True)
    if not mp4s:
        raise SystemExit(f"No mp4 files found in {broll_dir}")

    # Skip UHD unless allowed
    filtered = [p for p in mp4s if (not args.skip_uhd) or (not is_uhd_filename(os.path.basename(p)))]
    if not filtered:
        filtered = mp4s

    if args.dry_run:
        print("[assemble] DRY RUN — first 20 files:")
        for i, p in enumerate(filtered[:20], 1):
            print(f"  {i:02d}. {os.path.basename(p)}")
        return

    random.shuffle(filtered)
    scan_total = min(args.scan_limit, len(filtered))
    print(f"[assemble] Scanning up to {scan_total} of {len(filtered)} files in {broll_dir} …", flush=True)

    selected, duck_intervals = [], []
    total = scanned = 0.0
    for path in filtered:
        if scanned >= args.scan_limit: break
        if len(selected) >= args.max_clips or total >= args.target_seconds: break
        scanned += 1
        print(f"[assemble] [{int(scanned)}/{scan_total}] Probing: {os.path.basename(path)}", flush=True)
        sub = safe_subclip(path, args)
        if sub is None: continue
        selected.append(sub)
        if args.duck_native_audio and clip_has_audio_stream(path):
            duck_intervals.append((total, total + float(sub.duration or 0)))
        total += float(sub.duration or 0)

    if not selected:
        raise SystemExit("No suitable clips to assemble.")

    try:
        write_video(selected, args, duck_intervals)
    finally:
        for c in selected:
            try: c.close()
            except Exception: pass

if __name__ == "__main__":
    main()
