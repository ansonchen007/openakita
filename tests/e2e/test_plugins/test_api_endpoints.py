"""E2E tests for plugin REST API endpoints.

Run with: pytest tests/e2e/test_plugins/test_api_endpoints.py --noconftest -v
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from openakita.api.routes.plugins import PLUGIN_CATEGORIES, router


def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with the plugins router mounted."""
    app = FastAPI()
    app.include_router(router)
    app.state.agent = None
    return app


@pytest.fixture
def app(tmp_path):
    """Provide a test app with settings.project_root patched to tmp_path."""
    _app = _make_app()
    with patch("openakita.api.routes.plugins.settings") as mock_settings:
        mock_settings.project_root = tmp_path
        yield _app


@pytest.mark.asyncio
async def test_list_plugins_no_manager(app: FastAPI) -> None:
    """GET /api/plugins/list returns empty when no plugin_manager is available."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/plugins/list")

    assert resp.status_code == 200
    data = resp.json()
    assert data["plugins"] == []
    assert data["failed"] == {}


@pytest.mark.asyncio
async def test_hub_categories() -> None:
    """GET /api/plugins/hub/categories returns the category list."""
    app = _make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/plugins/hub/categories")

    assert resp.status_code == 200
    categories = resp.json()
    assert isinstance(categories, list)
    assert len(categories) == len(PLUGIN_CATEGORIES)

    slugs = {c["slug"] for c in categories}
    assert "channel" in slugs
    assert "llm" in slugs
    assert "tool" in slugs
    assert "skill" in slugs
    assert "mcp" in slugs


@pytest.mark.asyncio
async def test_hub_search() -> None:
    """GET /api/plugins/hub/search?q=test returns empty results for now."""
    app = _make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/plugins/hub/search", params={"q": "test"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "test"
    assert data["results"] == []
    assert data["total"] == 0
