# Per-tenant service-account isolation for the push pipeline

**Audience:** Dooers platform / DevOps
**Status:** Approved design — ready to plan
**Scope:** `dooers-push` (code), `dooers-protocol` (one additive error code), `dooers-cli` (error surfacing), and GCP infra in `dooers-agents` / `dooers-services`.
**Companion docs:** [`gcp-push-deploy.md`](../../../../dooers-push/docs/gcp-push-deploy.md), [`gcp-lb.md`](../../../../dooers-push/docs/gcp-lb.md) (in the `dooers-push` repo) — to be updated as part of implementation.

---

## 1. Problem

Today a single service account, **`agent-deploy-service@dooers-agents`**, is reused for four distinct jobs, and it holds broad project-wide roles on `dooers-agents`:

| Role (project-wide) | Grants |
|---|---|
| `roles/run.admin` | deploy / modify / **delete any** Cloud Run service in the project |
| `roles/storage.objectViewer` | read **every** object in **every** bucket (all tenants' source) |
| `roles/artifactregistry.writer` | push / overwrite images in the shared `agents` repo |
| `roles/iam.serviceAccountUser` | actAs itself (so deployed services run as it) |
| `roles/logging.logWriter` | write logs |

The same SA is set as the **runtime identity of every agent Cloud Run** (`gcp/cloudbuild.py` → `--service-account=agent-deploy-service@…`). Verified: all 4 live prod agents run as it. The Cloud Build pipeline also runs the user-controlled `docker build` **and** `gcloud run deploy` in the same worker under that same SA.

**Consequences (all of the risks raised in the request):**

- A malicious `Dockerfile`/build step can reach the metadata server, lift the build SA token, and read other agents' source from the bucket, push images, or deploy/overwrite other agents' Cloud Run services.
- An agent **at runtime** has `run.admin` + `storage.objectViewer` project-wide → it can read every other tenant's source and modify/delete any other Cloud Run service.

This is full cross-tenant compromise at build time and at runtime.

### Current-state facts (verified against live GCP, 2026-06-25)

- Projects: `dooers-services` (control plane / `dooers-push`), `dooers-agents` (agent infra + LB + AlloyDB). Region `southamerica-east1`.
- Source bucket: `gs://dooers-agents-src`. Object layout today: `agents/<agent_id>/<ts>-<file>` (no org dimension).
- Artifact Registry repo `agents` (shared) in `dooers-agents`.
- Control plane SA `dooers-push-runtime@dooers-services` holds on `dooers-agents`: `cloudbuild.builds.editor`, `compute.loadBalancerAdmin`, plus (resource-level) `storage.objectAdmin` on the bucket and `iam.serviceAccountUser` on `agent-deploy-service`.
- AlloyDB cluster `dooers-agents-db` exists with only a `postgres` superuser; **no IAM-auth DB users** → DB isolation is greenfield. Agents currently self-provide DB creds via `.env`.
- The push pipeline already resolves each agent's `organization_id` from core (for the hosting gate) but never uses it for identity. Agent Cloud Run services are labeled with `owner_user_id`, **not** org.

---

## 2. Goals & non-goals

**Goals**

1. Least-privilege identity for **each** push stage: ingest, build, deploy, runtime.
2. Per-organization runtime isolation: org A's agents can never read/modify org B's Cloud Run, bucket objects, or images.
3. Per-organization **build** isolation: a malicious build in org A cannot read org B's source or push to org B's images.
4. Remove deploy credentials from the user-controlled build environment entirely.
5. Migrate the 4 live agents without breaking them. Production has no real users yet, so a clean cutover is acceptable.
6. Additive infra: create new SAs/roles; do **not** delete the existing SAs (only strip their excess role bindings after migration).
7. Leave the **tenant runtime SA ready** to receive scoped database grants in a later effort.

**Non-goals (explicitly deferred)**

- Carving AlloyDB into per-org databases / IAM DB users (designed here at a high level, implemented separately).
- Async push + status polling.
- Deleting `agent-deploy-service` (defanged, not deleted).
- Private Cloud Build worker pools / VPC-SC / egress controls (noted as future hardening).
- Moving agent `.env` secrets into Secret Manager (future).

---

## 3. Target identity model

The principle: **every stage runs as a different identity with the minimum it needs, and user-controlled code (the Dockerfile) never shares a credential with deploy or runtime.**

| Stage | Identity | Scoped roles | Cross-org reach |
|---|---|---|---|
| **Ingest** (receive archive → GCS) | `dooers-push-runtime@dooers-services` (control plane, *our* code) | `storage.objectCreator` + `storage.objectViewer` on `gs://dooers-agents-src` (write at ingest, read its own uploads at deploy; was `objectAdmin`, which also allowed delete/overwrite) | n/a (trusted control plane) |
| **Build** (docker build + push) | **`build-<token>@dooers-agents`** (NEW, per org) | `artifactregistry.writer` on **own** repo `agents-<token>` · `logging.logWriter` · `storage.objectViewer` on the bucket **conditioned** to `agents/<org_id>/**` | None — cannot read other orgs' source, cannot push to other repos, no run.*, no actAs |
| **Deploy** (create/update Cloud Run) | control plane via **Cloud Run Admin API** (`run_v2`) | `run.developer` (project) + `iam.serviceAccountUser` on each `tenant-<token>` SA | Deploy is removed from the Cloud Build worker entirely |
| **Runtime** (agent container) | **`tenant-<token>@dooers-agents`** (NEW, per org) | `logging.logWriter` only (DB grants slot in later) | None — no run.*, storage, AR, or actAs |

### Per-org resources (created by provisioning)

For each organization with the hosting feature:

- `build-<token>@dooers-agents` — build SA (above roles)
- `tenant-<token>@dooers-agents` — runtime SA (above roles)
- Artifact Registry repo `agents-<token>` (docker, `southamerica-east1`) — per-org image isolation
- Control plane (`dooers-push-runtime`) granted `iam.serviceAccountUser` on **both** per-org SAs
- The Cloud Run service agent (`service-<projnum>@serverless-robot-prod.iam.gserviceaccount.com`) granted `artifactregistry.reader` on `agents-<token>` so it can pull the deployed image

Source objects move to a per-org prefix: `agents/<org_id>/<agent_id>/<ts>-<file>`.

### Naming scheme

`organizationId` from core is an arbitrary string and will not satisfy GCP's SA-ID / repo-ID rules (6–30 chars, `^[a-z]([-a-z0-9]*[a-z0-9])$`). Derive a deterministic, collision-resistant token:

```
token   = sha256(org_id).hexdigest()[:12]      # 12 hex chars, always valid
tenant  = f"tenant-{token}"                      # e.g. tenant-3f9a1b2c4d5e (19 chars)
build   = f"build-{token}"                       # e.g. build-3f9a1b2c4d5e  (18 chars)
ar_repo = f"agents-{token}"                      # e.g. agents-3f9a1b2c4d5e (19 chars)
```

For human/ops traceability, the real `organizationId` is stored as the SA **display name / description** and as a resource **label** on the SA, the repo, and the Cloud Run service (`org=<org_id>`, added now — agents are currently un-labeled by org). The GCS prefix and the build SA's IAM Condition use the **raw** `org_id`.

These helpers live in a pure, unit-tested `dooers_push/tenancy.py`; nothing else hard-codes SA names.

### Control-plane SA — final role set (`dooers-push-runtime@dooers-services` on `dooers-agents`)

- `storage.objectCreator` + `storage.objectViewer` on `gs://dooers-agents-src` (write at ingest, read its own uploads when the webhook re-parses env; **replaces** `objectAdmin`, dropping delete/overwrite)
- `cloudbuild.builds.editor` (create/poll builds) — keep
- `run.developer` (deploy via Run Admin API) — **NEW**
- `compute.loadBalancerAdmin` (LB registration) — keep
- `iam.serviceAccountUser` on each `build-<token>` and `tenant-<token>` — **NEW**, added per org by provisioning
- **Removed:** `iam.serviceAccountUser` on `agent-deploy-service` (after migration)

It is deliberately **not** granted `iam.serviceAccountAdmin` or project IAM-admin — provisioning is a separate privileged operation (§5), so a compromise of the request-serving control plane cannot create identities or grant itself roles.

---

## 4. Pipeline flow (after)

> **Async architecture (current `dooers-push` main `8152039`).** `POST /v1/push` returns **202** after triggering the build; build completion arrives via a Pub/Sub webhook (`POST /v1/internal/build-events`) that runs `finalize_deploy`. The security model is identical; the deploy simply happens in the webhook (still the trusted control plane) instead of in-request, because the image only exists after the build completes. The implementation plan's "Architecture re-baseline" section maps each change onto the async functions.

```
A) dooers push ──▶ POST /v1/push/{agent_id} → 202   (control plane: dooers-push-runtime)
  1. verify session + agent ownership (core)              [unchanged]
  2. hosting feature gate (core org settings)             [unchanged]
  3. resolve org_id → tenant/build SA + repo + prefix     [tenancy.py]
  4. PRECHECK: tenant SA exists? no → 403 org_not_provisioned
  5. upload archive → gs://…/agents/<org_id>/<agent_id>/<ts>-<file>  (objectCreator)
  6. Cloud Build (service_account = build-<token>): docker build → docker push  [NO deploy step]
  7. write BuildRecord{building, organization_id, gcs_uri}; return 202

B) Cloud Build completes ──▶ Pub/Sub ──▶ POST /v1/internal/build-events  (control plane)
  8. read image DIGEST from build.results.images[].digest
  9. re-parse .env / env.{env} from the archive in GCS (Python)  [env_files.py, keeps secrets in source bucket]
  10. DEPLOY via Run Admin API as tenant-<token>, image=<digest>, env=base+overrides,
      invoker_iam_disabled=true, labels incl. org=<token>        [gcp/cloudrun.py, in finalize_deploy]
  11. LB register (loadBalancerAdmin)                    [unchanged]
  12. patch hostUrl in core (prod only)                  [unchanged]
```

Key differences from today:

- **Deploy leaves Cloud Build.** The build (step 6) builds+pushes only as the build SA. The control plane does the deploy in the webhook (step 10). The build SA has no deploy/run rights and no actAs. (Today the build runs a third `gcloud run deploy` step as `agent-deploy-service` — that step is removed.)
- **Deploy by digest**, not the mutable tag, captured from `build.results.images[].digest`, to prevent a build from swapping the image after push.
- **Env-file merge moves to Python.** The bash `parse_env_file` in `_build_deploy_script` is replaced by a tested parser. In async it runs at deploy time in the webhook, re-reading the archive from GCS, so agent `.env` secrets stay confined to the source bucket and never land in the GCS-backed build-status store. Semantics preserved (skip blanks/comments, strip inline comments and surrounding whitespace, keep `KEY=VALUE` lines, `env.{env}` then `.env`).
- **`BuildRecord` carries `organization_id` + `gcs_uri`** so the post-build webhook can resolve the tenant SA and locate the archive.
- **Per-org image repo + GCS prefix.**

---

## 5. Provisioning (org lifecycle)

Provisioning is an **explicit, idempotent** operation, run by an operator (or later by core when an org gains the hosting feature) — **not** by the request-serving control plane.

Delivered as a Python CLI: `python -m dooers_push.provision <org_id> [--region …] [--project …]`. It imports the `tenancy.py` helpers and performs each step idempotently (check-then-create), shelling out to `gcloud` for transparency/auditability:

1. Create `tenant-<token>` SA (display name/description = `org_id`).
2. Create `build-<token>` SA (display name/description = `org_id`).
3. Create Artifact Registry repo `agents-<token>` (docker, region).
4. Bind roles:
   - `build-<token>`: `artifactregistry.writer` on `agents-<token>`; `logging.logWriter` (project); `storage.objectViewer` on `gs://dooers-agents-src` with condition `resource.name.startsWith("projects/_/buckets/dooers-agents-src/objects/agents/<org_id>/")`.
   - `tenant-<token>`: `logging.logWriter` (project).
   - serverless robot: `artifactregistry.reader` on `agents-<token>`.
   - control plane `dooers-push-runtime`: `iam.serviceAccountUser` on `build-<token>` and `tenant-<token>`.

Idempotency: every call tolerates "already exists" and re-asserts bindings. A future enhancement lets core invoke this when hosting is enabled; for now operators run it (and the migration runbook runs it for existing orgs).

The request path treats a missing tenant SA as **`org_not_provisioned`** (clear 403) rather than failing deep in deploy.

---

## 6. Code changes

### `dooers-push`

- **`tenancy.py` (new):** pure helpers — `org_token(org_id)`, `tenant_sa_email(org_id)`, `build_sa_email(org_id)`, `ar_repo(org_id)`, `source_prefix(org_id)`. Unit-tested for validity (length/charset), determinism, and collision-resistance.
- **`env_files.py` (new):** `parse_env_archive(path, env) -> dict[str,str]` — extract `env.{env}` then `.env` from a `.tar.gz`/`.tgz`/`.zip` and parse to a dict, matching the current bash semantics. Unit-tested against the bash behavior.
- **`gcp/cloudbuild.py`:** build is now **build + push only** (drop step 3 / `_build_deploy_script`). `service_account` = `build-<token>`. Image URI uses `agents-<token>` repo. `wait_for_build` (or a follow-up read) returns the resolved **digest** from `build.results.images[].digest`. Tags/labels keep `agent`/`owner`/`env` and add `org`.
- **`gcp/cloudrun.py` (new):** `deploy_service(...)` using `google.cloud.run_v2` — create-or-update the service `agent-<safe>-<env>` with `service_account=tenant-<token>`, `image=<digest>`, merged env, `cpu=1`/`memory=512Mi`/`min=1`/`max=3`/`timeout=300`/startup CPU boost, `invoker_iam_disabled=true` (replicates `--no-invoker-iam-check` for the DRS case), ingress=all, labels incl. `org`. Returns the service URL/identity for logging.
- **`pipeline/deployer.py`:** the post-build `finalize_deploy` / `_finalize_success` (run by the Pub/Sub webhook) gains the deploy: read digest from the `build` object → re-parse env from the archive in GCS → deploy (Run API as tenant SA, by digest) → LB register. The legacy synchronous `DeployerStep` only adopts the new `trigger_build` signature. Error handling preserved (failed-step, build-log URL).
- **`build_store.py`:** `BuildRecord` gains `organization_id` + `gcs_uri` so the post-build webhook can resolve the tenant SA and locate the archive.
- **`storage.py`:** object path → `agents/<org_id>/<agent_id>/<ts>-<file>`; add `organization_id` to blob metadata.
- **`main.py`:** push route adds the `org_not_provisioned` precheck (tenant SA existence) before upload, passes `org_id` to upload, and persists `organization_id`/`gcs_uri` on the `BuildRecord`; the `build_events` webhook rehydrates them onto the reconstructed `PipelineContext`.
- **`settings.py`:** add config as needed (e.g., source bucket already present; tenant/build SA + repo are derived, not configured). Remove the hard-coded `agent-deploy-service` assumption.
- **`provision.py` (new):** the provisioning CLI in §5.
- **Tests:** tenancy naming, env parser parity, digest capture, Run-API request construction (tenant SA + digest + labels + `invoker_iam_disabled`), provisioning idempotency (mock `subprocess`/gcloud), `org_not_provisioned` path. Update existing `test_cloudbuild_*` for the removed deploy step.

### `dooers-protocol`

- **`errors.py`:** add `ErrorCode.org_not_provisioned` (additive enum member). Bump protocol version per release process.

### `dooers-cli`

- Surface `org_not_provisioned` with an actionable message ("your organization isn't provisioned for hosting — contact Dooers"). Otherwise unchanged — isolation is server/infra-side.

### Docs

- Update `gcp-push-deploy.md`: new per-stage SA model, provisioning step, defanged `agent-deploy-service`, per-org repos/prefix. Add an "isolation model" section and the verification matrix.

---

## 7. Migration runbook (live, ordered, reversible)

Production has no real users; the 4 live agents must not break. All steps are additive until Phase 5.

**Phase 0 — prep.** Confirm required APIs enabled (iam, run, artifactregistry, cloudbuild, storage). Determine the `organizationId` for each of the 4 live agents (query core, or derive from the agent records). Record current `agent-deploy-service` bindings (for rollback).

**Phase 1 — provision existing orgs (additive).** Run `provision-org <org_id>` for each distinct org of the 4 agents. Creates `build-/tenant-` SAs, `agents-<token>` repos, and all bindings (incl. control-plane `serviceAccountUser`). Zero impact on running services.

**Phase 2 — control-plane roles (additive).** Grant `dooers-push-runtime` `run.developer` on `dooers-agents`. Replace its bucket binding `objectAdmin` with `objectViewer` + `objectCreator` (read its own uploads at deploy + write at ingest; drops delete/overwrite).

**Phase 3 — deploy new control plane.** Build + deploy the new `dooers-push` image to `dooers-services`. New pushes now use the new model.

**Phase 4 — migrate the 4 agents.**
- *Immediate runtime-hole close:* `gcloud run services update <svc> --region=… --service-account=tenant-<token>` for each agent → it stops running as `agent-deploy-service`. (Image still in the old shared repo; acceptable transiently — the runtime SA is what mattered.)
- *Full move (optional, at leisure):* re-push each agent through the new pipeline so its image lands in `agents-<token>`.

**Phase 5 — defang `agent-deploy-service`.** After confirming no service runs as it, remove `run.admin`, `storage.objectViewer`, `artifactregistry.writer`, and `iam.serviceAccountUser` (incl. the control-plane `serviceAccountUser` binding on it). Keep the SA (do not delete). It may retain harmless `logging.logWriter` or be left with no roles.

**Phase 6 — verify (§8).**

**Rollback:** re-add the removed bindings to `agent-deploy-service`; redeploy the previous `dooers-push` image; `gcloud run services update --service-account=agent-deploy-service` to revert runtime identity. All steps reversible.

---

## 8. Verification matrix

After migration, confirm each control AND each denial:

| Check | Expected |
|---|---|
| Each agent service `serviceAccountName` | `tenant-<token>` (not `agent-deploy-service`) |
| Agent service has label `org=<org_id>` | present |
| Impersonate `tenant-<org A>`, read `gs://dooers-agents-src/agents/<org B>/…` | **denied** |
| Impersonate `tenant-<org A>`, `gcloud run services update <org B's svc>` | **denied** |
| Impersonate `tenant-<org A>`, list/delete Cloud Run services | **denied** |
| Impersonate `build-<org A>`, read `gs://…/agents/<org B>/…` | **denied** |
| Impersonate `build-<org A>`, push to `agents-<org B>` | **denied** |
| Impersonate `build-<org A>`, `gcloud run deploy` | **denied** |
| New `dooers push` for a provisioned org | succeeds, runs as `tenant-<token>`, image in `agents-<token>` |
| `dooers push` for an unprovisioned org | clean `org_not_provisioned` 403 |
| `agent-deploy-service` after Phase 5 | no `run.admin`/`storage.objectViewer`/`artifactregistry.writer`/`serviceAccountUser` |

Use `gcloud --impersonate-service-account=<sa>` for the denial checks (requires `serviceAccountTokenCreator` for the operator, granted temporarily and removed after).

---

## 9. Follow-ups (designed, deferred)

- **Database isolation (next effort):** per-org AlloyDB database (or schema), AlloyDB IAM authentication, and a scoped IAM DB user per org. The tenant SA gains `roles/alloydb.client` + a database user bound to only that org's database; agents connect via the SA instead of `.env` creds. The tenant SA is already the single place this grant attaches.
- **Auto-provision from core** when an org gains the hosting feature (call `provision-org` from the org lifecycle).
- **Build egress controls:** private Cloud Build worker pool / VPC-SC to constrain what a malicious build can reach on the network.
- **Secret management:** move agent `.env` secrets into per-org Secret Manager with the tenant SA as accessor.
- **SA quota monitoring:** 2 SAs/org against the 100-SA/project default; raise quota before scale.
