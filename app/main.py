"""
SpeakForWater API — Main FastAPI application.

Automates NotebookLM podcast generation via Playwright browser automation.
Designed to be called by an n8n workflow for daily podcast publishing.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes.podcast import router as podcast_router, downloads_router
from app.routes.health import router as health_router

# ── Logging ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── App lifecycle ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    logger.info("SpeakForWater API starting up...")
    logger.info(f"Storage directory: {settings.storage_dir}")
    logger.info(f"Cookies file: {settings.cookies_path}")
    logger.info(f"Headless mode: {settings.headless}")

    # Browser is lazily initialized on first request (not here)
    # to keep startup fast and avoid issues if cookies aren't ready yet.
    yield

    # Shutdown: close browser if it was started
    from app.routes.podcast import _automator
    if _automator:
        await _automator.stop()
    logger.info("SpeakForWater API shut down cleanly")


# ── App factory ────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="SpeakForWater API",
        description=(
            "Automates NotebookLM to generate podcast episodes from "
            "water resources research papers. Designed for use with n8n workflows."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS — allow n8n and local dev to call the API
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Tighten in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    app.include_router(podcast_router)
    app.include_router(downloads_router)
    app.include_router(health_router)

    return app


# ── Entry point ────────────────────────────────────────────────────────

main_app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:main_app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
    )
