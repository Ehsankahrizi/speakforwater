"""
SpeakForWater — cover_generator.py

Generates a YouTube-ready PNG cover image (1920x1080) for an episode.

If the background is a VIDEO file (mp4/mov/etc.), it extracts the first
frame using FFmpeg before drawing on it.
"""

from __future__ import annotations

import logging
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1920, 1080
BRAND_DEEP = (10, 37, 64)
BRAND_LIGHT = (133, 183, 235)
WHITE = (255, 255, 255)

SERIF_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
MAC_SERIF = "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf"
MAC_SANS = "/System/Library/Fonts/Helvetica.ttc"

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _font(size: int, bold: bool = False, serif: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [SERIF_BOLD, MAC_SERIF] if serif else [SANS_BOLD, MAC_SANS]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_centered(draw, text, y, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text(((WIDTH - w) / 2, y), text, font=font, fill=fill)


def _video_to_frame(video_path: Path) -> Path | None:
    """Extract a frame from a video file using FFmpeg. Returns the PNG path."""
    frame_path = Path("/tmp") / f"{video_path.stem}_frame.png"
    if frame_path.exists():
        return frame_path
    cmd = [
        "ffmpeg", "-y",
        "-ss", "3",  # 3 seconds in (skip intro fade)
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(frame_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            log.warning(f"ffmpeg failed extracting frame: {result.stderr[-200:]}")
            return None
    except Exception as e:
        log.warning(f"ffmpeg crashed extracting frame: {e}")
        return None
    return frame_path if frame_path.exists() else None


def _resolve_background(background: Path | None) -> Path | None:
    """If background is a video, extract first frame. If image, return as-is."""
    if not background or not background.exists():
        return None
    if background.suffix.lower() in VIDEO_EXTS:
        log.info(f"Extracting frame from video: {background.name}")
        return _video_to_frame(background)
    return background


def make_cover(
    output_path: Path,
    title: str,
    episode_number: int,
    background: Path | None = None,
) -> Path:
    """Render a 1920×1080 cover PNG and save to output_path."""
    resolved_bg = _resolve_background(background)

    if resolved_bg and resolved_bg.exists():
        try:
            bg = Image.open(resolved_bg).convert("RGB")
            ratio = max(WIDTH / bg.width, HEIGHT / bg.height)
            new_size = (int(bg.width * ratio), int(bg.height * ratio))
            bg = bg.resize(new_size, Image.LANCZOS)
            left = (bg.width - WIDTH) // 2
            top = (bg.height - HEIGHT) // 2
            bg = bg.crop((left, top, left + WIDTH, top + HEIGHT))
            overlay = Image.new("RGB", (WIDTH, HEIGHT), BRAND_DEEP)
            img = Image.blend(bg, overlay, 0.55)
        except Exception as e:
            log.warning(f"Failed to use background ({e}); using solid color.")
            img = Image.new("RGB", (WIDTH, HEIGHT), BRAND_DEEP)
    else:
        img = Image.new("RGB", (WIDTH, HEIGHT), BRAND_DEEP)

    draw = ImageDraw.Draw(img)

    # Eyebrow
    eyebrow_font = _font(48, bold=True)
    _draw_centered(draw, f"EPISODE {episode_number}".upper(), int(HEIGHT * 0.18), eyebrow_font, BRAND_LIGHT)

    # Title
    title_font = _font(78, bold=True, serif=True)
    lines = textwrap.wrap(title.strip(), width=28) or [title]
    if len(lines) > 5:
        lines = lines[:5]
        lines[-1] = lines[-1] + "…"
    line_height = 95
    block_height = line_height * len(lines)
    start_y = int(HEIGHT * 0.42 - block_height / 2)
    for i, line in enumerate(lines):
        _draw_centered(draw, line, start_y + i * line_height, title_font, WHITE)

    # Brand
    brand_font = _font(40, bold=True)
    _draw_centered(draw, "SpeakForWater", int(HEIGHT * 0.86), brand_font, BRAND_LIGHT)

    # Tagline
    tag_font = _font(28)
    _draw_centered(
        draw,
        "speakforwater.com  ·  daily water research, narrated",
        int(HEIGHT * 0.92),
        tag_font,
        (180, 200, 220),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    log.info(f"Cover saved: {output_path} ({output_path.stat().st_size // 1024} KB)")
    return output_path
