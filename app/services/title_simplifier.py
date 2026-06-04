"""
SpeakForWater — title_simplifier.py

Uses Groq (free, open-source Llama 3.1) to turn a technical paper title
into a short, listener-friendly version suitable for the cover image.

Examples:
  Technical : "Global Trends in Household Rainwater Tank Systems: A
               Multifaceted Review"
  Cover     : "Rainwater tanks at home — what the science says"

  Technical : "Spatio-Temporal Evolution of Land-Use Patterns and Their
               Effects on Groundwater Recharge in Semi-Arid Regions"
  Cover     : "How land use is reshaping our groundwater"

Environment:
  GROQ_API_KEY     — required (already set as GitHub secret)
  COVER_TITLE_MODEL — default llama-3.1-8b-instant
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from groq import Groq

log = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
MODEL = os.environ.get("COVER_TITLE_MODEL", "llama-3.1-8b-instant")

# Limit on cover title length (in characters); generator will try to stay
# below this. 80 fits comfortably inside the TV box at the default font.
MAX_CHARS = int(os.environ.get("COVER_TITLE_MAX_CHARS", "80"))

SYSTEM = """You rewrite scientific paper titles into short, plain-English titles for the cover image of a daily water-research podcast called SpeakForWater.

GOALS
- Convey what the paper is REALLY about in language a farmer, homeowner,
  or city worker can understand at a glance.
- 4 to 9 words. Maximum ~80 characters.
- No jargon, no acronyms, no measurement units.
- Active and concrete, not abstract.
- No quotation marks around the result.
- No trailing period.
- Sentence case (Capitalize first word; everything else lowercase
  except proper nouns).

EXAMPLES
Technical: "Spatio-Temporal Evolution of Land-Use Patterns and Their
            Effects on Groundwater Recharge"
Cover    : How land use shapes our groundwater

Technical: "Global Trends in Household Rainwater Tank Systems: A
            Multifaceted Review"
Cover    : Rainwater tanks at home, explained

Technical: "Microplastic Contamination in Drinking Water from
            Hydraulic Fracturing Sites"
Cover    : Microplastics in tap water near fracking sites

Return ONLY the short title. No explanation, no quotes, no period."""

USER = """Original paper title:
{title}

Return ONLY the short, plain-English cover title (4-9 words)."""


def _clean(text: str) -> str:
    """Strip quotes, extra whitespace, trailing punctuation."""
    if not text:
        return ""
    t = text.strip()
    # Remove surrounding quotes/braces
    for ch in ['"', "'", "“", "”", "‘", "’", "`", "*"]:
        t = t.strip(ch).strip()
    # Cap length cleanly at a word boundary
    if len(t) > MAX_CHARS:
        cut = t[:MAX_CHARS].rsplit(" ", 1)[0]
        t = cut.rstrip(",;:.") + "…"
    # Remove trailing period (cover doesn't need it)
    t = t.rstrip(".")
    # Collapse internal spaces
    t = re.sub(r"\s+", " ", t)
    return t


def simplify_title(original_title: str) -> Optional[str]:
    """Return a short, listener-friendly version of the paper title.

    Returns None on failure (caller should fall back to the original).
    """
    if not original_title or not original_title.strip():
        return None
    if not GROQ_API_KEY:
        log.warning("GROQ_API_KEY not set; returning original title.")
        return None

    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": USER.format(title=original_title.strip())},
            ],
            max_tokens=60,
            temperature=0.3,
        )
        raw = (resp.choices[0].message.content or "").strip()
        short = _clean(raw)
        if not short or len(short) < 6:
            log.warning(f"Simplifier returned too-short output: {raw!r}")
            return None
        log.info(f"Cover title: {original_title!r} → {short!r}")
        return short
    except Exception as e:
        log.warning(f"Title simplifier failed ({e}); falling back to original.")
        return None
