"""Server-side client for the Dooers core API.

dooers-push only needs two calls against core for agent metadata:
- GET /agents/{id}        — fetch + verify ownership
- PATCH /agents/{id}      — write deployed_url after a successful push

POC scaffold.
"""

from dooers_protocol.agents import AgentRecord


class CoreClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    async def get_agent(self, agent_id: str) -> AgentRecord:
        raise NotImplementedError("scaffold")

    async def patch_agent_url(self, agent_id: str, deployed_url: str) -> None:
        raise NotImplementedError("scaffold")
