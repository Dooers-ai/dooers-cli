# dooers CLI/push → core v2 migration + agent `hostUrl`

**Status:** Draft for review
**Date:** 2026-06-03
**Author:** platform
**Scope:** `dooers-service-core` (v2), `dooers-protocol`, `dooers-cli`, `dooers-push`

---

## 1. Overview

The deployed core at `https://api.dooers.ai` is the **v2** service (`dooers-service-core`): Hono + better-auth, everything under `/api/v2`, `{success, data}` envelopes, agents keyed by **UUID** and scoped to an **organization**. The CLI and `dooers-push` were written against a **v1** core (`/api/v1/session/*`, `ag_xxxx` ids, `{output}` envelopes, file-shim fallbacks). **v1 is deprecated.**

This migration makes `dooers-cli` and `dooers-push` talk to core v2 correctly, and adds a place on the agent to persist the deployed URL (`hostUrl`) so a push is recorded centrally.

### Goal

After this work: `dooers login` authenticates against v2 (email OTP → Bearer token); `dooers agents create` creates a real org-scoped agent in core (UUID); `dooers push` deploys and **writes the public URL back to the agent's `hostUrl`** in core; `dooers agents list/show` read from core. No file-shim, no `DOOERS_USE_CORE_AGENTS` gating — v2 is the only path.

### Decisions (locked)

- **Agent public URL uses the raw UUID** — `https://agents.dooers.ai/<uuid>` (prod). A short slug is a future enhancement, out of scope.
- **Release cadence:** source changes only; the actual `pip` publish + core deploy happen once at the end, after testing.
- **Field name:** `hostUrl` (consistent with `host.dooers.ai`; reads product-level, not infra).

### Non-goals

- Password auth (live core is OTP); SSO; multi-factor.
- Slugged/custom agent URLs.
- Migrating existing v1 shim records.
- Touching `serverConfig` (the SDK runtime-protocol endpoints) — `hostUrl` is separate.
- Async push / status polling (unchanged from current POC).

---

## 2. v2 contract reference (verified live)

All under base `https://api.dooers.ai`. Auth via `Authorization: Bearer <token>` (better-auth `bearer()` plugin). Envelope: success `{ "success": true, "data": ... }`; error `{ "success": false, "error": { code, message, type, timestamp } }`.

### Auth / identity
| Call | Purpose |
|---|---|
| `GET /api/v2/identity/auth-method` | → `{data:{method:"otp"}}` (confirm OTP) |
| `POST /api/v2/auth/email-otp/send-verification-otp` `{email, type:"sign-in"}` | email the code |
| `POST /api/v2/auth/sign-in/email-otp` `{email, otp}` | sign in; Bearer token returned in the `set-auth-token` response header |
| `POST /api/v2/identity/token` (auth) | → `{data:{accessToken, expiresIn, tokenType:"Bearer"}}` — alternative/explicit token mint |
| `GET /api/v2/identity/me` (auth) | whoami; **push-side token verification** |
| `POST /api/v2/identity/revoke` (auth) | logout (current session) |

### Organizations
| Call | Purpose |
|---|---|
| `GET /api/v2/organizations` (auth) | → `{data:[{organizationId, ...}]}` for org resolution |

### Agents
| Call | Purpose |
|---|---|
| `POST /api/v2/agents` `{organizationId, name, ...}` (auth) | create → 201 `{data:{agentId (uuid), ...}}` |
| `GET /api/v2/agents/:agentId` (auth) | fetch one (ownership/`ownerUserId`) |
| `GET /api/v2/agents/organization/:organizationId` (auth) | list org agents |
| `PATCH /api/v2/agents/:agentId` `{hostUrl|...}` (auth) | update; requires `agent:edit_own` (the creator has it) |

---

## 3. Core change (`dooers-service-core`) — add `hostUrl`

Smallest piece. The agent already stores `serverConfig`; we add an explicit, nullable `hostUrl`.

