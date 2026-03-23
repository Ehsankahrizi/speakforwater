#!/usr/bin/env python3
"""
SpeakForWater — Paper Search Pipeline

Runs daily (separate from podcast generation) to find new
open-access water research papers and add them to the Google Sheet.

Steps:
  1. Load keywords from config/keywords.yml
  2. Load journal sources from config/journals.yml
  3. Search OpenAlex API for recent papers
  4. Check Google Sheet for duplicates
  5. Add new papers with status "queued"

Usage:
  python search_papers.py

Environment variables:
  GOOGLE_CREDENTIALS_JSON  — Service account JSON for Google Sheets
  SPREADSHEET_ID           — Google Sheet ID
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from app.services.paper_search import load_keywords, load_journals, search_papers

# ── Logging ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("paper-search")

# ── Config ─────────────────────────────────────────────────────────────

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = os.environ.get("SHEET_NAME", "Sheet1")
MAX_PAPERS = int(os.environ.get("MAX_PAPERS", "10"))
DAYS_BACK = int(os.environ.get("DAYS_BACK", "90"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def validate_env():
    """Check required environment variables."""
    missing = []
    if not GOOGLE_CREDENTIALS_JSON:
        missing.append("GOOGLE_CREDENTIALS_JSON")
    if not SPREADSHEET_ID:
        missing.append("SPREADSHEET_ID")
    if missing:
        logger.error(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)


def get_sheet():
    """Connect to Google Sheet."""
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    sheet = spreadsheet.worksheet(SHEET_NAME)
    logger.info(f"Connected to sheet: {spreadsheet.title} / {SHEET_NAME}")
    return sheet


def get_existing_urls(sheet) -> set[str]:
    """Get all paper URLs already in the Sheet to avoid duplicates."""
    all_rows = sheet.get_all_records()
    urls = set()
    titles = set()
    for row in all_rows:
        url = str(row.get("paper_url", "")).strip().lower()
        title = str(row.get("paper_title", "")).strip().lower()
        if url:
            urls.add(url)
        if title:
            titles.add(title)
    logger.info(f"Found {len(urls)} existing URLs in Sheet")
    return urls, titles


def get_next_episode_number(sheet) -> int:
    """Find the highest episode number in the Sheet and return next one."""
    all_rows = sheet.get_all_records()
    max_ep = 0
    for row in all_rows:
        try:
            ep = int(row.get("episode_number", 0))
            if ep > max_ep:
                max_ep = ep
        except (ValueError, TypeError):
            pass
    return max_ep + 1


def add_papers_to_sheet(sheet, papers: list[dict], existing_urls: set, existing_titles: set, start_episode: int) -> int:
    """
    Add new papers to the Google Sheet.
    Returns number of papers added.
    """
    added = 0
    episode_num = start_episode

    for paper in papers:
        # Skip duplicates by URL
        url_lower = paper["url"].strip().lower()
        if url_lower in existing_urls:
            logger.info(f"  Skipping (duplicate URL): {paper['title'][:60]}")
            continue

        # Skip duplicates by title
        title_lower = paper["title"].strip().lower()
        if title_lower in existing_titles:
            logger.info(f"  Skipping (duplicate title): {paper['title'][:60]}")
            continue

        # Add new row
        row = [
            paper.get("date", ""),          # A: date
            paper["url"],                    # B: paper_url
            paper["title"],                  # C: paper_title
            "queued",                        # D: status
            episode_num,                     # E: episode_number
            "",                              # F: mp3_url
            "",                              # G: published_at
        ]

        try:
            sheet.append_row(row, value_input_option="USER_ENTERED")
            existing_urls.add(url_lower)
            existing_titles.add(title_lower)
            logger.info(f"  Added ep#{episode_num}: {paper['title'][:60]}...")
            logger.info(f"    URL: {paper['url'][:80]}")
            logger.info(f"    Journal: {paper.get('journal', 'unknown')} | OA: {paper.get('is_open_access', '?')}")
            added += 1
            episode_num += 1
        except Exception as e:
            logger.error(f"  Failed to add paper: {e}")

    return added


def main():
    logger.info("=" * 60)
    logger.info("  SpeakForWater — Paper Search Pipeline")
    logger.info("=" * 60)

    validate_env()

    # Load config
    keywords = load_keywords()
    journals = load_journals()

    if not keywords:
        logger.error("No keywords configured. Edit config/keywords.yml")
        sys.exit(1)

    # Search for papers
    logger.info(f"\nSearching for up to {MAX_PAPERS} papers (last {DAYS_BACK} days)...")
    papers = search_papers(
        keywords=keywords,
        journals=journals,
        max_results=MAX_PAPERS,
        days_back=DAYS_BACK,
        open_access_only=True,
    )

    if not papers:
        logger.info("No papers found. Try adjusting keywords or date range.")
        return

    logger.info(f"\nFound {len(papers)} papers. Checking for duplicates...")

    # Connect to Sheet and check duplicates
    sheet = get_sheet()
    existing_urls, existing_titles = get_existing_urls(sheet)
    next_episode = get_next_episode_number(sheet)

    # Add new papers
    logger.info(f"\nAdding new papers (starting at episode #{next_episode})...")
    added = add_papers_to_sheet(sheet, papers, existing_urls, existing_titles, next_episode)

    logger.info("\n" + "=" * 60)
    logger.info(f"  Done! Added {added} new papers to the queue.")
    logger.info(f"  Skipped {len(papers) - added} duplicates.")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
