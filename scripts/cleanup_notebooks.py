#!/usr/bin/env python3
"""
SpeakForWater — cleanup_notebooks.py

Delete old NotebookLM notebooks, keeping only the KEEP most-recently-created
OWNED notebooks. NotebookLM free accounts cap at 100 owned notebooks, and the
pipeline used to leak one per episode, so the account fills up and blocks
generation. This clears the backlog.

Shared notebooks (is_owner == false) are never touched. Default mode is a
dry-run report; pass --apply to actually delete.

Usage:
  KEEP=5 python scripts/cleanup_notebooks.py            # dry-run report
  KEEP=5 python scripts/cleanup_notebooks.py --apply    # delete

Environment:
  NOTEBOOKLM_AUTH_JSON   Auth export (same secret the pipeline uses)
  KEEP                   How many recent notebooks to keep (default 5)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cleanup")

NOTEBOOKLM_AUTH_JSON = os.environ.get("NOTEBOOKLM_AUTH_JSON", "")
KEEP = int(os.environ.get("KEEP", "5"))
STORAGE = str(Path.home() / ".notebooklm" / "storage_state.json")


def _run_cli(args: list[str], timeout: int = 60) -> str:
    """Run a notebooklm CLI command with explicit --storage, return stdout."""
    cmd = ["notebooklm", "--storage", STORAGE, *args]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env={**os.environ}
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def _setup_auth() -> None:
    if not NOTEBOOKLM_AUTH_JSON:
        log.error("Missing NOTEBOOKLM_AUTH_JSON")
        sys.exit(1)
    p = Path(STORAGE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(NOTEBOOKLM_AUTH_JSON)
    log.info(f"Auth written to {STORAGE}")


def _list_notebooks() -> list[dict]:
    data = json.loads(_run_cli(["list", "--json"], timeout=90))
    if isinstance(data, dict):
        return data.get("notebooks", [])
    if isinstance(data, list):
        return data
    return []


def main() -> None:
    apply = "--apply" in sys.argv[1:]
    if KEEP < 0:
        log.error("KEEP must be >= 0")
        sys.exit(1)

    _setup_auth()

    nbs = _list_notebooks()
    owned = [n for n in nbs if n.get("is_owner", True)]
    log.info(f"Total returned: {len(nbs)} | owned: {len(owned)} | "
             f"shared (never touched): {len(nbs) - len(owned)}")

    # Most-recent first. Missing created_at sorts as oldest (empty string), so
    # undated notebooks become deletion candidates rather than accidental keeps.
    owned.sort(key=lambda n: n.get("created_at") or "", reverse=True)

    keep = owned[:KEEP]
    delete = owned[KEEP:]

    log.info(f"KEEP={KEEP} — keeping {len(keep)}, deleting {len(delete)}")
    log.info("Keeping (most recent):")
    for n in keep:
        log.info(f"   KEEP {n.get('id')}  {str(n.get('created_at'))[:10]}  "
                 f"{str(n.get('title'))[:55]}")

    if not delete:
        log.info("Nothing to delete — already at or below KEEP.")
        return

    log.info(f"Mode: {'APPLY (deleting)' if apply else 'REPORT (dry-run, no deletes)'}")
    if not apply:
        for n in delete[:15]:
            log.info(f"   del {n.get('id')}  {str(n.get('created_at'))[:10]}  "
                     f"{str(n.get('title'))[:55]}")
        if len(delete) > 15:
            log.info(f"   ... and {len(delete) - 15} more")
        log.info(f"DRY-RUN: {len(delete)} notebook(s) would be deleted. "
                 f"Re-run with --apply to delete.")
        return

    deleted = failed = 0
    for i, n in enumerate(delete, 1):
        nid = n.get("id")
        if not nid:
            failed += 1
            continue
        try:
            _run_cli(["delete", "-n", str(nid), "-y"], timeout=60)
            deleted += 1
            if deleted % 20 == 0 or i == len(delete):
                log.info(f"   deleted {deleted}/{len(delete)}...")
        except Exception as e:
            failed += 1
            log.warning(f"   delete failed for {nid}: {str(e)[:120]}")

    log.info(f"\n✓ Done. Deleted {deleted}, failed {failed}. "
             f"Remaining owned ~ {len(owned) - deleted}.")


if __name__ == "__main__":
    main()
