#!/usr/bin/env python3
"""
YouTube uploader (OAuth desktop app; resumable; Pillow-free).

Features
- Reads metadata from JSON (title/description/tags/category/language/madeForKids)
- Command-line overrides for title/description/tags/privacy/publishAt
- Resumable upload with progress prints
- Optional thumbnail upload
- Token caching in config/tokens.json (or via env YOUTUBE_TOKENS_JSON)
- Client secrets from config/client_secret.json (or env GOOGLE_CLIENT_SECRET_JSON)

Quickstart (first run opens browser locally)
  pip install google-api-python-client google-auth google-auth-oauthlib
  python scripts/upload_youtube.py \
    --video content/uploads/scenic_montage_001.mp4 \
    --thumbnail content/uploads/thumbnail_001.jpg \
    --metadata content/uploads/metadata_001.json \
    --privacy private

CI (non-interactive): set secrets GOOGLE_CLIENT_SECRET_JSON and YOUTUBE_TOKENS_JSON to the file contents.
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import sys
import time
from typing import Any, Dict, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaUploadProgress
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",  # needed for thumbnails.set and some updates
]

CONFIG_DIR = pathlib.Path("config")
TOKENS_PATH = CONFIG_DIR / "tokens.json"
CLIENT_SECRETS_PATH = CONFIG_DIR / "client_secret.json"

DEFAULT_META = pathlib.Path("content/uploads/metadata_001.json")


def ensure_paths():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def maybe_write_from_env(path: pathlib.Path, env_key: str) -> bool:
    """If env var with JSON content exists, write it to the given path. Return True if written."""
    val = os.environ.get(env_key)
    if not val:
        return False
    try:
        path.write_text(val, encoding="utf-8")
        print(f"[uploader] Wrote {path} from ${env_key}")
        return True
    except Exception as e:
        print(f"[uploader] Failed writing {path} from ${env_key}: {e}")
        return False


def load_metadata(path: pathlib.Path | None) -> Dict[str, Any]:
    if not path:
        return {}
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print("[uploader] Could not read metadata JSON:", e)
    return {}


def get_service() -> Any:
    ensure_paths()
    # Allow CI to inject secrets directly
    if not CLIENT_SECRETS_PATH.exists():
        maybe_write_from_env(CLIENT_SECRETS_PATH, "GOOGLE_CLIENT_SECRET_JSON")
    if not TOKENS_PATH.exists():
        maybe_write_from_env(TOKENS_PATH, "YOUTUBE_TOKENS_JSON")

    creds: Optional[Credentials] = None
    if TOKENS_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKENS_PATH), SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        print("[uploader] Refreshing access token…")
        creds.refresh(Request())
        TOKENS_PATH.write_text(creds.to_json(), encoding="utf-8")
    if not creds or not creds.valid:
        # Interactive auth flow (local browser) — only works on developer machine
        if not CLIENT_SECRETS_PATH.exists():
            raise SystemExit(
                "Missing client secrets. Provide config/client_secret.json or set GOOGLE_CLIENT_SECRET_JSON."
            )
        print("[uploader] Launching OAuth flow in your browser…")
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_PATH), SCOPES)
        creds = flow.run_local_server(port=0, prompt='consent')
        TOKENS_PATH.write_text(creds.to_json(), encoding="utf-8")
        print(f"[uploader] Saved tokens to {TOKENS_PATH}")
    return build("youtube", "v3", credentials=creds)


def build_snippet_status(meta: Dict[str, Any], overrides: argparse.Namespace) -> Dict[str, Any]:
    # Snippet
    snippet: Dict[str, Any] = {
        "title": overrides.title or meta.get("title_en") or meta.get("title") or pathlib.Path(overrides.video).stem,
        "description": overrides.description or meta.get("description", ""),
        "categoryId": (overrides.category or meta.get("categoryId") or "19"),
        "defaultLanguage": overrides.language or meta.get("defaultLanguage") or "en",
    }
    tags = overrides.tags or meta.get("tags")
    if tags:
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        snippet["tags"] = tags

    # Status
    status: Dict[str, Any] = {
        "privacyStatus": overrides.privacy,
        "selfDeclaredMadeForKids": bool(meta.get("madeForKids", False)),
    }
    if overrides.publish_at:
        status["publishAt"] = overrides.publish_at  # RFC3339 timestamp with timezone
        # YouTube requires status=private when publishAt is present
        status["privacyStatus"] = "private"

    return {"snippet": snippet, "status": status}


def resumable_upload(youtube, request):
    response = None
    last_progress = 0
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct != last_progress:
                print(f"[uploader] Upload progress: {pct}%")
                last_progress = pct
        time.sleep(0.1)
    return response


def upload_video(youtube, video_path: pathlib.Path, body: Dict[str, Any]) -> str:
    if not video_path.exists():
        raise SystemExit(f"Video file not found: {video_path}")
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True, chunksize=5 * 1024 * 1024)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    print("[uploader] Starting upload…")
    response = resumable_upload(youtube, request)
    video_id = response.get("id")
    if not video_id:
        raise SystemExit("Upload succeeded but no video ID returned.")
    print("[uploader] Uploaded video ID:", video_id)
    return video_id


def set_thumbnail(youtube, video_id: str, thumb_path: pathlib.Path) -> None:
    if not thumb_path.exists():
        print("[uploader] Thumbnail not found, skipping:", thumb_path)
        return
    media = MediaFileUpload(str(thumb_path), mimetype="image/jpeg")
    print("[uploader] Setting thumbnail…")
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    print("[uploader] Thumbnail set.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Upload a video to YouTube with optional thumbnail and metadata")
    p.add_argument("--video", required=True, help="Path to .mp4 file")
    p.add_argument("--thumbnail", default=None, help="Path to .jpg thumbnail (optional)")
    p.add_argument("--metadata", default=str(DEFAULT_META), help="Path to metadata JSON (optional)")

    p.add_argument("--title", default=None)
    p.add_argument("--description", default=None)
    p.add_argument("--tags", default=None, help="Comma-separated tags (overrides JSON)")
    p.add_argument("--category", default=None, help="YouTube categoryId (default: 19)")
    p.add_argument("--language", default=None, help="Default language (default: en)")

    p.add_argument("--privacy", choices=["public", "private", "unlisted"], default="private")
    p.add_argument("--publish-at", dest="publish_at", default=None, help="RFC3339 local time with offset, e.g. 2025-08-23T17:00:00-07:00")

    p.add_argument("--dry-run", action="store_true", help="Print body and exit without uploading")
    return p.parse_args()


def main():
    args = parse_args()
    meta = load_metadata(pathlib.Path(args.metadata)) if args.metadata else {}

    body = build_snippet_status(meta, args)
    if args.dry_run:
        print(json.dumps(body, indent=2))
        return

    youtube = get_service()
    try:
        video_id = upload_video(youtube, pathlib.Path(args.video), body)
        if args.thumbnail:
            set_thumbnail(youtube, video_id, pathlib.Path(args.thumbnail))
        print("[uploader] Done.")
    except HttpError as e:
        print("[uploader] API error:", e)
        # Surface error details if present
        try:
            print(e.error_details)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
