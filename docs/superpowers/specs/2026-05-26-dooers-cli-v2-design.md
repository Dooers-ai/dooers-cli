# Dooers CLI v2 + `dooers-push` POC — Design Spec

**Status:** Draft for review
**Date:** 2026-05-26
**Companion doc:** `docs/2026-05-26-dooers-push-poc.md` (stakeholder-facing overview)

---

## 1. Goal

Deliver a POC that lets an agent creator, from their terminal, **(a)** authenticate against the Dooers core API, **(b)** list and create agent records, and **(c)** run `dooers push` to upload code, run a (stubbed) audit + provision pipeline, build a container, deploy it to Cloud Run, and receive the live URL back — with that URL written back to the agent record in core.

The POC restructures the existing `deploy-service` repo into a three-package monorepo so the wire contract between CLI and server is owned by a shared package and cannot drift.

## 2. Non-goals (explicit deferrals)

- **Real auditor implementation.** Ships as a no-op stub returning `AuditReport(passed=True, findings=[])`. Real implementation is a policy + research problem, not a code problem.
- **Managed DB / Redis / RAG provisioning.** Provisioner ships as no-op. Isolation-model decision belongs in a separate spec.
- **Billing.** No metering or invoicing. POC does ensure every GCP resource is labeled with `agent_id` + `owner_user_id` so cost attribution is possible later via GCP billing export.
- **Async push.** Push is synchronous: CLI blocks ~3–5 min, server polls Cloud Build, returns URL.
- **Custom domain / load balancer routing.** Cloud Run default URLs only.
- **Rewrite of CLI in Go.** Stays Python/Typer.
- **CI/CD pipeline for the monorepo.** Manual deploy in POC; CI design deferred.

## 3. Architecture

```
   Creator's laptop                                  core API (api.dooers.ai)
   ┌──────────────────┐                              ┌──────────────────────────┐
   │  dooers CLI      │ ──── auth + agents CRUD ───▶ │ /session/*               │
   │  (typer)         │ ◀────────────────────────────│ GET /agents              │
   │                  │                              │ POST /agents             │
   │  ~/.dooers/token │                              │ PATCH /agents/{id}       │
   │  ./dooers.yaml   │                              └──────────────────────────┘
   └────────┬─────────┘                                       ▲
            │                                                 │
            │ push: archive + bearer                          │ PATCH agent url
            ▼                                                 │
   ┌────────────────────────────────────────────────┐         │
   │  dooers-push  (Cloud Run service)              │─────────┘
   │  ┌──────────────────────────────────────────┐  │
   │  │  POST /v1/push/{agent_id}                │  │
   │  │   - verify session (core)                │  │
   │  │   - resolve & own agent (core)           │  │
   │  │   - upload archive → GCS                 │  │
   │  │   - pipeline.run():                      │  │
   │  │       auditor.run()         [STUB]       │  │
   │  │       provisioner.provision() [STUB]     │  │
   │  │       deployer.deploy() → Cloud Build    │  │
   │  │   - poll Cloud Build until done          │  │
   │  │   - describe Cloud Run → URL             │  │
   │  │   - PATCH /agents/{id} (url)             │  │
   │  │   - return PushResponse                  │  │
   │  └──────────────────────────────────────────┘  │
   │                                                │
   │  imports: dooers-protocol                      │
   └────────────────────────────────────────────────┘
```

### Boundary rules (design contract)

1. The CLI talks to exactly two external services: `core API` and `dooers-push`. Nothing else.
2. `dooers-push` is read-only against core for agent metadata except for one `PATCH` to write `deployed_url`. It does **not** host `/agents` CRUD.
3. Every CLI ↔ `dooers-push` request and response is a Pydantic model declared in `dooers-protocol`.
4. The pipeline inside `dooers-push` is three sequential steps behind a common typed interface (`PipelineStep.run(ctx) -> StepResult`). POC ships `auditor` and `provisioner` as no-op subclasses.
5. Push is synchronous from the CLI's perspective. Cloud Run timeout raised to 600s.

## 4. Repository layout

