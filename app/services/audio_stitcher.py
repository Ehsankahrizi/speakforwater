"""
Audio stitching: combines intro jingle + podcast + outro jingle into a final MP3.

Uses ffmpeg (pre-installed on GitHub Actions ubuntu runners) to concatenate
audio files with crossfade transitions for a professional sound.

Usage:
    from app.services.audio_stitcher import stitch_podcast

    final_path = stitch_podcast(
        podcast_path="episodes/ep001_raw.mp3",
        output_path="episodes/ep001.mp3",
        intro_path="assets/intro.mp3",   # optional, uses default if omitted
        outro_path="assets/outro.mp3",   # optional, uses default if omitted
    )
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Default jingle locations (relative to repo root)
DEFAULT_INTRO = Path("assets/intro.mp3")
DEFAULT_OUTRO = Path("assets/outro.mp3")


def stitch_podcast(
    podcast_path: str | Path,
    output_path: str | Path | None = None,
    intro_path: str | Path | None = None,
    outro_path: str | Path | None = None,
    crossfade_ms: int = 500,
) -> Path:
    """
    Combine intro + podcast + outro into a single professional MP3.

    Args:
        podcast_path: Path to the raw podcast MP3 from NotebookLM.
        output_path: Where to save the final stitched MP3.
                     If None, overwrites the original podcast_path.
        intro_path: Path to intro jingle. Defaults to assets/intro.mp3.
        outro_path: Path to outro jingle. Defaults to assets/outro.mp3.
        crossfade_ms: Crossfade duration in milliseconds between segments.

    Returns:
        Path to the final stitched MP3 file.
    """
    podcast_path = Path(podcast_path)
    intro_path = Path(intro_path) if intro_path else DEFAULT_INTRO
    outro_path = Path(outro_path) if outro_path else DEFAULT_OUTRO

    if not podcast_path.exists():
        raise FileNotFoundError(f"Podcast file not found: {podcast_path}")

    # Determine output path
    if output_path is None:
        output_path = podcast_path
    output_path = Path(output_path)

    # Check which jingles are available
    has_intro = intro_path.exists()
    has_outro = outro_path.exists()

    if not has_intro and not has_outro:
        logger.warning(
            f"No jingle files found at {intro_path} or {outro_path}. "
            "Skipping stitching — returning raw podcast as-is."
        )
        if output_path != podcast_path:
            shutil.copy2(podcast_path, output_path)
        return output_path

    logger.info(
        f"Stitching podcast: intro={has_intro}, outro={has_outro}, "
        f"crossfade={crossfade_ms}ms"
    )

    # Build the ffmpeg filter for concatenation with crossfade
    # Strategy: use concat filter with short crossfades between segments
    inputs = []
    input_args = []
    segments = []

    if has_intro:
        input_args.extend(["-i", str(intro_path)])
        segments.append(len(inputs))
        inputs.append("intro")

    input_args.extend(["-i", str(podcast_path)])
    segments.append(len(inputs))
    inputs.append("podcast")

    if has_outro:
        input_args.extend(["-i", str(outro_path)])
        segments.append(len(inputs))
        inputs.append("outro")

    # Use a temp file to avoid overwriting the input
    temp_output = output_path.with_suffix(".tmp.mp3")

    if len(inputs) == 1:
        # Only podcast, no jingles — just copy
        shutil.copy2(podcast_path, output_path)
        return output_path

    cf_sec = crossfade_ms / 1000.0

    if len(inputs) == 2:
        # Either intro+podcast or podcast+outro
        if has_intro:
            # Crossfade intro into podcast
            filter_complex = (
                f"[0]afade=t=out:st=0:d={cf_sec}[a0];"
                f"[1]afade=t=in:st=0:d={cf_sec}[a1];"
                f"[a0][a1]concat=n=2:v=0:a=1,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11[out]"
            )
        else:
            # Crossfade podcast into outro
            filter_complex = (
                f"[0]apad=pad_dur=0[a0];"
                f"[1]afade=t=in:st=0:d={cf_sec}[a1];"
                f"[a0][a1]concat=n=2:v=0:a=1,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11[out]"
            )
    else:
        # All three: intro + podcast + outro
        filter_complex = (
            # Normalize each input
            f"[0]afade=t=out:st=0:d={cf_sec}[intro];"
            f"[1]afade=t=in:st=0:d={cf_sec}[podcast_in];"
            f"[podcast_in]apad=pad_dur=0[podcast];"
            f"[2]afade=t=in:st=0:d={cf_sec},afade=t=out:st=3:d=2[outro];"
            # Concatenate all three
            f"[intro][podcast][outro]concat=n=3:v=0:a=1,"
            # Final loudness normalization
            f"loudnorm=I=-16:TP=-1.5:LRA=11[out]"
        )

    cmd = [
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-b:a", "192k",
        "-ar", "44100",
        str(temp_output),
    ]

    logger.info(f"Running ffmpeg stitch ({len(inputs)} segments)...")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.error(f"ffmpeg error: {result.stderr[:500]}")
            raise RuntimeError(f"ffmpeg stitching failed: {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg stitching timed out after 120s")

    # Move temp to final output
    if temp_output.exists():
        shutil.move(str(temp_output), str(output_path))
        logger.info(
            f"Stitched podcast saved: {output_path} "
            f"({output_path.stat().st_size:,} bytes)"
        )
    else:
        raise RuntimeError(f"ffmpeg produced no output file at {temp_output}")

    return output_path
