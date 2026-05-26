"""Smoke tests — server boots, /health works, push route returns LB URL."""

from unittest.mock import AsyncMock, patch  # noqa: F401

import pytest
from fastapi.testclient import TestClient

from dooers_push.main import app


def test_health() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_app_metadata() -> None:
    assert app.title == "dooers-push"
    assert app.version == "0.1.0"


@pytest.mark.asyncio
async def test_push_returns_lb_url_when_pipeline_succeeds() -> None:
    """End-to-end shape check with all I/O mocked."""
    # This test will be filled in fully once Tasks 3.4-3.10 of the base plan land.
    # For now, assert the route exists and rejects an unauthorized request.
    client = TestClient(app)
    resp = client.post(
        "/v1/push/ag_7q4r",
        files={"archive": ("test.tar.gz", b"fake", "application/gzip")},
        headers={"Authorization": "Bearer bogus"},
    )
    # No core to verify the bogus token; expect 401 or 503.
    assert resp.status_code in (401, 503)