```
dooers-platform/                          # (rename of current `deploy-service`)
├── packages/
│   ├── dooers-cli/                       # PyPI: published as `dooers`
│   │   ├── dooers/
│   │   │   ├── __init__.py
│   │   │   ├── cli.py                    # typer app root + subcommand groups
│   │   │   ├── auth.py                   # login / logout / whoami / token store
│   │   │   ├── agents.py                 # list / create / get
│   │   │   ├── push.py                   # push command (archive + upload + wait)
│   │   │   ├── config.py                 # dooers.yaml reader/writer
│   │   │   ├── core_client.py            # HTTP client for core API
│   │   │   ├── push_client.py            # HTTP client for dooers-push
│   │   │   └── ignore.py                 # .dooersignore + defaults (extracted from v1)
│   │   ├── tests/                        # smoke tests only in POC
│   │   ├── pyproject.toml                # [project] name = "dooers"
│   │   └── README.md
│   │
│   ├── dooers-push/                      # Cloud Run service (not on PyPI)
│   │   ├── dooers_push/
│   │   │   ├── __init__.py
│   │   │   ├── main.py                   # FastAPI app, routes
│   │   │   ├── settings.py               # env-driven config (one place)
│   │   │   ├── auth.py                   # session verification
│   │   │   ├── core_client.py            # GET /agents/{id}, PATCH /agents/{id}
│   │   │   ├── storage.py                # GCS upload
│   │   │   ├── pipeline/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── base.py               # PipelineStep ABC, StepResult, PipelineContext
│   │   │   │   ├── auditor.py            # STUB: pass-through, logs detected endpoints
│   │   │   │   ├── provisioner.py        # STUB: no-op, returns empty env injection
│   │   │   │   ├── deployer.py           # Cloud Build trigger + Cloud Run describe
│   │   │   │   └── runner.py             # sequence runner
│   │   │   └── gcp/
│   │   │       ├── cloudbuild.py         # wraps existing v1 logic
│   │   │       └── cloudrun.py           # service URL lookup
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── README.md
│   │
│   └── dooers-protocol/                  # PyPI: `dooers-protocol`
│       ├── dooers_protocol/
│       │   ├── __init__.py               # PROTOCOL_VERSION = "1"
│       │   ├── auth.py                   # AuthSession, WhoamiResponse
│       │   ├── agents.py                 # AgentRecord, CreateAgentRequest, AgentManifest
│       │   ├── push.py                   # PushRequest, PushResponse, BuildStatus
│       │   ├── audit.py                  # AuditReport, AuditFinding, InfraManifest (used by future auditor)
│       │   └── errors.py                 # ErrorCode enum + ErrorEnvelope
│       ├── tests/
│       ├── pyproject.toml
│       └── README.md
│
├── cloudbuild/
│   └── cloudbuild.yaml                   # unchanged
├── docs/
│   ├── stakeholders/2026-05-26-dooers-push-poc.md
│   └── superpowers/specs/2026-05-26-dooers-cli-v2-design.md
├── pyproject.toml                        # uv workspace root
└── README.md
```

## 5. Packages — file-by-file ownership

### 5.1 `dooers-protocol` (the wire contract)

A small package with no runtime dependencies beyond `pydantic`. All shapes used between CLI and `dooers-push` live here.

```python
# dooers_protocol/__init__.py
PROTOCOL_VERSION = "1"

# dooers_protocol/agents.py
class AgentRecord(BaseModel):
    agent_id: str          # e.g., "ag_8h2k"
    name: str
    owner_user_id: str
    runtime: Literal["python", "node", "docker"] = "docker"
    env_required: list[str] = []
    deployed_url: str | None = None
    created_at: datetime
    updated_at: datetime

class CreateAgentRequest(BaseModel):
    name: str
    runtime: Literal["python", "node", "docker"] = "docker"
    env_required: list[str] = []

class AgentManifest(BaseModel):     # ./dooers.yaml shape
    protocol_version: str
    agent_id: str
    name: str
    runtime: str
    env_required: list[str] = []

# dooers_protocol/push.py
class BuildStatus(str, Enum):
    queued = "queued"
    building = "building"
    deploying = "deploying"
    succeeded = "succeeded"
    failed = "failed"

class PushResponse(BaseModel):
    agent_id: str
    build_id: str           # Cloud Build operation ID
    image: str              # full Artifact Registry image URI
    status: BuildStatus
    url: str | None = None  # set when status == succeeded
    error: str | None = None

# dooers_protocol/audit.py  (defined now, used by stub + future real auditor)
class AuditFinding(BaseModel):
    severity: Literal["info", "warning", "error"]
    category: str
    message: str
    file: str | None = None
    line: int | None = None

class InfraManifest(BaseModel):
    needs_db: bool = False
    needs_redis: bool = False
    detected_endpoints: list[str] = []

class AuditReport(BaseModel):
    passed: bool
    findings: list[AuditFinding] = []
    required_infra: InfraManifest = InfraManifest()
```

### 5.2 `dooers-cli` (user-facing CLI)

Subcommand structure using Typer's `add_typer`:

