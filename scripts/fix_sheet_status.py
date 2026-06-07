#!/usr/bin/env python3
"""
SpeakForWater — fix_sheet_status.py

Repairs the Google Sheet `status` column after catalog curation.

WHY: the sheet's `episode_number` column is corrupted (values don't match the
website episodes), so an earlier "mark dropped by episode_number" pass set the
wrong rows. This script re-keys on the reliable identity — `paper_url` (then
normalized `paper_title`) — using an authoritative decisions file built from
the website (config/episode_decisions.json):

    decision "keep" -> status "published"
    decision "drop" -> status "dropped"

Rows that don't match any website episode (e.g. never-published candidates or
genuinely queued future papers) are LEFT UNTOUCHED and reported, so nothing is
guessed.

Default mode is REPORT (dry-run, no writes). Pass --apply to write changes.

Usage:
  python scripts/fix_sheet_status.py            # dry-run diff
  python scripts/fix_sheet_status.py --apply    # apply

Environment:
  GOOGLE_CREDENTIALS_JSON, SPREADSHEET_ID, SHEET_NAME (default "Sheet1")
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("fix-sheet")

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = os.environ.get("SHEET_NAME", "Sheet1")
DECISIONS_FILE = os.environ.get("DECISIONS_FILE", "config/episode_decisions.json")

STATUS_FOR = {"keep": "published", "drop": "dropped"}
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def norm_title(t: str) -> str:
    t = re.sub(r"<[^>]+>", "", t or "")
    return re.sub(r"\s+", " ", t).strip().lower()


def norm_url(u: str) -> str:
    return (u or "").strip().lower()


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
    raise SystemExit(f"Missing column {names} in header {header}")


def main():
    apply = "--apply" in sys.argv[1:]
    decisions = json.loads(Path(DECISIONS_FILE).read_text())
    by_url = {norm_url(d["url"]): d for d in decisions if d.get("url")}
    by_title = {d["title_norm"]: d for d in decisions}
    log.info(f"Loaded {len(decisions)} authoritative decisions "
             f"({sum(d['decision']=='keep' for d in decisions)} keep / "
             f"{sum(d['decision']=='drop' for d in decisions)} drop)")
    log.info(f"Mode: {'APPLY (writing changes)' if apply else 'REPORT (dry-run, no writes)'}")
    log.info("=" * 90)

    sheet = get_sheet()
    rows = sheet.get_all_values()
    header = rows[0]
    i_st = _idx(header, "status")
    i_ti = _idx(header, "paper_title", "title")
    i_ur = _idx(header, "paper_url", "url")

    def cell(row, i):
        return row[i].strip() if (0 <= i < len(row)) else ""

    changes = []     # (row_num, title, cur, new)
    correct = 0
    unmatched = []   # (row_num, cur_status, title)
    for rn, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        title = cell(row, i_ti)
        url = cell(row, i_ur)
        cur = cell(row, i_st)
        d = by_url.get(norm_url(url)) or by_title.get(norm_title(title))
        if not d:
            unmatched.append((rn, cur, title))
            continue
        new = STATUS_FOR[d["decision"]]
        if cur != new:
            changes.append((rn, title, cur, new))
        else:
            correct += 1

    log.info(f"Rows already correct: {correct}")
    log.info(f"Rows to CHANGE: {len(changes)}")
    for rn, title, cur, new in changes:
        log.info(f"   row {rn:>3}: {cur:<10} -> {new:<10} | {title[:58]}")
    log.info("-" * 90)
    log.info(f"Unmatched rows (not website episodes — LEFT UNTOUCHED): {len(unmatched)}")
    for rn, cur, title in unmatched:
        log.info(f"   row {rn:>3}: status={cur:<10} | {title[:58]}")
    log.info("=" * 90)

    if not changes:
        log.info("Nothing to change.")
        return
    if not apply:
        log.info("DRY-RUN only. Re-run with --apply to write these changes.")
        return

    updates = [
        {"range": gspread.utils.rowcol_to_a1(rn, i_st + 1), "values": [[new]]}
        for rn, _, _, new in changes
    ]
    sheet.batch_update(updates, value_input_option="USER_ENTERED")
    log.info(f"APPLIED {len(changes)} status corrections.")


if __name__ == "__main__":
    main()
