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


class _YouTubeDisabled(Exception):
    """Signal that YouTube publishing is intentionally turned off."""
    pass


class FatalPipelineError(Exception):
    """
    A systemic failure that will affect every paper equally, so retrying
    other queued papers is pointless. Aborts the run immediately.
    """
    pass


def _is_sheets_permission_error(exc: Exception) -> bool:
    """
    True if the exception is a Google Sheets 403 ("caller does not have
    permission"). This is systemic — the service account can't write to the
    sheet — usually because Google Drive storage is full or the sheet is no
    longer shared with the service account as Editor. Retrying won't help.
    """
    try:
        from gspread.exceptions import APIError
    except Exception:
        return False

    if not isinstance(exc, APIError):
        return False

    # gspread 6.x exposes both a parsed `.code` and the raw response.
    if getattr(exc, "code", None) == 403:
        return True
    resp = getattr(exc, "response", None)
    return resp is not None and getattr(resp, "status_code", None) == 403


def _is_notebooklm_auth_error(exc: Exception) -> bool:
    """
    True if NotebookLM rejected the session cookies (NOTEBOOKLM_AUTH_JSON has
    expired, or Google invalidated it). The CLI surfaces this as an exit-2
    "Authentication expired or invalid. Redirected to accounts.google.com" on
    the first real API call.

    This is the most systemic failure there is — no paper can be processed until
    the secret is refreshed — so we abort instead of marking every queued paper
    "failed" for a problem that has nothing to do with the papers.
    """
    msg = str(exc).lower()
    return (
        "authentication expired" in msg
        or "authentication failed" in msg
        or "notebooklm login" in msg
        or "re-authenticate" in msg
        or "accounts.google.com" in msg
    )


def _is_notebooklm_limit_error(exc: Exception) -> bool:
    """
    True if the failure is NotebookLM refusing to create a notebook because the
    account is at its notebook cap (100 on free). This is systemic — every paper
    will hit it identically — so we abort instead of burning the whole queue.
    """
    msg = str(exc).lower()
    return (
        "notebook limit" in msg
        or "maximum number of notebooks" in msg
        or "owned notebooks" in msg
    )


def _is_notebooklm_ratelimit_error(exc: Exception) -> bool:
    """
    True if NotebookLM refused audio generation because the account hit its
    daily Audio Overview quota (~3/day on free). Surfaces as a RateLimitError on
    CREATE_ARTIFACT. Systemic for the rest of the UTC day — every remaining paper
    hits it identically — so we abort instead of burning the whole queue.
    """
    msg = str(exc).lower()
    return "ratelimiterror" in msg or "rate limit" in msg or "rate_limit" in msg


# ── Configuration from environment ─────────────────────────────────────

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = os.environ.get("SHEET_NAME", "Sheet1")
NOTEBOOKLM_AUTH_JSON = os.environ.get("NOTEBOOKLM_AUTH_JSON", "")
# Legacy: also check NOTEBOOKLM_COOKIES for backward compat
NOTEBOOKLM_COOKIES = os.environ.get("NOTEBOOKLM_COOKIES", "")
SITE_URL = os.environ.get("SITE_URL", "")
REPO_DIR = Path(os.environ.get("GITHUB_WORKSPACE", "."))
EPISODES_DIR = REPO_DIR / "public" / "episodes"
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

