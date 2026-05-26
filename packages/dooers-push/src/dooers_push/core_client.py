"""Server-side client for core's /agents endpoints.

dooers-push only needs two calls against core for agent metadata:
- GET /api/v1/agents/{id}     — fetch + verify ownership
- PATCH /api/v1/agents/{id}   — write deployed_url after a successful push

When DOOERS_USE_CORE_AGENTS != "1", get_agent fabricates a minimal record
from `agent_id` + the session user (matches the CLI's shim-mode behavior).
This keeps M3 demo possible even if core's endpoints aren't live yet.
"""

import os
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException

from dooers_protocol.agents import AgentRecord
from dooers_protocol.auth import AuthSession


class CoreClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._timeout = timeout

    async def get_agent(self, agent_id: str, fallback_session: AuthSession) -> AgentRecord:
        """Fetch agent record. Fabricates a minimal record when shim mode is active."""
        if os.environ.get("DOOERS_USE_CORE_AGENTS") != "1":
            now = datetime.now(timezone.utc)
            return AgentRecord(
                agent_id=agent_id,
                name=agent_id,
                owner_user_id=fallback_session.user_id,
                created_at=now,
                updated_at=now,
            )
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{self.base_url}/api/v1/agents/{agent_id}",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=self._timeout,
            )
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"core get_agent: HTTP {r.status_code}")
        body = r.json()
        return AgentRecord.model_validate(body.get("output", body))

    async def patch_agent_url(self, agent_id: str, deployed_url: str) -> None:
        """Update the agent's deployed_url. Best-effort in shim mode (no-op)."""
        if os.environ.get("DOOERS_USE_CORE_AGENTS") != "1":
            return
        async with httpx.AsyncClient() as c:
            r = await c.patch(
                f"{self.base_url}/api/v1/agents/{agent_id}",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"deployed_url": deployed_url},
                timeout=self._timeout,
            )
        if r.status_code not in (200, 204):
            raise HTTPException(
                status_code=502,
                detail=f"core patch_agent_url: HTTP {r.status_code}",
            )
