"""
Pydantic models for API request / response validation.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, HttpUrl


# ── Enums ──────────────────────────────────────────────────────────────

class AudioFormat(str, Enum):
    DEEP_DIVE = "deep_dive"
    BRIEF = "brief"
    CRITIQUE = "critique"
    DEBATE = "debate"


class AudioLength(str, Enum):
    SHORT = "short"
    DEFAULT = "default"
    LONG = "long"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    CREATING_NOTEBOOK = "creating_notebook"
    ADDING_SOURCE = "adding_source"
    CONFIGURING_AUDIO = "configuring_audio"
    GENERATING = "generating"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Requests ───────────────────────────────────────────────────────────

class PodcastGenerateRequest(BaseModel):
    """Request body for POST /api/podcast/generate"""
    paper_url: str = Field(
        ...,
        description="Full URL to the open-access journal paper",
        examples=["https://www.sciencedirect.com/science/article/pii/S0022169423001234"],
    )
    paper_title: str = Field(
        ...,
        description="Short title for the episode",
        examples=["Flood Risk Mapping with SAR Data"],
    )
    episode_number: int = Field(
        ..., ge=1,
        description="Episode number (used in filename)",
    )
    prompt: str | None = Field(
        None,
        description="Custom prompt for the audio overview. If omitted, uses the default SpeakForWater prompt.",
    )
    format: AudioFormat = Field(
        AudioFormat.DEEP_DIVE,
        description="Audio overview format",
    )
    language: str = Field(
        "English",
        description="Language for the audio overview",
    )
    length: AudioLength = Field(
        AudioLength.DEFAULT,
        description="Length of the generated audio",
    )


# ── Responses ──────────────────────────────────────────────────────────

class PodcastGenerateResponse(BaseModel):
    """Returned immediately when a generation task is accepted."""
    status: TaskStatus
    task_id: str
    message: str


class PodcastStatusResponse(BaseModel):
    """Returned by GET /api/podcast/status/{task_id}"""
    task_id: str
    status: TaskStatus
    progress_message: str = ""
    audio_url: str | None = None
    duration_seconds: int | None = None
    notebook_id: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    browser_ready: bool = False
