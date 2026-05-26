# Dooers Load Balancer Integration — Design Spec

**Status:** Draft for review
**Date:** 2026-05-27
**Companion docs:**
- `docs/devops/gcp-lb.md` — one-time GCP setup runbook (devops handoff)
- `docs/superpowers/specs/2026-05-26-dooers-cli-v2-design.md` — base CLI v2 design this builds on
- `docs/superpowers/plans/2026-05-26-dooers-cli-v2-poc.md` — M1-M4 implementation plan

---

## 1. Overview

### What this is

When `dooers push` deploys an agent to Cloud Run, the agent currently gets a default URL like `https://ag-7q4r-dev-abcd-uc.a.run.app` — opaque, region-bound, and not under a Dooers-owned domain. This design replaces those raw URLs with stable Dooers-owned URLs (`https://ag-7q4r-dev.agents.dooers.ai`) by registering each pushed agent as a Backend Service in a global Dooers-managed Cloud Load Balancer.

The work has two parallel tracks:

1. **DevOps** — one-time GCP setup (LB + IP + cert + DNS + IAM). Documented in `docs/devops/gcp-lb.md`. Estimated 45 min of clicks + 30-60 min waiting for the SSL cert to provision.
2. **Code** — per-push LB registration in `dooers-push`. This spec.

Both tracks proceed in parallel. The code assumes the LB platform resources exist; if they don't, pushes fail loudly with `lb_registration_failed` until devops completes their part. There is no feature flag and no fallback to raw Cloud Run URLs — the platform contract is "agents get stable Dooers URLs" and producing anything else would be a bug, not graceful degradation.

### Who benefits

- **Creators**: stable, brandable URLs from the first push; never see Cloud Run URLs.
- **Dooers**: control over the URL space (cert, routing, observability, future WAF/CDN).
- **Stakeholders**: demoable URLs that look like a real product.

### Where this fits in the timeline

The base M3 milestone (`dooers push round-trip`) currently returns raw Cloud Run URLs. This spec folds the LB layer into M3 so the first end-to-end demo already shows `*.agents.dooers.ai` URLs. The M1-M4 plan will be amended to include the LB tasks; no separate M5 milestone.

## 2. Goal & non-goals

### Goal

After this spec is implemented and devops completes the `gcp-lb.md` setup, every successful `dooers push` returns a stable HTTPS URL of the form `https://{agent_id_safe}-{env}.agents.dooers.ai` that routes to the agent's Cloud Run service via the Dooers-managed global Load Balancer. The agent's `deployed_url` in core is populated with this URL.

### Non-goals

- **Custom domain support** — creator brings their own domain. Future spec.
- **`dooers agents delete` exposed** — the `LBManager.unregister_agent` API exists in code, but no CLI command calls it during the POC.
- **WAF or rate limiting at the LB** — future spec.
- **Multi-region Cloud Run** — POC stays in `us-central1`.
- **CDN / Cloud CDN for agents** — most agent traffic is dynamic; CDN buys little.
- **Migration of already-deployed (raw-URL) agents** — re-pushing them is sufficient.
- **Tightening IAM beyond `roles/compute.loadBalancerAdmin`** — narrower roles in a follow-up.

## 3. Architecture

Topology of the routing path. Five durable platform resources (created by devops, once) and three per-agent resources (created by `dooers-push` on every push). Cert and DNS are wildcards that cover every future agent automatically.

