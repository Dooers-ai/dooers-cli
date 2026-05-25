# Dooers CLI v2 + dooers-push POC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship four incrementally-demoable milestones (M1 auth → M2 agents CRUD → M3 push round-trip → M4 visible auditor) that together prove `dooers push <agent_id>` works end-to-end on the dev GCP environment.

**Architecture:** Three-package Python monorepo (`dooers-cli`, `dooers-push`, `dooers-protocol`) following the rfnry/chat independent-package pattern: each package owns its own `pyproject.toml` / `uv.lock` / `.venv`, with cross-package imports resolved via `tool.uv.sources` editable paths. Push is synchronous from the CLI's view; `dooers-push` polls Cloud Build for ~3–5 min, then returns the live Cloud Run URL. Auditor and provisioner are typed pipeline stubs in this POC.

**Tech Stack:** Python 3.10+ (CLI) / 3.12+ (server), Typer, FastAPI, Pydantic v2, httpx, uv + poethepoet, hatchling, Google Cloud SDK clients (storage, cloudbuild, run), ruff/mypy/pytest. Reference for v1 logic to port: `../deploy-service/` (sibling repo).

**Companion spec:** `docs/superpowers/specs/2026-05-26-dooers-cli-v2-design.md`.

---

## File Structure

### Files modified per milestone

**M1 — Auth (`packages/dooers-cli/`):**
- `src/dooers/token_store.py` *(create)* — `~/.dooers/token` read/write/clear + JWT expiry check.
- `src/dooers/core_client.py` *(modify)* — implement auth methods (request_otp / verify_otp / whoami / logout).
- `src/dooers/auth.py` *(modify)* — replace stubs with real Typer commands.
- `src/dooers/settings.py` *(create)* — global config (core URL, push URL, env) with flag/env/default precedence.
- `src/dooers/cli.py` *(modify)* — top-level Typer callback that resolves settings once.
- `tests/test_token_store.py` *(create)* — pure tests for token persistence.

**M2 — Agents CRUD (`packages/dooers-cli/`):**
- `src/dooers/agent_store.py` *(create)* — `AgentStore` protocol + `HTTPCoreAgentStore` + `FileShimAgentStore`.
- `src/dooers/core_client.py` *(modify)* — wire `HTTPCoreAgentStore` through the existing class.
- `src/dooers/config.py` *(modify)* — implement `read_manifest` / `write_manifest`.
- `src/dooers/agents.py` *(modify)* — replace stubs with real Typer commands.
- `tests/test_agent_store_file.py` *(create)* — pure tests for `FileShimAgentStore`.
- `tests/test_config.py` *(create)* — pure tests for `dooers.yaml` round-trip.

**M3 — Push round-trip (both `packages/dooers-cli/` and `packages/dooers-push/`):**
- `packages/dooers-cli/src/dooers/ignore.py` *(modify)* — port v1's `.dooersignore` + `make_archive`.
- `packages/dooers-cli/src/dooers/push_client.py` *(modify)* — implement multipart `push()` with synchronous wait.
- `packages/dooers-cli/src/dooers/push.py` *(modify)* — wire archive → upload → spinner → print URL.
- `packages/dooers-cli/tests/test_ignore.py` *(create)* — pure tests for ignore patterns.
- `packages/dooers-push/src/dooers_push/settings.py` *(already exists)*.
- `packages/dooers-push/src/dooers_push/auth.py` *(modify)* — forward bearer to core `/session/verify`.
- `packages/dooers-push/src/dooers_push/core_client.py` *(modify)* — implement `get_agent` + `patch_agent_url`.
- `packages/dooers-push/src/dooers_push/storage.py` *(modify)* — GCS upload with labels.
- `packages/dooers-push/src/dooers_push/gcp/cloudbuild.py` *(modify)* — `trigger_build` + `wait_for_build`.
- `packages/dooers-push/src/dooers_push/gcp/cloudrun.py` *(modify)* — `describe_service_url`.
- `packages/dooers-push/src/dooers_push/pipeline/deployer.py` *(modify)* — wire CB + CR.
- `packages/dooers-push/src/dooers_push/main.py` *(modify)* — implement `POST /v1/push/{agent_id}` end-to-end.

**M4 — Visible auditor (`packages/dooers-push/` only):**
- `packages/dooers-push/src/dooers_push/pipeline/auditor.py` *(modify)* — extract archive, list endpoints + imports, log non-empty `AuditReport`.
- `packages/dooers-cli/src/dooers/push.py` *(modify)* — print audit summary after build.

---

## Phase 0: Bootstrap

### Task 0.1: Verify scaffold and install all packages

**Files:** none (uses existing scaffold).

- [ ] **Step 1: Sync dooers-protocol**

Run:
```bash
cd packages/dooers-protocol && uv sync --extra dev
```
Expected: creates `.venv`, installs `pydantic`, dev tools, no errors.

- [ ] **Step 2: Sync dooers-cli**

Run:
```bash
cd packages/dooers-cli && uv sync --extra dev
```
Expected: installs typer, httpx, pyyaml, tqdm, dooers-protocol (editable), no errors.

- [ ] **Step 3: Sync dooers-push**

Run:
```bash
cd packages/dooers-push && uv sync --extra dev
```
Expected: installs fastapi, uvicorn, google-cloud-*, dooers-protocol (editable), no errors.

- [ ] **Step 4: Run all smoke tests**

Run:
```bash
cd packages/dooers-protocol && uv run poe test
cd packages/dooers-cli && uv run poe test
cd packages/dooers-push && uv run poe test
```
Expected: all green. ~10 tests total.

- [ ] **Step 5: Verify CLI help works**

Run:
```bash
cd packages/dooers-cli && uv run dooers --help
uv run dooers auth --help
uv run dooers agents --help
uv run dooers push --help
```
Expected: each prints help text, exit 0.

- [ ] **Step 6: Commit any sync byproducts**

```bash
cd <repo-root>
git status
# uv.lock files were created; commit them
git add packages/*/uv.lock
git commit -m "chore: commit uv.lock files for all packages"
```

---

## Phase M1: Authentication (~0.5 day)

### Task 1.1: Global settings resolver

**Files:**
- Create: `packages/dooers-cli/src/dooers/settings.py`
- Modify: `packages/dooers-cli/src/dooers/cli.py`

- [ ] **Step 1: Write settings.py**

```python
# packages/dooers-cli/src/dooers/settings.py
"""Global CLI configuration: core URL, push URL, env.

Precedence: explicit CLI flag > env var > built-in default.
The top-level Typer callback resolves this once and stashes it on the
Typer context so every subcommand sees the same values.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    core_url: str
    push_url: str
    env: str

    @classmethod
    def resolve(
        cls,
        core_url: str | None = None,
        push_url: str | None = None,
        env: str | None = None,
    ) -> "Settings":
        return cls(
            core_url=(core_url or os.environ.get("DOOERS_CORE_URL") or "https://api.dooers.ai").rstrip("/"),
            push_url=(push_url or os.environ.get("DOOERS_PUSH_URL") or "https://push.dooers.ai").rstrip("/"),
            env=(env or os.environ.get("DOOERS_ENV") or "prod"),
        )
```

- [ ] **Step 2: Wire settings into the root Typer callback**

Replace `packages/dooers-cli/src/dooers/cli.py` with:

```python
"""Top-level Typer app — mounts auth, agents, push subcommand groups."""

import typer

from dooers import agents, auth, push
from dooers.settings import Settings

app = typer.Typer(
    name="dooers",
    add_completion=False,
    no_args_is_help=True,
    help="Dooers CLI — push agents to Dooers, manage agent records, authenticate.",
)


@app.callback()
def _root(
    ctx: typer.Context,
    core_url: str | None = typer.Option(None, "--core-url", help="Override core API URL."),
    push_url: str | None = typer.Option(None, "--push-url", help="Override dooers-push URL."),
    env: str | None = typer.Option(None, "--env", help="Target environment: prod | stg | dev."),
) -> None:
    """Resolve global settings once per invocation."""
    ctx.obj = Settings.resolve(core_url=core_url, push_url=push_url, env=env)


app.add_typer(auth.app, name="auth", help="Authenticate with the Dooers core API.")
app.add_typer(agents.app, name="agents", help="List, create, and inspect your agents.")
app.command(name="push", help="Archive cwd and push it as a new build of an agent.")(push.push)


if __name__ == "__main__":
    app()
```

- [ ] **Step 3: Verify CLI still works**

Run:
```bash
cd packages/dooers-cli && uv run dooers --help
DOOERS_CORE_URL=https://api.dev.dooers.ai uv run dooers auth --help
```
Expected: both print help, exit 0. (Settings resolution is silent unless a subcommand reads `ctx.obj`.)

- [ ] **Step 4: Run smoke tests**

Run: `uv run poe test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-cli/src/dooers/settings.py packages/dooers-cli/src/dooers/cli.py
git commit -m "feat(cli): add global settings resolver with flag/env/default precedence"
```

### Task 1.2: Token store with JWT expiry check

