"""
Application configuration — loaded from environment variables / .env file.
"""

from __future__ import annotations

import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # API
    api_key: str = "changeme-generate-a-real-key"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    debug: bool = False

    # Paths
    base_dir: Path = Path(__file__).resolve().parent.parent
    storage_dir: Path = base_dir / "app" / "storage" / "downloads"
    cookies_path: Path = base_dir / "cookies.txt"

    # Playwright / Browser
    headless: bool = True
    browser_timeout: int = 600  # seconds — max time to wait for audio generation
    browserless_url: str | None = None  # e.g. ws://browserless:3000

    # NotebookLM
    notebooklm_url: str = "https://notebooklm.google.com"

    # Cleanup
    keep_notebooks: bool = False  # delete notebook after generation to stay tidy
    max_stored_mp3s: int = 50  # auto-delete oldest files beyond this count

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# Ensure storage directory exists
settings.storage_dir.mkdir(parents=True, exist_ok=True)
