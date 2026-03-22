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

        # Verify auth is valid
        try:
            result = self._run_cli(["notebooklm", "auth", "check"])
            if "expired" in result.lower() or "invalid" in result.lower():
                raise RuntimeError(
                    f"NotebookLM auth is expired or invalid. "
                    f"Please re-run 'notebooklm login' locally and update the secret. "
                    f"Output: {result}"
                )
            logger.info(f"Auth check: {result[:100]}")
        except RuntimeError as e:
            # auth check command might not exist in all versions — try listing notebooks
            if "No such command" in str(e) or "no such command" in str(e).lower():
                logger.warning("'auth check' not available, trying 'notebooklm list'...")
                try:
                    self._run_cli(["notebooklm", "list"], timeout=30)
                    logger.info("Auth verified via notebook list")
                except RuntimeError as e2:
                    raise RuntimeError(
                        f"Authentication failed. Please re-run 'notebooklm login'. Error: {e2}"
                    )
            else:
                raise

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

            # ── Step 3: Add paper URL as source ─────────────────────
            if on_status:
                await on_status("adding_source", f"Adding source: {paper_url}")
            logger.info(f"Adding source URL: {paper_url}")

            self._run_cli(["notebooklm", "source", "add", paper_url])

            # Wait for the source to be indexed
            logger.info("Waiting 15s for source indexing...")
            await asyncio.sleep(15)

            # ── Step 4: Generate audio overview ──────────────────────
            if on_status:
                await on_status(
                    "generating",
                    "Generating podcast audio (this may take several minutes)..."
                )
            logger.info("Starting audio generation (--wait enabled, up to 25 min)...")

            # Truncate prompt if needed (CLI may have arg length limits)
            prompt_truncated = prompt[:2000] if len(prompt) > 2000 else prompt

            generate_output = self._run_cli([
                "notebooklm", "generate", "audio",
                prompt_truncated,
                "--wait",
                "--timeout", "1800",  # Tell SDK to wait up to 30 min
            ], timeout=1800)  # subprocess timeout also 30 min

            logger.info(f"Generation complete: {generate_output[:200]}")

            # ── Step 5: Download the audio ───────────────────────────
            if on_status:
                await on_status("downloading", "Downloading MP3...")
            logger.info(f"Downloading MP3 to {mp3_path}...")

            self._run_cli([
                "notebooklm", "download", "audio",
                str(mp3_path),
            ], timeout=120)

            if not mp3_path.exists():
                # Fallback: try with --output flag syntax
                logger.warning("First download attempt failed, trying --output flag...")
                self._run_cli([
                    "notebooklm", "download", "audio",
                    "--output", str(mp3_path),
                ], timeout=120)

            if not mp3_path.exists():
                raise RuntimeError(
                    f"MP3 file not found at {mp3_path} after download. "
                    "Check GitHub Actions logs for the download command output."
                )

            logger.info(f"Downloaded: {mp3_path} ({mp3_path.stat().st_size:,} bytes)")

            return {
                "mp3_path": str(mp3_path),
                "notebook_id": notebook_id,
                "duration_seconds": None,
            }

        except Exception as e:
            logger.error(f"Generation failed: {e}", exc_info=True)

            # Try to clean up the notebook (delete uses the active notebook set by 'use')
            if notebook_id:
                try:
                    logger.info(f"Cleaning up notebook {notebook_id}...")
                    # Ensure the notebook is set as active first
                    self._run_cli(["notebooklm", "use", notebook_id], timeout=15)
                    self._run_cli(["notebooklm", "delete", "--yes"], timeout=30)
                    logger.info("Notebook deleted.")
                except Exception as cleanup_err:
                    logger.warning(f"Cleanup failed (non-fatal): {cleanup_err}")

            raise

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
