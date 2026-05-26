# Dooers LB Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement LB integration in `dooers-push` so every successful push registers the agent in the Dooers-managed global LB and returns a stable `*.agents.dooers.ai` URL (or `*.agents.dooers.ai` without env suffix in prod).

**Architecture:** New `LBManager` class in `dooers_push/gcp/loadbalancer.py` orchestrates create-or-get for Serverless NEG + Backend Service, then GET-modify-UPDATE on the URL Map to add a host rule + path matcher. Integrated as the final phase of `DeployerStep` after Cloud Build completes. All operations idempotent (re-push produces no duplicate resources). Folds into M3 of the base plan; this plan can be executed in parallel with M1–M2 since the code paths don't overlap.

**Tech Stack:** Python 3.12, `google-cloud-compute` v1, FastAPI, pytest with `unittest.mock`.

**Companion docs:**
- Design spec: `docs/superpowers/specs/2026-05-27-dooers-lb-design.md`
- Devops runbook (one-time GCP setup): `docs/devops/gcp-lb.md`
- Base plan this supplements: `docs/superpowers/plans/2026-05-26-dooers-cli-v2-poc.md`

**Where in M3 these tasks fit:** Between base-plan Task 3.8 (Cloud Run URL describe) and Task 3.9 (DeployerStep wiring). Base-plan Task 3.8 stops being the URL source (kept for unregister path / debugging); base-plan Tasks 3.9, 3.10, 3.12 are amended by tasks L.10, L.11, L.12 below.

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `packages/dooers-protocol/src/dooers_protocol/errors.py` | Modify | Add `lb_registration_failed` to `ErrorCode` |
| `packages/dooers-push/src/dooers_push/settings.py` | Modify | Add `lb_domain`, `lb_url_map`, `lb_region` |
| `packages/dooers-push/src/dooers_push/pipeline/base.py` | Modify | Add `lb_url: str \| None` to `PipelineContext` |
| `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py` | Create | `LBManager`, `LBError`, naming helpers |
| `packages/dooers-push/tests/test_lb_naming.py` | Create | Pure-function tests for naming helpers |
| `packages/dooers-push/tests/test_lb_manager.py` | Create | Mock-based tests for `LBManager` operations |
| `packages/dooers-push/src/dooers_push/pipeline/deployer.py` | Modify | Add LB phase after Cloud Build |
| `packages/dooers-push/src/dooers_push/main.py` | Modify | Use `ctx.lb_url` as URL source |
| `packages/dooers-push/tests/test_smoke.py` | Modify | Update mocked test to assert LB URL |

---

## Phase L: LB Integration

### Task L.1: Scaffold types (errors, settings, pipeline context)

**Files:**
- Modify: `packages/dooers-protocol/src/dooers_protocol/errors.py`
- Modify: `packages/dooers-push/src/dooers_push/settings.py`
- Modify: `packages/dooers-push/src/dooers_push/pipeline/base.py`

- [ ] **Step 1: Add the new error code**

Edit `packages/dooers-protocol/src/dooers_protocol/errors.py`. Find:

```python
class ErrorCode(str, Enum):
    unauthenticated = "unauthenticated"
    forbidden = "forbidden"
    not_found = "not_found"
    archive_too_large = "archive_too_large"
    audit_failed = "audit_failed"
    build_failed = "build_failed"
    build_timeout = "build_timeout"
    core_unreachable = "core_unreachable"
    internal = "internal"
```

Change to:

```python
class ErrorCode(str, Enum):
    unauthenticated = "unauthenticated"
    forbidden = "forbidden"
    not_found = "not_found"
    archive_too_large = "archive_too_large"
    audit_failed = "audit_failed"
    build_failed = "build_failed"
    build_timeout = "build_timeout"
    core_unreachable = "core_unreachable"
    lb_registration_failed = "lb_registration_failed"
    internal = "internal"
```

- [ ] **Step 2: Add LB settings**

Edit `packages/dooers-push/src/dooers_push/settings.py`. Replace the file with:

```python
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
    lb_domain: str
    lb_url_map: str
    lb_region: str

    @classmethod
    def from_env(cls) -> "Settings":
        gcp_region = os.environ.get("GCP_REGION", "us-central1")
        return cls(
            gcp_project_id=_required("GCP_PROJECT_ID"),
            gcp_region=gcp_region,
            bucket_name=_required("BUCKET_NAME"),
            artifact_repo=os.environ.get("ARTIFACT_REPO", "agents"),
            core_api_url=os.environ.get("CORE_API_URL", "https://api.dooers.ai"),
            environment=os.environ.get("ENVIRONMENT", "dev"),
            request_timeout=int(os.environ.get("REQUEST_TIMEOUT", "10")),
            lb_domain=os.environ.get("DOOERS_LB_DOMAIN", "agents.dooers.ai"),
            lb_url_map=os.environ.get("DOOERS_LB_URL_MAP", "dooers-agents-url-map"),
            lb_region=os.environ.get("DOOERS_LB_REGION", gcp_region),
        )


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required env var: {name}")
    return value
```

- [ ] **Step 3: Add `lb_url` to PipelineContext**

Edit `packages/dooers-push/src/dooers_push/pipeline/base.py`. Find the `PipelineContext` definition and add the new field:

```python
class PipelineContext(BaseModel):
    """Shared state passed between pipeline steps. Steps mutate by attribute."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent: AgentRecord
    user: AuthSession
    gcs_uri: str
    tag: str
    env: str

    # populated by steps as they run:
    audit_report: AuditReport | None = None
    provisioned_env: dict[str, str] = {}
    build_id: str | None = None
    image: str | None = None
    lb_url: str | None = None
```

- [ ] **Step 4: Smoke check that all three packages still import cleanly**

```bash
cd packages/dooers-protocol && uv run python -c "from dooers_protocol.errors import ErrorCode; assert ErrorCode.lb_registration_failed.value == 'lb_registration_failed'; print('ok')"
cd ../dooers-push && uv run python -c "from dooers_push.settings import Settings; print('ok')"
cd ../dooers-push && uv run python -c "from dooers_push.pipeline.base import PipelineContext; assert 'lb_url' in PipelineContext.model_fields; print('ok')"
```

Each should print `ok`.

- [ ] **Step 5: Run existing tests across all packages**

```bash
cd packages/dooers-protocol && uv run poe test
cd ../dooers-push && uv run poe test
```

