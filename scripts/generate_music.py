#!/usr/bin/env python3
"""
Generate an ambient, royalty‑free soundtrack (WAV/MP3) for your montage.

All‑local synthesis with NumPy — no external APIs. Creates a gently evolving pad
+ ocean noise + optional arpeggio + optional soft percussion. Perfect under
scenic kitesurf clips.

Outputs to: content/assets/music/

Examples
  python scripts/generate_music.py --duration 75 --bpm 84 --key A --mode minor --mp3
  python scripts/generate_music.py --duration 60 --seed 42 --no-arp --percussion-level 0.25 --bright 0.35 --mp3

Requirements
  pip install numpy
  # MP3 (optional): FFmpeg in PATH (or install via apt-get on CI)
"""
from __future__ import annotations
import argparse
import math
import pathlib
import random
import shutil
import subprocess
import time
from typing import List, Tuple

try:
    import numpy as np
except Exception:
    raise SystemExit("NumPy is required. Install with: pip install numpy")

SR_DEFAULT = 44100  # sample rate
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
    triad = [scale[i], scale[(i+2)%7], scale[(i+4)%7]]
    if mode.lower().startswith("min") and deg == 5:
        triad[1] = triad[1] + 1  # raise third for major V in minor
    return triad


# ----------------------------- Signal helpers ---------------------------- #

def one_pole_lowpass(x: np.ndarray, cutoff_hz: float, sr: int) -> np.ndarray:
    if cutoff_hz <= 0:
        return x
    dt = 1.0 / sr
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    a = dt / (rc + dt)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = acc + a * (v - acc)
        y[i] = acc
    return y


def fade_io(x: np.ndarray, fade_in: float, fade_out: float, sr: int) -> np.ndarray:
    n = x.shape[0]
    fi = int(max(0.0, fade_in) * sr)
    fo = int(max(0.0, fade_out) * sr)
    if fi > 0:
        x[:fi] *= np.linspace(0.0, 1.0, fi, dtype=np.float32)
    if fo > 0:
        x[-fo:] *= np.linspace(1.0, 0.0, fo, dtype=np.float32)
    return x


def normalize_stereo(L: np.ndarray, R: np.ndarray, peak: float = MIX_HEADROOM) -> Tuple[np.ndarray, np.ndarray]:
    m = max(1e-9, float(np.max(np.abs([L, R]))))
    g = min(1.0, float(peak) / m)
    return L * g, R * g


def to_int16_interleaved(L: np.ndarray, R: np.ndarray) -> bytes:
    L = np.clip(L, -1.0, 1.0)
    R = np.clip(R, -1.0, 1.0)
    inter = np.empty((L.size + R.size,), dtype=np.int16)
    inter[0::2] = (L * 32767.0).astype(np.int16)
    inter[1::2] = (R * 32767.0).astype(np.int16)
    return inter.tobytes()


def write_wav(path: pathlib.Path, L: np.ndarray, R: np.ndarray, sr: int) -> None:
    import wave
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sr)
        wf.writeframes(to_int16_interleaved(L, R))


def tilt_brightness(x: np.ndarray, amount: float, sr: int) -> np.ndarray:
    """Simple high-shelf style tilt: add a high-passed copy scaled by amount (0..1)."""
    amount = max(0.0, min(1.0, amount))
    if amount <= 1e-6:
        return x
    hp = x - one_pole_lowpass(x, cutoff_hz=1200.0, sr=sr)
    return x + amount * hp


# ----------------------------- Music synthesis --------------------------- #