**Files:**
- Create: `packages/dooers-cli/src/dooers/token_store.py`
- Create: `packages/dooers-cli/tests/test_token_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# packages/dooers-cli/tests/test_token_store.py
"""Tests for token persistence and JWT expiry parsing."""

import base64
import json
import time
from pathlib import Path

import pytest

from dooers.token_store import TokenStore, is_token_expired


def _make_jwt(exp_offset_s: int) -> str:
    """Forge a JWT-shaped string with a given exp offset from now."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = {"exp": int(time.time()) + exp_offset_s}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{payload_b64}.sig"


def test_is_token_expired_returns_true_for_past_exp() -> None:
    assert is_token_expired(_make_jwt(-60)) is True


def test_is_token_expired_returns_false_for_future_exp() -> None:
    assert is_token_expired(_make_jwt(3600)) is False


def test_is_token_expired_returns_true_for_malformed() -> None:
    assert is_token_expired("not-a-jwt") is True
    assert is_token_expired("") is True


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    store = TokenStore(path=tmp_path / "token")
    store.save("abc123")
    assert store.load() == "abc123"


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    store = TokenStore(path=tmp_path / "missing")
    assert store.load() is None


def test_clear_removes_file(tmp_path: Path) -> None:
    p = tmp_path / "token"
    store = TokenStore(path=p)
    store.save("x")
    assert p.exists()
    store.clear()
    assert not p.exists()


def test_save_uses_0600_permissions(tmp_path: Path) -> None:
    p = tmp_path / "token"
    store = TokenStore(path=p)
    store.save("x")
    # mask off file type bits; require read+write for owner only
    mode = p.stat().st_mode & 0o777
    assert mode == 0o600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/dooers-cli && uv run pytest tests/test_token_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dooers.token_store'`.

- [ ] **Step 3: Implement token_store.py**

```python
# packages/dooers-cli/src/dooers/token_store.py
"""Persisted auth token at ~/.dooers/token with 0600 permissions.

Also exposes is_token_expired() — parses JWT `exp` claim without verifying
the signature (we re-verify against core on every authenticated request).
"""

import base64
import json
import time
from pathlib import Path

DEFAULT_TOKEN_PATH = Path.home() / ".dooers" / "token"


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_TOKEN_PATH

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            return self.path.read_text().strip() or None
        except OSError:
            return None

    def save(self, token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(token)
        self.path.chmod(0o600)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def is_token_expired(token: str) -> bool:
    """Decode JWT payload and check `exp`. Returns True on any parse error."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return True
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = int(payload.get("exp", 0))
        return time.time() >= exp
    except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_token_store.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-cli/src/dooers/token_store.py packages/dooers-cli/tests/test_token_store.py
git commit -m "feat(cli): add TokenStore with JWT expiry check"
```

### Task 1.3: CoreClient — auth methods

**Files:**
- Modify: `packages/dooers-cli/src/dooers/core_client.py`

- [ ] **Step 1: Replace core_client.py with the real auth implementation**

```python
# packages/dooers-cli/src/dooers/core_client.py
"""HTTP client for the Dooers core API (auth, agent records).

Reference behavior: see v1 CLI flow in ../../../deploy-service/cli/dooers/cli.py
- /api/v1/session/request  → returns {"output": {"email_id": "..."}}
- /api/v1/session/create   → returns auth token via `auth` cookie
- /api/v1/session/verify   → returns user dict
- /api/v1/session/remove   → logout
"""

import httpx
from pydantic import ValidationError

from dooers_protocol.agents import AgentRecord, CreateAgentRequest
from dooers_protocol.auth import WhoamiResponse


class CoreClientError(RuntimeError):
    """Anything we'd want to surface as a CLI-friendly error."""


class CoreClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._timeout = timeout

    # ------ auth ---------------------------------------------------------

    def login_request_otp(self, email: str) -> str:
        """POST /api/v1/session/request. Returns `email_id`."""
        try:
            r = httpx.post(
                f"{self.base_url}/api/v1/session/request",
                json={"email": email, "method": "email"},
                timeout=self._timeout,
            )
            r.raise_for_status()
            data = r.json()
            email_id = data.get("output", {}).get("email_id")
            if not email_id:
                raise CoreClientError(f"core returned no email_id (body: {data})")
            return email_id
        except httpx.HTTPError as e:
            raise CoreClientError(f"failed to request OTP: {e}") from e

    def login_verify_otp(self, email_id: str, code: str) -> str:
        """POST /api/v1/session/create. Returns the auth token (cookie value)."""
        try:
            r = httpx.post(
                f"{self.base_url}/api/v1/session/create",
                json={"email_id": email_id, "code": code},
                timeout=self._timeout,
            )
            r.raise_for_status()
            cookie = r.cookies.get("auth")
            if cookie:
                return cookie
            # fallback: token may also appear in body
            token = r.json().get("output", {}).get("token")
            if token:
                return token
            raise CoreClientError("core returned no auth token")
        except httpx.HTTPError as e:
            raise CoreClientError(f"failed to verify OTP: {e}") from e

    def whoami(self) -> WhoamiResponse:
        if not self.token:
            raise CoreClientError("not authenticated")
        try:
            r = httpx.get(
                f"{self.base_url}/api/v1/session/verify",
                cookies={"auth": self.token},
                timeout=self._timeout,
            )
            r.raise_for_status()
            data = r.json()
            output = data.get("output", data)
            # The core response shape isn't strict; accept either flat or nested.
            user_id = output.get("user_id") or output.get("id") or output.get("user", {}).get("id", "")
            email = output.get("email") or output.get("user", {}).get("email", "")
            try:
                return WhoamiResponse(user_id=user_id, email=email)
            except ValidationError as e:
                raise CoreClientError(f"unexpected /session/verify shape: {data}") from e
        except httpx.HTTPError as e:
            raise CoreClientError(f"whoami failed: {e}") from e

    def logout(self) -> None:
        if not self.token:
            return
        try:
            httpx.post(
                f"{self.base_url}/api/v1/session/remove",
                cookies={"auth": self.token},
                timeout=self._timeout,
            )
        except httpx.HTTPError:
            pass  # logout is best-effort

    # ------ agents (stub for M2) -----------------------------------------

    def list_agents(self) -> list[AgentRecord]:
        raise NotImplementedError("M2")

    def create_agent(self, req: CreateAgentRequest) -> AgentRecord:
        raise NotImplementedError("M2")

    def get_agent(self, agent_id: str) -> AgentRecord:
        raise NotImplementedError("M2")
```

- [ ] **Step 2: Sanity check — module imports cleanly**

Run: `cd packages/dooers-cli && uv run python -c "from dooers.core_client import CoreClient; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Run all CLI tests**

Run: `uv run poe test`
Expected: PASS (existing smoke tests + token_store tests).

- [ ] **Step 4: Commit**

```bash
git add packages/dooers-cli/src/dooers/core_client.py
git commit -m "feat(cli): implement CoreClient auth methods (request_otp, verify_otp, whoami, logout)"
```

### Task 1.4: `dooers auth login` command

**Files:**
- Modify: `packages/dooers-cli/src/dooers/auth.py`

- [ ] **Step 1: Replace auth.py with the real implementation**

```python
# packages/dooers-cli/src/dooers/auth.py
"""`dooers auth` subcommands: login, whoami, logout."""

import typer

from dooers.core_client import CoreClient, CoreClientError
from dooers.settings import Settings
from dooers.token_store import TokenStore, is_token_expired

app = typer.Typer(no_args_is_help=True)


def _settings(ctx: typer.Context) -> Settings:
    s = ctx.obj
    if not isinstance(s, Settings):
        raise typer.Exit("internal: settings not resolved")
    return s


@app.command()
def login(
    ctx: typer.Context,
    email: str = typer.Option(..., prompt=True, help="Your email address."),
) -> None:
    """Authenticate with Dooers via OTP sent to email."""
    settings = _settings(ctx)
    store = TokenStore()

    existing = store.load()
    if existing and not is_token_expired(existing):
        typer.echo("Already authenticated. Run `dooers auth logout` first to re-login.")
        raise typer.Exit(code=0)

    client = CoreClient(base_url=settings.core_url)
    try:
        typer.echo("Requesting verification code…")
        email_id = client.login_request_otp(email)
        typer.echo("Verification code sent to your email.")
        code = typer.prompt("Enter the code")
        token = client.login_verify_otp(email_id=email_id, code=code)
    except CoreClientError as e:
        typer.echo(f"Authentication failed: {e}", err=True)
        raise typer.Exit(code=1) from e

    store.save(token)
    typer.echo("Authenticated.")