def _ffprobe_duration(file_path: Path) -> float:
    """Get the duration of an audio file in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        logger.warning(f"Failed to get audio duration via ffprobe: {e}")
        return 0.0


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

    # Generate Cover
    from app.services.cover_generator import make_cover
    cover_filename = f"ep{str(ep_num).zfill(3)}.png"
    dest_cover = EPISODES_DIR / cover_filename
    bg = REPO_DIR / "public" / "movie_1.mp4"
    cover_generated = False

    # Short, listener-friendly title for the cover (Groq Llama). Falls back
    # to the full paper title if GROQ_API_KEY is unset or the call fails.
    cover_title = None
    try:
        from app.services.title_simplifier import simplify_title
        cover_title = simplify_title(episode["paper_title"])
        if cover_title:
            logger.info(f"Cover short title: {cover_title!r}")
        else:
            logger.warning("Short title unavailable; cover will use full paper title.")
    except Exception as e:
        logger.warning(f"title_simplifier error: {e}; using full paper title.")

    try:
        make_cover(
            output_path=dest_cover,
            title=episode["paper_title"],
            episode_number=ep_num,
            background=bg if bg.exists() else None,
            paper_url=episode.get("paper_url", ""),
            cover_title=cover_title,
        )
        logger.info(f"Generated and saved cover to {dest_cover}")
        cover_generated = True
    except Exception as e:
        logger.warning(f"Cover generation failed: {e}")

    # Get file size
    file_size = dest_mp3.stat().st_size

    # Upload the MP3 to Cloudflare R2 when configured. If it succeeds, the MP3
    # is NOT committed to the repo (it lives in R2), which keeps the repo small
    # and lets Cloudflare Pages build without hitting its 25 MiB file limit.
    from app.services.r2_uploader import upload_file as r2_upload, r2_enabled
    mp3_on_r2 = False
    if r2_enabled():
        mp3_on_r2 = r2_upload(dest_mp3, f"episodes/{filename}")

    # Create metadata JSON
    now = datetime.now(timezone.utc).isoformat()
    metadata = {
        "episode_number": ep_num,
        "title": episode["paper_title"],
        "paper_url": episode["paper_url"],
        "filename": filename,
        "published_at": now,
        "file_size_bytes": file_size,
        "duration_seconds": _ffprobe_duration(dest_mp3),
        "description": (
            f"SpeakForWater Episode {ep_num}: {episode['paper_title']}. "
            f"A podcast conversation between Anna and Ehsan discussing "
            f"the latest water resources research."
        ),
    }
    if cover_generated and dest_cover.exists():
        metadata["cover"] = f"/episodes/{cover_filename}"

    meta_path = EPISODES_DIR / meta_filename
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info(f"Created metadata: {meta_path}")

    # Regenerate RSS feed
    rss_content = generate_rss(
        episodes_dir=EPISODES_DIR,
        site_url=SITE_URL,
    )
    rss_path = REPO_DIR / "public" / "podcast.xml"
    rss_path.write_text(rss_content, encoding="utf-8")
    logger.info(f"Updated RSS feed: {rss_path}")

    # Git commit and push. When the MP3 is on R2 we don't commit it (and remove
    # any local copy so it never lands in the build output).
    commit_files = [str(meta_path), str(rss_path)]
    if mp3_on_r2:
        dest_mp3.unlink(missing_ok=True)
    else:
        commit_files.insert(0, str(dest_mp3))
    if cover_generated and dest_cover.exists():
        commit_files.append(str(dest_cover))
    _git_commit_and_push(
        files=commit_files,
        message=f"Add episode {ep_num}: {episode['paper_title']}",
    )

    # Ping the WebSub hub so Apple Podcasts re-fetches the feed within minutes
    # instead of waiting hours for its next poll. Best-effort: never fatal.
    try:
        from app.services.rss_generator import ping_websub_hub
        ping_websub_hub(f"{SITE_URL}/podcast.xml")
    except Exception as e:
        logger.warning(f"WebSub ping failed: {e}")

    # Also generate markdown for the Astro website
    try:
        import subprocess
        subprocess.run(['python3', 'scripts/json_to_md.py'], check=False, cwd=str(REPO_DIR))
    except Exception as e:
        logger.warning(f'json_to_md failed: {e}')

    # Re-commit if markdown files were created
    try:
        _git_commit_and_push(
            files=[str(REPO_DIR / 'src' / 'content' / 'episodes')],
            message=f'Sync episode {ep_num} to markdown',
        )
    except Exception:
        pass

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

    # Commit, pull latest, and push
    run(["git", "commit", "-m", message])
    run(["git", "pull", "--rebase", "origin", "main"])
    run(["git", "push"])
    logger.info("Pushed to GitHub")


# ── Main pipeline ──────────────────────────────────────────────────────

async def process_one_episode(episode: dict) -> bool:
    """
    Try to generate, stitch, commit, and publish one episode.
    Returns True if successful, False if failed.
    """
    row_number = episode["row_number"]

    try:
        # Mark as processing
        update_sheet_status(row_number, "processing")

        # Assign the next DISPLAY number as max(published episode_number) + 1.
        #
        # NOTE: we intentionally do NOT use "count of published rows + 1".
        # After catalog curation the published count is lower than the highest
        # episode number (numbering has gaps), so count+1 would point back into
        # the middle of the catalog and collide with an existing episode,
        # overwriting its files. max+1 always allocates a fresh number.
        #
        # We look only at PUBLISHED rows so that dropped/queued rows (which may
        # carry stale numbers) never affect the next number. The published rows'
        # episode_number column matches the website (see fix_sheet_numbers.py).
        # We also persist the assigned number back to this row so it is never
        # reused on a subsequent run.
        from app.services.google_sheets import EpisodeQueue
        _q = EpisodeQueue(
            credentials_json=GOOGLE_CREDENTIALS_JSON,
            spreadsheet_id=SPREADSHEET_ID,
            sheet_name=SHEET_NAME,
        )
        _nums = []
        for r in _q.sheet.get_all_records():
            if str(r.get("status") or "").strip().lower() != "published":
                continue
            # Header may be "episode_number" with a stray trailing space.
            val = next(
                (v for k, v in r.items()
                 if str(k).strip().lower() in ("episode_number", "episode", "episode number")),
                None,
            )
            try:
                _nums.append(int(str(val).strip()))
            except (ValueError, TypeError):
                continue
        episode["episode_number"] = (max(_nums) + 1) if _nums else 1
        # Persist the assigned number to this row (column 5 = episode_number)
        # so future max+1 calculations never reuse it.
        try:
            _q.sheet.update_cell(row_number, 5, episode["episode_number"])
        except Exception as e:
            logger.warning(f"Could not persist episode_number to Sheet: {e}")
        logger.info(
            f"Using sequential display number: Ep {episode['episode_number']}"
        )

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

        # Step 5: Publish to YouTube (auto, gated by YOUTUBE_ENABLED)
        try:
            if os.environ.get("YOUTUBE_ENABLED", "false").strip().lower() != "true":
                logger.info(
                    "YouTube publish disabled (YOUTUBE_ENABLED != 'true'). "
                    "To enable, set repo variable YOUTUBE_ENABLED=true."
                )
                raise _YouTubeDisabled()
            from app.services.video_generator import make_video
            from app.services.youtube_publisher import upload_video

            ep_num = episode["episode_number"]
            paper_title = episode["paper_title"]

            # Reuse the cover image if already generated, otherwise generate to /tmp
            cover_path = EPISODES_DIR / f"ep{str(ep_num).zfill(3)}.png"
            if not cover_path.exists():
                from app.services.cover_generator import make_cover
                cover_path = Path("/tmp") / f"ep{str(ep_num).zfill(3)}_cover.png"
                bg = REPO_DIR / "public" / "movie_1.mp4"
                make_cover(
                    cover_path,
                    title=paper_title,
                    episode_number=ep_num,
                    background=bg if bg.exists() else None,
                    paper_url=episode.get("paper_url", ""),
                )

            video_path = Path("/tmp") / f"ep{str(ep_num).zfill(3)}.mp4"
            make_video(mp3_path, cover_path, video_path)

            yt_description = (
                f"Episode {ep_num} of SpeakForWater — daily narrated water research scientific papers.\n"
                f"In this episode, the paper is entitled \"{paper_title}\", and is analyzed in simple terms.\n"
                f"Listen on the website: {SITE_URL}\n"
                f"Original paper: {episode['paper_url']}\n"
                f"\n"
                f"— DISCLAIMER —\n"
                f"This episode is an AI-generated audio interpretation of the referenced "
                f"peer-reviewed paper. SpeakForWater is an independent project and is not "
                f"affiliated with, endorsed by, or representing the authors, the journal, or "
                f"any institution involved in the original research.\n"
                f"\n"
                f"All scientific findings, conclusions, data, and intellectual credit belong "
                f"solely to the original paper's authors. We make a best-effort, good-faith "
                f"summary of publicly available open-access research for educational purposes "
                f"only and do not add, alter, or generate scientific claims of our own.\n"
                f"\n"
                f"AI narration may contain inaccuracies, omissions, or mischaracterizations. "
                f"Listeners are responsible for consulting the original paper before relying "
                f"on any information presented here. This podcast is not a substitute for "
                f"professional, medical, engineering, or policy advice.\n"
                f"\n"
                f"If you are an author or rights holder and would like an episode removed or "
                f"corrected, contact: hello@speakforwater.com. We respond within 7 days.\n"
                f"\n"
                f"Subscribe on Apple Podcasts, Spotify, and more."
            )

            yt_url = upload_video(
                video_path,
                title=f"Ep {ep_num}: {paper_title}",
                description=yt_description,
                tags=["water", "research", "podcast", "AI", "science", "hydrology"],
                privacy_status="public",
            )
            logger.info(f"YouTube URL: {yt_url}")
        except _YouTubeDisabled:
            pass
        except Exception as e:
            logger.warning(f"YouTube publish failed (continuing): {e}")

        logger.info("\n" + "=" * 60)
        logger.info(f"  Episode {episode['episode_number']} published!")
        logger.info(f"  MP3: {mp3_url}")
        logger.info("=" * 60)
        return True

    except Exception as e:
        # A Sheets 403 is systemic (storage full / lost Editor access): every
        # paper will hit it, and even marking this row "failed" would 403 again.
        # Abort the whole run instead of burning attempts on other papers.
        if _is_sheets_permission_error(e):
            raise FatalPipelineError(
                "Google Sheets write was denied (403). The service account "
                "cannot modify the sheet. Most likely the Google Drive storage "
                "is full, or the sheet is no longer shared with the service "
                "account as Editor. Fix that, then re-run — no papers were "
                "changed."
            ) from e

        if _is_notebooklm_auth_error(e):
            # The stored NotebookLM session is dead. Every remaining paper will
            # fail identically on its first API call, so re-queue this (good)
            # paper and abort rather than burning the queue.
            try:
                update_sheet_status(row_number, "queued")
            except Exception:
                pass
            raise FatalPipelineError(
                "NotebookLM authentication expired — the NOTEBOOKLM_AUTH_JSON "
                "secret is no longer accepted by Google (the CLI was redirected "
                "to the sign-in page). Refresh it: run 'notebooklm login' "
                "locally, then copy ~/.notebooklm/storage_state.json into the "
                "NOTEBOOKLM_AUTH_JSON repository secret. This paper was left "
                "queued. Aborting before burning the queue."
            ) from e

        if _is_notebooklm_limit_error(e):
            # This row was already flipped to "processing"; put it back to
            # "queued" so it is retried next run (the paper is fine — NotebookLM
            # was full). Otherwise it would be stranded in "processing".
            try:
                update_sheet_status(row_number, "queued")
            except Exception:
                pass
            raise FatalPipelineError(
                "NotebookLM notebook limit reached — no new notebooks can be "
                "created. Delete old notebooks at https://notebooklm.google.com "
                "(free accounts cap at 100). The pipeline now auto-deletes each "
                "notebook after use, so this should stop recurring once you are "
                "back under the limit. Aborting before burning the queue."
            ) from e

        if _is_notebooklm_ratelimit_error(e):
            # Daily Audio Overview quota is spent (~3/day on free). Every
            # remaining paper will hit the same limit today, so re-queue this
            # (good) paper and abort — it will generate after the UTC reset.
            try:
                update_sheet_status(row_number, "queued")
            except Exception:
                pass
            raise FatalPipelineError(
                "NotebookLM daily audio limit reached (RateLimitError). The "
                "free tier allows ~3 Audio Overviews per day; the quota is spent. "
                "This paper was left queued and will generate after the limit "
                "resets (around 00:00 UTC). Aborting before burning the queue."
            ) from e

        logger.error(f"\nFailed: {e}", exc_info=True)
        try:
            update_sheet_status(row_number, "failed")
        except Exception:
            pass
        return False

    finally:
        # Cleanup temp files
        if DOWNLOADS_DIR.exists():
            shutil.rmtree(DOWNLOADS_DIR, ignore_errors=True)


async def main():
    """Run the full pipeline: Sheet → NotebookLM → Git → Sheet.
    If a paper fails (e.g. paywalled URL), skip it and try the next queued paper.
    Tries up to MAX_ATTEMPTS papers per run to find one that works.
    """
    MAX_ATTEMPTS = 5  # Try up to 5 queued papers before giving up

    logger.info("=" * 60)
    logger.info("  SpeakForWater — Daily Podcast Pipeline")
    logger.info("=" * 60)

    validate_env()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        # Get next queued episode
        episode = get_next_episode()
        if not episode:
            logger.info("No more queued episodes — nothing to do.")
            return

        logger.info(f"\n--- Attempt {attempt}/{MAX_ATTEMPTS} ---")
        try:
            success = await process_one_episode(episode)
        except FatalPipelineError as e:
            logger.error(f"\nAborting run — {e}")
            sys.exit(1)

        if success:
            return  # Done!

        logger.warning(
            f"Episode #{episode['episode_number']} failed. "
            f"Trying next queued paper..."
        )

    logger.error(f"All {MAX_ATTEMPTS} attempts failed. Exiting.")
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
