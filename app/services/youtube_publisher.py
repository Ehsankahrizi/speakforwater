"""
SpeakForWater — youtube_publisher.py

Uploads MP4 videos to YouTube using the Data API v3.

Sanitizes title and description to comply with YouTube's rules:
- No HTML tags (<sub>, <sup>, etc.)
- Title max 100 chars
- Description max 5000 chars
- No problematic characters

Requires THREE secrets:
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from html import unescape
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

log = logging.getLogger(__name__)

YT_CLIENT_ID = os.environ.get("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN", "")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_URI = "https://oauth2.googleapis.com/token"

# YouTube category IDs:
#   22 = People & Blogs   27 = Education
#   28 = Science & Technology
DEFAULT_CATEGORY_ID = "27"

TITLE_MAX = 100
DESC_MAX = 5000

# Characters YouTube rejects in title/description
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")


def _sanitize(text: str, *, max_len: int) -> str:
    """Strip HTML tags, normalize unicode, remove < and >, trim length."""
    if not text:
        return ""
    # Decode HTML entities (&amp; → &)
    text = unescape(text)
    # Strip HTML tags
    text = _HTML_TAG_RE.sub("", text)
    # Normalize unicode (compose accented chars)
    text = unicodedata.normalize("NFKC", text)
    # Replace any stray < or > that survived
    text = text.replace("<", "(").replace(">", ")")
    # Collapse runs of spaces (but keep newlines)
    text = "\n".join(_WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n"))
    # Trim
    text = text.strip()
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def _credentials() -> Credentials:
    if not (YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise RuntimeError(
            "YouTube credentials missing. Set YT_CLIENT_ID, YT_CLIENT_SECRET, "
            "YT_REFRESH_TOKEN as GitHub secrets."
        )
    return Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )


def upload_video(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str] | None = None,
    privacy_status: str = "public",
    category_id: str = DEFAULT_CATEGORY_ID,
) -> str:
    """Upload a video to YouTube. Returns the public video URL."""
    creds = _credentials()
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    safe_title = _sanitize(title, max_len=TITLE_MAX)
    safe_desc = _sanitize(description, max_len=DESC_MAX)

    body = {
        "snippet": {
            "title": safe_title or "Untitled",
            "description": safe_desc or " ",
            "tags": [_sanitize(t, max_len=30) for t in (tags or [])][:30],
            "categoryId": category_id,
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy_status,
            "madeForKids": False,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )

    log.info(
        f"Uploading {video_path.name} to YouTube "
        f"({video_path.stat().st_size // 1024 // 1024} MB) — title: {safe_title!r}"
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info(f"  upload progress: {int(status.progress() * 100)}%")

    video_id = response["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    log.info(f"✓ Uploaded: {url}")
    return url
