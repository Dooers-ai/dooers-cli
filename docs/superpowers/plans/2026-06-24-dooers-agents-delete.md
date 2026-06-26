# `dooers agents delete` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `dooers agents delete <agent-id> [--archive] [--yes/-y]` that tears down the deployed Cloud Run service + LB rule (via `dooers-push`) and then deletes the core agent record.

**Architecture:** Three layers. `dooers-protocol` gains a `Teardown{Request,Response}` wire model + an `AgentRecord.status` field. `dooers-cli` gains store `archive`/`delete` methods, a `PushClient.teardown` call, and a `delete` command that orchestrates: GET → active-state pre-check → confirm → (archive) → push teardown → core record delete. The `dooers-push` teardown endpoint itself is implemented in its own repo (`Dooers-ai/dooers-push`) — out of scope for this plan, tracked in the Cross-Repo Dependencies section.

**Tech Stack:** Python 3.10+, `uv`, `typer`, `httpx`, `pydantic` v2, `respx` (HTTP mocking), `pytest`, `ruff`, `mypy`, `hatchling`.

## Global Constraints

- Each package is independent: `cd packages/<pkg>` before running its tasks. There is no top-level orchestrator.
- Both packages must pass `uv run poe dev` (ruff + mypy + pytest) at the end of every task that touches them.
- `mypy` runs as `mypy -p dooers.protocol` / `mypy -p dooers.cli` (PEP 420 namespace packages; `mypy_path=src`, `explicit_package_bases=true` already configured).
- Every CLI ↔ `dooers-push` request/response is a Pydantic model in `dooers-protocol`.
- Teardown runs **before** the core record delete (push verifies ownership by reading the still-existing core record). Teardown is idempotent.
- Error paths in the CLI must be graceful: `typer.echo(..., err=True)` + `raise typer.Exit(code=1)` — never a traceback.
- Version bumps (release prep, Task 5): `dooers-protocol` 0.4.0 → 0.5.0, `dooers-cli` 0.5.0 → 0.6.0 (dep `dooers-protocol>=0.5.0`). Publish + push deploy are manual/external.
- Out of scope: bulk/multi-id delete, a standalone `dooers agents archive` command, clearing products/active workers.

---

## File Structure

**`dooers-protocol` (`packages/dooers-protocol/`)**
- Create `src/dooers/protocol/teardown.py` — `TeardownRequest`, `TeardownResponse`, `format_teardown_result`.
- Modify `src/dooers/protocol/agents.py` — add `status: str | None = None` to `AgentRecord`.
- Create `tests/test_teardown.py` — model + formatter tests.
- Modify `tests/test_agents_models.py` — add `AgentRecord.status` test.
- Modify `pyproject.toml` — version 0.4.0 → 0.5.0 (Task 5).

**`dooers-cli` (`packages/dooers-cli/`)**
- Modify `src/dooers/cli/agent_store.py` — `_record` reads `status`; add `archive`, `delete`.
- Modify `src/dooers/cli/push_client.py` — add `teardown`.
- Modify `src/dooers/cli/agents.py` — refactor `_store` to return the token; add `delete` command.
- Modify `src/dooers/cli/cli.py` — agents help string.
- Modify `tests/test_agent_store.py` — `archive`/`delete`/`status` tests.
- Create `tests/test_push_teardown.py` — `PushClient.teardown` tests.
- Create `tests/test_agents_delete.py` — `delete` command tests (CliRunner + respx).
- Modify `pyproject.toml` — version 0.5.0 → 0.6.0, dep `dooers-protocol>=0.5.0` (Task 5).

---

## Cross-Repo Dependencies (out of scope for this plan)

`dooers-push` (`Dooers-ai/dooers-push`, separate repo) must implement
`DELETE /v1/agents/{agent_id}?env=`: Bearer auth → verify ownership against core →
idempotently delete the Cloud Run service (project `dooers-agents`, region
`southamerica-east1`) + remove the LB path rule → return `TeardownResponse`. It consumes
`dooers-protocol>=0.5.0` from PyPI. **The CLI `delete` command is not end-to-end functional
until this endpoint is live**, but every CLI/protocol task below is fully unit-testable here
with mocked HTTP (`respx`).

