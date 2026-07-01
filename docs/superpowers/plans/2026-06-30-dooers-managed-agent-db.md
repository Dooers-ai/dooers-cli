# Dooers-managed Agent Database Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a creator set `database.type: dooers` and have the platform auto-provision a per-agent AlloyDB database, bind it to the org's tenant SA via IAM, and have the agents-server SDK connect to it at runtime with no passwords.

**Architecture:** Per-org IAM DB user created once in `provision-org`. On push, the control plane calls a new privileged **db-provisioner** (Cloud Run in `dooers-agents`, VPC access to AlloyDB) to `CREATE DATABASE agent_<id>` + `GRANT` to the org's IAM user, then deploys the agent with Direct VPC egress + DB env. The SDK connects via the AlloyDB connector using the agent's ambient tenant-SA identity (IAM auth).

**Tech Stack:** Python 3.10+ (`dooers-cli`/`dooers-protocol`), 3.12 (`dooers-push`, db-provisioner, SDK). `uv`+`poethepoet`, `ruff`, `mypy`, `pytest`. FastAPI. GCP: AlloyDB + IAM auth (`google-cloud-alloydb-connector`), Cloud Run (`google-cloud-run`), `gcloud alloydb`. `asyncpg`.

## Global Constraints

- Design ref: `docs/superpowers/specs/2026-06-30-dooers-managed-agent-db-design.md`. Builds on the per-tenant SA isolation already live (tenant SA `tenant-<token>`, where `token = sha256(org_id)[:12]`, in `dooers_push.tenancy`).
- GCP: project `dooers-agents`, region `southamerica-east1`, AlloyDB cluster `dooers-agents-db`, primary instance `dooers-agents-db-primary`, network `dooers-agents-vpc`.
- **Database name (verbatim):** `agent_<id>` with the agent UUID's `-` replaced by `_` (unquoted Postgres identifier, ≤63 chars). e.g. `agent_50f823c0_06b7_4447_948d_551b43ddba63`.
- **Config values (verbatim):** `database.type` ∈ {`dooers`, `postgres`, `none`}; default `postgres`.
- **Agent env vars (verbatim):** `AGENT_DATABASE_TYPE`, `AGENT_DATABASE_INSTANCE`, `AGENT_DATABASE_NAME`, `AGENT_DATABASE_IAM_USER`.
- No passwords — IAM token auth end-to-end. The db-provisioner is callable ONLY by `dooers-push-runtime` (OIDC). DB-admin never on the agent or the request-serving push service.
- TDD: failing test → watch fail → minimal impl → watch pass → commit. Small commits.
- **Phase 1 = Tasks A1–E2 (end-to-end happy path).** Phase 2 (Tasks P2-*) = teardown/quota/audit, outlined at the end.

**Repos:** `dooers-cli` (manifest field), `dooers-protocol` (AgentRecord field, if used), `dooers-push` (`provision.py`, push integration), **db-provisioner** (new service, in the `dooers-push` repo under `dbprovisioner/`), `dooers-service-agent` (SDK), core (agent `database` field — contract noted), GCP infra runbook (operator).

---

## Phase A — config field (contract)

### Task A1: `database_type` on the agent contract + manifest

**Files:**
- Modify: `dooers-push` `src/dooers_push/core_models.py` (or `dooers.protocol.agents.AgentRecord` consumer) — add `database_type`
- Modify: `dooers-cli` `packages/dooers-cli/src/dooers/cli/config.py` (manifest model) — `database.type`
- Modify: `dooers-cli` `packages/dooers-cli/src/dooers/cli/manifest_sync.py` if it syncs config to core
- Test: `dooers-cli` `packages/dooers-cli/tests/test_config.py`; `dooers-push` `tests/test_core_client.py`
- **Core (separate service, contract only):** `GET /api/v2/agents/:id` returns `database: { type: "dooers"|"postgres"|"none" }`; `PATCH` accepts it. Coordinate with the core team; this plan assumes the field is present (default `postgres`).

**Interfaces:**
- Produces: `AgentRecord.database_type: str = "postgres"` (read from core `data.get("database", {}).get("type", "postgres")`). Manifest gains `database.type`.

