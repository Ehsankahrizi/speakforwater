#!/usr/bin/env python3
"""
SpeakForWater — drop_episodes.py

Mark (or delete) episode rows in the Google Sheet for episodes that have been
removed from the website during a catalog curation pass.

By default it MARKS matching rows: it sets the `status` column to "dropped" so
the rows are preserved for history and never reprocessed. Pass --delete to
remove the rows entirely instead.

Rows are matched by the `episode_number` column, so it works regardless of
column order.

Usage:
  python scripts/drop_episodes.py 1 3 7 8 ...        # mark as "dropped"
  python scripts/drop_episodes.py --delete 1 3 7 ... # delete rows

Environment variables:
  QUEUE_FILE               — optional, default data/queue.json
  DROP_STATUS              — status value to write (default "dropped")
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.json_queue import open_worksheet, rowcol_to_a1  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("drop-episodes")

DROP_STATUS = os.environ.get("DROP_STATUS", "dropped")



def get_sheet():
    """Open data/queue.json (gspread-compatible worksheet)."""
    return open_worksheet()


def _col_index(header: list[str], *names: str) -> int:
    """1-based column index for the first header that matches any of `names`."""
    lower = [h.strip().lower() for h in header]
    for name in names:
        if name in lower:
            return lower.index(name) + 1
    raise SystemExit(f"Could not find a column named any of {names} in header: {header}")


def main():
    args = [a for a in sys.argv[1:] if a != "--delete"]
    delete = "--delete" in sys.argv[1:]

    try:
        drop = sorted({int(a) for a in args})
    except ValueError:
        logger.error(f"All arguments must be episode numbers. Got: {args}")
        sys.exit(1)
    if not drop:
        logger.error("No episode numbers provided.")
        sys.exit(1)

    logger.info(f"Mode: {'DELETE rows' if delete else f'MARK status = {DROP_STATUS!r}'}")
    logger.info(f"Target episodes ({len(drop)}): {drop}")

    sheet = get_sheet()
    all_values = sheet.get_all_values()
    if not all_values:
        logger.error("Sheet is empty.")
        sys.exit(1)

    header = all_values[0]
    ep_col = _col_index(header, "episode_number", "episode", "episode number")
    status_col = _col_index(header, "status")

    # Collect matching row numbers (1-based; row 1 is the header).
    matches = []  # (row_number, episode_number)
    drop_set = set(drop)
    for i, row in enumerate(all_values[1:], start=2):
        cell = row[ep_col - 1] if len(row) >= ep_col else ""
        try:
            ep = int(str(cell).strip())
        except (ValueError, TypeError):
            continue
        if ep in drop_set:
            matches.append((i, ep))

    found_eps = sorted({ep for _, ep in matches})
    missing = sorted(drop_set - set(found_eps))
    logger.info(f"Matched {len(matches)} rows for {len(found_eps)} episodes.")
    if missing:
        logger.warning(f"No sheet row found for episodes: {missing}")

    if not matches:
        logger.info("Nothing to update.")
        return

    if delete:
        # Delete bottom-up so row indices stay valid.
        for row_num, ep in sorted(matches, reverse=True):
            sheet.delete_rows(row_num)
            logger.info(f"  Deleted row {row_num} (episode {ep})")
    else:
        # Batch-update the status cell for each matched row.
        updates = [
            {"range": rowcol_to_a1(row_num, status_col), "values": [[DROP_STATUS]]}
            for row_num, _ in matches
        ]
        sheet.batch_update(updates, value_input_option="USER_ENTERED")
        for row_num, ep in matches:
            logger.info(f"  Row {row_num} (episode {ep}) -> status = {DROP_STATUS}")

    logger.info(f"Done. {len(matches)} rows {'deleted' if delete else 'updated'}.")


if __name__ == "__main__":
    main()
