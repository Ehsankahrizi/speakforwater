"""
SpeakForWater — paper_ranker.py

AI-powered paper ranking using Groq's free API + open-source Llama 3.1.

Takes a list of candidate papers from OpenAlex, scores each one for
podcast-fit, and returns only papers above a configurable score threshold.

Free tier limits (more than enough for this use case):
- 14,400 requests/day
- 6,000 tokens/min
- 30 requests/min

Get your free API key at: https://console.groq.com

Environment:
  GROQ_API_KEY  — required (free; sign up at console.groq.com)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from groq import Groq

log = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
RANKER_MODEL = os.environ.get(
    "RANKER_MODEL",
    "llama-3.1-8b-instant",  # fast + free; upgrade to llama-3.3-70b-versatile for better judgment
)
SCORE_THRESHOLD = float(os.environ.get("RANKER_THRESHOLD", "7.0"))
RATE_LIMIT_DELAY = float(os.environ.get("RANKER_DELAY_S", "2.0"))  # 30 req/min = 1 req/2s

SYSTEM_PROMPT = """You are a science editor evaluating water research papers for a daily podcast aimed at a general audience (farmers, water managers, citizens, policymakers — NOT scientists).

You score each paper on a 1-10 scale across four dimensions and return a JSON object.

DIMENSIONS:
- novelty (1-10): Genuinely new findings vs incremental / review / survey
- impact (1-10): Real-world relevance to many people
- accessibility (1-10): Can methodology and findings be explained in plain language
- audience_fit (1-10): Would a non-scientist find this engaging for 10 min

REJECT (overall_score: 0):
- Pure literature reviews / surveys with no new findings
- Highly technical with no real-world hook
- Off-topic (not about water)
- Predatory journals or low quality

ACCEPT (overall_score >= 7):
- Novel, real-world relevance, explainable simply, clear "so what"

ALWAYS return ONLY a valid JSON object, no other text, no markdown."""

USER_TEMPLATE = """Score this paper for the SpeakForWater podcast.

TITLE: {title}
JOURNAL: {journal}
YEAR: {year}
ABSTRACT: {abstract}

Return JSON exactly:
{{
  "novelty": <1-10>,
  "impact": <1-10>,
  "accessibility": <1-10>,
  "audience_fit": <1-10>,
  "overall_score": <average 1-10>,
  "topics": ["topic1", "topic2"],
  "reason": "<one sentence>"
}}"""


def _client() -> Groq:
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY not set. Get a free key at console.groq.com "
            "and add it as a GitHub secret."
        )
    return Groq(api_key=GROQ_API_KEY)


def rank_paper(paper: dict[str, Any]) -> dict[str, Any] | None:
    """Score a single paper. Returns enriched paper or None if rejected."""
    client = _client()

    title = (paper.get("title") or "").strip()
    abstract = (paper.get("abstract") or "").strip() or "(no abstract available)"
    journal = (paper.get("journal") or "Unknown").strip()
    year = paper.get("year") or paper.get("date", "")[:4] or "Unknown"

    if len(abstract) > 1500:
        abstract = abstract[:1500] + "…"

    prompt = USER_TEMPLATE.format(
        title=title, abstract=abstract, journal=journal, year=year
    )

    try:
        resp = client.chat.completions.create(
            model=RANKER_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=300,
            temperature=0.2,
        )
    except Exception as e:
        log.warning(f"  ! Groq API failed for '{title[:60]}': {e}")
        return None

    raw = resp.choices[0].message.content.strip() if resp.choices else ""

    # Strip code fences if the model wrapped JSON
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()

    try:
        data = json.loads(raw)
    except Exception as e:
        log.warning(f"  ! Could not parse response for '{title[:60]}': {e}")
        log.warning(f"    raw: {raw[:200]}")
        return None

    overall = float(data.get("overall_score", 0))
    if overall < SCORE_THRESHOLD:
        log.info(
            f"  - rejected ({overall:.1f}/10): {title[:70]} — "
            f"{data.get('reason', '')}"
        )
        return None

    enriched = dict(paper)
    enriched["score"] = overall
    enriched["topics"] = data.get("topics", [])
    enriched["reason"] = data.get("reason", "")
    enriched["novelty"] = data.get("novelty", 0)
    enriched["impact"] = data.get("impact", 0)
    enriched["accessibility"] = data.get("accessibility", 0)
    enriched["audience_fit"] = data.get("audience_fit", 0)

    log.info(
        f"  ✓ accepted ({overall:.1f}/10): {title[:70]} — "
        f"{data.get('reason', '')}"
    )
    return enriched


def rank_papers(
    papers: list[dict[str, Any]],
    max_keep: int = 20,
) -> list[dict[str, Any]]:
    """Rank papers, return only those above threshold, sorted by score."""
    if not papers:
        return []

    if not GROQ_API_KEY:
        log.warning(
            "GROQ_API_KEY not set — skipping AI ranking, "
            "returning papers as-is."
        )
        return papers[:max_keep]

    log.info(
        f"\nRanking {len(papers)} candidate papers with "
        f"{RANKER_MODEL} via Groq…"
    )

    accepted = []
    for i, paper in enumerate(papers, 1):
        log.info(f"[{i}/{len(papers)}] {paper.get('title', 'Untitled')[:60]}…")
        ranked = rank_paper(paper)
        if ranked:
            accepted.append(ranked)
        # Polite rate limiting (30 req/min on free tier)
        if i < len(papers):
            time.sleep(RATE_LIMIT_DELAY)

    accepted.sort(key=lambda p: p.get("score", 0), reverse=True)
    log.info(
        f"\nRanking done. {len(accepted)} of {len(papers)} papers passed "
        f"the score >= {SCORE_THRESHOLD} threshold."
    )

    return accepted[:max_keep]
