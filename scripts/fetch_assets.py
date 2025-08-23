#!/usr/bin/env python3
"""
Fetch CC0/CC-BY kitesurf/beach b‑roll from Pexels and save under
content/assets/broll/YYYY-MM-DD. Writes a newline‑separated credits.txt.

If no API key is provided, the script exits with a helpful message.

Ways to provide your key (precedence order):
  1) CLI flag:          --pexels-key YOUR_KEY
  2) Env var:           PEXELS_API_KEY=YOUR_KEY
  3) config.yaml:       integrations.pexels_api_key

Usage examples:
  python scripts/fetch_assets.py
  python scripts/fetch_assets.py --query "kitesurf drone" --per-page 8
  python scripts/fetch_assets.py --pexels-key sk_live_... --date 2025-08-22
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
import yaml
from typing import List, Dict, Optional

DEFAULT_QUERIES = [
    "kitesurf drone",
    "kiteboarding ocean",
    "beach dunes aerial",
    "sunset ocean drone",
]

CONFIG_PATH_DEFAULT = pathlib.Path("config/config.yaml")


def read_config_key(config_path: pathlib.Path) -> Optional[str]:
    if not config_path.exists():
        return None
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        key = (
            data.get("integrations", {}).get("pexels_api_key")
            or data.get("PEXELS_API_KEY")
        )
        if key and str(key).strip() and str(key).strip().upper() != "YOUR_PEXELS_API_KEY":
            return str(key).strip()
    except Exception:
        pass
    return None


def resolve_pexels_key(cli_key: Optional[str], config_path: pathlib.Path) -> Optional[str]:
    # 1) CLI wins
    if cli_key and cli_key.strip():
        return cli_key.strip()
    # 2) ENV
    env_key = os.getenv("PEXELS_API_KEY", "").strip()
    if env_key:
        return env_key
    # 3) config.yaml
    return read_config_key(config_path)


def api_fetch_pexels(query: str, per_page: int, api_key: str) -> List[Dict]:
    """Return a list of best-quality video file infos for the query."""
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": api_key}
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
    path.write_text("\n".join(sorted(set(credits))), encoding="utf-8")
    return path


def run(date_str: Optional[str], queries: List[str], per_page: int, api_key: str) -> pathlib.Path:
    target_date = date_str or time.strftime("%Y-%m-%d")
    outdir = pathlib.Path("content/assets/broll") / target_date
    outdir.mkdir(parents=True, exist_ok=True)

    all_credits: List[str] = []
    for q in queries:
        try:
            for item in api_fetch_pexels(q, per_page=per_page, api_key=api_key):
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
    p.add_argument("--pexels-key", dest="pexels_key", help="API key (overrides env/config)")
    p.add_argument("--config", type=pathlib.Path, default=CONFIG_PATH_DEFAULT, help="Path to config.yaml with integrations.pexels_api_key")
    p.add_argument("--selftest", action="store_true", help="Run basic functional tests and exit")
    return p.parse_args()


def selftest() -> None:
    import tempfile

    # Test credits writer with duplicates and unicode
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="fetch_assets_test_"))
    try:
        creds = ["q1: Alice", "q1: Alice", "q2: José", "q3: Bob"]
        save_credits(creds, tmp)
        txt = (tmp / "credits.txt").read_text(encoding="utf-8").splitlines()
        assert "q1: Alice" in txt and "q3: Bob" in txt and "q2: José" in txt, "credits content"
        assert len(txt) == 3, "credits should be deduplicated"
        print("[selftest] credits writer ok")

        # Test key resolution precedence
        # 1) config only
        cfg = tmp / "config.yaml"
        cfg.write_text("integrations:\n  pexels_api_key: CFGKEY\n", encoding="utf-8")
        os.environ.pop("PEXELS_API_KEY", None)
        assert resolve_pexels_key(None, cfg) == "CFGKEY"
        # 2) env wins over config
        os.environ["PEXELS_API_KEY"] = "ENVKEY"
        assert resolve_pexels_key(None, cfg) == "ENVKEY"
        # 3) CLI wins over env
        assert resolve_pexels_key("CLIKEY", cfg) == "CLIKEY"
        print("[selftest] key precedence ok")

        print("[selftest] network call skipped (requires real API key)")
    finally:
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

    api_key = resolve_pexels_key(args.pexels_key, args.config)
    if not api_key:
        msg = (
            "\n[error] No PEXELS_API_KEY provided. Set it one of these ways:\n"
            "  1) CLI:     python scripts/fetch_assets.py --pexels-key YOUR_KEY\n"
            "  2) Env var: set PEXELS_API_KEY=YOUR_KEY (Windows CMD)\n"
            "             $env:PEXELS_API_KEY=\"YOUR_KEY\" (PowerShell)\n"
            "  3) Config:  put it under integrations.pexels_api_key in config/config.yaml\n"
        )
        print(msg)
        sys.exit(2)

    queries = args.query or DEFAULT_QUERIES
    run(args.date, queries, args.per_page, api_key)
