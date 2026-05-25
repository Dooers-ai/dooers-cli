# Dooers CLI v2 + `dooers-push` POC — Design Spec

**Status:** Draft for review
**Date:** 2026-05-26
**Companion doc:** `docs/2026-05-26-dooers-push-poc.md` (stakeholder-facing overview)
**Implementation plan:** `docs/superpowers/plans/2026-05-26-dooers-cli-v2-poc.md`

---

## 1. Overview

### What this is

Dooers is a platform where creators publish AI agents — chatbots, parsers, assistants, anything that runs as a containerized HTTP service. The **Dooers CLI** is the one tool a creator needs to go from "I have working code on my laptop" to "my agent is live at a URL on Dooers infrastructure."

This document specifies a Proof of Concept for **v2 of that CLI** plus a new server called **`dooers-push`** that owns the deploy pipeline. Together they deliver one focused capability: a creator can authenticate from their terminal, register agents in the Dooers catalog, and run `dooers push` to ship a build to Cloud Run — without ever touching the GCP console.

### Who it's for

- **Primary user:** an agent creator (typically a Python or Node developer) who writes agent code on a laptop, wants Dooers to host it, and doesn't want to learn the underlying cloud.
- **Secondary user:** the Dooers core team, who will eventually surface deployed agents to end-users through other Dooers products.

### What success looks like for the POC

A creator on a fresh laptop can, in under five minutes of human time:

1. `pip install dooers` and `dooers auth login --email <them>` — verified via OTP against the existing core API.
2. `dooers agents create --name my-agent` — registers an agent in core (or the local shim, see §2) and writes `dooers.yaml` in the current directory.
3. `dooers push` — the CLI archives the directory, ships it to `dooers-push`, and waits while the audit + Cloud Build + Cloud Run pipeline runs. At completion, it prints the live URL. Visiting that URL in a browser returns the agent's response.

What success **does not** look like in this POC: managed databases, billing, malicious-code blocking, custom domains. Those are deliberately deferred and listed in §4 (Non-goals).

### The moving parts at a glance

Three Python packages live in one monorepo at `dooers/dooers-cli`:

| Package | What it is | Where it runs |
|---|---|---|
| **`dooers`** (directory: `dooers-cli`) | The CLI itself. Subcommands: `auth`, `agents`, `push`. Installed by creators. | Creator's laptop. Distributed via PyPI. |
| **`dooers-push`** | The Cloud Run service that owns the push pipeline (auditor → provisioner → deployer) and orchestrates Cloud Build + Cloud Run. | Cloud Run, in Dooers' GCP project. |
| **`dooers-protocol`** | A tiny package defining every Pydantic model used in HTTP traffic between any client and `dooers-push`. | Imported by both. Published to PyPI for future SDKs. |

The CLI also talks to the **Dooers core API** (`api.dooers.ai`) for authentication and agent records. `dooers-push` talks to core only to verify sessions and to write back the deployed URL after a successful push.

---

## 2. Why this shape

This section names the design forces that pushed the architecture toward its current shape. Each subsection is one decision and its rationale, so a future reader can challenge them individually rather than as a bundle.

### 2.1 Why three packages instead of one

The naive approach is a single Python package containing both the CLI and the server. Tempting because it's simpler to set up — one `pyproject.toml`, one venv, no cross-package imports. We rejected it because:

