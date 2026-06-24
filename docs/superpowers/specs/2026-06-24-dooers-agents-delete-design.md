# `dooers agents delete` — record deletion + infra teardown

**Status:** Draft for review
**Date:** 2026-06-24
**Author:** platform
**Scope:** `dooers-protocol`, `dooers-cli`, `dooers-push` (separate repo `Dooers-ai/dooers-push`)

---

## 1. Overview

The CLI has `dooers agents list | create | show` but no `delete`. While E2E-testing
the deploy flow we created throwaway agents; every successful push leaves **two** live
artifacts with no CLI cleanup path:

1. a **core record** (`/api/v2/agents/:id`), and
2. a deployed **Cloud Run service** (project `dooers-agents`, region
   `southamerica-east1`) plus a **load-balancer path rule** (`/<uuid>` on
   `agents.dooers.ai`).

This adds `dooers agents delete <agent-id>` that tears down **both**: it asks
`dooers-push` to remove the Cloud Run service + LB rule, then deletes the core record.

### Goal

After this work, a creator can run `dooers agents delete <agent-id>` and have the agent
fully removed — record gone from core, Cloud Run service deleted, LB path rule removed —
with a confirmation prompt by default and graceful, traceback-free error messages.

### Decisions (locked)

- **Full teardown, not record-only.** `delete` tears down the Cloud Run service + LB rule
  via `dooers-push`, then deletes the core record. (gcloud auth is not available to the
  CLI, so all infra teardown goes through `dooers-push`.)
- **`--archive` flag for active agents.** Plain delete by default; core 422s on `active`
  agents. With `--archive`, the CLI archives the agent first (clears the active-state
  guard) and then deletes. Without it, an active agent fails fast with a clear message.
- **Confirmation prompt by default**, skippable with `--yes`/`-y` for scripting/batch
  cleanup.
- **Teardown is idempotent and happens before the record delete** (see §3).

### Non-goals

- **No standalone `dooers agents archive` command.** Archiving is only a sub-step reached
  via `delete --archive`. A standalone command can be added later if needed.
- **No bulk / multi-id delete** (`delete <id1> <id2> …`). One agent per invocation in v1.
- **No clearing of products / active workers.** Those are independent core guards; the
  command surfaces the core message and stops (the creator resolves them out-of-band).
- **No change to the `dooers_protocol` legacy shim** beyond what the new models require.

---

## 2. Contract reference

### 2.1 Core (existing — `dooers-service-core`, base `https://api.dooers.ai`)

Auth via `Authorization: Bearer <token>`. Envelope: success `{ "success": true, "data": … }`;
error `{ "success": false, "error": { code, message, type, timestamp } }`.

| Call | Purpose |
|---|---|
| `GET /api/v2/agents/:id` (auth) | fetch record — `name`, `status`, `hostUrl`, ownership |
| `POST /api/v2/agents/:id/archive` (auth) | **archive** — set non-active status *(path assumed; verify, see §7)* |
| `DELETE /api/v2/agents/:id` (auth) | delete record → `{ success: true, message: "Agent deleted" }` (no `data` key) |

Core `DELETE` **422s** (`INVALID_STATE`, `error.message`) when the agent:
- `status === 'active'` → "cannot delete active agents"
- has existing products
- has active workers

### 2.2 dooers-push (new endpoint, separate repo)

Existing push endpoint is `POST /v1/push/{agent_id}?tag&env` (multipart, Bearer auth,
`ErrorEnvelope` on ≥400, Pydantic response). The teardown endpoint mirrors it:

| Call | Purpose |
|---|---|
| `DELETE /v1/agents/{agent_id}?env=prod` (auth) | tear down Cloud Run service + LB path rule, idempotently → `TeardownResponse` |

`dooers-push` verifies ownership by reading the agent's core record (the existing pattern),
so the **core record must still exist** when teardown runs. Teardown is **idempotent**: it
looks up the Cloud Run service by the agent-id naming convention and no-ops if it (or the
LB rule) is already absent, reporting exactly what it removed.

---

## 3. Operation flow

`dooers agents delete <agent-id> [--archive] [--yes/-y]`

1. **`GET /api/v2/agents/{id}`** (core) — confirm existence; read `name`, `status`,
   `hostUrl`. `404` → "Agent `<id>` not found." and exit 1.
2. **Active-state pre-check (client-side):** if `status == 'active'` and `--archive` was
   *not* passed → exit 1 with: "Agent `<id>` is active; pass `--archive` to
   archive-then-delete, or archive it first." **No infra is touched.** This avoids the
   half-deleted state of tearing down infra and *then* failing core's delete guard.