---

### Task 1: `dooers-protocol` — Teardown models + `AgentRecord.status`

**Files:**
- Create: `packages/dooers-protocol/src/dooers/protocol/teardown.py`
- Modify: `packages/dooers-protocol/src/dooers/protocol/agents.py:9-16` (`AgentRecord`)
- Test: `packages/dooers-protocol/tests/test_teardown.py`
- Test: `packages/dooers-protocol/tests/test_agents_models.py`

**Interfaces:**
- Produces:
  - `TeardownRequest(agent_id: str, env: str = "prod")`
  - `TeardownResponse(agent_id: str, service_deleted: bool, lb_rule_removed: bool, service_name: str | None = None, error: str | None = None)`
  - `format_teardown_result(resp: TeardownResponse) -> str`
  - `AgentRecord.status: str | None = None`

- [ ] **Step 1: Write the failing tests**

Create `packages/dooers-protocol/tests/test_teardown.py`:

```python
"""Teardown wire models + display helper shared with the CLI."""

from dooers.protocol.teardown import (
    TeardownRequest,
    TeardownResponse,
    format_teardown_result,
)


def test_teardown_request_env_defaults_to_prod() -> None:
    assert TeardownRequest(agent_id="a1").env == "prod"


def test_teardown_response_roundtrip() -> None:
    resp = TeardownResponse.model_validate(
        {"agent_id": "a1", "service_deleted": True, "lb_rule_removed": True, "service_name": "svc"}
    )
    assert resp.service_deleted is True
    assert resp.lb_rule_removed is True
    assert resp.service_name == "svc"
    assert resp.error is None


def test_format_both_removed() -> None:
    resp = TeardownResponse(agent_id="a1", service_deleted=True, lb_rule_removed=True)
    assert format_teardown_result(resp) == "Cloud Run service deleted; load-balancer rule removed."


def test_format_service_only() -> None:
    resp = TeardownResponse(agent_id="a1", service_deleted=True, lb_rule_removed=False)
    assert format_teardown_result(resp) == "Cloud Run service deleted."


def test_format_nothing_removed() -> None:
    resp = TeardownResponse(agent_id="a1", service_deleted=False, lb_rule_removed=False)
    assert format_teardown_result(resp) == "No deployed service found — record only."
```

Add to `packages/dooers-protocol/tests/test_agents_models.py` (after `test_agent_record_v2_shape`):

```python
def test_agent_record_status_optional_and_settable() -> None:
    assert AgentRecord(agent_id="a1", name="x").status is None
    assert AgentRecord(agent_id="a1", name="x", status="active").status == "active"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/dooers-protocol && uv run pytest tests/test_teardown.py tests/test_agents_models.py::test_agent_record_status_optional_and_settable -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dooers.protocol.teardown'` and `AttributeError`/validation on `status`.

- [ ] **Step 3: Create the teardown module**

Create `packages/dooers-protocol/src/dooers/protocol/teardown.py`:

```python
"""Teardown request/response for dooers-push agent deletion."""

from pydantic import BaseModel


class TeardownRequest(BaseModel):
    # agent_id is sent in the path and env in the query string; this model
    # documents the contract (mirrors PushRequest alongside POST /v1/push/{id}).
    agent_id: str
    env: str = "prod"


class TeardownResponse(BaseModel):
    agent_id: str
    service_deleted: bool  # Cloud Run service existed and was deleted
    lb_rule_removed: bool  # load-balancer path rule existed and was removed
    service_name: str | None = None
    error: str | None = None


def format_teardown_result(resp: TeardownResponse) -> str:
    """One-line human summary of what teardown removed (used by `dooers agents delete`)."""
    if not resp.service_deleted and not resp.lb_rule_removed:
        return "No deployed service found — record only."
    parts = []
    if resp.service_deleted:
        parts.append("Cloud Run service deleted")
    if resp.lb_rule_removed:
        parts.append("load-balancer rule removed")
    return "; ".join(parts) + "."
```

