"""
Core Playwright automation for NotebookLM.

Replicates Ehsan's manual workflow:
  1. Navigate to NotebookLM
  2. Create a new notebook
  3. Add paper URL as a source (via "Websites" option)
  4. Wait for source to be indexed
  5. Click "Audio..." in the Studio panel to open Audio Overview
  6. Configure format (Deep Dive), language, length, and custom prompt
  7. Click "Generate" and wait for completion (up to 10 min)
  8. Download the generated MP3

Authentication is handled via a Netscape-format cookies.txt file
exported from the user's browser while logged into Google.
"""

import asyncio
import json
import logging
import re
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
)

from app.config import settings
from app.models.schemas import AudioFormat, AudioLength, TaskStatus

logger = logging.getLogger(__name__)


# ── Cookie parser ──────────────────────────────────────────────────────

def parse_cookies_txt(cookies_path: Path) -> list[dict]:
    """
    Parse a Netscape-format cookies.txt into Playwright cookie dicts.
    Handles the standard tab-separated format exported by browser extensions
    like "Get cookies.txt LOCALLY".
    """
    cookies = []
    if not cookies_path.exists():
        logger.warning(f"Cookies file not found: {cookies_path}")
        return cookies

    with open(cookies_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue

            domain, _, path, secure, expires, name, value = parts[:7]

            cookie = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": secure.upper() == "TRUE",
                "httpOnly": False,
            }

            # Handle expiry
            try:
                exp = int(expires)
                if exp > 0:
                    cookie["expires"] = exp
            except ValueError:
                pass

            cookies.append(cookie)

    logger.info(f"Loaded {len(cookies)} cookies from {cookies_path}")
    return cookies


# ── Status callback type ───────────────────────────────────────────────

StatusCallback = callable  # async def callback(status: TaskStatus, message: str)


# ── Main automator class ──────────────────────────────────────────────

