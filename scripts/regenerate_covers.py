#!/usr/bin/env python3
"""
SpeakForWater — regenerate_covers.py

Regenerates EVERY episode cover image with the current production logic so the
whole archive is visually consistent:

  - Template : public/cover2.png (auto-resolved by cover_generator)
  - Fonts    : Montserrat ExtraBold (title) / SemiBold (episode label)
  - Layout   : driven by env vars (TITLE_PX_*, EP_*) — same as generate-podcast.yml
  - Title    : short, listener-friendly version via Groq (title_simplifier)

For each episode it also:
  - writes public/episodes/epXXX.png
  - records the short title + cover path back into public/episodes/epXXX.json
  - back-fills `cover:` into the episode markdown frontmatter if missing

Run from the repo root. Requires GROQ_API_KEY in the environment for short
titles (otherwise the full paper title is used as a fallback).

    python scripts/regenerate_covers.py            # all episodes
    python scripts/regenerate_covers.py 1 2 3       # only these episode numbers
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("regen-covers")

REPO = Path(__file__).resolve().parent.parent
EP_DIR = REPO / "public" / "episodes"
MD_DIR = REPO / "src" / "content" / "episodes"

# Make `app...` importable when run from repo root.
sys.path.insert(0, str(REPO))

from app.services.cover_generator import make_cover  # noqa: E402
from app.services.title_simplifier import simplify_title  # noqa: E402


def md_for(ep_num: int) -> Path | None:
    """Return the markdown file for an episode number (filenames: NNN-slug.md)."""
    matches = sorted(MD_DIR.glob(f"{ep_num:03d}-*.md"))
    return matches[0] if matches else None


def ensure_cover_field(md_path: Path, cover_rel: str) -> bool:
    """Add `cover: "<path>"` to the frontmatter if it's not already there."""
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False

    # Locate frontmatter bounds.
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return False

    if any(re.match(r"\s*cover:", lines[i]) for i in range(1, end)):
        return False  # already present

    # Prefer to insert right after audio_url, else just before the closing ---.
    insert_at = end
    for i in range(1, end):
        if lines[i].startswith("audio_url:"):
            insert_at = i + 1
            break

    lines.insert(insert_at, f'cover: "{cover_rel}"')
    out = "\n".join(lines)
    if text.endswith("\n"):
        out += "\n"
    md_path.write_text(out, encoding="utf-8")
    return True


def regenerate(ep_num: int, total: int, idx: int) -> None:
    jf = EP_DIR / f"ep{ep_num:03d}.json"
    if not jf.exists():
        log.warning(f"[{idx}/{total}] ep{ep_num:03d}: no JSON, skipping")
        return

    data = json.loads(jf.read_text(encoding="utf-8"))
    title = (data.get("title") or "").strip()
    if not title:
        log.warning(f"[{idx}/{total}] ep{ep_num:03d}: empty title, skipping")
        return

    # Reuse the previously generated short title unless REROLL_TITLES=1.
    # This lets us re-render covers (e.g. tweak layout) without changing the
    # approved wording or spending Groq calls.
    reroll = os.environ.get("REROLL_TITLES", "").strip().lower() in ("1", "true", "yes")
    cover_title = data.get("cover_title")
    if cover_title and not reroll:
        log.info(f"ep{ep_num:03d}: reusing stored cover_title")
    else:
        cover_title = None
        try:
            cover_title = simplify_title(title)
        except Exception as e:  # pragma: no cover
            log.warning(f"ep{ep_num:03d}: title_simplifier error: {e}")

    out_png = EP_DIR / f"ep{ep_num:03d}.png"
    make_cover(
        output_path=out_png,
        title=title,
        episode_number=ep_num,
        cover_title=cover_title,
    )

    # Persist cover path + short title into the JSON sidecar.
    cover_rel = f"/episodes/ep{ep_num:03d}.png"
    data["cover"] = cover_rel
    if cover_title:
        data["cover_title"] = cover_title
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    md = md_for(ep_num)
    if md and ensure_cover_field(md, cover_rel):
        log.info(f"ep{ep_num:03d}: added cover field to {md.name}")

    log.info(
        f"[{idx}/{total}] ep{ep_num:03d} OK — "
        f"cover_title={'(full title)' if not cover_title else repr(cover_title)}"
    )


def main(argv: list[str]) -> int:
    if argv:
        ep_nums = [int(a) for a in argv]
    else:
        ep_nums = sorted(
            int(p.stem[2:]) for p in EP_DIR.glob("ep*.json") if p.stem[2:].isdigit()
        )

    total = len(ep_nums)
    log.info(f"Regenerating {total} cover(s)…")
    for idx, ep_num in enumerate(ep_nums, start=1):
        try:
            regenerate(ep_num, total, idx)
        except Exception as e:
            log.error(f"ep{ep_num:03d}: FAILED — {e}")
        time.sleep(0.2)  # be gentle with the Groq free tier
    log.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