- [ ] **Step 4: Add `status` to `AgentRecord`**

In `packages/dooers-protocol/src/dooers/protocol/agents.py`, add the `status` field to `AgentRecord` (after `host_url`):

```python
class AgentRecord(BaseModel):
    agent_id: str
    name: str
    owner_user_id: str | None = None
    organization_id: str | None = None
    host_url: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/dooers-protocol && uv run pytest tests/test_teardown.py tests/test_agents_models.py -v`
Expected: PASS (all teardown tests + the new status test + existing agents-model tests).

- [ ] **Step 6: Full package gate**

Run: `cd packages/dooers-protocol && uv run poe dev`
Expected: `ruff check` passes, `mypy -p dooers.protocol` → Success, all pytest pass.

- [ ] **Step 7: Commit**

```bash
git add packages/dooers-protocol/src/dooers/protocol/teardown.py \
        packages/dooers-protocol/src/dooers/protocol/agents.py \
        packages/dooers-protocol/tests/test_teardown.py \
        packages/dooers-protocol/tests/test_agents_models.py
git commit -m "feat(protocol): add Teardown models + AgentRecord.status"
```

---

### Task 2: `dooers-cli` — store `archive` + `delete` + `status`

**Files:**
- Modify: `packages/dooers-cli/src/dooers/cli/agent_store.py:22-29` (`_record`), add methods after `update`
- Test: `packages/dooers-cli/tests/test_agent_store.py`

**Interfaces:**
- Consumes: `AgentRecord.status` (Task 1).
- Produces:
  - `HTTPCoreAgentStore.archive(self, agent_id: str) -> None`
  - `HTTPCoreAgentStore.delete(self, agent_id: str) -> None`
  - `_record()` now populates `status`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/dooers-cli/tests/test_agent_store.py` (it already defines `BASE = "https://core.test"` and `A`, and imports `httpx`, `respx`, `HTTPCoreAgentStore`):

```python
import pytest

from dooers.cli.agent_store import AgentStoreError


@respx.mock
def test_delete_succeeds_without_data_key():
    # Core returns {success, message} with NO data key — must not call _record().
    respx.delete(f"{BASE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(200, json={"success": True, "message": "Agent deleted"})
    )
    HTTPCoreAgentStore(BASE, "tok").delete(A)  # should not raise


@respx.mock
def test_delete_surfaces_core_error_message():
    respx.delete(f"{BASE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(
            422, json={"success": False, "error": {"message": "cannot delete active agents"}}
        )
    )
    with pytest.raises(AgentStoreError) as exc:
        HTTPCoreAgentStore(BASE, "tok").delete(A)
    assert "cannot delete active agents" in str(exc.value)


@respx.mock
def test_archive_posts_to_archive_route():
    route = respx.post(f"{BASE}/api/v2/agents/{A}/archive").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"agentId": A, "name": "x"}})
    )
    HTTPCoreAgentStore(BASE, "tok").archive(A)  # should not raise
    assert route.called


@respx.mock
def test_archive_surfaces_core_error_message():
    respx.post(f"{BASE}/api/v2/agents/{A}/archive").mock(
        return_value=httpx.Response(422, json={"success": False, "error": {"message": "already archived"}})
    )
    with pytest.raises(AgentStoreError) as exc:
        HTTPCoreAgentStore(BASE, "tok").archive(A)
    assert "already archived" in str(exc.value)


@respx.mock
def test_get_populates_status():
    respx.get(f"{BASE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(
            200, json={"success": True, "data": {"agentId": A, "name": "x", "status": "active"}}
        )
    )
    rec = HTTPCoreAgentStore(BASE, "tok").get(A)
    assert rec.status == "active"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/dooers-cli && uv run pytest tests/test_agent_store.py -v`
Expected: FAIL — `AttributeError: 'HTTPCoreAgentStore' object has no attribute 'delete'` (and `archive`); `test_get_populates_status` fails because `_record` doesn't set `status`.

- [ ] **Step 3: Implement `_record` status + `archive` + `delete`**

In `packages/dooers-cli/src/dooers/cli/agent_store.py`, add `status` to `_record`:

```python
def _record(d: dict) -> AgentRecord:
    return AgentRecord(
        agent_id=d["agentId"],
        name=d.get("name", ""),
        owner_user_id=d.get("ownerUserId"),
        organization_id=d.get("organizationId"),
        host_url=d.get("hostUrl"),
        status=d.get("status"),
    )
