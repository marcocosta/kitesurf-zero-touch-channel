
import os, glob, pathlib, random
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips

BROLL_DIR = max([p for p in pathlib.Path("content/assets/broll").glob("*") if p.is_dir()], default=None)
MUSIC_DIR = pathlib.Path("content/assets/music")
OUTPUT = pathlib.Path("content/uploads")
OUTPUT.mkdir(parents=True, exist_ok=True)
TARGET_LEN = 90  # seconds

if not BROLL_DIR:
    raise SystemExit("No b-roll directory found. Run scripts/fetch_assets.py first.")

clips = []
for path in glob.glob(str(BROLL_DIR / "*.mp4")):
    try:
        clip = VideoFileClip(path).without_audio()
        if clip.w < 1280 or clip.h < 720:
            clip.close()
            continue
        dur = clip.duration
        if dur < 6:
            clip.close()
            continue
        start = random.uniform(0, max(0, dur - 8))
        sub = clip.subclip(start, min(dur, start + random.uniform(6, 10)))
        sub = sub.resize(height=1080)
        clips.append(sub)
    except Exception as e:
        print("Skip", path, e)

random.shuffle(clips)

assembled = []
total = 0
for c in clips:
    if total >= TARGET_LEN:
        break
    assembled.append(c)
    total += c.duration

if not assembled:
    raise SystemExit("No suitable clips to assemble.")

final = concatenate_videoclips(assembled, method="compose")

music_files = list(MUSIC_DIR.glob("*.mp3"))
if music_files:
    try:
        from moviepy.editor import AudioFileClip
        music = AudioFileClip(str(random.choice(music_files))).volumex(0.15)
        final = final.set_audio(music.set_duration(final.duration))
    except Exception as e:
        print("Music load error:", e)

out_path = OUTPUT / "scenic_montage_001.mp4"
final.write_videofile(str(out_path), codec="libx264", audio_codec="aac", fps=30, threads=4)
print("Saved", out_path)