```
                Internet
                   │
                   │ HTTPS to ag-7q4r-dev.agents.dooers.ai
                   ▼
   ┌─────────────────────────────────────────┐
   │  Global Anycast IPv4 (static)           │  ← devops, once
   └────────────────┬────────────────────────┘
                    ▼
   ┌─────────────────────────────────────────┐
   │  Forwarding Rule :443                   │  ← devops, once
   └────────────────┬────────────────────────┘
                    ▼
   ┌─────────────────────────────────────────┐
   │  Target HTTPS Proxy                     │  ← devops, once
   │   └─ SSL Cert: *.agents.dooers.ai       │
   │      (Google-managed wildcard)          │
   └────────────────┬────────────────────────┘
                    ▼
   ┌─────────────────────────────────────────┐
   │  URL Map  (dooers-agents-url-map)       │  ← devops creates; dooers-push patches
   │  Host rules:                            │
   │   ag-7q4r-dev.agents.dooers.ai → BS_1   │
   │   ag-8h2k-prod.agents.dooers.ai → BS_2  │
   │   …                                     │
   │   default (no match)         → 404 BS   │
   └────────────────┬────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────┐
   │  Backend Service per agent              │  ← dooers-push, per push
   │   - protocol: HTTPS                     │
   │   - 1 Serverless NEG                    │
   └────────────────┬────────────────────────┘
                    ▼
   ┌─────────────────────────────────────────┐
   │  Serverless NEG                         │  ← dooers-push, per push
   │   - region: us-central1                 │
   │   - cloud_run.service: ag-7q4r-dev      │
   └────────────────┬────────────────────────┘
                    ▼
   ┌─────────────────────────────────────────┐
   │  Cloud Run service: ag-7q4r-dev         │  ← already created by DeployerStep
   └─────────────────────────────────────────┘
```

### URL convention

```
https://{agent_id_safe}-{env}.{lb_domain}
       ↑                ↑     ↑
       │                │     └── DOOERS_LB_DOMAIN (default: agents.dooers.ai)
       │                └─── prod | stg | dev
       └─── lowercase, underscores → hyphens (DNS-safe)
```

Examples:
- `https://ag-7q4r-dev.agents.dooers.ai`
- `https://ag-8h2k-prod.agents.dooers.ai`
- `https://ag-3m1p-stg.agents.dooers.ai`

### Boundary rules

1. **The LB is created and managed in `dooers-push` only.** Neither the CLI nor core touches the LB.
2. **URL Map mutations are append-only on push** (with idempotency — re-pushing the same agent updates the existing NEG to point at the latest Cloud Run revision; it does not create duplicates).
3. **The default Backend Service for unmatched hosts returns 404.** No accidental fallthrough to a random agent.
4. **All per-agent LB resources are labeled** `agent_id`, `owner_user_id`, `env` for traceability + billing attribution.
5. **No feature flag.** LB registration is always part of the happy path. If LB resources don't exist, pushes fail with a clear error until they do.

## 4. One-time GCP setup (devops handoff)

The durable platform resources are created once via the runbook at `docs/devops/gcp-lb.md`. This spec assumes that document has been executed before any push attempt succeeds.

**Names defined in the runbook (the contract between devops and the code):**

| Resource | Name |
|---|---|
| URL Map | `dooers-agents-url-map` |
| Static IP | `dooers-agents-lb-ip` |
| SSL cert | `dooers-agents-wildcard-cert` (covering `*.agents.dooers.ai`) |
| Default 404 backend service | `dooers-agents-default-404-bs` |
| Target HTTPS Proxy | `dooers-agents-https-proxy` |
| Forwarding rule | `dooers-agents-https-rule` |
| SA with LB perms | `agent-deploy-service@<PROJECT_ID>.iam.gserviceaccount.com` |
| DNS A record | `*.agents.dooers.ai. → <static IP>` |

Code reads these names from env vars (with sensible defaults) — see §6.

## 5. Per-push code flow

### Where the code lives

New module: `packages/dooers-push/src/dooers_push/gcp/loadbalancer.py`. One class `LBManager` plus one exception type `LBError`. The deployer step gains a final block that calls into it.

### `LBManager` interface

