"""Env-driven configuration in one place."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    gcp_project_id: str
    gcp_region: str
    bucket_name: str
    artifact_repo: str
    core_api_url: str
    environment: str
    request_timeout: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            gcp_project_id=_required("GCP_PROJECT_ID"),
            gcp_region=os.environ.get("GCP_REGION", "us-central1"),
            bucket_name=_required("BUCKET_NAME"),
            artifact_repo=os.environ.get("ARTIFACT_REPO", "agents"),
            core_api_url=os.environ.get("CORE_API_URL", "https://api.dooers.ai"),
            environment=os.environ.get("ENVIRONMENT", "dev"),
            request_timeout=int(os.environ.get("REQUEST_TIMEOUT", "10")),
        )


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required env var: {name}")
    return value
