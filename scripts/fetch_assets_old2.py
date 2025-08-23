#!/usr/bin/env python3
"""
Fetch CC0/CC-BY kitesurf/beach b‑roll from Pexels and save under
content/assets/broll/YYYY-MM-DD. Writes a newline‑separated credits.txt.

Usage:
  python scripts/fetch_assets.py
  python scripts/fetch_assets.py --date 2025-08-22 --query "kitesurf drone" --per-page 10
  python scripts/fetch_assets.py --selftest
"""
from __future__ import annotations
import os
import sys
import time
import json
import argparse
import pathlib
import requests
from typing import List, Dict

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

DEFAULT_QUERIES = [
    "kitesurf drone",
    "kiteboarding ocean",
    "beach dunes aerial",
    "sunset ocean drone",
]


def api_fetch_pexels(query: str, per_page: int = 20) -> List[Dict]:
    """Return a list of best-quality video file infos for the query."""
    if not PEXELS_API_KEY:
        print("[warn] PEXELS_API_KEY not set; skipping Pexels fetch for:", query)
        return []
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "per_page": per_page, "orientation": "landscape"}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    out: List[Dict] = []
    for v in data.get("videos", []):
        files = sorted(
            v.get("video_files", []),
            key=lambda f: (f.get("width", 0) or 0) * (f.get("height", 0) or 0),
            reverse=True,
        )
        if files:
            top = files[0]
            out.append(
                {
                    "src": top["link"],
                    "width": top.get("width", 0),
                    "height": top.get("height", 0),
                    "credit": v.get("user", {}).get("name", "Pexels Creator"),
                }
            )
    return out


def http_download(url: str, dest: pathlib.Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)


def save_credits(credits: List[str], outdir: pathlib.Path) -> pathlib.Path:
    path = outdir / "credits.txt"
    # ✅ FIXED: newline join and encoding specified
    path.write_text("\n".join(sorted(set(credits))), encoding="utf-8")
    return path


def run(date_str: str | None, queries: List[str], per_page: int) -> pathlib.Path:
    target_date = date_str or time.strftime("%Y-%m-%d")
    outdir = pathlib.Path("content/assets/broll") / target_date
    outdir.mkdir(parents=True, exist_ok=True)

    all_credits: List[str] = []
    for q in queries:
        try:
            for item in api_fetch_pexels(q, per_page=per_page):
                fname = q.replace(" ", "_") + "_" + os.path.basename(item["src"].split("?")[0])
                dest = outdir / fname
                http_download(item["src"], dest)
                all_credits.append(f"{q}: {item['credit']}")
                print("Downloaded:", dest)
        except Exception as e:
            print("[error]", q, e)

    credit_file = save_credits(all_credits, outdir)
    print("Credits written to:", credit_file)
    return outdir


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch CC0/CC-BY kitesurf b-roll from Pexels.")
    p.add_argument("--date", help="YYYY-MM-DD output subfolder (default: today)")
    p.add_argument("--query", action="append", help="Search query (can repeat; default uses presets)")
    p.add_argument("--per-page", type=int, default=15, help="Results per query (default 15)")
    p.add_argument("--selftest", action="store_true", help="Run basic functional tests and exit")
    return p.parse_args()


def selftest() -> None:
    import tempfile

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="fetch_assets_test_"))
    try:
        # Test credits writer with duplicates and unicode
        creds = ["q1: Alice", "q1: Alice", "q2: José", "q3: Bob"]
        save_credits(creds, tmp)
        txt = (tmp / "credits.txt").read_text(encoding="utf-8").splitlines()
        assert "q1: Alice" in txt and "q3: Bob" in txt and "q2: José" in txt, "credits content"
        assert len(txt) == 3, "credits should be deduplicated"
        print("[selftest] credits writer ok")

        # Smoke test API call path (skipped if no key)
        if not PEXELS_API_KEY:
            print("[selftest] no API key; skipping network smoke test.")
        else:
            results = api_fetch_pexels("kitesurf", per_page=1)
            assert isinstance(results, list), "api results type"
            print("[selftest] api fetch ok (", len(results), ")")
    finally:
        # leave tmp for inspection if you want; otherwise comment next line
        for f in tmp.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass
        try:
            tmp.rmdir()
        except Exception:
            pass


if __name__ == "__main__":
    args = parse_args()
    if args.selftest:
        selftest()
        sys.exit(0)

    queries = args.query or DEFAULT_QUERIES
    run(args.date, queries, args.per_page)