3. **Confirmation prompt** (unless `--yes`/`-y`): shows `name` + `id` + an irreversibility
   warning ("This deletes the agent record and tears down its deployed service. This cannot
   be undone."). Declining aborts via `typer.Abort` (exit 1) without changes.
4. **Archive (only if `status == 'active'` and `--archive`):** `POST /api/v2/agents/{id}/archive`
   (core). On failure → abort, surface message, exit 1.
5. **Teardown:** `DELETE /v1/agents/{id}?env=<env>` (push). On failure → **abort before the
   record delete**; the record is left intact so the user can retry. Surface the push error,
   exit 1.
6. **Record delete:** `DELETE /api/v2/agents/{id}` (core). On 422 (products/active workers)
   → report "Service torn down, but the core record was not deleted: `<message>`" and exit
   1 (state is reported, never silently inconsistent).
7. **Summary** (success): print record deleted + teardown result, e.g.
   - `Deleted agent <id> (<name>).`
   - `Cloud Run service deleted; load-balancer rule removed.` *(or)* `No deployed service found — record only.`

### Why this order

`dooers-push` authorizes teardown by reading the core record, so teardown must precede the
record delete (step 5 before step 6). Because teardown is idempotent, re-running `delete`
after a step-6 failure is safe: teardown no-ops and the record delete is retried.

### Why not rely on `hostUrl` to decide whether to tear down

`hostUrl` can currently read `None` even for deployed agents (the live core has not yet
redeployed the merged `hostUrl` field). So the CLI does **not** branch on `hostUrl`; it
always calls teardown and lets `dooers-push` decide whether a service exists. The
`TeardownResponse` reports what was actually removed.

---

## 4. `dooers-protocol` changes (this repo)

New module `src/dooers/protocol/teardown.py`:

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
    service_deleted: bool          # Cloud Run service existed and was deleted
    lb_rule_removed: bool          # load-balancer path rule existed and was removed
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

Edit `src/dooers/protocol/agents.py` — add `status` to `AgentRecord` (additive, optional):

```python
status: str | None = None
```

(`format_teardown_result` lives in protocol for the same reason `format_push_failure` does:
it keeps display logic unit-testable without a live service.)

---

## 5. `dooers-cli` changes (this repo)

### 5.1 `agent_store.py` (`HTTPCoreAgentStore`)

- Extend `_record()` to read `status`: `status=d.get("status")`.
- Add `archive`:

  ```python
  def archive(self, agent_id: str) -> None:
      r = httpx.post(f"{self.api}/agents/{agent_id}/archive", headers=self._h(), timeout=self._timeout)
      _data(r)  # raises AgentStoreError on {success: false}; no record to parse
  ```

- Add `delete`:

  ```python
  def delete(self, agent_id: str) -> None:
      r = httpx.delete(f"{self.api}/agents/{agent_id}", headers=self._h(), timeout=self._timeout)
      _data(r)  # success body is {success, message} with no data key — do NOT call _record()
  ```

`_data()` already extracts `error.message` on `{success: false}` and raises
`AgentStoreError`, so guard messages (active / products / workers) surface verbatim.

### 5.2 `push_client.py` (`PushClient`)

Add `teardown`, mirroring `push()`'s error handling:

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

### 5.3 `agents.py` — new `delete` command

```python
@app.command()
def delete(
    ctx: typer.Context,
    agent_id: str = typer.Argument(...),
    archive: bool = typer.Option(False, "--archive", help="Archive an active agent first, then delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    ...
```

Orchestrates the §3 flow:
- Build the core store via `_store(ctx)` and a `PushClient` from `settings.push_url` + token
  (mirror `push.py`'s client construction).
- `store.get(id)` → `KeyError` → "not found"; `AgentStoreError` → surfaced.
- Active-state pre-check (step 2).
- `typer.confirm(...)` unless `--yes` (step 3).
- `store.archive(id)` if needed (step 4).
- `push.teardown(id, env=settings.env)` (step 5), `PushClientError` surfaced; abort on failure.
- `store.delete(id)` (step 6); on `AgentStoreError` report "Service torn down, but record not
  deleted: …".
- Print summary using `format_teardown_result` (step 7).

All error paths use `typer.echo(..., err=True)` + `raise typer.Exit(code=1)` (no tracebacks),
matching `list`/`show`.

### 5.4 `cli.py`

Update the agents group help string:

```python
app.add_typer(agents.app, name="agents",
    help="Manage agents — subcommands: list | create | show | delete.")
```

---

## 6. `dooers-push` changes (separate repo `Dooers-ai/dooers-push`)

Implemented in its own repo; this spec is the source of truth for the wire contract.

- New route `DELETE /v1/agents/{agent_id}?env=`:
  - Bearer auth → verify ownership against core (existing pattern).
  - Delete the Cloud Run service (`dooers-agents`, `southamerica-east1`) by the agent-id
    naming convention used at deploy time; **idempotent** (no-op + `service_deleted=False`
    if absent).
  - Remove the load-balancer path rule for `/<agent_id>`; idempotent
    (`lb_rule_removed=False` if absent).
  - Return `TeardownResponse`.
- Consumes the new `dooers-protocol` (≥ 0.5.0) from PyPI; redeploy after protocol is
  published (see §8).

---

## 7. Assumptions to verify during implementation

1. **Archive route path.** Assumed `POST /api/v2/agents/{id}/archive` (the `archiveAgent`
   route sits just above delete in `dooers-service-core` controller). Confirm the verb +
   path against the core controller before wiring `store.archive`.
2. **`GET /api/v2/agents/:id` returns `status`.** Required for the client-side active
   pre-check (step 2). If `status` is absent from the GET payload, fall back to 422-driven
   handling: attempt `delete`; on an active-state 422, if `--archive` was passed, archive
   and retry once, else surface the message. The spec's preferred path is the client-side
   pre-check.

---

## 8. Release ordering (cross-repo chain)

CLI `delete` is not fully functional until the push endpoint is live, so the chain is:

1. **`dooers-protocol`** 0.4.0 → **0.5.0** (new `teardown.py` models + `AgentRecord.status`).
   Publish to PyPI via trusted publishing, tag `protocol-v0.5.0`, owner `Dooers-ai`.
2. **`dooers-push`** (separate repo): bump `dooers-protocol` dep to `>=0.5.0`, implement the
   `DELETE /v1/agents/{id}` endpoint, redeploy.
3. **`dooers-cli`** 0.5.0 → **0.6.0**: bump `dooers-protocol` dep to `>=0.5.0`, add the
   command. Publish, tag `cli-v0.6.0`, owner `Dooers-ai`.

(See memory `pypi-release-process.md` for the publish mechanics.)

---

## 9. Testing

### `dooers-protocol`
- `TeardownRequest` / `TeardownResponse` round-trip (defaults, optional fields).
- `format_teardown_result` for the three cases: both removed, service-only, neither
  ("record only"). Mirror `tests/test_push_display.py`.

### `dooers-cli`
- `test_agent_store.py`:
  - `delete` on `{success: true, message: …}` (no `data`) succeeds without calling `_record()`.
  - `delete` on a 422 `{success: false, error: {message}}` raises `AgentStoreError` with the
    message.
  - `archive` success + error.
  - `_record()` populates `status`.
- New command tests (`typer.testing.CliRunner` + `respx` mocking **both** core and push):
  - confirm prompt shown; declining makes no calls.
  - `--yes` skips the prompt.
  - active agent **without** `--archive` → fast-fail, no archive/teardown/delete calls.
  - active agent **with** `--archive` → archive → teardown → delete in order.
  - teardown failure → aborts **before** the record delete (no core DELETE issued).
  - record-delete 422 after successful teardown → "Service torn down, but record not
    deleted: …" message.
  - success summary uses `format_teardown_result`.

Both packages must pass `uv run poe dev` (ruff + mypy + pytest).

---

## 10. Proof / cleanup targets

The new command is proven by cleaning up the existing test agents:

- `00ac312f-3950-4066-ba25-5bbbae03a1ac` — org **Dufrio** (no hosting), record-only (push
  was blocked). Not active → easiest first delete; teardown reports "no service found".
- `52607f58-b084-4efd-854e-a225b7d98a3e` — org **rayan-entra**, live (earlier test) →
  exercises `--archive` + full teardown.
- `50f823c0-06b7-4447-948d-551b43ddba63` — org **rayan-entra**, live at
  `https://agents.dooers.ai/50f823c0-06b7-4447-948d-551b43ddba63` (`/chat` works) → full
  teardown end-to-end.

Orgs: rayan-entra `e7d3594c-f61f-49c7-a4a0-ca288dd0619e` (has hosting); Dufrio (no hosting).