```
dooers
├── auth
│   ├── login    [--email]                  # OTP flow (reuse v1)
│   ├── logout
│   └── whoami
├── agents
│   ├── list
│   ├── create   [--name --runtime]         # writes dooers.yaml in cwd
│   └── show     [agent_id]
└── push         [agent_id_optional]        # uses dooers.yaml if not given
                 [--tag --env --no-build]
```

**Key files:**

- `cli.py` — top-level Typer app, mounts subcommand groups, handles global `--server-url` / `--env`.
- `auth.py` — extracted from v1. Token at `~/.dooers/token` with `0o600`. Adds `core_client.AuthClient`.
- `agents.py` — three commands; calls `core_client`. `agents create` writes `dooers.yaml`.
- `push.py` — reads `dooers.yaml` (or accepts explicit `agent_id`), archives cwd respecting `.dooersignore`, calls `push_client.push(...)`, shows spinner during the synchronous wait, prints `PushResponse`.
- `config.py` — `read_manifest() / write_manifest()` for `dooers.yaml`. Validates against `AgentManifest`.
- `core_client.py` — thin HTTP wrapper. Methods: `login_request_otp`, `login_verify_otp`, `whoami`, `logout`, `list_agents`, `create_agent`, `get_agent`. (`patch_agent_url` is NOT on the CLI; only `dooers-push` calls it.)
- `push_client.py` — `push(agent_id, archive_path, tag, env) -> PushResponse`. Multipart upload, 600s timeout, streams progress via `tqdm`.
- `ignore.py` — extracted from v1's `_make_tar_gz_of_cwd` + `is_ignored`. Pure functions, easier to test.

### 5.3 `dooers-push` (Cloud Run server)

Single endpoint that matters: `POST /v1/push/{agent_id}`.

**`main.py`** — wires the route. Skinny.

```python
@app.post("/v1/push/{agent_id}")
async def push(agent_id: str, request: Request,
               archive: UploadFile = File(...),
               tag: str = Query("latest"),
               env: str = Query("dev")) -> PushResponse:
    user = await verify_session(request)
    agent = await core_client.get_agent(agent_id, user)
    require_ownership(agent, user)

    gcs_uri = await storage.upload_archive(agent_id, archive)
    ctx = PipelineContext(agent=agent, user=user, gcs_uri=gcs_uri, tag=tag, env=env)
    result = await pipeline.runner.run(ctx, steps=[auditor, provisioner, deployer])

    if result.status == BuildStatus.failed:
        return PushResponse(... error=result.error ...)

    url = await gcp.cloudrun.describe_url(service_name=f"{agent_id}-{env}")
    await core_client.patch_agent_url(agent_id, url)
    return PushResponse(agent_id=agent_id, build_id=result.build_id,
                        image=result.image, status=BuildStatus.succeeded, url=url)
```

**`pipeline/base.py`** — the contract every step implements.

```python
class PipelineContext(BaseModel):
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

class StepResult(BaseModel):
    status: BuildStatus
    error: str | None = None

class PipelineStep(ABC):
    name: str
    @abstractmethod
    async def run(self, ctx: PipelineContext) -> StepResult: ...
```

**`pipeline/auditor.py`** — POC stub.

```python
class AuditorStep(PipelineStep):
    name = "auditor"
    async def run(self, ctx):
        ctx.audit_report = AuditReport(passed=True, findings=[])
        logger.info("auditor stub: passing through (agent=%s)", ctx.agent.agent_id)
        return StepResult(status=BuildStatus.queued)
```

**`pipeline/provisioner.py`** — POC stub.

```python
class ProvisionerStep(PipelineStep):
    name = "provisioner"
    async def run(self, ctx):
        ctx.provisioned_env = {}   # nothing to inject
        logger.info("provisioner stub: no infra (agent=%s)", ctx.agent.agent_id)
        return StepResult(status=BuildStatus.queued)
```

**`pipeline/deployer.py`** — real logic. Wraps the existing v1 `_trigger_cloud_build_with_gcs_source` + Cloud Build polling + `gcloud run services describe`. Service name is `{agent_id}-{env}` (not `{agent_name}-{env}` as in v1). Labels every resource with `agent_id` and `owner_user_id`.

**`pipeline/runner.py`** — sequential execution. Stops on first failure. Returns final aggregated result.

## 6. CLI surface (user-facing reference)

```bash
# auth (OTP via email — flow unchanged from v1)
dooers auth login --email <email>
dooers auth whoami
dooers auth logout

# agents
dooers agents list
dooers agents create --name <name> [--runtime docker|python|node]
dooers agents show <agent_id>

# push (reads dooers.yaml in cwd unless agent_id given)
dooers push [<agent_id>] [--tag <tag>] [--env prod|stg|dev] [--no-build]
```

