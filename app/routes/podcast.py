"""
Podcast generation API routes.

POST /api/podcast/generate  — Start a new generation (async, returns task_id)
GET  /api/podcast/status/{task_id} — Poll generation status
GET  /api/podcast/tasks     — List recent tasks
GET  /api/downloads/{filename} — Download a generated MP3
"""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Security
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader

from app.config import settings
from app.models.schemas import (
    PodcastGenerateRequest,
    PodcastGenerateResponse,
    PodcastStatusResponse,
    TaskStatus,
)
from app.services.notebooklm import NotebookLMAutomator
from app.services.prompt_manager import get_prompt
from app.services.task_manager import task_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/podcast", tags=["podcast"])

# ── Auth ───────────────────────────────────────────────────────────────

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)):
    """Simple Bearer token authentication."""
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    # Accept "Bearer <key>" or just "<key>"
    token = api_key.replace("Bearer ", "").strip()
    if token != settings.api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return token


# ── Shared automator instance ─────────────────────────────────────────

_automator: NotebookLMAutomator | None = None
_automator_lock = asyncio.Lock()


async def get_automator() -> NotebookLMAutomator:
    """Get or create the shared NotebookLM automator instance."""
    global _automator
    async with _automator_lock:
        if _automator is None or not _automator.is_ready:
            _automator = NotebookLMAutomator()
            await _automator.start()
    return _automator


# ── Background generation task ─────────────────────────────────────────

async def _run_generation(task_id: str, request: PodcastGenerateRequest):
    """
    Runs in the background. Updates task_manager with progress.
    This is the actual work that drives Playwright.
    """
    try:
        automator = await get_automator()
        prompt = get_prompt(request.prompt)

        # Status callback that updates the task manager
        async def on_status(status: TaskStatus, message: str):
            await task_manager.update_status(task_id, status, message)

        result = await automator.generate_podcast(
            paper_url=request.paper_url,
            paper_title=request.paper_title,
            episode_number=request.episode_number,
            prompt=prompt,
            audio_format=request.format,
            language=request.language,
            length=request.length,
            on_status=on_status,
        )

        # Build the download URL
        mp3_filename = Path(result["mp3_path"]).name
        audio_url = f"/api/downloads/{mp3_filename}"

        await task_manager.update_status(
            task_id,
            TaskStatus.COMPLETED,
            "Podcast generated successfully",
            audio_url=audio_url,
            duration_seconds=result.get("duration_seconds"),
            notebook_id=result.get("notebook_id"),
        )

    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}", exc_info=True)
        await task_manager.update_status(
            task_id,
            TaskStatus.FAILED,
            f"Generation failed: {str(e)}",
            error=str(e),
        )


# ── Routes ─────────────────────────────────────────────────────────────

@router.post(
    "/generate",
    response_model=PodcastGenerateResponse,
    summary="Start podcast generation",
    description=(
        "Accepts a paper URL and episode details, starts an async background task "
        "that automates NotebookLM to generate a podcast. Returns a task_id to poll."
    ),
)
async def generate_podcast(
    request: PodcastGenerateRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_api_key),
):
    # Create a task entry
    task_id = task_manager.create_task()

    # Launch background generation
    background_tasks.add_task(_run_generation, task_id, request)

    return PodcastGenerateResponse(
        status=TaskStatus.QUEUED,
        task_id=task_id,
        message=(
            f"Generation started for episode {request.episode_number}. "
            f"Poll GET /api/podcast/status/{task_id} for progress."
        ),
    )


@router.get(
    "/status/{task_id}",
    response_model=PodcastStatusResponse,
    summary="Check generation status",
)
async def get_status(
    task_id: str,
    _: str = Depends(verify_api_key),
):
    task = task_manager.get_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return task


@router.get(
    "/tasks",
    response_model=list[PodcastStatusResponse],
    summary="List recent generation tasks",
)
async def list_tasks(
    limit: int = 20,
    _: str = Depends(verify_api_key),
):
    return task_manager.list_tasks(limit=limit)


# ── Downloads ──────────────────────────────────────────────────────────

downloads_router = APIRouter(prefix="/api/downloads", tags=["downloads"])


@downloads_router.get(
    "/{filename}",
    summary="Download a generated MP3 file",
)
async def download_file(filename: str):
    """Serve a generated MP3 file. No auth required (can be used as direct link)."""
    # Sanitize filename to prevent path traversal
    safe_name = Path(filename).name
    file_path = settings.storage_dir / safe_name

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File {safe_name} not found")

    return FileResponse(
        path=str(file_path),
        media_type="audio/mpeg",
        filename=safe_name,
    )