```

Add these methods to `HTTPCoreAgentStore` (after `update`):

```python
    def archive(self, agent_id: str) -> None:
        r = httpx.post(
            f"{self.api}/agents/{agent_id}/archive", headers=self._h(), timeout=self._timeout
        )
        _data(r)  # raises AgentStoreError on {success: false}; no record to parse

    def delete(self, agent_id: str) -> None:
        r = httpx.delete(
            f"{self.api}/agents/{agent_id}", headers=self._h(), timeout=self._timeout
        )
        _data(r)  # success body is {success, message} with no data key — do NOT call _record()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/dooers-cli && uv run pytest tests/test_agent_store.py -v`
Expected: PASS (new + existing store tests).

- [ ] **Step 5: Full package gate**

Run: `cd packages/dooers-cli && uv run poe dev`
Expected: ruff passes, `mypy -p dooers.cli` → Success, all pytest pass.

- [ ] **Step 6: Commit**

```bash
git add packages/dooers-cli/src/dooers/cli/agent_store.py \
        packages/dooers-cli/tests/test_agent_store.py
git commit -m "feat(cli): add archive/delete to agent store + parse status"
```

---

### Task 3: `dooers-cli` — `PushClient.teardown`

**Files:**
- Modify: `packages/dooers-cli/src/dooers/cli/push_client.py` (imports + new method)
- Test: `packages/dooers-cli/tests/test_push_teardown.py`

**Interfaces:**
- Consumes: `TeardownResponse` (Task 1), existing `PushClientError`, `ErrorEnvelope`.
- Produces: `PushClient.teardown(self, agent_id: str, env: str = "prod") -> TeardownResponse`.

- [ ] **Step 1: Write the failing tests**

Create `packages/dooers-cli/tests/test_push_teardown.py`:

```python
"""PushClient.teardown — DELETE /v1/agents/{id} → TeardownResponse / PushClientError."""

import httpx
import pytest
import respx
from dooers.protocol.teardown import TeardownResponse

from dooers.cli.push_client import PushClient, PushClientError

BASE = "https://push.test"
A = "550e8400-e29b-41d4-a716-446655440000"


@respx.mock
def test_teardown_parses_response():
    respx.delete(f"{BASE}/v1/agents/{A}").mock(
        return_value=httpx.Response(
            200,
            json={
                "agent_id": A,
                "service_deleted": True,
                "lb_rule_removed": False,
                "service_name": "svc",
            },
        )
    )
    resp = PushClient(BASE, "tok").teardown(A, env="prod")
    assert isinstance(resp, TeardownResponse)
    assert resp.service_deleted is True
    assert resp.lb_rule_removed is False


@respx.mock
def test_teardown_raises_on_error_envelope():
    respx.delete(f"{BASE}/v1/agents/{A}").mock(
        return_value=httpx.Response(
            404,
            json={"error_code": "not_found", "message": "agent not found", "correlation_id": "c1"},
        )
    )
    with pytest.raises(PushClientError) as exc:
        PushClient(BASE, "tok").teardown(A)
    assert "agent not found" in str(exc.value)
    assert exc.value.envelope is not None


@respx.mock
def test_teardown_raises_on_non_json_error():
    respx.delete(f"{BASE}/v1/agents/{A}").mock(return_value=httpx.Response(502, text="bad gateway"))
    with pytest.raises(PushClientError) as exc:
        PushClient(BASE, "tok").teardown(A)
    assert "502" in str(exc.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/dooers-cli && uv run pytest tests/test_push_teardown.py -v`
