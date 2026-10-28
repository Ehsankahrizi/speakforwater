#!/usr/bin/env python3
"""
Commit + push data/queue.json if it changed.

Usage (in a workflow step):  python scripts/commit_queue.py "Queue: <what changed>"

Run it as an `if: always()` step after any job that edits the queue, so a
paper's status change is saved even when the job itself failed.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.json_queue import commit_queue  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "Queue: update"
    commit_queue(msg)