@app.command()
def whoami(ctx: typer.Context) -> None:
    """Show the currently authenticated user."""
    settings = _settings(ctx)
    store = TokenStore()
    token = store.load()
    if not token:
        typer.echo("Not authenticated. Run `dooers auth login`.", err=True)
        raise typer.Exit(code=1)
    if is_token_expired(token):
        typer.echo("Session expired. Run `dooers auth login`.", err=True)
        store.clear()
        raise typer.Exit(code=1)

    client = CoreClient(base_url=settings.core_url, token=token)
    try:
        me = client.whoami()
    except CoreClientError as e:
        typer.echo(f"whoami failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"Authenticated as {me.email} (user_id={me.user_id})")


@app.command()
def logout(ctx: typer.Context) -> None:
    """Clear local credentials."""
    settings = _settings(ctx)
    store = TokenStore()
    token = store.load()
    if token:
        CoreClient(base_url=settings.core_url, token=token).logout()
    store.clear()
    typer.echo("Logged out.")
```

- [ ] **Step 2: Run smoke tests (verify Typer wiring still works)**

Run: `cd packages/dooers-cli && uv run poe test`
Expected: PASS. The smoke tests check `--help` exits 0 for each subcommand.

- [ ] **Step 3: Manual end-to-end against dev core**

Run:
```bash
export DOOERS_CORE_URL=https://api.dev.dooers.ai
uv run dooers auth login --email <your-email>
# paste OTP code from email
uv run dooers auth whoami
uv run dooers auth logout
uv run dooers auth whoami   # should say "Not authenticated."
```
Expected: login completes, whoami prints email, logout succeeds, post-logout whoami fails cleanly.

- [ ] **Step 4: Commit**

```bash
git add packages/dooers-cli/src/dooers/auth.py
git commit -m "feat(cli): wire dooers auth login/whoami/logout against core"
```

### Task 1.5: M1 milestone commit

- [ ] **Step 1: Tag the milestone**

```bash
git tag -a m1-auth -m "M1: auth subcommand working end-to-end against dev core"
git log --oneline -10
```

- [ ] **Step 2: Demo checklist (verify before declaring M1 done)**

- [ ] `dooers auth login --email <email>` requests OTP, accepts code, saves token to `~/.dooers/token` with 0600.
- [ ] `dooers auth whoami` prints email + user_id from the live core response.
- [ ] `dooers auth logout` clears the token and tells core to remove the session.
- [ ] Running `whoami` after `logout` exits 1 with a helpful message.
- [ ] Re-running `login` while still authenticated says "already authenticated" without making API calls.
- [ ] Token expiry: if `exp` < now (test by manually editing the token), `whoami` exits 1 and clears the token.

---

## Phase M2: Agents CRUD (~1 day)

### Task 2.1: `AgentStore` protocol + `FileShimAgentStore`

**Files:**
- Create: `packages/dooers-cli/src/dooers/agent_store.py`
- Create: `packages/dooers-cli/tests/test_agent_store_file.py`

- [ ] **Step 1: Write the failing test for FileShimAgentStore**

```python
# packages/dooers-cli/tests/test_agent_store_file.py
"""Tests for the local-file shim used when core's /agents endpoints are unavailable."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from dooers.agent_store import FileShimAgentStore
from dooers_protocol.agents import CreateAgentRequest


def test_create_then_list_returns_record(tmp_path: Path) -> None:
    store = FileShimAgentStore(path=tmp_path / "agents.json", owner_user_id="u_1")
    record = store.create(CreateAgentRequest(name="my-agent"))

    assert record.name == "my-agent"
    assert record.owner_user_id == "u_1"
    assert record.agent_id.startswith("ag_")

    listed = store.list()
    assert len(listed) == 1
    assert listed[0].agent_id == record.agent_id


def test_create_assigns_unique_ids(tmp_path: Path) -> None:
    store = FileShimAgentStore(path=tmp_path / "agents.json", owner_user_id="u_1")
    a = store.create(CreateAgentRequest(name="a"))
    b = store.create(CreateAgentRequest(name="b"))
    assert a.agent_id != b.agent_id


def test_get_returns_match(tmp_path: Path) -> None:
    store = FileShimAgentStore(path=tmp_path / "agents.json", owner_user_id="u_1")
    a = store.create(CreateAgentRequest(name="a"))
    fetched = store.get(a.agent_id)
    assert fetched == a


def test_get_missing_raises(tmp_path: Path) -> None:
    store = FileShimAgentStore(path=tmp_path / "agents.json", owner_user_id="u_1")
    with pytest.raises(KeyError):
        store.get("ag_missing")


def test_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "agents.json"
    FileShimAgentStore(path=path, owner_user_id="u_1").create(
        CreateAgentRequest(name="persist")
    )
    listed = FileShimAgentStore(path=path, owner_user_id="u_1").list()
    assert len(listed) == 1
    assert listed[0].name == "persist"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/dooers-cli && uv run pytest tests/test_agent_store_file.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement agent_store.py**

```python
# packages/dooers-cli/src/dooers/agent_store.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_store_file.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-cli/src/dooers/agent_store.py packages/dooers-cli/tests/test_agent_store_file.py
git commit -m "feat(cli): add AgentStore protocol + FileShimAgentStore for unblocked M2 demos"
```

### Task 2.2: `HTTPCoreAgentStore` (shim-compatible)

**Files:**
- Modify: `packages/dooers-cli/src/dooers/agent_store.py`

- [ ] **Step 1: Append HTTPCoreAgentStore to agent_store.py**

Add to the bottom of `packages/dooers-cli/src/dooers/agent_store.py`:

```python
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
```

- [ ] **Step 2: Sanity check imports**

Run: `cd packages/dooers-cli && uv run python -c "from dooers.agent_store import FileShimAgentStore, HTTPCoreAgentStore; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Run all tests**

Run: `uv run poe test`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/dooers-cli/src/dooers/agent_store.py
git commit -m "feat(cli): add HTTPCoreAgentStore for when core endpoints land"
```

### Task 2.3: `dooers.yaml` reader/writer

**Files:**
- Modify: `packages/dooers-cli/src/dooers/config.py`
- Create: `packages/dooers-cli/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/dooers-cli/tests/test_config.py
"""Tests for dooers.yaml read/write."""

from pathlib import Path

import pytest

from dooers.config import MANIFEST_FILENAME, read_manifest, write_manifest
from dooers_protocol import PROTOCOL_VERSION
from dooers_protocol.agents import AgentManifest


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    m = AgentManifest(
        protocol_version=PROTOCOL_VERSION,
        agent_id="ag_8h2k",
        name="test",
        runtime="docker",
        env_required=["FOO", "BAR"],
    )
    write_manifest(m, directory=tmp_path)
    loaded = read_manifest(directory=tmp_path)
    assert loaded == m


def test_read_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_manifest(directory=tmp_path) is None


def test_write_creates_named_file(tmp_path: Path) -> None:
    m = AgentManifest(
        protocol_version=PROTOCOL_VERSION,
        agent_id="ag_x",
        name="x",
    )
    p = write_manifest(m, directory=tmp_path)
    assert p == tmp_path / MANIFEST_FILENAME
    assert p.exists()


def test_read_rejects_unknown_fields(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        "protocol_version: '1'\nagent_id: ag_x\nname: x\nbogus: nope\n"
    )
    with pytest.raises(Exception):  # pydantic ValidationError
        read_manifest(directory=tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/dooers-cli && uv run pytest tests/test_config.py -v`
Expected: FAIL — NotImplementedError on read_manifest/write_manifest.

- [ ] **Step 3: Implement config.py**

```python
# packages/dooers-cli/src/dooers/config.py
"""dooers.yaml reader/writer."""

from pathlib import Path

import yaml

from dooers_protocol.agents import AgentManifest

MANIFEST_FILENAME = "dooers.yaml"


def read_manifest(directory: Path | None = None) -> AgentManifest | None:
    """Read and validate dooers.yaml from `directory` (default: cwd).

    Returns None if missing. Raises pydantic.ValidationError on schema violations.
    """
    target = (directory or Path.cwd()) / MANIFEST_FILENAME
    if not target.exists():
        return None
    raw = yaml.safe_load(target.read_text()) or {}
    return AgentManifest.model_validate(raw)


def write_manifest(manifest: AgentManifest, directory: Path | None = None) -> Path:
    """Write `manifest` to `directory/dooers.yaml`. Returns the path written."""
    target = (directory or Path.cwd()) / MANIFEST_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(manifest.model_dump(mode="json"), sort_keys=False))
    return target
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-cli/src/dooers/config.py packages/dooers-cli/tests/test_config.py
git commit -m "feat(cli): implement dooers.yaml read/write with strict schema validation"
```

### Task 2.4: Wire `dooers agents list / create / show`

**Files:**
- Modify: `packages/dooers-cli/src/dooers/agents.py`

- [ ] **Step 1: Replace agents.py with real implementation**

```python
# packages/dooers-cli/src/dooers/agents.py
"""`dooers agents` subcommands: list, create, show.

By default uses FileShimAgentStore (no core dependency required).
Set DOOERS_USE_CORE_AGENTS=1 to switch to HTTPCoreAgentStore.
"""

import os
from pathlib import Path

import typer

from dooers import config
from dooers.agent_store import (
    AgentStore,
    FileShimAgentStore,
    HTTPCoreAgentStore,
)
from dooers.core_client import CoreClient, CoreClientError
from dooers.settings import Settings
from dooers.token_store import TokenStore, is_token_expired
from dooers_protocol import PROTOCOL_VERSION
from dooers_protocol.agents import AgentManifest, CreateAgentRequest, Runtime

app = typer.Typer(no_args_is_help=True)


def _ensure_authenticated() -> tuple[str, str]:
    """Returns (token, user_id) or exits."""
    store = TokenStore()
    token = store.load()
    if not token or is_token_expired(token):
        typer.echo("Not authenticated. Run `dooers auth login`.", err=True)
        raise typer.Exit(code=1)
    return token, ""  # user_id filled per-call below


def _resolve_store(ctx: typer.Context) -> AgentStore:
    settings: Settings = ctx.obj
    token, _ = _ensure_authenticated()
    if os.environ.get("DOOERS_USE_CORE_AGENTS") == "1":
        return HTTPCoreAgentStore(base_url=settings.core_url, token=token)
    # Shim mode: derive owner_user_id from whoami.
    try:
        me = CoreClient(base_url=settings.core_url, token=token).whoami()
    except CoreClientError as e:
        typer.echo(f"whoami failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    return FileShimAgentStore(owner_user_id=me.user_id)


@app.command(name="list")
def list_agents(ctx: typer.Context) -> None:
    """List the agents owned by the authenticated user."""
    store = _resolve_store(ctx)
    records = store.list()
    if not records:
        typer.echo("No agents yet. Try `dooers agents create --name my-agent`.")
        return
    typer.echo(f"{'ID':<14}{'NAME':<32}{'STATUS':<12}URL")
    for r in records:
        status = "deployed" if r.deployed_url else "draft"
        url = r.deployed_url or "—"
        typer.echo(f"{r.agent_id:<14}{r.name:<32}{status:<12}{url}")


@app.command()
def create(
    ctx: typer.Context,
    name: str = typer.Option(..., help="Display name for the new agent."),
    runtime: Runtime = typer.Option("docker", help="docker | python | node"),
) -> None:
    """Create an agent record and write dooers.yaml in cwd."""
    store = _resolve_store(ctx)
    record = store.create(CreateAgentRequest(name=name, runtime=runtime))
    manifest = AgentManifest(
        protocol_version=PROTOCOL_VERSION,
        agent_id=record.agent_id,
        name=record.name,
        runtime=record.runtime,
        env_required=record.env_required,
    )
    config.write_manifest(manifest, directory=Path.cwd())
    typer.echo(f"Created {record.agent_id}. {config.MANIFEST_FILENAME} written.")


@app.command()
def show(
    ctx: typer.Context,
    agent_id: str = typer.Argument(..., help="Agent ID (e.g. ag_8h2k)."),
) -> None:
    """Show details of a single agent."""
    store = _resolve_store(ctx)
    try:
        r = store.get(agent_id)
    except KeyError:
        typer.echo(f"Agent {agent_id} not found.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"ID:          {r.agent_id}")
    typer.echo(f"Name:        {r.name}")
    typer.echo(f"Runtime:     {r.runtime}")
    typer.echo(f"Env needed:  {', '.join(r.env_required) or '—'}")
    typer.echo(f"Status:      {'deployed' if r.deployed_url else 'draft'}")
    typer.echo(f"URL:         {r.deployed_url or '—'}")
    typer.echo(f"Created:     {r.created_at.isoformat()}")
```

- [ ] **Step 2: Run smoke tests**

Run: `cd packages/dooers-cli && uv run poe test`
Expected: PASS (all 3 subcommand --help tests pass).

- [ ] **Step 3: Manual end-to-end (shim mode)**

Run:
```bash
export DOOERS_CORE_URL=https://api.dev.dooers.ai
uv run dooers auth login --email <your-email>  # if not logged in
uv run dooers agents list                       # expect "No agents yet."
mkdir -p /tmp/my-agent && cd /tmp/my-agent
uv run dooers agents create --name my-agent
cat dooers.yaml                                  # see populated manifest
uv run dooers agents list                        # see the created agent
uv run dooers agents show ag_xxxxxxx
```
Expected: full flow works, `dooers.yaml` has correct shape.

- [ ] **Step 4: Commit**

```bash
git add packages/dooers-cli/src/dooers/agents.py
git commit -m "feat(cli): wire dooers agents list/create/show with shim+HTTP stores"
```

### Task 2.5: M2 milestone commit

- [ ] **Step 1: Tag the milestone**

```bash
git tag -a m2-agents -m "M2: agents list/create/show working via shim (HTTP-ready)"
```

- [ ] **Step 2: Demo checklist**

- [ ] `dooers agents list` shows "No agents yet" on a fresh user.
- [ ] `dooers agents create --name my-agent` creates a record and writes `dooers.yaml`.
- [ ] `cat dooers.yaml` shows `protocol_version`, `agent_id`, `name`, `runtime`, `env_required: []`.
- [ ] `dooers agents list` now shows the created agent in a table.
- [ ] `dooers agents show ag_xxx` shows full details.
- [ ] `dooers agents show ag_nonexistent` exits 1 with "Agent ... not found."

---

## Phase M3: Push round-trip (~2 days)

### Task 3.1: Port `.dooersignore` + archive logic

**Files:**
- Modify: `packages/dooers-cli/src/dooers/ignore.py`
- Create: `packages/dooers-cli/tests/test_ignore.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/dooers-cli/tests/test_ignore.py
"""Tests for .dooersignore parsing + archive creation."""

import tarfile
from pathlib import Path

from dooers.ignore import (
    DEFAULT_IGNORE_PATTERNS,
    is_ignored,
    load_ignore_patterns,
    make_archive,
)


def test_default_patterns_match_node_modules() -> None:
    assert is_ignored("node_modules/foo", DEFAULT_IGNORE_PATTERNS) is True
    assert is_ignored("src/main.py", DEFAULT_IGNORE_PATTERNS) is False


def test_directory_pattern_matches_subpaths() -> None:
    assert is_ignored(".git/HEAD", DEFAULT_IGNORE_PATTERNS) is True
    assert is_ignored(".gitignore", DEFAULT_IGNORE_PATTERNS) is True


def test_glob_pattern_matches() -> None:
    assert is_ignored("app.log", DEFAULT_IGNORE_PATTERNS) is True
    assert is_ignored("dist/main.js", DEFAULT_IGNORE_PATTERNS) is True


def test_load_merges_default_with_dooersignore(tmp_path: Path) -> None:
    (tmp_path / ".dooersignore").write_text("*.secret\nlocal/\n# a comment\n\n")
    patterns = load_ignore_patterns(tmp_path)
    assert "*.secret" in patterns
    assert "local/" in patterns
    # defaults still present
    assert "node_modules/" in patterns
    # comment & blank not added
    assert "# a comment" not in patterns


def test_make_archive_excludes_ignored(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x")
    (tmp_path / "app.log").write_text("noise")

    archive_path = make_archive(directory=str(tmp_path))
    try:
        with tarfile.open(archive_path) as tar:
            names = sorted(tar.getnames())
    finally:
        Path(archive_path).unlink(missing_ok=True)

    assert "src/main.py" in names
    assert not any("node_modules" in n for n in names)
    assert "app.log" not in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/dooers-cli && uv run pytest tests/test_ignore.py -v`
Expected: FAIL — `NotImplementedError`.

- [ ] **Step 3: Implement ignore.py (port from v1)**

```python
# packages/dooers-cli/src/dooers/ignore.py
"""`.dooersignore` parsing + cwd archiving with default ignore patterns.

Ported from v1 cli.py — refactored into pure functions for testability.
Reference: ../../../deploy-service/cli/dooers/cli.py
"""

import fnmatch
import os
import tarfile
import tempfile
from pathlib import Path

DEFAULT_IGNORE_PATTERNS: list[str] = [
    ".git/",
    ".gitignore",
    ".venv/",
    "venv/",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    ".DS_Store",
    "dist/",
    "build/",
    "*.log",
]


def load_ignore_patterns(directory: Path | None = None) -> list[str]:
    """Return default patterns merged with any from `.dooersignore`."""
    directory = directory or Path.cwd()
    patterns = list(DEFAULT_IGNORE_PATTERNS)
    ignore_file = directory / ".dooersignore"
    if ignore_file.exists():
        for raw in ignore_file.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
    return patterns


def is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """Check whether `rel_path` matches any pattern (gitignore-style)."""
    posix_path = rel_path.replace(os.sep, "/")
    for pat in patterns:
        pat = pat.strip()
        if not pat:
            continue
        if pat.endswith("/"):
            prefix = pat[:-1]
            if posix_path == prefix or posix_path.startswith(prefix + "/"):
                return True
        if pat.startswith("/"):
            if fnmatch.fnmatch(posix_path, pat.lstrip("/")):
                return True
        if fnmatch.fnmatch(posix_path, pat):
            return True
        if "/" not in pat and pat in posix_path.split("/"):
            return True
    return False


def make_archive(directory: str = ".") -> str:
    """Create a temp .tar.gz of `directory` respecting ignore patterns.

    Returns the absolute path to the temp archive (caller is responsible
    for cleanup).
    """
    patterns = load_ignore_patterns(Path(directory))
    tmpfd, tmppath = tempfile.mkstemp(suffix=".tar.gz", prefix="dooers-")
    os.close(tmpfd)
    with tarfile.open(tmppath, "w:gz") as tar:
        for root, dirs, files in os.walk(directory):
            relroot = os.path.relpath(root, directory)
            if relroot == ".":
                relroot = ""
            # prune ignored dirs in-place
            pruned = [
                d for d in dirs
                if is_ignored((os.path.join(relroot, d) if relroot else d) + "/", patterns)
            ]
            for d in pruned:
                dirs.remove(d)
            for name in files:
                rel = os.path.join(relroot, name) if relroot else name
                if is_ignored(rel, patterns):
                    continue
                tar.add(os.path.join(root, name), arcname=rel)
    return tmppath
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ignore.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-cli/src/dooers/ignore.py packages/dooers-cli/tests/test_ignore.py
git commit -m "feat(cli): port .dooersignore + make_archive from v1, refactored for tests"
```

### Task 3.2: `PushClient` — multipart upload with synchronous wait

**Files:**
- Modify: `packages/dooers-cli/src/dooers/push_client.py`

- [ ] **Step 1: Replace push_client.py**

```python
# packages/dooers-cli/src/dooers/push_client.py
"""HTTP client for dooers-push (multipart upload + synchronous wait)."""

from pathlib import Path

import httpx

from dooers_protocol.errors import ErrorEnvelope
from dooers_protocol.push import PushResponse


class PushClientError(RuntimeError):
    def __init__(self, message: str, envelope: ErrorEnvelope | None = None) -> None:
        super().__init__(message)
        self.envelope = envelope


class PushClient:
    def __init__(self, base_url: str, token: str, timeout: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._timeout = timeout

    def push(
        self,
        agent_id: str,
        archive_path: Path,
        tag: str = "latest",
        env: str = "prod",
    ) -> PushResponse:
        url = f"{self.base_url}/v1/push/{agent_id}"
        headers = {"Authorization": f"Bearer {self.token}"}
        params = {"tag": tag, "env": env}
        with archive_path.open("rb") as f:
            files = {"archive": (archive_path.name, f, "application/gzip")}
            try:
                r = httpx.post(
                    url,
                    headers=headers,
                    params=params,
                    files=files,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as e:
                raise PushClientError(f"push request failed: {e}") from e

        if r.status_code >= 400:
            try:
                envelope = ErrorEnvelope.model_validate(r.json())
                raise PushClientError(envelope.message, envelope=envelope)
            except (ValueError, TypeError):
                raise PushClientError(f"push failed (HTTP {r.status_code}): {r.text}")

        return PushResponse.model_validate(r.json())
```

- [ ] **Step 2: Sanity check**

Run: `cd packages/dooers-cli && uv run python -c "from dooers.push_client import PushClient; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add packages/dooers-cli/src/dooers/push_client.py
git commit -m "feat(cli): implement PushClient.push() with multipart + synchronous wait"
```

### Task 3.3: Wire `dooers push` command

**Files:**
- Modify: `packages/dooers-cli/src/dooers/push.py`

- [ ] **Step 1: Replace push.py**

```python
# packages/dooers-cli/src/dooers/push.py
"""`dooers push` — archive cwd and POST to dooers-push."""

import os
import sys
import threading
import time
from pathlib import Path

import typer

from dooers import config, ignore
from dooers.push_client import PushClient, PushClientError
from dooers.settings import Settings
from dooers.token_store import TokenStore, is_token_expired


def _spinner(message: str) -> "callable[[], None]":  # type: ignore[type-arg]
    """Background spinner on stderr. Returns a stopper function."""
    stop = threading.Event()
    frames = "|/-\\"

    def run() -> None:
        i = 0
        while not stop.is_set():
            sys.stderr.write(f"\r{message} {frames[i % 4]}")
            sys.stderr.flush()
            i += 1
            time.sleep(0.1)
        sys.stderr.write("\r" + " " * (len(message) + 4) + "\r")
        sys.stderr.flush()

    t = threading.Thread(target=run, daemon=True)
    t.start()

    def cancel() -> None:
        stop.set()
        t.join(timeout=1)

    return cancel


def push(
    ctx: typer.Context,
    agent_id: str | None = typer.Argument(
        None,
        help="Agent ID. If omitted, reads agent_id from ./dooers.yaml.",
    ),
    tag: str = typer.Option("latest", help="Docker image tag."),
    env: str | None = typer.Option(
        None, help="Target environment: prod | stg | dev (overrides --env on the root)."
    ),
) -> None:
    """Push the current directory as a new build of an agent."""
    settings: Settings = ctx.obj
    target_env = env or settings.env

    # Resolve agent_id from arg or manifest.
    if agent_id is None:
        manifest = config.read_manifest()
        if manifest is None:
            typer.echo(
                f"Missing {config.MANIFEST_FILENAME}. Run `dooers agents create` first "
                f"or pass an agent_id explicitly.",
                err=True,
            )
            raise typer.Exit(code=1)
        agent_id = manifest.agent_id

    # Auth.
    token = TokenStore().load()
    if not token or is_token_expired(token):
        typer.echo("Not authenticated. Run `dooers auth login`.", err=True)
        raise typer.Exit(code=1)

    # Archive cwd.
    typer.echo("Archiving …")
    archive_path = Path(ignore.make_archive("."))
    size_mb = archive_path.stat().st_size / (1024 * 1024)
    typer.echo(f"Archive: {archive_path.name} ({size_mb:.1f} MB)")

    # Push.
    client = PushClient(base_url=settings.push_url, token=token)
    cancel_spinner = _spinner(f"Pushing {agent_id} (this can take 3-5 min)")
    try:
        resp = client.push(agent_id=agent_id, archive_path=archive_path, tag=tag, env=target_env)
    except PushClientError as e:
        cancel_spinner()
        typer.echo(f"Push failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    finally:
        cancel_spinner()
        os.unlink(archive_path)

    # Report.
    if resp.status.value == "succeeded" and resp.url:
        typer.echo(f"\nLive at: {resp.url}")
    else:
        typer.echo(f"\nStatus: {resp.status.value}")
        if resp.error:
            typer.echo(f"Error: {resp.error}", err=True)
        if resp.build_id:
            typer.echo(f"Build ID: {resp.build_id}")
        raise typer.Exit(code=1 if resp.status.value == "failed" else 0)
```

- [ ] **Step 2: Run smoke tests**

Run: `cd packages/dooers-cli && uv run poe test`
Expected: PASS — including the `dooers push --help` smoke test.

- [ ] **Step 3: Commit**

```bash
git add packages/dooers-cli/src/dooers/push.py
git commit -m "feat(cli): wire dooers push with archive + multipart upload + synchronous wait"
```

### Task 3.4: `dooers-push` — session verification

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/auth.py`

- [ ] **Step 1: Replace auth.py**

```python
# packages/dooers-push/src/dooers_push/auth.py
"""Session verification — forwards bearer token to core's /session/verify."""

import httpx
from fastapi import HTTPException, Request

from dooers_protocol.auth import AuthSession
from dooers_push.settings import Settings


async def verify_session(request: Request, settings: Settings) -> AuthSession:
    """Verify the incoming Bearer token by forwarding it to core."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth_header[len("Bearer "):]

    verify_url = f"{settings.core_api_url}/api/v1/session/verify"
    try:
        async with httpx.AsyncClient() as client:
            # Try Bearer first, fall back to cookie (v1 server uses this same dance).
            resp = await client.get(
                verify_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=settings.request_timeout,
            )
            if resp.status_code != 200:
                resp = await client.get(
                    verify_url,
                    cookies={"auth": token},
                    timeout=settings.request_timeout,
                )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"core unreachable: {e}") from e

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="invalid session")

    body = resp.json()
    output = body.get("output", body) if isinstance(body, dict) else body
    user_id = output.get("user_id") or output.get("id") or output.get("user", {}).get("id", "")
    email = output.get("email") or output.get("user", {}).get("email", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="core returned no user_id")
    return AuthSession(user_id=user_id, email=email)
```

- [ ] **Step 2: Smoke test**

Run: `cd packages/dooers-push && uv run poe test`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add packages/dooers-push/src/dooers_push/auth.py
git commit -m "feat(push): implement verify_session by forwarding bearer to core"
```

### Task 3.5: `dooers-push` — core_client (get_agent + patch_agent_url)

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/core_client.py`

- [ ] **Step 1: Replace core_client.py**

```python
# packages/dooers-push/src/dooers_push/core_client.py
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
```

- [ ] **Step 2: Commit**

```bash
git add packages/dooers-push/src/dooers_push/core_client.py
git commit -m "feat(push): implement core_client get_agent + patch_agent_url with shim fallback"
```

### Task 3.6: `dooers-push` — GCS storage

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/storage.py`

- [ ] **Step 1: Replace storage.py**

```python
# packages/dooers-push/src/dooers_push/storage.py
"""GCS archive upload. Labels every object with agent_id + owner_user_id."""

import logging
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import UploadFile
from google.cloud import storage

from dooers_push.settings import Settings

logger = logging.getLogger(__name__)


async def upload_archive(
    settings: Settings,
    agent_id: str,
    archive: UploadFile,
    owner_user_id: str,
) -> str:
    """Stream `archive` to gs://{bucket}/agents/{agent_id}/{ts}-{name}. Returns gs:// URI.

    Labels: agent_id, owner_user_id (for billing attribution).
    """
    # Stream upload to a temp file first (Cloud Storage SDK is sync).
    suffix = ""
    if archive.filename and "." in archive.filename:
        suffix = "." + archive.filename.rsplit(".", 1)[-1]
    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        while chunk := await archive.read(1024 * 1024):
            tmp.write(chunk)
        tmp_path = Path(tmp.name)

    ts = int(time.time())
    filename = archive.filename or "archive.tar.gz"
    object_path = f"agents/{agent_id}/{ts}-{filename}"

    client = storage.Client(project=settings.gcp_project_id)
    bucket = client.bucket(settings.bucket_name)
    blob = bucket.blob(object_path)
    blob.metadata = {
        "agent_id": agent_id,
        "owner_user_id": owner_user_id,
        "pushed_at": str(ts),
    }
    blob.upload_from_filename(str(tmp_path))
    tmp_path.unlink(missing_ok=True)

    gcs_uri = f"gs://{settings.bucket_name}/{object_path}"
    logger.info("uploaded archive: %s", gcs_uri)
    return gcs_uri
```

- [ ] **Step 2: Commit**

```bash
git add packages/dooers-push/src/dooers_push/storage.py
git commit -m "feat(push): implement GCS archive upload with billing labels"
```

### Task 3.7: `dooers-push` — Cloud Build trigger

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/gcp/cloudbuild.py`

- [ ] **Step 1: Replace gcp/cloudbuild.py**

```python
# packages/dooers-push/src/dooers_push/gcp/cloudbuild.py
"""Cloud Build trigger + polling. Ports v1 trigger logic and adds polling.

Reference v1: ../../../../../deploy-service/server/main.py
_trigger_cloud_build_with_gcs_source()
"""

import asyncio
import logging

from google.cloud.devtools import cloudbuild_v1

logger = logging.getLogger(__name__)


def _build_deploy_script(
    *,
    service_name: str,
    image: str,
    region: str,
    project_id: str,
    base_env_vars_str: str,
) -> str:
    """Bash script merging env.{env} / .env with base vars, then deploying."""
    return f"""#!/bin/bash
set -e
AGENT_ENV_VARS=""
parse_env_file() {{
    local file="$1"
    if [ -f "$file" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            if [[ -n "$line" && ! "$line" =~ ^# ]]; then
                line=$(echo "$line" | sed 's/#.*$//' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
                if [[ -n "$line" && "$line" =~ = ]]; then
                    if [ -z "$AGENT_ENV_VARS" ]; then
                        AGENT_ENV_VARS="$line"
                    else
                        AGENT_ENV_VARS="$AGENT_ENV_VARS,$line"
                    fi
                fi
            fi
        done < "$file"
    fi
}}
[ -f ".env" ] && parse_env_file ".env"
ALL_ENV_VARS="{base_env_vars_str}"
if [ -n "$AGENT_ENV_VARS" ]; then
    ALL_ENV_VARS="$ALL_ENV_VARS,$AGENT_ENV_VARS"
fi
gcloud run deploy {service_name} \\
    --image={image} --region={region} --platform=managed \\
    --allow-unauthenticated \\
    --service-account=agent-deploy-service@{project_id}.iam.gserviceaccount.com \\
    --set-env-vars="$ALL_ENV_VARS" \\
    --labels=agent_id={service_name} \\
    --cpu=1 --memory=512Mi --min-instances=1 --max-instances=3 \\
    --timeout=300 --cpu-boost"""


def _service_name(agent_id: str, env: str) -> str:
    """Cloud Run service name. Lowercased; Cloud Run is strict."""
    safe = agent_id.lower().replace("_", "-")
    return f"{safe}-{env}"


def trigger_build(
    *,
    project_id: str,
    gcs_uri: str,
    agent_id: str,
    owner_user_id: str,
    region: str,
    artifact_repo: str,
    env: str,
    tag: str,
) -> tuple[str, str]:
    """Create the Cloud Build that does: docker build → push → gcloud run deploy.

    Returns (operation_name, image_uri).
    """
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"invalid gcs uri: {gcs_uri}")
    _, rest = gcs_uri.split("gs://", 1)
    bucket, object_path = rest.split("/", 1)

    service_name = _service_name(agent_id, env)
    image = f"{region}-docker.pkg.dev/{project_id}/{artifact_repo}/{service_name}:{tag}"

    base_env_vars = {
        "GCP_PROJECT_ID": project_id,
        "GCP_REGION": region,
        "ENVIRONMENT": env,
    }
    base_env_vars_str = ",".join(f"{k}={v}" for k, v in base_env_vars.items())
    deploy_script = _build_deploy_script(
        service_name=service_name,
        image=image,
        region=region,
        project_id=project_id,
        base_env_vars_str=base_env_vars_str,
    )

    source = cloudbuild_v1.Source(
        storage_source=cloudbuild_v1.StorageSource(bucket=bucket, object_=object_path)
    )
    service_account = (
        f"projects/{project_id}/serviceAccounts/"
        f"agent-deploy-service@{project_id}.iam.gserviceaccount.com"
    )
    build = cloudbuild_v1.Build(
        source=source,
        steps=[
            cloudbuild_v1.BuildStep(
                name="gcr.io/cloud-builders/docker",
                args=["build", "-t", image, "."],
            ),
            cloudbuild_v1.BuildStep(
                name="gcr.io/cloud-builders/docker",
                args=["push", image],
            ),
            cloudbuild_v1.BuildStep(
                name="gcr.io/cloud-builders/gcloud",
                entrypoint="bash",
                args=["-c", deploy_script],
            ),
        ],
        images=[image],
        service_account=service_account,
        tags=[f"agent-{agent_id}", f"owner-{owner_user_id}"],
        options=cloudbuild_v1.BuildOptions(
            machine_type=cloudbuild_v1.BuildOptions.MachineType.N1_HIGHCPU_8,
            logging="CLOUD_LOGGING_ONLY",
        ),
        timeout={"seconds": 1800},
    )

    client = cloudbuild_v1.services.cloud_build.CloudBuildClient()
    op = client.create_build(project_id=project_id, build=build)
    logger.info("triggered cloud build: %s (image=%s)", op.operation.name, image)
    return op.operation.name, image


async def wait_for_build(operation_name: str, *, timeout_s: int = 540) -> bool:
    """Poll the Cloud Build operation. Returns True on success, False on failure.

    Raises asyncio.TimeoutError beyond `timeout_s`.
    """
    client = cloudbuild_v1.services.cloud_build.CloudBuildAsyncClient()
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        op = await client.get_operation(name=operation_name)
        if op.done:
            return not op.error.code
        await asyncio.sleep(5)
    raise TimeoutError(f"build {operation_name} did not complete within {timeout_s}s")
```

- [ ] **Step 2: Sanity check**

Run: `cd packages/dooers-push && uv run python -c "from dooers_push.gcp.cloudbuild import trigger_build, wait_for_build; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add packages/dooers-push/src/dooers_push/gcp/cloudbuild.py
git commit -m "feat(push): port Cloud Build trigger from v1 and add async polling"
```

### Task 3.8: `dooers-push` — Cloud Run URL describe

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/gcp/cloudrun.py`

- [ ] **Step 1: Replace gcp/cloudrun.py**

```python
# packages/dooers-push/src/dooers_push/gcp/cloudrun.py
"""Cloud Run service URL lookup."""

import logging

from google.cloud import run_v2

logger = logging.getLogger(__name__)


async def describe_service_url(project_id: str, region: str, service_name: str) -> str:
    """Fetch the live URL of a Cloud Run service.

    Returns e.g. 'https://my-agent-prod-xxx.run.app'.
    """
    client = run_v2.ServicesAsyncClient()
    name = f"projects/{project_id}/locations/{region}/services/{service_name}"
    service = await client.get_service(name=name)
    url = service.uri or ""
    if not url:
        raise RuntimeError(f"service {service_name} has no URI yet")
    logger.info("resolved service url: %s -> %s", service_name, url)
    return url
```

- [ ] **Step 2: Commit**

```bash
git add packages/dooers-push/src/dooers_push/gcp/cloudrun.py
git commit -m "feat(push): implement Cloud Run service URL describe"
```

### Task 3.9: `dooers-push` — wire `DeployerStep`

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/pipeline/deployer.py`

- [ ] **Step 1: Replace deployer.py**

```python
# packages/dooers-push/src/dooers_push/pipeline/deployer.py
"""Deployer step — Cloud Build trigger + polling. URL describe + writeback
happen in main.py after the pipeline returns success, because they need
access to settings + core_client beyond what the step interface carries.
"""

import logging

from dooers_protocol.push import BuildStatus
from dooers_push.gcp.cloudbuild import trigger_build, wait_for_build
from dooers_push.pipeline.base import PipelineContext, PipelineStep, StepResult
from dooers_push.settings import Settings

logger = logging.getLogger(__name__)


class DeployerStep(PipelineStep):
    name = "deployer"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run(self, ctx: PipelineContext) -> StepResult:
        try:
            op_name, image = trigger_build(
                project_id=self.settings.gcp_project_id,
                gcs_uri=ctx.gcs_uri,
                agent_id=ctx.agent.agent_id,
                owner_user_id=ctx.user.user_id,
                region=self.settings.gcp_region,
                artifact_repo=self.settings.artifact_repo,
                env=ctx.env,
                tag=ctx.tag,
            )
            ctx.build_id = op_name
            ctx.image = image
            logger.info("deployer: build %s started", op_name)
            success = await wait_for_build(op_name)
            if not success:
                return StepResult(status=BuildStatus.failed, error="Cloud Build reported failure")
            return StepResult(status=BuildStatus.succeeded)
        except TimeoutError as e:
            return StepResult(status=BuildStatus.failed, error=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("deployer crashed")
            return StepResult(status=BuildStatus.failed, error=f"deployer error: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add packages/dooers-push/src/dooers_push/pipeline/deployer.py
git commit -m "feat(push): wire DeployerStep with Cloud Build trigger + polling"
```

### Task 3.10: `dooers-push` — implement `POST /v1/push/{agent_id}`

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/main.py`

- [ ] **Step 1: Replace main.py**

```python
# packages/dooers-push/src/dooers_push/main.py
"""FastAPI routes. Skinny — logic lives in pipeline/ and gcp/."""

import logging
import uuid

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile

from dooers_protocol.push import BuildStatus, PushResponse
from dooers_push import storage
from dooers_push.auth import verify_session
from dooers_push.core_client import CoreClient
from dooers_push.gcp.cloudbuild import _service_name
from dooers_push.gcp.cloudrun import describe_service_url
from dooers_push.pipeline import (
    AuditorStep,
    DeployerStep,
    PipelineContext,
    ProvisionerStep,
    run_pipeline,
)
from dooers_push.settings import Settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="dooers-push",
    version="0.1.0",
    description="Owns the push pipeline backing `dooers push`.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/push/{agent_id}")
async def push(
    agent_id: str,
    request: Request,
    archive: UploadFile = File(...),
    tag: str = Query("latest"),
    env: str = Query("prod"),
) -> PushResponse:
    """Run the synchronous push pipeline for `agent_id`."""
    correlation_id = str(uuid.uuid4())
    settings = Settings.from_env()
    logger.info("push start: agent_id=%s correlation_id=%s", agent_id, correlation_id)

    if not archive.filename or not archive.filename.endswith((".tar.gz", ".tgz", ".zip")):
        raise HTTPException(status_code=400, detail="archive must be .tar.gz/.tgz/.zip")

    session = await verify_session(request, settings)
    token = request.headers["Authorization"][len("Bearer "):]
    core = CoreClient(base_url=settings.core_api_url, token=token)
    agent = await core.get_agent(agent_id, fallback_session=session)
    if agent.owner_user_id != session.user_id:
        raise HTTPException(status_code=403, detail=f"you do not own {agent_id}")

    gcs_uri = await storage.upload_archive(
        settings, agent_id, archive, owner_user_id=session.user_id
    )

    ctx = PipelineContext(
        agent=agent, user=session, gcs_uri=gcs_uri, tag=tag, env=env,
    )
    result = await run_pipeline(
        ctx, [AuditorStep(), ProvisionerStep(), DeployerStep(settings)]
    )

    if result.status == BuildStatus.failed:
        return PushResponse(
            agent_id=agent_id,
            build_id=ctx.build_id or "",
            image=ctx.image or "",
            status=BuildStatus.failed,
            error=result.error,
        )

    # Build succeeded → describe URL → write back to core.
    service_name = _service_name(agent_id, env)
    url = await describe_service_url(settings.gcp_project_id, settings.gcp_region, service_name)
    await core.patch_agent_url(agent_id, url)

    return PushResponse(
        agent_id=agent_id,
        build_id=ctx.build_id or "",
        image=ctx.image or "",
        status=BuildStatus.succeeded,
        url=url,
    )
```

- [ ] **Step 2: Run smoke tests**

Run: `cd packages/dooers-push && uv run poe test`
Expected: PASS (`/health` works, pipeline stubs still pass).

- [ ] **Step 3: Commit**

```bash
git add packages/dooers-push/src/dooers_push/main.py
git commit -m "feat(push): implement POST /v1/push/{agent_id} end-to-end"
```

### Task 3.11: Deploy `dooers-push` to dev Cloud Run

**Files:**
- (no code changes; infrastructure step)

- [ ] **Step 1: Build the image locally to verify Dockerfile**

Run from `packages/dooers-push/`:
```bash
docker build -t dooers-push:local -f Dockerfile ..
```
Expected: image builds. (Build context is `..` so that the `COPY ../dooers-protocol` in the Dockerfile resolves — confirm and adjust if needed.)

- [ ] **Step 2: Push to dev project & deploy**

Run (replace `<PROJECT_ID>` with the actual dev project ID, e.g. taken from the v1 deploy-service env files):
```bash
export PROJECT_ID=<PROJECT_ID>
export REGION=us-central1
gcloud auth configure-docker $REGION-docker.pkg.dev
docker tag dooers-push:local \
  $REGION-docker.pkg.dev/$PROJECT_ID/agents/dooers-push:dev
docker push $REGION-docker.pkg.dev/$PROJECT_ID/agents/dooers-push:dev

gcloud run deploy dooers-push-dev \
  --image=$REGION-docker.pkg.dev/$PROJECT_ID/agents/dooers-push:dev \
  --region=$REGION --platform=managed --allow-unauthenticated \
  --service-account=agent-deploy-service@$PROJECT_ID.iam.gserviceaccount.com \
  --set-env-vars="GCP_PROJECT_ID=$PROJECT_ID,GCP_REGION=$REGION,BUCKET_NAME=dooers-agents-deploy,ARTIFACT_REPO=agents,CORE_API_URL=https://api.dev.dooers.ai,ENVIRONMENT=dev,REQUEST_TIMEOUT=10" \
  --cpu=1 --memory=1Gi --timeout=600
```
Expected: Cloud Run prints a URL, e.g. `https://dooers-push-dev-xxx.a.run.app`.

- [ ] **Step 3: Smoke-test the deployed service**

Run:
```bash
curl https://dooers-push-dev-xxx.a.run.app/health
```
Expected: `{"status":"ok"}`.

- [ ] **Step 4: Note the URL for client config**

The CLI's `--push-url` / `DOOERS_PUSH_URL` should point at this. Copy the URL into a local note or shell rc.

- [ ] **Step 5: No commit (infra step)**

### Task 3.12: End-to-end demo push

**Files:**
- (no code changes; manual verification)

- [ ] **Step 1: Prepare a tiny demo agent locally**

Run:
```bash
mkdir -p /tmp/demo-agent && cd /tmp/demo-agent
cat > Dockerfile <<'EOF'
FROM python:3.12-slim
RUN pip install --no-cache-dir fastapi uvicorn
COPY main.py .
ENV PORT=8080
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
EOF
cat > main.py <<'EOF'
from fastapi import FastAPI
app = FastAPI()
@app.get("/")
def root(): return {"hello": "from-my-agent"}
EOF
```

- [ ] **Step 2: Configure shell**

```bash
export DOOERS_CORE_URL=https://api.dev.dooers.ai
export DOOERS_PUSH_URL=https://dooers-push-dev-xxx.a.run.app
export DOOERS_ENV=dev
```

- [ ] **Step 3: Run the full flow**

```bash
cd <repo-root>/packages/dooers-cli
uv run dooers auth login --email <your-email>   # if not already
cd /tmp/demo-agent
uv run dooers agents create --name demo-agent
cat dooers.yaml                                 # contains agent_id
uv run dooers push                              # blocks ~3-5 min
```

Expected: spinner runs for 3–5 min, final output:
```
Live at: https://<service-name>-dev-xxx.a.run.app
```

- [ ] **Step 4: Verify the deployed agent responds**

```bash
curl https://<service-name>-dev-xxx.a.run.app/
```
Expected: `{"hello":"from-my-agent"}`.

- [ ] **Step 5: No commit (verification step)**

### Task 3.13: M3 milestone commit

- [ ] **Step 1: Tag the milestone**

```bash
git tag -a m3-push -m "M3: dooers push round-trip working end-to-end on dev"
```

- [ ] **Step 2: Demo checklist**

- [ ] `dooers push` with no args reads `dooers.yaml` and uses its agent_id.
- [ ] `dooers push <agent_id>` overrides the manifest.
- [ ] Archive respects `.dooersignore` (verify with a `.dooersignore` that excludes a known file).
- [ ] CLI shows a spinner during the 3-5 min wait.
- [ ] On success, the live Cloud Run URL is printed and accessible.
- [ ] On Cloud Build failure, the CLI exits 1 and prints the build_id.
- [ ] Pushing without auth fails cleanly: "Not authenticated. Run `dooers auth login`."

---

## Phase M4: Visible auditor (~0.5 day)

### Task 4.1: Auditor — scan archive for endpoints + imports

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/pipeline/auditor.py`

- [ ] **Step 1: Replace auditor.py**

```python
# packages/dooers-push/src/dooers_push/pipeline/auditor.py
"""Auditor step — POC version that produces a visible AuditReport.

It scans the uploaded archive (downloaded from GCS) for:
- top-level imports of Python source files
- HTTP endpoint route decorators (FastAPI/Flask)

It does NOT block anything — `passed=True` always. The visible output is
purely for stakeholder demos: "look, the auditor saw your code."
Replace this with real maliciousness rules in a future spec.
"""

import io
import logging
import re
import tarfile
import zipfile

from google.cloud import storage

from dooers_protocol.audit import AuditFinding, AuditReport, InfraManifest
from dooers_protocol.push import BuildStatus
from dooers_push.pipeline.base import PipelineContext, PipelineStep, StepResult

logger = logging.getLogger(__name__)

_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))", re.MULTILINE)
_ROUTE_RE = re.compile(
    r"@\w+\.(?:get|post|put|patch|delete|route)\s*\(\s*['\"]([^'\"]+)['\"]"
)


def _read_text_member(data: bytes, name: str) -> str:
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _scan_text(text: str) -> tuple[set[str], list[str]]:
    imports = set()
    for m in _IMPORT_RE.finditer(text):
        mod = m.group(1) or m.group(2)
        if mod:
            imports.add(mod.split(".")[0])
    endpoints = _ROUTE_RE.findall(text)
    return imports, endpoints


def _scan_archive_bytes(blob_bytes: bytes, archive_name: str) -> tuple[set[str], list[str]]:
    imports: set[str] = set()
    endpoints: list[str] = []
    if archive_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(blob_bytes)) as zf:
            for info in zf.infolist():
                if not info.filename.endswith(".py"):
                    continue
                text = _read_text_member(zf.read(info), info.filename)
                imp, eps = _scan_text(text)
                imports |= imp
                endpoints.extend(eps)
    else:
        with tarfile.open(fileobj=io.BytesIO(blob_bytes), mode="r:*") as tar:
            for member in tar.getmembers():
                if not (member.isfile() and member.name.endswith(".py")):
                    continue
                f = tar.extractfile(member)
                if not f:
                    continue
                text = _read_text_member(f.read(), member.name)
                imp, eps = _scan_text(text)
                imports |= imp
                endpoints.extend(eps)
    return imports, endpoints


class AuditorStep(PipelineStep):
    name = "auditor"

    async def run(self, ctx: PipelineContext) -> StepResult:
        # Download archive from GCS (small enough — POC stance).
        if not ctx.gcs_uri.startswith("gs://"):
            ctx.audit_report = AuditReport(passed=True)
            return StepResult(status=BuildStatus.queued)
        _, rest = ctx.gcs_uri.split("gs://", 1)
        bucket, object_path = rest.split("/", 1)
        client = storage.Client()
        blob_bytes = client.bucket(bucket).blob(object_path).download_as_bytes()

        try:
            imports, endpoints = _scan_archive_bytes(blob_bytes, ctx.gcs_uri)
        except Exception as e:  # noqa: BLE001
            logger.warning("auditor scan failed: %s", e)
            imports, endpoints = set(), []

        findings = [
            AuditFinding(severity="info", category="endpoint", message=f"detected endpoint: {ep}")
            for ep in sorted(set(endpoints))
        ]
        ctx.audit_report = AuditReport(
            passed=True,
            findings=findings,
            required_infra=InfraManifest(
                detected_endpoints=sorted(set(endpoints)),
            ),
        )
        logger.info(
            "auditor: agent=%s endpoints=%d imports=%d (top-level: %s)",
            ctx.agent.agent_id, len(endpoints), len(imports),
            ", ".join(sorted(imports)[:8]) or "—",
        )
        return StepResult(status=BuildStatus.queued)
```

- [ ] **Step 2: Smoke test**

Run: `cd packages/dooers-push && uv run poe test`
Expected: PASS — the existing pipeline stub test now exercises the real (non-blocking) auditor.

- [ ] **Step 3: Commit**

```bash
git add packages/dooers-push/src/dooers_push/pipeline/auditor.py
git commit -m "feat(push): make auditor visible (scans archive for endpoints + imports)"
```

### Task 4.2: Surface audit report in push response + CLI output

**Files:**
- Modify: `packages/dooers-protocol/src/dooers_protocol/push.py`
- Modify: `packages/dooers-push/src/dooers_push/main.py`
- Modify: `packages/dooers-cli/src/dooers/push.py`

- [ ] **Step 1: Add `audit` to `PushResponse`**

Replace `packages/dooers-protocol/src/dooers_protocol/push.py`:

```python
"""Push request/response shapes and build status enum."""

from enum import Enum

from pydantic import BaseModel

from dooers_protocol.audit import AuditReport


class BuildStatus(str, Enum):
    queued = "queued"
    building = "building"
    deploying = "deploying"
    succeeded = "succeeded"
    failed = "failed"


class PushRequest(BaseModel):
    agent_id: str
    tag: str = "latest"
    env: str = "prod"


class PushResponse(BaseModel):
    agent_id: str
    build_id: str
    image: str
    status: BuildStatus
    url: str | None = None
    error: str | None = None
    audit: AuditReport | None = None
```

- [ ] **Step 2: Populate `audit` in `dooers-push/main.py`**

In the two `return PushResponse(...)` calls in `packages/dooers-push/src/dooers_push/main.py`, add `audit=ctx.audit_report` to both.

Locate:
```python
return PushResponse(
    agent_id=agent_id,
    build_id=ctx.build_id or "",
    image=ctx.image or "",
    status=BuildStatus.failed,
    error=result.error,
)
```
Change to:
```python
return PushResponse(
    agent_id=agent_id,
    build_id=ctx.build_id or "",
    image=ctx.image or "",
    status=BuildStatus.failed,
    error=result.error,
    audit=ctx.audit_report,
)
```

And the success return:
```python
return PushResponse(
    agent_id=agent_id,
    build_id=ctx.build_id or "",
    image=ctx.image or "",
    status=BuildStatus.succeeded,
    url=url,
)
```
Change to:
```python
return PushResponse(
    agent_id=agent_id,
    build_id=ctx.build_id or "",
    image=ctx.image or "",
    status=BuildStatus.succeeded,
    url=url,
    audit=ctx.audit_report,
)
```

- [ ] **Step 3: Print audit summary in `dooers-cli/push.py`**

Find the success block in `packages/dooers-cli/src/dooers/push.py`:
```python
if resp.status.value == "succeeded" and resp.url:
    typer.echo(f"\nLive at: {resp.url}")
```
Replace with:
```python
if resp.audit and resp.audit.required_infra.detected_endpoints:
    endpoints = resp.audit.required_infra.detected_endpoints
    typer.echo(f"\nAudit: {len(endpoints)} endpoint(s) detected:")
    for ep in endpoints[:10]:
        typer.echo(f"  - {ep}")
    if len(endpoints) > 10:
        typer.echo(f"  … and {len(endpoints) - 10} more")
elif resp.audit:
    typer.echo("\nAudit: 0 endpoints detected.")
if resp.status.value == "succeeded" and resp.url:
    typer.echo(f"\nLive at: {resp.url}")
```

- [ ] **Step 4: Smoke tests**

Run:
```bash
cd packages/dooers-protocol && uv run poe test
cd packages/dooers-cli && uv run poe test
cd packages/dooers-push && uv run poe test
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-protocol/src/dooers_protocol/push.py \
        packages/dooers-push/src/dooers_push/main.py \
        packages/dooers-cli/src/dooers/push.py
git commit -m "feat: surface auditor report in PushResponse and print summary in CLI"
```

### Task 4.3: Redeploy dooers-push and verify

- [ ] **Step 1: Rebuild and redeploy**

Run from `packages/dooers-push/`:
```bash
docker build -t $REGION-docker.pkg.dev/$PROJECT_ID/agents/dooers-push:dev -f Dockerfile ..
docker push $REGION-docker.pkg.dev/$PROJECT_ID/agents/dooers-push:dev
gcloud run deploy dooers-push-dev \
  --image=$REGION-docker.pkg.dev/$PROJECT_ID/agents/dooers-push:dev \
  --region=$REGION
```

- [ ] **Step 2: Re-run the demo from Task 3.12**

```bash
cd /tmp/demo-agent
uv run dooers push
```
Expected output now includes:
```
Audit: 1 endpoint(s) detected:
  - /
Live at: https://demo-agent-dev-xxx.a.run.app
```

- [ ] **Step 3: Tag the milestone**

```bash
git tag -a m4-audit -m "M4: auditor visible in push output"
```

- [ ] **Step 4: Demo checklist**

- [ ] Push a single-endpoint agent → CLI prints exactly 1 endpoint detected.
- [ ] Push an agent with no `.py` files → CLI prints "0 endpoints detected".
- [ ] Push an agent with >10 endpoints → CLI prints first 10 + "and N more".
- [ ] Audit information appears even on failed builds (because it runs before the build).

---

## Self-Review

**Spec coverage check** — every spec requirement maps to at least one task:

| Spec §  | Requirement | Tasks |
|---|---|---|
| 3 | CLI talks to two services only (boundary) | 1.3, 2.4, 3.2 |
| 3 | `dooers-push` does not host `/agents` CRUD | enforced — no task adds it |
| 3 | Pipeline = 3 steps behind PipelineStep ABC | scaffold + 4.1 |
| 4 | Three-package monorepo, independent pyproject.toml | scaffold (already committed) |
| 5.1 | `dooers-protocol` models (auth, agents, push, audit, errors) | scaffold + 4.2 (PushResponse.audit) |
| 5.2 | CLI subcommand structure (auth/agents/push) | 1.4, 2.4, 3.3 |
| 5.2 | `~/.dooers/token` with 0600 | 1.2 |
| 5.3 | `dooers-push` endpoint `POST /v1/push/{agent_id}` | 3.10 |
| 6 | Global config flag/env/default precedence | 1.1 |
| 7 | `dooers.yaml` strict schema validation | 2.3 |
| 8 | Data flow steps 1–13 | 3.1–3.10, 4.1, 4.2 |
| 9 | Error mapping (401 / 404 / 403 / 500 / 504) | 1.3, 3.2, 3.4, 3.10 |
| 10 | Smoke tests + manual E2E | every milestone task block |
| 11 | Billing-ready labels on GCS / Cloud Build / Cloud Run | 3.6 (GCS metadata), 3.7 (CB tags + CR labels) |
| 13.1 | Agents CRUD blocker (core endpoints) | 2.1+2.2 shim/HTTP split unblocks |

**Placeholder scan:** no TBDs, no "TODO", no "implement later", no "similar to". Every code step shows complete code. ✓

**Type consistency check:**
- `AgentRecord` used identically in shim + HTTP stores + push pipeline.
- `PushResponse` extended in 4.2 (adds `audit`); CLI handles backward-compat by checking `resp.audit is not None`.
- `_service_name()` used in both `cloudbuild.py` and `main.py` — imported from one place.
- `Settings` (CLI) and `Settings` (push) are distinct classes in distinct packages — no name clash because of module path.

**Open dependencies the engineer should resolve before starting:**
1. Confirm dev GCP `PROJECT_ID`, `BUCKET_NAME`, and `agent-deploy-service@…` service account exist (carry-over from v1 deploy-service).
2. Confirm core's `/api/v1/session/*` endpoints respond on `https://api.dev.dooers.ai` (already used by v1 CLI).
3. Decide `DOOERS_USE_CORE_AGENTS` value: leave unset (shim) for M2 demo; set to `"1"` once core's `/api/v1/agents` endpoints land.

---

## Notes

- **Frequent commits:** every task ends in a commit. ~30 commits across the POC. Resist squashing — the audit trail is valuable.
- **TDD where free:** pure-function modules (`token_store`, `agent_store` shim, `config`, `ignore`) get test-first treatment. HTTP / GCP integrations are write-then-smoke-test + manual E2E per the user's "fast development without stress test units" direction.
- **Demos at every milestone:** the demo checklist at the end of M1/M2/M3/M4 is what you show stakeholders. If a checkbox can't be ticked, that milestone isn't done.
- **Sibling repo reference:** the v1 deploy-service repo at `../deploy-service/` is the source of truth for Cloud Build trigger logic, env-file parsing, archive ignore patterns, and Cloud Run deploy flags. When in doubt about how the v1 system behaved, read it before guessing.