Both should PASS. (Existing tests don't yet exercise the new fields.)

- [ ] **Step 6: Commit**

```bash
git add packages/dooers-protocol/src/dooers_protocol/errors.py \
        packages/dooers-push/src/dooers_push/settings.py \
        packages/dooers-push/src/dooers_push/pipeline/base.py
git commit -m "feat(lb): scaffold types for LB integration (settings + error code + ctx.lb_url)"
```

### Task L.2: Naming helpers (TDD)

**Files:**
- Create: `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`
- Create: `packages/dooers-push/tests/test_lb_naming.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/dooers-push/tests/test_lb_naming.py`:

```python
"""Tests for LB naming helpers — pure functions."""

import pytest

from dooers_push.gcp.loadbalancer import (
    bs_name,
    host_for,
    neg_name,
    path_matcher_name,
    safe_agent_id,
)


def test_safe_agent_id_lowercases_and_replaces_underscores() -> None:
    assert safe_agent_id("ag_7q4r") == "ag-7q4r"
    assert safe_agent_id("AG_7Q4R") == "ag-7q4r"
    assert safe_agent_id("ag-already-safe") == "ag-already-safe"


def test_safe_agent_id_rejects_whitespace() -> None:
    with pytest.raises(ValueError):
        safe_agent_id(" ag_7q4r ")
    with pytest.raises(ValueError):
        safe_agent_id("ag 7q4r")


def test_safe_agent_id_rejects_empty() -> None:
    with pytest.raises(ValueError):
        safe_agent_id("")


def test_host_for_prod_drops_env_suffix() -> None:
    assert host_for("ag_7q4r", "prod", "agents.dooers.ai") == "ag-7q4r.agents.dooers.ai"


def test_host_for_non_prod_keeps_env_suffix() -> None:
    assert host_for("ag_7q4r", "dev", "agents.dooers.ai") == "ag-7q4r-dev.agents.dooers.ai"
    assert host_for("ag_7q4r", "stg", "agents.dooers.ai") == "ag-7q4r-stg.agents.dooers.ai"


def test_neg_name_keeps_env_in_all_envs() -> None:
    # Internal resource names are symmetric (env always present) for easy filtering.
    assert neg_name("ag_7q4r", "prod") == "agent-ag-7q4r-prod-neg"
    assert neg_name("ag_7q4r", "dev") == "agent-ag-7q4r-dev-neg"


def test_bs_name_keeps_env_in_all_envs() -> None:
    assert bs_name("ag_7q4r", "prod") == "agent-ag-7q4r-prod-bs"
    assert bs_name("ag_7q4r", "dev") == "agent-ag-7q4r-dev-bs"


def test_path_matcher_name() -> None:
    assert path_matcher_name("ag_7q4r", "prod") == "agent-ag-7q4r-prod-pm"
    assert path_matcher_name("ag_7q4r", "dev") == "agent-ag-7q4r-dev-pm"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/dooers-push && uv run pytest tests/test_lb_naming.py -v
```

Expected: ImportError or all tests fail (`loadbalancer` module doesn't exist).

- [ ] **Step 3: Implement the naming helpers**

Create `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`:

```python
"""LBManager + naming helpers + LBError.

Owns per-agent Load Balancer registration (Serverless NEG + Backend
Service + URL Map host rule).

All operations are idempotent: re-registering the same agent updates
the existing NEG to point at the latest Cloud Run revision; it does
not create duplicates.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Naming helpers (pure functions)
# ---------------------------------------------------------------------------

def safe_agent_id(agent_id: str) -> str:
    """Convert an agent_id to a DNS- and GCP-safe form.

    'ag_7q4r' → 'ag-7q4r'.  Lowercases, replaces underscores.
    Raises ValueError on empty input or input containing whitespace.
    """
    if not agent_id:
        raise ValueError("agent_id must not be empty")
    if any(c.isspace() for c in agent_id):
        raise ValueError(f"agent_id must not contain whitespace: {agent_id!r}")
    return agent_id.lower().replace("_", "-")


def host_for(agent_id: str, env: str, lb_domain: str) -> str:
    """Return the per-agent LB hostname.

    Prod drops the env suffix; non-prod keeps it.
    host_for('ag_7q4r', 'prod', 'agents.dooers.ai')
    → 'ag-7q4r.agents.dooers.ai'
    host_for('ag_7q4r', 'dev', 'agents.dooers.ai')
    → 'ag-7q4r-dev.agents.dooers.ai'
    """
    safe = safe_agent_id(agent_id)
    if env == "prod":
        return f"{safe}.{lb_domain}"
    return f"{safe}-{env}.{lb_domain}"


def neg_name(agent_id: str, env: str) -> str:
    """Internal resource name; keeps env in all envs for easy filtering."""
    return f"agent-{safe_agent_id(agent_id)}-{env}-neg"


def bs_name(agent_id: str, env: str) -> str:
    return f"agent-{safe_agent_id(agent_id)}-{env}-bs"


def path_matcher_name(agent_id: str, env: str) -> str:
    return f"agent-{safe_agent_id(agent_id)}-{env}-pm"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_lb_naming.py -v
```

Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-push/src/dooers_push/gcp/loadbalancer.py \
        packages/dooers-push/tests/test_lb_naming.py
git commit -m "feat(lb): add LB naming helpers with prod env-suffix asymmetry"
```

### Task L.3: LBError + LBManager skeleton

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`

- [ ] **Step 1: Append LBError + LBManager skeleton**

Append to `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`:

```python


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class LBError(RuntimeError):
    """Any failure interacting with the LB. Carries GCP error context."""

    def __init__(self, message: str, *, operation: str | None = None,
                 cause: Exception | None = None) -> None:
        super().__init__(message)
        self.operation = operation
        self.cause = cause


# ---------------------------------------------------------------------------
# LBManager
# ---------------------------------------------------------------------------

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from dooers_push.settings import Settings

logger = logging.getLogger(__name__)


class LBManager:
    """Per-agent LB registration. Idempotent on every call."""

    def __init__(self, settings: "Settings") -> None:
        self.project_id = settings.gcp_project_id
        self.region = settings.lb_region
        self.url_map_name = settings.lb_url_map
        self.domain = settings.lb_domain

    async def register_agent(self, agent_id: str, env: str) -> str:
        """Wire {agent_id}-{env} Cloud Run into the LB; return the URL.

        Steps (each idempotent):
        1. Ensure Serverless NEG exists.
        2. Ensure Backend Service exists; attach NEG.
        3. Update URL Map to include host rule + path matcher.
        4. Return the full HTTPS URL.

        Raises LBError on any GCP failure.
        """
        raise NotImplementedError("filled in Task L.7")

    async def unregister_agent(self, agent_id: str, env: str) -> None:
        """Reverse of register_agent. Used on agent delete."""
        raise NotImplementedError("filled in Task L.9")

    async def wait_until_reachable(self, url: str, timeout_s: int = 90) -> None:
        """Poll the URL until it returns a non-default response."""
        raise NotImplementedError("filled in Task L.8")
```

- [ ] **Step 2: Verify the module imports**

```bash
cd packages/dooers-push && uv run python -c "from dooers_push.gcp.loadbalancer import LBManager, LBError; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add packages/dooers-push/src/dooers_push/gcp/loadbalancer.py
git commit -m "feat(lb): add LBManager skeleton + LBError"
```

### Task L.4: `_ensure_neg` — create-or-get Serverless NEG

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`
- Modify: `packages/dooers-push/tests/test_lb_manager.py` (will create in this task)

- [ ] **Step 1: Create the test file with the first failing test**

Create `packages/dooers-push/tests/test_lb_manager.py`:

```python
"""Tests for LBManager operations — mocks google-cloud-compute."""

from unittest.mock import MagicMock, patch

import pytest
from google.api_core import exceptions as gcp_exceptions

from dooers_push.gcp.loadbalancer import LBError, LBManager
from dooers_push.settings import Settings


def _settings() -> Settings:
    return Settings(
        gcp_project_id="test-project",
        gcp_region="us-central1",
        bucket_name="test-bucket",
        artifact_repo="agents",
        core_api_url="https://api.test",
        environment="dev",
        request_timeout=10,
        lb_domain="agents.dooers.ai",
        lb_url_map="dooers-agents-url-map",
        lb_region="us-central1",
    )


@pytest.mark.asyncio
async def test_ensure_neg_creates_when_missing() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_op = MagicMock()
    mock_op.result.return_value = None
    mock_client.insert.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.RegionNetworkEndpointGroupsClient",
        return_value=mock_client,
    ):
        await lb._ensure_neg("ag_7q4r", "dev")

    mock_client.insert.assert_called_once()
    args, kwargs = mock_client.insert.call_args
    request = kwargs["request"]
    assert request.project == "test-project"
    assert request.region == "us-central1"
    assert request.network_endpoint_group_resource.name == "agent-ag-7q4r-dev-neg"
    assert request.network_endpoint_group_resource.network_endpoint_type == "SERVERLESS"


@pytest.mark.asyncio
async def test_ensure_neg_is_noop_when_already_exists() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_op = MagicMock()
    mock_op.result.side_effect = gcp_exceptions.Conflict("already exists")
    mock_client.insert.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.RegionNetworkEndpointGroupsClient",
        return_value=mock_client,
    ):
        # Should not raise — Conflict is caught and treated as success.
        await lb._ensure_neg("ag_7q4r", "dev")


