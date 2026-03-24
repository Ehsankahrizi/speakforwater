"""
Manages the SpeakForWater podcast prompt.

The canonical prompt lives in  config/podcast_prompt.yml  so it can be
edited in one place and every future episode stays consistent.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Default location of the YAML prompt file (relative to repo root)
_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[2] / "config" / "podcast_prompt.yml"


def _load_prompt_from_yaml(path: Path = _DEFAULT_PROMPT_PATH) -> str:
    """Read the prompt field from the YAML config file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        prompt = data.get("prompt", "").strip()
        if prompt:
            logger.info(f"Loaded podcast prompt from {path}")
            return prompt
        logger.warning(f"No 'prompt' field found in {path}")
    except FileNotFoundError:
        logger.warning(f"Prompt file not found: {path}")
    except Exception as e:
        logger.warning(f"Error reading prompt file: {e}")

    return ""


def get_prompt(custom_prompt: str | None = None) -> str:
    """
    Return the prompt to send to NotebookLM Audio Overview.

    Priority:
      1. custom_prompt argument (if provided)
      2. config/podcast_prompt.yml
    """
    if custom_prompt and custom_prompt.strip():
        return custom_prompt.strip()

    yaml_prompt = _load_prompt_from_yaml()
    if yaml_prompt:
        return yaml_prompt

    # Should never reach here if the YAML file exists
    raise FileNotFoundError(
        "No podcast prompt available. "
        "Make sure config/podcast_prompt.yml exists and contains a 'prompt' field."
    )