```python
class LBManager:
    """Per-agent LB registration. Idempotent on every call.

    All operations are idempotent: re-registering the same agent
    updates the existing NEG to point at the latest Cloud Run revision;
    it does not create duplicates.
    """

    def __init__(self, settings: Settings) -> None:
        self.project_id = settings.gcp_project_id
        self.region = settings.lb_region
        self.url_map_name = settings.lb_url_map
        self.domain = settings.lb_domain

    async def register_agent(self, agent_id: str, env: str) -> str:
        """Wire {agent_id}-{env} Cloud Run into the LB; return the URL.

        Steps:
        1. Ensure Serverless NEG exists (create-or-get).
        2. Ensure Backend Service exists (create-or-get); attach NEG.
        3. PATCH URL Map to include host rule routing
           {host}.{lb_domain} → this BS. PATCH preserves rules for
           other agents.
        4. Return the full HTTPS URL.

        Raises LBError on any GCP failure (URL Map missing, permission
        denied, quota exceeded, etc.).
        """

    async def unregister_agent(self, agent_id: str, env: str) -> None:
        """Reverse of register_agent. Used on agent delete.

        Order matters: remove host rule first, then BS, then NEG.
        You can't delete a BS while the URL Map references it; can't
        delete a NEG while a BS references it.

        Idempotent — missing resources are not an error here.
        """

    async def wait_until_reachable(self, url: str, timeout_s: int = 90) -> None:
        """Poll the URL until it returns a non-default response.

        LB updates propagate globally over ~30-60s. We poll once per
        second until we see a real response or hit timeout_s. Timeout
        is not a fatal error — the LB will become live shortly; we
        just couldn't confirm before returning. Logs a warning.
        """


class LBError(RuntimeError):
    """Any failure interacting with the LB. Carries the GCP error
    context (operation name, HTTP status, message) for diagnostics."""
```

### Naming helpers (pure functions, TDD candidates)

```python
def safe_agent_id(agent_id: str) -> str:
    """Convert an agent_id to a DNS-/GCP-safe form.

    'ag_7q4r' → 'ag-7q4r'.  Lowercases and replaces underscores.
    """
    return agent_id.lower().replace("_", "-")


def host_for(agent_id: str, env: str, lb_domain: str) -> str:
    """Return the per-agent LB hostname.

    host_for('ag_7q4r', 'dev', 'agents.dooers.ai')
    → 'ag-7q4r-dev.agents.dooers.ai'
    """
    return f"{safe_agent_id(agent_id)}-{env}.{lb_domain}"


def neg_name(agent_id: str, env: str) -> str:
    return f"agent-{safe_agent_id(agent_id)}-{env}-neg"


def bs_name(agent_id: str, env: str) -> str:
    return f"agent-{safe_agent_id(agent_id)}-{env}-bs"
```

### Idempotency contract

Each `LBManager` operation is implemented as create-or-get:

| Operation | First push | Subsequent push |
|---|---|---|
| NEG creation | succeeds | catches `AlreadyExists`, returns existing |
| Backend Service creation | succeeds | catches `AlreadyExists`, returns existing |
| URL Map host rule | added via PATCH | already present, PATCH is a set-op no-op |
| Cloud Run service | new revision | new revision, same service name |

Result: re-pushing the same agent produces exactly the same set of LB resources. No duplicates. No leaks. The post-state is deterministic regardless of how many times the push was attempted.

### Integration in `DeployerStep`

```python
class DeployerStep(PipelineStep):
    name = "deployer"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lb = LBManager(settings)

    async def run(self, ctx: PipelineContext) -> StepResult:
        # Phase 1: Cloud Build + Cloud Run (unchanged from M3)
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

        # Phase 2: LB registration (new)
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

### URL source in `main.py`

```python
# After successful pipeline:
url = ctx.lb_url            # always set on success
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

No `describe_service_url(...)` fallback. Either LB is ready and push succeeds with a Dooers URL, or LB isn't ready and push fails. No two URL formats living in agent records.

### Parallel development workflow

The code can ship fully complete before the LB exists:

