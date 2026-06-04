"""
SpeakForWater — title_simplifier.py

Uses Groq (free, open-source Llama 3.1) to convert a technical paper
title into a short, listener-friendly version (8–10 words) for the
cover image.

Examples:
  Technical : "Global Trends in Household Rainwater Tank Systems: A
               Multifaceted Review"
  Cover     : "Household rainwater tanks: a global review for homeowners"

Environment:
  GROQ_API_KEY              — required
  COVER_TITLE_MODEL         — default llama-3.1-8b-instant
  COVER_TITLE_MAX_CHARS     — default 100
  COVER_TITLE_TARGET_WORDS  — default 9 (range 8–10)
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
MAX_CHARS = int(os.environ.get("COVER_TITLE_MAX_CHARS", "100"))
TARGET_WORDS = int(os.environ.get("COVER_TITLE_TARGET_WORDS", "9"))

SYSTEM = """You rewrite scientific paper titles into short, plain-English titles for the cover image of a daily water-research podcast called SpeakForWater.

GOALS
- 8 to 10 words. Always. Not shorter, not longer.
- Plain language a farmer, homeowner, or city worker can understand at a glance.
- No jargon, no acronyms, no measurement units, no chemical formulas.
- Concrete, active, and informative — not abstract.
- No quotation marks around the result.
- No trailing period.
- Sentence case (only first word and proper nouns capitalised).

EXAMPLES
Technical: "Spatio-Temporal Evolution of Land-Use Patterns and Their Effects on Groundwater Recharge"
Cover    : How land use is reshaping groundwater across regions today

Technical: "Global Trends in Household Rainwater Tank Systems: A Multifaceted Review"
Cover    : Household rainwater tanks: a global review for homeowners

Technical: "Microplastic Contamination in Drinking Water from Hydraulic Fracturing Sites"
Cover    : Tiny plastics in tap water from oil fracking sites

Technical: "A Data-Centric Approach to Water Quality Prediction with a Focus on Ammonium"
Cover    : Predicting water quality with better data, focused on ammonium

Return ONLY the short title (8–10 words). No explanation, no quotes, no period."""

USER = """Original paper title:
{title}

Return ONLY the short, plain-English cover title (exactly 8 to 10 words)."""


def _clean(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    for ch in ['"', "'", "“", "”", "‘", "’", "`", "*"]:
        t = t.strip(ch).strip()
    if len(t) > MAX_CHARS:
        cut = t[:MAX_CHARS].rsplit(" ", 1)[0]
        t = cut.rstrip(",;:.") + "…"
    t = t.rstrip(".")
    t = re.sub(r"\s+", " ", t)
    return t


def simplify_title(original_title: str) -> Optional[str]:
    """Return a short, listener-friendly title (8-10 words). None on failure."""
    if not original_title or not original_title.strip():
        return None
    if not GROQ_API_KEY:
        log.warning("GROQ_API_KEY not set; title_simplifier returning None.")
        return None

    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": USER.format(title=original_title.strip())},
            ],
            max_tokens=80,
            temperature=0.3,
        )
        raw = (resp.choices[0].message.content or "").strip()
        short = _clean(raw)
        if not short or len(short) < 10:
            log.warning(f"title_simplifier returned too-short output: {raw!r}")
            return None
        word_count = len(short.split())
        log.info(f"Cover title ({word_count} words): {original_title!r} → {short!r}")
        return short
    except Exception as e:
        log.warning(f"title_simplifier failed ({e}); falling back to original.")
        return None