- [ ] **Step 1: Write the failing test (push core_client maps the field)**
```python
# dooers-push tests/test_core_client.py
def test_get_agent_reads_database_type(...):
    # core returns data={"agentId": "...", "ownerUserId": "u1", "organizationId": "o1",
    #                     "database": {"type": "dooers"}}
    agent = await core.get_agent("a1", session)
    assert agent.database_type == "dooers"

def test_get_agent_defaults_database_type_postgres(...):
    # data without "database" → default
    assert agent.database_type == "postgres"
```

- [ ] **Step 2: Run → FAIL** (`AgentRecord` has no `database_type`).

- [ ] **Step 3: Implement** — add `database_type: str = "postgres"` to the push-side `AgentRecord` construction in `core_client.get_agent`:
```python
return AgentRecord(
    agent_id=data["agentId"], name=data.get("name", agent_id),
    owner_user_id=owner, organization_id=data.get("organizationId"),
    host_url=data.get("hostUrl"),
    database_type=(data.get("database") or {}).get("type", "postgres"),
)
```
(If `AgentRecord` is the `dooers.protocol.agents` model, add the optional field there; else use a local field on the push model.)

- [ ] **Step 4: Manifest field (dooers-cli)** — add a `database` block to the manifest model (default `{type: "postgres"}`) and a test in `test_config.py` that round-trips `database.type: dooers`.

- [ ] **Step 5: Run suites green; commit**
```bash
git commit -m "feat: add database.type to agent contract + dooers.yaml manifest"
```

---

## Phase B — `provision-org` extension (per-org IAM DB user)

### Task B1: provision the tenant SA as an AlloyDB IAM user

**Files:**
- Modify: `dooers-push` `src/dooers_push/provision.py`
- Test: `dooers-push` `tests/test_provision.py`

**Interfaces:**
- Consumes: `tenancy.tenant_sa_email`, `tenancy.org_token`.
- Produces: provisioning emits two new gcloud steps — grant `roles/alloydb.client` to the tenant SA, and `gcloud alloydb users create <tenant_sa_email> --type=IAM_BASED`.

- [ ] **Step 1: Write the failing test**
```python
def test_provision_includes_alloydb_iam_user(capsys):
    provision.main(["org1", "--control-plane-sa", "cp@x.iam.gserviceaccount.com",
                    "--alloydb-cluster", "dooers-agents-db", "--dry-run"])
    out = capsys.readouterr().out
    assert "roles/alloydb.client" in out
    assert "alloydb users create" in out
    assert tenancy.tenant_sa_email("org1", "dooers-agents") in out
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** — add an `--alloydb-cluster` arg (default `dooers-agents-db`) and two steps in `_build_steps`:
```python
tenant = tenancy.tenant_sa_email(org_id, project)
# AlloyDB IAM user uses the SA email WITHOUT the .gserviceaccount.com suffix:
iam_db_user = tenant.removesuffix(".gserviceaccount.com")
steps += [
    ["gcloud", "projects", "add-iam-policy-binding", project,
     f"--member=serviceAccount:{tenant}", "--role=roles/alloydb.client"],
    ["gcloud", "alloydb", "users", "create", iam_db_user,
     f"--cluster={alloydb_cluster}", f"--region={region}", f"--project={project}",
     "--type=IAM_BASED"],
]
```
(`alloydb users create` errors if the user exists → tolerate "already exists", same as other steps.)

- [ ] **Step 4: Run tests + ruff + mypy green.**

- [ ] **Step 5: Commit**
```bash
git commit -m "feat: provision-org registers tenant SA as an AlloyDB IAM user"
```

---

## Phase C — db-provisioner service (new, privileged per-agent DB SQL)

New service in the `dooers-push` repo under `dbprovisioner/` (own FastAPI app + Dockerfile; deployed separately to `dooers-agents`).

### Task C1: agent → database-name helper (pure)

**Files:**
- Create: `dooers-push` `dbprovisioner/naming.py`
- Test: `dooers-push` `tests/test_dbprovisioner_naming.py`

**Interfaces:**
- Produces: `agent_db_name(agent_id: str) -> str` → `agent_<id-with-underscores>`, validated as a Postgres identifier.

- [ ] **Step 1: Write the failing test**
```python
import re
from dbprovisioner.naming import agent_db_name

