# GCP setup — deploying `dooers-push` + wiring the two projects

**Audience:** Dooers DevOps / platform team
**Status:** Ready to execute
**Companion doc:** [`gcp-lb.md`](./gcp-lb.md) — the one-time Load Balancer setup. Run this doc and that one together; this doc tells you *when* to run it (Phase D).

This is the end-to-end runbook to take the v1 CLI live: stand up the `dooers-push` Cloud Run service that backs `dooers push`, and connect it to the agent infrastructure (Cloud Build, Cloud Run, Artifact Registry, GCS, and the Load Balancer). `gcp-lb.md` covers only the durable LB resources; this doc covers everything else and the cross-project glue.

---

## Two-project layout

| Project | What lives here |
|---|---|
| **`dooers-agents`** | Everything agent-side: GCS source bucket, Artifact Registry (`agents` repo), per-agent Cloud Build, per-agent Cloud Run services, **all Load Balancer resources**, and the `agent-deploy-service` SA. |
| **`dooers-services`** | Only the `dooers-push` Cloud Run service itself, its image, and its runtime SA (`dooers-push-runtime`). |

```
dooers-services (serves the CLI)            dooers-agents (agent infra + LB)
┌──────────────────────────┐                ┌──────────────────────────────────────┐
│ Cloud Run: dooers-push   │ ── build ────▶ │ Cloud Build → Artifact Registry        │
│  runtime SA:             │ ── deploy ───▶ │ Cloud Run: {agent}-{env}               │
│  dooers-push-runtime     │ ── register ─▶ │ LB: IP → cert → proxy → url-map         │
└──────────────────────────┘                │ GCS source bucket │ agent-deploy SA     │
   push.dooers.ai (DNS)                      │   agents.dooers.ai (DNS → LB static IP) │
                                             └──────────────────────────────────────┘
       core API (api.dooers.ai) — pre-existing: auth + agent metadata
```

### Why agents and the LB share one project (non-negotiable)

The push pipeline reads a single `GCP_PROJECT_ID` for *all* agent-side work — source upload, build, Cloud Run deploy, and LB registration (see `settings.py` and `gcp/loadbalancer.py`). A Serverless NEG can only point at a Cloud Run service **in its own project and region**. Therefore the agent Cloud Run services and the LB must live together. We put both in `dooers-agents` and set `GCP_PROJECT_ID=dooers-agents` on the push service — even though that service *runs* in `dooers-services`. The push service reaches across projects via the IAM grants in Phase C.

---

## Path-based routing (`agents.dooers.ai/<agent-id>`)

Confirmed supported and already implemented. A Global External Application Load Balancer URL map routes per-agent **path rules** (`/ag-7q4r-dev`, `/ag-7q4r-dev/*`) to per-agent backend services, with `pathPrefixRewrite="/"` so the agent never sees its own prefix (`loadbalancer.py:_upsert_path_rule`). One host, one Google-managed single-domain cert, one A record. The deprecated subdomain/wildcard approach is **not** used — follow `gcp-lb.md` as written.

---

## Prerequisites

- `gcloud` installed and authenticated (`gcloud auth login`), with access to both projects.
- **Core API already serving** `https://api.dooers.ai` (`/api/v1/session/verify` + agents CRUD). The push service forwards the user's bearer token to it; nothing here provisions core.
- Region `us-central1` assumed throughout — substitute consistently if different. The LB NEG region **must** match the agent Cloud Run region.
- Two GCP projects created: `dooers-agents`, `dooers-services`.

Set shell vars used below:

```bash
AGENTS=dooers-agents
SERVICES=dooers-services
REGION=us-central1
BUCKET=dooers-agents-src        # must be globally unique; change if taken
```

---

## Phase A — `dooers-agents`: agent-side foundation

```bash
gcloud services enable \
  compute.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com storage.googleapis.com \
  --project=$AGENTS

# Build/run SA used by agent builds and agent Cloud Run services.
# The name is hardcoded in gcp/cloudbuild.py as agent-deploy-service@<project>.
gcloud iam service-accounts create agent-deploy-service --project=$AGENTS

# Artifact Registry repo named "agents" (matches the ARTIFACT_REPO default).
gcloud artifacts repositories create agents \
  --repository-format=docker --location=$REGION --project=$AGENTS

# GCS bucket for uploaded agent source archives.
gcloud storage buckets create gs://$BUCKET --location=$REGION --project=$AGENTS

# Roles agent-deploy-service needs to do its job inside each build:
for R in roles/run.admin roles/artifactregistry.writer roles/logging.logWriter \
         roles/storage.objectViewer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding $AGENTS \
    --member=serviceAccount:agent-deploy-service@$AGENTS.iam.gserviceaccount.com \
    --role=$R
done
```

