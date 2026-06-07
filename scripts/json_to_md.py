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


# ── Topic taxonomy + keyword classifier ──────────────────────────────
# Stakeholder-friendly topics. The pipeline does not set topics on new
# episodes, so we (a) use topics stored in the JSON when present, and
# (b) auto-classify from the title/description otherwise — guaranteeing
# every episode always has at least one topic (the filter rail breaks
# when the catalog has zero topics).
TOPIC_KEYWORDS = {
    "Agriculture": ["agricultur", "crop", "farm", "soil", "yield", "cropland"],
    "Aquaculture": ["aquaculture", "fish farm", "fishery", "aquatic farm"],
    "Climate Change": ["climate", "warming", "greenhouse", "carbon"],
    "Coastal & Seawater": ["coastal", "seawater", "saltwater", "saline", "sea level", "estuar", "desalin"],
    "Drinking Water": ["drinking water", "tap water", "potable", "water supply", "water security", "household water"],
    "Drought": ["drought", "water scarcity", "dry spell", "aridity"],
    "Flooding": ["flood", "inundation", "stormwater", "extreme rainfall"],
    "Groundwater": ["groundwater", "aquifer", "well ", "subsurface water"],
    "Irrigation": ["irrigation", "irrigat"],
    "Pollution": ["pollut", "contaminat", "nitrate", "heavy metal", "microplastic", "runoff"],
    "Public Health": ["health", "disease", "waterborne", "sanitation", "hygiene"],
    "Rainwater Harvesting": ["rainwater", "rain harvest", "rooftop catchment"],
    "Urban Water": ["urban", "city", "municipal", "cities"],
    "Water & Energy": ["energy", "hydropower", "hydroelectric", "water-energy", "power plant"],
    "Water Policy": ["policy", "governance", "allocation", "regulation", "management framework", "stakeholder"],
    "Water Quality": ["water quality", "turbidity", "salinity", "dissolved", "pollutant level"],
    "Water Reuse": ["reuse", "recycl", "wastewater", "reclaimed", "greywater"],
    "Water Security": ["security", "reliability", "resilience", "supply risk", "scarcity risk"],
}
DEFAULT_TOPIC = "Water Security"


def classify_topics(text: str, max_topics: int = 3) -> list[str]:
    """Pick stakeholder topics by keyword match over title + description."""
    low = text.lower()
    hits = []
    for topic, kws in TOPIC_KEYWORDS.items():
        score = sum(1 for kw in kws if kw in low)
        if score:
            hits.append((score, topic))
    hits.sort(key=lambda x: (-x[0], x[1]))
    topics = [t for _, t in hits[:max_topics]]
    return topics or [DEFAULT_TOPIC]


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

    cover = data.get("cover")

    # Topics: prefer curated topics stored in the JSON; otherwise reuse any
    # already present in the existing markdown; otherwise auto-classify.
    # This guarantees the catalog is never left with zero topics, which
    # would hide the filter rail and break the episodes-page layout.
    topics = data.get("topics") or []
    audio_url = f"/episodes/ep{str(ep_num).zfill(3)}.mp3"
    slug = f"{str(ep_num).zfill(3)}-{slugify(title)}"
    md_path = MD_DIR / f"{slug}.md"

    if not topics and md_path.exists():
        m = re.search(r"topics:\s*\n((?:\s*-\s*.*\n)+)", md_path.read_text(encoding="utf-8"))
        if m:
            topics = re.findall(r'-\s*"?([^"\n]+?)"?\s*$', m.group(1), re.M)

    if not topics:
        topics = classify_topics(f"{title} {description}")

    front_lines = [
        "---",
        f"episode_number: {ep_num}",
        f'title: "{yaml_escape(title)}"',
        f'description: "{yaml_escape(description)}"',
        f"pub_date: {pub_date}",
        f'duration: "{duration}"',
        f'audio_url: "{audio_url}"',
    ]
    if cover:
        front_lines.append(f'cover: "{yaml_escape(cover)}"')
    front_lines.append("topics:")
    for t in topics:
        front_lines.append(f'  - "{yaml_escape(t.strip())}"')
    front_lines.extend([
        "paper:",
        f'  title: "{yaml_escape(title)}"',
        f'  url: "{yaml_escape(paper_url)}"',
        "  open_access: true",
        "---",
    ])
    front = "\n".join(front_lines)

    body = (
        f"## About this episode\n\n"
        f"This episode discusses **{title}**.\n\n"
        f"[Read the original paper]({paper_url})\n"
    )

    md_path.write_text(f"{front}\n\n{body}", encoding="utf-8")
    written += 1
    print(f"  + {md_path.name}")

print(f"\nDone. Wrote {written} markdown files.")
