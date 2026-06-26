"""v2 core-backed agent store. Talks to /api/v2/agents with {success,data}."""

import json

import httpx
from dooers.protocol.agents import AgentRecord, CreateAgentRequest


class AgentStoreError(RuntimeError):
    pass


def _data(resp: httpx.Response):
    # The body may not be JSON: framework-level errors (e.g. a 403 "Forbidden")
    # and empty success bodies (204) are common. Never let json() crash the CLI.
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        body = None

    if isinstance(body, dict) and body.get("success") is False:
        raise AgentStoreError(body.get("error", {}).get("message", f"HTTP {resp.status_code}"))
    if resp.status_code >= 400:
        detail = ""
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                detail = err.get("message", "")
            detail = detail or body.get("message", "")
        detail = detail or (resp.text or "").strip()
        msg = f"HTTP {resp.status_code}"
        raise AgentStoreError(f"{msg}: {detail}" if detail else msg)
    return body.get("data", body) if isinstance(body, dict) else body


def _record(d: dict) -> AgentRecord:
    return AgentRecord(
        agent_id=d["agentId"],
        name=d.get("name", ""),
        owner_user_id=d.get("ownerUserId"),
        organization_id=d.get("organizationId"),
        host_url=d.get("hostUrl"),
        status=d.get("status"),
        runtime_api_key=d.get("runtimeApiKey"),
    )


class HTTPCoreAgentStore:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0) -> None:
        self.api = base_url.rstrip("/") + "/api/v2"
        self.token = token
        self._timeout = timeout

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _post_json(self, url: str, body: dict | None = None) -> httpx.Response:
        """POST with application/json — required to bypass core CSRF for Bearer-only clients."""
        return httpx.post(
            url,
            headers={**self._h(), "Content-Type": "application/json"},
            json=body if body is not None else {},
            timeout=self._timeout,
        )

    def create(self, req: CreateAgentRequest) -> AgentRecord:
        body = json.dumps({"organizationId": req.organization_id, "name": req.name})
        r = httpx.post(
            f"{self.api}/agents",
            headers={**self._h(), "content-type": "application/json"},
            content=body,
            timeout=self._timeout,
        )
        return _record(_data(r))

    def list_by_org(self, organization_id: str) -> list[AgentRecord]:
        url = f"{self.api}/agents/organization/{organization_id}"
        r = httpx.get(url, headers=self._h(), timeout=self._timeout)
        return [_record(d) for d in _data(r)]

    def get(self, agent_id: str) -> AgentRecord:
        r = httpx.get(f"{self.api}/agents/{agent_id}", headers=self._h(), timeout=self._timeout)
        if r.status_code == 404:
            raise KeyError(agent_id)
        return _record(_data(r))

    def update(self, agent_id: str, patch: dict) -> AgentRecord:
        r = httpx.patch(
            f"{self.api}/agents/{agent_id}", headers=self._h(), json=patch, timeout=self._timeout
        )
        return _record(_data(r))

    def archive(self, agent_id: str) -> None:
        r = self._post_json(f"{self.api}/agents/{agent_id}/archive")
        _data(r)  # raises AgentStoreError on {success: false}; no record to parse

    def delete(self, agent_id: str) -> None:
        r = httpx.delete(
            f"{self.api}/agents/{agent_id}", headers=self._h(), timeout=self._timeout
        )
        _data(r)  # success body is {success, message} with no data key — do NOT call _record()

    def regenerate_runtime_api_key(self, agent_id: str) -> AgentRecord:
        r = self._post_json(f"{self.api}/agents/{agent_id}/runtime-api-key/regenerate")
        return _record(_data(r))
