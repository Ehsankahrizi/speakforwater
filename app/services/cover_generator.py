"""
SpeakForWater — cover_generator.py

Generates a YouTube-ready PNG cover image (1920x1080) for an episode.

New layout:
  Top-left:  EPISODE 11  (large, bold)
  Middle:    "Title of Paper:"  +  italic title (centered)
  Bottom:    Original paper authors (or fallback line)
  Footer:    Narrated by speakforwater.com

Background: extracts a frame from a video (movie_1.mp4) or solid color.
"""

from __future__ import annotations

import logging
import re
import subprocess
import textwrap
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1920, 1080
BRAND_DEEP = (10, 37, 64)
BRAND_LIGHT = (133, 183, 235)
WHITE = (255, 255, 255)
SOFT_WHITE = (220, 230, 245)

# Linux fonts (GitHub Actions)
SERIF_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
SERIF_ITALIC = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"
SERIF_BOLDITALIC = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf"
SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# macOS fallbacks (for local testing)
MAC_SERIF = "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf"
MAC_SERIF_ITALIC = "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf"
MAC_SANS = "/System/Library/Fonts/Helvetica.ttc"

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _font(size: int, *, serif=False, italic=False, bold=False) -> ImageFont.FreeTypeFont:
    """Pick the best available font for the requested style."""
    if serif and italic and bold:
        candidates = [SERIF_BOLDITALIC, SERIF_ITALIC, MAC_SERIF_ITALIC]
    elif serif and italic:
        candidates = [SERIF_ITALIC, MAC_SERIF_ITALIC]
    elif serif:
        candidates = [SERIF_BOLD, MAC_SERIF]
    elif bold:
        candidates = [SANS_BOLD, MAC_SANS]
    else:
        candidates = [SANS_REGULAR, MAC_SANS]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return _HTML_TAG_RE.sub("", unescape(text)).strip()