Expected: FAIL — `AttributeError: 'PushClient' object has no attribute 'teardown'`.

- [ ] **Step 3: Implement `teardown`**

In `packages/dooers-cli/src/dooers/cli/push_client.py`, add the import (next to the existing `PushResponse` import):

```python
from dooers.protocol.teardown import TeardownResponse
```

Add the method to `PushClient` (after `push`):

```python
    def teardown(self, agent_id: str, env: str = "prod") -> TeardownResponse:
        url = f"{self.base_url}/v1/agents/{agent_id}"
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            r = httpx.delete(url, headers=headers, params={"env": env}, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise PushClientError(f"teardown request failed: {e}") from e

        if r.status_code >= 400:
            try:
                envelope = ErrorEnvelope.model_validate(r.json())
                raise PushClientError(envelope.message, envelope=envelope)
            except (ValueError, TypeError):
                raise PushClientError(f"teardown failed (HTTP {r.status_code}): {r.text}")

        return TeardownResponse.model_validate(r.json())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/dooers-cli && uv run pytest tests/test_push_teardown.py -v`
Expected: PASS.

- [ ] **Step 5: Full package gate**

Run: `cd packages/dooers-cli && uv run poe dev`
Expected: ruff passes, `mypy -p dooers.cli` → Success, all pytest pass.

- [ ] **Step 6: Commit**

```bash
git add packages/dooers-cli/src/dooers/cli/push_client.py \
        packages/dooers-cli/tests/test_push_teardown.py
git commit -m "feat(cli): add PushClient.teardown for agent infra removal"
```

---

### Task 4: `dooers-cli` — `delete` command + help string

**Files:**
- Modify: `packages/dooers-cli/src/dooers/cli/agents.py` (`_store` refactor + caller updates + new `delete`)
- Modify: `packages/dooers-cli/src/dooers/cli/cli.py:30` (agents help string)
- Test: `packages/dooers-cli/tests/test_agents_delete.py`

**Interfaces:**
- Consumes: `HTTPCoreAgentStore.get/archive/delete` (Task 2), `PushClient.teardown` (Task 3), `format_teardown_result` (Task 1), `AgentRecord.status`.
- Produces: `dooers agents delete <agent-id> [--archive] [--yes/-y]`.
- Internal change: `_store(ctx)` now returns `(HTTPCoreAgentStore, Settings, str)` — the third element is the validated bearer token.

- [ ] **Step 1: Write the failing tests**

Create `packages/dooers-cli/tests/test_agents_delete.py`:

```python
"""`dooers agents delete` — orchestration over core + dooers-push."""

import httpx
import respx
from typer.testing import CliRunner

from dooers.cli.cli import app

runner = CliRunner()

CORE = "https://core.test"
PUSH = "https://push.test"
A = "550e8400-e29b-41d4-a716-446655440000"
ROOT = ["--core-url", CORE, "--push-url", PUSH, "--env", "dev"]


def _auth(monkeypatch):
    class _Tok:
        def load(self):
            return "tok"

    monkeypatch.setattr("dooers.cli.agents.TokenStore", _Tok)
    monkeypatch.setattr("dooers.cli.agents.is_token_expired", lambda token, store: False)


def _agent_get(status=None, name="my-agent"):
    data = {"agentId": A, "name": name, "organizationId": "o1"}
    if status is not None:
        data["status"] = status
    return httpx.Response(200, json={"success": True, "data": data})


def _teardown_ok():
    return httpx.Response(
        200,
        json={"agent_id": A, "service_deleted": True, "lb_rule_removed": True, "service_name": "svc"},
    )


def _core_delete_ok():
    return httpx.Response(200, json={"success": True, "message": "Agent deleted"})


def test_agents_help_lists_delete():
    result = runner.invoke(app, ["agents", "--help"])
    assert result.exit_code == 0
    assert "delete" in result.stdout


@respx.mock
def test_delete_inactive_with_yes(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(return_value=_agent_get(status="archived"))
    td = respx.delete(f"{PUSH}/v1/agents/{A}").mock(return_value=_teardown_ok())
    dele = respx.delete(f"{CORE}/api/v2/agents/{A}").mock(return_value=_core_delete_ok())

    result = runner.invoke(app, ROOT + ["agents", "delete", A, "--yes"])

    assert result.exit_code == 0, result.output
    assert td.called and dele.called
    assert "Deleted agent" in result.output
    assert "Cloud Run service deleted; load-balancer rule removed." in result.output


@respx.mock
def test_active_without_archive_fast_fails(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(return_value=_agent_get(status="active"))
    td = respx.delete(f"{PUSH}/v1/agents/{A}").mock(return_value=_teardown_ok())
    dele = respx.delete(f"{CORE}/api/v2/agents/{A}").mock(return_value=_core_delete_ok())

    result = runner.invoke(app, ROOT + ["agents", "delete", A, "--yes"])

    assert result.exit_code == 1
    assert "is active" in result.output
    assert not td.called and not dele.called


@respx.mock
def test_active_with_archive_then_delete(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(return_value=_agent_get(status="active"))
    arch = respx.post(f"{CORE}/api/v2/agents/{A}/archive").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"agentId": A, "name": "my-agent"}})
    )
    td = respx.delete(f"{PUSH}/v1/agents/{A}").mock(return_value=_teardown_ok())
    dele = respx.delete(f"{CORE}/api/v2/agents/{A}").mock(return_value=_core_delete_ok())

    result = runner.invoke(app, ROOT + ["agents", "delete", A, "--archive", "--yes"])

    assert result.exit_code == 0, result.output
    assert arch.called and td.called and dele.called


@respx.mock
def test_teardown_failure_aborts_before_record_delete(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(return_value=_agent_get(status="archived"))
    respx.delete(f"{PUSH}/v1/agents/{A}").mock(
        return_value=httpx.Response(
            500, json={"error_code": "internal", "message": "teardown boom", "correlation_id": "c1"}
        )
    )
    dele = respx.delete(f"{CORE}/api/v2/agents/{A}").mock(return_value=_core_delete_ok())

    result = runner.invoke(app, ROOT + ["agents", "delete", A, "--yes"])

    assert result.exit_code == 1
    assert "Teardown failed" in result.output
    assert "teardown boom" in result.output
    assert not dele.called  # record must NOT be deleted when teardown failed


@respx.mock
def test_record_delete_422_after_teardown(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(return_value=_agent_get(status="archived"))
    respx.delete(f"{PUSH}/v1/agents/{A}").mock(return_value=_teardown_ok())
    respx.delete(f"{CORE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(422, json={"success": False, "error": {"message": "agent has products"}})
    )

    result = runner.invoke(app, ROOT + ["agents", "delete", A, "--yes"])

    assert result.exit_code == 1
    assert "Service torn down, but the core record was not deleted" in result.output
    assert "agent has products" in result.output


@respx.mock
def test_confirm_decline_makes_no_mutating_calls(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(return_value=_agent_get(status="archived"))
    td = respx.delete(f"{PUSH}/v1/agents/{A}").mock(return_value=_teardown_ok())
    dele = respx.delete(f"{CORE}/api/v2/agents/{A}").mock(return_value=_core_delete_ok())

    result = runner.invoke(app, ROOT + ["agents", "delete", A], input="n\n")

    assert result.exit_code == 1  # typer.confirm(abort=True) → Abort → exit 1
    assert not td.called and not dele.called


@respx.mock
def test_delete_not_found(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(404, json={"success": False, "error": {"message": "nope"}})
    )

    result = runner.invoke(app, ROOT + ["agents", "delete", A, "--yes"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/dooers-cli && uv run pytest tests/test_agents_delete.py -v`
Expected: FAIL — `delete` is not a registered command (`No such command 'delete'`, exit code 2) and the help test fails.

- [ ] **Step 3: Refactor `_store` and update its callers**

In `packages/dooers-cli/src/dooers/cli/agents.py`, change `_store` to return the token, and update the three existing callers' unpacking.

`_store`:

