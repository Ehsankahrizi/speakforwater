#!/usr/bin/env python3
"""
SpeakForWater — requeue_rows.py

Reset specific rows' `status` back to "queued" so they are picked up again by
the daily pipeline. Use this to recover papers that were wrongly marked
"failed" by a systemic error (e.g. NotebookLM was at its notebook limit) rather
than a problem with the paper itself.

SAFETY: by default only rows whose current status starts with "failed" are
touched. Pass --force to re-queue the listed rows regardless of current status.
Default mode is a dry-run report; pass --apply to write.

Usage:
  ROWS="160,161,162,163,164" python scripts/requeue_rows.py            # dry-run
  ROWS="160,161,162,163,164" python scripts/requeue_rows.py --apply    # write

Environment:
  ROWS                     Comma-separated 1-indexed sheet row numbers
  GOOGLE_CREDENTIALS_JSON  Service-account JSON
  SPREADSHEET_ID           Sheet ID
  SHEET_NAME               Optional, default Sheet1
"""

from __future__ import annotations

import json
import logging
import os
import sys

import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("requeue")

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = os.environ.get("SHEET_NAME") or "Sheet1"
ROWS = os.environ.get("ROWS", "")

STATUS_COL = 4  # column D
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def parse_rows(raw: str) -> list[int]:
    out: list[int] = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            log.error(f"Invalid row number: {part!r}")
            sys.exit(1)
        if n < 2:
            log.error(f"Row {n} is invalid (row 1 is the header).")
            sys.exit(1)
        out.append(n)
    return out


def main() -> None:
    apply = "--apply" in sys.argv[1:]
    force = "--force" in sys.argv[1:]

    if not GOOGLE_CREDENTIALS_JSON or not SPREADSHEET_ID:
        log.error("Missing env: GOOGLE_CREDENTIALS_JSON or SPREADSHEET_ID")
        sys.exit(1)

    rows = parse_rows(ROWS)
    if not rows:
        log.error("No rows given. Set ROWS='160,161,...'")
        sys.exit(1)

    creds = Credentials.from_service_account_info(
        json.loads(GOOGLE_CREDENTIALS_JSON), scopes=SCOPES
    )
    sheet = gspread.authorize(creds).open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    log.info(f"Sheet: {sheet.spreadsheet.title} / {SHEET_NAME}")
    log.info(f"Mode: {'APPLY (writing)' if apply else 'REPORT (dry-run)'}"
             f"{' [--force]' if force else ''}")

    to_change: list[int] = []
    for rn in rows:
        cur = str(sheet.cell(rn, STATUS_COL).value or "").strip()
        cur_l = cur.lower()
        title = str(sheet.cell(rn, 3).value or "")[:60]  # column C = paper_title
        if cur_l == "queued":
            log.info(f"  row {rn}: already 'queued' — skip | {title}")
            continue
        if not force and not cur_l.startswith("failed"):
            log.info(f"  row {rn}: status='{cur}' is not 'failed' — skip "
                     f"(use --force to override) | {title}")
            continue
        log.info(f"  row {rn}: '{cur}' -> 'queued' | {title}")
        to_change.append(rn)

    if not to_change:
        log.info("Nothing to change.")
        return
    if not apply:
        log.info(f"DRY-RUN: {len(to_change)} row(s) would be re-queued. "
                 f"Re-run with --apply to write.")
        return

    updates = [
        {"range": gspread.utils.rowcol_to_a1(rn, STATUS_COL), "values": [["queued"]]}
        for rn in to_change
    ]
    sheet.batch_update(updates, value_input_option="USER_ENTERED")
    log.info(f"\n✓ Re-queued {len(to_change)} row(s): {to_change}")


if __name__ == "__main__":
    main()