def test_agent_db_name_is_valid_pg_identifier():
    n = agent_db_name("50f823c0-06b7-4447-948d-551b43ddba63")
    assert n == "agent_50f823c0_06b7_4447_948d_551b43ddba63"
    assert re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", n)

def test_rejects_injection():
    import pytest
    with pytest.raises(ValueError):
        agent_db_name('a"; DROP DATABASE x;--')
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**
```python
import re
_AGENT_ID_RE = re.compile(r"^[0-9a-fA-F-]{1,64}$")  # UUID-ish only

def agent_db_name(agent_id: str) -> str:
    if not _AGENT_ID_RE.fullmatch(agent_id):
        raise ValueError(f"unsafe agent_id for db name: {agent_id!r}")
    name = "agent_" + agent_id.lower().replace("-", "_")
    if len(name) > 63 or not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise ValueError(f"invalid db name produced: {name!r}")
    return name
```

- [ ] **Step 4: Run → PASS.**  **Step 5: Commit** `feat(dbprovisioner): safe agent→database name mapping`.

### Task C2: provisioning SQL logic

**Files:**
- Create: `dooers-push` `dbprovisioner/provisioner.py`
- Test: `dooers-push` `tests/test_dbprovisioner_sql.py`

**Interfaces:**
- Consumes: `naming.agent_db_name`. A `Connector`-like object exposing `execute(sql)` (injected for tests).
- Produces:
  - `async def ensure_agent_db(conn, agent_id: str, iam_user: str) -> str` — `CREATE DATABASE` (if absent) + `GRANT`s; returns db name.
  - `async def drop_agent_db(conn, agent_id: str) -> None` (Phase 2).

- [ ] **Step 1: Write the failing test** (assert the exact SQL: db created/owned by the IAM user, db-name interpolated from the validated helper, iam_user double-quoted)
```python
import asyncio
from unittest.mock import AsyncMock
from dbprovisioner.provisioner import ensure_agent_db

def test_ensure_agent_db_creates_owned_by_iam_user():
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)  # db does not exist
    name = asyncio.run(ensure_agent_db(conn, "50f823c0-06b7-4447-948d-551b43ddba63",
                                       "tenant-84601f39ecd0@dooers-agents.iam"))
    sql = " ".join(c.args[0] for c in conn.execute.call_args_list)
    assert name == "agent_50f823c0_06b7_4447_948d_551b43ddba63"
    assert ('CREATE DATABASE agent_50f823c0_06b7_4447_948d_551b43ddba63 '
            'OWNER "tenant-84601f39ecd0@dooers-agents.iam"') in sql

def test_ensure_agent_db_idempotent_realigns_owner():
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)  # db already exists
    asyncio.run(ensure_agent_db(conn, "50f823c0-06b7-4447-948d-551b43ddba63",
                                "tenant-84601f39ecd0@dooers-agents.iam"))
    sql = " ".join(c.args[0] for c in conn.execute.call_args_list)
    assert "ALTER DATABASE agent_50f823c0_06b7_4447_948d_551b43ddba63 OWNER TO" in sql
    assert "CREATE DATABASE" not in sql
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** — db name from `agent_db_name` (validated → safe to interpolate); IAM user double-quoted. `CREATE DATABASE` can't run in a transaction and has no `IF NOT EXISTS`, so guard with a `pg_database` existence check (benign TOCTOU — concurrent pushes of the *same* agent are rate-limited/serial; `ALTER OWNER` on the exists-path is idempotent). **Owning the database** is what grants the creator full control (schema/tables) — no separate CONNECT/schema GRANTs needed (the owner is implicitly a member of `pg_database_owner`, which owns `public` on PG15+):
```python
from dbprovisioner.naming import agent_db_name