- **The CLI and server ship on different cadences.** The CLI lives on creator laptops via PyPI; the server lives in Cloud Run. A creator running `dooers==0.3.0` from PyPI may talk to a `dooers-push` already on v0.5.0, and vice versa. If they only share a wire format by *convention* (i.e., the CLI builds JSON dicts that happen to match the server's Pydantic models), schema drift is silent until it breaks in someone's terminal.
- **Splitting protocol from implementation forces wire-shape discipline.** Putting `dooers-protocol` between the CLI and `dooers-push` means every request and response has a model that both sides import. Renaming `agent_name` to `agent_id` becomes a build error on both sides, not a 500 in production.
- **Future non-CLI clients get the contract for free.** A JS SDK, a web dashboard, or an IDE plugin would import the same `dooers-protocol` (or its OpenAPI-generated equivalent) and stay in sync automatically.

The cost is real: one extra package to publish, slightly more `uv sync` overhead. We judged this acceptable because `dooers-protocol` is small (~6 modules of Pydantic models) and the safety it buys is high.

### 2.2 Why `dooers-push` is its own service, not part of core

The push pipeline is fundamentally different from the rest of the core API:

- It accepts **large multipart uploads**. Agent archives are typically 1–50 MB. Routing those through the main API would degrade its other endpoints.
- It **runs for minutes, not milliseconds**. Synchronous push holds an HTTP connection open for 3–5 minutes while Cloud Build runs. Embedding that in core would block worker slots from serving everything else.
- It needs **GCP service-account credentials** (Cloud Build, Cloud Run, GCS, Artifact Registry). Granting those to core would broaden core's blast radius without need.
- It will **grow asymmetrically**: the auditor will pull in static-analysis libraries; the provisioner will eventually pull in Cloud SQL admin SDKs. None of that belongs in core.

Naming the service after the verb (`push`) is deliberate scope discipline: if you find yourself wanting to add `/agents` CRUD to `dooers-push`, the name is telling you to put it somewhere else.

### 2.3 Why the CLI talks to two services, not one

A simpler design would route every CLI call through `dooers-push`, which would proxy to core. We rejected that because:

- It would hide the data ownership: agent metadata belongs to core; push belongs to `dooers-push`. Routing through one service muddies that.
- It would add a hop and a coupling: a core outage would also take down `dooers agents list`.
- It would tempt scope creep on `dooers-push` every time a new CLI command needed backend support.

The slight cost is that the CLI configures two base URLs (`--core-url` and `--push-url`). Cheap.

### 2.4 Why synchronous push for the POC

Push could be async: the server kicks off Cloud Build and returns a build ID immediately; the CLI polls `dooers status <build-id>` until done. That's how mature platforms work (Vercel, Fly.io, Cloud Run itself).

We chose synchronous for the POC because:

- **No new infrastructure required.** Async push needs either a Cloud Build webhook into `dooers-push` (which then needs build-state persistence and an authenticated status endpoint) or a polling worker. That's a half-day of plumbing for something creators don't visibly benefit from in the POC.
- **The demo is more visceral.** "Run this command, wait 3 minutes, see the URL" is a single concrete moment. "Run this, get a build ID, run another command to check it" needs more explanation.
- **Cloud Run can hold the connection.** A 600-second request timeout is well within Cloud Run's limits.

Honest tradeoff: this design will be replaced by async push in v2. The CLI's `push_client.push()` interface is shaped to accept either — today it returns the final `PushResponse`; tomorrow it could return a queued response plus a poller. Migration cost is contained.

### 2.5 Why auditor and provisioner are typed stubs, not absent

The stakeholders flagged the auditor (maliciousness detection) and provisioner (managed DB/Redis isolation) as critical product questions. The temptation is to either (a) ship without them and address later, or (b) try to design them now.

We do neither. Instead, we ship them as **typed no-op steps in the pipeline**. The interfaces — `PipelineStep.run(ctx) → StepResult`, `AuditReport`, `InfraManifest` — are committed in `dooers-protocol` so future implementations slot in without changing callers. The stub steps return a successful `AuditReport(passed=True)` and an empty provisioned-env dict.

This buys us:

- A **demoable seam**: stakeholders see the pipeline structure today, even though it doesn't enforce anything yet. (M4 of the implementation plan makes the auditor visible by scanning the archive for endpoints and imports.)
- **Forward compatibility**: the day the real auditor lands, the only code that changes is `pipeline/auditor.py`. The CLI, the server route, the protocol — all untouched.
- **Avoidance of premature policy decisions**: "what's malicious?" and "shared DB vs per-agent DB?" are research questions that don't belong in this spec. The stub buys us time to answer them separately.

### 2.6 Why a local-file shim is an acceptable fallback for `/agents` endpoints

The CLI's `agents list / create / show` commands expect core to expose `GET /agents`, `POST /agents`, `PATCH /agents/{id}`. We don't yet know whether those exist or when they'll land.

To keep the POC unblocked, the CLI ships with two implementations of an `AgentStore` interface: `HTTPCoreAgentStore` (the real one) and `FileShimAgentStore` (which writes to `~/.dooers/agents.json`). An env var picks between them. The shim isn't a parallel system — it implements the same interface, so swapping in HTTP later is one URL change and one delete.

This is **not** a permanent architecture. It's a single-purpose unblocker for the demo, named honestly so nobody mistakes it for production state.

### 2.7 Why we kept the v1 archive + Cloud Build logic largely intact

The existing `deploy-service/server/main.py` already does the hard work: archive upload to GCS, Cloud Build trigger, env-file parsing, Cloud Run deploy with merged env vars. It works in production today.

We deliberately do **not** rewrite that logic. We extract it into `dooers-push/pipeline/deployer.py` and `dooers-push/gcp/cloudbuild.py` (and add a polling loop + URL describe that v1 didn't have), but the core trigger code is copied with minimal change. The risk of subtle breakage from a rewrite far outweighs the cosmetic gain.

---

## 3. Goal

Deliver a POC that lets an agent creator, from their terminal, **(a)** authenticate against the Dooers core API, **(b)** list and create agent records, and **(c)** run `dooers push` to upload code, run a (stubbed) audit + provision pipeline, build a container, deploy it to Cloud Run, and receive the live URL back — with that URL written back to the agent record in core.

The POC delivers this as a three-package monorepo (`dooers-cli`, `dooers-push`, `dooers-protocol`) so the wire contract between CLI and server is owned by a shared package and cannot drift.

## 4. Non-goals (explicit deferrals)

Each of these is intentional. The rationale for the auditor / provisioner deferrals is in §2.5; the rationale for the sync push deferral is in §2.4.

- **Real auditor implementation.** Ships as a no-op stub returning `AuditReport(passed=True, findings=[])`. Real implementation is a policy + research problem, not a code problem.
- **Managed DB / Redis / RAG provisioning.** Provisioner ships as no-op. Isolation-model decision belongs in a separate spec.
- **Billing.** No metering or invoicing. POC does ensure every GCP resource is labeled with `agent_id` + `owner_user_id` so cost attribution is possible later via GCP billing export (see §13).
- **Async push.** Push is synchronous: CLI blocks ~3–5 min, server polls Cloud Build, returns URL.
- **Custom domain / load balancer routing.** Cloud Run default URLs only.
- **Rewrite of CLI in Go.** Stays Python/Typer.
- **CI/CD pipeline for the monorepo.** Manual deploy in POC; CI design deferred.

## 5. Architecture

How to read the diagram below: each box is a process (or persistent client state for the laptop side). Arrows are HTTP calls; double-headed arrows mean both sides initiate. The two boundaries that matter most are (a) the CLI ↔ services boundary and (b) the strict read-mostly boundary between `dooers-push` and core.

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

These are the load-bearing invariants. Each one came from a specific design discussion (see §2).

1. **The CLI talks to exactly two external services**: `core API` (auth + agent metadata) and `dooers-push` (the push pipeline). Nothing else. *(Rationale: §2.3.)*
2. **`dooers-push` is read-mostly against core for agent metadata.** It performs `GET /agents/{id}` to verify ownership and exactly one `PATCH /agents/{id}` to write `deployed_url`. It does **not** host `/agents` CRUD. *(Rationale: §2.2 — naming-as-scope-discipline.)*
3. **Every CLI ↔ `dooers-push` request and response is a Pydantic model declared in `dooers-protocol`.** No JSON-by-convention. *(Rationale: §2.1.)*
4. **The pipeline inside `dooers-push` is three sequential steps behind a common typed interface** (`PipelineStep.run(ctx) -> StepResult`). The POC ships `auditor` and `provisioner` as no-op subclasses. *(Rationale: §2.5.)*
5. **Push is synchronous from the CLI's perspective.** Cloud Run timeout raised to 600s. *(Rationale: §2.4.)*

## 6. Repository layout

```
dooers-cli/                              # the repo (Dooers-ai/dooers-cli on GitHub)
├── packages/
│   ├── dooers-cli/                      # PyPI: published as `dooers`
│   │   ├── src/dooers/
│   │   │   ├── __init__.py
│   │   │   ├── cli.py                   # typer app root + subcommand groups
│   │   │   ├── settings.py              # global config (core url, push url, env)
│   │   │   ├── auth.py                  # login / logout / whoami subcommands
│   │   │   ├── agents.py                # list / create / show subcommands
│   │   │   ├── push.py                  # push command (archive + upload + wait)
│   │   │   ├── config.py                # dooers.yaml reader/writer
│   │   │   ├── token_store.py           # ~/.dooers/token + JWT expiry check
│   │   │   ├── core_client.py           # HTTP client for core (auth methods)
│   │   │   ├── agent_store.py           # AgentStore protocol + Http + FileShim
│   │   │   ├── push_client.py           # HTTP client for dooers-push
│   │   │   └── ignore.py                # .dooersignore + defaults (port from v1)
│   │   ├── tests/                       # smoke tests + pure-function tests
│   │   ├── pyproject.toml               # [project] name = "dooers"
│   │   └── README.md
│   │
│   ├── dooers-push/                     # Cloud Run service (not on PyPI)
│   │   ├── src/dooers_push/
│   │   │   ├── __init__.py
│   │   │   ├── main.py                  # FastAPI app, routes
│   │   │   ├── settings.py              # env-driven config (one place)
│   │   │   ├── auth.py                  # session verification (forwards to core)
│   │   │   ├── core_client.py           # GET /agents/{id}, PATCH /agents/{id}
│   │   │   ├── storage.py               # GCS upload
│   │   │   ├── pipeline/
│   │   │   │   ├── base.py              # PipelineStep ABC, StepResult, Context
│   │   │   │   ├── auditor.py           # STUB → real in M4 (scans archive)
│   │   │   │   ├── provisioner.py       # STUB (no infra)
│   │   │   │   ├── deployer.py          # Cloud Build trigger + Cloud Run deploy
│   │   │   │   └── runner.py            # sequence runner
│   │   │   └── gcp/
│   │   │       ├── cloudbuild.py        # ports v1 logic + adds polling
│   │   │       └── cloudrun.py          # service URL lookup
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── README.md
│   │
│   └── dooers-protocol/                 # PyPI: `dooers-protocol`
│       ├── src/dooers_protocol/
│       │   ├── __init__.py              # PROTOCOL_VERSION = "1"
│       │   ├── auth.py                  # AuthSession, WhoamiResponse
│       │   ├── agents.py                # AgentRecord, CreateAgentRequest, AgentManifest
│       │   ├── push.py                  # PushRequest, PushResponse, BuildStatus
│       │   ├── audit.py                 # AuditReport, AuditFinding, InfraManifest
│       │   └── errors.py                # ErrorCode enum + ErrorEnvelope
│       ├── tests/
│       ├── pyproject.toml
│       └── README.md
│
├── cloudbuild/
│   └── cloudbuild.yaml                  # unchanged from v1
├── docs/
│   ├── 2026-05-26-dooers-push-poc.md    # stakeholder overview
│   └── superpowers/
│       ├── specs/2026-05-26-dooers-cli-v2-design.md   # this file
│       └── plans/2026-05-26-dooers-cli-v2-poc.md
└── README.md
```

Note: each package is **independent** (its own `pyproject.toml`, `uv.lock`, `.venv`), following the rfnry/chat monorepo pattern. There is no top-level uv workspace. Cross-package imports use `tool.uv.sources` with editable paths.

## 7. Packages — file-by-file ownership

### 7.1 `dooers-protocol` (the wire contract)

A small package with no runtime dependencies beyond `pydantic`. All shapes used between CLI and `dooers-push` live here. Both packages import from it.

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

class AgentManifest(BaseModel):     # ./dooers.yaml shape, strict mode
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
    audit: AuditReport | None = None    # populated even on failure

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

### 7.2 `dooers-cli` (user-facing CLI)

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
                 [--tag --env]
```

**Key files:**

- `cli.py` — top-level Typer app, mounts subcommand groups, root callback resolves global `Settings`.
- `settings.py` — `Settings.resolve(...)` returns the core URL, push URL, and env (flag > env var > default).
- `auth.py` — login / whoami / logout. Token stored at `~/.dooers/token` with `0o600`.
- `agents.py` — list / create / show. Calls into `agent_store` (HTTP or file shim).
- `push.py` — reads `dooers.yaml` (or accepts explicit `agent_id`), archives cwd respecting `.dooersignore`, calls `push_client.push(...)`, shows spinner during the synchronous wait, prints `PushResponse` summary including audit info.
- `config.py` — `read_manifest() / write_manifest()` for `dooers.yaml`. Validates against `AgentManifest`.
- `token_store.py` — pure file IO + JWT-expiry parsing. Doesn't verify signatures (we re-verify against core on each authenticated request).
- `core_client.py` — thin HTTP wrapper for `/session/*` endpoints only. The auth flow lives here.
- `agent_store.py` — `AgentStore` Protocol + `HTTPCoreAgentStore` (talks to `/api/v1/agents`) + `FileShimAgentStore` (writes `~/.dooers/agents.json`). The shim is picked when `DOOERS_USE_CORE_AGENTS != "1"`. *(Rationale: §2.6.)*
- `push_client.py` — `push(agent_id, archive_path, tag, env) -> PushResponse`. Multipart upload, 600s timeout, returns the final `PushResponse`.
- `ignore.py` — extracted from v1's `_make_tar_gz_of_cwd` + `is_ignored`. Pure functions, easier to test.

### 7.3 `dooers-push` (Cloud Run server)

Single endpoint that matters: `POST /v1/push/{agent_id}`. The `main.py` is intentionally skinny; logic lives in `pipeline/` and `gcp/`.

```python
@app.post("/v1/push/{agent_id}")
async def push(agent_id: str, request: Request,
               archive: UploadFile = File(...),
               tag: str = Query("latest"),
               env: str = Query("prod")) -> PushResponse:
    settings = Settings.from_env()
    session = await verify_session(request, settings)
    token = request.headers["Authorization"][len("Bearer "):]
    core = CoreClient(base_url=settings.core_api_url, token=token)
    agent = await core.get_agent(agent_id, fallback_session=session)
    if agent.owner_user_id != session.user_id:
        raise HTTPException(403, "you do not own this agent")

    gcs_uri = await storage.upload_archive(settings, agent_id, archive, owner_user_id=session.user_id)
    ctx = PipelineContext(agent=agent, user=session, gcs_uri=gcs_uri, tag=tag, env=env)
    result = await run_pipeline(ctx, [AuditorStep(), ProvisionerStep(), DeployerStep(settings)])

    if result.status == BuildStatus.failed:
        return PushResponse(agent_id=agent_id, build_id=ctx.build_id or "",
                            image=ctx.image or "", status=BuildStatus.failed,
                            error=result.error, audit=ctx.audit_report)

    service_name = _service_name(agent_id, env)
    url = await describe_service_url(settings.gcp_project_id, settings.gcp_region, service_name)
    await core.patch_agent_url(agent_id, url)
    return PushResponse(agent_id=agent_id, build_id=ctx.build_id or "",
                        image=ctx.image or "", status=BuildStatus.succeeded,
                        url=url, audit=ctx.audit_report)
```

**`pipeline/base.py`** — the contract every step implements:

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

**`pipeline/auditor.py`** — POC starts as no-op stub; M4 of the plan upgrades it to scan the archive for endpoints + imports and return a visible `AuditReport`. Still never blocks the deploy.

**`pipeline/provisioner.py`** — no-op stub. Future: given `InfraManifest`, provision DB schema / Redis namespace, return env vars to inject.

**`pipeline/deployer.py`** — real logic. Wraps the existing v1 `_trigger_cloud_build_with_gcs_source` + adds Cloud Build polling + Cloud Run `describe` for URL. Service name: `{agent_id}-{env}` (not `{name}-{env}` as in v1 — IDs are stable, names can change). Labels every resource with `agent_id` and `owner_user_id`.

**`pipeline/runner.py`** — sequential execution. Stops on first failure. Returns final aggregated result.

## 8. CLI surface (user-facing reference)

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
dooers push [<agent_id>] [--tag <tag>] [--env prod|stg|dev]
```

**Global config (resolved in this precedence order, highest first):**

| Setting | Flag | Env var | Default |
|---|---|---|---|
| Core API host | `--core-url` | `DOOERS_CORE_URL` | `https://api.dooers.ai` |
| `dooers-push` host | `--push-url` | `DOOERS_PUSH_URL` | `https://push.dooers.ai` |
| Target environment | `--env` | `DOOERS_ENV` | `prod` |
| Agents backend | (none) | `DOOERS_USE_CORE_AGENTS` | unset (= shim mode) |

The CLI reads global config once at the top-level Typer callback so every subcommand sees the same values. `dev` environment users typically set `DOOERS_CORE_URL=https://api.dev.dooers.ai` and `DOOERS_PUSH_URL=https://push.dev.dooers.ai` once in their shell profile and forget about it.

## 9. `dooers.yaml` format

```yaml
protocol_version: "1"
agent_id: ag_8h2k                # immutable; assigned by core on create
name: customer-support           # informational; mirrors the agent record
runtime: docker                  # docker | python | node
env_required:                    # names only (values come from creator's local .env)
  - OPENAI_API_KEY
  - SLACK_WEBHOOK_URL
```

`dooers agents create` writes this file; `dooers push` reads it. Schema validated against `AgentManifest` in `dooers-protocol`. **Unknown fields are rejected** (strict mode) to catch typos early.

## 10. Data flow — `dooers push` step by step

(Read §5's diagram alongside this list.)

1. **CLI loads `dooers.yaml`**, validates schema, extracts `agent_id`. (Or uses the explicit positional arg.)
2. **CLI archives cwd** to a temp `.tar.gz`, respecting `.dooersignore` + defaults from v1.
3. **CLI POSTs** to `${DOOERS_PUSH_URL}/v1/push/{agent_id}` with the archive as multipart, `Authorization: Bearer <token>` from `~/.dooers/token`, and query params `tag`, `env`. Connection timeout: 600s.
4. **`dooers-push.verify_session`** extracts the bearer token from the incoming `Authorization` header and forwards it (as `Authorization: Bearer <token>`, with a cookie fallback) to core's `/session/verify`. The token is **the same token** the CLI received at `dooers auth login` and stored at `~/.dooers/token` — `dooers-push` never mints or holds its own tokens. 401 if core rejects.
5. **`dooers-push.core_client.get_agent`** fetches the agent. 403 if `owner_user_id != session.user_id`. 404 if not found. (In shim mode, fabricates a minimal record from `agent_id` + the verified session so the demo runs without core endpoints.)
6. **GCS upload** at `gs://<bucket>/agents/{agent_id}/{ts}-archive.tar.gz`. Object metadata: `agent_id`, `owner_user_id`, `pushed_at` (billing-ready labels — see §13).
7. **Pipeline runs** auditor → provisioner → deployer in sequence. Each step gets the `PipelineContext`. Any step returning `status=failed` short-circuits with the error message.
8. **`deployer.deploy`** triggers Cloud Build (same steps as v1: docker build → push → `gcloud run deploy`). Cloud Run service name: `{agent_id}-{env}`. Env vars: base env (`GCP_PROJECT_ID`, etc.) + agent's `.env` file (parsed in build script, as in v1) + anything from `ctx.provisioned_env` (empty in POC).
9. **Poll Cloud Build operation** every 5s until `done==True`. Hard cap 540s (under the 600s HTTP timeout). On timeout: return `BuildStatus.failed` with a clear error.
10. **`describe_url`** calls Cloud Run admin API for `services/{agent_id}-{env}`. Returns `status.url`.
11. **`core_client.patch_agent_url(agent_id, url)`** updates the agent record. (No-op in shim mode.)
12. **Return `PushResponse`** to CLI with status, URL, build_id, image, audit report.
13. **CLI prints** the audit summary (endpoints detected), then the live URL on success. On failure, prints the build_id and error.

## 11. Error handling

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

## 12. Testing

POC stance: **smoke tests + manual end-to-end on dev, no unit-test rigor**. (Per user direction: "fast development without stress test units".)

- **`dooers-protocol`** — one test per model verifying round-trip JSON serialization with example payloads.
- **`dooers-cli`** — TDD for pure-function modules (`token_store`, `agent_store` shim, `config`, `ignore`). Smoke tests assert each subcommand's `--help` works (catches Typer wiring breakage). HTTP clients tested manually against dev core.
- **`dooers-push`** — smoke tests for the pipeline stubs and `/health`. End-to-end FastAPI `TestClient` test that mocks `core_client`, `storage`, `gcp.cloudbuild`, `gcp.cloudrun` and verifies the route returns a valid `PushResponse`.
- **Manual** — full end-to-end on the dev GCP project: login → create agent → push → see live URL → confirm URL persisted in core (or in shim file). This is the demo and the acceptance criterion.

Not in scope: load tests, fuzz tests, contract tests against real core, CI matrix.

## 13. Resource labels (billing-ready)

Every GCP resource created by `dooers-push` carries these labels from day one — not because we have billing today, but because cost-attribution-after-the-fact is impossible if you didn't label up front.

| Resource | Labels |
|---|---|
| GCS object | metadata: `agent_id`, `owner_user_id`, `pushed_at` |
| Cloud Build | `agent_id`, `owner_user_id`, `env` (via tags) |
| Artifact Registry repo | (shared `agents` repo; per-image tags include `agent_id`) |
| Cloud Run service | `agent_id`, `owner_user_id`, `env`, `pushed_at` |

Cost data flows through GCP billing export to BigQuery; aggregation by label happens whenever the billing model is decided. *(See companion stakeholder doc for the open question on billing.)*

## 14. Migration from v1

The current `deploy-service/cli/dooers/cli.py` and `deploy-service/server/main.py` are **moved** (not rewritten from scratch) into the new package layout:

| v1 location | v2 location | Action |
|---|---|---|
| `cli/dooers/cli.py` (login/logout/whoami) | `packages/dooers-cli/src/dooers/auth.py` + `core_client.py` + `token_store.py` | Extract, restructure under `dooers auth` subcommand |
| `cli/dooers/cli.py` (push) | `packages/dooers-cli/src/dooers/push.py` + `push_client.py` | Move; switch to `agent_id`-based, read `dooers.yaml`, use `push_client` |
| `cli/dooers/cli.py` (`.dooersignore`) | `packages/dooers-cli/src/dooers/ignore.py` | Extract pure functions |
| `server/main.py` (route + verify_session) | `packages/dooers-push/src/dooers_push/{main,auth}.py` | Split routing from auth |
| `server/main.py` (`_trigger_cloud_build_*`) | `packages/dooers-push/src/dooers_push/pipeline/deployer.py` + `gcp/cloudbuild.py` | Move; add polling + URL describe |
| `cloudbuild/cloudbuild.yaml` | `cloudbuild/cloudbuild.yaml` | Unchanged |
| `Dockerfile` | `packages/dooers-push/Dockerfile` | Adjust paths for new layout |

The existing `dooers==0.2.0` PyPI release is **not yanked**. The next release becomes `dooers==0.3.0` with the new subcommand structure. v1 commands (`dooers login`, `dooers push <name>`) remain as deprecation shims that print "use `dooers auth login` / `dooers push <agent_id>`" and exit, for one release cycle.

## 15. Open questions (must resolve before / during implementation)

| # | Question | Blocker level | Owner |
|---:|---|:-:|---|
| 1 | Do `GET/POST /agents` and `PATCH /agents/{id}` exist on core? If not, who/when adds them? | **HIGH** — blocks the CLI's real (non-shim) agents commands and URL writeback | Backend team |
| 2 | What's the `agent_id` format? `ag_` + 8 hex chars (shim assumes this)? UUID? Decided by core. | Medium | Backend team |
| 3 | Is the synchronous push UX (~3–5 min CLI hold) acceptable for the demo? | Low — confirmed in brainstorming | Stakeholders |
| 4 | Cloud Run host for `dooers-push` in dev — reuse current `agent-deploy` Cloud Run service or stand up a new one? | Low | Eng |

## 16. Out of scope explicitly (so we don't drift)

If something below comes up during implementation, it belongs in a follow-up spec, not in this one:

- Auditor real implementation (rules, blocking policy, review workflow)
- Provisioner real implementation (DB / Redis / RAG / LLM token reseller; isolation model)
- Billing (metering, invoicing, plans, free tier)
- Async push + status polling + webhook plumbing
- Custom domain / load balancer routing
- Go rewrite of the CLI
- Multi-env contract versioning beyond `PROTOCOL_VERSION = "1"`
- Web UI

## 17. Next step

This spec is approved and the implementation plan is live at `docs/superpowers/plans/2026-05-26-dooers-cli-v2-poc.md`. The plan breaks the POC into four demo-driven milestones (M1 auth → M2 agents CRUD → M3 push round-trip → M4 visible auditor) with task-by-task instructions.

Execution: dispatch via `superpowers:subagent-driven-development` (recommended for the long plan) or `superpowers:executing-plans` (inline with checkpoints).