def synth_pad(duration: float, chords: List[List[int]], sr: int, chord_dur: float = 4.0, xfade: float = 0.5, bright: float = 0.4) -> Tuple[np.ndarray, np.ndarray]:
    N = int(duration * sr)
    L = np.zeros(N, dtype=np.float32)
    R = np.zeros(N, dtype=np.float32)
    rng = np.random.default_rng()

    t = np.arange(0, chord_dur + xfade, 1/sr)
    for idx in range(int(math.ceil(duration / chord_dur))):
        ch = chords[idx % len(chords)]
        seg = np.zeros_like(t, dtype=np.float32)
        for m in ch:
            f = midi_to_freq(m - 12)  # one octave down for warmth
            detunes = [0.0, -0.3, +0.3]  # cents
            for cents in detunes:
                fm = f * (2.0 ** (cents / 1200.0))
                # subtle LFO time wobble
                vib = 0.2 + 0.1 * np.sin(2*np.pi*(0.08 + 0.02*rng.random())*t)
                seg += 0.28 * np.sin(2*np.pi*fm*(t + vib*1e-3)).astype(np.float32)
        # gentle tone shaping
        seg = one_pole_lowpass(seg, 2200, sr)
        seg = tilt_brightness(seg, bright, sr)
        # xfade ends
        if xfade > 0:
            xf = int(xfade * sr)
            win = np.ones_like(seg)
            win[:xf] *= np.linspace(0.0, 1.0, xf, dtype=np.float32)
            win[-xf:] *= np.linspace(1.0, 0.0, xf, dtype=np.float32)
            seg *= win
        # place with slight stereo width
        start = int(idx * chord_dur * sr)
        end = min(N, start + seg.size)
        if start >= N:
            break
        seg = seg[:end-start]
        width = 0.12
        L[start:end] += seg * (1.0 - width)
        R[start:end] += seg * (1.0 + width)
    return L, R