```python
def _store(ctx: typer.Context) -> tuple[HTTPCoreAgentStore, Settings, str]:
    settings: Settings = ctx.obj
    store_token = TokenStore()
    token = store_token.load()
    if not token or is_token_expired(token, store=store_token):
        typer.echo("Not authenticated. Run `dooers login`.", err=True)
        raise typer.Exit(code=1)
    return HTTPCoreAgentStore(settings.core_url, token), settings, token
```

In `create`: change `store, settings = _store(ctx)` → `store, settings, _ = _store(ctx)`.
In `list_agents`: change `store, settings = _store(ctx)` → `store, settings, _ = _store(ctx)`.
In `show`: change `store, _ = _store(ctx)` → `store, _, _ = _store(ctx)`.

- [ ] **Step 4: Add the `delete` command + imports**

In `packages/dooers-cli/src/dooers/cli/agents.py`, add imports near the top (with the other `dooers.cli`/`dooers.protocol` imports):

```python
from dooers.protocol.teardown import format_teardown_result

from dooers.cli.push_client import PushClient, PushClientError
```

Add the command (next to `show`):

```python
@app.command()
def delete(
    ctx: typer.Context,
    agent_id: str = typer.Argument(..., help="Agent id to delete."),
    archive: bool = typer.Option(
        False, "--archive", help="Archive an active agent first, then delete."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    store, settings, token = _store(ctx)

    # 1. Fetch the record (existence + status + name).
    try:
        rec = store.get(agent_id)
    except KeyError:
        typer.echo(f"Agent {agent_id} not found.", err=True)
        raise typer.Exit(code=1)
    except AgentStoreError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    # 2. Active-state pre-check — fail fast before touching any infra.
    if rec.status == "active" and not archive:
        typer.echo(
            f"Agent {agent_id} is active; pass --archive to archive-then-delete, "
            "or archive it first.",
            err=True,
        )
        raise typer.Exit(code=1)

    # 3. Confirm (abort=True raises typer.Abort → exit 1, no further calls).
    if not yes:
        typer.confirm(
            f"Delete agent {rec.name or agent_id} ({agent_id})? "
            "This deletes the record and tears down its deployed service. "
            "This cannot be undone.",
            abort=True,
        )

    # 4. Archive an active agent if requested (clears core's active-state delete guard).
    if rec.status == "active" and archive:
        try:
            store.archive(agent_id)
        except AgentStoreError as e:
            typer.echo(f"Archive failed: {e}", err=True)
            raise typer.Exit(code=1) from e

    # 5. Tear down infra (Cloud Run + LB rule) via dooers-push — BEFORE the record delete.
    push = PushClient(base_url=settings.push_url, token=token)
    try:
        teardown = push.teardown(agent_id, env=settings.env)
    except PushClientError as e:
        typer.echo(f"Teardown failed: {e}", err=True)
        raise typer.Exit(code=1) from e

    # 6. Delete the core record.
    try:
        store.delete(agent_id)
    except AgentStoreError as e:
        typer.echo(f"Service torn down, but the core record was not deleted: {e}", err=True)
        raise typer.Exit(code=1) from e

    # 7. Summary.
    typer.echo(f"Deleted agent {agent_id} ({rec.name}).")
    typer.echo(format_teardown_result(teardown))
```

- [ ] **Step 5: Update the agents help string**

In `packages/dooers-cli/src/dooers/cli/cli.py`, line 30:

```python
app.add_typer(
    agents.app, name="agents", help="Manage agents — subcommands: list | create | show | delete."
)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd packages/dooers-cli && uv run pytest tests/test_agents_delete.py -v`
Expected: PASS (all delete-command tests + the help test).

- [ ] **Step 7: Full package gate (catches the `_store` refactor regressions too)**

Run: `cd packages/dooers-cli && uv run poe dev`
Expected: ruff passes, `mypy -p dooers.cli` → Success (verifies all three `_store` callers were updated), all 60+ pytest pass.

- [ ] **Step 8: Commit**

