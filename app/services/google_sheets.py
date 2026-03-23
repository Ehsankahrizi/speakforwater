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

    @staticmethod
    def _find_column(row: dict, target: str, default=""):
        """
        Find a value in a row dict using flexible header matching.
        Handles trailing spaces, different casing, underscores vs spaces, etc.
        """
        # Normalise the target: lowercase, strip, replace spaces with underscores
        norm_target = target.strip().lower().replace(" ", "_")

        for key, value in row.items():
            norm_key = str(key).strip().lower().replace(" ", "_")
            if norm_key == norm_target:
                return value

        return default

    def get_next_queued(self) -> dict | None:
        """
        Find the first row where status = 'queued'.
        Returns dict with row data and row_number, or None if nothing queued.
        """
        all_rows = self.sheet.get_all_records()

        # Log headers once to help debug column name issues
        if all_rows:
            headers = list(all_rows[0].keys())
            logger.info(f"Sheet headers: {headers}")

        for i, row in enumerate(all_rows):
            status_val = str(self._find_column(row, "status")).strip().lower()
            if status_val == "queued":
                row_number = i + 2  # +1 for header, +1 for 1-indexed

                # Read episode_number with flexible matching
                ep_raw = self._find_column(row, "episode_number", default=0)
                try:
                    ep_num = int(ep_raw)
                except (ValueError, TypeError):
                    ep_num = 0

                # Fallback: if ep_num is still 0, read directly from column E
                if ep_num == 0:
                    try:
                        cell_val = self.sheet.cell(row_number, 5).value  # Column E
                        if cell_val is not None:
                            ep_num = int(cell_val)
                            logger.info(f"Episode number from column E fallback: {ep_num}")
                    except (ValueError, TypeError):
                        pass

                logger.info(f"Queued row {row_number}: episode_number={ep_num}")

                return {
                    "row_number": row_number,
                    "date": str(self._find_column(row, "date")),
                    "paper_url": str(self._find_column(row, "paper_url")),
                    "paper_title": str(self._find_column(row, "paper_title")),
                    "status": str(self._find_column(row, "status")),
                    "episode_number": ep_num,
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
