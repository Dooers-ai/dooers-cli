# Core v2 Migration + Agent `hostUrl` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `dooers-cli` and `dooers-push` authenticate and persist agents against the live **v2 core** (`/api/v2`, better-auth OTP, UUID org-scoped agents, `{success,data}`), and persist each deployed agent's public URL in a new `hostUrl` field.

**Architecture:** Four packages. `dooers-service-core` (TS/Hono/Drizzle) gains a nullable `hostUrl` column on `agent`. `dooers-protocol` (shared Pydantic) is re-shaped to v2. `dooers-push` (FastAPI) verifies tokens via `/identity/me` and writes `hostUrl` via `PATCH /agents/:id`. `dooers-cli` (Typer) does OTP login → Bearer token, org resolution, and v2 agents CRUD. The v1 file-shim and `DOOERS_USE_CORE_AGENTS` gating are removed.

**Tech Stack:** Python 3.10+/3.12 (uv, pytest, httpx, respx for tests), TypeScript (Hono, Drizzle, vitest), better-auth.

**Verification core (dev):** `https://api-v2.dev.dooers.ai/api/v2`. Use it for the two `[VERIFY]` steps (OTP `set-auth-token` header, `GET /organizations` field names) before coding the dependent parts.

---

## File Structure

**dooers-protocol** (`packages/dooers-protocol/src/dooers_protocol/`)
- `agents.py` — reshape `AgentRecord`, `CreateAgentRequest`, `AgentManifest`; drop `Runtime`, `env_required`.

**dooers-service-core** (`src/`)
- `database/models.ts` — add `hostUrl` column to `agent`.
- `database/migrations/0007_agent_host_url.sql` — new migration.
- `modules/agent/models.ts` — `hostUrl` in `agentSchema` + `updateAgentRequestSchema`.
- `modules/agent/repository.ts` — `hostUrl` in select/DTO/mapper/update type.
- `modules/agent/services.ts` — `hostUrl` passthrough in `updateAgentById`.

**dooers-push** (`packages/dooers-push/src/dooers_push/`)
- `gcp/cloudbuild.py` — UUID-safe `cloud_run_service_name()`.
- `gcp/loadbalancer.py` — use shared service-name helper.
- `auth.py` — verify via `/api/v2/identity/me`.
- `core_client.py` — `get_agent` + `patch_host_url` against v2.
- `pipeline/deployer.py` / `main.py` — call `patch_host_url`.

**dooers-cli** (`packages/dooers-cli/src/dooers/`)
- `token_store.py` — JSON `{token, expires_at}` storage.
- `core_client.py` — v2 auth (`auth_method`, `send_otp`, `verify_otp`, `me`, `revoke`, `list_organizations`).
- `user_config.py` (new) — `~/.dooers/config.json` (default org).
- `org.py` (new) — org resolution + `dooers org list|use`.
- `agent_store.py` — v2-only `HTTPCoreAgentStore` (delete shim).
- `agents.py`, `auth.py`, `cli.py`, `config.py`, `settings.py` — wire to the above.

Each package's tests live under its `tests/`.

---

## Phase A — `dooers-protocol` (shared models first; push/cli import these)

### Task A1: Reshape agent models to v2

**Files:**
- Modify: `packages/dooers-protocol/src/dooers_protocol/agents.py`
- Test: `packages/dooers-protocol/tests/test_agents_models.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/dooers-protocol/tests/test_agents_models.py
import pytest
from pydantic import ValidationError
from dooers_protocol.agents import AgentRecord, CreateAgentRequest, AgentManifest


def test_agent_record_v2_shape():
    r = AgentRecord(
        agent_id="550e8400-e29b-41d4-a716-446655440000",
        name="x",
        owner_user_id="user_1",
        organization_id="org_1",
        host_url=None,
    )
    assert r.host_url is None
    assert r.organization_id == "org_1"


def test_create_agent_request_requires_org():
    req = CreateAgentRequest(organization_id="org_1", name="x")
    assert req.organization_id == "org_1"


def test_manifest_carries_org_and_uuid():
    m = AgentManifest(
        protocol_version="2",
        agent_id="550e8400-e29b-41d4-a716-446655440000",
        name="x",
        organization_id="org_1",
    )
    assert m.organization_id == "org_1"


def test_manifest_rejects_unknown_field():
    with pytest.raises(ValidationError):
        AgentManifest(
            protocol_version="2",
            agent_id="u",
            name="x",
            organization_id="o",
            runtime="docker",  # removed field → forbidden
        )
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd packages/dooers-protocol && uv run pytest tests/test_agents_models.py -x`
Expected: FAIL (`organization_id` unexpected / not accepted).

- [ ] **Step 3: Rewrite `agents.py`**

```python
"""Agent records, create requests, and the dooers.yaml manifest schema (core v2)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentRecord(BaseModel):
    """An agent as returned by core v2 (the `data` of /api/v2/agents/:id)."""

    agent_id: str
    name: str
    owner_user_id: str | None = None
    organization_id: str | None = None
    host_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CreateAgentRequest(BaseModel):
    """Body of `POST /api/v2/agents` (sent by `dooers agents create`)."""

    organization_id: str
    name: str


class AgentManifest(BaseModel):
    """Schema of `dooers.yaml` written by `dooers agents create`."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: str
    agent_id: str
    name: str
    organization_id: str
```

- [ ] **Step 4: Bump protocol version**

