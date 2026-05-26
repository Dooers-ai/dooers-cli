"""Pytest fixtures for dooers-push tests.

Provides dummy GCP env vars so Settings.from_env() succeeds in tests
without requiring a real GCP project to be configured.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _dummy_gcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required GCP env vars to dummy values for the test session."""
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("GCP_REGION", "us-central1")
    monkeypatch.setenv("ARTIFACT_REPO", "agents")
    monkeypatch.setenv("CORE_API_URL", "https://api.test.dooers.ai")
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("DOOERS_LB_DOMAIN", "agents.dooers.ai")
    monkeypatch.setenv("DOOERS_LB_URL_MAP", "dooers-agents-url-map")