`roles/iam.serviceAccountUser` lets it deploy Cloud Run services that *run as* itself; `roles/run.admin` lets it set `--allow-unauthenticated` on agent services so the LB can reach them.

---

## Phase B — `dooers-services`: build & deploy `dooers-push`

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com --project=$SERVICES

# Runtime identity for the push service.
gcloud iam service-accounts create dooers-push-runtime --project=$SERVICES

# Artifact Registry repo for the push image.
gcloud artifacts repositories create services \
  --repository-format=docker --location=$REGION --project=$SERVICES

IMAGE=$REGION-docker.pkg.dev/$SERVICES/services/dooers-push:v1
```

**Build the image.** The Dockerfile lives in `packages/dooers-push/` but the build context must be `packages/` (so it can pull in the `dooers-protocol` sibling). The committed `cloudbuild.yaml` handles this — run it with `packages/` as the upload source:

```bash
gcloud builds submit packages/ \
  --config=packages/dooers-push/cloudbuild.yaml \
  --substitutions=_IMAGE=$IMAGE \
  --project=$SERVICES
```

**Deploy.** `--allow-unauthenticated` because the CLI authenticates with a *Dooers* session token (forwarded to core), not a GCP token.

```bash
gcloud run deploy dooers-push \
  --image=$IMAGE --region=$REGION --project=$SERVICES \
  --service-account=dooers-push-runtime@$SERVICES.iam.gserviceaccount.com \
  --allow-unauthenticated \
  --set-env-vars=GCP_PROJECT_ID=$AGENTS,GCP_REGION=$REGION,BUCKET_NAME=$BUCKET,ARTIFACT_REPO=agents,CORE_API_URL=https://api.dooers.ai,ENVIRONMENT=prod,DOOERS_LB_DOMAIN=agents.dooers.ai,DOOERS_LB_URL_MAP=dooers-agents-url-map,TRUSTED_HOSTS=push.dooers.ai
```

Note `GCP_PROJECT_ID=dooers-agents` even though the service runs in `dooers-services` — that is the whole cross-project design. Grab the service URL for now (used until `push.dooers.ai` DNS exists):

```bash
gcloud run services describe dooers-push --region=$REGION --project=$SERVICES \
  --format='value(status.url)'
```

---

## Phase C — cross-project IAM (the glue)

Grant `dooers-push-runtime` (in `dooers-services`) what it needs **on `dooers-agents`**:

| Grant | Scope | Why |
|---|---|---|
| `roles/cloudbuild.builds.editor` | project `dooers-agents` | create/poll agent builds |
| `roles/compute.loadBalancerAdmin` | project `dooers-agents` | create per-agent NEG/Backend Service, patch URL map |
| `roles/iam.serviceAccountUser` | SA `agent-deploy-service` | submit builds/deploys that run **as** `agent-deploy-service` |
| `roles/storage.objectAdmin` | bucket `$BUCKET` | upload agent source archives |

```bash
RT=dooers-push-runtime@$SERVICES.iam.gserviceaccount.com

gcloud projects add-iam-policy-binding $AGENTS \
  --member=serviceAccount:$RT --role=roles/cloudbuild.builds.editor
gcloud projects add-iam-policy-binding $AGENTS \
  --member=serviceAccount:$RT --role=roles/compute.loadBalancerAdmin
gcloud iam service-accounts add-iam-policy-binding \
  agent-deploy-service@$AGENTS.iam.gserviceaccount.com \
  --member=serviceAccount:$RT --role=roles/iam.serviceAccountUser --project=$AGENTS
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member=serviceAccount:$RT --role=roles/storage.objectAdmin
```

> **Deviation from `gcp-lb.md` Step 8:** that step grants `compute.loadBalancerAdmin` to `agent-deploy-service` because it assumes the push service runs *as* that SA in a single project. In this two-project setup the LB calls originate from `dooers-push-runtime`, so the grant above replaces it. **Skip Step 8 of `gcp-lb.md`.**

---

## Phase D — Load Balancer in `dooers-agents`

Run **`gcp-lb.md` Steps 1–7 and 9–10**, with `--project=dooers-agents` on every command. **Skip Step 8** (handled in Phase C above). That produces: static IP → single-domain managed cert (`dooers-agents-cert`) → HTTPS proxy → URL map `dooers-agents-url-map` with the `agents-pm` path matcher + 404 default → forwarding rule, plus the `agents.dooers.ai` A record. Do not declare done until the cert reads `ACTIVE`.

---

## Phase E — DNS

`dooers.ai` does **not** need to be hosted in GCP — the managed cert provisions off the A record regardless of provider. Find where the zone lives:

```bash
dig NS dooers.ai +short
```

- `ns-cloud-*.googledomains.com` → Cloud DNS (use the `gcloud dns` commands in `gcp-lb.md`).
- Cloudflare / Route53 / registrar nameservers → add records in that provider's dashboard.

Records:

1. **`agents.dooers.ai` → A → LB static IP** (created in Phase D / `gcp-lb.md` Step 7).
2. **`push.dooers.ai` → the push service.** Simplest start: skip this and point the CLI at the `*.run.app` URL via `DOOERS_PUSH_URL`. For the clean hostname, add a **Cloud Run domain mapping** for `push.dooers.ai` (or front it with its own LB), and ensure `push.dooers.ai` is in `TRUSTED_HOSTS`.

---

## Phase F — end-to-end verification

```bash
# TLS + default backend (after the cert is ACTIVE, before any push):
curl -sI https://agents.dooers.ai/anything       # valid cert; 404 from placeholder

