"""
SpeakForWater — video_generator.py

Combines a static cover PNG + MP3 audio into a YouTube-ready MP4 video.
Uses FFmpeg (already required by the audio_stitcher).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def make_video(
    mp3_path: Path,
    cover_path: Path,
    output_path: Path,
) -> Path:
    """
    Build a YouTube-ready MP4: static cover image + the podcast audio.
    Output is 1920x1080 H.264 + AAC, fast-start optimised.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-framerate", "2",
        "-i", str(cover_path),
        "-i", str(mp3_path),
        "-c:v", "libx264",
        "-preset", "medium",
        "-tune", "stillimage",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]

    log.info(f"Encoding video: {output_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error(f"FFmpeg failed: {result.stderr[-500:]}")
        raise RuntimeError(f"FFmpeg failed: {result.stderr[-500:]}")

    size_mb = output_path.stat().st_size / (1024 * 1024)
    log.info(f"Video saved: {output_path} ({size_mb:.1f} MB)")
    return output_path