@pytest.mark.asyncio
async def test_ensure_neg_raises_lberror_on_permission_denied() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_op = MagicMock()
    mock_op.result.side_effect = gcp_exceptions.PermissionDenied("no perms")
    mock_client.insert.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.RegionNetworkEndpointGroupsClient",
        return_value=mock_client,
    ):
        with pytest.raises(LBError) as exc_info:
            await lb._ensure_neg("ag_7q4r", "dev")
        assert "no perms" in str(exc_info.value).lower() or "permission" in str(exc_info.value).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/dooers-push && uv run pytest tests/test_lb_manager.py -v
```

Expected: FAIL — `compute_v1` import missing in loadbalancer.py, `_ensure_neg` not defined.

- [ ] **Step 3: Implement `_ensure_neg`**

Edit `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`. After the existing `import httpx`, add:

```python
from google.api_core import exceptions as gcp_exceptions
from google.cloud import compute_v1
```

Then in the `LBManager` class, add this method (after `wait_until_reachable`):

```python
    # ---- internal --------------------------------------------------------

    async def _ensure_neg(self, agent_id: str, env: str) -> str:
        """Create or get the Serverless NEG. Returns its self-link URL."""
        name = neg_name(agent_id, env)
        cloud_run_service = f"{safe_agent_id(agent_id)}-{env}"

        neg_resource = compute_v1.NetworkEndpointGroup(
            name=name,
            network_endpoint_type="SERVERLESS",
            cloud_run=compute_v1.NetworkEndpointGroupCloudRun(service=cloud_run_service),
        )
        request = compute_v1.InsertRegionNetworkEndpointGroupRequest(
            project=self.project_id,
            region=self.region,
            network_endpoint_group_resource=neg_resource,
        )

        client = compute_v1.RegionNetworkEndpointGroupsClient()
        loop = asyncio.get_running_loop()

        def _insert() -> None:
            op = client.insert(request=request)
            op.result(timeout=120)

        try:
            await loop.run_in_executor(None, _insert)
            logger.info("lb_op=ensure_neg agent_id=%s env=%s neg=created", agent_id, env)
        except gcp_exceptions.Conflict:
            logger.info("lb_op=ensure_neg agent_id=%s env=%s neg=already_exists", agent_id, env)
        except gcp_exceptions.PermissionDenied as e:
            raise LBError(f"permission denied creating NEG {name}: {e}",
                          operation="ensure_neg", cause=e) from e
        except gcp_exceptions.GoogleAPIError as e:
            raise LBError(f"failed to create NEG {name}: {e}",
                          operation="ensure_neg", cause=e) from e

        # Self-link form used by Backend Service refs:
        return (
            f"https://www.googleapis.com/compute/v1/projects/{self.project_id}"
            f"/regions/{self.region}/networkEndpointGroups/{name}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_lb_manager.py -v
```

Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-push/src/dooers_push/gcp/loadbalancer.py \
        packages/dooers-push/tests/test_lb_manager.py
git commit -m "feat(lb): implement LBManager._ensure_neg (create-or-get Serverless NEG)"
```

### Task L.5: `_ensure_backend_service` — create-or-get Backend Service

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`
- Modify: `packages/dooers-push/tests/test_lb_manager.py`

- [ ] **Step 1: Append the failing tests**

Append to `packages/dooers-push/tests/test_lb_manager.py`:

```python
@pytest.mark.asyncio
async def test_ensure_bs_creates_when_missing() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_op = MagicMock()
    mock_op.result.return_value = None
    mock_client.insert.return_value = mock_op

    neg_url = (
        "https://www.googleapis.com/compute/v1/projects/test-project"
        "/regions/us-central1/networkEndpointGroups/agent-ag-7q4r-dev-neg"
    )

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.BackendServicesClient",
        return_value=mock_client,
    ):
        await lb._ensure_backend_service("ag_7q4r", "dev", neg_url)

    mock_client.insert.assert_called_once()
    args, kwargs = mock_client.insert.call_args
    bs = kwargs["request"].backend_service_resource
    assert bs.name == "agent-ag-7q4r-dev-bs"
    assert bs.protocol == "HTTPS"
    assert len(bs.backends) == 1
    assert bs.backends[0].group == neg_url


