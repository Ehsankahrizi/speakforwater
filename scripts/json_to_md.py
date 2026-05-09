#!/usr/bin/env python3
"""
Convert episode JSON metadata files (in public/episodes/) into Astro
markdown content (in src/content/episodes/) so the website can display
them with a working audio player.

Run from the repo root:
    python3 json_to_md.py
"""

import json
import re
from pathlib import Path

REPO = Path(".")
JSON_DIR = REPO / "public" / "episodes"
MD_DIR = REPO / "src" / "content" / "episodes"

if not JSON_DIR.exists():
    print(f"No {JSON_DIR} folder yet — nothing to convert.")
    exit(0)

MD_DIR.mkdir(parents=True, exist_ok=True)


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-+", "-", text)
    return text[:60]


def yaml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


written = 0
for json_path in sorted(JSON_DIR.glob("*.json")):
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ! cannot parse {json_path.name}: {e}")
        continue

    ep_num = data.get("episode_number", 0)
    title = (data.get("title") or "Untitled").strip()
    description = (data.get("description") or f"Episode {ep_num}: {title}").strip()
    paper_url = (data.get("paper_url") or "").strip()
    pub_date = (data.get("published_at") or "").strip()
    if pub_date and "T" in pub_date:
        pub_date = pub_date.split("T")[0]
    if not pub_date:
        pub_date = "2026-05-08"
    duration_sec = data.get("duration_seconds", 0)
    duration = (
        f"{duration_sec // 60} min"
        if duration_sec > 0 else "10 min"
    )

    audio_url = f"/episodes/ep{str(ep_num).zfill(3)}.mp3"
    slug = f"{str(ep_num).zfill(3)}-{slugify(title)}"
    md_path = MD_DIR / f"{slug}.md"

    front = "\n".join([
        "---",
        f"episode_number: {ep_num}",
        f'title: "{yaml_escape(title)}"',
        f'description: "{yaml_escape(description)}"',
        f"pub_date: {pub_date}",
        f'duration: "{duration}"',
        f'audio_url: "{audio_url}"',
        "paper:",
        f'  title: "{yaml_escape(title)}"',
        f'  url: "{yaml_escape(paper_url)}"',
        "  open_access: true",
        "---",
    ])

    body = (
        f"## About this episode\n\n"
        f"This episode discusses **{title}**.\n\n"
        f"[Read the original paper]({paper_url})\n"
    )

    md_path.write_text(f"{front}\n\n{body}", encoding="utf-8")
    written += 1
    print(f"  + {md_path.name}")

print(f"\nDone. Wrote {written} markdown files.")