1. **Write everything** — `LBManager`, deployer integration, settings, naming helpers, tests. Ship complete.
2. **Local development** — `dooers-push` runs in Docker locally, hitting the GCP project. As long as `DOOERS_LB_URL_MAP` points at a name that doesn't exist, `register_agent()` raises `LBError("URL Map not found")`. That's the correct failure mode and a useful integration signal.
3. **Deploy `dooers-push` to dev Cloud Run** — independent of LB. Service is live; `/health` works; pushes fail at the LB step with `lb_registration_failed`.
4. **DevOps completes `gcp-lb.md`.**
5. **First real `dooers push`** — succeeds end-to-end with a Dooers URL.

## 6. Data model + protocol changes

### `dooers-protocol` (one new enum value)

```python
# packages/dooers-protocol/src/dooers_protocol/errors.py
class ErrorCode(str, Enum):
    unauthenticated = "unauthenticated"
    forbidden = "forbidden"
    not_found = "not_found"
    archive_too_large = "archive_too_large"
    audit_failed = "audit_failed"
    build_failed = "build_failed"
    build_timeout = "build_timeout"
    core_unreachable = "core_unreachable"
    lb_registration_failed = "lb_registration_failed"   # new
    internal = "internal"
```

`PushResponse.url`, `AgentRecord.deployed_url`, and `AgentManifest` are unchanged — they were always shaped as "whatever the platform decides is the agent's URL." The LB just changes what value goes into those existing fields.

### `dooers-push` internal state — one new field on `PipelineContext`

```python
# packages/dooers-push/src/dooers_push/pipeline/base.py
class PipelineContext(BaseModel):
    # ... existing fields ...
    lb_url: str | None = None     # populated by DeployerStep after LB registration
```

Never crosses the wire; doesn't need a protocol change.

### `dooers-push` settings additions

```python
# packages/dooers-push/src/dooers_push/settings.py
@dataclass(frozen=True)
class Settings:
    # ... existing fields ...
    lb_domain: str         # default: "agents.dooers.ai"
    lb_url_map: str        # default: "dooers-agents-url-map"
    lb_region: str         # default: value of GCP_REGION, else "us-central1"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            # ... existing assignments ...
            lb_domain=os.environ.get("DOOERS_LB_DOMAIN", "agents.dooers.ai"),
            lb_url_map=os.environ.get("DOOERS_LB_URL_MAP", "dooers-agents-url-map"),
            lb_region=os.environ.get("DOOERS_LB_REGION", os.environ.get("GCP_REGION", "us-central1")),
        )
```

### What does not change

| Surface | Reason it stays |
|---|---|
| `dooers-cli` source files | CLI just prints `PushResponse.url`; doesn't care what shape the URL has |
| `dooers.yaml` schema | Local manifest carries `agent_id`; URL is decided server-side |
| Core API endpoints | `GET/PATCH /agents/{id}` accept `deployed_url`; just contains a different string |
| Pipeline interface | `PipelineStep.run(ctx) -> StepResult` unchanged |
| `PushResponse` model | Has `url: str | None`, populated identically |

### Migration of already-deployed agents

Agents deployed during the pre-LB window have raw `*.run.app` URLs in their records. After this spec lands:

- Re-pushing them overwrites `deployed_url` with the LB URL.
- Their Cloud Run service is unchanged (same name, new revision per push).
- The raw `*.run.app` URL still works — Cloud Run doesn't disable it just because something else fronts it. Both URLs are live; only the LB one is shown in the agent record.

No migration script is required for the POC. If a one-shot fix-up is desired post-POC, it iterates `dooers agents list`, calls `LBManager.register_agent()` for each, and PATCHes `deployed_url`.

## 7. Error handling

### Failure taxonomy

