# Per-tenant Service-Account Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every push stage (ingest / build / deploy / runtime) its own least-privilege identity and give every organization its own build + runtime service accounts, eliminating cross-tenant access during build and at runtime.

**Architecture:** `dooers-push` (the trusted control plane) keeps orchestrating, but Cloud Build now only builds+pushes an image as a per-org **build SA**; the control plane then deploys Cloud Run by image **digest** via the Run Admin API, setting the runtime identity to a per-org **tenant SA**. A separate, idempotent `provision-org` CLI creates the per-org SAs, an Artifact Registry repo, and all bindings. Org id → resource names is a pure, hashed mapping.

**Tech Stack:** Python 3.10+, `uv` + `poethepoet`, `ruff`, `mypy`, `pytest`. GCP: Cloud Build (`google-cloud-build`), Cloud Run Admin API (`google-cloud-run`, NEW dep), Artifact Registry, GCS (`google-cloud-storage`), IAM. FastAPI control plane. Pydantic wire models in `dooers-protocol`.

## Global Constraints

- Python floor **3.10** for `dooers-cli` and `dooers-protocol`; `dooers-push` runs on 3.12 but keep code 3.10-safe in shared shapes. (verbatim from CLAUDE.md)
- Each package is independent: `cd packages/<pkg>` (cli/protocol) or the `dooers-push` repo root first; run tasks via `uv run poe <task>`.
- GCP project for agents/build/run/AR/bucket/LB/AlloyDB: **`dooers-agents`**. Control plane runs in **`dooers-services`** as `dooers-push-runtime@dooers-services`. Region **`southamerica-east1`**. Source bucket **`dooers-agents-src`**.
- SA account IDs and AR repo IDs MUST match `^[a-z]([-a-z0-9]{4,28}[a-z0-9])$` (6–30 chars). Org id is hashed to satisfy this: `token = sha256(org_id).hexdigest()[:12]`.
- Resource names, verbatim: tenant SA id `tenant-<token>`, build SA id `build-<token>`, AR repo `agents-<token>`, GCS prefix `agents/<org_id>/`, build SA source IAM condition prefix `projects/_/buckets/dooers-agents-src/objects/agents/<org_id>/`.
- **Additive infra only.** Do NOT delete `agent-deploy-service`, `dooers-push-runtime`, or `onodera-agente-faq`. Only strip excess role bindings from `agent-deploy-service` in the final migration phase.
- TDD: write the failing test first, watch it fail, implement minimally, watch it pass, commit. Small commits.
- The wire contract change is one **additive** enum member (`ErrorCode.org_not_provisioned`). No breaking protocol changes.