```bash
git add packages/dooers-cli/src/dooers/cli/agents.py \
        packages/dooers-cli/src/dooers/cli/cli.py \
        packages/dooers-cli/tests/test_agents_delete.py
git commit -m "feat(cli): add 'dooers agents delete' with infra teardown"
```

---

### Task 5: Release prep — version bumps + final verification

**Files:**
- Modify: `packages/dooers-protocol/pyproject.toml:3` (`version`)
- Modify: `packages/dooers-cli/pyproject.toml:3` (`version`) and the `dooers-protocol` dependency floor

**Interfaces:** none (config + verification only).

- [ ] **Step 1: Bump `dooers-protocol` version**

In `packages/dooers-protocol/pyproject.toml`, change `version = "0.4.0"` → `version = "0.5.0"`.

- [ ] **Step 2: Bump `dooers-cli` version and protocol dependency floor**

In `packages/dooers-cli/pyproject.toml`:
- change `version = "0.5.0"` → `version = "0.6.0"`;
- in `[project].dependencies`, change `"dooers-protocol>=0.4.0"` → `"dooers-protocol>=0.5.0"`.

(The `[tool.uv.sources]` editable path is unchanged — local resolution still uses the sibling.)

- [ ] **Step 3: Re-sync and verify both packages**

Run:
```bash
cd packages/dooers-protocol && uv sync --extra dev && uv run poe dev
cd ../dooers-cli && uv sync --extra dev && uv run poe dev
```
Expected: both `uv sync` resolve cleanly (no `dooers-protocol` version conflict) and both `poe dev` are fully green.

- [ ] **Step 4: Commit**

```bash
git add packages/dooers-protocol/pyproject.toml packages/dooers-cli/pyproject.toml
git commit -m "release: protocol 0.5.0 + cli 0.6.0 (agents delete + teardown)"
```

- [ ] **Step 5: Record the manual release/cross-repo steps (do NOT execute here)**

These happen outside this plan, in order (see spec §8 and memory `pypi-release-process.md`):
1. Publish `dooers-protocol` 0.5.0 to PyPI (tag `protocol-v0.5.0`, owner `Dooers-ai`, trusted publishing).
2. In `Dooers-ai/dooers-push`: bump `dooers-protocol` dep to `>=0.5.0`, implement `DELETE /v1/agents/{id}`, redeploy.
3. Publish `dooers-cli` 0.6.0 to PyPI (tag `cli-v0.6.0`) — after the push endpoint is live.

---

## Self-Review

**Spec coverage:**
- §3 flow (GET → pre-check → confirm → archive → teardown → record delete → summary) → Task 4 command. ✓
- §4 protocol models + `AgentRecord.status` → Task 1. ✓
- §5.1 store `archive`/`delete` + `_record` status → Task 2. ✓
- §5.2 `PushClient.teardown` → Task 3. ✓
- §5.3 delete command, §5.4 help string → Task 4. ✓
- §6 dooers-push endpoint → Cross-Repo Dependencies (out of scope, documented). ✓
- §7 assumptions: archive route `POST /api/v2/agents/{id}/archive` (Task 2 implementation + tests use this path — verify against core before merge); `GET` returns `status` (Task 2 `test_get_populates_status` + Task 4 active-path tests depend on it). ✓ *If core's archive verb/path or the `status` field differs, adjust Task 2 + the affected Task 4 mocks.*
- §8 release ordering → Task 5. ✓
- §9 testing → tests embedded in Tasks 1–4. ✓

**Placeholder scan:** No TBD/TODO; all steps contain runnable code + exact commands. ✓

**Type consistency:** `teardown(agent_id, env="prod") -> TeardownResponse` (Task 3) is called as `push.teardown(agent_id, env=settings.env)` and consumed via `format_teardown_result(teardown)` (Task 4). `_store(ctx) -> (store, settings, token)` (Task 4 Step 3) matches its use `store, settings, token = _store(ctx)`. `archive`/`delete` return `None` and are called for effect only. `AgentRecord.status` (Task 1) is read as `rec.status == "active"` (Task 4) and set by `_record` (Task 2). ✓