| Failure | When it happens | HTTP status to CLI | `ErrorCode` |
|---|---|:-:|---|
| URL Map not found | Devops setup incomplete, or env var misconfigured | 503 | `lb_registration_failed` |
| Permission denied | SA missing `roles/compute.loadBalancerAdmin` | 503 | `lb_registration_failed` |
| Host-rule quota exceeded | >1,000 host rules in URL Map; needs quota increase | 503 | `lb_registration_failed` |
| Cloud Run service not found | Race: NEG creation fires before Cloud Run deploy propagates (sub-second window) | 409 | `lb_registration_failed` |
| Existing host rule conflict | Same host already points to a different BS (data corruption) | 409 | `lb_registration_failed` |
| Transient API error | GCP API hiccup | 503 | `lb_registration_failed` |
| Wait-until-reachable timeout | Host rule live but not yet propagated globally after 90s | (success, with warning) | — |

### CLI behavior

Extends §11 of the base CLI v2 spec:

| Push outcome | CLI exit | Message |
|---|:-:|---|
| LB not ready (URL Map missing) | 1 | `Push failed: LB resources not ready. The platform team is still completing setup. Try again in a few hours.` |
| LB permission denied | 1 | `Push failed: dooers-push is missing LB permissions. Contact the platform team.` |
| LB quota exceeded | 1 | `Push failed: agent quota reached on this LB. Contact the platform team.` |
| LB conflict (409) | 1 | `Push failed: host {host} is already assigned to a different agent. Contact the platform team.` |
| LB transient error | 1 | `Push failed: temporary LB error. Re-run dooers push.` |
| LB propagation timeout | 0 (success) | `Live at: https://… (may take ~30s to be reachable from all regions)` |

`wait_until_reachable` timing out is **not a push failure**. The LB accepted the rule; the URL becomes live globally within ~60s. We log a warning and add a hint to the success message.

### Partial-failure recovery

Push is composed of independent idempotent operations. If any one fails midway, the next `dooers push` reaches the same end state:

| Crash point | State after crash | Next push outcome |
|---|---|---|
| After NEG create, before BS create | NEG exists; no BS | NEG `AlreadyExists` (silent); BS create succeeds; rule added |
| After BS create, before URL Map PATCH | NEG + BS exist; URL Map unchanged | Both `AlreadyExists`; URL Map PATCH adds rule |
| After URL Map PATCH, before `wait_until_reachable` returns | All resources exist | Next push is no-op for LB; only Cloud Build re-runs |

No manual cleanup step is ever required after a failed push. The creator re-runs `dooers push`.

### Race conditions (named, not solved)

**Race A: two pushes for the same agent at the same time.** Both attempt NEG create-or-get; one wins, the other gets `AlreadyExists`. Same for BS. URL Map PATCH is a set operation; second writer overwrites the same rule (no-op). Both pushes return success. Cloud Run keeps both revisions; traffic ends up on whichever Cloud Build completed last. Safe but wasteful. No cross-push locking in the POC.

**Race B: push completes, agent delete starts immediately.** Out of POC scope (no delete). Future design must serialize per-agent operations.

**Race C: cert isn't ACTIVE yet when first push lands.** `register_agent` succeeds (doesn't depend on cert state). `wait_until_reachable` fails (TLS handshake fails). CLI gets the propagation-warning message. Once cert is active (~30 min after DNS), URL works. Acceptable; `gcp-lb.md` step 9 instructs devops not to proceed until ACTIVE, which prevents this in practice.

### Cleanup on agent delete (designed, not exposed)

`LBManager.unregister_agent(agent_id, env)` is implemented and tested. No CLI command calls it during the POC. When `dooers agents delete` arrives in v2:

1. Remove host rule from URL Map first (cuts traffic).
2. Delete the Backend Service (requires no URL Map reference).
3. Delete the Serverless NEG (requires no BS reference).
4. Delete the Cloud Run service (independent; can be parallel to 1–3).

Each delete is idempotent — missing = no error.

### Observability

For diagnosing LB failures in production:

