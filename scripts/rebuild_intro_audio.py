#!/usr/bin/env python3
"""
SpeakForWater — rebuild_intro_audio.py

Add the intro + outro jingles to the home-page introduction audio (ep000.m4a),
so it matches the regular episodes. The regular episodes are stitched by
app/services/audio_stitcher.py during generation; ep000 was uploaded once
without jingles, so it needs a one-off (repeatable) rebuild.

Idempotent by design: the pristine, un-stitched original is preserved in R2 as
`ep000_raw.m4a`. We always stitch FROM that raw and overwrite the served
`ep000.m4a`, so re-running never stacks a second intro/outro on top.

Flow:
  1. If `ep000_raw.m4a` does not exist in R2, copy the current `ep000.m4a`
     (which is still raw) to `ep000_raw.m4a` to preserve the original.
  2. Download `ep000_raw.m4a`.
  3. Stitch  assets/intro.mp3 + raw + assets/outro.mp3  ->  ep000.m4a  (AAC).
  4. Upload the stitched `ep000.m4a` back to R2.

Default mode is a dry-run; pass --apply to write to R2.

Environment (R2 secrets, same as the pipeline):
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.r2_uploader import (  # noqa: E402
    copy_object,
    download_file,
    object_exists,
    r2_enabled,
    upload_file,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rebuild-intro")

REPO = Path(__file__).resolve().parent.parent
INTRO = REPO / "assets" / "intro.mp3"
OUTRO = REPO / "assets" / "outro.mp3"

SERVED_KEY = "ep000.m4a"       # what the home page plays
RAW_KEY = "ep000_raw.m4a"      # pristine, un-stitched original (backup)


def stitch(raw: Path, out: Path) -> None:
    """intro + raw + outro -> out (AAC m4a), loudness-normalised."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(INTRO),
        "-i", str(raw),
        "-i", str(OUTRO),
        "-filter_complex",
        "[0][1][2]concat=n=3:v=0:a=1,loudnorm=I=-16:TP=-1.5:LRA=11[out]",
        "-map", "[out]",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        str(out),
    ]
    log.info("Stitching intro + ep000 + outro ...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")
    if not out.exists() or out.stat().st_size < 10_000:
        raise RuntimeError("ffmpeg produced no/empty output")
    log.info(f"Stitched: {out} ({out.stat().st_size:,} bytes)")


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    log.info(f"Mode: {'APPLY (writing to R2)' if apply else 'DRY-RUN (no writes)'}")

    if not r2_enabled():
        log.error("R2 is not configured (need R2_ACCOUNT_ID/ACCESS_KEY_ID/"
                  "SECRET_ACCESS_KEY/BUCKET).")
        return 1
    for f in (INTRO, OUTRO):
        if not f.exists():
            log.error(f"Missing jingle: {f}")
            return 1

    work = Path("/tmp/sfw-intro")
    work.mkdir(parents=True, exist_ok=True)
    raw_local = work / "ep000_raw.m4a"
    out_local = work / "ep000.m4a"

    # 1. Ensure a pristine raw backup exists (so re-runs never double-stitch).
    if object_exists(RAW_KEY):
        log.info(f"Raw backup {RAW_KEY} already exists — using it as the source.")
    else:
        log.info(f"No {RAW_KEY} yet; current {SERVED_KEY} is still raw.")
        if apply:
            if not copy_object(SERVED_KEY, RAW_KEY):
                log.error("Failed to preserve raw backup — aborting.")
                return 1
        else:
            log.info(f"DRY-RUN: would copy {SERVED_KEY} -> {RAW_KEY} (preserve raw).")

    # 2. Download the raw source (fall back to served key in dry-run first pass).
    src_key = RAW_KEY if object_exists(RAW_KEY) else SERVED_KEY
    if not download_file(src_key, raw_local):
        log.error(f"Could not download raw source {src_key}.")
        return 1

    # 3. Stitch.
    stitch(raw_local, out_local)

    # 4. Upload the stitched result over the served key.
    if not apply:
        log.info(f"DRY-RUN: would upload stitched {out_local.name} -> {SERVED_KEY}. "
                 f"Re-run with --apply to publish.")
        return 0
    if not upload_file(out_local, SERVED_KEY):
        log.error("Upload failed.")
        return 1
    log.info(f"\n✓ Done. {SERVED_KEY} now has intro + outro. Raw preserved at {RAW_KEY}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