def _video_to_frame(video_path: Path) -> Optional[Path]:
    """Extract a frame from a video file using FFmpeg."""
    frame_path = Path("/tmp") / f"{video_path.stem}_frame.png"
    if frame_path.exists():
        return frame_path
    cmd = [
        "ffmpeg", "-y",
        "-ss", "3",
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(frame_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            log.warning(f"ffmpeg failed: {result.stderr[-200:]}")
            return None
    except Exception as e:
        log.warning(f"ffmpeg crashed: {e}")
        return None
    return frame_path if frame_path.exists() else None


def _resolve_background(background: Optional[Path]) -> Optional[Path]:
    if not background or not background.exists():
        return None
    if background.suffix.lower() in VIDEO_EXTS:
        log.info(f"Extracting frame from video: {background.name}")
        return _video_to_frame(background)
    return background


def _fetch_authors_from_openalex(paper_url: str) -> Optional[str]:
    """Try to fetch authors from OpenAlex using the paper URL. Best-effort."""
    if not paper_url:
        return None
    try:
        # OpenAlex accepts DOI URL or any landing page URL
        api = "https://api.openalex.org/works/" + urllib.parse.quote(paper_url, safe="")
        req = urllib.request.Request(api, headers={"User-Agent": "SpeakForWater/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            import json
            data = json.loads(r.read().decode("utf-8"))
        names = []
        for a in data.get("authorships", []) or []:
            au = a.get("author") or {}
            n = au.get("display_name")
            if n:
                names.append(n)
        if not names:
            return None
        if len(names) <= 3:
            return ", ".join(names)
        return ", ".join(names[:2]) + f", et al."
    except Exception as e:
        log.info(f"Could not fetch authors from OpenAlex ({e}); using generic credit.")
        return None


def _draw_centered(draw, text, y, font, fill, width=WIDTH):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text(((width - w) / 2, y), text, font=font, fill=fill)


def _wrap_lines(text: str, font: ImageFont.FreeTypeFont, draw, max_width: int) -> list[str]:
    """Word-wrap text so each line fits within max_width pixels."""
    words = text.split()
    lines = []
    current = []
    for w in words:
        trial = " ".join(current + [w])
        bbox = draw.textbbox((0, 0), trial, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current.append(w)
        else:
            if current:
                lines.append(" ".join(current))
            current = [w]
    if current:
        lines.append(" ".join(current))
    return lines


def make_cover(
    output_path: Path,
    title: str,
    episode_number: int,
    background: Optional[Path] = None,
    paper_url: str = "",
    authors: Optional[str] = None,
) -> Path:
    """Render a 1920×1080 cover PNG with new layout."""
    title = _strip_html(title)
    resolved_bg = _resolve_background(background)

    # Background
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
            img = Image.blend(bg, overlay, 0.6)
        except Exception as e:
            log.warning(f"Background failed ({e}); using solid color.")
            img = Image.new("RGB", (WIDTH, HEIGHT), BRAND_DEEP)
    else:
        img = Image.new("RGB", (WIDTH, HEIGHT), BRAND_DEEP)

    draw = ImageDraw.Draw(img)

    # ── Top-left: EPISODE N (large, bold) ──────────────────────────
    ep_label_font = _font(36, bold=True)
    ep_num_font = _font(140, bold=True)
    margin_x = 80
    margin_y = 60
    draw.text((margin_x, margin_y), "EPISODE", font=ep_label_font, fill=BRAND_LIGHT)
    draw.text((margin_x, margin_y + 38), str(episode_number), font=ep_num_font, fill=WHITE)

    # ── Center: "Title of Paper:" + italic title ───────────────────
    label_font = _font(34, bold=True)
    title_font = _font(60, serif=True, italic=True, bold=True)

    label_text = "Title of Paper"
    label_bbox = draw.textbbox((0, 0), label_text, font=label_font)
    label_w = label_bbox[2] - label_bbox[0]
    label_y = int(HEIGHT * 0.36)
    draw.text(((WIDTH - label_w) / 2, label_y), label_text, font=label_font, fill=BRAND_LIGHT)

    # Quoted, italic, wrapped title
    quoted = f"“{title}”"
    max_title_width = WIDTH - 240  # leave margins
    lines = _wrap_lines(quoted, title_font, draw, max_title_width)
    if len(lines) > 5:
        lines = lines[:5]
        lines[-1] = lines[-1].rstrip(",.;:!?”") + "…”"

    line_height = 78
    title_block_height = line_height * len(lines)
    title_start_y = label_y + 70
    for i, line in enumerate(lines):
        _draw_centered(draw, line, title_start_y + i * line_height, title_font, WHITE)

    # ── Bottom: authors credit ─────────────────────────────────────
    # Try to fetch authors from OpenAlex if not provided
    if not authors and paper_url:
        authors = _fetch_authors_from_openalex(paper_url)
    if authors:
        author_text = f"Original paper by {authors}"
    else:
        author_text = "Original paper by the authors cited in the description"

    author_font = _font(28, bold=True)
    # Wrap author text in case it's long
    author_lines = _wrap_lines(author_text, author_font, draw, WIDTH - 240)
    if len(author_lines) > 2:
        author_lines = author_lines[:2]
        author_lines[-1] = author_lines[-1].rstrip(",.") + "…"
    author_y = int(HEIGHT * 0.82)
    for i, line in enumerate(author_lines):
        _draw_centered(draw, line, author_y + i * 36, author_font, SOFT_WHITE)

    # ── Footer: narrated by speakforwater.com ──────────────────────
    footer_font = _font(24, bold=True)
    _draw_centered(
        draw,
        "Narrated by speakforwater.com",
        int(HEIGHT * 0.93),
        footer_font,
        BRAND_LIGHT,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    log.info(f"Cover saved: {output_path} ({output_path.stat().st_size // 1024} KB)")
    return output_path