In `packages/dooers-protocol/src/dooers_protocol/__init__.py` set `PROTOCOL_VERSION = "2"`.

- [ ] **Step 5: Run tests, verify pass**

Run: `cd packages/dooers-protocol && uv run pytest tests/test_agents_models.py -v && uv run poe check && uv run poe typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/dooers-protocol
git commit -m "feat(protocol): reshape agent models to core v2 (uuid, org, host_url)"
```

---

## Phase B — `dooers-service-core`: add `hostUrl`

> Core uses vitest + a test DB. The DB-backed assertion is a manual dev curl (Task B2) because spinning a test Postgres is out of scope here; the code changes are type-checked by `tsc`/biome.

### Task B1: Add the column, schema, and DTO plumbing

**Files:**
- Modify: `src/database/models.ts` (agent table, ~line 398)
- Create: `database/migrations/0007_agent_host_url.sql`
- Modify: `src/modules/agent/models.ts`
- Modify: `src/modules/agent/repository.ts`
- Modify: `src/modules/agent/services.ts`

- [ ] **Step 1: DB column** — in `src/database/models.ts`, inside the `agent` `pgTable`, add after `serverConfig`:

```ts
    /** Public URL of the deployed agent (set by dooers-push after a push). */
    hostUrl: text('host_url'),
```

- [ ] **Step 2: Migration** — create `database/migrations/0007_agent_host_url.sql`:

```sql
ALTER TABLE "agent" ADD COLUMN "host_url" text;--> statement-breakpoint
```

Then register it in the drizzle journal: run `npx drizzle-kit generate` (preferred — it writes the SQL + journal entry from the model change; if it produces an equivalent file, delete this hand-written one). Confirm `database/migrations/meta/_journal.json` gains a `0007_agent_host_url` entry.

- [ ] **Step 3: Zod models** — in `src/modules/agent/models.ts`:
  - add to `agentSchema` (after `serverConfig`): `hostUrl: z.string().url().nullable().optional(),`
  - add to `updateAgentRequestSchema` (after `serverConfig`): `hostUrl: z.string().url().nullable().optional(),`

