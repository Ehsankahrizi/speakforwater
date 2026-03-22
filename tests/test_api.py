"""
Tests for the SpeakForWater API.

Run with: pytest tests/ -v
"""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from app.main import main_app
from app.config import settings


@pytest.fixture
def api_key():
    return settings.api_key


@pytest.fixture
async def client():
    transport = ASGITransport(app=main_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Health ─────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_health(client: AsyncClient):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


# ── Auth ───────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_generate_requires_auth(client: AsyncClient):
    resp = await client.post("/api/podcast/generate", json={
        "paper_url": "https://example.com/paper",
        "paper_title": "Test Paper",
        "episode_number": 1,
    })
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_generate_rejects_bad_key(client: AsyncClient):
    resp = await client.post(
        "/api/podcast/generate",
        json={
            "paper_url": "https://example.com/paper",
            "paper_title": "Test Paper",
            "episode_number": 1,
        },
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 403


# ── Generate endpoint ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_generate_returns_task_id(client: AsyncClient, api_key: str):
    """Test that /generate accepts a valid request and returns a task_id."""
    with patch("app.routes.podcast.get_automator") as mock_auto:
        # Mock the automator so we don't actually launch a browser
        mock_instance = AsyncMock()
        mock_instance.generate_podcast = AsyncMock(return_value={
            "mp3_path": "/app/storage/downloads/ep001.mp3",
            "notebook_id": "test-notebook-123",
            "duration_seconds": 420,
        })
        mock_auto.return_value = mock_instance

        resp = await client.post(
            "/api/podcast/generate",
            json={
                "paper_url": "https://www.sciencedirect.com/science/article/pii/S0022169423001234",
                "paper_title": "Flood Risk Mapping with SAR Data",
                "episode_number": 1,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert "task_id" in data
    assert len(data["task_id"]) == 12


# ── Status endpoint ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_status_not_found(client: AsyncClient, api_key: str):
    resp = await client.get(
        "/api/podcast/status/nonexistent",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert resp.status_code == 404


# ── Downloads ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_download_not_found(client: AsyncClient):
    resp = await client.get("/api/downloads/nonexistent.mp3")
    assert resp.status_code == 404


# ── Input validation ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_invalid_episode_number(client: AsyncClient, api_key: str):
    resp = await client.post(
        "/api/podcast/generate",
        json={
            "paper_url": "https://example.com/paper",
            "paper_title": "Test",
            "episode_number": 0,  # must be >= 1
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert resp.status_code == 422  # validation error