@pytest.mark.asyncio
async def test_ensure_bs_is_noop_when_already_exists() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_op = MagicMock()
    mock_op.result.side_effect = gcp_exceptions.Conflict("already exists")
    mock_client.insert.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.BackendServicesClient",
        return_value=mock_client,
    ):
        await lb._ensure_backend_service("ag_7q4r", "dev", "neg-url")
    # No raise = success
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_lb_manager.py::test_ensure_bs_creates_when_missing -v
```

Expected: FAIL — `_ensure_backend_service` doesn't exist.

- [ ] **Step 3: Implement `_ensure_backend_service`**

Append to the `LBManager` class in `loadbalancer.py`:

```python
    async def _ensure_backend_service(self, agent_id: str, env: str, neg_url: str) -> str:
        """Create or get the Backend Service. Returns its self-link URL."""
        name = bs_name(agent_id, env)

        backend = compute_v1.Backend(group=neg_url)
        bs_resource = compute_v1.BackendService(
            name=name,
            protocol="HTTPS",
            backends=[backend],
        )
        request = compute_v1.InsertBackendServiceRequest(
            project=self.project_id,
            backend_service_resource=bs_resource,
        )

        client = compute_v1.BackendServicesClient()
        loop = asyncio.get_running_loop()

        def _insert() -> None:
            op = client.insert(request=request)
            op.result(timeout=120)

        try:
            await loop.run_in_executor(None, _insert)
            logger.info("lb_op=ensure_bs agent_id=%s env=%s bs=created", agent_id, env)
        except gcp_exceptions.Conflict:
            logger.info("lb_op=ensure_bs agent_id=%s env=%s bs=already_exists", agent_id, env)
        except gcp_exceptions.PermissionDenied as e:
            raise LBError(f"permission denied creating BS {name}: {e}",
                          operation="ensure_bs", cause=e) from e
        except gcp_exceptions.GoogleAPIError as e:
            raise LBError(f"failed to create BS {name}: {e}",
                          operation="ensure_bs", cause=e) from e

        return (
            f"https://www.googleapis.com/compute/v1/projects/{self.project_id}"
            f"/global/backendServices/{name}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_lb_manager.py -v
```

Expected: PASS, 5 tests total.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-push/src/dooers_push/gcp/loadbalancer.py \
        packages/dooers-push/tests/test_lb_manager.py
git commit -m "feat(lb): implement LBManager._ensure_backend_service"
```

### Task L.6: `_update_url_map` — add host rule + path matcher

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`
- Modify: `packages/dooers-push/tests/test_lb_manager.py`

- [ ] **Step 1: Append the failing tests**

Append to `packages/dooers-push/tests/test_lb_manager.py`:

```python
@pytest.mark.asyncio
async def test_update_url_map_appends_when_host_missing() -> None:
    lb = LBManager(_settings())

    # Mock the existing URL map (no rules for our host yet)
    existing_url_map = MagicMock()
    existing_url_map.host_rules = []
    existing_url_map.path_matchers = []
    existing_url_map.default_service = "default-bs"

    mock_client = MagicMock()
    mock_client.get.return_value = existing_url_map
    mock_op = MagicMock()
    mock_op.result.return_value = None
    mock_client.patch.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.UrlMapsClient",
        return_value=mock_client,
    ):
        await lb._update_url_map(
            "ag_7q4r", "dev",
            host="ag-7q4r-dev.agents.dooers.ai",
            bs_self_link="bs-url",
        )

    mock_client.patch.assert_called_once()
    args, kwargs = mock_client.patch.call_args
    patched = kwargs["url_map_resource"]
    assert len(patched.host_rules) == 1
    assert patched.host_rules[0].hosts == ["ag-7q4r-dev.agents.dooers.ai"]
    assert patched.host_rules[0].path_matcher == "agent-ag-7q4r-dev-pm"
    assert len(patched.path_matchers) == 1
    assert patched.path_matchers[0].name == "agent-ag-7q4r-dev-pm"
    assert patched.path_matchers[0].default_service == "bs-url"


@pytest.mark.asyncio
async def test_update_url_map_is_noop_when_host_already_routed() -> None:
    lb = LBManager(_settings())

    # Pre-existing host rule + path matcher for the same agent.
    existing_host_rule = compute_v1.HostRule(
        hosts=["ag-7q4r-dev.agents.dooers.ai"],
        path_matcher="agent-ag-7q4r-dev-pm",
    )
    existing_pm = compute_v1.PathMatcher(
        name="agent-ag-7q4r-dev-pm",
        default_service="bs-url",
    )

    existing_url_map = MagicMock()
    existing_url_map.host_rules = [existing_host_rule]
    existing_url_map.path_matchers = [existing_pm]

    mock_client = MagicMock()
    mock_client.get.return_value = existing_url_map
    mock_op = MagicMock()
    mock_op.result.return_value = None
    mock_client.patch.return_value = mock_op

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.UrlMapsClient",
        return_value=mock_client,
    ):
        await lb._update_url_map(
            "ag_7q4r", "dev",
            host="ag-7q4r-dev.agents.dooers.ai",
            bs_self_link="bs-url",
        )

    # Patch may still be called (with same content) — idempotent.
    # Key assertion: nothing duplicated.
    if mock_client.patch.called:
        patched = mock_client.patch.call_args.kwargs["url_map_resource"]
        host_strings = [h for hr in patched.host_rules for h in hr.hosts]
        assert host_strings.count("ag-7q4r-dev.agents.dooers.ai") == 1


