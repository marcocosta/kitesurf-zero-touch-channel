#!/usr/bin/env python3
"""
Generate an ambient, royalty‑free soundtrack (WAV/MP3) for your montage.

Zero external APIs — fully local synthesis with NumPy and FFmpeg (optional).
Produces a gently evolving pad + ocean noise + light arpeggio, perfect under
scenic kitesurf clips.

Outputs to: content/assets/music/

Examples (PowerShell/CMD)
  python scripts/generate_music.py --duration 75 --bpm 84 --key A --mode minor
  python scripts/generate_music.py --duration 60 --seed 42 --mp3

Requirements
  pip install numpy
  # MP3 (optional): FFmpeg in PATH, or install on CI (apt-get install ffmpeg)
"""
from __future__ import annotations
import argparse
import math
import os
import pathlib
import random
import shutil
import struct
import subprocess
import sys
import time
from typing import List, Tuple

try:
    import numpy as np
except Exception as e:
    raise SystemExit("NumPy is required. Install with: pip install numpy")

SR = 44100  # sample rate
MIX_HEADROOM = 0.9  # target peak (~ -1 dBFS)

OUTDIR = pathlib.Path("content/assets/music")
OUTDIR.mkdir(parents=True, exist_ok=True)


# ----------------------------- Music helpers ----------------------------- #

def midi_to_freq(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def build_scale(key: str, mode: str) -> List[int]:
    """Return MIDI degrees for the diatonic scale at octave 4 (C4=60)."""
    key = key.upper()
    KEYS = {"C": 60, "C#": 61, "DB": 61, "D": 62, "D#": 63, "EB": 63, "E": 64,
            "F": 65, "F#": 66, "GB": 66, "G": 67, "G#": 68, "AB": 68, "A": 69, "A#": 70, "BB": 70, "B": 71}
    root = KEYS.get(key)
    if root is None:
        raise SystemExit(f"Unknown key: {key}")
    if mode.lower().startswith("maj"):
        steps = [0,2,4,5,7,9,11]
    else:
        # natural minor
        steps = [0,2,3,5,7,8,10]
    return [root + s for s in steps]


def chord_from_degree(scale: List[int], deg: int, mode: str) -> List[int]:
    """Triad from degree (1..7) in the given scale/mode."""
    i = (deg - 1) % 7
    # triad: 1-3-5
    triad = [scale[i], scale[(i+2)%7], scale[(i+4)%7]]
    # adjust for harmonic functions in minor (make V major sometimes)
    if mode.lower().startswith("min") and deg == 5:
        triad[1] = triad[1] + 1  # raise third for major V
    return triad


# ----------------------------- Signal helpers ---------------------------- #

def lowpass_1pole(x: np.ndarray, cutoff_hz: float, sr: int = SR) -> np.ndarray:
    if cutoff_hz <= 0:
        return x
    # RC filter
    dt = 1.0 / sr
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    a = dt / (rc + dt)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = acc + a * (v - acc)
        y[i] = acc
    return y


def fade_io(x: np.ndarray, fade_in: float, fade_out: float, sr: int = SR) -> np.ndarray:
    n = x.shape[0]
    fi = int(max(0.0, fade_in) * sr)
    fo = int(max(0.0, fade_out) * sr)
    if fi > 0:
        x[:fi] *= np.linspace(0.0, 1.0, fi)
    if fo > 0:
        x[-fo:] *= np.linspace(1.0, 0.0, fo)
    return x


def normalize_stereo(L: np.ndarray, R: np.ndarray, peak: float = MIX_HEADROOM) -> Tuple[np.ndarray, np.ndarray]:
    m = max(1e-9, float(np.max(np.abs([L, R]))))
    g = min(1.0, float(peak) / m)
    return L * g, R * g


def to_int16_stereo(L: np.ndarray, R: np.ndarray) -> bytes:
    L = np.clip(L, -1.0, 1.0)
    R = np.clip(R, -1.0, 1.0)
    inter = np.empty((L.size + R.size,), dtype=np.int16)
    inter[0::2] = (L * 32767.0).astype(np.int16)
    inter[1::2] = (R * 32767.0).astype(np.int16)
    return inter.tobytes()


def write_wav(path: pathlib.Path, L: np.ndarray, R: np.ndarray, sr: int = SR) -> None:
    import wave
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sr)
        wf.writeframes(to_int16_stereo(L, R))


# ----------------------------- Music synthesis --------------------------- #

def synth_pad(duration: float, chords: List[List[int]], chord_dur: float = 4.0, xfade: float = 0.5) -> Tuple[np.ndarray, np.ndarray]:
    N = int(duration * SR)
    L = np.zeros(N, dtype=np.float32)
    R = np.zeros(N, dtype=np.float32)
    rng = np.random.default_rng()

    t = np.arange(0, chord_dur + xfade, 1/SR)
    for idx in range(int(math.ceil(duration / chord_dur))):
        ch = chords[idx % len(chords)]
        # detuned sines per chord tone
        seg = np.zeros_like(t, dtype=np.float32)
        for m in ch:
            f = midi_to_freq(m - 12)  # keep pads warm (one octave down)
            detunes = [0.0, -0.3, +0.3]  # cents
            for cents in detunes:
                fm = f * (2.0 ** (cents / 1200.0))
                # subtle vibrato
                vib = 0.2 + 0.1 * np.sin(2*np.pi*(0.08 + 0.02*rng.random())*t)
                seg += 0.3 * np.sin(2*np.pi*fm*(t + vib*1e-3)).astype(np.float32)
        # gentle lowpass
        seg = lowpass_1pole(seg, 2200)
        # xfade at ends
        if xfade > 0:
            xf = int(xfade * SR)
            win = np.ones_like(seg)
            win[:xf] *= np.linspace(0.0, 1.0, xf)
            win[-xf:] *= np.linspace(1.0, 0.0, xf)
            seg *= win
        # place into track with slight stereo width
        start = int(idx * chord_dur * SR)
        end = min(N, start + seg.size)
        if start >= N:
            break
        seg = seg[:end-start]
        width = 0.12
        L[start:end] += seg * (1.0 - width)
        R[start:end] += seg * (1.0 + width)
    return L, R


