"""AgentStore protocol + a file-based shim used when core's /agents endpoints
aren't ready. Once core lands, swap to HTTPCoreAgentStore (Task 2.2).

The shim writes to a JSON file (default ~/.dooers/agents.json). It's
intentionally simple so the demo flow runs without backend dependencies.
"""

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from dooers_protocol.agents import AgentRecord, CreateAgentRequest

DEFAULT_SHIM_PATH = Path.home() / ".dooers" / "agents.json"


class AgentStore(Protocol):
    def list(self) -> list[AgentRecord]: ...
    def create(self, req: CreateAgentRequest) -> AgentRecord: ...
    def get(self, agent_id: str) -> AgentRecord: ...


def _new_agent_id() -> str:
    return "ag_" + secrets.token_hex(4)


class FileShimAgentStore:
    """JSON-file-backed shim. NOT for production — for unblocking M2 demos."""

    def __init__(self, path: Path | None = None, *, owner_user_id: str) -> None:
        self.path = path or DEFAULT_SHIM_PATH
        self.owner_user_id = owner_user_id

    def _load(self) -> list[AgentRecord]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text())
        return [AgentRecord.model_validate(item) for item in raw]

    def _save(self, records: list[AgentRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([r.model_dump(mode="json") for r in records], indent=2)
        )

    def list(self) -> list[AgentRecord]:
        return [r for r in self._load() if r.owner_user_id == self.owner_user_id]

    def create(self, req: CreateAgentRequest) -> AgentRecord:
        now = datetime.now(timezone.utc)
        record = AgentRecord(
            agent_id=_new_agent_id(),
            name=req.name,
            owner_user_id=self.owner_user_id,
            runtime=req.runtime,
            env_required=req.env_required,
            deployed_url=None,
            created_at=now,
            updated_at=now,
        )
        records = self._load()
        records.append(record)
        self._save(records)
        return record

    def get(self, agent_id: str) -> AgentRecord:
        for r in self._load():
            if r.agent_id == agent_id and r.owner_user_id == self.owner_user_id:
                return r
        raise KeyError(agent_id)


# ---- HTTP-backed implementation (used when core's endpoints are ready) ----

import httpx  # noqa: E402

from dooers.core_client import CoreClientError  # noqa: E402


class HTTPCoreAgentStore:
    """Talks to core's /api/v1/agents endpoints.

    Implements the same interface as FileShimAgentStore. Switch is one
    line in agents.py — `_resolve_store()` picks based on env var.
    """

    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._timeout = timeout

    def _cookies(self) -> dict[str, str]:
        return {"auth": self.token}

    def list(self) -> list[AgentRecord]:
        try:
            r = httpx.get(
                f"{self.base_url}/api/v1/agents",
                cookies=self._cookies(),
                timeout=self._timeout,
            )
            r.raise_for_status()
            body = r.json()
            items = body.get("output", body) if isinstance(body, dict) else body
            return [AgentRecord.model_validate(item) for item in items]
        except httpx.HTTPError as e:
            raise CoreClientError(f"list_agents failed: {e}") from e

    def create(self, req: CreateAgentRequest) -> AgentRecord:
        try:
            r = httpx.post(
                f"{self.base_url}/api/v1/agents",
                cookies=self._cookies(),
                json=req.model_dump(),
                timeout=self._timeout,
            )
            r.raise_for_status()
            body = r.json()
            data = body.get("output", body)
            return AgentRecord.model_validate(data)
        except httpx.HTTPError as e:
            raise CoreClientError(f"create_agent failed: {e}") from e

    def get(self, agent_id: str) -> AgentRecord:
        try:
            r = httpx.get(
                f"{self.base_url}/api/v1/agents/{agent_id}",
                cookies=self._cookies(),
                timeout=self._timeout,
            )
            if r.status_code == 404:
                raise KeyError(agent_id)
            r.raise_for_status()
            body = r.json()
            data = body.get("output", body)
            return AgentRecord.model_validate(data)
        except httpx.HTTPError as e:
            raise CoreClientError(f"get_agent failed: {e}") from e