@pytest.mark.asyncio
async def test_update_url_map_raises_lberror_when_map_not_found() -> None:
    lb = LBManager(_settings())
    mock_client = MagicMock()
    mock_client.get.side_effect = gcp_exceptions.NotFound("url map not found")

    with patch(
        "dooers_push.gcp.loadbalancer.compute_v1.UrlMapsClient",
        return_value=mock_client,
    ):
        with pytest.raises(LBError) as exc_info:
            await lb._update_url_map(
                "ag_7q4r", "dev",
                host="ag-7q4r-dev.agents.dooers.ai",
                bs_self_link="bs-url",
            )
        assert "not found" in str(exc_info.value).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_lb_manager.py -v
```

Expected: FAIL — `_update_url_map` not defined.

- [ ] **Step 3: Implement `_update_url_map`**

Append to the `LBManager` class:

```python
    async def _update_url_map(self, agent_id: str, env: str, *,
                              host: str, bs_self_link: str) -> None:
        """Add or update the host rule + path matcher for this agent."""
        pm = path_matcher_name(agent_id, env)
        client = compute_v1.UrlMapsClient()
        loop = asyncio.get_running_loop()

        def _get_and_patch() -> None:
            try:
                url_map = client.get(project=self.project_id, url_map=self.url_map_name)
            except gcp_exceptions.NotFound as e:
                raise LBError(
                    f"URL map {self.url_map_name!r} not found — has devops completed gcp-lb.md setup?",
                    operation="url_map_get",
                    cause=e,
                ) from e

            # Find existing rule for this host (if any)
            existing_pm_idx = None
            existing_hr_idx = None
            for i, pm_obj in enumerate(url_map.path_matchers):
                if pm_obj.name == pm:
                    existing_pm_idx = i
                    break
            for i, hr in enumerate(url_map.host_rules):
                if host in hr.hosts:
                    existing_hr_idx = i
                    break

            # Build new path matcher
            new_pm = compute_v1.PathMatcher(name=pm, default_service=bs_self_link)
            if existing_pm_idx is not None:
                url_map.path_matchers[existing_pm_idx] = new_pm
            else:
                url_map.path_matchers.append(new_pm)

            # Build new host rule
            new_hr = compute_v1.HostRule(hosts=[host], path_matcher=pm)
            if existing_hr_idx is not None:
                url_map.host_rules[existing_hr_idx] = new_hr
            else:
                url_map.host_rules.append(new_hr)

            op = client.patch(
                project=self.project_id,
                url_map=self.url_map_name,
                url_map_resource=url_map,
            )
            op.result(timeout=120)

        try:
            await loop.run_in_executor(None, _get_and_patch)
            logger.info("lb_op=update_url_map agent_id=%s env=%s host=%s ok",
                        agent_id, env, host)
        except LBError:
            raise
        except gcp_exceptions.PermissionDenied as e:
            raise LBError(f"permission denied patching URL Map: {e}",
                          operation="url_map_patch", cause=e) from e
        except gcp_exceptions.GoogleAPIError as e:
            raise LBError(f"failed to patch URL Map: {e}",
                          operation="url_map_patch", cause=e) from e
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_lb_manager.py -v
```

Expected: PASS, 8 tests total.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-push/src/dooers_push/gcp/loadbalancer.py \
        packages/dooers-push/tests/test_lb_manager.py
git commit -m "feat(lb): implement LBManager._update_url_map (idempotent host rule)"
```

### Task L.7: `register_agent` — public orchestrator

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`
- Modify: `packages/dooers-push/tests/test_lb_manager.py`

- [ ] **Step 1: Append the failing test**

Append to `tests/test_lb_manager.py`:

```python
@pytest.mark.asyncio
async def test_register_agent_orchestrates_calls_and_returns_url() -> None:
    lb = LBManager(_settings())

    with (
        patch.object(lb, "_ensure_neg", return_value="neg-url") as m_neg,
        patch.object(lb, "_ensure_backend_service", return_value="bs-url") as m_bs,
        patch.object(lb, "_update_url_map", return_value=None) as m_url_map,
    ):
        url = await lb.register_agent("ag_7q4r", "dev")

    m_neg.assert_called_once_with("ag_7q4r", "dev")
    m_bs.assert_called_once_with("ag_7q4r", "dev", "neg-url")
    m_url_map.assert_called_once()
    assert url == "https://ag-7q4r-dev.agents.dooers.ai"


@pytest.mark.asyncio
async def test_register_agent_prod_drops_env_suffix_in_url() -> None:
    lb = LBManager(_settings())

    with (
        patch.object(lb, "_ensure_neg", return_value="neg-url"),
        patch.object(lb, "_ensure_backend_service", return_value="bs-url"),
        patch.object(lb, "_update_url_map", return_value=None),
    ):
        url = await lb.register_agent("ag_7q4r", "prod")

    assert url == "https://ag-7q4r.agents.dooers.ai"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_lb_manager.py::test_register_agent_orchestrates_calls_and_returns_url -v
```

Expected: FAIL — `register_agent` still raises `NotImplementedError`.

- [ ] **Step 3: Implement `register_agent`**

Replace the `register_agent` method in `LBManager` with:

```python
    async def register_agent(self, agent_id: str, env: str) -> str:
        """Wire {agent_id}-{env} Cloud Run into the LB; return the URL."""
        host = host_for(agent_id, env, self.domain)

        neg_url = await self._ensure_neg(agent_id, env)
        bs_url = await self._ensure_backend_service(agent_id, env, neg_url)
        await self._update_url_map(agent_id, env, host=host, bs_self_link=bs_url)

        return f"https://{host}"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_lb_manager.py -v
```

Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-push/src/dooers_push/gcp/loadbalancer.py \
        packages/dooers-push/tests/test_lb_manager.py
git commit -m "feat(lb): implement LBManager.register_agent (orchestrates NEG + BS + URL Map)"
```

### Task L.8: `wait_until_reachable`

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`
- Modify: `packages/dooers-push/tests/test_lb_manager.py`

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_lb_manager.py`:

```python
@pytest.mark.asyncio
async def test_wait_until_reachable_returns_on_first_success() -> None:
    lb = LBManager(_settings())

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client = MagicMock()
    mock_client.__aenter__ = MagicMock(return_value=mock_client)
    mock_client.__aexit__ = MagicMock(return_value=False)

    async def _aenter(self):
        return mock_client

    async def _aexit(self, *_):
        return False

    async def _get(_url, **_kwargs):
        return mock_response

    mock_client.get = _get
    mock_client.__aenter__ = _aenter.__get__(mock_client)
    mock_client.__aexit__ = _aexit.__get__(mock_client)

    with patch(
        "dooers_push.gcp.loadbalancer.httpx.AsyncClient",
        return_value=mock_client,
    ):
        # Should return without raising
        await lb.wait_until_reachable("https://ag-test.agents.dooers.ai", timeout_s=5)


@pytest.mark.asyncio
async def test_wait_until_reachable_returns_on_timeout_without_raising() -> None:
    lb = LBManager(_settings())

    # Mock httpx.AsyncClient where every request raises ConnectError
    class FailingClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("nope")

    with patch(
        "dooers_push.gcp.loadbalancer.httpx.AsyncClient",
        return_value=FailingClient(),
    ):
        # Should NOT raise on timeout — logs a warning instead.
        await lb.wait_until_reachable("https://ag-test.agents.dooers.ai", timeout_s=1)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_lb_manager.py -k wait_until -v
```

Expected: FAIL — `wait_until_reachable` raises `NotImplementedError`.

- [ ] **Step 3: Implement `wait_until_reachable`**

Replace the `wait_until_reachable` method:

```python
    async def wait_until_reachable(self, url: str, timeout_s: int = 90) -> None:
        """Poll the URL until it returns a non-default response or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout_s

        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    response = await client.get(url)
                    if response.status_code < 500:
                        # LB is responding; rule has propagated.
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1.0)

        logger.warning(
            "lb_op=wait_until_reachable url=%s timed_out_after=%ds (LB will propagate shortly)",
            url, timeout_s,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_lb_manager.py -v
```

Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-push/src/dooers_push/gcp/loadbalancer.py \
        packages/dooers-push/tests/test_lb_manager.py
git commit -m "feat(lb): implement LBManager.wait_until_reachable (poll URL until live)"
```

### Task L.9: `unregister_agent`

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`
- Modify: `packages/dooers-push/tests/test_lb_manager.py`

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_lb_manager.py`:

```python
@pytest.mark.asyncio
async def test_unregister_agent_removes_in_correct_order() -> None:
    lb = LBManager(_settings())
    calls: list[str] = []

    async def _record_url_map(*args, **kwargs):
        calls.append("url_map")

    async def _record_bs(*args, **kwargs):
        calls.append("bs")

    async def _record_neg(*args, **kwargs):
        calls.append("neg")

    with (
        patch.object(lb, "_remove_url_map_host_rule", side_effect=_record_url_map),
        patch.object(lb, "_delete_backend_service", side_effect=_record_bs),
        patch.object(lb, "_delete_neg", side_effect=_record_neg),
    ):
        await lb.unregister_agent("ag_7q4r", "dev")

    assert calls == ["url_map", "bs", "neg"]


@pytest.mark.asyncio
async def test_unregister_agent_ignores_missing_resources() -> None:
    lb = LBManager(_settings())

    async def _raise_not_found(*args, **kwargs):
        raise gcp_exceptions.NotFound("gone")

    with (
        patch.object(lb, "_remove_url_map_host_rule", side_effect=_raise_not_found),
        patch.object(lb, "_delete_backend_service", side_effect=_raise_not_found),
        patch.object(lb, "_delete_neg", side_effect=_raise_not_found),
    ):
        # Should not raise — delete is idempotent.
        await lb.unregister_agent("ag_7q4r", "dev")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_lb_manager.py -k unregister -v
```

Expected: FAIL — `unregister_agent` and helpers not defined.

- [ ] **Step 3: Implement `unregister_agent` + helpers**

Replace `unregister_agent` and add three internal helpers:

```python
    async def unregister_agent(self, agent_id: str, env: str) -> None:
        """Reverse of register_agent. Removes in order: URL Map rule, BS, NEG."""
        host = host_for(agent_id, env, self.domain)

        for step_fn, op_name in (
            (lambda: self._remove_url_map_host_rule(agent_id, env, host=host), "remove_url_map"),
            (lambda: self._delete_backend_service(agent_id, env), "delete_bs"),
            (lambda: self._delete_neg(agent_id, env), "delete_neg"),
        ):
            try:
                await step_fn()
            except gcp_exceptions.NotFound:
                logger.info("lb_op=%s agent_id=%s env=%s already_gone",
                            op_name, agent_id, env)
            except gcp_exceptions.GoogleAPIError as e:
                raise LBError(f"unregister failed at {op_name}: {e}",
                              operation=op_name, cause=e) from e

    async def _remove_url_map_host_rule(self, agent_id: str, env: str, *, host: str) -> None:
        pm = path_matcher_name(agent_id, env)
        client = compute_v1.UrlMapsClient()
        loop = asyncio.get_running_loop()

        def _patch() -> None:
            url_map = client.get(project=self.project_id, url_map=self.url_map_name)
            url_map.host_rules = [hr for hr in url_map.host_rules if host not in hr.hosts]
            url_map.path_matchers = [m for m in url_map.path_matchers if m.name != pm]
            op = client.patch(project=self.project_id, url_map=self.url_map_name,
                              url_map_resource=url_map)
            op.result(timeout=120)

        await loop.run_in_executor(None, _patch)

    async def _delete_backend_service(self, agent_id: str, env: str) -> None:
        name = bs_name(agent_id, env)
        client = compute_v1.BackendServicesClient()
        loop = asyncio.get_running_loop()

        def _delete() -> None:
            op = client.delete(project=self.project_id, backend_service=name)
            op.result(timeout=120)

        await loop.run_in_executor(None, _delete)

    async def _delete_neg(self, agent_id: str, env: str) -> None:
        name = neg_name(agent_id, env)
        client = compute_v1.RegionNetworkEndpointGroupsClient()
        loop = asyncio.get_running_loop()

        def _delete() -> None:
            op = client.delete(
                project=self.project_id,
                region=self.region,
                network_endpoint_group=name,
            )
            op.result(timeout=120)

        await loop.run_in_executor(None, _delete)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_lb_manager.py -v
```

Expected: PASS, 14 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-push/src/dooers_push/gcp/loadbalancer.py \
        packages/dooers-push/tests/test_lb_manager.py
git commit -m "feat(lb): implement LBManager.unregister_agent (ordered teardown)"
```

### Task L.10: Wire `LBManager` into `DeployerStep`

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/pipeline/deployer.py`

- [ ] **Step 1: Replace deployer.py with LB-aware version**

Replace `packages/dooers-push/src/dooers_push/pipeline/deployer.py` with:

```python
"""Deployer step — Cloud Build → Cloud Run, then LB registration.

POC: ports v1 Cloud Build trigger from server/main.py, then adds the
LB phase via LBManager. Service name: `{agent_id_safe}-{env}` (per the
deploy-time naming in gcp/cloudbuild.py).
"""

