"""
RSS feed generator for the SpeakForWater podcast.

Generates a valid podcast RSS feed (compatible with Spotify, Apple Podcasts, etc.)
from the episodes directory metadata.
"""

from __future__ import annotations

import json
import logging
import os
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
PODCAST_OWNER_EMAIL = os.getenv("PODCAST_OWNER_EMAIL", "kahriziehsan490@gmail.com")
PODCAST_CATEGORY = "Science"
PODCAST_LANGUAGE = "en"

# WebSub (PubSubHubbub) hub. Advertised in the feed and pinged after each new
# episode so subscribers like Apple Podcasts pick up new items within minutes
# instead of waiting hours for their next poll.
WEBSUB_HUB = "https://pubsubhubbub.appspot.com/"


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
        cover_image_url = f"{site_url}/podcast-cover.jpg"

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

    # Audio is served from MEDIA_BASE_URL (Cloudflare R2) when set; otherwise
    # it falls back to the site itself (current GitHub Pages behavior).
    media_base = (os.getenv("MEDIA_BASE_URL") or site_url).rstrip("/")

    # Build RSS XML
    items_xml = ""
    for ep in episodes:
        pub_date = _parse_date(ep.get("published_at", ""))
        pub_date_str = format_datetime(pub_date) if pub_date else ""

        filename = ep.get("filename", "")
        mp3_url = f"{media_base}/episodes/{filename}"
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
      <guid isPermaLink="false">speakforwater-ep{episode_number}</guid>
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
  <itunes:type>episodic</itunes:type>
  <itunes:owner>
    <itunes:name>{_escape_xml(PODCAST_AUTHOR)}</itunes:name>
    <itunes:email>{_escape_xml(PODCAST_OWNER_EMAIL)}</itunes:email>
  </itunes:owner>
  <itunes:category text="{PODCAST_CATEGORY}"/>
  <itunes:image href="{_escape_xml(cover_image_url)}"/>
  <itunes:explicit>false</itunes:explicit>
  <itunes:new-feed-url>{_escape_xml(rss_url)}</itunes:new-feed-url>
  <atom:link href="{_escape_xml(rss_url)}" rel="self" type="application/rss+xml"/>
  <atom:link href="{_escape_xml(WEBSUB_HUB)}" rel="hub"/>
  <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>
{items_xml}
</channel>
</rss>"""

    return rss


def ping_websub_hub(feed_url: str, hub_url: str = WEBSUB_HUB) -> bool:
    """Notify the WebSub hub that the feed changed (best-effort, no deps).

    Subscribers such as Apple Podcasts that follow the hub then re-fetch the
    feed within minutes instead of waiting for their next scheduled poll.
    Returns True on a 2xx response, False otherwise; never raises.
    """
    import urllib.parse
    import urllib.request

    payload = urllib.parse.urlencode(
        {"hub.mode": "publish", "hub.url": feed_url}
    ).encode()
    try:
        req = urllib.request.Request(hub_url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = getattr(resp, "status", resp.getcode())
            logger.info(f"[websub] pinged hub for {feed_url}: HTTP {status}")
            return 200 <= status < 300
    except Exception as e:
        logger.warning(f"[websub] hub ping failed: {e}")
        return False


def _parse_date(date_str: str) -> datetime | None:
    """Parse various date formats into a datetime object."""
    if not date_str:
        return None
    # fromisoformat handles the pipeline's timestamps, including microseconds
    # and timezone offsets (e.g. 2026-06-25T09:49:19.483323+00:00).
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]:
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