**Global config (resolved in this precedence order, highest first):**
| Setting | Flag | Env var | Default |
|---|---|---|---|
| Core API host | `--core-url` | `DOOERS_CORE_URL` | `https://api.dooers.ai` |
| `dooers-push` host | `--push-url` | `DOOERS_PUSH_URL` | `https://push.dooers.ai` |
| Target environment | `--env` | `DOOERS_ENV` | `prod` |

The CLI reads `--core-url` / `--push-url` once at the top-level Typer callback so every subcommand sees the same values. `dev` environment users typically set `DOOERS_CORE_URL=https://api.dev.dooers.ai` and `DOOERS_PUSH_URL=https://push.dev.dooers.ai` once in their shell profile and forget about it.

## 7. `dooers.yaml` format

```yaml
protocol_version: "1"
agent_id: ag_8h2k                # immutable; assigned by core on create
name: customer-support           # informational; mirrors the agent record
runtime: docker                  # docker | python | node
env_required:                    # names only (values come from creator's local .env)
  - OPENAI_API_KEY
  - SLACK_WEBHOOK_URL
```

`dooers agents create` writes this file. `dooers push` reads it.
Schema validated against `AgentManifest` in `dooers-protocol`. Unknown fields rejected (strict mode) to catch typos early.

## 8. Data flow — `dooers push`

1. **CLI loads `dooers.yaml`**, validates schema, extracts `agent_id`. (Or uses explicit positional arg.)
2. **CLI archives cwd** to a temp `.tar.gz`, respecting `.dooersignore` + defaults from v1.
3. **CLI POSTs** to `${DOOERS_PUSH_URL}/v1/push/{agent_id}` with the archive as multipart, `Authorization: Bearer <token>` from `~/.dooers/token`, and query params `tag`, `env`. Connection timeout: 600s.
4. **`dooers-push.verify_session`** extracts the bearer token from the incoming `Authorization` header and forwards it (as `Authorization: Bearer <token>`) to core's `/session/verify`. The token is **the same token** the CLI received at `dooers auth login` and stored at `~/.dooers/token` — `dooers-push` never mints or holds its own tokens. 401 if core rejects.
5. **`dooers-push.core_client.get_agent`** fetches the agent. 403 if `owner_user_id != session.user_id`. 404 if not found.
6. **GCS upload** at `gs://<bucket>/agents/{agent_id}/{ts}-archive.tar.gz`. Labels: `agent_id`, `owner_user_id`.
7. **Pipeline runs** auditor → provisioner → deployer in sequence. Each step gets the `PipelineContext`. Any step returning `status=failed` short-circuits with the error message.
8. **`deployer.deploy`** triggers Cloud Build (same steps as v1: docker build → push → `gcloud run deploy`). Cloud Run service name: `{agent_id}-{env}`. Env vars: base env (`GCP_PROJECT_ID`, etc.) + agent's `.env` file (parsed in build script, as in v1) + anything from `ctx.provisioned_env` (empty in POC).
9. **Poll Cloud Build operation** every 5s until `done==True`. Hard cap 540s (under the 600s HTTP timeout). On timeout: return `BuildStatus.failed` with a clear error.
10. **`describe_url`** calls Cloud Run admin API for `services/{agent_id}-{env}`. Returns `status.url`.
11. **`core_client.patch_agent_url(agent_id, url)`** updates the agent record.
12. **Return `PushResponse`** to CLI with status, URL, build_id, image.
13. **CLI prints** the URL on success; the build_id + error on failure.

## 9. Error handling

| Failure | HTTP status | CLI behavior |
|---|:-:|---|
| Token missing/expired | 401 | Print "Session expired. Run `dooers auth login`." Exit 1. |
| Agent not found | 404 | Print "Agent <id> not found." Exit 1. |
| Not the owner | 403 | Print "You do not own agent <id>." Exit 1. |
| Archive too large (>200MB) | 413 | Print size + suggest `.dooersignore`. Exit 1. |
| Auditor failed (future) | 422 | Print findings table. Exit 1. |
| Cloud Build failed | 500 | Print build_id + suggest checking Cloud Build console. Exit 1. |
| Cloud Build timeout (>540s) | 504 | Print build_id; build may still complete async. Exit 1. |
| Core API down | 503 | Print "Core API unreachable. Try again." Exit 1. |
| Network / unknown | 500 | Print short message + correlation_id from response header. Exit 1. |

All `dooers-push` error responses use a common `ErrorEnvelope` (`dooers-protocol/errors.py`):

