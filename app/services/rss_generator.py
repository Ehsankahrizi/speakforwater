"""
RSS feed generator for the SpeakForWater podcast.

Generates a valid podcast RSS feed (compatible with Spotify, Apple Podcasts, etc.)
from the episodes directory metadata.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from email.utils import format_datetime

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

PODCAST_TITLE = "SpeakForWater"
PODCAST_LINK = "https://{github_user}.github.io/{repo_name}"
PODCAST_DESCRIPTION = (
    "Making water resources research accessible to everyone. "
    "Join Anna and Ehsan as they discuss the latest open-access papers "
    "on hydrology, flood management, remote sensing, and water engineering — "
    "explained in plain language for farmers, water managers, and anyone who cares about water."
)
PODCAST_AUTHOR = "Ehsan Kahrizi"
PODCAST_CATEGORY = "Science"
PODCAST_LANGUAGE = "en"


def generate_rss(
    episodes_dir: Path,
    site_url: str,
    cover_image_url: str = "",
) -> str:
    """
    Generate a complete podcast RSS XML feed from episode metadata files.

    Args:
        episodes_dir: Path to directory containing epXXX.json metadata files
        site_url: Base URL of the GitHub Pages site
        cover_image_url: URL to the podcast cover art (3000x3000 recommended)

    Returns:
        RSS XML string
    """
    if not cover_image_url:
        cover_image_url = f"{site_url}/images/cover.jpg"

    rss_url = f"{site_url}/podcast.xml"

    # Collect all episode metadata
    episodes = []
    for meta_file in sorted(episodes_dir.glob("ep*.json"), reverse=True):
        try:
            with open(meta_file) as f:
                ep = json.load(f)
                episodes.append(ep)
        except Exception as e:
            logger.warning(f"Skipping {meta_file}: {e}")

    # Build RSS XML
    items_xml = ""
    for ep in episodes:
        pub_date = _parse_date(ep.get("published_at", ""))
        pub_date_str = format_datetime(pub_date) if pub_date else ""

        filename = ep.get("filename", "")
        mp3_url = f"{site_url}/episodes/{filename}"
        file_size = ep.get("file_size_bytes", 0)
        duration = ep.get("duration_seconds", 0)
        episode_number = ep.get("episode_number", 0)
        title = ep.get("title", f"Episode {episode_number}")
        description = ep.get("description", "")
        paper_url = ep.get("paper_url", "")

        if paper_url:
            description += f"\n\nOriginal paper: {paper_url}"

        items_xml += f"""
    <item>
      <title>Ep {episode_number}: {_escape_xml(title)}</title>
      <enclosure url="{_escape_xml(mp3_url)}" length="{file_size}" type="audio/mpeg"/>
      <guid isPermaLink="true">{_escape_xml(mp3_url)}</guid>
      <pubDate>{pub_date_str}</pubDate>
      <itunes:episode>{episode_number}</itunes:episode>
      <itunes:duration>{duration}</itunes:duration>
      <description>{_escape_xml(description)}</description>
    </item>"""

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
  xmlns:atom="http://www.w3.org/2005/Atom"
  xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>{PODCAST_TITLE}</title>
  <link>{_escape_xml(site_url)}</link>
  <description>{_escape_xml(PODCAST_DESCRIPTION)}</description>
  <language>{PODCAST_LANGUAGE}</language>
  <itunes:author>{_escape_xml(PODCAST_AUTHOR)}</itunes:author>
  <itunes:owner>
    <itunes:name>{_escape_xml(PODCAST_AUTHOR)}</itunes:name>
  </itunes:owner>
  <itunes:category text="{PODCAST_CATEGORY}"/>
  <itunes:image href="{_escape_xml(cover_image_url)}"/>
  <itunes:explicit>false</itunes:explicit>
  <atom:link href="{_escape_xml(rss_url)}" rel="self" type="application/rss+xml"/>
  <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>
{items_xml}
</channel>
</rss>"""

    return rss


def _parse_date(date_str: str) -> datetime | None:
    """Parse various date formats into a datetime object."""
    if not date_str:
        return None
    for fmt in ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _escape_xml(text: str) -> str:
    """Escape special XML characters."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
