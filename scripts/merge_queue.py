#!/usr/bin/env python3
"""
Git merge driver for data/queue.json  —  row-level 3-way merge.

Registered via .gitattributes (`data/queue.json merge=queuejson`) and
`git config merge.queuejson.driver "python3 scripts/merge_queue.py %O %A %B"`
(commit_queue() sets that config automatically on CI).

Why: Search Papers appends rows and Generate Podcast updates a row's status.
If both push around the same time, a plain text merge can conflict on
neighbouring lines. This merges by paper (keyed on paper_url):

  * rows added on either side are kept (ours order first, then theirs)
  * a row deleted on one side (and present in base) is deleted
  * for rows changed on both sides, each FIELD takes whichever side changed
    it relative to base (if both changed the same field, ours wins)

Usage: merge_queue.py BASE OURS THEIRS   (result written to OURS)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.json_queue import COLUMNS, dumps_rows, load_rows  # noqa: E402


def _key(row: dict) -> str:
    return (row.get("paper_url") or "").strip().lower() or (row.get("paper_title") or "").strip().lower()


def _index(rows):
    return {_key(r): r for r in rows}


def merge(base: list[dict], ours: list[dict], theirs: list[dict]) -> list[dict]:
    b, o, t = _index(base), _index(ours), _index(theirs)
    order = [_key(r) for r in ours] + [_key(r) for r in theirs if _key(r) not in o]
    out = []
    for k in order:
        in_b, in_o, in_t = k in b, k in o, k in t
        if in_b and (not in_o or not in_t):
            continue  # deleted on one side
        if in_o and in_t:
            if in_b:
                row = {}
                for c in COLUMNS:
                    ov, tv, bv = o[k].get(c), t[k].get(c), b[k].get(c)
                    row[c] = tv if ov == bv else ov
            else:
                row = o[k]  # added on both sides independently: keep ours
            out.append(row)
        elif in_o:
            out.append(o[k])
        elif in_t:
            out.append(t[k])
    return out


def main(argv):
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    base_p, ours_p, theirs_p = map(Path, argv[1:])
    try:
        merged = merge(load_rows(base_p), load_rows(ours_p), load_rows(theirs_p))
    except Exception as e:  # unparseable side → let git report a conflict
        print(f"merge_queue: cannot merge automatically: {e}", file=sys.stderr)
        return 1
    ours_p.write_text(dumps_rows(merged), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
