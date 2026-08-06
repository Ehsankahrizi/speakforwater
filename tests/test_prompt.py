"""
Tests for the podcast prompt in config/podcast_prompt.yml.

Two properties this file must hold, both learned the hard way:

1. It has to FIT. Only the first PROMPT_MAX_CHARS characters are sent. The
   file had grown to 5,932 chars against a 2,000 cap, so two thirds of it was
   discarded mid-sentence — including the casting rule that a previous session
   recorded as "the fix that worked". It never reached NotebookLM at all.

2. It must name nobody and gender nobody. NotebookLM chooses the two TTS
   voices itself, offers no control over them, and ignores instructions about
   speaker gender. Named speakers can therefore be voiced against their name,
   which is what listeners actually noticed. Unnamed speakers cannot.

Run with: pytest tests/test_prompt.py -v
"""

import re

import pytest

from app.services.notebooklm import PROMPT_MAX_CHARS
from app.services.prompt_manager import get_prompt


@pytest.fixture(scope="module")
def prompt() -> str:
    return get_prompt()


def test_prompt_fits_within_what_we_send(prompt):
    assert len(prompt) <= PROMPT_MAX_CHARS, (
        f"prompt is {len(prompt)} chars but only the first {PROMPT_MAX_CHARS} "
        f"are sent — the last {len(prompt) - PROMPT_MAX_CHARS} would be "
        "silently dropped. Shorten config/podcast_prompt.yml."
    )


def test_prompt_is_not_nearly_empty(prompt):
    """A truncated or mis-parsed YAML block would still 'fit'."""
    assert len(prompt) > 800, "prompt looks truncated or failed to load"


@pytest.mark.parametrize("name", ["Anna", "Ehsan"])
def test_prompt_names_no_speaker(prompt, name):
    assert name.lower() not in prompt.lower(), (
        f"{name!r} is back in the prompt. Named speakers get mismatched to "
        "voices NotebookLM assigns on its own and will not let us control; "
        "that mismatch is the bug this removal exists to prevent."
    )


def test_prompt_uses_no_gendered_pronouns(prompt):
    """A stray 'she' re-attaches a gender to a voice we cannot choose."""
    found = re.findall(r"\b(?:he|she|him|her|his|hers)\b", prompt, re.I)
    assert not found, f"gendered pronouns in prompt: {sorted(set(found))}"


def test_prompt_still_instructs_against_names(prompt):
    """The rule itself must survive edits, not just today's absence of names."""
    assert re.search(r"\bno names\b", prompt, re.I), (
        "the explicit no-names instruction is gone; without it the model may "
        "invent names of its own"
    )


def test_prompt_keeps_the_solo_open(prompt):
    """One voice alone first is our only lever on voice assignment."""
    assert re.search(r"\balone\b", prompt, re.I), (
        "the solo open is gone — it is the best available heuristic for how "
        "NotebookLM assigns the two voices"
    )


def test_prompt_keeps_the_source_restriction(prompt):
    """Without this the episode can drift into invented findings."""
    assert "ONLY" in prompt
    assert "does not mention" in prompt
