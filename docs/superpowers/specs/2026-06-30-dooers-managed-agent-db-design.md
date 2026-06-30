# Dooers-managed agent database — design

**Audience:** Dooers platform / DevOps
**Status:** Approved design — ready to plan
**Scope:** `dooers-service-agent` (SDK connection), `dooers-push` (per-agent provisioning trigger + VPC deploy), a new **db-provisioner** service in `dooers-agents`, `provision-org` extension, core/`AgentConfig` + `dooers.yaml` (config field), AlloyDB infra.
**Builds on:** [`2026-06-25-per-tenant-service-account-isolation-design.md`](./2026-06-25-per-tenant-service-account-isolation-design.md). This is the deferred "per-org database" follow-up; it attaches to the existing per-org **tenant SA** (`tenant-<token>`).

---

## 1. Goal

A creator opts into a Dooers-managed database by setting one field in their agent config:

```yaml
database:
  type: dooers
```

On `dooers push`, the platform provisions a **per-agent AlloyDB database**, binds the org's **tenant SA** to it via **IAM** (no passwords), and the agents-server SDK connects to it **automatically** at runtime. The creator just uses the DB — creating and querying their own tables — with nothing to configure.

This replaces, for opted-in agents, today's model where creators self-provide `AGENT_DATABASE_*` (host/user/password) for an external Postgres.

## 2. Decisions (settled in brainstorming)

1. **Access model: direct IAM-auth.** The agent runs *as* the tenant SA (ambient Cloud Run runtime identity); the SDK uses that identity to authenticate to AlloyDB via IAM (short-lived token from the metadata server). No hosted SQL proxy in v1 — isolation comes from per-database GRANTs + network, not from hiding the identity. A hosted query proxy remains a clean future upgrade for central audit/quotas; native options (Query Insights, pgAudit, role `CONNECTION LIMIT`) cover v1.
2. **Partitioning: one database per agent** — `agent_<id>` in the shared `dooers-agents-db` cluster. The per-org tenant-SA IAM user is granted access to each of its agents' databases. Same-org agents share the SA identity (acceptable — same trust domain, consistent with the compute model).
3. **Provisioning: dedicated db-provisioner, triggered by push.** The per-org IAM DB user is created once in `provision-org`. The privileged per-agent SQL (`CREATE DATABASE` + `GRANT`) runs in a small **db-provisioner** service that lives in `dooers-agents` with VPC access to AlloyDB and an admin identity; the push flow calls it automatically when `database.type=dooers`. DB-admin credentials/network never touch the request-serving push service or the agent.

## 3. Current state (verified)

- AlloyDB cluster `dooers-agents-db` (primary instance `dooers-agents-db-primary`) in `dooers-agents`, network `dooers-agents-vpc`, region `southamerica-east1`. Only a `postgres` built-in superuser; **no IAM users yet**.
- Per-org `tenant-<token>` SA exists with `logging.logWriter` only (from the SA-isolation work).
- SDK (`dooers-service-agent`) already models `database_type` ∈ {`sqlite`, `postgres`, `cosmos`} on `Agent(...)`, with Postgres fields defaulting from `AGENT_DATABASE_*` (`settings.py`). Agents connect via `asyncpg` with a password DSN.
- Agent Cloud Run services currently have **no VPC egress** (they reached external DBs over the internet).

## 4. Config surface — `database.type`