- **`dooers-push` Cloud Run logs** — every `LBManager` method emits a structured log line: `lb_op=register_agent agent_id=ag_7q4r env=dev neg=created bs=already_exists url_map_patch=ok elapsed_ms=2340`.
- **`correlation_id`** in `ErrorEnvelope` — printed by the CLI on failure; ops greps Cloud Logs by it.
- **GCP Operations API** — every LB call returns an operation name; logged so an operator can fetch the full GCP-side error.

Cloud Logging query that finds all LB failures:
```
resource.type="cloud_run_revision"
resource.labels.service_name="dooers-push-dev"
jsonPayload.lb_op=~".*"
severity>=ERROR
```

No new alerting infrastructure for the POC. Post-POC: a Cloud Monitoring alert on the count of `lb_op` ERROR lines per 5 min, paged through whatever you use for on-call.

## 8. Testing

POC stance is "smoke + manual end-to-end" per the base spec. Concretely for the LB integration:

### Unit tests (TDD where pure)

`packages/dooers-push/tests/test_lb_naming.py`:

- `safe_agent_id("ag_7q4r")` → `"ag-7q4r"`.
- `safe_agent_id("AG_7Q4R")` → `"ag-7q4r"` (lowercased).
- `host_for("ag_7q4r", "dev", "agents.dooers.ai")` → `"ag-7q4r-dev.agents.dooers.ai"`.
- `neg_name("ag_7q4r", "dev")` → `"agent-ag-7q4r-dev-neg"`.
- `bs_name("ag_7q4r", "dev")` → `"agent-ag-7q4r-dev-bs"`.
- Edge: agent_id with trailing/leading whitespace — caller responsibility, but the function should not silently strip (raise `ValueError` if input contains whitespace).
- Edge: agent_id > 50 chars — DNS label limit is 63; the host has `{id}-{env}.{domain}` so the agent_id portion is effectively 50ish chars max. Document this as a constraint; assume `agent_id` from core is short.

### Mock-based tests for `LBManager`

`packages/dooers-push/tests/test_lb_manager.py` using `unittest.mock` against the `google-cloud-compute` clients:

- `register_agent` issues the calls in order: NEG create → BS create → URL Map PATCH.
- Idempotency: when NEG.create raises `AlreadyExists`, the manager catches and proceeds.
- Idempotency: when BS.create raises `AlreadyExists`, the manager catches and proceeds.
- Idempotency: when URL Map PATCH includes an existing rule, no duplication (test by asserting the PATCH body has exactly one entry for our host).
- `register_agent` raises `LBError` when URL Map GET returns 404.
- `register_agent` raises `LBError` when permission denied.
- `unregister_agent` removes in the right order: host rule → BS → NEG. Each missing-resource case is non-fatal.
- `wait_until_reachable` returns normally on first 200 response.
- `wait_until_reachable` logs warning + returns on timeout (no exception).

### Smoke tests

`packages/dooers-push/tests/test_smoke.py` (extend existing):

- The push endpoint, with `LBManager` mocked to return a fixed URL, returns a `PushResponse` whose `url` matches the mock.

### Manual acceptance (the demo)

After devops completes `gcp-lb.md`:

1. Create a test agent: `dooers agents create --name lb-smoke-test`
2. `dooers push` — wait ~3-5 min
3. CLI prints `Live at: https://ag-xxx-dev.agents.dooers.ai`
4. `curl https://ag-xxx-dev.agents.dooers.ai` returns the agent's HTTP response
5. `dig +short ag-xxx-dev.agents.dooers.ai` returns the LB static IP
6. `gcloud compute url-maps describe dooers-agents-url-map` shows the new host rule
7. `dooers agents show ag_xxx` shows the LB URL in `deployed_url`
8. Re-run `dooers push` — succeeds with the same URL (idempotency check)

This is the acceptance criterion for the spec.

## 9. Ops + limits + cost

### Common ops commands

