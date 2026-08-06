"""
NotebookLM automation using the notebooklm-py SDK.

Uses the unofficial Python SDK (notebooklm-py) instead of Playwright browser
automation. This works in headless CI/CD environments like GitHub Actions
by using NOTEBOOKLM_AUTH_JSON for authentication.

Auth flow:
  1. Run `notebooklm login` on your local machine once (opens browser)
  2. Export the auth JSON: read ~/.notebooklm/storage_state.json
  3. Store it as a GitHub Actions secret: NOTEBOOKLM_AUTH_JSON
  4. The SDK uses these cookies to make direct API calls (no browser needed)

Correct CLI commands (notebooklm-py):
  notebooklm create "Title"               — create notebook, prints ID
  notebooklm use <notebook_id>            — set active notebook context
  notebooklm source add <url>             — add a source URL
  notebooklm generate audio "<prompt>" --wait  — generate podcast audio
  notebooklm download audio <output.mp3>  — download the generated audio
  notebooklm delete <notebook_id> --yes   — delete notebook (cleanup)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ── Source-content validation ────────────────────────────────────────────
# NotebookLM fetches URL sources server-side, from Google's own IPs. When a
# publisher blocks that fetcher, the fetch still "succeeds": the block page is
# ingested and the source reaches status=ready like any other. Verified live on
# 2026-08-05 — a https://doi.org/... link for a Springer paper produced a ready
# source titled "406 Not Acceptable" holding 44 words of "Your IP has been
# blocked due to suspicious activity".
#
# Nothing raises, so without this check the pipeline generates a full episode
# out of an HTTP error page and publishes it. A silently wrong episode is far
# worse than a failed run, so these checks are fatal, not advisory.

# ── Source ingestion ─────────────────────────────────────────────────────
# Because NotebookLM's own fetcher is blocked, the pipeline downloads the PDF
# itself and uploads the bytes. Publisher CDNs serve a bot wall to non-browser
# user agents, so the download presents a browser one.
PDF_DOWNLOAD_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
PDF_DOWNLOAD_TIMEOUT = 60
MIN_PDF_BYTES = 10_000       # below this it is an error page, not a paper
MAX_PDF_BYTES = 100_000_000  # guard against a mislabelled huge response


# Fatal below this: a real paper never lands here, a block page always does.
MIN_CONTENT_WORDS = 150

# Not fatal, but loud. Enough to build an episode on, but thin for a paper —
# usually an abstract-only or partially-rendered fetch.
THIN_CONTENT_WORDS = 500

# Matched case-insensitively against the fetched source title.
BLOCK_TITLE_MARKERS = (
    "not acceptable",       # 406 — Springer Nature's fetcher block
    "access denied",
    "access blocked",
    "forbidden",            # 403
    "not found",            # 404
    "too many requests",    # 429
    "just a moment",        # Cloudflare interstitial
    "attention required",   # Cloudflare block
    "are you a robot",
    "security check",
    "verify you are human",
    "service unavailable",  # 503
)

# Matched case-insensitively against the fetched source body, for block pages
# whose title looks innocent.
BLOCK_BODY_MARKERS = (
    "your ip has been blocked",
    "ip has been blocked",
    "unusual traffic",
    "suspicious activity",
    "enable javascript to continue",
    "please complete the captcha",
    "access to this page has been denied",
)


class SourceContentError(RuntimeError):
    """The source was ingested, but its content is not the paper.

    Distinct from a source-add failure: the add succeeded and NotebookLM
    reports the source ready. Raised so callers can tell "this paper is
    unusable" apart from "NotebookLM is broken".
    """


class NotebookLMAutomator:
    """
    Automates NotebookLM podcast generation using the notebooklm-py CLI/SDK.

    Steps:
      1. Create a new notebook
      2. Set the notebook as active (notebooklm use <id>)
      3. Add paper URL as a source
      4. Generate audio overview with custom prompt (--wait for completion)
      5. Download the MP3
    """

    def __init__(self, auth_json: str | None = None, storage_dir: Path | None = None):
        """
        Args:
            auth_json: JSON string with NotebookLM auth cookies.
                       If not provided, reads from NOTEBOOKLM_AUTH_JSON env var.
            storage_dir: Directory to save downloaded MP3 files.
        """
        self.auth_json = auth_json or os.environ.get("NOTEBOOKLM_AUTH_JSON", "")
        self.storage_dir = storage_dir or Path("/tmp/speakforwater-downloads")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._ready = False

    async def start(self):
        """Validate authentication and write auth file."""
        if not self.auth_json:
            raise RuntimeError(
                "No auth JSON provided. Set NOTEBOOKLM_AUTH_JSON environment variable. "
                "To get it: run 'notebooklm login' locally, then read "
                "~/.notebooklm/storage_state.json"
            )

        # Write auth JSON to the file location the SDK expects
        auth_dir = Path.home() / ".notebooklm"
        auth_dir.mkdir(parents=True, exist_ok=True)
        auth_file = auth_dir / "storage_state.json"
        auth_file.write_text(self.auth_json)
        logger.info("Auth JSON written to ~/.notebooklm/storage_state.json")

        # Local sanity check on the cookie file (cheap, and not always present).
        try:
            result = self._run_cli(["notebooklm", "auth", "check"])
            logger.info(f"Auth check (local): {result[:100]}")
        except RuntimeError as e:
            if "no such command" not in str(e).lower():
                raise
            logger.warning("'auth check' not available in this CLI version.")

        # `auth check` only inspects the cookie file on disk — it happily reports
        # success for cookies Google has already expired server-side. So make one
        # real API call: an expired secret then fails here, once and clearly,
        # instead of surfacing mid-generation as a confusing per-paper error.
        try:
            self._run_cli(["notebooklm", "list"], timeout=60)
            logger.info("Auth verified against the NotebookLM API (notebook list)")
        except RuntimeError as e:
            if "no such command" in str(e).lower():
                logger.warning(
                    "'notebooklm list' unavailable — skipping live auth probe."
                )
            else:
                raise RuntimeError(
                    "NotebookLM authentication expired or invalid — the stored "
                    "session cookies were rejected by Google. Re-run "
                    "'notebooklm login' locally and update the "
                    "NOTEBOOKLM_AUTH_JSON secret with the new "
                    "~/.notebooklm/storage_state.json. "
                    f"Error: {e}"
                ) from e

        self._ready = True
        logger.info("NotebookLM SDK authentication verified")

    async def stop(self):
        """Cleanup."""
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready

    async def generate_podcast(
        self,
        paper_url: str,
        paper_title: str,
        episode_number: int,
        prompt: str,
        audio_format: str = "deep_dive",
        language: str = "English",
        length: str = "default",
        on_status: Optional[Callable] = None,
    ) -> dict:
        """
        Full pipeline using notebooklm-py CLI commands.

        Returns dict with:
            - mp3_path: Path to the downloaded MP3 file
            - notebook_id: The NotebookLM notebook ID
            - duration_seconds: Estimated duration (if available)
        """
        if not self._ready:
            raise RuntimeError("Automator not started. Call start() first.")

        notebook_id = None
        filename = f"ep{str(episode_number).zfill(3)}.mp3"
        mp3_path = self.storage_dir / filename

        try:
            # ── Step 1: Create a new notebook ──────────────────────
            if on_status:
                await on_status("creating_notebook", "Creating new notebook...")
            logger.info("Creating new notebook...")

            notebook_title = f"SpeakForWater Ep{episode_number}: {paper_title[:50]}"
            create_output = self._run_cli([
                "notebooklm", "create", notebook_title
            ])

            notebook_id = self._parse_notebook_id(create_output)
            logger.info(f"Created notebook: {notebook_id}")

            # ── Step 2: Set active notebook context ─────────────────
            logger.info(f"Setting active notebook: {notebook_id}")
            self._run_cli(["notebooklm", "use", notebook_id])

            # ── Step 3: Add the paper as a source ───────────────────
            if on_status:
                await on_status("adding_source", f"Adding source: {paper_url}")

            # Download-and-upload first, URL second. NotebookLM's server-side
            # fetcher is blocked by most publishers, so handing it a URL either
            # fails outright or silently ingests a block page; fetching the
            # bytes ourselves sidesteps that entirely. See _download_pdf.
            source_added = self._add_source_as_file(notebook_id, paper_url, paper_title)

            if not source_added:
                logger.info("File upload unavailable — falling back to URL add.")
                for attempt in range(1, 4):
                    try:
                        self._run_cli(
                            ["notebooklm", "source", "add", paper_url, "-n", notebook_id],
                            timeout=60,
                        )
                        source_added = True
                        break
                    except RuntimeError as e:
                        logger.warning(
                            f"Source add attempt {attempt}/3 failed: {str(e)[:150]}"
                        )
                        if attempt < 3:
                            await asyncio.sleep(10)

            if not source_added:
                raise RuntimeError(
                    f"Failed to add source after 3 attempts: {paper_url}\n"
                    f"An rpc_code=9 rejection in under a second is NotebookLM "
                    f"refusing the domain outright, not a paywall or a slow "
                    f"fetch — verified on 2026-08-05 for link.springer.com, "
                    f"www.nature.com and pubs.acs.org, article pages included.\n"
                    f"Do NOT retry this as a https://doi.org/... link. The DOI "
                    f"is accepted, but it resolves to the same blocked "
                    f"publisher and NotebookLM ingests the '406 Not "
                    f"Acceptable' page as a healthy source. Use an "
                    f"open-access host (PMC, arXiv, DOAJ) instead."
                )

            # Wait for the source to be indexed
            logger.info("Waiting 15s for source indexing...")
            await asyncio.sleep(15)

            # ── Step 3b: Verify what was actually ingested ──────────
            # A successful add proves NotebookLM accepted the URL, not that it
            # fetched the paper. Check before spending ~25 min of generation
            # (and a daily audio quota slot) on an error page.
            if on_status:
                await on_status("validating_source", "Verifying ingested content...")
            self._validate_source_content(notebook_id)

            # ── Step 4 & 5: Generate audio + wait + download via Python API ──
            # The CLI --wait has a hardcoded 300s timeout we can't change,
            # so we use the Python API directly for full timeout control.
            if on_status:
                await on_status(
                    "generating",
                    "Generating podcast audio (this may take up to 25 minutes)..."
                )

            prompt_truncated = prompt[:2000] if len(prompt) > 2000 else prompt

            await self._generate_and_download_via_api(
                notebook_id, prompt_truncated, mp3_path, on_status
            )

            logger.info(f"Downloaded: {mp3_path} ({mp3_path.stat().st_size:,} bytes)")

            # Delete the notebook now that the MP3 is safely downloaded — free
            # accounts cap at 100 notebooks, so we must not leak one per episode.
            self._delete_notebook(notebook_id)

            return {
                "mp3_path": str(mp3_path),
                "notebook_id": notebook_id,
                "duration_seconds": None,
            }

        except Exception as e:
            logger.error(f"Generation failed: {e}", exc_info=True)

            # Clean up the notebook so a failed run doesn't leak one either.
            # (No-op if creation itself failed and notebook_id is still None.)
            self._delete_notebook(notebook_id)

            raise

    async def _generate_and_download_via_api(
        self,
        notebook_id: str,
        prompt: str,
        mp3_path: Path,
        on_status: Optional[Callable] = None,
    ):
        """
        Generate audio without --wait, then poll by attempting download every 30s.

        The CLI --wait has a hardcoded 300s timeout we can't change, and the
        Python API class names are undocumented. So we:
          1. Start generation (no --wait) — returns immediately
          2. Wait 60s initial delay (generation always takes minutes)
          3. Try downloading every 30s until it succeeds or 30 min elapsed
        """
        # Step 4a: Start audio generation (no --wait, returns immediately)
        logger.info(f"Starting audio generation for notebook {notebook_id}...")
        generate_output = self._run_cli([
            "notebooklm", "generate", "audio", prompt,
        ], timeout=60)
        logger.info(f"Generation started: {generate_output[:200]}")

        # Step 4b: Wait then poll by trying to download
        logger.info("Waiting 90s before first download attempt (generation takes time)...")
        await asyncio.sleep(90)

        max_wait = 1800   # 30 minutes total
        poll_interval = 30  # try every 30 seconds
        elapsed = 90       # already waited 90s

        while elapsed < max_wait:
            logger.info(f"Download attempt ({elapsed}s elapsed)...")
            try:
                self._run_cli([
                    "notebooklm", "download", "audio", str(mp3_path),
                ], timeout=120)

                if mp3_path.exists() and mp3_path.stat().st_size > 1000:
                    logger.info(
                        f"Download succeeded after {elapsed}s! "
                        f"Size: {mp3_path.stat().st_size:,} bytes"
                    )
                    return
                else:
                    logger.info("File too small or missing, generation still in progress...")
                    if mp3_path.exists():
                        mp3_path.unlink()  # remove incomplete file
            except RuntimeError as e:
                logger.info(f"Not ready yet ({elapsed}s): {str(e)[:100]}")

            if on_status and elapsed % 120 == 0:
                minutes = elapsed // 60
                await on_status(
                    "generating",
                    f"Still generating... ({minutes} min elapsed)"
                )

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise RuntimeError(
            f"Audio generation timed out after {max_wait}s. "
            "The audio may still be generating on NotebookLM — "
            "check notebooklm.google.com manually."
        )

    def _download_pdf(self, url: str, filename_stem: str = "source") -> Path | None:
        """Download `url` to a temp file if it really serves a PDF, else None.

        Verifies the magic number rather than the Content-Type header: the
        publishers that block automated access serve an HTML bot wall, and
        some of them still label it as a PDF.
        """
        if not url.lower().startswith(("http://", "https://")):
            return None
        try:
            import requests
        except ImportError:
            logger.warning("requests unavailable — cannot use the file-upload path.")
            return None

        # Resolve symlinks: on macOS /tmp is a link to /private/tmp, and the
        # CLI refuses to upload through a symlink (its anti-exfiltration guard).
        tmp = (self.storage_dir.resolve() / f"{filename_stem}_{int(time.time())}.pdf")
        try:
            with requests.get(
                url,
                headers={"User-Agent": PDF_DOWNLOAD_UA, "Accept": "application/pdf,*/*"},
                timeout=PDF_DOWNLOAD_TIMEOUT,
                stream=True,
                allow_redirects=True,
            ) as r:
                if r.status_code != 200:
                    logger.info(f"Download returned HTTP {r.status_code}: {url[:80]}")
                    return None

                first = b""
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(65536):
                        if not chunk:
                            continue
                        if not first:
                            first = chunk
                            if not first.lstrip()[:5].startswith(b"%PDF"):
                                logger.info(
                                    "Downloaded content is not a PDF (likely a bot "
                                    f"wall): {url[:80]}"
                                )
                                return None
                        fh.write(chunk)
                        if fh.tell() > MAX_PDF_BYTES:
                            logger.warning(
                                f"PDF exceeds {MAX_PDF_BYTES // 1_000_000} MB, "
                                "abandoning download."
                            )
                            return None

            size = tmp.stat().st_size
            if size < MIN_PDF_BYTES:
                logger.info(f"Downloaded PDF is implausibly small ({size} bytes).")
                return None

            logger.info(f"Downloaded PDF: {size:,} bytes from {url[:80]}")
            return tmp
        except Exception as e:
            logger.info(f"Download failed ({type(e).__name__}): {str(e)[:120]}")
            return None
        finally:
            # Remove the partial file on every failure path above.
            if tmp.exists() and (not tmp.stat().st_size or tmp.stat().st_size < MIN_PDF_BYTES):
                tmp.unlink(missing_ok=True)

    def _add_source_as_file(
        self, notebook_id: str, paper_url: str, paper_title: str
    ) -> bool:
        """Download the paper and upload the bytes. True if the source landed.

        The temp file is named after the paper because NotebookLM derives an
        uploaded source's title from its filename, and that title is what the
        content guard and the logs read back.
        """
        stem = re.sub(r"[^A-Za-z0-9]+", "_", paper_title).strip("_")[:60] or "paper"
        pdf = self._download_pdf(paper_url, filename_stem=stem)
        if pdf is None:
            return False
        try:
            logger.info(f"Uploading {pdf.name} as a file source...")
            self._run_cli(
                [
                    "notebooklm", "source", "add", str(pdf),
                    "--type", "file",
                    "--title", paper_title[:200],
                    "-n", notebook_id,
                ],
                timeout=300,   # upload + server-side extraction
            )
            logger.info("Source uploaded from file.")
            return True
        except RuntimeError as e:
            logger.warning(f"File upload failed: {str(e)[:150]}")
            return False
        finally:
            pdf.unlink(missing_ok=True)

    def _validate_source_content(self, notebook_id: str) -> dict:
        """Verify the ingested source is the paper, not a publisher block page.

        Raises SourceContentError when the content is unusable. See the
        module-level notes on MIN_CONTENT_WORDS for why this is fatal.

        Returns the accepted source's {title, url, words} for logging.
        """
        listing = self._run_cli_json(
            ["notebooklm", "source", "list", "-n", notebook_id, "--json"],
            timeout=60,
        )
        sources = listing.get("sources") or []
        if not sources:
            raise SourceContentError(
                "NotebookLM reports no sources in the notebook after a "
                "successful add — nothing to generate an episode from."
            )

        # The pipeline adds exactly one source per episode; validate whichever
        # ones are present so a future multi-source change stays covered.
        accepted: dict | None = None
        for src in sources:
            title = (src.get("title") or "").strip()
            url = src.get("url") or ""
            status = (src.get("status") or "").lower()

            if status != "ready":
                raise SourceContentError(
                    f"Source did not finish processing (status={status!r}): {url}"
                )

            lowered_title = title.lower()
            for marker in BLOCK_TITLE_MARKERS:
                if marker in lowered_title:
                    raise SourceContentError(
                        f"The publisher served a block/error page instead of the "
                        f"paper. NotebookLM ingested it as a valid source titled "
                        f"{title!r} (matched {marker!r}). URL: {url}"
                    )

            detail = self._run_cli_json(
                [
                    "notebooklm", "source", "fulltext", src["id"],
                    "-n", notebook_id, "--json",
                ],
                timeout=90,
            )
            content = detail.get("content") or ""
            words = len(content.split())
            lowered_body = content.lower()

            for marker in BLOCK_BODY_MARKERS:
                if marker in lowered_body:
                    raise SourceContentError(
                        f"The fetched page is a bot/IP block, not the paper "
                        f"(matched {marker!r} in the body). "
                        f"Title={title!r}, {words} words. URL: {url}"
                    )

            if words < MIN_CONTENT_WORDS:
                raise SourceContentError(
                    f"Ingested source holds only {words} words "
                    f"(minimum {MIN_CONTENT_WORDS}) — too little to be the "
                    f"paper, and almost certainly an error page. "
                    f"Title={title!r}. URL: {url}"
                )

            if words < THIN_CONTENT_WORDS:
                logger.warning(
                    f"Source content is thin ({words} words, expected "
                    f">{THIN_CONTENT_WORDS}) — possibly abstract-only. "
                    f"Continuing. Title={title!r}"
                )

            logger.info(
                f"Source content validated: {words:,} words, title={title!r}"
            )
            if accepted is None:
                accepted = {"title": title, "url": url, "words": words}

        return accepted or {}

    def _run_cli_json(self, cmd: list[str], timeout: int = 60) -> dict:
        """Run a --json CLI command and parse its payload.

        Some subcommands print a human line before the JSON (``source
        fulltext`` emits ``Matched: <id> (<title>)``), so parse from the first
        brace rather than assuming stdout is pure JSON.
        """
        out = self._run_cli(cmd, timeout=timeout)
        start = out.find("{")
        if start == -1:
            raise RuntimeError(f"Expected JSON from {cmd[1:4]}, got: {out[:200]}")
        try:
            return json.loads(out[start:])
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Could not parse JSON from {cmd[1:4]}: {e}. Output: {out[:200]}"
            ) from e

    def _delete_notebook(self, notebook_id: str) -> None:
        """
        Delete a notebook (best-effort, never raises).

        NotebookLM caps free accounts at 100 owned notebooks, so we must not
        leak a notebook per episode. The MP3 is already downloaded before this
        is called, so the notebook is disposable. Delete operates on the active
        notebook, so we set it active first.
        """
        if not notebook_id:
            return
        try:
            logger.info(f"Deleting notebook {notebook_id}...")
            self._run_cli(["notebooklm", "use", notebook_id], timeout=15)
            self._run_cli(["notebooklm", "delete", "--yes"], timeout=30)
            logger.info("Notebook deleted.")
        except Exception as del_err:
            logger.warning(f"Notebook delete failed (non-fatal): {del_err}")

    def _run_cli(self, cmd: list[str], timeout: int = 120) -> str:
        """Run a notebooklm CLI command and return stdout."""
        display = " ".join(cmd[:8])
        logger.info(f"Running: {display}...")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ},
            )
            if result.stdout:
                logger.debug(f"stdout: {result.stdout[:300]}")
            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip()
                logger.error(f"CLI error (exit {result.returncode}): {error_msg}")
                raise RuntimeError(f"notebooklm CLI failed: {error_msg}")
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Command timed out after {timeout}s: {' '.join(cmd[:4])}"
            )

    def _parse_notebook_id(self, output: str) -> str:
        """
        Extract notebook ID from CLI output.

        notebooklm create prints something like:
          Created notebook 'Title' with ID: abc123def456
          or just: abc123def456
        """
        if not output:
            raise RuntimeError("Empty output from 'notebooklm create'")

        # Try JSON first
        try:
            data = json.loads(output)
            for key in ["id", "notebook_id", "notebookId", "project_id"]:
                if key in data:
                    return str(data[key])
            if isinstance(data, str):
                return data
        except (json.JSONDecodeError, TypeError):
            pass

        # Look for "ID: <value>" pattern
        match = re.search(r'(?:id|ID):\s*([A-Za-z0-9_-]+)', output)
        if match:
            return match.group(1)

        # Look for a UUID-like pattern
        match = re.search(
            r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
            output, re.IGNORECASE
        )
        if match:
            return match.group(0)

        # Look for a long hex/alphanumeric ID (typical NotebookLM project IDs)
        match = re.search(r'\b([a-f0-9]{16,})\b', output, re.IGNORECASE)
        if match:
            return match.group(1)

        # Last resort: first non-empty word/token from output
        first_line = output.strip().split('\n')[0].strip()
        tokens = first_line.split()
        if tokens:
            # Try the last token (often the ID comes at the end)
            return tokens[-1].strip("'\".,")

        raise RuntimeError(
            f"Could not parse notebook ID from output: {output[:300]}"
        )

    async def health_check(self) -> bool:
        """Verify auth is still valid by listing notebooks."""
        try:
            self._run_cli(["notebooklm", "list"], timeout=30)
            return True
        except Exception:
            return False
