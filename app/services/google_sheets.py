"""
Google Sheets integration — reads the episode queue and updates status.

Uses a Google Service Account for authentication (no browser needed).
The service account JSON key is stored as a GitHub Actions secret.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


class EpisodeQueue:
    """
    Reads and updates the Google Sheet episode queue.

    Expected columns:
      A: date           (scheduled publish date, e.g. 2026-03-22)
      B: paper_url      (full URL to the paper)
      C: paper_title    (short title)
      D: status         (queued / processing / published / failed)
      E: episode_number (integer)
      F: mp3_url        (filled after publish)
      G: published_at   (timestamp)
    """

    def __init__(self, credentials_json: str, spreadsheet_id: str, sheet_name: str = "Sheet1"):
        """
        Args:
            credentials_json: JSON string of the Google service account key
            spreadsheet_id: The ID from the Google Sheet URL
            sheet_name: Name of the worksheet tab
        """
        creds_dict = json.loads(credentials_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        client = gspread.authorize(creds)

        self.spreadsheet = client.open_by_key(spreadsheet_id)
        self.sheet = self.spreadsheet.worksheet(sheet_name)
        logger.info(f"Connected to sheet: {self.spreadsheet.title} / {sheet_name}")

    def get_next_queued(self) -> dict | None:
        """
        Find the first row where status = 'queued'.
        Returns dict with row data and row_number, or None if nothing queued.
        """
        all_rows = self.sheet.get_all_records()

        for i, row in enumerate(all_rows):
            if str(row.get("status", "")).strip().lower() == "queued":
                row_number = i + 2  # +1 for header, +1 for 1-indexed
                return {
                    "row_number": row_number,
                    "date": str(row.get("date", "")),
                    "paper_url": str(row.get("paper_url", "")),
                    "paper_title": str(row.get("paper_title", "")),
                    "status": str(row.get("status", "")),
                    "episode_number": int(row.get("episode_number", 0)),
                }

        logger.info("No queued episodes found")
        return None

    def update_status(self, row_number: int, status: str, mp3_url: str = "", published_at: str = ""):
        """Update the status (and optionally mp3_url, published_at) for a row."""
        # Column D = status (col 4), F = mp3_url (col 6), G = published_at (col 7)
        self.sheet.update_cell(row_number, 4, status)

        if mp3_url:
            self.sheet.update_cell(row_number, 6, mp3_url)
        if published_at:
            self.sheet.update_cell(row_number, 7, published_at)

        logger.info(f"Row {row_number} updated: status={status}")

    def mark_processing(self, row_number: int):
        self.update_status(row_number, "processing")

    def mark_published(self, row_number: int, mp3_url: str):
        now = datetime.now(timezone.utc).isoformat()
        self.update_status(row_number, "published", mp3_url=mp3_url, published_at=now)

    def mark_failed(self, row_number: int, error: str = ""):
        self.update_status(row_number, f"failed: {error}" if error else "failed")
