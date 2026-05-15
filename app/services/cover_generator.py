"""
SpeakForWater — cover_generator.py

Uses public/cover.png as a template (TV screen mockup) and overlays:
  - Episode number
  - Paper title (auto-fitted)
  - Authors and year

Inside the TV area boundaries. Auto-shrinks font if text overflows.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

WHITE = (245, 248, 252)
SOFT_BLUE = (155, 200, 230)
ACCENT = (255, 220, 110)

# Linux / macOS font candidates
SERIF_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
]
SERIF_ITALIC_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf",
]
SANS_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

# TV-screen bounding box inside the cover.png template (as fractions of image
# size). Defaults assume the TV occupies roughly the center 60% of the image.
# Override via env vars TV_X1, TV_Y1, TV_X2, TV_Y2 (as 0..1 fractions) if your
# template has different proportions.
TV_X1 = float(os.environ.get("TV_X1", "0.18"))
TV_Y1 = float(os.environ.get("TV_Y1", "0.20"))
TV_X2 = float(os.environ.get("TV_X2", "0.82"))
TV_Y2 = float(os.environ.get("TV_Y2", "0.78"))

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _font(size: int, candidates: list[str]) -> ImageFont.FreeTypeFont:
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


def _wrap_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Greedy word-wrap so each line fits within max_width."""
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        trial = " ".join(cur + [w])
        bbox = draw.textbbox((0, 0), trial, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            cur.append(w)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    candidates: list[str],
    max_width: int,
    max_height: int,
    *,
    start_size: int = 64,
    min_size: int = 18,
    line_spacing: int = 8,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Find the largest font size at which `text` fits within (max_width, max_height)."""
    size = start_size
    while size >= min_size:
        font = _font(size, candidates)
        lines = _wrap_lines(draw, text, font, max_width)
        ascent, descent = font.getmetrics()
        line_h = ascent + descent + line_spacing
        total_h = line_h * len(lines)
        if total_h <= max_height:
            return font, lines
        size -= 2
    font = _font(min_size, candidates)
    return font, _wrap_lines(draw, text, font, max_width)


def _draw_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    x_center: int,
    y_top: int,
    fill,
    line_spacing: int = 8,
) -> int:
    """Draw lines centered horizontally starting at y_top. Returns the y after the block."""
    ascent, descent = font.getmetrics()
    line_h = ascent + descent + line_spacing
    y = y_top
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        draw.text((x_center - w / 2, y), line, font=font, fill=fill)
        y += line_h
    return y


def _fetch_authors_from_openalex(paper_url: str) -> tuple[Optional[str], Optional[str]]:
    """Best-effort: return (authors_string, year_string) from OpenAlex."""
    if not paper_url:
        return None, None
    try:
        api = "https://api.openalex.org/works/" + urllib.parse.quote(paper_url, safe="")
        req = urllib.request.Request(api, headers={"User-Agent": "SpeakForWater/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        names = []
        for a in data.get("authorships", []) or []:
            au = a.get("author") or {}
            n = au.get("display_name")
            if n:
                names.append(n)
        year = data.get("publication_year")
        if names:
            if len(names) <= 3:
                authors_str = ", ".join(names)
            else:
                authors_str = ", ".join(names[:2]) + f", et al."
            return authors_str, (str(year) if year else None)
    except Exception as e:
        log.info(f"Could not fetch authors from OpenAlex ({e}); skipping.")
    return None, None


def make_cover(
    output_path: Path,
    title: str,
    episode_number: int,
    background: Optional[Path] = None,
    paper_url: str = "",
    authors: Optional[str] = None,
    year: Optional[str] = None,
    template: Optional[Path] = None,
) -> Path:
    """
    Render a cover PNG using public/cover.png as a TV template.

    `background` is accepted for backward compatibility and ignored when
    `template` (or public/cover.png) exists.
    """
    title = _strip_html(title)

    # Resolve template path
    if template and template.exists():
        tpl_path = template
    else:
        # Try common locations
        for candidate in [
            Path("public/cover.png"),
            Path("./public/cover.png"),
            Path(os.environ.get("GITHUB_WORKSPACE", ".")) / "public" / "cover.png",
        ]:
            if candidate.exists():
                tpl_path = candidate
                break
        else:
            tpl_path = None

    if not tpl_path or not tpl_path.exists():
        log.warning("No cover.png template found — falling back to solid blue cover.")
        img = Image.new("RGB", (1920, 1080), (10, 37, 64))
    else:
        img = Image.open(tpl_path).convert("RGB")

    W, H = img.size
    draw = ImageDraw.Draw(img)

    # TV bounding box in absolute pixels
    tv_x1 = int(W * TV_X1)
    tv_y1 = int(H * TV_Y1)
    tv_x2 = int(W * TV_X2)
    tv_y2 = int(H * TV_Y2)
    tv_w = tv_x2 - tv_x1
    tv_h = tv_y2 - tv_y1
    tv_cx = (tv_x1 + tv_x2) // 2

    # Fetch authors/year if not provided
    if (not authors or not year) and paper_url:
        fetched_authors, fetched_year = _fetch_authors_from_openalex(paper_url)
        authors = authors or fetched_authors
        year = year or fetched_year

    # ── Layout inside TV ────────────────────────────────────────────
    # Reserve vertical space for each block (approximate, fonts will fit
    # within these heights):
    #   30% for Episode label + number
    #   55% for title
    #   15% for authors/year
    pad_y = int(tv_h * 0.06)
    ep_h = int(tv_h * 0.22)
    title_h = int(tv_h * 0.50)
    authors_h = int(tv_h * 0.22)

    cursor_y = tv_y1 + pad_y

    # Episode label
    ep_label_font, _ = _fit_text(
        draw, "EPISODE", SANS_BOLD_CANDIDATES,
        max_width=tv_w, max_height=int(ep_h * 0.32),
        start_size=44, min_size=18,
    )
    _draw_block(draw, ["EPISODE"], ep_label_font, tv_cx, cursor_y, SOFT_BLUE)
    cursor_y += int(ep_h * 0.32)

    # Episode number (huge)
    ep_num_font, _ = _fit_text(
        draw, str(episode_number), SANS_BOLD_CANDIDATES,
        max_width=tv_w, max_height=int(ep_h * 0.68),
        start_size=160, min_size=40,
    )
    _draw_block(draw, [str(episode_number)], ep_num_font, tv_cx, cursor_y, ACCENT)
    cursor_y = tv_y1 + pad_y + ep_h

    # Title — italic, fitted
    title_text = f"“{title}”"
    title_font, title_lines = _fit_text(
        draw, title_text, SERIF_ITALIC_CANDIDATES,
        max_width=int(tv_w * 0.92), max_height=title_h,
        start_size=64, min_size=20,
    )
    cursor_y = _draw_block(draw, title_lines, title_font, tv_cx, cursor_y, WHITE)

    # Authors + year
    authors_line = ""
    if authors and year:
        authors_line = f"{authors} — {year}"
    elif authors:
        authors_line = authors
    elif year:
        authors_line = f"Published {year}"
    else:
        authors_line = "Original authors cited in the description"

    auth_font, auth_lines = _fit_text(
        draw, authors_line, SERIF_BOLD_CANDIDATES,
        max_width=int(tv_w * 0.92), max_height=authors_h,
        start_size=34, min_size=16,
    )
    auth_y_top = tv_y2 - pad_y - (auth_font.getmetrics()[0] + auth_font.getmetrics()[1] + 8) * len(auth_lines)
    _draw_block(draw, auth_lines, auth_font, tv_cx, auth_y_top, SOFT_BLUE)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    log.info(f"Cover saved: {output_path} ({output_path.stat().st_size // 1024} KB)")
    return output_path
