#!/usr/bin/env python3
"""
SpeakForWater — audit_sheet.py  (READ-ONLY)

Dumps the structure of the Google Sheet so we can understand duplicate /
leftover / garbage rows before any cleanup. Makes NO changes.

Reports:
  - total rows + header
  - every data row: row#, episode_number, status, has-title, has-url, title
  - duplicate episode numbers (same number on >1 row)
  - blank rows and rows missing title/url
  - a recommended cleanup plan (which rows are canonical vs removable)

Usage:
  python scripts/audit_sheet.py

Environment:
  GOOGLE_CREDENTIALS_JSON, SPREADSHEET_ID, SHEET_NAME (default "Sheet1")
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import defaultdict

import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("audit-sheet")

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = os.environ.get("SHEET_NAME", "Sheet1")
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def get_sheet():
    if not GOOGLE_CREDENTIALS_JSON or not SPREADSHEET_ID:
        log.error("Missing GOOGLE_CREDENTIALS_JSON or SPREADSHEET_ID.")
        sys.exit(1)
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDENTIALS_JSON), scopes=SCOPES)
    ss = gspread.authorize(creds).open_by_key(SPREADSHEET_ID)
    sheet = ss.worksheet(SHEET_NAME)
    log.info(f"Sheet: {ss.title} / {SHEET_NAME}")
    return sheet


def _idx(header, *names):
    lower = [h.strip().lower() for h in header]
    for n in names:
        if n in lower:
            return lower.index(n)
    return -1


def main():
    sheet = get_sheet()
    rows = sheet.get_all_values()
    if not rows:
        log.info("Sheet is empty.")
        return
    header = rows[0]
    log.info(f"Total rows (incl header): {len(rows)}")
    log.info(f"Header: {header}")

    i_ep = _idx(header, "episode_number", "episode", "episode number")
    i_st = _idx(header, "status")
    i_ti = _idx(header, "paper_title", "title")
    i_ur = _idx(header, "paper_url", "url")
    log.info(f"Column indexes -> episode:{i_ep} status:{i_st} title:{i_ti} url:{i_ur}")
    log.info("=" * 90)

    by_ep = defaultdict(list)   # ep_number -> [row_num,...]
    blank_rows, no_title_rows, nonint_ep = [], [], []

    def cell(row, i):
        return row[i].strip() if (0 <= i < len(row)) else ""

    log.info(f"{'row':>4} {'ep':>5} {'status':<12} {'T':<2} {'U':<2} title")
    for rn, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            blank_rows.append(rn)
            continue
        ep_raw = cell(row, i_ep)
        status = cell(row, i_st)
        title = cell(row, i_ti)
        url = cell(row, i_ur)
        try:
            ep = int(ep_raw)
            by_ep[ep].append(rn)
        except (ValueError, TypeError):
            ep = None
            if ep_raw:
                nonint_ep.append((rn, ep_raw))
        if not title or not url:
            no_title_rows.append(rn)
        log.info(f"{rn:>4} {ep_raw:>5} {status:<12} {'Y' if title else '-':<2} {'Y' if url else '-':<2} {title[:60]}")

    log.info("=" * 90)
    dups = {ep: rl for ep, rl in by_ep.items() if len(rl) > 1}
    log.info(f"Unique episode numbers: {len(by_ep)}")
    log.info(f"Episode numbers appearing on >1 row (DUPLICATES): {len(dups)}")
    for ep in sorted(dups):
        log.info(f"   episode {ep}: rows {dups[ep]}")
    log.info(f"Completely blank rows: {blank_rows or 'none'}")
    log.info(f"Rows missing title or url: {no_title_rows or 'none'}")
    log.info(f"Rows with non-integer episode_number: {nonint_ep or 'none'}")

    # Recommended cleanup: keep the first row per episode that has BOTH title+url
    # (fallback: first row); flag every other duplicate row as removable.
    removable = []
    for ep, rl in by_ep.items():
        if len(rl) == 1:
            continue
        def score(rn):
            row = rows[rn - 1]
            return (bool(cell(row, i_ti)), bool(cell(row, i_ur)), -rn)  # prefer title+url, then earliest
        keep = max(rl, key=score)
        removable += [rn for rn in rl if rn != keep]
    removable = sorted(set(removable) | set(blank_rows))
    log.info("=" * 90)
    log.info(f"RECOMMENDED removable rows (duplicate copies + blank rows): {len(removable)}")
    log.info(f"   rows: {removable}")
    log.info("(READ-ONLY audit — nothing was changed.)")


if __name__ == "__main__":
    main()
