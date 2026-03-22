"""
In-memory task manager for tracking podcast generation jobs.

Each generation runs as a background asyncio task. The API returns
a task_id immediately, and the client polls GET /status/{task_id}.
"""

from __future__ import annotations

import asyncio
import uuid
import logging
from datetime import datetime, timezone

from app.models.schemas import TaskStatus, PodcastStatusResponse

logger = logging.getLogger(__name__)


class TaskManager:
    """Tracks running and completed generation tasks."""

    def __init__(self):
        self._tasks: dict[str, PodcastStatusResponse] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def create_task(self) -> str:
        """Create a new task entry and return its ID."""
        task_id = uuid.uuid4().hex[:12]
        self._tasks[task_id] = PodcastStatusResponse(
            task_id=task_id,
            status=TaskStatus.QUEUED,
            progress_message="Task queued",
            started_at=datetime.now(timezone.utc),
        )
        self._locks[task_id] = asyncio.Lock()
        return task_id

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        message: str = "",
        audio_url: str | None = None,
        duration_seconds: int | None = None,
        notebook_id: str | None = None,
        error: str | None = None,
    ):
        """Update task status (called from the background generation task)."""
        if task_id not in self._tasks:
            return

        task = self._tasks[task_id]
        task.status = status
        task.progress_message = message

        if audio_url:
            task.audio_url = audio_url
        if duration_seconds:
            task.duration_seconds = duration_seconds
        if notebook_id:
            task.notebook_id = notebook_id
        if error:
            task.error = error
        if status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            task.completed_at = datetime.now(timezone.utc)

        logger.info(f"Task {task_id}: {status.value} — {message}")

    def get_status(self, task_id: str) -> PodcastStatusResponse | None:
        """Get current status of a task."""
        return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 20) -> list[PodcastStatusResponse]:
        """List most recent tasks."""
        tasks = sorted(
            self._tasks.values(),
            key=lambda t: t.started_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return tasks[:limit]

    def cleanup_old(self, max_age_hours: int = 24):
        """Remove completed/failed tasks older than max_age_hours."""
        now = datetime.now(timezone.utc)
        to_remove = []
        for task_id, task in self._tasks.items():
            if task.completed_at:
                age = (now - task.completed_at).total_seconds() / 3600
                if age > max_age_hours:
                    to_remove.append(task_id)
        for task_id in to_remove:
            del self._tasks[task_id]
            del self._locks[task_id]


# Singleton instance
task_manager = TaskManager()