| Goal | Command |
|---|---|
| List all per-agent host rules | `gcloud compute url-maps describe dooers-agents-url-map --format='value(hostRules.hosts.flatten())'` |
| List all per-agent NEGs | `gcloud compute network-endpoint-groups list --filter='name~^agent-'` |
| Inspect a specific agent's routing | `gcloud compute url-maps describe dooers-agents-url-map` and grep for the agent host |
| Cert status | `gcloud compute ssl-certificates describe dooers-agents-wildcard-cert --global --format='value(managed.status)'` |
| Test from another region | `curl -sI https://ag-xxx-dev.agents.dooers.ai` from a remote shell |
| Manually unregister a stuck agent | See sequence in §7 "Cleanup on agent delete" — run as `gcloud` one-liners against the named resources |

### GCP limits (relevant for capacity planning)

| Limit | Default | Max with increase | Request increase at |
|---|---|---|---|
| URL Map host rules | 1,000 | 10,000 | ~500 agents per env |
| Serverless NEGs per region | 50 | 1,000+ | **~30 agents — early bottleneck** |
| Backend Services per project | 75 | 4,000+ | ~30 agents |
| Forwarding rules per project | 75 | 1,000+ | not in POC scope |
| SSL certs per project | 100 | 1,000 | doesn't apply (one wildcard cert) |

**Action item for ops:** the Serverless NEG and Backend Service quotas are the early bottlenecks. File a quota-increase request via GCP support around 30 active agents to avoid surprise failures.

### Cost

Per-LB monthly cost, independent of traffic:

- Global Forwarding Rule: ~$18/month ($0.025/hour × 720 hours).
- Static IP (in use): free.
- Serverless NEG, Backend Service, URL Map, SSL cert: free.

**Flat ~$18/month for the LB itself**, regardless of agent count. Per-agent runtime costs (Cloud Run, Cloud Build, Artifact Registry) are unchanged from M3.

### Cert renewal

Google-managed wildcard certs auto-renew. No human action required.

## 10. Open questions

| # | Question | Blocker | Owner |
|---|---|---|---|
| 1 | Is `dooers.ai` zone in Cloud DNS or another provider? Determines DNS step in `gcp-lb.md`. | Yes (for devops, not code) | DevOps |
| 2 | At what point do we request the Serverless NEG quota increase? | No (POC won't hit it) | DevOps / platform |

## 11. Out of scope (so we don't drift)

- `dooers agents delete` CLI command
- Per-agent custom domains
- WAF / rate limiting at the LB
- CDN for agent responses
- Multi-region deployment
- Migration script for existing raw-URL agents
- Tightening IAM beyond `roles/compute.loadBalancerAdmin`

## 12. Next step

Once this spec is approved and `docs/devops/gcp-lb.md` is on devops's queue, transition to **writing-plans** skill to produce a task-by-task implementation plan. The plan will:

- Add `lb_*` settings fields and the `lb_registration_failed` error code (Task ~1).
- Add `lb_url` to `PipelineContext` (Task ~1).
- Implement naming helpers (TDD) (Task ~2).
- Implement `LBManager.register_agent` with create-or-get for NEG and BS, and PATCH for URL Map (Tasks ~3-5).
- Implement `LBManager.wait_until_reachable` (Task ~6).
- Implement `LBManager.unregister_agent` (Task ~7).
- Update `DeployerStep` to call `LBManager` after Cloud Build (Task ~8).
- Update `main.py` to use `ctx.lb_url` as the URL source (Task ~9).
- Update the M3 smoke test (Task ~10).
- Update the M3 demo checklist to assert `*.agents.dooers.ai` URLs (Task ~11).

Estimated additional dev work to fold this into M3: ~1.5 days. Estimated devops work: ~45 min of clicks + 30-60 min wait for cert provisioning.

---

*Companion runbook for devops: [`docs/devops/gcp-lb.md`](../../devops/gcp-lb.md). Read both together when scheduling the work.*