- [ ] **Step 4: Repository** — in `src/modules/agent/repository.ts`:
  - `agentSelectWithUsers()`: add `hostUrl: agent.hostUrl,`
  - `AgentSelectRow` type: add `hostUrl: string | null`
  - `mapRowToAgentDTO()`: add `hostUrl: row.hostUrl,`
  - `AgentDTO` interface: add `hostUrl: string | null`
  - `updateAgent()` `data` param type: add `hostUrl?: string | null` (it's spread via `...rest` into `.set()`, so no other change needed)

- [ ] **Step 5: Service passthrough** — in `src/modules/agent/services.ts` `updateAgentById`:
  - add `hostUrl?: string | null` to the `data` param type (next to `serverConfig?: ServerConfig`)
  - where the repository `updateAgent` call is built (the object containing `serverConfig: data.serverConfig`), add `hostUrl: data.hostUrl,`
  - include `data.hostUrl !== undefined ||` in the "has any updatable field" guard near line 352.

- [ ] **Step 6: OpenAPI** — `grep -n "serverConfig" src/openapi/schemas.ts`. If an agent response schema lists fields explicitly, add `hostUrl: z.string().url().nullable().optional()` mirroring `agentSchema`. If openapi is derived from `agentSchema`, no change.

- [ ] **Step 7: Build + typecheck**

Run: `cd /home/frndvrgs/software/dooers/dooers-service-core && npm run build && npx biome check src`
Expected: no type errors.

- [ ] **Step 8: Commit**

```bash
git add src database/migrations
git commit -m "feat(agent): add nullable hostUrl, settable via PATCH /agents/:id"
```

### Task B2: [VERIFY] dev round-trip (after dev deploy)

- [ ] Once the dev core has the migration applied, with a valid Bearer token `$T` and an agent UUID `$A`:

```bash
curl -s -X PATCH https://api-v2.dev.dooers.ai/api/v2/agents/$A \
  -H "authorization: Bearer $T" -H 'content-type: application/json' \
  -d '{"hostUrl":"https://agents.dooers.ai/'$A'"}' | python3 -m json.tool
curl -s https://api-v2.dev.dooers.ai/api/v2/agents/$A -H "authorization: Bearer $T" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['hostUrl'])"
```

Expected: the PATCH returns `{success:true,...}` and the GET prints the URL. Record the exact `data` field names seen (confirms `agentId`, `ownerUserId`, `organizationId`, `hostUrl`) for Phase C/D.

---

## Phase C — `dooers-push` → v2

### Task C1: UUID-safe Cloud Run service name (pure functions, TDD)

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/gcp/cloudbuild.py`
- Modify: `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`
- Test: `packages/dooers-push/tests/test_lb_naming.py` (extend)

- [ ] **Step 1: Write failing tests**

```python
# add to packages/dooers-push/tests/test_lb_naming.py
from dooers_push.gcp.cloudbuild import cloud_run_service_name

UUID = "550e8400-e29b-41d4-a716-446655440000"

def test_service_name_starts_with_letter_for_uuid():
    name = cloud_run_service_name(UUID, "prod")
    assert name == f"agent-{UUID}-prod"
    assert name[0].isalpha()           # Cloud Run names must start with a letter
    assert len(name) <= 63

def test_neg_targets_same_service_name():
    from dooers_push.gcp import loadbalancer as lb
    # NEG cloud_run_service must equal the deployed service name
    assert lb._cloud_run_service(UUID, "dev") == cloud_run_service_name(UUID, "dev")
```

- [ ] **Step 2: Run, verify fail**

Run: `cd packages/dooers-push && uv run pytest tests/test_lb_naming.py -x`
Expected: FAIL (`cloud_run_service_name` undefined).

- [ ] **Step 3: Add the shared helper in `cloudbuild.py`**

Replace `_service_name` with a public, letter-prefixed helper and keep `_service_name` as an alias:

```python
def cloud_run_service_name(agent_id: str, env: str) -> str:
    """Cloud Run service name. Letter-prefixed so it's valid even when
    agent_id is a UUID that starts with a digit. Lowercased; '_' → '-'."""
    safe = agent_id.lower().replace("_", "-")
    return f"agent-{safe}-{env}"


def _service_name(agent_id: str, env: str) -> str:
    return cloud_run_service_name(agent_id, env)
```

- [ ] **Step 4: Use it in `loadbalancer.py`**

In `loadbalancer.py`, add an import and a small wrapper, and use it in `_ensure_neg`:

```python
from dooers_push.gcp.cloudbuild import cloud_run_service_name

def _cloud_run_service(agent_id: str, env: str) -> str:
    return cloud_run_service_name(agent_id, env)
```

In `_ensure_neg`, replace:
```python
cloud_run_service = f"{safe_agent_id(agent_id)}-{env}"
```
with:
```python
cloud_run_service = _cloud_run_service(agent_id, env)
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd packages/dooers-push && uv run pytest tests/test_lb_naming.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/dooers-push/src/dooers_push/gcp/cloudbuild.py packages/dooers-push/src/dooers_push/gcp/loadbalancer.py packages/dooers-push/tests/test_lb_naming.py
git commit -m "fix(push): letter-prefixed Cloud Run service name for UUID agent ids"
```

### Task C2: Verify tokens via `/identity/me`

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/auth.py`
- Test: `packages/dooers-push/tests/test_auth.py` (create)

- [ ] **Step 1: Add `respx` dev dep** — in `packages/dooers-push/pyproject.toml` `[project.optional-dependencies].dev`, add `"respx>=0.21.1"`. Run `cd packages/dooers-push && uv sync --extra dev`.

- [ ] **Step 2: Write failing test**

```python
# packages/dooers-push/tests/test_auth.py
import httpx, pytest, respx
from fastapi import HTTPException
from starlette.requests import Request
from dooers_push.auth import verify_session
from dooers_push.settings import Settings

def _req(token: str | None) -> Request:
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    return Request({"type": "http", "headers": headers})

def _settings() -> Settings:
    import os
    os.environ.update(GCP_PROJECT_ID="p", BUCKET_NAME="b")
    return Settings.from_env()

@pytest.mark.asyncio
@respx.mock
async def test_verify_session_ok():
    s = _settings()
    respx.get(f"{s.core_api_url}/api/v2/identity/me").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"id": "u1", "email": "a@b.c"}})
    )
    sess = await verify_session(_req("tok"), s)
    assert sess.user_id == "u1" and sess.email == "a@b.c"

@pytest.mark.asyncio
@respx.mock
async def test_verify_session_401():
    s = _settings()
    respx.get(f"{s.core_api_url}/api/v2/identity/me").mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(HTTPException) as e:
        await verify_session(_req("tok"), s)
    assert e.value.status_code == 401

@pytest.mark.asyncio
async def test_verify_session_missing_bearer():
    with pytest.raises(HTTPException) as e:
        await verify_session(_req(None), _settings())
    assert e.value.status_code == 401
```

- [ ] **Step 3: Run, verify fail**

Run: `cd packages/dooers-push && uv run pytest tests/test_auth.py -x`
Expected: FAIL (still calls `/api/v1/session/verify`).

- [ ] **Step 4: Rewrite `verify_session`**

```python
"""Session verification — forwards the Bearer token to core v2 /identity/me."""

import httpx
from fastapi import HTTPException, Request

from dooers_protocol.auth import AuthSession
from dooers_push.settings import Settings


async def verify_session(request: Request, settings: Settings) -> AuthSession:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth_header[len("Bearer "):]

    url = f"{settings.core_api_url}/api/v2/identity/me"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=settings.request_timeout,
            )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"core unreachable: {e}") from e

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="invalid session")

    data = resp.json().get("data", {})
    user_id = data.get("id") or data.get("user_id") or ""
    email = data.get("email", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="core returned no user id")
    return AuthSession(user_id=user_id, email=email)
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd packages/dooers-push && uv run pytest tests/test_auth.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/dooers-push/src/dooers_push/auth.py packages/dooers-push/tests/test_auth.py packages/dooers-push/pyproject.toml packages/dooers-push/uv.lock
git commit -m "feat(push): verify sessions against core v2 /identity/me"
```

### Task C3: `get_agent` + `patch_host_url` against v2

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/core_client.py`
- Test: `packages/dooers-push/tests/test_core_client.py` (create)

- [ ] **Step 1: Write failing test**

```python
# packages/dooers-push/tests/test_core_client.py
import httpx, pytest, respx
from fastapi import HTTPException
from dooers_protocol.auth import AuthSession
from dooers_push.core_client import CoreClient

BASE = "https://core.test"
A = "550e8400-e29b-41d4-a716-446655440000"

@pytest.mark.asyncio
@respx.mock
async def test_get_agent_ok_and_owner():
    respx.get(f"{BASE}/api/v2/agents/{A}").mock(return_value=httpx.Response(
        200, json={"success": True, "data": {"agentId": A, "name": "x", "ownerUserId": "u1", "organizationId": "o1"}}))
    rec = await CoreClient(BASE, "tok").get_agent(A, AuthSession(user_id="u1", email="a@b.c"))
    assert rec.agent_id == A and rec.owner_user_id == "u1"

@pytest.mark.asyncio
@respx.mock
async def test_get_agent_not_owner_403():
    respx.get(f"{BASE}/api/v2/agents/{A}").mock(return_value=httpx.Response(
        200, json={"success": True, "data": {"agentId": A, "name": "x", "ownerUserId": "someone_else"}}))
    with pytest.raises(HTTPException) as e:
        await CoreClient(BASE, "tok").get_agent(A, AuthSession(user_id="u1", email="a@b.c"))
    assert e.value.status_code == 403

@pytest.mark.asyncio
@respx.mock
async def test_patch_host_url_ok():
    route = respx.patch(f"{BASE}/api/v2/agents/{A}").mock(return_value=httpx.Response(200, json={"success": True, "data": {}}))
    await CoreClient(BASE, "tok").patch_host_url(A, "https://agents.dooers.ai/" + A)
    assert route.called
    assert route.calls.last.request.read() == b'{"hostUrl": "https://agents.dooers.ai/' + A.encode() + b'"}'
```

- [ ] **Step 2: Run, verify fail**

Run: `cd packages/dooers-push && uv run pytest tests/test_core_client.py -x`
Expected: FAIL (`patch_host_url` undefined / wrong path).

- [ ] **Step 3: Rewrite `core_client.py`**

```python
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
            r = await c.patch(url, headers=self._headers(), json={"hostUrl": host_url}, timeout=self._timeout)
        if r.status_code not in (200, 204):
            raise HTTPException(status_code=502, detail=f"core patch_host_url: HTTP {r.status_code}")
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd packages/dooers-push && uv run pytest tests/test_core_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-push/src/dooers_push/core_client.py packages/dooers-push/tests/test_core_client.py
git commit -m "feat(push): v2 get_agent + patch_host_url"
```

### Task C4: Wire `patch_host_url` into the push handler

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/main.py` (around line 106–144)

- [ ] **Step 1:** In `main.py`, the existing `core.get_agent(agent_id, fallback_session=session)` call → change to `core.get_agent(agent_id, session)` (new signature). After a successful pipeline (where it currently calls `core.patch_agent_url(agent_id, ctx.lb_url)`), change to:

```python
    try:
        await core.patch_host_url(agent_id, ctx.lb_url)
    except Exception as e:  # non-fatal: agent is live, URL just not recorded
        logger.warning("patch_host_url failed for %s: %s", agent_id, e)
```

- [ ] **Step 2: Run full push test suite**

Run: `cd packages/dooers-push && uv run poe dev`
Expected: lint + typecheck + tests PASS. Fix any references to the removed `patch_agent_url`/`fallback_session`/shim.

- [ ] **Step 3: Commit**

```bash
git add packages/dooers-push/src/dooers_push/main.py
git commit -m "feat(push): record hostUrl in core after deploy (non-fatal)"
```

---

## Phase D — `dooers-cli` → v2

### Task D1: Token store with expiry

**Files:**
- Modify: `packages/dooers-cli/src/dooers/token_store.py`
- Test: `packages/dooers-cli/tests/test_token_store.py` (create or extend)

- [ ] **Step 1: Write failing test**

```python
# packages/dooers-cli/tests/test_token_store.py
import time
from pathlib import Path
from dooers.token_store import TokenStore, is_token_expired

def test_roundtrip_with_expiry(tmp_path: Path):
    p = tmp_path / "token"
    s = TokenStore(path=p)
    s.save("abc", expires_at=int(time.time()) + 3600)
    assert s.load() == "abc"
    assert not is_token_expired("abc", store=s)

def test_expired(tmp_path: Path):
    p = tmp_path / "token"
    s = TokenStore(path=p)
    s.save("abc", expires_at=int(time.time()) - 1)
    assert is_token_expired("abc", store=s)
```

- [ ] **Step 2: Run, verify fail**

Run: `cd packages/dooers-cli && uv run pytest tests/test_token_store.py -x`
Expected: FAIL (`save()` takes no `expires_at`).

- [ ] **Step 3: Rewrite `token_store.py`** — store JSON `{token, expires_at}`; keep `load()` returning the token string.

```python
"""Persisted auth token at ~/.dooers/token.json (0600). Stores token + expiry."""

import json
import time
from pathlib import Path

DEFAULT_TOKEN_PATH = Path.home() / ".dooers" / "token.json"


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_TOKEN_PATH

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def load(self) -> str | None:
        return self._read().get("token") or None

    def expires_at(self) -> int:
        return int(self._read().get("expires_at", 0))

    def save(self, token: str, expires_at: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"token": token, "expires_at": expires_at}))
        self.path.chmod(0o600)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def is_token_expired(token: str | None, store: "TokenStore | None" = None) -> bool:
    """True if no token or the stored expiry has passed."""
    if not token:
        return True
    store = store or TokenStore()
    exp = store.expires_at()
    return exp == 0 or time.time() >= exp
```

- [ ] **Step 4: Run tests, verify pass.** `cd packages/dooers-cli && uv run pytest tests/test_token_store.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-cli/src/dooers/token_store.py packages/dooers-cli/tests/test_token_store.py
git commit -m "feat(cli): token store with expiry (json)"
```

### Task D2: v2 `CoreClient` (OTP auth + me + revoke + orgs)

**Files:**
- Modify: `packages/dooers-cli/src/dooers/core_client.py`
- Test: `packages/dooers-cli/tests/test_core_client.py` (create)

- [ ] **Step 1: [VERIFY] OTP token header (dev)** — confirm how the Bearer token is returned:

```bash
# request a code to your email, then:
curl -i -s -X POST https://api-v2.dev.dooers.ai/api/v2/auth/sign-in/email-otp \
  -H 'content-type: application/json' -d '{"email":"<you>","otp":"<code>"}' | grep -i 'set-auth-token'
```

Expected: a `set-auth-token: <token>` response header. If absent, the client falls back to `POST /api/v2/identity/token` (cookie session) — note which path holds and implement that branch in Step 3.

- [ ] **Step 2: Add `respx` dev dep** to `packages/dooers-cli/pyproject.toml` (`"respx>=0.21.1"`), `uv sync --extra dev`. Write failing tests:

```python
# packages/dooers-cli/tests/test_core_client.py
import httpx, pytest, respx
from dooers.core_client import CoreClient, CoreClientError

BASE = "https://core.test"

@respx.mock
def test_send_otp():
    r = respx.post(f"{BASE}/api/v2/auth/email-otp/send-verification-otp").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {}}))
    CoreClient(BASE).send_otp("a@b.c")
    assert r.called

@respx.mock
def test_verify_otp_reads_header():
    respx.post(f"{BASE}/api/v2/auth/sign-in/email-otp").mock(
        return_value=httpx.Response(200, headers={"set-auth-token": "TKN"}, json={"success": True, "data": {}}))
    token, exp = CoreClient(BASE).verify_otp("a@b.c", "123456")
    assert token == "TKN" and exp > 0

@respx.mock
def test_me():
    respx.get(f"{BASE}/api/v2/identity/me").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"id": "u1", "email": "a@b.c"}}))
    me = CoreClient(BASE, token="t").me()
    assert me.user_id == "u1" and me.email == "a@b.c"

@respx.mock
def test_list_organizations():
    respx.get(f"{BASE}/api/v2/organizations").mock(
        return_value=httpx.Response(200, json={"success": True, "data": [{"organizationId": "o1", "name": "Org"}]}))
    orgs = CoreClient(BASE, token="t").list_organizations()
    assert orgs[0]["organizationId"] == "o1"

@respx.mock
def test_error_envelope_surfaces_message():
    respx.get(f"{BASE}/api/v2/identity/me").mock(
        return_value=httpx.Response(401, json={"success": False, "error": {"message": "nope"}}))
    with pytest.raises(CoreClientError, match="nope"):
        CoreClient(BASE, token="t").me()
```

- [ ] **Step 3: Run, verify fail**, then rewrite `core_client.py`:

```python
"""HTTP client for Dooers core v2 (better-auth OTP + agents)."""

import time

import httpx

from dooers_protocol.auth import WhoamiResponse

ACCESS_TOKEN_FALLBACK_TTL = 60 * 60 * 24 * 7  # 7d if core doesn't tell us


class CoreClientError(RuntimeError):
    """CLI-friendly error."""


def _data(resp: httpx.Response) -> dict:
    body = resp.json()
    if isinstance(body, dict) and body.get("success") is False:
        raise CoreClientError(body.get("error", {}).get("message", f"HTTP {resp.status_code}"))
    if resp.status_code >= 400:
        raise CoreClientError(f"HTTP {resp.status_code}")
    return body.get("data", body) if isinstance(body, dict) else body


class CoreClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 15.0) -> None:
        self.api = base_url.rstrip("/") + "/api/v2"
        self.token = token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    # ---- auth ----
    def auth_method(self) -> str:
        try:
            r = httpx.get(f"{self.api}/identity/auth-method", timeout=self._timeout)
            return _data(r).get("method", "otp")
        except httpx.HTTPError as e:
            raise CoreClientError(f"auth-method failed: {e}") from e

    def send_otp(self, email: str) -> None:
        try:
            r = httpx.post(
                f"{self.api}/auth/email-otp/send-verification-otp",
                json={"email": email, "type": "sign-in"},
                timeout=self._timeout,
            )
            _data(r)
        except httpx.HTTPError as e:
            raise CoreClientError(f"failed to send code: {e}") from e

    def verify_otp(self, email: str, code: str) -> tuple[str, int]:
        """Returns (bearer_token, expires_at_epoch)."""
        try:
            r = httpx.post(
                f"{self.api}/auth/sign-in/email-otp",
                json={"email": email, "otp": code},
                timeout=self._timeout,
            )
            _data(r)  # raises on error envelope
            token = r.headers.get("set-auth-token")
            if not token:
                # fallback: mint via /identity/token using the session cookie just set
                tr = httpx.post(f"{self.api}/identity/token", cookies=r.cookies, timeout=self._timeout)
                d = _data(tr)
                token = d["accessToken"]
                return token, int(time.time()) + int(d.get("expiresIn", ACCESS_TOKEN_FALLBACK_TTL))
            return token, int(time.time()) + ACCESS_TOKEN_FALLBACK_TTL
        except httpx.HTTPError as e:
            raise CoreClientError(f"failed to verify code: {e}") from e

    def me(self) -> WhoamiResponse:
        try:
            r = httpx.get(f"{self.api}/identity/me", headers=self._headers(), timeout=self._timeout)
            d = _data(r)
            return WhoamiResponse(user_id=d.get("id", ""), email=d.get("email", ""))
        except httpx.HTTPError as e:
            raise CoreClientError(f"me failed: {e}") from e

    def revoke(self) -> None:
        try:
            httpx.post(f"{self.api}/identity/revoke", headers=self._headers(), timeout=self._timeout)
        except httpx.HTTPError:
            pass  # best-effort

    def list_organizations(self) -> list[dict]:
        try:
            r = httpx.get(f"{self.api}/organizations", headers=self._headers(), timeout=self._timeout)
            return _data(r)
        except httpx.HTTPError as e:
            raise CoreClientError(f"list organizations failed: {e}") from e
```

- [ ] **Step 4: Run tests, verify pass.** `cd packages/dooers-cli && uv run pytest tests/test_core_client.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-cli/src/dooers/core_client.py packages/dooers-cli/tests/test_core_client.py packages/dooers-cli/pyproject.toml packages/dooers-cli/uv.lock
git commit -m "feat(cli): core v2 client (otp auth, me, revoke, organizations)"
```

### Task D3: User config + org resolution + `dooers org`

**Files:**
- Create: `packages/dooers-cli/src/dooers/user_config.py`
- Create: `packages/dooers-cli/src/dooers/org.py`
- Modify: `packages/dooers-cli/src/dooers/cli.py` (register `org` group)
- Test: `packages/dooers-cli/tests/test_org.py` (create)

- [ ] **Step 1: Write failing test for resolution precedence**

```python
# packages/dooers-cli/tests/test_org.py
from dooers.org import resolve_org

ORGS = [{"organizationId": "o1", "name": "A"}, {"organizationId": "o2", "name": "B"}]

def test_explicit_flag_wins():
    assert resolve_org(orgs=ORGS, explicit="o2", default=None, prompt=lambda o: "o1") == "o2"

def test_saved_default_used():
    assert resolve_org(orgs=ORGS, explicit=None, default="o1", prompt=lambda o: "o2") == "o1"

def test_single_org_auto():
    one = [ORGS[0]]
    assert resolve_org(orgs=one, explicit=None, default=None, prompt=lambda o: "x") == "o1"

def test_multiple_prompts():
    assert resolve_org(orgs=ORGS, explicit=None, default=None, prompt=lambda o: "o2") == "o2"
```

- [ ] **Step 2: Run, verify fail.** Then create `user_config.py`:

```python
"""~/.dooers/config.json — non-secret CLI prefs (default org)."""

import json
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".dooers" / "config.json"


class UserConfig:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_CONFIG_PATH

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def get_default_org(self) -> str | None:
        return self._read().get("default_org")

    def set_default_org(self, org_id: str) -> None:
        data = self._read()
        data["default_org"] = org_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data))
```

- [ ] **Step 3: Create `org.py`** (pure `resolve_org` + the `dooers org` Typer group):

```python
"""Org resolution + `dooers org list|use`."""

from collections.abc import Callable

import typer

from dooers.core_client import CoreClient, CoreClientError
from dooers.settings import Settings
from dooers.token_store import TokenStore, is_token_expired
from dooers.user_config import UserConfig

app = typer.Typer(no_args_is_help=True)


def resolve_org(
    *,
    orgs: list[dict],
    explicit: str | None,
    default: str | None,
    prompt: Callable[[list[dict]], str],
) -> str:
    """Precedence: explicit flag > saved default > single-org auto > prompt."""
    if explicit:
        return explicit
    ids = {o["organizationId"] for o in orgs}
    if default and default in ids:
        return default
    if len(orgs) == 1:
        return orgs[0]["organizationId"]
    return prompt(orgs)


def _token(settings: Settings) -> str:
    store = TokenStore()
    token = store.load()
    if not token or is_token_expired(token, store=store):
        typer.echo("Not authenticated. Run `dooers login`.", err=True)
        raise typer.Exit(code=1)
    return token


def resolve_org_for_cli(settings: Settings, explicit: str | None) -> str:
    """Fetch orgs, apply precedence, persist the chosen default when prompting."""
    token = _token(settings)
    try:
        orgs = CoreClient(base_url=settings.core_url, token=token).list_organizations()
    except CoreClientError as e:
        typer.echo(f"could not list organizations: {e}", err=True)
        raise typer.Exit(code=1) from e
    if not orgs:
        typer.echo("You don't belong to any organization.", err=True)
        raise typer.Exit(code=1)
    cfg = UserConfig()

    def _prompt(options: list[dict]) -> str:
        typer.echo("Multiple organizations — choose one:")
        for i, o in enumerate(options, 1):
            typer.echo(f"  {i}. {o.get('name', o['organizationId'])} ({o['organizationId']})")
        idx = typer.prompt("Number", type=int)
        chosen = options[idx - 1]["organizationId"]
        cfg.set_default_org(chosen)
        typer.echo(f"Saved default org: {chosen}")
        return chosen

    return resolve_org(orgs=orgs, explicit=explicit, default=cfg.get_default_org(), prompt=_prompt)


@app.command(name="list")
def list_orgs(ctx: typer.Context) -> None:
    settings: Settings = ctx.obj
    token = _token(settings)
    orgs = CoreClient(base_url=settings.core_url, token=token).list_organizations()
    default = UserConfig().get_default_org()
    for o in orgs:
        mark = " (default)" if o["organizationId"] == default else ""
        typer.echo(f"{o['organizationId']}  {o.get('name', '')}{mark}")


@app.command()
def use(ctx: typer.Context, organization_id: str = typer.Argument(...)) -> None:
    UserConfig().set_default_org(organization_id)
    typer.echo(f"Default org set to {organization_id}")
```

- [ ] **Step 4: Register in `cli.py`** — add `from dooers import org` and `app.add_typer(org.app, name="org", help="List and select your organization.")`.

- [ ] **Step 5: Run tests, verify pass.** `cd packages/dooers-cli && uv run pytest tests/test_org.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/dooers-cli/src/dooers/user_config.py packages/dooers-cli/src/dooers/org.py packages/dooers-cli/src/dooers/cli.py packages/dooers-cli/tests/test_org.py
git commit -m "feat(cli): org resolution + dooers org list|use"
```

### Task D4: v2 agents store + `agents` commands (remove shim)

**Files:**
- Modify: `packages/dooers-cli/src/dooers/agent_store.py` (delete `FileShimAgentStore`)
- Modify: `packages/dooers-cli/src/dooers/agents.py`
- Test: `packages/dooers-cli/tests/test_agent_store.py` (create)

- [ ] **Step 1: Write failing test**

```python
# packages/dooers-cli/tests/test_agent_store.py
import httpx, respx
from dooers.agent_store import HTTPCoreAgentStore
from dooers_protocol.agents import CreateAgentRequest

BASE = "https://core.test"
A = "550e8400-e29b-41d4-a716-446655440000"

@respx.mock
def test_create_posts_org_and_name():
    route = respx.post(f"{BASE}/api/v2/agents").mock(return_value=httpx.Response(
        201, json={"success": True, "data": {"agentId": A, "name": "x", "organizationId": "o1", "ownerUserId": "u1"}}))
    rec = HTTPCoreAgentStore(BASE, "tok").create(CreateAgentRequest(organization_id="o1", name="x"))
    assert rec.agent_id == A
    assert route.calls.last.request.read() == b'{"organizationId": "o1", "name": "x"}'

@respx.mock
def test_list_by_org():
    respx.get(f"{BASE}/api/v2/agents/organization/o1").mock(return_value=httpx.Response(
        200, json={"success": True, "data": [{"agentId": A, "name": "x", "organizationId": "o1"}]}))
    recs = HTTPCoreAgentStore(BASE, "tok").list_by_org("o1")
    assert recs[0].agent_id == A
```

- [ ] **Step 2: Run, verify fail.** Then rewrite `agent_store.py` (single class, no shim):

```python
"""v2 core-backed agent store. Talks to /api/v2/agents with {success,data}."""

import httpx

from dooers_protocol.agents import AgentRecord, CreateAgentRequest


class AgentStoreError(RuntimeError):
    pass


def _data(resp: httpx.Response):
    body = resp.json()
    if isinstance(body, dict) and body.get("success") is False:
        raise AgentStoreError(body.get("error", {}).get("message", f"HTTP {resp.status_code}"))
    if resp.status_code >= 400:
        raise AgentStoreError(f"HTTP {resp.status_code}")
    return body.get("data", body)


def _record(d: dict) -> AgentRecord:
    return AgentRecord(
        agent_id=d["agentId"],
        name=d.get("name", ""),
        owner_user_id=d.get("ownerUserId"),
        organization_id=d.get("organizationId"),
        host_url=d.get("hostUrl"),
    )


class HTTPCoreAgentStore:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0) -> None:
        self.api = base_url.rstrip("/") + "/api/v2"
        self.token = token
        self._timeout = timeout

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def create(self, req: CreateAgentRequest) -> AgentRecord:
        r = httpx.post(
            f"{self.api}/agents",
            headers=self._h(),
            json={"organizationId": req.organization_id, "name": req.name},
            timeout=self._timeout,
        )
        return _record(_data(r))

    def list_by_org(self, organization_id: str) -> list[AgentRecord]:
        r = httpx.get(f"{self.api}/agents/organization/{organization_id}", headers=self._h(), timeout=self._timeout)
        return [_record(d) for d in _data(r)]

    def get(self, agent_id: str) -> AgentRecord:
        r = httpx.get(f"{self.api}/agents/{agent_id}", headers=self._h(), timeout=self._timeout)
        if r.status_code == 404:
            raise KeyError(agent_id)
        return _record(_data(r))
```

- [ ] **Step 3: Rewrite `agents.py`** — remove shim/`whoami`-store logic; use token + org resolution:

```python
"""`dooers agents` subcommands: list, create, show (core v2)."""

from pathlib import Path

import typer

from dooers import config
from dooers.agent_store import AgentStoreError, HTTPCoreAgentStore
from dooers.org import resolve_org_for_cli
from dooers.settings import Settings
from dooers.token_store import TokenStore, is_token_expired
from dooers_protocol import PROTOCOL_VERSION
from dooers_protocol.agents import AgentManifest, CreateAgentRequest

app = typer.Typer(no_args_is_help=True)


def _store(ctx: typer.Context) -> tuple[HTTPCoreAgentStore, Settings]:
    settings: Settings = ctx.obj
    store_token = TokenStore()
    token = store_token.load()
    if not token or is_token_expired(token, store=store_token):
        typer.echo("Not authenticated. Run `dooers login`.", err=True)
        raise typer.Exit(code=1)
    return HTTPCoreAgentStore(settings.core_url, token), settings


@app.command()
def create(
    ctx: typer.Context,
    name: str = typer.Option(..., help="Display name for the new agent."),
    org: str | None = typer.Option(None, "--org", help="Organization id (else resolved/prompted)."),
) -> None:
    store, settings = _store(ctx)
    organization_id = resolve_org_for_cli(settings, org)
    try:
        rec = store.create(CreateAgentRequest(organization_id=organization_id, name=name))
    except AgentStoreError as e:
        typer.echo(f"create failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    config.write_manifest(
        AgentManifest(
            protocol_version=PROTOCOL_VERSION,
            agent_id=rec.agent_id,
            name=rec.name,
            organization_id=rec.organization_id or organization_id,
        ),
        directory=Path.cwd(),
    )
    typer.echo(f"Created {rec.agent_id}. {config.MANIFEST_FILENAME} written.")


@app.command(name="list")
def list_agents(ctx: typer.Context, org: str | None = typer.Option(None, "--org")) -> None:
    store, settings = _store(ctx)
    organization_id = resolve_org_for_cli(settings, org)
    records = store.list_by_org(organization_id)
    if not records:
        typer.echo("No agents yet. Try `dooers agents create --name my-agent`.")
        return
    typer.echo(f"{'ID':<38}{'NAME':<24}URL")
    for r in records:
        typer.echo(f"{r.agent_id:<38}{r.name:<24}{r.host_url or '—'}")


@app.command()
def show(ctx: typer.Context, agent_id: str = typer.Argument(...)) -> None:
    store, _ = _store(ctx)
    try:
        r = store.get(agent_id)
    except KeyError:
        typer.echo(f"Agent {agent_id} not found.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"ID:    {r.agent_id}\nName:  {r.name}\nOrg:   {r.organization_id}\nURL:   {r.host_url or '—'}")
```

- [ ] **Step 4: Run tests + full CLI suite.** `cd packages/dooers-cli && uv run pytest tests/test_agent_store.py -v && uv run poe dev` → PASS (fix any leftover imports of the removed shim).

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-cli/src/dooers/agent_store.py packages/dooers-cli/src/dooers/agents.py packages/dooers-cli/tests/test_agent_store.py
git commit -m "feat(cli): v2 agents store + commands; remove file shim"
```

### Task D5: Wire login/whoami/logout to v2

**Files:**
- Modify: `packages/dooers-cli/src/dooers/auth.py`

- [ ] **Step 1:** Update `login` to use `send_otp` + `verify_otp` and store with expiry:

```python
    client = CoreClient(base_url=settings.core_url)
    try:
        typer.echo("Requesting verification code…")
        client.send_otp(email)
        code = typer.prompt("Enter the code emailed to you")
        token, expires_at = client.verify_otp(email, code)
    except CoreClientError as e:
        typer.echo(f"Authentication failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    store.save(token, expires_at=expires_at)
    typer.echo("Authenticated.")
```

- [ ] **Step 2:** `whoami` → `client.me()`; `logout` → `client.revoke()`. Update `is_token_expired(token)` calls to `is_token_expired(token, store=store)`.

- [ ] **Step 3: Run full CLI suite.** `cd packages/dooers-cli && uv run poe dev` → PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/dooers-cli/src/dooers/auth.py
git commit -m "feat(cli): login/whoami/logout via core v2 (otp + bearer)"
```

---

## Phase E — End-to-end (manual, pre-release)

- [ ] Deploy dev/prod core with the migration; rebuild + redeploy `dooers-push` (image v4) with the v2 changes (envs unchanged; `host.dooers.ai` already fronts it).
- [ ] `pip install -e packages/dooers-cli` (or run from the repo) and run:

```bash
cd /home/frndvrgs/software/dooers/dooers-agent-deploy-test
dooers login <email>            # OTP
dooers org list                 # confirm org resolution
dooers agents create --name agent-deploy-test
dooers push
curl -X POST https://agents.dooers.ai/<uuid>/chat -H 'content-type: application/json' -d '{"message":"olá"}'
dooers agents show <uuid>       # hostUrl populated
```

- [ ] Confirm `dooers agents show` reports the `hostUrl`. Then do the single `pip`/core release.

---

## Self-review notes
- **Spec coverage:** §3 core→B; §4 cli→D; §5 push→C (incl. §5.3 UUID naming→C1); §6 protocol→A; §7 error handling→`_data` envelope helper + non-fatal patch (C4); §8 testing→tests in each task; §9 sequencing reordered to protocol-first (A) since push/cli import it.
- **Two `[VERIFY]` gates** (B2 dev curl, D2 `set-auth-token` header) cover the §10 risks; the `verify_otp` fallback handles either header/cookie outcome.
- **Type consistency:** `cloud_run_service_name` used in C1 (cloudbuild + loadbalancer); `AgentRecord(agent_id, name, owner_user_id, organization_id, host_url)` consistent across A/C/D; `is_token_expired(token, store=)` signature consistent across D1/D3/D4/D5.