`database.type` ∈ {`dooers`, `postgres`, `none`}; default `postgres` (back-compat — today's self-provided behavior). Lives in:

- **`dooers.yaml`** (creator-facing manifest): a `database:` block.
- **core agent record**: `database` object, returned by `GET /api/v2/agents/:id` and writable via `PATCH`. **⚠ Critical-path external dependency:** core is a separate service/team and must ship this field before the push side can detect `dooers`. This is the first thing to coordinate — until core returns `database.type`, push always sees the default and managed-DB never triggers. (Interim unblock for testing: read it from the uploaded `dooers.yaml` in the archive, but core is the source of truth.)
- **SDK** (`dooers-service-agent`): `database_type="dooers"` becomes a valid value (alongside `sqlite`/`postgres`/`cosmos`).

Semantics: `dooers` → platform-managed (this feature); `postgres` → creator's own `AGENT_DATABASE_*` (unchanged); `none` → no DB.

## 5. Architecture & data flow

```
A) provision-org <org_id>   (one-time per org; extend the existing CLI)
     tenant-<org> SA  →  + roles/alloydb.client            (allows IAM connections)
                         + AlloyDB IAM user:  gcloud alloydb users create
                             tenant-<token>@dooers-agents.iam … --type=IAM_BASED
                         (optional) role CONNECTION LIMIT / statement_timeout

B) dooers push  (per agent, only when agent.database.type == "dooers")
     push control plane ──OIDC──▶ db-provisioner  (Cloud Run in dooers-agents)
        db-provisioner (VPC egress to AlloyDB private IP; auth = its own AlloyDB
        admin IAM user — no stored password):
            CREATE DATABASE agent_<id> OWNER "tenant-<token>@…"   (idempotent;
              ALTER DATABASE … OWNER TO … if it already exists)
            # owning the database gives the tenant user full control of its own
            # schema/tables (via pg_database_owner on PG15+), so no separate
            # CONNECT / schema GRANTs are needed.
     then deploy the agent Cloud Run (in finalize_deploy) with:
        • Direct VPC egress into dooers-agents-vpc  (subnet in southamerica-east1)
        • env: AGENT_DATABASE_TYPE=dooers
               AGENT_DATABASE_INSTANCE=<alloydb instance URI>
               AGENT_DATABASE_NAME=agent_<id>
               AGENT_DATABASE_IAM_USER=tenant-<token>@dooers-agents.iam

C) agent runtime  (SDK, running as tenant-<org>)
     settings sees AGENT_DATABASE_TYPE=dooers →
        AlloyDB connector opens a pool to <instance>/agent_<id>,
        user = tenant-<token>@…, auth = IAM token from the metadata server (ambient SA)
     creator code: get_pool() → CREATE TABLE / queries, exactly as today
```

**Naming:** database `agent_<id>` with the agent UUID's hyphens replaced by underscores (valid unquoted Postgres identifier, ≤63 chars), e.g. `agent_50f823c0_06b7_4447_948d_551b43ddba63`.

## 6. Components

### 6.1 `provision-org` extension (`dooers-push/src/dooers_push/provision.py`)
Add two idempotent steps when provisioning an org: grant the tenant SA `roles/alloydb.client`, and create the AlloyDB IAM user for it (`gcloud alloydb users create … --type=IAM_BASED --cluster=dooers-agents-db --region=…`). This is the only place IAM DB users are minted.

### 6.2 db-provisioner (new service, `dooers-agents`)
A small FastAPI Cloud Run service (`dooers-db-provisioner`) — single responsibility: run privileged per-agent DB SQL.
- **Runs as** a dedicated SA `db-provisioner@dooers-agents`, registered as an AlloyDB IAM user with `alloydbsuperuser` (or `CREATEDB`+`CREATEROLE`). Connects to AlloyDB via the connector with IAM auth (no password).
- **Network:** Direct VPC egress into `dooers-agents-vpc` to reach the AlloyDB private IP.
- **Auth:** invoker restricted to the push control plane (`dooers-push-runtime`) via OIDC (mirrors the build-events webhook pattern).
- **API:**
  - `POST /v1/agent-db {agent_id, org_token}` → `CREATE DATABASE agent_<id> OWNER "tenant-<token>@…"` (or `ALTER DATABASE … OWNER TO …` if it already exists). Idempotent. Returns the resolved db name. Ownership (not separate GRANTs) is what lets the creator make/use tables.
  - `DELETE /v1/agent-db/{agent_id}` → `DROP DATABASE agent_<id>` (Phase 2 / teardown).
- **Why a separate service:** keeps the AlloyDB admin identity + VPC path out of both the request-serving push service and the agents.

### 6.3 Push integration (`dooers-push`)
- `main.py` push route: read `agent.database` (from core). When `type=dooers`, before/within deploy, call the db-provisioner to ensure the database exists.
- `gcp/cloudrun.py` `deploy_service`: when managed-DB, add **Direct VPC egress** (network/subnet) + the `AGENT_DATABASE_*` env (instance, name, IAM user, `TYPE=dooers`). The deploy already runs in `finalize_deploy` (control plane).
- `pipeline/deployer.py` `finalize_deploy`: thread the db step + env. (db.type carried on the `BuildRecord`, like `organization_id`/`gcs_uri`.)

### 6.4 SDK (`dooers-service-agent`)
- `settings.py`: add `agent_database_type` (`AGENT_DATABASE_TYPE`), `agent_database_instance` (`AGENT_DATABASE_INSTANCE`), `agent_database_iam_user` (`AGENT_DATABASE_IAM_USER`).
- DB pool init: if `type == "dooers"`, build the pool via the **AlloyDB Python connector** (`google-cloud-alloydb-connector`) with `enable_iam_auth=True`, instance = `AGENT_DATABASE_INSTANCE`, user = the IAM user, db = `AGENT_DATABASE_NAME`; else keep the current password DSN path. **Same `get_pool()` API** so `Agent(...)` and `ensure_rag_schema()` are unchanged.
- `Agent(database_type="dooers")` accepted (new enum value).

## 7. Security properties

- The tenant SA's IAM DB user owns/can reach **only its org's agent databases** → cannot read/write other orgs' data, even on a shared cluster (it has no grant on other orgs' databases).
- **Within an org there is no inter-agent DB isolation** — all of an org's agents run as the same tenant SA / IAM user, which owns each of that org's agent databases. This is by design (same trust domain, consistent with the shared per-org compute identity). Per-agent DB isolation would require per-agent SAs, which we deliberately don't do.
- DB-admin credential + VPC path live **only in the db-provisioner**, callable **only by the push control plane**. Agents and the request-serving push service never hold DB-admin.
- No passwords anywhere — IAM token auth end-to-end.
- Agents reach AlloyDB over **private IP in the VPC**; no public DB exposure.
- Audit/quota: native (Query Insights, pgAudit, role `CONNECTION LIMIT`) in Phase 2; hosted proxy is a future, contract-compatible upgrade.

