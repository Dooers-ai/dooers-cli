"""Server-side client for core v2 agent metadata.

- GET  /api/v2/agents/:id   — fetch + verify ownership
- PATCH /api/v2/agents/:id  — write hostUrl after a successful push
"""

import httpx
from fastapi import HTTPException

from dooers_protocol.agents import AgentRecord
from dooers_protocol.auth import AuthSession


class CoreClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def get_agent(self, agent_id: str, session: AuthSession) -> AgentRecord:
        url = f"{self.base_url}/api/v2/agents/{agent_id}"
        async with httpx.AsyncClient() as c:
            r = await c.get(url, headers=self._headers(), timeout=self._timeout)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"core get_agent: HTTP {r.status_code}")
        data = r.json().get("data", {})
        owner = data.get("ownerUserId")
        if owner != session.user_id:
            raise HTTPException(status_code=403, detail=f"you do not own {agent_id}")
        return AgentRecord(
            agent_id=data["agentId"],
            name=data.get("name", agent_id),
            owner_user_id=owner,
            organization_id=data.get("organizationId"),
            host_url=data.get("hostUrl"),
        )

    async def patch_host_url(self, agent_id: str, host_url: str) -> None:
        url = f"{self.base_url}/api/v2/agents/{agent_id}"
        async with httpx.AsyncClient() as c:
            r = await c.patch(
                url, headers=self._headers(), json={"hostUrl": host_url}, timeout=self._timeout
            )
        if r.status_code not in (200, 204):
            raise HTTPException(
                status_code=502, detail=f"core patch_host_url: HTTP {r.status_code}"
            )
