#!/usr/bin/env python3
"""
SpeakForWater — reset_queue.py

Two jobs in one script:

1. RENUMBER every row in the Sheet so episode_number is globally unique
   and sequential (1, 2, 3, ...) based on row position. Fixes the bug
   where each batch of search_papers.py restarts numbering from 1.

2. RESET previously published rows back to "queued" so they regenerate
   with the new improved prompt. Clears mp3_url and published_at for
   those rows. Leaves "failed" and "processing" rows untouched.

Trigger this manually from GitHub Actions → Reset queue → Run workflow.

Environment variables (set as GitHub Secrets):
  QUEUE_FILE               Optional, default data/queue.json
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.json_queue import open_worksheet  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("reset")




def main() -> None:
    sheet = open_worksheet()

    rows = sheet.get_all_records()
    log.info(f"Read {len(rows)} rows from sheet: {sheet.path}")

    if not rows:
        log.warning("Sheet is empty.")
        return

    updates: list[dict] = []
    requeued = 0
    renumbered = 0

    for i, row in enumerate(rows):
        sheet_row = i + 2  # +2 because data starts at row 2 (row 1 = header)
        status = (str(row.get("status") or "")).strip().lower()
        new_episode_num = i + 1  # globally unique, row-based

        # Always renumber episode_number (column E)
        current_ep = row.get("episode_number")
        if current_ep != new_episode_num:
            updates.append({
                "range": f"E{sheet_row}",
                "values": [[new_episode_num]],
            })
            renumbered += 1

        # If row was previously published, reset it for regeneration
        if status == "published":
            updates.append({"range": f"D{sheet_row}", "values": [["queued"]]})
            updates.append({"range": f"F{sheet_row}", "values": [[""]]})  # mp3_url
            updates.append({"range": f"G{sheet_row}", "values": [[""]]})  # published_at
            requeued += 1

    log.info(f"Will renumber {renumbered} rows and re-queue {requeued} previously-published rows.")

    if not updates:
        log.info("Nothing to update — sheet is already correct.")
        return

    # Batch update in chunks of 100 (legacy chunking; harmless with the JSON queue)
    CHUNK = 100
    total_chunks = (len(updates) + CHUNK - 1) // CHUNK
    for c in range(total_chunks):
        chunk = updates[c * CHUNK : (c + 1) * CHUNK]
        sheet.batch_update(chunk, value_input_option="USER_ENTERED")
        log.info(f"  pushed chunk {c + 1}/{total_chunks} ({len(chunk)} cells)")

    log.info(f"\n✓ Done. Renumbered {renumbered} rows and re-queued {requeued} rows.")


if __name__ == "__main__":
    main()
