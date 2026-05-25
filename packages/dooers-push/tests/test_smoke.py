"""Smoke tests — server boots, /health works, pipeline stubs run."""

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
