#!/usr/bin/env python3
"""
Generate YouTube metadata JSON for the kitesurf montage.

- Finds the latest content/assets/broll/YYYY-MM-DD folder.
- Pulls creator credits from credits.txt if present.
- Produces content/uploads/metadata_001.json with title_en/title/description/tags.
- Defaults are safe; override with CLI flags.

Usage:
  python scripts/generate_metadata.py
  python scripts/generate_metadata.py --title "Downwind Dream" --lang en --category 19
  python scripts/generate_metadata.py --broll-dir content/assets/broll/2025-08-22 --output content/uploads/metadata_001.json
  python scripts/generate_metadata.py --selftest
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import pathlib
import random
from typing import List, Optional

BROLL_ROOT = pathlib.Path("content/assets/broll")
DEFAULT_OUT = pathlib.Path("content/uploads/metadata_001.json")

DEFAULT_TAGS = [
    "kitesurf", "kiteboarding", "kitesurfing", "drone", "fpv", "aerial",
    "ocean", "beach", "waves", "sunset", "travel", "adventure",
    "4k", "1080p", "relaxing", "montage", "scenic", "nature",
]

TITLE_TEMPLATES = [
    "Kitesurf Drone Views — {date}",
    "Scenic Kitesurfing Aerials — {date}",
    "Kiteboarding Ocean Montage — {date}",
]


def latest_broll_dir(root: pathlib.Path) -> Optional[pathlib.Path]:
    if not root.exists():
        return None
    candidates = [p for p in root.iterdir() if p.is_dir()]
    if not candidates:
        return None
    # Expect YYYY-MM-DD format; fall back to mtime
    def key(p: pathlib.Path):
        try:
            return dt.datetime.strptime(p.name, "%Y-%m-%d")
        except Exception:
            return dt.datetime.fromtimestamp(p.stat().st_mtime)
    return max(candidates, key=key)


def read_credits(folder: pathlib.Path) -> List[str]:
    f = folder / "credits.txt"
    if f.exists():
        try:
            lines = [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
            # dedupe, keep order
            seen = set()
            out = []
            for x in lines:
                if x not in seen:
                    out.append(x)
                    seen.add(x)
            return out
        except Exception:
            return []
    return []


def natural_date(d: dt.date) -> str:
    return d.strftime("%B %d, %Y")


def build_title(date_str: str) -> str:
    tmpl = random.choice(TITLE_TEMPLATES)
    return tmpl.format(date=date_str)


def build_description(date_str: str, credits: List[str]) -> str:
    parts = []
    parts.append("Scenic kitesurfing drone montage — relaxing ocean vibes and coastal aerials.\n")
    parts.append(f"Shot date: {date_str}\n")
    parts.append("\n")
    parts.append("🎧 Music: Ambient/chill licensed for use in this video.\n")
    if credits:
        parts.append("\n🎥 Clips courtesy of creators on Pexels/Pixabay — thanks to:\n")
        for c in credits:
            parts.append(f"• {c}\n")
    parts.append("\n#kitesurf #kiteboarding #drone #ocean #beach #sunset #aerial #travel\n")
    return "".join(parts)


def write_json(out_path: pathlib.Path, payload: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate YouTube metadata JSON for montage")
    p.add_argument("--broll-dir", type=pathlib.Path, default=None, help="Folder like content/assets/broll/YYYY-MM-DD (default: latest)")
    p.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUT, help="Where to write metadata JSON")
    p.add_argument("--title", dest="title", default=None, help="Explicit title (overrides template)")
    p.add_argument("--title-en", dest="title_en", default=None, help="Explicit English title")
    p.add_argument("--lang", dest="lang", default="en", help="Default language code (default: en)")
    p.add_argument("--category", dest="category", default="19", help="YouTube categoryId (default: 19 Travel & Events)")
    p.add_argument("--tag", dest="tags", action="append", help="Add/override tags (can repeat)")
    p.add_argument("--selftest", action="store_true", help="Run a tiny self test and exit")
    return p.parse_args()


def selftest() -> None:
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="meta_"))
    # fake broll dir + credits
    bdir = tmp / "content/assets/broll/2025-08-22"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "credits.txt").write_text("Pexels • kiteboarding — Alice\nPixabay • sunset — Bob\nPexels • kiteboarding — Alice\n", encoding="utf-8")
    out = tmp / "content/uploads/metadata_001.json"
    args = argparse.Namespace(broll_dir=bdir, output=out, title=None, title_en=None, lang="en", category="19", tags=None)
    d = dt.date(2025, 8, 22)
    t = build_title(natural_date(d))
    desc = build_description(natural_date(d), read_credits(bdir))
    write_json(out, {
        "title": t,
        "title_en": t,
        "description": desc,
        "tags": DEFAULT_TAGS,
        "defaultLanguage": "en",
        "categoryId": "19",
        "madeForKids": False,
    })
    assert out.exists() and out.stat().st_size > 0, "metadata not written"
    print("[selftest] OK →", out)


def main() -> None:
    args = parse_args()
    if args.selftest:
        selftest()
        return

    bdir = args.broll_dir or latest_broll_dir(BROLL_ROOT)
    if not bdir or not bdir.exists():
        raise SystemExit("No b-roll folder found under content/assets/broll. Run fetch_assets first.")

    # derive date from folder name if possible
    try:
        d = dt.datetime.strptime(bdir.name, "%Y-%m-%d").date()
    except Exception:
        d = dt.date.today()
    date_str = natural_date(d)

    title = args.title or build_title(date_str)
    title_en = args.title_en or title
    credits = read_credits(bdir)

    tags = DEFAULT_TAGS[:]
    if args.tags:
        # if user provided any --tag, prefer those (dedupe)
        extra = []
        seen = set()
        for t in args.tags:
            if t not in seen:
                extra.append(t)
                seen.add(t)
        tags = extra

    payload = {
        "title": title,
        "title_en": title_en,
        "description": build_description(date_str, credits),
        "tags": tags,
        "defaultLanguage": args.lang or "en",
        "categoryId": str(args.category or "19"),
        "madeForKids": False,
    }

    write_json(args.output, payload)
    print("Wrote", args.output)


if __name__ == "__main__":
    main()