- **DB** (`src/database/models.ts`, `agent` table): add `hostUrl: text('host_url')` (nullable). Generate a migration (`drizzle-kit generate`) following the `database/migrations/` convention (cf. `0006_agent_owner_audit.sql`): `ALTER TABLE "agent" ADD COLUMN "host_url" text;`.
- **Model** (`src/modules/agent/models.ts`): add `hostUrl: z.string().url().nullable().optional()` to `agentSchema` and to `updateAgentRequestSchema`.
- **Repository/services**: include `hostUrl` in the row→record mapping and the update path.
- **OpenAPI** (`src/openapi/schemas.ts`): reflect the field.
- **Auth:** no new endpoint or service-auth. `PATCH /agents/:id` already requires `requireAuth()` + `agent:edit_own`; `dooers-push` writes it by **forwarding the creator's Bearer token** (the creator owns the agent). `serverConfig` is untouched.

---

## 4. `dooers-cli` changes

### 4.1 Auth (`core_client.py`, `token_store.py`, `auth.py`)
- Rewrite `CoreClient` for v2: `auth_method()`, `send_otp(email)`, `verify_otp(email, code) -> (token, expiresIn)` (read `set-auth-token` header; fall back to `POST /identity/token`), `me() -> user`, `revoke()`. Base path `=> {core_url}/api/v2`. Bearer header on authed calls. Parse `{success,data}`; surface `error.message` on failure.
- `token_store`: persist `{token, expires_at}`; `is_token_expired` uses `expires_at`.
- `dooers login <email>` UX: send OTP → prompt for code → store token. `whoami` → `me()`. `logout` → `revoke()`.

### 4.2 Org resolution (new `org.py` + `dooers org` command group)
- `GET /organizations`. Resolution order: `--org` flag › saved default (`~/.dooers/config.json`) › exactly-one-org auto › else **interactive prompt** listing orgs, then save the chosen one as default.
- New commands: `dooers org list`, `dooers org use <id>`.

