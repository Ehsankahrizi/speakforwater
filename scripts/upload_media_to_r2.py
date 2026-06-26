#!/usr/bin/env python3
"""
One-time migration: upload all existing site media to Cloudflare R2.

Uploads every episode MP3, the intro audio, and the hero video so the same
paths that the site/RSS request from R2 resolve. Safe to re-run — uploads
overwrite, and you can `--dry-run` first to see what would happen.

Usage (from the repo root, with R2_* env vars set):

    R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
    R2_BUCKET=speakforwater-media \
    python3 scripts/upload_media_to_r2.py            # do it
    python3 scripts/upload_media_to_r2.py --dry-run  # preview only

After it finishes and you've verified the files are public, run the cutover
steps in CLOUDFLARE_MIGRATION.md (remove the large media from the repo).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `app` importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.r2_uploader import r2_enabled, upload_file  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PUBLIC = REPO / "public"


def _targets() -> list[tuple[Path, str]]:
    """(local_path, r2_key) pairs for every large media file."""
    items: list[tuple[Path, str]] = []

    # Episode audio (and any per-episode m4a, if present)
    episodes_dir = PUBLIC / "episodes"
    for f in sorted(episodes_dir.glob("ep*.mp3")):
        items.append((f, f"episodes/{f.name}"))

    # Intro audio + hero video live at the site root
    for name in ("ep000.m4a", "movie_1.mp4"):
        f = PUBLIC / name
        if f.exists():
            items.append((f, name))

    return items


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    if not dry_run and not r2_enabled():
        print(
            "R2 is not configured. Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY, and R2_BUCKET, or pass --dry-run.",
            file=sys.stderr,
        )
        return 1

    targets = _targets()
    total_bytes = sum(p.stat().st_size for p, _ in targets)
    print(f"Found {len(targets)} files ({total_bytes / 1e9:.2f} GB) to upload.\n")

    ok = fail = 0
    for local_path, key in targets:
        size_mb = local_path.stat().st_size / 1e6
        if dry_run:
            print(f"  [dry-run] {key:32s} {size_mb:6.1f} MB")
            continue
        if upload_file(local_path, key):
            print(f"  ✓ {key:32s} {size_mb:6.1f} MB")
            ok += 1
        else:
            print(f"  ✗ {key:32s} FAILED")
            fail += 1

    if dry_run:
        print(f"\nDry run complete — {len(targets)} files would be uploaded.")
    else:
        print(f"\nDone. Uploaded {ok}, failed {fail}.")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