```python
class ErrorEnvelope(BaseModel):
    error_code: ErrorCode    # enum
    message: str             # human-readable
    correlation_id: str      # echoed in logs
    details: dict = {}
```

## 10. Testing

POC stance: **smoke tests + manual end-to-end on dev, no unit-test rigor**. (Per user direction: "fast development without stress test units".)

- **`dooers-protocol`** — one test per model verifying round-trip JSON serde with example payloads.
- **`dooers-cli`** — one smoke test invoking each subcommand with `--help` (catches Typer wiring breakage). One end-to-end test against a mock `dooers-push` (using `httpx.MockTransport`) that exercises archive → upload → response parse.
- **`dooers-push`** — one test per pipeline step (stub steps test trivially). One end-to-end FastAPI `TestClient` test that mocks `core_client`, `storage`, `gcp.cloudbuild`, `gcp.cloudrun` and verifies the route returns a valid `PushResponse`.
- **Manual** — full end-to-end on the dev GCP project: login → create agent → push → see live URL → confirm URL persisted in core. This is the demo and the acceptance criterion.

Not in scope: load tests, fuzz tests, contract tests against real core, CI matrix.

## 11. Resource labels (billing-ready)

Every GCP resource created by `dooers-push` carries these labels from day one:

| Resource | Labels |
|---|---|
| GCS object | metadata: `agent_id`, `owner_user_id`, `pushed_at` |
| Cloud Build | `agent_id`, `owner_user_id`, `env` |
| Artifact Registry repo | (shared `agents` repo; per-image tags include `agent_id`) |
| Cloud Run service | `agent_id`, `owner_user_id`, `env`, `pushed_at` |

This is the only billing-related work in the POC. Cost data flows through GCP billing export to BigQuery; aggregation by label happens whenever the billing model is decided.

## 12. Migration from v1

The current `cli/dooers/cli.py` and `server/main.py` will be **moved** (not rewritten from scratch) into the new package layout:

| v1 location | v2 location | Action |
|---|---|---|
| `cli/dooers/cli.py` (login/logout/whoami) | `packages/dooers-cli/dooers/auth.py` | Extract, restructure under `dooers auth` subcommand |
| `cli/dooers/cli.py` (push) | `packages/dooers-cli/dooers/push.py` | Move; switch to `agent_id`-based, read `dooers.yaml`, use `push_client` |
| `cli/dooers/cli.py` (`.dooersignore`) | `packages/dooers-cli/dooers/ignore.py` | Extract pure functions |
| `server/main.py` (route + verify_session) | `packages/dooers-push/dooers_push/{main,auth}.py` | Split routing from auth |
| `server/main.py` (`_trigger_cloud_build_*`) | `packages/dooers-push/dooers_push/pipeline/deployer.py` + `gcp/cloudbuild.py` | Move; add polling + URL describe |
| `cloudbuild/cloudbuild.yaml` | `cloudbuild/cloudbuild.yaml` | Unchanged |
| `Dockerfile` | `packages/dooers-push/Dockerfile` | Adjust paths for new layout |

The existing `dooers==0.2.0` PyPI release is **not yanked**. The next release becomes `dooers==0.3.0` with the new subcommand structure. v1 commands (`dooers login`, `dooers push <name>`) remain as deprecation shims that print "use `dooers auth login` / `dooers push <agent_id>`" and exit, for one release cycle.

## 13. Open questions (must resolve before implementation)

| # | Question | Blocker level | Owner |
|---:|---|:-:|---|
| 1 | Do `GET/POST /agents` and `PATCH /agents/{id}` exist on core? If not, who/when adds them? | **HIGH** — blocks CLI agents commands and URL writeback | Backend team |
| 2 | What's the `agent_id` format? `ag_` + 8 chars? UUID? Decided by core. | Medium | Backend team |
| 3 | Is the synchronous push UX (~3–5 min CLI hold) acceptable for the demo? | Low — confirmed in brainstorming | Stakeholders |
| 4 | Cloud Run host for `dooers-push` in dev — reuse current `agent-deploy` Cloud Run service or stand up a new one? | Low | Eng |

## 14. Out of scope explicitly (so we don't drift)

- Auditor real implementation
- Provisioner real implementation (DB / Redis / RAG / LLM token reseller)
- Billing
- Async push + status polling
- Custom domain / LB
- Go rewrite of CLI
- Multi-env contract versioning beyond `PROTOCOL_VERSION = "1"`
- Web UI

## 15. Next step

Once this spec is approved and the open questions in §13 are resolved, transition to **writing-plans** skill to produce a step-by-step implementation plan that an engineer (or subagent) can execute.
