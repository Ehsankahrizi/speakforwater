#!/usr/bin/env python3
"""
SpeakForWater — Complete pipeline runner for GitHub Actions.

This single script replaces both the FastAPI server and n8n workflow.
It runs once per execution (triggered daily by GitHub Actions cron):

  1. Read next queued episode from Google Sheets
  2. Automate NotebookLM to generate the podcast
  3. Commit the MP3 + metadata to this repo
  4. Update the RSS feed
  5. Mark the episode as published in Google Sheets

Usage:
  python run_pipeline.py

Environment variables (set as GitHub Actions secrets):
  GOOGLE_CREDENTIALS_JSON  — Service account JSON key for Google Sheets
  SPREADSHEET_ID           — Google Sheet ID (from the URL)
  NOTEBOOKLM_COOKIES       — Contents of cookies.txt (Netscape format)
  GITHUB_TOKEN             — Automatically provided by GitHub Actions
  SITE_URL                 — Your GitHub Pages URL (e.g. https://ehsan.github.io/speakforwater)
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Logging ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("speakforwater")


# ── Configuration from environment ─────────────────────────────────────

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = os.environ.get("SHEET_NAME", "Sheet1")
NOTEBOOKLM_AUTH_JSON = os.environ.get("NOTEBOOKLM_AUTH_JSON", "")
# Legacy: also check NOTEBOOKLM_COOKIES for backward compat
NOTEBOOKLM_COOKIES = os.environ.get("NOTEBOOKLM_COOKIES", "")
SITE_URL = os.environ.get("SITE_URL", "")
REPO_DIR = Path(os.environ.get("GITHUB_WORKSPACE", "."))
EPISODES_DIR = REPO_DIR / "episodes"
DOWNLOADS_DIR = Path("/tmp/speakforwater-downloads")


def validate_env():
    """Check all required environment variables are set."""
    missing = []
    if not GOOGLE_CREDENTIALS_JSON:
        missing.append("GOOGLE_CREDENTIALS_JSON")
    if not SPREADSHEET_ID:
        missing.append("SPREADSHEET_ID")
    if not NOTEBOOKLM_AUTH_JSON and not NOTEBOOKLM_COOKIES:
        missing.append("NOTEBOOKLM_AUTH_JSON")
    if not SITE_URL:
        missing.append("SITE_URL")

    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        logger.error("Set these as GitHub Actions secrets in your repo settings.")
        sys.exit(1)


# ── Step 1: Read from Google Sheets ────────────────────────────────────

def get_next_episode() -> dict | None:
    """Fetch the next queued episode from the Google Sheet."""
    from app.services.google_sheets import EpisodeQueue

    logger.info("Connecting to Google Sheets...")
    queue = EpisodeQueue(
        credentials_json=GOOGLE_CREDENTIALS_JSON,
        spreadsheet_id=SPREADSHEET_ID,
        sheet_name=SHEET_NAME,
    )
    episode = queue.get_next_queued()

    if episode:
        logger.info(
            f"Found queued episode #{episode['episode_number']}: "
            f"{episode['paper_title']}"
        )
    else:
        logger.info("No queued episodes — nothing to do today.")

    return episode


def update_sheet_status(row_number: int, status: str, mp3_url: str = ""):
    """Update the Google Sheet with the episode status."""
    from app.services.google_sheets import EpisodeQueue

    queue = EpisodeQueue(
        credentials_json=GOOGLE_CREDENTIALS_JSON,
        spreadsheet_id=SPREADSHEET_ID,
        sheet_name=SHEET_NAME,
    )
    if status == "published":
        queue.mark_published(row_number, mp3_url)
    elif status == "failed":
        queue.mark_failed(row_number)
    else:
        queue.update_status(row_number, status)


# ── Step 2: Generate podcast via NotebookLM ────────────────────────────

async def generate_podcast(episode: dict) -> Path:
    """
    Use notebooklm-py SDK to generate the podcast MP3.
    No browser needed — uses direct API calls with auth token.
    Returns the path to the downloaded MP3 file.
    """
    from app.services.notebooklm import NotebookLMAutomator
    from app.services.prompt_manager import get_prompt

    # Ensure downloads directory exists
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

    # Use auth JSON (preferred) or fall back to cookies
    auth_json = NOTEBOOKLM_AUTH_JSON or NOTEBOOKLM_COOKIES

    automator = NotebookLMAutomator(
        auth_json=auth_json,
        storage_dir=DOWNLOADS_DIR,
    )

    try:
        await automator.start()
        logger.info("NotebookLM SDK ready")

        prompt = get_prompt()  # Use default SpeakForWater prompt

        async def on_status(status, message):
            logger.info(f"  [{status}] {message}")

        result = await automator.generate_podcast(
            paper_url=episode["paper_url"],
            paper_title=episode["paper_title"],
            episode_number=episode["episode_number"],
            prompt=prompt,
            on_status=on_status,
        )

        mp3_path = Path(result["mp3_path"])
        logger.info(f"Podcast generated: {mp3_path}")
        return mp3_path

    finally:
        await automator.stop()


# ── Step 3: Commit to repo ─────────────────────────────────────────────

def commit_episode(episode: dict, mp3_path: Path) -> str:
    """
    Copy the MP3 into the repo's episodes/ directory,
    create metadata JSON, update the RSS feed, and git commit + push.
    Returns the public MP3 URL.
    """
    from app.services.rss_generator import generate_rss

    ep_num = episode["episode_number"]
    filename = f"ep{str(ep_num).zfill(3)}.mp3"
    meta_filename = f"ep{str(ep_num).zfill(3)}.json"

    # Ensure episodes directory exists
    EPISODES_DIR.mkdir(parents=True, exist_ok=True)

    # Copy MP3 to repo
    dest_mp3 = EPISODES_DIR / filename
    shutil.copy2(mp3_path, dest_mp3)
    logger.info(f"Copied MP3 to {dest_mp3}")

    # Get file size
    file_size = dest_mp3.stat().st_size

    # Create metadata JSON
    now = datetime.now(timezone.utc).isoformat()
    metadata = {
        "episode_number": ep_num,
        "title": episode["paper_title"],
        "paper_url": episode["paper_url"],
        "filename": filename,
        "published_at": now,
        "file_size_bytes": file_size,
        "duration_seconds": 0,  # Could be extracted with mutagen/ffprobe
        "description": (
            f"SpeakForWater Episode {ep_num}: {episode['paper_title']}. "
            f"A podcast conversation between Anna and Ehsan discussing "
            f"the latest water resources research."
        ),
    }

    meta_path = EPISODES_DIR / meta_filename
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info(f"Created metadata: {meta_path}")

    # Regenerate RSS feed
    rss_content = generate_rss(
        episodes_dir=EPISODES_DIR,
        site_url=SITE_URL,
    )
    rss_path = REPO_DIR / "podcast.xml"
    rss_path.write_text(rss_content, encoding="utf-8")
    logger.info(f"Updated RSS feed: {rss_path}")

    # Git commit and push
    _git_commit_and_push(
        files=[str(dest_mp3), str(meta_path), str(rss_path)],
        message=f"Add episode {ep_num}: {episode['paper_title']}",
    )

    # Return the public URL
    mp3_url = f"{SITE_URL}/episodes/{filename}"
    return mp3_url


def _git_commit_and_push(files: list[str], message: str):
    """Stage files, commit, and push to the repo."""
    def run(cmd):
        logger.info(f"  $ {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_DIR))
        if result.returncode != 0:
            logger.error(f"    stderr: {result.stderr}")
            raise RuntimeError(f"Git command failed: {' '.join(cmd)}")
        return result.stdout.strip()

    # Configure git for GitHub Actions
    run(["git", "config", "user.name", "SpeakForWater Bot"])
    run(["git", "config", "user.email", "bot@speakforwater.com"])

    # Stage files
    for f in files:
        run(["git", "add", f])

    # Check if there are changes to commit
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(REPO_DIR),
    )
    if result.returncode == 0:
        logger.info("No changes to commit")
        return

    # Commit and push
    run(["git", "commit", "-m", message])
    run(["git", "push"])
    logger.info("Pushed to GitHub")


# ── Main pipeline ──────────────────────────────────────────────────────

async def main():
    """Run the full pipeline: Sheet → NotebookLM → Git → Sheet."""
    logger.info("=" * 60)
    logger.info("  SpeakForWater — Daily Podcast Pipeline")
    logger.info("=" * 60)

    validate_env()

    # Step 1: Get next episode from Google Sheets
    episode = get_next_episode()
    if not episode:
        logger.info("Nothing to process. Exiting.")
        return

    row_number = episode["row_number"]

    try:
        # Mark as processing
        update_sheet_status(row_number, "processing")

        # Step 2: Generate podcast
        logger.info(f"\nGenerating podcast for: {episode['paper_title']}")
        mp3_path = await generate_podcast(episode)

        # Step 2b: Stitch intro/outro music
        logger.info("\nStitching intro/outro jingles...")
        from app.services.audio_stitcher import stitch_podcast
        try:
            mp3_path = stitch_podcast(
                podcast_path=mp3_path,
                intro_path=REPO_DIR / "assets" / "intro.mp3",
                outro_path=REPO_DIR / "assets" / "outro.mp3",
            )
            logger.info(f"Stitched podcast: {mp3_path}")
        except Exception as e:
            logger.warning(f"Stitching failed (using raw podcast): {e}")

        # Step 3: Commit to repo
        logger.info("\nCommitting episode to repository...")
        mp3_url = commit_episode(episode, mp3_path)

        # Step 4: Mark as published
        update_sheet_status(row_number, "published", mp3_url=mp3_url)

        logger.info("\n" + "=" * 60)
        logger.info(f"  Episode {episode['episode_number']} published!")
        logger.info(f"  MP3: {mp3_url}")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"\nPipeline failed: {e}", exc_info=True)
        try:
            update_sheet_status(row_number, "failed")
        except Exception:
            pass
        sys.exit(1)

    finally:
        # Cleanup temp files
        if DOWNLOADS_DIR.exists():
            shutil.rmtree(DOWNLOADS_DIR, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
