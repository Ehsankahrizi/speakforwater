#!/usr/bin/env python3
"""
SpeakForWater — sync_episodes.py

Reads published episodes from the Google Sheet (the same one used by your
existing run_pipeline.py) and generates a markdown file under
src/content/episodes/ for each one. Astro picks them up automatically.

Run this AFTER run_pipeline.py succeeds in publishing a new episode, or
include it in your GitHub Actions workflow before the site build.

Usage:
  python scripts/sync_episodes.py

Environment variables:
  GOOGLE_CREDENTIALS_JSON  Service account JSON (GitHub secret)
  SPREADSHEET_ID           Google Sheet ID
  SHEET_NAME               (optional, default Sheet1)
  EPISODES_DIR             (optional, default src/content/episodes)
  SITE_AUDIO_BASE          (optional) base URL where MP3s are served from.
                           Defaults to /episodes/ which assumes the GitHub
                           Pages root serves /episodes/ep00X.mp3
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = os.environ.get("SHEET_NAME", "Sheet1")
EPISODES_DIR = Path(os.environ.get("EPISODES_DIR", "src/content/episodes"))
SITE_AUDIO_BASE = os.environ.get("SITE_AUDIO_BASE", "/episodes/")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def slugify(text: str) -> str:
    """Convert a paper title to a URL-safe slug."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-+", "-", text)
    return text[:80]


def yaml_escape(s: str) -> str:
    """Escape a string for safe inclusion inside YAML double quotes."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def get_sheet():
    if not GOOGLE_CREDENTIALS_JSON or not SPREADSHEET_ID:
        print("ERROR: GOOGLE_CREDENTIALS_JSON and SPREADSHEET_ID required.", file=sys.stderr)
        sys.exit(1)
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    return spreadsheet.worksheet(SHEET_NAME)


def parse_topics(raw: str) -> list[str]:
    """Comma- or pipe-separated topic list."""
    if not raw:
        return []
    parts = re.split(r"[,|]", raw)
    return [p.strip() for p in parts if p.strip()]


def render_markdown(row: dict) -> tuple[str, str]:
    """Return (filename, markdown_content) for one published episode row."""
    ep_num = int(row.get("episode_number") or 0)
    title = (row.get("paper_title") or "").strip()
    paper_url = (row.get("paper_url") or "").strip()
    pub_date = (row.get("published_at") or row.get("date") or "").strip()
    journal = (row.get("journal") or "").strip()
    description = (row.get("description") or "").strip()
    duration = (row.get("duration") or "10 min").strip()
    topics = parse_topics(row.get("topics") or "")
    show_notes = (row.get("show_notes") or "").strip()

    if not pub_date:
        pub_date = datetime.now(timezone.utc).date().isoformat()
    elif "T" in pub_date:
        pub_date = pub_date.split("T")[0]

    audio_filename = f"ep{str(ep_num).zfill(3)}.mp3"
    audio_url = SITE_AUDIO_BASE + audio_filename

    if not description:
        description = f"Episode {ep_num} discusses the paper: {title}"

    front_matter_lines = [
        "---",
        f"episode_number: {ep_num}",
        f'title: "{yaml_escape(title)}"',
        f'description: "{yaml_escape(description)}"',
        f"pub_date: {pub_date}",
        f'duration: "{yaml_escape(duration)}"',
        f'audio_url: "{audio_url}"',
        "paper:",
        f'  title: "{yaml_escape(title)}"',
        f'  url: "{yaml_escape(paper_url)}"',
        "  open_access: true",
    ]
    if journal:
        front_matter_lines.insert(-1, f'  journal: "{yaml_escape(journal)}"')

    if topics:
        topics_yaml = ", ".join(f'"{yaml_escape(t)}"' for t in topics)
        front_matter_lines.append(f"topics: [{topics_yaml}]")

    front_matter_lines.append("---")
    front_matter = "\n".join(front_matter_lines)

    body = show_notes or (
        f"## About this episode\n\n"
        f"This episode discusses **{title}**, published in "
        f"{journal or 'the source journal'}. "
        f"For full details, [read the original paper]({paper_url}).\n"
    )

    slug = f"{str(ep_num).zfill(3)}-{slugify(title)}"
    filename = f"{slug}.md"
    return filename, f"{front_matter}\n\n{body}\n"


def main():
    sheet = get_sheet()
    rows = sheet.get_all_records()
    print(f"Read {len(rows)} rows from sheet")

    EPISODES_DIR.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0
    for row in rows:
        status = (row.get("status") or "").strip().lower()
        if status != "published":
            skipped += 1
            continue

        try:
            filename, content = render_markdown(row)
        except Exception as e:
            print(f"  ! Failed to render row {row.get('episode_number')}: {e}", file=sys.stderr)
            continue

        path = EPISODES_DIR / filename
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if existing == content:
            continue

        path.write_text(content, encoding="utf-8")
        written += 1
        print(f"  + {filename}")

    print(f"\nDone. Wrote {written} files, skipped {skipped} non-published rows.")


if __name__ == "__main__":
    main()
