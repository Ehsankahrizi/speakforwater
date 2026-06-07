#!/usr/bin/env python3
"""
SpeakForWater — fix_sheet_numbers.py

Rebuilds the Google Sheet `episode_number` column, which is corrupted (its
values don't match the website episodes). Re-keys each row on paper_url /
paper_title against the authoritative decisions file (config/episode_decisions
.json) and writes the correct website episode number.

After this, max(episode_number) == the real highest episode, so the pipeline
can number new episodes as max+1 (see run_pipeline.py) without collisions.

Rows that don't match a website episode (non-website candidates) are LEFT
UNTOUCHED. Default mode is REPORT (dry-run). Pass --apply to write.

Usage:
  python scripts/fix_sheet_numbers.py            # dry-run diff
  python scripts/fix_sheet_numbers.py --apply    # apply

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
log = logging.getLogger("fix-numbers")

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = os.environ.get("SHEET_NAME", "Sheet1")
DECISIONS_FILE = os.environ.get("DECISIONS_FILE", "config/episode_decisions.json")
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
    log.info(f"Loaded {len(decisions)} authoritative decisions")
    log.info(f"Mode: {'APPLY (writing changes)' if apply else 'REPORT (dry-run, no writes)'}")
    log.info("=" * 90)

    sheet = get_sheet()
    rows = sheet.get_all_values()
    header = rows[0]
    i_ep = _idx(header, "episode_number", "episode", "episode number")
    i_ti = _idx(header, "paper_title", "title")
    i_ur = _idx(header, "paper_url", "url")

    def cell(row, i):
        return row[i].strip() if (0 <= i < len(row)) else ""

    changes = []     # (row_num, title, cur, new)
    correct = 0
    unmatched = []
    for rn, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        title = cell(row, i_ti)
        url = cell(row, i_ur)
        cur = cell(row, i_ep)
        d = by_url.get(norm_url(url)) or by_title.get(norm_title(title))
        if not d:
            unmatched.append((rn, cur, title))
            continue
        new = str(d["website_ep"])
        if cur != new:
            changes.append((rn, title, cur, new))
        else:
            correct += 1

    log.info(f"Rows already correct: {correct}")
    log.info(f"Rows to CHANGE: {len(changes)}")
    for rn, title, cur, new in changes:
        log.info(f"   row {rn:>3}: ep {cur:>4} -> {new:>4} | {title[:56]}")
    log.info("-" * 90)
    log.info(f"Unmatched rows (left untouched): {len(unmatched)}")
    for rn, cur, title in unmatched:
        log.info(f"   row {rn:>3}: ep={cur:<5} | {title[:56]}")
    log.info(f"Highest website episode number in decisions = {max(d['website_ep'] for d in decisions)} "
             f"(pipeline will number the next episode as this + 1)")
    log.info("=" * 90)

    if not changes:
        log.info("Nothing to change.")
        return
    if not apply:
        log.info("DRY-RUN only. Re-run with --apply to write these changes.")
        return

    updates = [
        {"range": gspread.utils.rowcol_to_a1(rn, i_ep + 1), "values": [[new]]}
        for rn, _, _, new in changes
    ]
    sheet.batch_update(updates, value_input_option="USER_ENTERED")
    log.info(f"APPLIED {len(changes)} episode_number corrections.")


if __name__ == "__main__":
    main()