### 4.3 Agents (`agent_store.py`, `agents.py`, `config.py`)
- **Remove** `FileShimAgentStore` and the `DOOERS_USE_CORE_AGENTS` switch. `HTTPCoreAgentStore` is the only store, talking to `/api/v2/agents` with `{success,data}` parsing.
- `create`: resolve org → `POST /agents {organizationId, name}` → UUID → write `dooers.yaml`. Drop the `--runtime` option (v2 has no runtime field; the agent's Dockerfile defines runtime).
- `list` → `/agents/organization/:org`; `show` → `/agents/:id`.
- `dooers.yaml` (`AgentManifest`): `agent_id` (UUID str), `name`, `organization_id`. Drop `runtime`/`env_required` from the manifest contract.

### 4.4 Settings
- `core_url` default stays `https://api.dooers.ai` (host; clients append `/api/v2`). `push_url` default already `https://host.dooers.ai`. `--core-url/--push-url/--env` unchanged.

---

## 5. `dooers-push` changes

### 5.1 Auth (`auth.py`)
- `verify_session`: forward the incoming Bearer token to `GET /api/v2/identity/me`. 200 → build `AuthSession(user_id=data.id, email=data.email)`. Non-200 → 401. (Replaces `/api/v1/session/verify`.)

### 5.2 Core client (`core_client.py`)
- Remove shim fabrication + `DOOERS_USE_CORE_AGENTS` gating (v2 is the path).
- `get_agent`: `GET /api/v2/agents/:agentId` (Bearer). Ownership check: `data.ownerUserId == session.user_id` (the controller also enforces access; we keep the explicit check). Parse `{success,data}`.
- `patch_host_url(agent_id, host_url)`: `PATCH /api/v2/agents/:agentId {hostUrl: host_url}`. Replaces `patch_agent_url`/`deployed_url`.
- `main.py`: call `patch_host_url(agent_id, ctx.lb_url)` after a successful pipeline.

### 5.3 UUID impact on GCP resource naming (important)
v2 agent IDs are UUIDs, which can **start with a digit** — invalid for a Cloud Run **service name** (`^[a-z]([-a-z0-9]*[a-z0-9])?$`). Today `cloudbuild._service_name` and `loadbalancer._ensure_neg`'s `cloud_run_service` both produce `{safe}-{env}` (no prefix) and must stay identical.
- **Change both** to a letter-prefixed form: **`agent-{uuid}-{env}`** (e.g. `agent-550e8400-…-prod`, ~47 chars, < 63). Keep `_service_name` and the NEG's `cloud_run_service` in lockstep.
- **Public path segment is unchanged** in spirit but now a UUID: `path_segment_for` → `/<uuid>` (prod) or `/<uuid>-<env>` (non-prod). URL paths have no Cloud Run naming constraints, so the raw UUID is fine (decision A).
- NEG/BS names already prefix `agent-…`; labels take the UUID as a value (valid: lowercase hex + hyphens, ≤63).

---

## 6. `dooers-protocol` changes
- **Keep:** push request/response models (`PushResponse`, etc. — the CLI↔push wire contract) and `ErrorCode`. Rename the URL field semantics to `host`/`hostUrl` where it surfaces, or keep `url` (it's "whatever the platform decided") — keep `url`, no rename needed.
- **Update:** `AgentManifest` → `{agent_id: str(UUID), name, organization_id}`. `AgentRecord` → v2 shape used by the Python clients (`agent_id`, `name`, `owner_user_id`, `organization_id`, `host_url`). `CreateAgentRequest` → `{organization_id, name}`.
- **Remove:** v1-only bits no longer used — `Runtime`, `env_required`, and the v1 OTP/session auth models that the CLI replaced with direct v2 calls.

---

## 7. Error handling
- v2 errors `{success:false, error:{code,message}}` → CLI prints `error.message` with a non-zero exit. 401 / expired token → "Run `dooers login`." Push maps core failures to existing `ErrorCode`s (`unauthenticated`, `forbidden`, `not_found`, `core_unreachable`, `internal`).
- `hostUrl` PATCH failure after a successful deploy is **non-fatal**: the agent is live; log a warning and still return the URL (the URL just isn't recorded in core). Mirrors the existing `wait_until_reachable` leniency.

---

## 8. Testing
- Mock-based unit tests (httpx mocked) for: CLI `CoreClient` (otp send/verify, me, revoke, org list), org resolution precedence, `HTTPCoreAgentStore` (create/list/show, `{success,data}` parsing, error envelope), push `verify_session` (me 200/401), push `get_agent` + `patch_host_url`.
- Naming tests: `_service_name`/NEG name produce a valid Cloud-Run name for a digit-leading UUID; `path_segment_for` returns the raw UUID.
- Manual acceptance (end of project, single release): `dooers login` (OTP) → `dooers agents create` → `dooers push` → `curl https://agents.dooers.ai/<uuid>/chat` → `dooers agents show <uuid>` shows `hostUrl`.

---

## 9. Sequencing (each phase independently testable)
1. **Core `hostUrl`** — model + migration + serialize + openapi.
2. **dooers-push → v2** — `auth.py` (`/identity/me`), `core_client.py` (`get_agent`, `patch_host_url`), UUID-safe resource naming.
3. **dooers-cli → v2** — auth (OTP+Bearer), org resolution + `dooers org`, agents store, manifest.
4. **dooers-protocol cleanup** — model updates/removals (done alongside 2–3 as imports require; finalized here).

No `pip`/core deploy until all phases are implemented and tested locally; then one release.

---

## 10. Open risks
- better-auth `set-auth-token` header behavior on the OTP sign-in response — if the token isn't returned there, fall back to `POST /identity/token` immediately after sign-in. Plan must verify against the live/dev core (`https://api-v2.dev.dooers.ai/api/v2`).
- Org membership shape from `GET /organizations` (exact field names) — confirm during implementation against dev.
- Existing locally-created shim agents become invisible (acceptable; pre-release, no real data).