class NotebookLMAutomator:
    """
    Drives a headless Chromium browser to automate NotebookLM
    podcast generation.
    """

    def __init__(self, cookies_path: Path | None = None):
        self.cookies_path = cookies_path or settings.cookies_path
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._ready = False

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self):
        """Launch browser and load authentication cookies."""
        logger.info("Starting Playwright browser...")
        self._playwright = await async_playwright().start()

        launch_kwargs = {
            "headless": settings.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }

        if settings.browserless_url:
            # Connect to a remote browserless instance
            self._browser = await self._playwright.chromium.connect(
                settings.browserless_url
            )
            logger.info(f"Connected to browserless at {settings.browserless_url}")
        else:
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            logger.info("Launched local Chromium")

        # Create context with realistic viewport and user-agent
        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        # Load Google authentication cookies
        cookies = parse_cookies_txt(self.cookies_path)
        if cookies:
            await self._context.add_cookies(cookies)
            logger.info("Authentication cookies loaded")
        else:
            logger.warning("No cookies loaded — authentication will likely fail")

        self._ready = True

    async def stop(self):
        """Shut down browser cleanly."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._ready = False
        logger.info("Browser stopped")

    @property
    def is_ready(self) -> bool:
        return self._ready

    # ── Main generation pipeline ──────────────────────────────────

    async def generate_podcast(
        self,
        paper_url: str,
        paper_title: str,
        episode_number: int,
        prompt: str,
        audio_format: AudioFormat = AudioFormat.DEEP_DIVE,
        language: str = "English",
        length: AudioLength = AudioLength.DEFAULT,
        on_status: StatusCallback | None = None,
    ) -> dict:
        """
        Full pipeline: create notebook → add source → configure audio → generate → download.

        Returns dict with:
            - mp3_path: Path to the downloaded MP3 file
            - notebook_id: The NotebookLM notebook ID
            - duration_seconds: Estimated duration (if available)
        """
        if not self._ready:
            raise RuntimeError("Automator not started. Call start() first.")

        page = await self._context.new_page()
        notebook_id = None

        try:
            # ── Step 1: Create new notebook ────────────────────────
            await self._report(on_status, TaskStatus.CREATING_NOTEBOOK, "Creating new notebook...")
            notebook_id = await self._create_notebook(page)
            logger.info(f"Created notebook: {notebook_id}")

            # ── Step 2: Add paper URL as source ────────────────────
            await self._report(on_status, TaskStatus.ADDING_SOURCE, f"Adding source: {paper_url}")
            await self._add_url_source(page, paper_url)
            logger.info("Source added and indexed")

            # ── Step 3: Configure and generate audio overview ──────
            await self._report(on_status, TaskStatus.CONFIGURING_AUDIO, "Configuring audio overview...")
            await self._open_audio_overview(page)
            await self._configure_audio(page, audio_format, language, length, prompt)
            logger.info("Audio overview configured")

            # ── Step 4: Generate ───────────────────────────────────
            await self._report(on_status, TaskStatus.GENERATING, "Generating podcast audio (this may take several minutes)...")
            await self._click_generate(page)
            logger.info("Generation started — waiting for completion")

            # ── Step 5: Wait for generation and download ───────────
            await self._report(on_status, TaskStatus.DOWNLOADING, "Downloading MP3...")
            filename = f"ep{str(episode_number).zfill(3)}.mp3"
            mp3_path = await self._wait_and_download(page, filename)
            logger.info(f"Downloaded: {mp3_path}")

            # ── Optional: delete notebook to stay tidy ─────────────
            if not settings.keep_notebooks:
                await self._delete_notebook(page, notebook_id)

            return {
                "mp3_path": str(mp3_path),
                "notebook_id": notebook_id,
                "duration_seconds": None,  # NotebookLM doesn't expose this directly
            }

        except Exception as e:
            logger.error(f"Generation failed: {e}", exc_info=True)
            raise
        finally:
            await page.close()

    # ── Step implementations ──────────────────────────────────────

    async def _create_notebook(self, page: Page) -> str:
        """Navigate to NotebookLM home and create a new notebook. Returns notebook ID."""
        await page.goto(settings.notebooklm_url, wait_until="networkidle")
        await asyncio.sleep(2)

        # Check if we're logged in by looking for the main UI
        if "accounts.google.com" in page.url:
            raise RuntimeError(
                "Redirected to Google login — cookies are expired or invalid. "
                "Please re-export cookies.txt from your browser."
            )

        # Click "Create new notebook" or "+ Create new"
        create_btn = page.locator('text=Create new notebook').first
        if not await create_btn.is_visible():
            create_btn = page.locator('button:has-text("Create new")').first

        await create_btn.click()
        await page.wait_for_url("**/notebook/**", timeout=15000)
        await asyncio.sleep(2)

        # Extract notebook ID from URL
        # URL pattern: https://notebooklm.google.com/notebook/NOTEBOOK_ID
        notebook_id = page.url.split("/notebook/")[-1].split("?")[0]
        return notebook_id

    async def _add_url_source(self, page: Page, url: str):
        """Click 'Add sources' → 'Websites' → paste URL → insert."""
        # Click "+ Add sources" button
        add_sources_btn = page.locator('button:has-text("Add sources")').first
        if await add_sources_btn.is_visible():
            await add_sources_btn.click()
            await asyncio.sleep(1)

        # Click "Websites" option in the source picker dialog
        websites_btn = page.locator('text=Websites').first
        await websites_btn.wait_for(state="visible", timeout=10000)
        await websites_btn.click()
        await asyncio.sleep(1)

        # Find the URL input field and paste the paper URL
        url_input = page.locator('input[type="url"], input[placeholder*="URL"], input[placeholder*="url"], input[placeholder*="Paste"]').first
        if not await url_input.is_visible():
            # Fallback: look for any text input in the dialog
            url_input = page.locator('input[type="text"]').last
        await url_input.fill(url)
        await asyncio.sleep(0.5)

        # Click "Insert" or "Submit" or the arrow button
        insert_btn = page.locator('button:has-text("Insert")').first
        if not await insert_btn.is_visible():
            insert_btn = page.locator('button:has-text("Submit")').first
        if not await insert_btn.is_visible():
            # Try the arrow/send button
            insert_btn = page.locator('button[aria-label="Submit"], button[aria-label="Insert"]').first

        await insert_btn.click()

        # Wait for the source to appear in the Sources panel
        # The source shows as a list item in the left panel
        await page.wait_for_selector(
            '.source-item, [class*="source"], text=/1 source/',
            timeout=60000,
        )
        await asyncio.sleep(3)  # Give it time to finish indexing

        # Close the dialog if still open
        close_btn = page.locator('button[aria-label="Close"]').first
        if await close_btn.is_visible():
            await close_btn.click()
            await asyncio.sleep(1)

    async def _open_audio_overview(self, page: Page):
        """Click the 'Audio...' button in the Studio panel on the right."""
        # The Studio panel has buttons: Audio..., Slide Deck, Video..., etc.
        audio_btn = page.locator('text=Audio').first
        await audio_btn.wait_for(state="visible", timeout=10000)
        await audio_btn.click()
        await asyncio.sleep(2)

        # Wait for the "Customize Audio Overview" dialog to appear
        await page.wait_for_selector(
            'text=Customize Audio Overview, text=Audio Overview',
            timeout=10000,
        )

    async def _configure_audio(
        self,
        page: Page,
        audio_format: AudioFormat,
        language: str,
        length: AudioLength,
        prompt: str,
    ):
        """Set format, language, length, and custom prompt in the Audio Overview dialog."""

        # ── Select format ──────────────────────────────────────────
        format_labels = {
            AudioFormat.DEEP_DIVE: "Deep Dive",
            AudioFormat.BRIEF: "Brief",
            AudioFormat.CRITIQUE: "Critique",
            AudioFormat.DEBATE: "Debate",
        }
        format_label = format_labels[audio_format]
        format_option = page.locator(f'text="{format_label}"').first
        if await format_option.is_visible():
            await format_option.click()
            await asyncio.sleep(0.5)

        # ── Select language ────────────────────────────────────────
        # Language is a dropdown/select element
        lang_dropdown = page.locator('select, [role="listbox"]').first
        if await lang_dropdown.is_visible():
            try:
                await lang_dropdown.select_option(label=language)
            except Exception:
                # If it's a custom dropdown, click and find the option
                await lang_dropdown.click()
                await asyncio.sleep(0.5)
                lang_option = page.locator(f'text="{language}"').first
                if await lang_option.is_visible():
                    await lang_option.click()
            await asyncio.sleep(0.5)

        # ── Select length ──────────────────────────────────────────
        length_labels = {
            AudioLength.SHORT: "Short",
            AudioLength.DEFAULT: "Default",
            AudioLength.LONG: "Long",
        }
        length_label = length_labels[length]
        length_option = page.locator(f'text="{length_label}"').first
        if await length_option.is_visible():
            await length_option.click()
            await asyncio.sleep(0.5)

        # ── Enter custom prompt ────────────────────────────────────
        # The prompt textarea is labeled "What should the AI hosts focus on..."
        prompt_field = page.locator(
            'textarea, [contenteditable="true"]'
        ).last
        await prompt_field.wait_for(state="visible", timeout=5000)

        # Clear existing placeholder/content and type the prompt
        await prompt_field.click()
        await page.keyboard.press("Control+a")
        await prompt_field.fill(prompt)
        await asyncio.sleep(0.5)

        logger.info(f"Audio configured: format={format_label}, lang={language}, length={length_label}")

    async def _click_generate(self, page: Page):
        """Click the Generate button to start audio generation."""
        generate_btn = page.locator('button:has-text("Generate")').first
        await generate_btn.wait_for(state="visible", timeout=5000)
        await generate_btn.click()
        logger.info("Generate button clicked")

    async def _wait_and_download(self, page: Page, filename: str) -> Path:
        """
        Wait for audio generation to complete and download the MP3.

        NotebookLM shows a loading/progress indicator while generating.
        Once done, a play button and download option appear.
        Generation typically takes 3-8 minutes.
        """
        timeout_ms = settings.browser_timeout * 1000
        start_time = time.time()

        # Wait for the generation to complete
        # Look for indicators that generation is done:
        # - A "Download" button appears
        # - A play button appears
        # - The audio player becomes visible
        while True:
            elapsed = time.time() - start_time
            if elapsed > settings.browser_timeout:
                raise TimeoutError(
                    f"Audio generation timed out after {settings.browser_timeout}s"
                )

            # Check for completion indicators
            download_btn = page.locator(
                'button[aria-label="Download"], '
                'button:has-text("Download"), '
                'a[download], '
                '[class*="download"]'
            ).first

            if await download_btn.is_visible():
                break

            # Check for error state
            error_el = page.locator('text=error, text=failed, text=try again').first
            if await error_el.is_visible():
                error_text = await error_el.text_content()
                raise RuntimeError(f"NotebookLM generation error: {error_text}")

            # Log progress
            if int(elapsed) % 30 == 0 and elapsed > 0:
                logger.info(f"Still generating... ({int(elapsed)}s elapsed)")

            await asyncio.sleep(5)

        # Download the MP3
        save_path = settings.storage_dir / filename

        # Try to intercept the download
        try:
            async with page.expect_download(timeout=30000) as download_info:
                await download_btn.click()
            download = await download_info.value
            await download.save_as(str(save_path))
        except PlaywrightTimeout:
            # Fallback: try right-click → save, or look for audio src
            audio_el = page.locator("audio source, audio[src]").first
            if await audio_el.is_visible():
                audio_url = await audio_el.get_attribute("src")
                if audio_url:
                    # Download via a new request
                    response = await page.request.get(audio_url)
                    with open(save_path, "wb") as f:
                        f.write(await response.body())
            else:
                raise RuntimeError("Could not find audio download link")

        logger.info(f"MP3 saved to {save_path}")
        return save_path

    async def _delete_notebook(self, page: Page, notebook_id: str):
        """Navigate back to home and delete the notebook to keep things tidy."""
        try:
            await page.goto(settings.notebooklm_url, wait_until="networkidle")
            await asyncio.sleep(2)

            # Find the notebook's menu button (three dots)
            # This is fragile and may need updating if the UI changes
            notebooks = page.locator('[class*="notebook-card"], [class*="NotebookCard"]')
            count = await notebooks.count()

            for i in range(count):
                card = notebooks.nth(i)
                card_text = await card.text_content()
                if notebook_id in (await card.get_attribute("href") or ""):
                    menu_btn = card.locator('button[aria-label="More"], button:has-text("⋮")').first
                    await menu_btn.click()
                    await asyncio.sleep(0.5)
                    delete_btn = page.locator('text=Delete').first
                    await delete_btn.click()
                    await asyncio.sleep(0.5)
                    confirm_btn = page.locator('button:has-text("Delete")').last
                    await confirm_btn.click()
                    logger.info(f"Deleted notebook {notebook_id}")
                    break
        except Exception as e:
            logger.warning(f"Failed to delete notebook {notebook_id}: {e}")

    # ── Utility ───────────────────────────────────────────────────

    async def _report(self, callback: StatusCallback | None, status: TaskStatus, message: str):
        """Report progress via callback if provided."""
        if callback:
            await callback(status, message)
        logger.info(f"[{status.value}] {message}")

    async def health_check(self) -> bool:
        """Verify browser is alive and cookies are valid."""
        if not self._ready:
            return False
        try:
            page = await self._context.new_page()
            await page.goto(settings.notebooklm_url, timeout=15000)
            is_logged_in = "accounts.google.com" not in page.url
            await page.close()
            return is_logged_in
        except Exception:
            return False