def synth_ocean(duration: float) -> Tuple[np.ndarray, np.ndarray]:
    N = int(duration * SR)
    rng = np.random.default_rng()
    white = rng.normal(0, 1, N).astype(np.float32)
    # Pinkish by lowpassing and gentle tilt
    ocean = lowpass_1pole(white, 400)
    # slow amplitude undulation (waves)
    t = np.arange(N) / SR
    amp = 0.18 * (0.6 + 0.4*np.sin(2*np.pi*0.08*t) + 0.3*np.sin(2*np.pi*0.093*t + 1.3))
    L = ocean * (amp * 0.9)
    R = ocean * (amp * 1.1)
    return L, R


def synth_arp(duration: float, chords: List[List[int]], bpm: float) -> Tuple[np.ndarray, np.ndarray]:
    N = int(duration * SR)
    L = np.zeros(N, dtype=np.float32)
    R = np.zeros(N, dtype=np.float32)
    step = 60.0 / bpm / 2.0  # eighth notes
    t_step = np.arange(0, 0.6, 1/SR)  # pluck length ~600ms
    env = np.exp(-t_step/0.25).astype(np.float32)
    pan_lfo = np.sin(2*np.pi*0.02*np.arange(N)/SR).astype(np.float32)

    idx = 0
    t = 0.0
    while t < duration:
        ch = chords[idx % len(chords)]
        note = random.choice(ch)
        f = midi_to_freq(note + 12)  # brighter octave
        pl = 0.12 * np.sin(2*np.pi*f*t_step).astype(np.float32) * env
        start = int(t * SR)
        end = min(N, start + pl.size)
        if start >= N:
            break
        p = pan_lfo[start:end]
        L[start:end] += pl[:end-start] * (0.6 - 0.4*p)
        R[start:end] += pl[:end-start] * (0.6 + 0.4*p)
        t += step
        idx += 1
    return L, R


# ----------------------------- Main generator ---------------------------- #

def generate_track(duration: float, bpm: float, key: str, mode: str, fade: float, seed: int | None) -> Tuple[np.ndarray, np.ndarray]:
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
    scale = build_scale(key, mode)
    prog_degrees = [1, 6, 7, 4] if mode.lower().startswith('min') else [1, 5, 6, 4]
    chords = [chord_from_degree(scale, d, mode) for d in prog_degrees]

    Lp, Rp = synth_pad(duration, chords)
    Lo, Ro = synth_ocean(duration)
    La, Ra = synth_arp(duration, chords, bpm)

    L = Lp + Lo + La
    R = Rp + Ro + Ra

    # fades + normalize
    fade_io(L, fade, fade)
    fade_io(R, fade, fade)
    L, R = normalize_stereo(L, R, MIX_HEADROOM)
    return L.astype(np.float32), R.astype(np.float32)


def encode_mp3(wav_path: pathlib.Path) -> pathlib.Path | None:
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    mp3_path = wav_path.with_suffix('.mp3')
    cmd = [ff, '-y', '-hide_banner', '-loglevel', 'warning', '-i', str(wav_path), '-codec:a', 'libmp3lame', '-q:a', '2', str(mp3_path)]
    try:
        subprocess.check_call(cmd)
        return mp3_path
    except subprocess.CalledProcessError:
        return None


def parse_args():
    p = argparse.ArgumentParser(description="Generate a royalty-free ambient soundtrack (WAV/MP3)")
    p.add_argument('--duration', type=float, default=75.0, help='Seconds (default 75)')
    p.add_argument('--bpm', type=float, default=84.0, help='Arpeggio tempo (default 84)')
    p.add_argument('--key', type=str, default='A', help='Musical key (A, C#, Eb, etc.)')
    p.add_argument('--mode', type=str, default='minor', choices=['major','minor'], help='Scale mode (default minor)')
    p.add_argument('--fade', type=float, default=2.0, help='Fade in/out seconds (default 2.0)')
    p.add_argument('--seed', type=int, default=None, help='Random seed for reproducible results')
    p.add_argument('--mp3', action='store_true', help='Also export MP3 if ffmpeg is available')
    return p.parse_args()


def main():
    args = parse_args()
    start = time.time()
    L, R = generate_track(args.duration, args.bpm, args.key, args.mode, args.fade, args.seed)

    ts = time.strftime('%Y%m%d_%H%M%S')
    base = f"generated_{ts}_dur{int(args.duration)}s_{args.key}{'m' if args.mode=='minor' else ''}_bpm{int(args.bpm)}"
    wav_path = OUTDIR / f"{base}.wav"
    write_wav(wav_path, L, R, SR)
    print("[music] Wrote", wav_path)

    if args.mp3:
        mp3 = encode_mp3(wav_path)
        if mp3:
            print("[music] Wrote", mp3)
        else:
            print("[music] FFmpeg not found — skipping MP3 encode (WAV is fine)")

    print(f"[music] Done in {time.time()-start:.1f}s")


if __name__ == '__main__':
    main()
