"""
NotebookLM automation using the notebooklm-py SDK.

Uses the unofficial Python SDK (notebooklm-py) instead of Playwright browser
automation. This works in headless CI/CD environments like GitHub Actions
by using NOTEBOOKLM_AUTH_JSON for authentication.

Auth flow:
  1. Run `notebooklm login` on your local machine once (opens browser)
  2. Export the auth JSON: `notebooklm auth export`
  3. Store it as a GitHub Actions secret: NOTEBOOKLM_AUTH_JSON
  4. The SDK uses these cookies to make direct API calls (no browser needed)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
      2. Add paper URL as a source
      3. Generate audio overview with custom prompt
      4. Wait for generation to complete
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
        """Validate authentication."""
        if not self.auth_json:
            raise RuntimeError(
                "No auth JSON provided. Set NOTEBOOKLM_AUTH_JSON environment variable. "
                "To get it: run 'notebooklm login' locally, then 'notebooklm auth export'."
            )

        # Write auth JSON to the file location the SDK expects
        auth_dir = Path.home() / ".notebooklm"
        auth_dir.mkdir(parents=True, exist_ok=True)
        auth_file = auth_dir / "storage_state.json"
        auth_file.write_text(self.auth_json)
        logger.info("Auth JSON written to ~/.notebooklm/storage_state.json")

        # Verify auth is valid
        result = self._run_cli(["notebooklm", "auth", "check"])
        if "expired" in result.lower() or "invalid" in result.lower():
            raise RuntimeError(
                f"NotebookLM auth is expired or invalid. "
                f"Please re-run 'notebooklm login' locally and update the secret. "
                f"Output: {result}"
            )

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

        try:
            # ── Step 1: Create a new notebook ──────────────────────
            if on_status:
                await on_status("creating_notebook", "Creating new notebook...")
            logger.info("Creating new notebook...")

            create_output = self._run_cli([
                "notebooklm", "notebook", "create",
                "--title", f"SpeakForWater Ep{episode_number}: {paper_title[:50]}",
                "--json"
            ])

            notebook_id = self._parse_notebook_id(create_output)
            logger.info(f"Created notebook: {notebook_id}")

            # ── Step 2: Add paper URL as source ────────────────────
            if on_status:
                await on_status("adding_source", f"Adding source: {paper_url}")
            logger.info(f"Adding source URL: {paper_url}")

            self._run_cli([
                "notebooklm", "source", "add",
                "--notebook", notebook_id,
                "--url", paper_url,
            ])

            # Wait a moment for the source to be indexed
            logger.info("Waiting for source indexing...")
            await asyncio.sleep(10)

            # ── Step 3: Generate audio overview ────────────────────
            if on_status:
                await on_status("generating", "Generating podcast audio (this may take several minutes)...")
            logger.info("Starting audio generation...")

            # Write prompt to a temp file (CLI may have length limits)
            prompt_file = Path("/tmp/speakforwater_prompt.txt")
            prompt_file.write_text(prompt)

            generate_output = self._run_cli([
                "notebooklm", "generate", "audio",
                "--notebook", notebook_id,
                "--instructions", prompt,
                "--wait",
                "--json",
            ], timeout=600)  # 10 minute timeout

            logger.info(f"Generation output: {generate_output[:200]}")

            # ── Step 4: Download the audio ─────────────────────────
            if on_status:
                await on_status("downloading", "Downloading MP3...")

            filename = f"ep{str(episode_number).zfill(3)}.mp3"
            mp3_path = self.storage_dir / filename

            # Try to extract download URL from the output and download
            download_output = self._run_cli([
                "notebooklm", "artifact", "download",
                "--notebook", notebook_id,
                "--type", "audio",
                "--output", str(mp3_path),
            ], timeout=120)

            if not mp3_path.exists():
                # Fallback: try listing artifacts to find the audio file
                list_output = self._run_cli([
                    "notebooklm", "artifact", "list",
                    "--notebook", notebook_id,
                    "--json",
                ])
                logger.info(f"Artifacts: {list_output[:500]}")
                raise RuntimeError(
                    f"MP3 file not found at {mp3_path}. "
                    f"Artifact list: {list_output[:200]}"
                )

            logger.info(f"Downloaded: {mp3_path} ({mp3_path.stat().st_size} bytes)")

            return {
                "mp3_path": str(mp3_path),
                "notebook_id": notebook_id,
                "duration_seconds": None,
            }

        except Exception as e:
            logger.error(f"Generation failed: {e}", exc_info=True)

            # Try to clean up the notebook
            if notebook_id:
                try:
                    self._run_cli([
                        "notebooklm", "notebook", "delete",
                        "--notebook", notebook_id, "--yes"
                    ])
                except Exception:
                    pass

            raise

    def _run_cli(self, cmd: list[str], timeout: int = 120) -> str:
        """Run a notebooklm CLI command and return stdout."""
        logger.info(f"Running: {' '.join(cmd[:6])}...")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ},
            )
            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip()
                logger.error(f"CLI error (exit {result.returncode}): {error_msg}")
                raise RuntimeError(f"notebooklm CLI failed: {error_msg}")
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(cmd[:4])}")

    def _parse_notebook_id(self, output: str) -> str:
        """Extract notebook ID from CLI JSON output."""
        try:
            data = json.loads(output)
            # Try common field names
            for key in ["id", "notebook_id", "notebookId", "project_id"]:
                if key in data:
                    return str(data[key])
            # If it's a string, use it directly
            if isinstance(data, str):
                return data
        except json.JSONDecodeError:
            pass

        # Fallback: look for a UUID-like pattern in the output
        import re
        match = re.search(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', output)
        if match:
            return match.group(0)

        # Last resort: return the full output trimmed
        if output.strip():
            return output.strip().split('\n')[0].strip()

        raise RuntimeError(f"Could not parse notebook ID from: {output[:200]}")

    async def health_check(self) -> bool:
        """Verify auth is still valid."""
        try:
            result = self._run_cli(["notebooklm", "auth", "check"])
            return "expired" not in result.lower() and "invalid" not in result.lower()
        except Exception:
            return False