## 8. Error handling

- `database.type=dooers` but org not provisioned for managed DB (no IAM user) → push fails with a clear message (extend the existing `org_not_provisioned` style).
- db-provisioner unreachable / SQL error → push fails the deploy with a creator-facing message (reuse `build_user_error`/runtime-log diagnosis); idempotent retry safe.
- SDK can't reach AlloyDB at runtime (VPC/IAM misconfig) → existing `diagnose_creator_message` already surfaces "cannot reach PostgreSQL"; extend for the managed case.

## 9. Phasing

- **Phase 1 (core, end-to-end):** config field → `provision-org` IAM user + `alloydb.client` → db-provisioner (`CREATE DATABASE` + GRANT) → push integration (VPC egress + env) → SDK IAM connection. Result: `database.type: dooers` works on push.
- **Phase 2 (lifecycle + hardening):** teardown `DROP DATABASE` wired to `DELETE /v1/agents/{id}`; connection-limit quotas; pgAudit; (optional) hosted query proxy.

## 10. Non-goals

- Hosted SQL proxy (future; native audit/quota suffices for v1).
- Per-agent service accounts / per-agent DB users (per-org SA is the identity, by design).
- Migrating existing `postgres`-type agents (opt-in only; default stays `postgres`).
- Cross-region / HA AlloyDB topology changes.

## 11. Open items to verify during planning (not blockers)

- Enable **IAM authentication** on `dooers-agents-db-primary` (database flag `alloydb.iam_authentication` / instance setting) if not already on.
- Confirm/create a **subnet** in `dooers-agents-vpc` (region `southamerica-east1`) usable for Cloud Run Direct VPC egress.
- Confirm the AlloyDB **connector** library + asyncpg/SQLAlchemy driver choice for the SDK pool.
- Decide the db-provisioner admin role precisely (`alloydbsuperuser` vs `CREATEDB`+`CREATEROLE`).
