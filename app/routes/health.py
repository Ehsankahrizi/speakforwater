"""
Health check endpoint — used by Docker, n8n, and monitoring.
"""

from fastapi import APIRouter
from app.models.schemas import HealthResponse
from app.services.notebooklm import NotebookLMAutomator

router = APIRouter(tags=["health"])


@router.get("/api/health", response_model=HealthResponse)
async def health_check():
    """Check if the API and browser are operational."""
    from app.routes.podcast import _automator

    browser_ready = False
    if _automator and _automator.is_ready:
        browser_ready = await _automator.health_check()

    return HealthResponse(
        status="ok",
        version="1.0.0",
        browser_ready=browser_ready,
    )