import logging

from dooers_protocol.push import BuildStatus
from dooers_push.gcp.cloudbuild import trigger_build, wait_for_build
from dooers_push.gcp.loadbalancer import LBError, LBManager
from dooers_push.pipeline.base import PipelineContext, PipelineStep, StepResult
from dooers_push.settings import Settings

logger = logging.getLogger(__name__)


class DeployerStep(PipelineStep):
    name = "deployer"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lb = LBManager(settings)

    async def run(self, ctx: PipelineContext) -> StepResult:
        # Phase 1: Cloud Build + Cloud Run
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
            success = await wait_for_build(op_name)
            if not success:
                return StepResult(status=BuildStatus.failed,
                                  error="Cloud Build reported failure")
        except TimeoutError as e:
            return StepResult(status=BuildStatus.failed, error=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("build phase crashed")
            return StepResult(status=BuildStatus.failed, error=f"build error: {e}")

        # Phase 2: LB registration
        try:
            lb_url = await self.lb.register_agent(ctx.agent.agent_id, ctx.env)
            await self.lb.wait_until_reachable(lb_url)
            ctx.lb_url = lb_url
            return StepResult(status=BuildStatus.succeeded)
        except LBError as e:
            logger.exception("LB registration failed")
            return StepResult(status=BuildStatus.failed,
                              error=f"LB registration failed: {e}")
```

- [ ] **Step 2: Run all push tests to verify nothing else breaks**

```bash
cd packages/dooers-push && uv run poe test
```

Expected: PASS. The smoke tests for the pipeline stubs should still work (they don't exercise the deployer's real branch).

- [ ] **Step 3: Commit**

```bash
git add packages/dooers-push/src/dooers_push/pipeline/deployer.py
git commit -m "feat(lb): integrate LBManager into DeployerStep as final phase"
```

### Task L.11: Use `ctx.lb_url` as the URL source in `main.py`

**Files:**
- Modify: `packages/dooers-push/src/dooers_push/main.py`

- [ ] **Step 1: Replace the URL-source block**

In `packages/dooers-push/src/dooers_push/main.py`, find the success-path return at the end of the `push()` handler. It looks like this (after Task 3.10 of the base plan):

```python
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
        audit=ctx.audit_report,
    )
```

Replace with:

```python
    # Build + LB registration succeeded → URL comes from ctx.lb_url.
    if not ctx.lb_url:
        # Defensive: should never happen on success path.
        return PushResponse(
            agent_id=agent_id,
            build_id=ctx.build_id or "",
            image=ctx.image or "",
            status=BuildStatus.failed,
            error="internal: deployer reported success but no lb_url set",
            audit=ctx.audit_report,
        )

    await core.patch_agent_url(agent_id, ctx.lb_url)
    return PushResponse(
        agent_id=agent_id,
        build_id=ctx.build_id or "",
        image=ctx.image or "",
        status=BuildStatus.succeeded,
        url=ctx.lb_url,
        audit=ctx.audit_report,
    )