def synth_ocean(duration: float, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    N = int(duration * sr)
    rng = np.random.default_rng()
    white = rng.normal(0, 1, N).astype(np.float32)
    ocean = one_pole_lowpass(white, 400, sr)
    t = np.arange(N) / sr
    amp = 0.18 * (0.6 + 0.4*np.sin(2*np.pi*0.08*t) + 0.3*np.sin(2*np.pi*0.093*t + 1.3))
    L = ocean * (amp * 0.9)
    R = ocean * (amp * 1.1)
    return L, R


def synth_arp(duration: float, chords: List[List[int]], bpm: float, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    N = int(duration * sr)
    L = np.zeros(N, dtype=np.float32)
    R = np.zeros(N, dtype=np.float32)
    step = 60.0 / bpm / 2.0  # eighth notes
    t_step = np.arange(0, 0.6, 1/sr)  # pluck length ~600ms
    env = np.exp(-t_step/0.25).astype(np.float32)
    pan_lfo = np.sin(2*np.pi*0.02*np.arange(N)/sr).astype(np.float32)

    idx = 0
    tcur = 0.0
    while tcur < duration:
        ch = chords[idx % len(chords)]
        note = random.choice(ch)
        f = midi_to_freq(note + 12)  # brighter octave
        pl = 0.12 * np.sin(2*np.pi*f*t_step).astype(np.float32) * env
        start = int(tcur * sr)
        end = min(N, start + pl.size)
        if start >= N:
            break
        p = pan_lfo[start:end]
        L[start:end] += pl[:end-start] * (0.6 - 0.4*p)
        R[start:end] += pl[:end-start] * (0.6 + 0.4*p)
        tcur += step
        idx += 1
    return L, R


def synth_percussion(duration: float, bpm: float, sr: int, level: float) -> Tuple[np.ndarray, np.ndarray]:
    """Very light kick/hat bed. Level is 0..1 for overall loudness."""
    if level <= 1e-6:
        N = int(duration * sr)
        return np.zeros(N, np.float32), np.zeros(N, np.float32)
    N = int(duration * sr)
    L = np.zeros(N, dtype=np.float32)
    R = np.zeros(N, dtype=np.float32)
    beat = 60.0 / bpm
    rng = np.random.default_rng()

    # Kick: low sine thump with fast decay every beat
    t_k = np.arange(0, 0.25, 1/sr)
    env_k = np.exp(-t_k/0.09).astype(np.float32)
    kick = 0.4 * np.sin(2*np.pi*50.0*t_k).astype(np.float32) * env_k

    # Hat: bright noise tick on off-beats
    t_h = np.arange(0, 0.08, 1/sr)
    hat = rng.normal(0, 1, t_h.size).astype(np.float32)
    hat = hat - one_pole_lowpass(hat, 8000, sr)  # crude highpass
    hat *= np.exp(-t_h/0.03).astype(np.float32) * 0.15

    t = 0.0
    n = 0
    while t < duration:
        # place kick on beats
        ks = int(t * sr)
        ke = min(N, ks + kick.size)
        if ks < N:
            L[ks:ke] += kick[:ke-ks]
            R[ks:ke] += kick[:ke-ks]
        # hat on "and" of each beat
        hs = int((t + beat/2.0) * sr)
        he = min(N, hs + hat.size)
        if hs < N:
            # light stereo spread
            L[hs:he] += hat[:he-hs] * 0.9
            R[hs:he] += hat[:he-hs] * 1.1
        t += beat
        n += 1

    # scale overall level softly
    L *= float(level)
    R *= float(level)
    return L, R


# ----------------------------- Main generator ---------------------------- #

def generate_track(duration: float, bpm: float, key: str, mode: str, fade: float, seed: int | None,
                   sr: int,
                   pad_level: float, ocean_level: float, arp_level: float, percussion_level: float,
                   no_arp: bool, bright: float) -> Tuple[np.ndarray, np.ndarray]:
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
    scale = build_scale(key, mode)
    prog_degrees = [1, 6, 7, 4] if mode.lower().startswith('min') else [1, 5, 6, 4]
    chords = [chord_from_degree(scale, d, mode) for d in prog_degrees]

    Lp, Rp = synth_pad(duration, chords, sr, bright=bright)
    Lo, Ro = synth_ocean(duration, sr)
    La, Ra = (np.zeros_like(Lp), np.zeros_like(Rp)) if no_arp else synth_arp(duration, chords, bpm, sr)
    Lperc, Rperc = synth_percussion(duration, bpm, sr, percussion_level)

    L = pad_level*Lp + ocean_level*Lo + arp_level*La + Lperc
    R = pad_level*Rp + ocean_level*Ro + arp_level*Ra + Rperc

    # fades + normalize
    fade_io(L, fade, fade, sr)
    fade_io(R, fade, fade, sr)
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
    p.add_argument('--bpm', type=float, default=84.0, help='Arpeggio/perc tempo (default 84)')
    p.add_argument('--key', type=str, default='A', help='Key (A, C#, Eb, etc.)')
    p.add_argument('--mode', type=str, default='minor', choices=['major','minor'], help='Mode (default minor)')
    p.add_argument('--fade', type=float, default=2.0, help='Fade in/out seconds (default 2.0)')
    p.add_argument('--seed', type=int, default=None, help='Random seed')
    p.add_argument('--sr', type=int, default=SR_DEFAULT, help='Sample rate (default 44100)')

    # Layer controls
    p.add_argument('--pad-level', type=float, default=1.0, help='Pad layer level (0..2)')
    p.add_argument('--ocean-level', type=float, default=1.0, help='Ocean noise level (0..2)')
    p.add_argument('--arp-level', type=float, default=0.35, help='Arpeggio level (0..2)')
    p.add_argument('--percussion-level', type=float, default=0.0, help='Percussion level (0..1)')
    p.add_argument('--no-arp', action='store_true', help='Disable arpeggio layer entirely')
    p.add_argument('--bright', type=float, default=0.4, help='Pad brightness tilt 0..1 (default 0.4)')

    p.add_argument('--mp3', action='store_true', help='Also export MP3 if ffmpeg is available')
    return p.parse_args()


def main():
    args = parse_args()
    start = time.time()
    L, R = generate_track(
        args.duration, args.bpm, args.key, args.mode, args.fade, args.seed,
        args.sr,
        args.pad_level, args.ocean_level, args.arp_level, args.percussion_level,
        args.no_arp, args.bright,
    )

    ts = time.strftime('%Y%m%d_%H%M%S')
    mode_tag = 'm' if args.mode == 'minor' else 'M'
    base = f"generated_{ts}_dur{int(args.duration)}s_{args.key}{mode_tag}_bpm{int(args.bpm)}"
    wav_path = OUTDIR / f"{base}.wav"
    write_wav(wav_path, L, R, args.sr)
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