**Repos touched:** `dooers-protocol` (Phase A), `dooers-push` (Phase B), `dooers-cli` (Phase C), GCP infra runbook (Phase D — operator-run, gated on team approval, NOT executed by this plan's code tasks).

---

## Phase A — `dooers-protocol`: additive error code

### Task A1: Add `org_not_provisioned` error code

**Files:**
- Modify: `packages/dooers-protocol/src/dooers/protocol/errors.py`
- Test: `packages/dooers-protocol/tests/test_smoke.py` (add a focused test)

**Interfaces:**
- Produces: `ErrorCode.org_not_provisioned` (str enum member, value `"org_not_provisioned"`).

- [ ] **Step 1: Write the failing test**

```python
# packages/dooers-protocol/tests/test_errors_org_not_provisioned.py
from dooers.protocol.errors import ErrorCode

def test_org_not_provisioned_member_exists():
    assert ErrorCode.org_not_provisioned.value == "org_not_provisioned"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/dooers-protocol && uv run pytest tests/test_errors_org_not_provisioned.py -v`
Expected: FAIL — `AttributeError: org_not_provisioned`.

- [ ] **Step 3: Add the enum member**

Open `errors.py` and add the member to the `ErrorCode` enum (keep alphabetic/logical grouping near `forbidden`):

```python
    org_not_provisioned = "org_not_provisioned"
```

- [ ] **Step 4: Run test + full protocol suite**

Run: `cd packages/dooers-protocol && uv run poe dev`
Expected: PASS (lint + mypy + tests green).

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-protocol/src/dooers/protocol/errors.py packages/dooers-protocol/tests/test_errors_org_not_provisioned.py
git commit -m "feat(protocol): add org_not_provisioned error code"
```

> Release note for the team: this needs a `dooers-protocol` patch release (per `pypi-release-process` memory) before `dooers-push` can depend on it from PyPI, OR consume it via the editable sibling during development. The version bump is folded into the release step at the end of Phase B.

---

## Phase B — `dooers-push`: pipeline identity split

All Phase B paths are in the **`dooers-push` repo root** (sibling of `dooers-cli`). Run `uv sync --extra dev` once at the start.

### Task B1: `tenancy.py` — org id → resource names (pure)

**Files:**
- Create: `src/dooers_push/tenancy.py`
- Test: `tests/test_tenancy.py`

**Interfaces:**
- Produces:
  - `org_token(org_id: str) -> str` (12 lowercase hex chars)
  - `tenant_sa_id(org_id: str) -> str` → `tenant-<token>`
  - `build_sa_id(org_id: str) -> str` → `build-<token>`
  - `tenant_sa_email(org_id: str, project: str) -> str`
  - `build_sa_email(org_id: str, project: str) -> str`
  - `ar_repo(org_id: str) -> str` → `agents-<token>`
  - `source_prefix(org_id: str) -> str` → `agents/<org_id>/`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tenancy.py
import re
import pytest
from dooers_push import tenancy

SA_ID_RE = re.compile(r"^[a-z][-a-z0-9]{4,28}[a-z0-9]$")

def test_token_is_deterministic_and_12_hex():
    t1 = tenancy.org_token("org_ABC-123")
    t2 = tenancy.org_token("org_ABC-123")
    assert t1 == t2
    assert re.fullmatch(r"[0-9a-f]{12}", t1)

def test_distinct_orgs_get_distinct_tokens():
    assert tenancy.org_token("org-a") != tenancy.org_token("org-b")

def test_sa_ids_are_valid_gcp_ids():
    assert SA_ID_RE.fullmatch(tenancy.tenant_sa_id("Org With UPPER & symbols!!"))
    assert SA_ID_RE.fullmatch(tenancy.build_sa_id("Org With UPPER & symbols!!"))

def test_emails_and_repo_and_prefix():
    org = "acme"
    assert tenancy.tenant_sa_email(org, "dooers-agents") == f"tenant-{tenancy.org_token(org)}@dooers-agents.iam.gserviceaccount.com"
    assert tenancy.build_sa_email(org, "dooers-agents") == f"build-{tenancy.org_token(org)}@dooers-agents.iam.gserviceaccount.com"
    assert tenancy.ar_repo(org) == f"agents-{tenancy.org_token(org)}"
    assert tenancy.source_prefix(org) == "agents/acme/"

def test_empty_org_rejected():
    with pytest.raises(ValueError):
        tenancy.org_token("")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tenancy.py -v`
Expected: FAIL — module `tenancy` not found.

- [ ] **Step 3: Implement `tenancy.py`**

```python
"""Pure org-id → GCP resource-name mapping.

Org ids from core are arbitrary strings and won't satisfy GCP's SA / repo
id rules (6-30 chars, ^[a-z]([-a-z0-9]*[a-z0-9])$). We hash them to a stable
12-hex token. The human-readable org id is carried elsewhere (SA description,
`org` label, GCS object prefix).
"""

from __future__ import annotations

import hashlib

_TOKEN_LEN = 12


def org_token(org_id: str) -> str:
    if not org_id:
        raise ValueError("org_id is required")
    return hashlib.sha256(org_id.encode("utf-8")).hexdigest()[:_TOKEN_LEN]


def tenant_sa_id(org_id: str) -> str:
    return f"tenant-{org_token(org_id)}"


def build_sa_id(org_id: str) -> str:
    return f"build-{org_token(org_id)}"


def tenant_sa_email(org_id: str, project: str) -> str:
    return f"{tenant_sa_id(org_id)}@{project}.iam.gserviceaccount.com"


def build_sa_email(org_id: str, project: str) -> str:
    return f"{build_sa_id(org_id)}@{project}.iam.gserviceaccount.com"


def ar_repo(org_id: str) -> str:
    return f"agents-{org_token(org_id)}"


def source_prefix(org_id: str) -> str:
    return f"agents/{org_id}/"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tenancy.py -v && uv run poe typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dooers_push/tenancy.py tests/test_tenancy.py
git commit -m "feat: add tenancy module mapping org id to per-org SA/repo names"
```

---

### Task B2: `env_files.py` — parse `.env` / `env.<env>` from the archive

**Files:**
- Create: `src/dooers_push/env_files.py`
- Test: `tests/test_env_files.py`

**Interfaces:**
- Produces: `parse_env_archive(archive_path: str | Path, env: str) -> dict[str, str]`. Reads top-level `env.<env>` then `.env` from a `.tar.gz`/`.tgz`/`.zip`; later file wins on key conflict (matches the legacy bash order: base < env.<env> < .env).

> Replaces the inline bash `parse_env_file` previously in `gcp/cloudbuild.py:_build_deploy_script`. Semantics preserved: strip surrounding whitespace, skip blank lines and `#` comment lines, strip inline `#...` comments, keep `KEY=VALUE` lines. Archive arcnames are top-level with no wrapper dir (see `dooers-cli` `ignore.make_archive`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_env_files.py
import io
import tarfile
import zipfile
from pathlib import Path

from dooers_push.env_files import parse_env_archive


def _make_targz(tmp_path: Path, files: dict[str, str]) -> Path:
    p = tmp_path / "src.tar.gz"
    with tarfile.open(p, "w:gz") as t:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return p


def test_parses_env_and_strips_comments(tmp_path):
    archive = _make_targz(tmp_path, {
        ".env": "FOO=bar\n# comment\nBAZ=qux  # inline\n\n  SPACED = 1 \n",
    })
    assert parse_env_archive(archive, "prod") == {"FOO": "bar", "BAZ": "qux", "SPACED": "1"}


def test_env_specific_then_dotenv_later_wins(tmp_path):
    archive = _make_targz(tmp_path, {
        "env.prod": "A=from_env_prod\nB=keep",
        ".env": "A=from_dotenv",
    })
    merged = parse_env_archive(archive, "prod")
    assert merged == {"A": "from_dotenv", "B": "keep"}


def test_missing_files_returns_empty(tmp_path):
    archive = _make_targz(tmp_path, {"main.py": "print(1)"})
    assert parse_env_archive(archive, "prod") == {}


def test_zip_supported(tmp_path):
    p = tmp_path / "src.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(".env", "K=V")
    assert parse_env_archive(p, "dev") == {"K": "V"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_env_files.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `env_files.py`**

```python
"""Parse agent env files (`env.<env>`, `.env`) out of the uploaded archive.

Done in Python (in the trusted control plane) instead of inline bash inside
the Cloud Build deploy step, so the build worker never runs the deploy and
the parsing is testable.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path


def _parse_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split("#", 1)[0].strip()  # strip inline comment
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        out[key] = value.strip()
    return out


def _read_members(archive_path: str, names: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    wanted = set(names)
    if archive_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as z:
            for n in z.namelist():
                norm = n.lstrip("./")
                if norm in wanted:
                    found[norm] = z.read(n).decode("utf-8", "replace")
    else:
        with tarfile.open(archive_path, "r:*") as t:
            for m in t.getmembers():
                norm = m.name.lstrip("./")
                if m.isfile() and norm in wanted:
                    f = t.extractfile(m)
                    if f is not None:
                        found[norm] = f.read().decode("utf-8", "replace")
    return found


def parse_env_archive(archive_path: str | Path, env: str) -> dict[str, str]:
    """Return merged env vars: `env.<env>` first, then `.env` (later wins)."""
    order = [f"env.{env}", ".env"]
    contents = _read_members(str(archive_path), order)
    merged: dict[str, str] = {}
    for name in order:
        if name in contents:
            merged.update(_parse_text(contents[name]))
    return merged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_env_files.py -v && uv run poe typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dooers_push/env_files.py tests/test_env_files.py
git commit -m "feat: parse agent env files from the archive in Python"
```

---

### Task B3: Add `google-cloud-run` dependency

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Modify: `uv.lock` (regenerated)

- [ ] **Step 1: Add the dependency**

Add to `[project].dependencies` in `pyproject.toml`:

```toml
    "google-cloud-run>=0.10.0",
```

- [ ] **Step 2: Sync and verify import**

Run:
```bash
uv sync --extra dev
uv run python -c "from google.cloud import run_v2; print(hasattr(run_v2, 'ServicesClient'))"
```
Expected: prints `True`. (Also confirm `run_v2.Service` has an `invoker_iam_disabled` field; if the installed version lacks it, bump the floor — see Task B5 note.)

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add google-cloud-run for the Run Admin API deploy"
```

---

### Task B4: `gcp/cloudbuild.py` — build+push only, per-org repo + SA, return digest

**Files:**
- Modify: `src/dooers_push/gcp/cloudbuild.py`
- Test: `tests/test_cloudbuild_metadata.py` (update), `tests/test_cloudbuild_digest.py` (new)

**Interfaces:**
- Consumes: `tenancy.build_sa_email`, `tenancy.ar_repo` (Task B1).
- Produces:
  - `trigger_build(*, project_id, gcs_uri, agent_id, owner_user_id, org_id, region, env, tag) -> tuple[str, str]` returns `(build_id, image_uri_with_tag)`. (NOTE: `artifact_repo` param is **removed**; `org_id` is **added**.)
  - `BuildWaitResult` gains `image_digest: str | None = None`.
  - `wait_for_build(...)` populates `image_digest` from `build.results.images[0].digest` on success.

- [ ] **Step 1: Update the metadata test (build SA + per-org repo, no deploy step)**

Replace the body of `tests/test_cloudbuild_metadata.py`'s call + assertions:

```python
from dooers_push import tenancy
...
        build_id, image = trigger_build(
            project_id="dooers-agents",
            gcs_uri="gs://bucket/agents/org1/agent-1/123-archive.tar.gz",
            agent_id="agent-1",
            owner_user_id="user-1",
            org_id="org1",
            region="southamerica-east1",
            env="dev",
            tag="latest",
        )
    assert build_id == "build-123"
    assert image == f"southamerica-east1-docker.pkg.dev/dooers-agents/{tenancy.ar_repo('org1')}/agent-agent-1-dev:latest"

    # build is build+push only — exactly two steps, no deploy/run step
    sent_build = client_cls.return_value.create_build.call_args.kwargs["build"]
    assert len(sent_build.steps) == 2
    assert sent_build.service_account.endswith(f"{tenancy.build_sa_id('org1')}@dooers-agents.iam.gserviceaccount.com")
```

- [ ] **Step 2: Write the digest test**

```python
# tests/test_cloudbuild_digest.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from google.cloud.devtools.cloudbuild_v1.types import Build
from dooers_push.gcp.cloudbuild import wait_for_build


def test_wait_for_build_returns_digest_on_success():
    build = Build(status=Build.Status.SUCCESS)
    img = build.results.images.add()
    img.digest = "sha256:deadbeef"

    async def run():
        with patch("dooers_push.gcp.cloudbuild.cloudbuild_v1.services.cloud_build.CloudBuildAsyncClient") as c:
            c.return_value.get_build = AsyncMock(return_value=build)
            return await wait_for_build("bid", "dooers-agents")

    result = asyncio.run(run())
    assert result.success is True
    assert result.image_digest == "sha256:deadbeef"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_cloudbuild_metadata.py tests/test_cloudbuild_digest.py -v`
Expected: FAIL (signature mismatch / no `image_digest`).

- [ ] **Step 4: Implement the changes**

In `gcp/cloudbuild.py`:
- Add `from dooers_push import tenancy`.
- Reduce `_STEP_LABELS` to `("build image", "push image")`.
- Delete `_build_deploy_script` entirely.
- Change `trigger_build` signature: drop `artifact_repo`, add `org_id: str`. Build the image URI from `tenancy.ar_repo(org_id)`; set `service_account` from `tenancy.build_sa_email(org_id, project_id)`; steps are only `build` and `push`:

```python
def trigger_build(
    *,
    project_id: str,
    gcs_uri: str,
    agent_id: str,
    owner_user_id: str,
    org_id: str,
    region: str,
    env: str,
    tag: str,
) -> tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"invalid gcs uri: {gcs_uri}")
    _, rest = gcs_uri.split("gs://", 1)
    bucket, object_path = rest.split("/", 1)

    service_name = _service_name(agent_id, env)
    repo = tenancy.ar_repo(org_id)
    image = f"{region}-docker.pkg.dev/{project_id}/{repo}/{service_name}:{tag}"
    service_account = (
        f"projects/{project_id}/serviceAccounts/"
        f"{tenancy.build_sa_email(org_id, project_id)}"
    )
    source = cloudbuild_v1.Source(
        storage_source=cloudbuild_v1.StorageSource(bucket=bucket, object_=object_path)
    )
    build = cloudbuild_v1.Build(
        source=source,
        steps=[
            cloudbuild_v1.BuildStep(name="gcr.io/cloud-builders/docker", args=["build", "-t", image, "."]),
            cloudbuild_v1.BuildStep(name="gcr.io/cloud-builders/docker", args=["push", image]),
        ],
        images=[image],
        service_account=service_account,
        tags=[
            f"agent-{gcp_label_value(agent_id)}",
            f"owner-{gcp_label_value(owner_user_id)}",
            f"org-{gcp_label_value(org_id)}",
            f"env-{gcp_label_value(env)}",
        ],
        options=cloudbuild_v1.BuildOptions(
            machine_type=cloudbuild_v1.BuildOptions.MachineType.N1_HIGHCPU_8,
            logging="CLOUD_LOGGING_ONLY",
        ),
        timeout={"seconds": 1800},
    )
    client = cloudbuild_v1.services.cloud_build.CloudBuildClient()  # type: ignore[attr-defined]
    op = client.create_build(project_id=project_id, build=build)
    metadata = op.metadata
    if metadata is None or metadata.build is None or not metadata.build.id:
        raise RuntimeError("create_build returned operation without build metadata")
    build_id = metadata.build.id
    logger.info("triggered cloud build: id=%s image=%s", build_id, image)
    return build_id, image
```

- Add `image_digest: str | None = None` to `BuildWaitResult`.
- In `wait_for_build`, on `Build.Status.SUCCESS`, read the digest:

```python
        if build.status in (Build.Status.SUCCESS,):
            digest = None
            if build.results and build.results.images:
                digest = build.results.images[0].digest or None
            return BuildWaitResult(success=True, build_log_url=log_url, image_digest=digest)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cloudbuild_metadata.py tests/test_cloudbuild_digest.py tests/test_cloudbuild_failure.py -v`
Expected: PASS (fix any failure-test fallout from `_STEP_LABELS` change).

- [ ] **Step 6: Commit**

```bash
git add src/dooers_push/gcp/cloudbuild.py tests/test_cloudbuild_metadata.py tests/test_cloudbuild_digest.py
git commit -m "feat: cloud build does build+push only, per-org repo/SA, returns digest"
```

---

### Task B5: `gcp/cloudrun.py` — deploy via Run Admin API as the tenant SA

**Files:**
- Create: `src/dooers_push/gcp/cloudrun.py`
- Test: `tests/test_cloudrun_deploy.py`

**Interfaces:**
- Consumes: `google.cloud.run_v2` (Task B3).
- Produces:
  - `build_image_ref(region, project, org_id, service_name, digest) -> str` → `<region>-docker.pkg.dev/<project>/agents-<token>/<service>@<digest>`.
  - `deploy_service(*, project, region, service_name, image_ref, service_account, env_vars: dict[str,str], labels: dict[str,str]) -> str` — create-or-update the Cloud Run service running as `service_account`, returns the service URI.

> `invoker_iam_disabled=True` replicates `--no-invoker-iam-check` (public via the LB without an `allUsers` binding, required under Domain Restricted Sharing). If the installed `run_v2.Service` lacks that field, bump `google-cloud-run` (Task B3) until it's present — do not fall back to an `allUsers` binding.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cloudrun_deploy.py
from unittest.mock import MagicMock, patch

from google.api_core.exceptions import NotFound
from dooers_push.gcp.cloudrun import build_image_ref, deploy_service
from dooers_push import tenancy


def test_build_image_ref():
    ref = build_image_ref("southamerica-east1", "dooers-agents", "org1", "agent-x-dev", "sha256:abc")
    assert ref == f"southamerica-east1-docker.pkg.dev/dooers-agents/{tenancy.ar_repo('org1')}/agent-x-dev@sha256:abc"


def test_deploy_creates_when_missing_and_sets_tenant_sa():
    client = MagicMock()
    client.get_service.side_effect = NotFound("nope")
    op = MagicMock()
    op.result.return_value = MagicMock(uri="https://agent-x-dev-abc.run.app")
    client.create_service.return_value = op

    with patch("dooers_push.gcp.cloudrun.run_v2.ServicesClient", return_value=client):
        uri = deploy_service(
            project="dooers-agents",
            region="southamerica-east1",
            service_name="agent-x-dev",
            image_ref="img@sha256:abc",
            service_account="tenant-abc@dooers-agents.iam.gserviceaccount.com",
            env_vars={"FOO": "bar"},
            labels={"org": "org1"},
        )

    assert uri == "https://agent-x-dev-abc.run.app"
    sent = client.create_service.call_args.kwargs["service"]
    assert sent.template.service_account == "tenant-abc@dooers-agents.iam.gserviceaccount.com"
    assert sent.template.containers[0].image == "img@sha256:abc"
    assert {e.name: e.value for e in sent.template.containers[0].env} == {"FOO": "bar"}
    assert sent.invoker_iam_disabled is True


def test_deploy_updates_when_present():
    client = MagicMock()
    client.get_service.return_value = MagicMock()  # exists
    op = MagicMock()
    op.result.return_value = MagicMock(uri="https://x.run.app")
    client.update_service.return_value = op
    with patch("dooers_push.gcp.cloudrun.run_v2.ServicesClient", return_value=client):
        deploy_service(
            project="dooers-agents", region="southamerica-east1", service_name="agent-x-dev",
            image_ref="img@sha256:abc", service_account="tenant-abc@dooers-agents.iam.gserviceaccount.com",
            env_vars={}, labels={},
        )
    client.update_service.assert_called_once()
    client.create_service.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cloudrun_deploy.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `gcp/cloudrun.py`**

```python
"""Deploy agent Cloud Run services via the Run Admin API.

Deploy runs in the trusted control plane (not in the Cloud Build worker), so
the user-controlled build never shares a credential with deploy. The service
runs as the per-org tenant SA. Image is pinned by digest.
"""

from __future__ import annotations

import logging

from google.api_core.exceptions import NotFound
from google.cloud import run_v2
from google.protobuf import duration_pb2

from dooers_push import tenancy

logger = logging.getLogger(__name__)


def build_image_ref(region: str, project: str, org_id: str, service_name: str, digest: str) -> str:
    repo = tenancy.ar_repo(org_id)
    return f"{region}-docker.pkg.dev/{project}/{repo}/{service_name}@{digest}"


def deploy_service(
    *,
    project: str,
    region: str,
    service_name: str,
    image_ref: str,
    service_account: str,
    env_vars: dict[str, str],
    labels: dict[str, str],
) -> str:
    client = run_v2.ServicesClient()
    parent = f"projects/{project}/locations/{region}"
    full_name = f"{parent}/services/{service_name}"

    container = run_v2.Container(
        image=image_ref,
        env=[run_v2.EnvVar(name=k, value=v) for k, v in env_vars.items()],
        resources=run_v2.ResourceRequirements(
            limits={"cpu": "1", "memory": "512Mi"},
            cpu_idle=True,            # CPU only during requests (matches prior deploy)
            startup_cpu_boost=True,   # == --cpu-boost
        ),
    )
    template = run_v2.RevisionTemplate(
        service_account=service_account,
        containers=[container],
        scaling=run_v2.RevisionScaling(min_instance_count=1, max_instance_count=3),
        timeout=duration_pb2.Duration(seconds=300),
    )
    service = run_v2.Service(
        template=template,
        labels=labels,
        ingress=run_v2.IngressTraffic.INGRESS_TRAFFIC_ALL,
        invoker_iam_disabled=True,
    )

    try:
        client.get_service(name=full_name)
        service.name = full_name
        op = client.update_service(service=service)
    except NotFound:
        op = client.create_service(parent=parent, service=service, service_id=service_name)
    result = op.result(timeout=300)
    logger.info("deployed cloud run service=%s sa=%s", service_name, service_account)
    return result.uri
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cloudrun_deploy.py -v && uv run poe typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dooers_push/gcp/cloudrun.py tests/test_cloudrun_deploy.py
git commit -m "feat: deploy agent Cloud Run via Run Admin API as the tenant SA"
```

---

### Task B6: `storage.py` — per-org prefix + org metadata

**Files:**
- Modify: `src/dooers_push/storage.py`
- Test: `tests/test_storage_prefix.py`

**Interfaces:**
- Consumes: `tenancy.source_prefix` (Task B1).
- Produces: `upload_archive(settings, agent_id, archive, owner_user_id, org_id) -> str` (param `org_id` **added**). Object path becomes `agents/<org_id>/<agent_id>/<ts>-<file>`; blob metadata gains `organization_id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage_prefix.py
import asyncio
from unittest.mock import MagicMock, patch

from dooers_push import storage
from dooers_push.settings import Settings


def _settings():
    return Settings(
        gcp_project_id="dooers-agents", gcp_region="southamerica-east1",
        bucket_name="dooers-agents-src", artifact_repo="agents",
        core_api_url="http://core", environment="dev", request_timeout=10,
        lb_domain="agents.dooers.ai", lb_url_map="agents-lb", lb_region="southamerica-east1",
    )


def test_upload_uses_per_org_prefix_and_metadata(tmp_path):
    archive = MagicMock()
    archive.filename = "src.tar.gz"
    chunks = [b"data", b""]
    async def _read(_n): return chunks.pop(0)
    archive.read = _read

    blob = MagicMock()
    bucket = MagicMock(); bucket.blob.return_value = blob
    client = MagicMock(); client.bucket.return_value = bucket

    with patch("dooers_push.storage.storage.Client", return_value=client):
        uri = asyncio.run(storage.upload_archive(_settings(), "agent-1", archive, owner_user_id="u1", org_id="org1"))

    object_path = bucket.blob.call_args.args[0]
    assert object_path.startswith("agents/org1/agent-1/")
    assert blob.metadata["organization_id"] == "org1"
    assert uri == f"gs://dooers-agents-src/{object_path}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage_prefix.py -v`
Expected: FAIL — `upload_archive()` missing `org_id`.

- [ ] **Step 3: Implement the change**

In `storage.py`, add `org_id: str` to the signature and build the path with the per-org prefix + metadata:

```python
async def upload_archive(
    settings: Settings,
    agent_id: str,
    archive: UploadFile,
    owner_user_id: str,
    org_id: str,
) -> str:
    ...
    from dooers_push import tenancy
    object_path = f"{tenancy.source_prefix(org_id)}{agent_id}/{ts}-{filename}"
    ...
    blob.metadata = {
        "agent_id": agent_id,
        "owner_user_id": owner_user_id,
        "organization_id": org_id,
        "pushed_at": str(ts),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_storage_prefix.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dooers_push/storage.py tests/test_storage_prefix.py
git commit -m "feat: store agent source under a per-org prefix with org metadata"
```

---

### Task B7: `pipeline/base.py` + `pipeline/deployer.py` — orchestrate build → digest → deploy

**Files:**
- Modify: `src/dooers_push/pipeline/base.py` (context fields)
- Modify: `src/dooers_push/pipeline/deployer.py`
- Test: `tests/test_deployer_flow.py`

**Interfaces:**
- Consumes: updated `trigger_build`/`wait_for_build` (B4), `cloudrun.deploy_service`/`build_image_ref` (B5), `LBManager` (existing).
- Produces: `PipelineContext` gains `env_overrides: dict[str, str] = {}` and `image_digest: str | None = None`. `DeployerStep.run` orchestrates: trigger build → wait → on success capture digest → `deploy_service(... service_account=tenant SA ...)` → LB register.

- [ ] **Step 1: Add context fields**

In `pipeline/base.py`, add to `PipelineContext`:

```python
    env_overrides: dict[str, str] = {}
    image_digest: str | None = None
```

- [ ] **Step 2: Write the failing deployer test**

```python
# tests/test_deployer_flow.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from dooers.protocol.agents import AgentRecord
from dooers.protocol.auth import AuthSession
from dooers.protocol.push import BuildStatus

from dooers_push.gcp.cloudbuild import BuildWaitResult
from dooers_push.pipeline.base import PipelineContext
from dooers_push.pipeline.deployer import DeployerStep
from dooers_push.settings import Settings


def _settings():
    return Settings(
        gcp_project_id="dooers-agents", gcp_region="southamerica-east1",
        bucket_name="dooers-agents-src", artifact_repo="agents",
        core_api_url="http://core", environment="dev", request_timeout=10,
        lb_domain="agents.dooers.ai", lb_url_map="agents-lb", lb_region="southamerica-east1",
    )


def _ctx():
    return PipelineContext(
        agent=AgentRecord(agent_id="a1", name="a1", owner_user_id="u1", organization_id="org1", host_url=None),
        user=AuthSession(user_id="u1"),
        gcs_uri="gs://dooers-agents-src/agents/org1/a1/1-x.tar.gz",
        tag="latest", env="dev", env_overrides={"FOO": "bar"},
    )


def test_deployer_deploys_as_tenant_sa_by_digest():
    step = DeployerStep(_settings())
    step.lb.register_agent = AsyncMock(return_value="https://agents.dooers.ai/a1-dev")
    step.lb.wait_until_reachable = AsyncMock(return_value=None)

    with patch("dooers_push.pipeline.deployer.trigger_build", return_value=("bid", "img:latest")), \
         patch("dooers_push.pipeline.deployer.wait_for_build", new=AsyncMock(return_value=BuildWaitResult(success=True, image_digest="sha256:abc"))), \
         patch("dooers_push.pipeline.deployer.deploy_service", return_value="https://a1-dev.run.app") as deploy:
        result = asyncio.run(step.run(_ctx()))

    assert result.status == BuildStatus.succeeded
    kwargs = deploy.call_args.kwargs
    assert kwargs["service_account"].startswith("tenant-")
    assert kwargs["image_ref"].endswith("@sha256:abc")
    assert kwargs["env_vars"]["FOO"] == "bar"
    assert kwargs["env_vars"]["GCP_PROJECT_ID"] == "dooers-agents"
    assert kwargs["labels"]["org"] == "org1"


def test_deployer_fails_when_no_digest():
    step = DeployerStep(_settings())
    with patch("dooers_push.pipeline.deployer.trigger_build", return_value=("bid", "img:latest")), \
         patch("dooers_push.pipeline.deployer.wait_for_build", new=AsyncMock(return_value=BuildWaitResult(success=True, image_digest=None))):
        result = asyncio.run(step.run(_ctx()))
    assert result.status == BuildStatus.failed
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_deployer_flow.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement the deployer orchestration**

Rewrite `DeployerStep.run` so Phase 1 builds (build+push) then deploys via the Run API:

```python
from dooers_push import tenancy
from dooers_push.gcp.cloudbuild import build_log_url, trigger_build, wait_for_build
from dooers_push.gcp.cloudrun import build_image_ref, deploy_service
from dooers_push.gcp.cloudbuild import cloud_run_service_name

    async def run(self, ctx: PipelineContext) -> StepResult:
        org_id = ctx.agent.organization_id or ""
        try:
            build_id, _image = trigger_build(
                project_id=self.settings.gcp_project_id,
                gcs_uri=ctx.gcs_uri,
                agent_id=ctx.agent.agent_id,
                owner_user_id=ctx.user.user_id,
                org_id=org_id,
                region=self.settings.gcp_region,
                env=ctx.env,
                tag=ctx.tag,
            )
            ctx.build_id = build_id
            build_result = await wait_for_build(build_id, project_id=self.settings.gcp_project_id)
            if not build_result.success:
                return StepResult(status=BuildStatus.failed, error=build_result.error or "Cloud Build reported failure",
                                  failed_step=build_result.failed_step, build_log_url=build_result.build_log_url)
            if not build_result.image_digest:
                return StepResult(status=BuildStatus.failed, error="build succeeded but no image digest was returned",
                                  build_log_url=build_result.build_log_url)
            ctx.image_digest = build_result.image_digest
        except TimeoutError as e:
            log_url = build_log_url(ctx.build_id, self.settings.gcp_project_id) if ctx.build_id else None
            return StepResult(status=BuildStatus.failed, error=str(e), build_log_url=log_url)
        except ValueError as e:
            return StepResult(status=BuildStatus.failed, error=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("build phase crashed")
            log_url = build_log_url(ctx.build_id, self.settings.gcp_project_id) if ctx.build_id else None
            return StepResult(status=BuildStatus.failed, error=f"build error: {e}", build_log_url=log_url)

        # Phase 2: deploy via Run Admin API as the per-org tenant SA, pinned by digest.
        service_name = cloud_run_service_name(ctx.agent.agent_id, ctx.env)
        image_ref = build_image_ref(self.settings.gcp_region, self.settings.gcp_project_id, org_id, service_name, ctx.image_digest)
        ctx.image = image_ref
        env_vars = {
            "GCP_PROJECT_ID": self.settings.gcp_project_id,
            "GCP_REGION": self.settings.gcp_region,
            "ENVIRONMENT": ctx.env,
            **ctx.env_overrides,
        }
        labels = {
            "agent_id": ctx.agent.agent_id,
            "owner_user_id": ctx.user.user_id,
            "org": tenancy.org_token(org_id),
            "env": ctx.env,
        }
        try:
            deploy_service(
                project=self.settings.gcp_project_id,
                region=self.settings.gcp_region,
                service_name=service_name,
                image_ref=image_ref,
                service_account=tenancy.tenant_sa_email(org_id, self.settings.gcp_project_id),
                env_vars=env_vars,
                labels=labels,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("deploy phase crashed")
            return StepResult(status=BuildStatus.failed, error=f"deploy error: {e}",
                              build_log_url=build_log_url(ctx.build_id, self.settings.gcp_project_id) if ctx.build_id else None)

        # Phase 3: LB registration (unchanged)
        try:
            lb_url = await self.lb.register_agent(ctx.agent.agent_id, ctx.env)
            await self.lb.wait_until_reachable(lb_url)
            ctx.lb_url = lb_url
            return StepResult(status=BuildStatus.succeeded)
        except LBError as e:
            logger.exception("LB registration failed")
            return StepResult(status=BuildStatus.failed, error=f"LB registration failed: {e}",
                              build_log_url=build_log_url(ctx.build_id, self.settings.gcp_project_id) if ctx.build_id else None)
```

> Note: the `labels["org"]` uses the hashed token (label values must be ≤63 chars and `[a-z0-9_-]`); the raw org id lives in GCS metadata + the SA description. If you prefer the raw org id as a label, sanitize it to the GCP label charset first.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_deployer_flow.py tests/ -v`
Expected: PASS (update any other deployer-touching tests).

- [ ] **Step 6: Commit**

```bash
git add src/dooers_push/pipeline/base.py src/dooers_push/pipeline/deployer.py tests/test_deployer_flow.py
git commit -m "feat: deployer builds then deploys via Run API as tenant SA by digest"
```

---

### Task B8: `main.py` — thread org id, parse env, precheck provisioning

**Files:**
- Modify: `src/dooers_push/main.py`
- Test: `tests/test_push_org_provisioned.py`

**Interfaces:**
- Consumes: `tenancy.tenant_sa_email` (B1), `env_files.parse_env_archive` (B2), updated `storage.upload_archive` (B6).
- Produces: the `/v1/push/{agent_id}` route now (a) parses env from the uploaded archive, (b) rejects with 403 `org_not_provisioned` when the tenant SA doesn't exist, (c) passes `org_id` + `env_overrides` into the context.

> Provisioning precheck: use `google.cloud.iam_admin_v1` `get_service_account` and catch `NotFound`, OR (simpler, no new dep) `google.api_core` call against the IAM API. Wrap in a small `tenancy.tenant_sa_exists(org_id, project)` helper so it's mockable. Add a unit test that mocks it both ways.

- [ ] **Step 1: Add a mockable existence helper to `tenancy.py`**

```python
def tenant_sa_exists(org_id: str, project: str) -> bool:
    from google.api_core.exceptions import NotFound
    from google.cloud import iam_admin_v1
    client = iam_admin_v1.IAMClient()
    name = f"projects/{project}/serviceAccounts/{tenant_sa_email(org_id, project)}"
    try:
        client.get_service_account(name=name)
        return True
    except NotFound:
        return False
```

Add `google-cloud-iam>=2.12.0` to `pyproject.toml` dependencies and `uv sync --extra dev`.

- [ ] **Step 2: Write the failing route test**

```python
# tests/test_push_org_provisioned.py
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import io

from dooers_push.main import app


def _auth_headers():
    return {"Authorization": "Bearer t"}


def test_push_rejected_when_org_not_provisioned(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "dooers-agents")
    monkeypatch.setenv("BUCKET_NAME", "dooers-agents-src")
    # ... set other required env vars ...
    with patch("dooers_push.main.verify_session", new=AsyncMock()), \
         patch("dooers_push.main.CoreClient") as core_cls, \
         patch("dooers_push.tenancy.tenant_sa_exists", return_value=False):
        core = core_cls.return_value
        core.get_agent = AsyncMock(return_value=type("A", (), {"agent_id": "a1", "organization_id": "org1"})())
        core.get_organization = AsyncMock(return_value=type("O", (), {"settings": type("S", (), {"features": type("F", (), {"hosting": True})()})()})())
        client = TestClient(app)
        files = {"archive": ("src.tar.gz", io.BytesIO(b"x"), "application/gzip")}
        r = client.post("/v1/push/a1", headers=_auth_headers(), files=files)
        assert r.status_code == 403
        assert r.json()["error_code"] == "org_not_provisioned"
```

> This test is illustrative — adapt the `CoreClient`/`verify_session` mocks to the existing test style in `tests/test_smoke.py` / `tests/test_push_hosting_gate.py`. The key assertions: 403 + `org_not_provisioned` when `tenant_sa_exists` is False.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_push_org_provisioned.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement the route changes**

In `main.py`, after the hosting gate and before upload, add the provisioning precheck and env parsing, and pass `org_id` + `env_overrides` through. The archive must be parsed before/around upload — read it into a temp file once, parse env from it, then upload. Minimal change: parse from the same temp path `storage.upload_archive` writes (refactor `upload_archive` to also return parsed env, OR parse the `UploadFile` separately). Recommended: have the route save the upload to a temp file, call `parse_env_archive(tmp, env)`, then pass the temp path to a thin `upload_archive` variant. Concretely:

```python
    org_id = agent.organization_id
    if not tenancy.tenant_sa_exists(org_id, settings.gcp_project_id):
        raise HTTPException(status_code=403, detail="your organization is not provisioned for hosting")

    # persist upload once, parse env, then upload to GCS
    env_overrides = {}
    gcs_uri = await storage.upload_archive(settings, agent_id, archive, owner_user_id=session.user_id, org_id=org_id)
    # parse env from the archive bytes the client sent (re-read or capture during upload)
    # simplest: storage.upload_archive returns (uri, tmp_path) or accept env parsing inside it.
```

Implementation choice (pick one and keep it tested): extend `storage.upload_archive` to also return the local temp path (or the parsed env dict) so the route can build `env_overrides` without re-reading the network stream. Update the `PipelineContext(...)` construction to pass `env_overrides=env_overrides`. Map the `HTTPException(403, ...)` to the `org_not_provisioned` code in `_error_code_for_status` (add `403`-with-this-detail handling, or raise a typed exception the envelope handler maps to `ErrorCode.org_not_provisioned`).

> Cleanest: change `_error_code_for_status` is keyed only on status. Instead, build the `ErrorEnvelope` for this specific case directly: return a `JSONResponse(ErrorEnvelope(error_code=ErrorCode.org_not_provisioned, message=..., correlation_id=...).model_dump(mode="json"), status_code=403)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/dooers_push/main.py src/dooers_push/tenancy.py src/dooers_push/storage.py pyproject.toml uv.lock tests/test_push_org_provisioned.py
git commit -m "feat: precheck org provisioning + parse env, thread org id into pipeline"
```

---

### Task B9: `provision.py` — idempotent per-org provisioning CLI

**Files:**
- Create: `src/dooers_push/provision.py`
- Test: `tests/test_provision.py`

**Interfaces:**
- Consumes: `tenancy.*` (B1).
- Produces: `python -m dooers_push.provision <org_id> [--project dooers-agents] [--region southamerica-east1] [--bucket dooers-agents-src] [--control-plane-sa dooers-push-runtime@dooers-services.iam.gserviceaccount.com]`. Idempotently creates the two SAs, the AR repo, and all bindings by shelling out to `gcloud` (check-then-create). A `--dry-run` prints the commands without executing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_provision.py
from unittest.mock import patch
from dooers_push import provision, tenancy


def test_dry_run_emits_expected_resources(capsys):
    provision.main(["org1", "--project", "dooers-agents", "--region", "southamerica-east1",
                    "--bucket", "dooers-agents-src", "--control-plane-sa", "cp@x.iam.gserviceaccount.com",
                    "--dry-run"])
    out = capsys.readouterr().out
    assert tenancy.tenant_sa_id("org1") in out
    assert tenancy.build_sa_id("org1") in out
    assert tenancy.ar_repo("org1") in out
    assert "agents/org1/" in out  # the source-prefix IAM condition


def test_apply_invokes_gcloud_idempotently():
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    with patch("dooers_push.provision.subprocess.run", side_effect=fake_run):
        provision.main(["org1", "--project", "dooers-agents", "--region", "southamerica-east1",
                        "--bucket", "dooers-agents-src", "--control-plane-sa", "cp@x.iam.gserviceaccount.com"])
    flat = " ".join(" ".join(c) for c in calls)
    assert "iam service-accounts create" in flat
    assert "artifacts repositories create" in flat
    assert "add-iam-policy-binding" in flat
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_provision.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `provision.py`**

```python
"""Idempotent per-org provisioning: tenant + build SAs, AR repo, bindings.

Run by an operator (or later by core when an org gains hosting). NOT run by
the request-serving control plane — keeping SA-admin out of the hot path.

  python -m dooers_push.provision <org_id> --project dooers-agents \
      --region southamerica-east1 --bucket dooers-agents-src \
      --control-plane-sa dooers-push-runtime@dooers-services.iam.gserviceaccount.com
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from dooers_push import tenancy


def _cmds(org_id, project, region, bucket, control_plane_sa):
    tenant = tenancy.tenant_sa_email(org_id, project)
    build = tenancy.build_sa_email(org_id, project)
    repo = tenancy.ar_repo(org_id)
    prefix = f"projects/_/buckets/{bucket}/objects/{tenancy.source_prefix(org_id)}"
    serverless_robot = None  # resolved at runtime via project number; see note below
    cmds: list[list[str]] = []

    # 1. service accounts (create is not idempotent → guarded by describe in apply())
    cmds.append(["gcloud", "iam", "service-accounts", "create", tenancy.tenant_sa_id(org_id),
                 f"--project={project}", f"--description={org_id}", f"--display-name={org_id}"])
    cmds.append(["gcloud", "iam", "service-accounts", "create", tenancy.build_sa_id(org_id),
                 f"--project={project}", f"--description={org_id}", f"--display-name={org_id}"])

    # 2. per-org Artifact Registry repo
    cmds.append(["gcloud", "artifacts", "repositories", "create", repo,
                 "--repository-format=docker", f"--location={region}", f"--project={project}",
                 f"--description={org_id}"])

    # 3. build SA roles
    cmds.append(["gcloud", "artifacts", "repositories", "add-iam-policy-binding", repo,
                 f"--location={region}", f"--project={project}",
                 f"--member=serviceAccount:{build}", "--role=roles/artifactregistry.writer"])
    cmds.append(["gcloud", "projects", "add-iam-policy-binding", project,
                 f"--member=serviceAccount:{build}", "--role=roles/logging.logWriter"])
    cmds.append(["gcloud", "storage", "buckets", "add-iam-policy-binding", f"gs://{bucket}",
                 f"--member=serviceAccount:{build}", "--role=roles/storage.objectViewer",
                 f"--condition=^:^title=src-{tenancy.org_token(org_id)}:expression=resource.name.startsWith(\"{prefix}\")"])

    # 4. tenant SA roles
    cmds.append(["gcloud", "projects", "add-iam-policy-binding", project,
                 f"--member=serviceAccount:{tenant}", "--role=roles/logging.logWriter"])

    # 5. control plane actAs both per-org SAs
    for sa in (build, tenant):
        cmds.append(["gcloud", "iam", "service-accounts", "add-iam-policy-binding", sa,
                     f"--project={project}", f"--member=serviceAccount:{control_plane_sa}",
                     "--role=roles/iam.serviceAccountUser"])
    return cmds, repo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dooers_push.provision")
    ap.add_argument("org_id")
    ap.add_argument("--project", default="dooers-agents")
    ap.add_argument("--region", default="southamerica-east1")
    ap.add_argument("--bucket", default="dooers-agents-src")
    ap.add_argument("--control-plane-sa", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cmds, repo = _cmds(args.org_id, args.project, args.region, args.bucket, args.control_plane_sa)
    print(f"# provisioning org={args.org_id} tenant={tenancy.tenant_sa_id(args.org_id)} "
          f"build={tenancy.build_sa_id(args.org_id)} repo={repo} prefix={tenancy.source_prefix(args.org_id)}")
    for cmd in cmds:
        print(" ".join(cmd))
        if not args.dry_run:
            # create commands fail if the resource exists → tolerate that (idempotent)
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0 and "already exists" not in (r.stderr or "").lower():
                print(r.stderr, file=sys.stderr)
                return r.returncode
    # NOTE: also grant the Cloud Run serverless robot artifactregistry.reader on `repo`
    # (resolve service-<projnum>@serverless-robot-prod.iam.gserviceaccount.com) — add here.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

> Implementation note for the engineer: add the serverless-robot `artifactregistry.reader` grant on `repo` (resolve the project number via `gcloud projects describe <project> --format='value(projectNumber)'`). The IAM-condition flag quoting (`--condition`) is fiddly; the test asserts the prefix string is present — verify the real `gcloud` accepts it during Phase D dry-run before applying.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_provision.py -v && uv run poe typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dooers_push/provision.py tests/test_provision.py
git commit -m "feat: idempotent per-org provisioning CLI (SAs, AR repo, bindings)"
```

---

### Task B10: Update `dooers-push` docs + run full suite + release

**Files:**
- Modify: `docs/gcp-push-deploy.md`
- Modify: `pyproject.toml` (version bump), `RELEASE.md` if needed

- [ ] **Step 1: Rewrite the identity section of `gcp-push-deploy.md`**

Replace the single-`agent-deploy-service` model with: the per-stage table from the spec, the `provision-org` step, the per-org AR repo + GCS prefix, deploy-via-Run-API, and the defanged `agent-deploy-service`. Add the §8 verification matrix.

- [ ] **Step 2: Full suite green**

Run: `uv run poe dev`
Expected: lint + mypy + all tests PASS.

- [ ] **Step 3: Commit + tag for release**

```bash
git add docs/gcp-push-deploy.md pyproject.toml
git commit -m "docs+build: per-tenant isolation model; bump dooers-push version"
```
(Coordinate the `dooers-protocol` release from Task A1 first so `dooers-push` can pin the new version — see `pypi-release-process`.)

---

## Phase C — `dooers-cli`: surface the new error

### Task C1: Friendly message for `org_not_provisioned`

**Files:**
- Modify: `packages/dooers-cli/src/dooers/cli/push_client.py` (or wherever push errors are mapped to messages)
- Test: `packages/dooers-cli/tests/test_push_failure_display.py`

**Interfaces:**
- Consumes: `ErrorCode.org_not_provisioned` (Task A1).
- Produces: when the push service returns `org_not_provisioned`, the CLI prints an actionable line instead of a raw error.

- [ ] **Step 1: Write the failing test**

```python
# add to packages/dooers-cli/tests/test_push_failure_display.py
def test_org_not_provisioned_message():
    from dooers.cli.push_client import friendly_push_error  # or the existing mapper
    msg = friendly_push_error("org_not_provisioned", "…")
    assert "not provisioned for hosting" in msg.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/dooers-cli && uv run pytest tests/test_push_failure_display.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the mapping**

In the push client's error handling, special-case the code:

```python
if error_code == "org_not_provisioned":
    return ("Your organization isn't set up for agent hosting yet. "
            "Contact Dooers to enable hosting for your org.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/dooers-cli && uv run poe dev`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/dooers-cli/src/dooers/cli/push_client.py packages/dooers-cli/tests/test_push_failure_display.py
git commit -m "feat(cli): friendly message for org_not_provisioned push errors"
```

---

## Phase D — GCP infra migration (OPERATOR-RUN, gated on team approval)

> These are **not** automated by the code above. They are run by an operator with owner on both projects **after** the code is merged and the new `dooers-push` image is built. Every step is additive until Phase D5. Shell vars:
>
> ```bash
> AGENTS=dooers-agents; SERVICES=dooers-services; REGION=southamerica-east1
> BUCKET=dooers-agents-src
> CP=dooers-push-runtime@$SERVICES.iam.gserviceaccount.com
> ```

### D0 — prep (read-only)
- [ ] Confirm APIs enabled on `$AGENTS`: `iam`, `run`, `artifactregistry`, `cloudbuild`, `storage`, `iamcredentials` (for impersonation checks).
- [ ] For each of the 4 live agents, resolve its `organizationId` (query core, or read the agent record). Record the distinct org ids → call them `$ORG1 …`.
- [ ] Snapshot current `agent-deploy-service` bindings for rollback: `gcloud projects get-iam-policy $AGENTS --flatten=bindings[].members --filter="bindings.members:agent-deploy-service" --format="table(bindings.role)"`.

### D1 — provision each existing org (additive)
- [ ] For each distinct org: `python -m dooers_push.provision <ORG> --project $AGENTS --region $REGION --bucket $BUCKET --control-plane-sa $CP --dry-run` → review, then run without `--dry-run`.
- [ ] Verify: the two SAs exist, repo `agents-<token>` exists, build SA has the conditioned bucket read, control plane has `serviceAccountUser` on both.

### D2 — control-plane roles (additive)
- [ ] `gcloud projects add-iam-policy-binding $AGENTS --member=serviceAccount:$CP --role=roles/run.developer`
- [ ] Downgrade the bucket grant: add `roles/storage.objectCreator` for `$CP` on `gs://$BUCKET`, then remove `roles/storage.objectAdmin` for `$CP` (add-then-remove, never leave it unable to upload).

### D3 — deploy the new control plane
- [ ] Build the new `dooers-push` image and deploy to `$SERVICES` (same command as `gcp-push-deploy.md` Phase B). No new env vars are required (org id comes from core).

### D4 — migrate the 4 live agents
- [ ] **Immediate (closes the runtime hole):** for each agent service, switch its runtime SA to its tenant SA:
  `gcloud run services update <svc> --region=$REGION --project=$AGENTS --service-account=tenant-<token>@$AGENTS.iam.gserviceaccount.com`
  (The image stays in the old shared repo transiently — only the runtime identity matters for the hole.)
- [ ] **Full move (optional):** re-`dooers push` each agent so its image lands in `agents-<token>` via the new pipeline.

### D5 — defang `agent-deploy-service` (the actual privilege removal)
- [ ] Confirm no Cloud Run service still runs as `agent-deploy-service`: `gcloud run services list --project=$AGENTS --format="table(metadata.name, spec.template.spec.serviceAccountName)"`.
- [ ] Remove its excess bindings (keep the SA itself):
  `for R in roles/run.admin roles/storage.objectViewer roles/artifactregistry.writer roles/iam.serviceAccountUser; do gcloud projects remove-iam-policy-binding $AGENTS --member=serviceAccount:agent-deploy-service@$AGENTS.iam.gserviceaccount.com --role=$R; done`
- [ ] Remove the control-plane `serviceAccountUser` binding on `agent-deploy-service` (no longer used).

### D6 — verify (the §8 matrix from the spec)
- [ ] Each agent service `serviceAccountName` is its `tenant-<token>` (not `agent-deploy-service`).
- [ ] Impersonation denials (grant the operator `serviceAccountTokenCreator` temporarily, remove after):
  - `tenant-<orgA>` cannot read `gs://$BUCKET/agents/<orgB>/…`.
  - `tenant-<orgA>` cannot `gcloud run services update`/`delete` another org's service.
  - `build-<orgA>` cannot read `gs://$BUCKET/agents/<orgB>/…`, cannot push to `agents-<orgB>`, cannot `gcloud run deploy`.
- [ ] A fresh `dooers push` for a provisioned org succeeds, the service runs as `tenant-<token>`, and the image is in `agents-<token>`.
- [ ] A `dooers push` for an unprovisioned org returns a clean `org_not_provisioned` 403.
- [ ] `agent-deploy-service` no longer holds `run.admin`/`storage.objectViewer`/`artifactregistry.writer`/`serviceAccountUser`.

**Rollback:** re-add the removed `agent-deploy-service` bindings (from the D0 snapshot); redeploy the previous `dooers-push` image; `gcloud run services update <svc> --service-account=agent-deploy-service@$AGENTS…` to revert runtime identity.

---

## Self-review notes (coverage map)

- Spec §3 identity model → Tasks B1, B4, B5, B7, B9, Phase D.
- Spec §4 pipeline flow (deploy out of build, digest, env in Python) → B2, B4, B5, B6, B7, B8.
- Spec §5 provisioning → B9, D1.
- Spec §6 code changes → B1–B10, A1, C1.
- Spec §7 migration runbook → Phase D.
- Spec §8 verification matrix → D6.
- Spec §9 follow-ups (DB, auto-provision, build egress, secrets, quota) → intentionally out of scope; documented in the spec, not tasked here.