async def ensure_agent_db(conn, agent_id: str, iam_user: str) -> str:
    db = agent_db_name(agent_id)
    user = '"' + iam_user.replace('"', '""') + '"'
    exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db)
    if not exists:
        await conn.execute(f"CREATE DATABASE {db} OWNER {user}")
    else:
        await conn.execute(f"ALTER DATABASE {db} OWNER TO {user}")
    return db
```
(The service layer connects to the maintenance `postgres` db as the admin IAM user to run this — no second connection / schema-grant pass needed.)

- [ ] **Step 4: PASS. Step 5: Commit** `feat(dbprovisioner): ensure_agent_db SQL (create + grant)`.

### Task C3: FastAPI service + OIDC auth

**Files:**
- Create: `dooers-push` `dbprovisioner/main.py`, `dbprovisioner/db.py` (AlloyDB connector wiring), `dbprovisioner/settings.py`
- Test: `dooers-push` `tests/test_dbprovisioner_api.py`

**Interfaces:**
- Consumes: `provisioner.ensure_agent_db`, OIDC verify (reuse the `pubsub_auth.verify_oidc` pattern), AlloyDB connector.
- Produces: `POST /v1/agent-db {agent_id, org_token}` → `{ "database": "<name>" }` (200); OIDC-restricted to `dooers-push-runtime`. `GET /health`.

- [ ] **Step 1: Write the failing test** — mock the connector + OIDC; assert 200 + db name on a valid call, 403 on bad/missing OIDC.
```python
def test_provision_endpoint_creates_db(monkeypatch):
    # patch verify_oidc to pass; patch db.acquire() to an AsyncMock conn; patch ensure_agent_db
    resp = client.post("/v1/agent-db", json={"agent_id": UUID, "org_token": "84601f39ecd0"},
                       headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    assert resp.json()["database"] == f"agent_{UUID.replace('-', '_')}"

def test_provision_endpoint_rejects_bad_oidc(...):
    assert resp.status_code == 403
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** — FastAPI app: verify OIDC (audience = this service URL, SA = `dooers-push-runtime`), acquire an AlloyDB connection (connector, `enable_iam_auth=True`, as the db-provisioner's own admin IAM identity, to the `postgres` db for CREATE then to the agent db for schema grant), call `ensure_agent_db`. `db.py` wraps `google.cloud.alloydb.connector.AsyncConnector` + asyncpg.

- [ ] **Step 4: PASS. Step 5: Commit** `feat(dbprovisioner): OIDC-guarded POST /v1/agent-db`.

### Task C4: Dockerfile + deploy

**Files:**
- Create: `dooers-push` `dbprovisioner/Dockerfile`, `dbprovisioner/pyproject.toml`
- Modify: `dooers-push` `docs/gcp-push-deploy.md` (deploy steps)

- [ ] **Step 1: Dockerfile** (FROM python:3.12-slim; install `fastapi uvicorn google-cloud-alloydb-connector asyncpg google-auth`; CMD uvicorn).
- [ ] **Step 2: Deploy runbook** — see Phase F.
- [ ] **Step 3: Commit** `build(dbprovisioner): container + deploy docs`.

---

## Phase D — push integration

### Task D1: carry `database_type` through the pipeline

**Files:**
- Modify: `dooers-push` `src/dooers_push/pipeline/base.py` (already has fields), `src/dooers_push/build_store.py` (`BuildRecord` += `database_type: str = "postgres"`), `src/dooers_push/main.py` (persist + rehydrate, like `organization_id`)
- Test: `dooers-push` `tests/test_build_store.py` (round-trip)

- [ ] **Step 1–5:** add `database_type` to `BuildRecord` (default `postgres`), persist it in the push route's `store.put(...)`, rehydrate it in the `build_events` webhook ctx (mirror `organization_id`/`gcs_uri`). Test the record round-trips. Commit `feat: carry database_type on the build record`.

### Task D2: push calls the db-provisioner when `type=dooers`

**Files:**
- Create: `dooers-push` `src/dooers_push/gcp/dbprovision_client.py` (HTTP client → db-provisioner, OIDC token via metadata)
- Modify: `dooers-push` `src/dooers_push/main.py` (call it after the hosting/org-provisioned prechecks)
- Modify: `dooers-push` `src/dooers_push/settings.py` (`DB_PROVISIONER_URL`)
- Test: `dooers-push` `tests/test_push_managed_db.py`

**Interfaces:**
- Produces: `async def ensure_agent_db(agent_id, org_token, settings) -> str` — POSTs to the db-provisioner with an OIDC token; returns the db name. Called only when `agent.database_type == "dooers"`.

- [ ] **Step 1: Failing test** — patch the client; assert it's called for `type=dooers` and NOT for `type=postgres`; a provisioner error → push fails with a clear message.
- [ ] **Steps 2–4:** implement the client (OIDC ID token for the provisioner audience via `google.oauth2.id_token`), wire into the push route guarded by `database_type == "dooers"`.
- [ ] **Step 5: Commit** `feat: push provisions the managed agent DB via db-provisioner`.

### Task D3: deploy with VPC egress + DB env when `type=dooers`

**Files:**
- Modify: `dooers-push` `src/dooers_push/gcp/cloudrun.py` (`deploy_service` gains optional `vpc_egress` + extra `env_vars`)
- Modify: `dooers-push` `src/dooers_push/pipeline/deployer.py` (`_deploy_agent_service`: when `database_type=dooers`, set VPC egress + the `AGENT_DATABASE_*` env)
- Modify: `dooers-push` `src/dooers_push/settings.py` (`AGENT_VPC_NETWORK`, `AGENT_VPC_SUBNET`, `ALLOYDB_INSTANCE_URI`)
- Test: `dooers-push` `tests/test_cloudrun_deploy.py`, `tests/test_deployer_managed_db.py`

**Interfaces:**
- Consumes: `tenancy` (tenant SA email), `naming`/db name.
- Produces: `deploy_service(..., vpc_access: run_v2.VpcAccess | None = None)`; when managed-DB, env includes `AGENT_DATABASE_TYPE=dooers`, `AGENT_DATABASE_INSTANCE=<ALLOYDB_INSTANCE_URI>`, `AGENT_DATABASE_NAME=agent_<id>`, `AGENT_DATABASE_IAM_USER=tenant-<token>@dooers-agents.iam`.

- [ ] **Step 1: Failing test** — assert the deployed `Service` has `template.vpc_access` set (Direct VPC egress, network/subnet) and the 4 `AGENT_DATABASE_*` env vars, only when `database_type=dooers`.
- [ ] **Steps 2–4:** implement; for `type=postgres`/`none`, no VPC/DB env added (unchanged path).
- [ ] **Step 5: Commit** `feat: deploy managed-DB agents with VPC egress + AlloyDB env`.

---

## Phase E — SDK (`dooers-service-agent`)

### Task E1: settings fields for managed DB

**Files:**
- Modify: `dooers-service-agent` `src/settings.py`
- Test: `dooers-service-agent` `tests/test_settings.py`

- [ ] **Step 1: Failing test** — `AGENT_DATABASE_TYPE=dooers`, `AGENT_DATABASE_INSTANCE=...`, `AGENT_DATABASE_IAM_USER=...` load into settings.
- [ ] **Step 3: Implement** — add `agent_database_type` (default `postgres`), `agent_database_instance`, `agent_database_iam_user` fields (Pydantic `Field` + `validation_alias`, mirroring the existing `AGENT_DATABASE_*` fields).
- [ ] **Step 5: Commit** `feat(sdk): settings for managed (dooers) database`.

### Task E2: connect via AlloyDB IAM when `type=dooers`

**Files:**
- Modify: `dooers-service-agent` `src/agent.py` (or the DB pool layer the SDK uses)
- Modify: `dooers-service-agent` `pyproject.toml` (+`google-cloud-alloydb-connector`)
- Test: `dooers-service-agent` `tests/test_db_connect.py`

**Interfaces:**
- Consumes: settings (E1).
- Produces: the pool builder branches on `database_type`: `dooers` → AlloyDB connector with `enable_iam_auth=True` (instance, IAM user, db name; token via metadata — the agent runs as the tenant SA); `postgres`/`sqlite`/`cosmos` → unchanged.

- [ ] **Step 1: Failing test** — patch the AlloyDB connector; assert that for `type=dooers` the pool is built with `enable_iam_auth=True`, the instance URI, the IAM user, and db name (no password); for `type=postgres` the existing DSN path is used.
- [ ] **Steps 2–4:** implement the branch. Same `get_pool()`/`Agent(...)` API so creator code and the starter's `ensure_rag_schema()` are unchanged.
- [ ] **Step 5: Commit** `feat(sdk): connect to Dooers-managed AlloyDB via IAM`.

---

## Phase F — infra runbook (operator-run; gated)

> Not automated by the code above. Shell vars: `AGENTS=dooers-agents; REGION=southamerica-east1; CLUSTER=dooers-agents-db`.

- [ ] **F1 — Enable IAM auth on AlloyDB.** Set the instance flag `alloydb.iam_authentication=on` on `dooers-agents-db-primary` (`gcloud alloydb instances update`), if not already.
- [ ] **F2 — db-provisioner admin identity.** Create SA `db-provisioner@$AGENTS`; create its AlloyDB IAM user (`gcloud alloydb users create db-provisioner@$AGENTS.iam --type=IAM_BASED --cluster=$CLUSTER`); grant it `alloydbsuperuser` (or `CREATEDB`+`CREATEROLE`) via SQL connected as `postgres`.
- [ ] **F3 — VPC subnet.** Confirm/create a subnet in `dooers-agents-vpc` (region `$REGION`) for Cloud Run Direct VPC egress.
- [ ] **F4 — Deploy db-provisioner.** Build + deploy `dbprovisioner/` to Cloud Run in `$AGENTS` with Direct VPC egress into `dooers-agents-vpc`, runtime SA `db-provisioner@$AGENTS`, ingress internal, invoker = `dooers-push-runtime` only. Set `DB_PROVISIONER_URL` on the push service.
- [ ] **F5 — Run `provision-org` (updated)** for each org that will use managed DB (creates the tenant-SA IAM user + `alloydb.client`).
- [ ] **F6 — Verify** (next section).

---

## Verification (allow + deny)

- [ ] `dooers push` an agent with `database.type: dooers` → succeeds; `gcloud alloydb databases list --cluster=$CLUSTER` shows `agent_<id>`; the agent's `/health` is 200 and DB-backed endpoints work (creator can create a table).
- [ ] The agent's Cloud Run service has `vpc_access` set + the 4 `AGENT_DATABASE_*` env vars; runs as `tenant-<token>`.
- [ ] Impersonate `tenant-<orgA>` IAM user and attempt to connect to **another org's** `agent_<id>` database → **denied** (no GRANT).
- [ ] db-provisioner rejects a direct call without the `dooers-push-runtime` OIDC token → **403**.
- [ ] An agent with `database.type: postgres` → unchanged (no VPC egress, no managed DB, uses its own `AGENT_DATABASE_*`).

---

## Phase 2 (outlined — separate plan when scheduled)

- **P2-1 Teardown:** `DELETE /v1/agents/{id}` calls db-provisioner `DELETE /v1/agent-db/{id}` → `DROP DATABASE agent_<id>` (guarded/confirmable to avoid accidental data loss).
- **P2-2 Quotas:** set role `CONNECTION LIMIT` + `statement_timeout` on the per-org IAM user in `provision-org`.
- **P2-3 Audit:** enable `pgAudit` + surface Query Insights per org.
- **P2-4 (optional) Hosted query proxy:** front AlloyDB with a Dooers proxy for centralized per-call audit/quota; SDK repointed via the same `AGENT_DATABASE_*` contract.

---

## Self-review (coverage map)

- Spec §4 config field → Task A1. · §6.1 provision-org → B1. · §6.2 db-provisioner → C1–C4. · §6.3 push integration → D1–D3. · §6.4 SDK → E1–E2. · §5 data flow → B1+C+D+E. · §7 security (per-db GRANT, OIDC-only provisioner, IAM auth, VPC) → B1/C3/D3/Verification. · §8 error handling → D2 (provisioner failure), E2 (runtime). · §9 phasing → Phase 1 (A–F) / Phase 2. · §11 open items → Phase F (F1–F3) + Task C2 driver note.