# Drive the CLI against the new services:
export DOOERS_CORE_URL=https://api.dooers.ai
export DOOERS_PUSH_URL=https://push.dooers.ai    # or the run.app URL from Phase B
dooers login usuario.frndvrgs@gmail.com
dooers agents create --name smoke-test
dooers push                                      # ~3-5 min

curl https://agents.dooers.ai/ag-xxxx-dev/       # the agent's "/" response
gcloud compute url-maps describe dooers-agents-url-map \
  --project=$AGENTS --format="yaml(pathMatchers)"   # shows the new path rule
dooers push                                      # re-run: same URL (idempotency)
```

---

## `dooers-push` environment variables

| Var | Required | Default | Notes |
|---|:-:|---|---|
| `GCP_PROJECT_ID` | ✅ | — | Set to **`dooers-agents`** (agents + LB live there). |
| `BUCKET_NAME` | ✅ | — | GCS source bucket in `dooers-agents`. |
| `GCP_REGION` | | `us-central1` | Agent Cloud Run + Cloud Build region. |
| `ARTIFACT_REPO` | | `agents` | Artifact Registry repo in `dooers-agents`. |
| `CORE_API_URL` | | `https://api.dooers.ai` | Core, for `/api/v1/session/verify`. |
| `ENVIRONMENT` | | `dev` | Set `prod`; enables host-header protection (needs `TRUSTED_HOSTS`). |
| `TRUSTED_HOSTS` | | `*` | Comma-separated; set to your push hostname(s) when `ENVIRONMENT=prod`. |
| `DOOERS_LB_DOMAIN` | | `agents.dooers.ai` | Public agent domain. |
| `DOOERS_LB_URL_MAP` | | `dooers-agents-url-map` | Must match the URL map from Phase D. |
| `DOOERS_LB_REGION` | | `GCP_REGION` | NEG region; must match agent Cloud Run region. |
| `REQUEST_TIMEOUT` | | `10` | Seconds, outbound to core. |
| `RATE_LIMIT_PUSH` | | `10/minute` | Per-IP push rate limit. |

---

## Troubleshooting

**`gcloud builds submit` fails pushing the image** — the Cloud Build service account in `dooers-services` needs Artifact Registry write + logging. Grant:
```bash
PNUM=$(gcloud projects describe $SERVICES --format='value(projectNumber)')
gcloud projects add-iam-policy-binding $SERVICES \
  --member=serviceAccount:$PNUM@cloudbuild.gserviceaccount.com --role=roles/artifactregistry.writer
gcloud projects add-iam-policy-binding $SERVICES \
  --member=serviceAccount:$PNUM@cloudbuild.gserviceaccount.com --role=roles/logging.logWriter
```

**Push fails `LB registration failed: URL map ... not found`** — Phase D hasn't run, or `DOOERS_LB_URL_MAP` is wrong. Confirm the URL map exists in `dooers-agents`.

**Push fails `path matcher 'agents-pm' missing`** — `gcp-lb.md` Step 5's `add-path-matcher` was skipped. Re-run it.

**Push fails with a permission error on NEG/build/SA** — a Phase C grant is missing. Re-check the four bindings, especially `serviceAccountUser` on `agent-deploy-service`.

**Push succeeds but the URL 404s for ~30–60s** — normal LB propagation. `wait_until_reachable` warns rather than fails; the path goes live shortly.

**Image build can't find `dooers-protocol`** — you built with the wrong context. The source must be `packages/` (via the `cloudbuild.yaml`), not `packages/dooers-push/`.

---

## What's a one-time setup vs. per-push

Everything in this doc and `gcp-lb.md` is **one-time**. After it's done, every `dooers push` automatically: uploads source to GCS → Cloud Build (build + push image + `gcloud run deploy`) → LB registration (NEG → Backend Service → URL-map path rule) → returns `https://agents.dooers.ai/<agent-id>[-env]`. No manual steps per push.
