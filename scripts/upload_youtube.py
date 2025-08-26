#!/usr/bin/env python3
"""
YouTube uploader (OAuth desktop flow, resumable upload) — works locally and in CI.

Usage:
  python scripts/upload_youtube.py \
    --video content/uploads/scenic_montage_001.mp4 \
    --thumbnail content/uploads/thumbnail_001.jpg \
    --metadata content/uploads/metadata_001.json \
    --privacy unlisted

Requires:
  - Client secrets JSON at config/client_secret.json (or --client-secret path)
  - Tokens JSON will be stored at config/tokens.json (or --tokens path)
  - Packages: google-api-python-client, google-auth, google-auth-oauthlib

In CI, you can inject the JSON contents via env/Secrets and write the files
before running this script.
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import sys
import time
from typing import Any, Dict, List

# Google libraries
try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
except Exception as e:
    print("[uploader] Missing Google API packages. Install: \n  pip install google-api-python-client google-auth google-auth-oauthlib", file=sys.stderr)
    raise

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

DEFAULT_CLIENT = pathlib.Path("config/client_secret.json")
DEFAULT_TOKENS = pathlib.Path("config/tokens.json")

CATEGORY_MAP = {
    # YouTube categoryId mapping (EN)
    "film & animation": 1,
    "autos & vehicles": 2,
    "music": 10,
    "pets & animals": 15,
    "sports": 17,
    "short movies": 18,
    "travel & events": 19,
    "gaming": 20,
    "people & blogs": 22,
    "comedy": 23,
    "entertainment": 24,
    "news & politics": 25,
    "howto & style": 26,
    "education": 27,
    "science & technology": 28,
    "nonprofits & activism": 29,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Upload a video to YouTube with optional thumbnail and metadata")
    p.add_argument("--video", required=True, help="Path to the MP4 (or MOV/MKV…) video")
    p.add_argument("--thumbnail", help="Path to a JPG/PNG to use as custom thumbnail")
    p.add_argument("--metadata", help="Path to metadata JSON (title/description/tags/categoryId/language)")
    p.add_argument("--title", help="Override title")
    p.add_argument("--description", help="Override description")
    p.add_argument("--tags", help="Comma-separated tags override/addition")
    p.add_argument("--category", help="Category name or numeric ID (default: travel & events)")
    p.add_argument("--language", default="en", help="Default language code")
    p.add_argument("--privacy", choices=["public","private","unlisted"], default="unlisted")
    p.add_argument("--publish-at", dest="publish_at", help="RFC3339 time for scheduled publish (requires privacy=private)")
    p.add_argument("--client-secret", type=pathlib.Path, default=DEFAULT_CLIENT)
    p.add_argument("--tokens", type=pathlib.Path, default=DEFAULT_TOKENS)
    p.add_argument("--dry-run", action="store_true", help="Print request and exit")
    return p.parse_args()


def load_metadata(path: str | None) -> Dict[str, Any]:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[uploader] Warning: metadata load failed for {path}: {e}")
        return {}


def coalesce_title(md: Dict[str, Any], override: str | None) -> str:
    if override:
        return override
    for k in ("title_en", "title", "Title"):
        v = md.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return pathlib.Path(md.get("video_path", "untitled.mp4")).stem.replace("_", " ").title()


def parse_tags(md: Dict[str, Any], override: str | None) -> List[str]:
    tags: List[str] = []
    md_tags = md.get("tags")
    if isinstance(md_tags, list):
        tags.extend([str(t) for t in md_tags if str(t).strip()])
    if override:
        tags.extend([t.strip() for t in override.split(",") if t.strip()])
    # de-dupe, keep order
    seen = set()
    out = []
    for t in tags:
        if t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t)
    return out[:500]


def category_to_id(cat: str | None) -> int:
    if not cat:
        return 19  # Travel & Events default for scenic/kitesurf
    cat = str(cat).strip()
    if cat.isdigit():
        return int(cat)
    return CATEGORY_MAP.get(cat.lower(), 19)


def load_credentials(client_path: pathlib.Path, tokens_path: pathlib.Path) -> Credentials:
    creds: Credentials | None = None
    if tokens_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(tokens_path), SCOPES)
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not client_path.exists():
                raise FileNotFoundError(f"Client secrets not found at {client_path}. Create an OAuth client (Desktop) and save JSON.")
            flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for next time
        tokens_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tokens_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


def upload_video(youtube, video_path: str, body: Dict[str, Any]) -> str:
    media = MediaFileUpload(video_path, chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    print("[uploader] Starting resumable upload…")
    response = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status and hasattr(status, 'progress'):  # v1 style
                print(f"  progress: {int(status.progress() * 100)}%", flush=True)
            elif status and hasattr(status, 'resumable_progress'):  # v2 style
                # best-effort display
                print(f"  sent bytes: {getattr(status, 'resumable_progress', 0)}", flush=True)
        except HttpError as e:
            retry += 1
            if retry > 5:
                raise
            wait = min(2 ** retry, 30)
            print(f"[uploader] HttpError {e.status_code}; retrying in {wait}s… {e}")
            time.sleep(wait)
    video_id = response["id"]
    print("[uploader] Uploaded videoId:", video_id)
    return video_id


def set_thumbnail(youtube, video_id: str, path: str):
    if not path or not os.path.exists(path):
        print("[uploader] Thumbnail not found; skipping:", path)
        return
    try:
        media = MediaFileUpload(path, mimetype="image/jpeg")
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        print("[uploader] Thumbnail set ✔")
    except HttpError as e:
        print("[uploader] API error while setting thumbnail:", e)
        try:
            print(getattr(e, 'error_details', e.content))
        except Exception:
            pass


def main():
    args = parse_args()

    md = load_metadata(args.metadata)

    title = coalesce_title(md, args.title)
    description = args.description if args.description is not None else md.get("description", "")
    tags = parse_tags(md, args.tags)
    categoryId = category_to_id(args.category or str(md.get("categoryId") or ""))
    defaultLanguage = args.language or md.get("defaultLanguage") or "en"

    snippet = {
        "title": title,
        "description": description,
        "tags": tags,
        "categoryId": categoryId,
        "defaultLanguage": defaultLanguage,
    }
    status = {"privacyStatus": args.privacy}
    if args.publish_at:
        # scheduled publish must be private + RFC3339 time
        status["publishAt"] = args.publish_at
        if status["privacyStatus"] != "private":
            print("[uploader] For scheduled publish, privacy must be 'private'. Overriding.")
            status["privacyStatus"] = "private"

    body = {"snippet": snippet, "status": status}

    print("[uploader] Request body:")
    print(json.dumps(body, indent=2, ensure_ascii=False))

    if args.dry_run:
        print("[uploader] Dry-run: not uploading.")
        return 0

    creds = load_credentials(args.client_secret, args.tokens)
    youtube = build("youtube", "v3", credentials=creds, static_discovery=False)

    video_id = upload_video(youtube, args.video, body)

    if args.thumbnail:
        set_thumbnail(youtube, video_id, args.thumbnail)

    print(f"[uploader] Done. Watch: https://youtu.be/{video_id}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