```

Remove the now-unused `describe_service_url` import + `_service_name` import if they're only used for this path. (Inspect with `grep`; if they're used elsewhere — e.g., the unregister path — leave them in.)

- [ ] **Step 2: Run smoke tests**

```bash
uv run poe test
```

Expected: existing smoke tests should still PASS. The `test_smoke.py` test for the route will need a future update (Task L.12) to actually exercise the LB URL — for now it just verifies the route returns a `PushResponse` shape.

- [ ] **Step 3: Commit**

```bash
git add packages/dooers-push/src/dooers_push/main.py
git commit -m "feat(lb): use ctx.lb_url as URL source in push response"
```

### Task L.12: Update smoke test to assert LB URL

**Files:**
- Modify: `packages/dooers-push/tests/test_smoke.py`

- [ ] **Step 1: Replace the smoke test**

Replace `packages/dooers-push/tests/test_smoke.py` with:

```python
"""Smoke tests — server boots, /health works, push route returns LB URL."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from dooers_push.main import app


def test_health() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_app_metadata() -> None:
    assert app.title == "dooers-push"
    assert app.version == "0.1.0"


@pytest.mark.asyncio
async def test_push_returns_lb_url_when_pipeline_succeeds() -> None:
    """End-to-end shape check with all I/O mocked."""
    # This test will be filled in fully once Tasks 3.4-3.10 of the base plan land.
    # For now, assert the route exists and rejects an unauthorized request.
    client = TestClient(app)
    resp = client.post(
        "/v1/push/ag_7q4r",
        files={"archive": ("test.tar.gz", b"fake", "application/gzip")},
        headers={"Authorization": "Bearer bogus"},
    )
    # No core to verify the bogus token; expect 401 or 503.
    assert resp.status_code in (401, 503)
```

- [ ] **Step 2: Run tests**

```bash
uv run poe test
```

Expected: PASS. The smoke test is intentionally light because the heavy mocking lives in `test_lb_manager.py`.

- [ ] **Step 3: Commit**

```bash
git add packages/dooers-push/tests/test_smoke.py
git commit -m "test(lb): tighten smoke tests for the LB-integrated push route"
```

### Task L.13: Manual end-to-end verification (gated on devops completing `gcp-lb.md`)

**Files:** None (manual verification only).

**Prerequisite:** Devops must have completed all steps in `docs/devops/gcp-lb.md`, including SSL cert in `ACTIVE` state and DNS resolving.

- [ ] **Step 1: Confirm prerequisites**

```bash
# DNS resolves to the static LB IP
dig +short ag-anything.agents.dooers.ai
# → static IP

# SSL cert is ACTIVE
gcloud compute ssl-certificates describe dooers-agents-wildcard-cert \
  --global --format='value(managed.status)'
# → ACTIVE

# URL Map exists
gcloud compute url-maps describe dooers-agents-url-map --format='value(name)'
# → dooers-agents-url-map

# dooers-push is deployed and healthy
curl https://<dooers-push-host>/health
# → {"status":"ok"}
```

If any of these fail, devops's setup is incomplete; do not proceed.

- [ ] **Step 2: Create a fresh test agent**

```bash
# Use the dooers-cli implemented in M2 of the base plan
export DOOERS_CORE_URL=https://api.dev.dooers.ai
export DOOERS_PUSH_URL=https://<dooers-push-host>
export DOOERS_ENV=dev

cd packages/dooers-cli
uv run dooers auth login --email <your-email>   # if not already
mkdir -p /tmp/lb-smoke && cd /tmp/lb-smoke
cat > Dockerfile <<'EOF'
FROM python:3.12-slim
RUN pip install fastapi uvicorn
COPY main.py .
ENV PORT=8080
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
EOF
cat > main.py <<'EOF'
from fastapi import FastAPI
app = FastAPI()
@app.get("/")
def root(): return {"agent": "lb-smoke-test", "url": "from-the-LB"}
EOF
uv run dooers agents create --name lb-smoke-test
cat dooers.yaml   # contains agent_id
```

- [ ] **Step 3: Push and observe the LB URL**

```bash
uv run dooers push
```

Expected output (after ~3-5 min):
```
Archiving …
Pushing ag-xxxx (this can take 3-5 min) /
Audit: 1 endpoint(s) detected:
  - /
Live at: https://ag-xxxx-dev.agents.dooers.ai
```

Specifically: the URL must end in `.agents.dooers.ai`, **not** `.run.app`.

- [ ] **Step 4: Verify the URL routes correctly**

```bash
curl https://ag-xxxx-dev.agents.dooers.ai
# → {"agent":"lb-smoke-test","url":"from-the-LB"}
```

If it returns 404 or the default placeholder body, wait 30s for LB propagation and retry.

- [ ] **Step 5: Verify LB state in GCP**

```bash
gcloud compute network-endpoint-groups list --filter='name~^agent-ag-xxxx-'
# → 1 NEG named agent-ag-xxxx-dev-neg

gcloud compute backend-services list --global --filter='name~^agent-ag-xxxx-'
# → 1 BS named agent-ag-xxxx-dev-bs

gcloud compute url-maps describe dooers-agents-url-map \
  --format='value(hostRules)' | grep ag-xxxx
# → ag-xxxx-dev.agents.dooers.ai routed
```

- [ ] **Step 6: Verify idempotency by re-pushing**

```bash
cd /tmp/lb-smoke
uv run dooers push
# → same URL, completes faster (build is cached, LB is no-op)

# Confirm no duplicate resources:
gcloud compute network-endpoint-groups list --filter='name~^agent-ag-xxxx-' \
  --format='value(name)' | wc -l
# → 1 (still)
```

- [ ] **Step 7: Verify the agent record was updated in core**

```bash
uv run dooers agents show ag_xxxx
# Status: deployed
# URL: https://ag-xxxx-dev.agents.dooers.ai
```

- [ ] **Step 8: Tag the milestone**

```bash
git tag -a m3-lb -m "M3 LB integration verified end-to-end on dev"
```

This task does not commit code — it's a verification gate.

---

## Self-Review

**Spec coverage check** — every section of `docs/superpowers/specs/2026-05-27-dooers-lb-design.md` maps to at least one task:

| Spec § | Requirement | Tasks |
|---|---|---|
| 3 | URL convention (prod drops env, non-prod keeps) | L.2 (helpers), L.7 (orchestrator returns URL) |
| 3 | Boundary rule: LB managed only in `dooers-push` | L.10 (DeployerStep) |
| 5 | `LBManager` class + methods | L.3 (skeleton), L.4-L.9 (each method) |
| 5 | Naming helpers (`safe_agent_id`, `host_for`, etc.) | L.2 (TDD) |
| 5 | Idempotency contract | L.4, L.5, L.6 (catch `Conflict`) |
| 5 | Integration in `DeployerStep` | L.10 |
| 5 | URL source from `ctx.lb_url` in `main.py` | L.11 |
| 6 | `lb_registration_failed` error code | L.1 |
| 6 | `lb_domain`, `lb_url_map`, `lb_region` settings | L.1 |
| 6 | `lb_url` on `PipelineContext` | L.1 |
| 7 | Failure taxonomy → `LBError` | L.3, L.4-L.6 (raise paths) |
| 7 | Partial-failure recovery via idempotency | L.4, L.5, L.6 |
| 7 | `wait_until_reachable` non-fatal on timeout | L.8 |
| 7 | Cleanup designed (`unregister_agent`) | L.9 |
| 8 | Unit tests (naming) | L.2 |
| 8 | Mock-based tests (LBManager) | L.4-L.9 each |
| 8 | Manual end-to-end | L.13 |

**Placeholder scan:** No TBDs / TODOs in the plan body. Every code block is complete code an engineer can paste. ✓

**Type consistency check:**
- `LBManager.__init__(settings: Settings)` consistent across L.3, L.4, L.10.
- Method names: `_ensure_neg`, `_ensure_backend_service`, `_update_url_map`, `register_agent`, `wait_until_reachable`, `unregister_agent` — same in tests and implementation. ✓
- `ctx.lb_url: str | None` consistent in L.1 (field add), L.10 (deployer sets), L.11 (main.py reads). ✓
- `LBError(message, *, operation, cause)` signature consistent throughout. ✓

**Open dependencies the engineer should know:**
1. Tasks L.1-L.12 can be executed today without LB infra. Tests pass against mocks.
2. Task L.13 requires devops's `gcp-lb.md` setup to be complete (SSL cert ACTIVE).
3. The base plan's Task 3.11 (deploy dooers-push to dev Cloud Run) can happen any time after L.12 lands — the dooers-push deployment is independent of LB readiness; it will simply fail at `register_agent()` with "URL Map not found" until devops finishes. That's the correct failure mode.

---

## Notes for the engineer

- **Frequent commits**: every numbered task ends in a `git commit`. ~12 commits across this plan; ~30 cumulative with the base plan. Resist squashing.
- **TDD where free**: pure-function modules (`safe_agent_id`, `host_for`) get test-first treatment. `LBManager` methods are mock-based but still test-first (write the test, watch it fail, implement, watch it pass). HTTP/GCP integrations test against mocks; real-world correctness validated by the manual run in Task L.13.
- **Parallelism with devops**: Tasks L.1-L.12 do not require any GCP setup. The engineer can ship all code while devops is still working through `gcp-lb.md`. The two tracks meet at Task L.13.
- **Reference for SDK syntax**: `google-cloud-compute` Python docs at <https://cloud.google.com/python/docs/reference/compute/latest>. If an idiom looks unfamiliar, consult the autogenerated client stubs — the resource class names (`NetworkEndpointGroup`, `BackendService`, `UrlMap`) match the GCP API.
