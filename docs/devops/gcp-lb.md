# GCP Load Balancer — one-time setup for `dooers-push`

**Audience:** Dooers DevOps / platform team
**Status:** Ready to execute
**Estimated time:** ~45 min in the console + 15–60 min waiting for SSL cert to provision

This document describes the **one-time** GCP setup required before the `dooers-push` Cloud Run service can wire newly deployed agents into the global Dooers load balancer. After this setup completes, every `dooers push` will result in the deployed agent being reachable at a stable URL like `https://agents.dooers.ai/ag-7q4r-dev`.

The application code (`dooers-push`) handles the **per-push** LB updates (Serverless NEG creation, Backend Service, per-agent path rule). This document covers only the durable platform resources that exist once.

---

## Architecture summary

```
Internet
   │  HTTPS → agents.dooers.ai/{agent-id}
   ▼
[ Global Anycast IPv4 (static) ]
   ▼
[ Forwarding Rule :443 ]
   ▼
[ Target HTTPS Proxy ]      ──── SSL cert: agents.dooers.ai (single-domain, Google-managed)
   ▼
[ URL Map: dooers-agents-url-map ]
   │   host: agents.dooers.ai → path matcher: agents-pm
   │   path rules added per push by dooers-push (with prefix rewrite → /)
   │   default → 404 placeholder
   ▼
[ Backend Service per agent ] ── created per push
   ▼
[ Serverless NEG per agent ] ── created per push
   ▼
[ Cloud Run service: {agent_id}-{env} ]
```

Six resources are created in this one-time setup (steps below):

1. Global static IPv4
2. Google-managed single-domain SSL certificate (`agents.dooers.ai`)
3. Placeholder 404 default backend (Cloud Run + NEG + Backend Service)
4. URL Map with default-404 backend **and** a named `agents-pm` path matcher
5. Target HTTPS Proxy
6. Global Forwarding Rule

Plus:
- One IAM grant on the `dooers-push` service account
- One DNS A record (`agents.dooers.ai`)

---

## Prerequisites

- **GCP role:** Owner, or (Compute Admin + Cloud Run Admin + DNS Administrator + Project IAM Admin) on the target project.
- **DNS:** `dooers.ai` zone must be in Cloud DNS, or you must have write access to whichever provider hosts it.
- **Region:** Cloud Run region is assumed to be `us-central1`. Substitute everywhere if different.
- **Project ID:** Write yours down: `_______________________________`. Substitute `<PROJECT_ID>` below.
- **Existing service account:** `agent-deploy-service@<PROJECT_ID>.iam.gserviceaccount.com` should already exist (carry-over from the v1 deploy-service). If it doesn't, create it first with: `gcloud iam service-accounts create agent-deploy-service --project=<PROJECT_ID>`.

---

## Step 1 — Enable the Compute Engine API

The Compute Engine API powers Cloud Load Balancing even when no VMs are involved.

**Console:**
- APIs & Services → Library → search "Compute Engine API" → **Enable**.

**gcloud:**
```bash
gcloud services enable compute.googleapis.com --project=<PROJECT_ID>
```

Verify:
```bash
gcloud services list --enabled --project=<PROJECT_ID> | grep compute
```

---

## Step 2 — Reserve a global static IPv4

**Console:**
- VPC network → IP addresses → **Reserve external static address**
- Name: `dooers-agents-lb-ip`
- Network Service Tier: **Premium**
- IP version: **IPv4**
- Type: **Global**
- Click **Reserve**.
- **Record the IP** (e.g., `34.120.55.88`) — needed in Step 7.

**gcloud:**
```bash
gcloud compute addresses create dooers-agents-lb-ip \
  --global --ip-version=IPV4 --project=<PROJECT_ID>

# Read it back:
gcloud compute addresses describe dooers-agents-lb-ip \
  --global --project=<PROJECT_ID> --format='value(address)'
```

---

## Step 3 — Create the single-domain SSL certificate

We use a Google-managed classic compute SSL certificate scoped to the single host `agents.dooers.ai`. This is simpler than a wildcard cert — no DNS-01 authorization is needed. The cert provisions automatically once DNS resolves to the LB IP and the LB is serving on port 443 (Step 7 triggers this).

**Console:**
- Network security → Certificate Manager → **Classic certificates** tab → **Add certificate**
- Name: `dooers-agents-cert`
- Creation mode: **Create Google-managed certificate**
- Domains: `agents.dooers.ai`
- Click **Create**.

The certificate's status will start at **PROVISIONING**. It will not become **ACTIVE** until Step 7 (DNS) is complete. That's normal. After DNS lands, provisioning typically takes 15–60 min.

**gcloud:**
```bash
gcloud compute ssl-certificates create dooers-agents-cert \
  --domains=agents.dooers.ai \
  --global \
  --project=<PROJECT_ID>
```

---

## Step 4 — Create the placeholder default-404 backend

The URL Map requires a default backend service for unmatched paths. We deploy a tiny placeholder Cloud Run service, then wrap it in a NEG and Backend Service.

**Console:**

**4a. Deploy placeholder Cloud Run service**
- Cloud Run → **Deploy container**
- Container image URL: `gcr.io/cloudrun/hello`
- Service name: `dooers-agents-default-404`
- Region: **us-central1**
- Authentication: **Allow unauthenticated invocations**
- CPU allocation: **Only during request processing** (cheaper)
- Click **Create**. Wait ~30s for green status.

**4b. Create the Serverless NEG**
- Network services → Network endpoint groups → **Create network endpoint group**
- Name: `dooers-agents-default-neg`
- Region: **us-central1**
- Type: **Serverless network endpoint group**
- Serverless service: Cloud Run, **dooers-agents-default-404**
- Click **Create**.

**4c. Create the default Backend Service**
- Network services → Load balancing → **Backend services** → **Create**
- Name: `dooers-agents-default-404-bs`
- Backend type: **Serverless network endpoint group**
- Protocol: **HTTPS**
- Backend: add the NEG `dooers-agents-default-neg` from 4b.
- Click **Create**.

**gcloud:**
```bash
# 4a
gcloud run deploy dooers-agents-default-404 \
  --image=gcr.io/cloudrun/hello \
  --region=us-central1 \
  --allow-unauthenticated \
  --cpu-throttling \
  --project=<PROJECT_ID>

# 4b
gcloud compute network-endpoint-groups create dooers-agents-default-neg \
  --region=us-central1 \
  --network-endpoint-type=serverless \
  --cloud-run-service=dooers-agents-default-404 \
  --project=<PROJECT_ID>

# 4c
gcloud compute backend-services create dooers-agents-default-404-bs \
  --global --protocol=HTTPS --project=<PROJECT_ID>

gcloud compute backend-services add-backend dooers-agents-default-404-bs \
  --global \
  --network-endpoint-group=dooers-agents-default-neg \
  --network-endpoint-group-region=us-central1 \
  --project=<PROJECT_ID>
```

---

## Step 5 — Create the URL Map with the shared `agents-pm` path matcher

This step differs from the old subdomain setup. Instead of leaving the URL Map empty and letting `dooers-push` add a host rule per agent, we create the URL Map **and** immediately add the single shared path matcher `agents-pm` that `dooers-push` will append per-agent path rules to.

**Console:**
- Network services → Load balancing → **URL maps** → **Create URL map**
- Name: `dooers-agents-url-map`
- Default backend service: `dooers-agents-default-404-bs` (from Step 4c)
- Add a host rule: host `agents.dooers.ai` → path matcher name `agents-pm`, default service `dooers-agents-default-404-bs`
- Click **Create**.

**gcloud:**
```bash
# Create the URL Map with the 404 default
gcloud compute url-maps create dooers-agents-url-map \
  --default-service=dooers-agents-default-404-bs \
  --project=<PROJECT_ID>

# Add the host rule for agents.dooers.ai pointing at the shared path matcher "agents-pm".
# dooers-push appends per-agent path rules to this matcher on every push.
gcloud compute url-maps add-path-matcher dooers-agents-url-map \
  --path-matcher-name=agents-pm \
  --default-service=dooers-agents-default-404-bs \
  --new-hosts=agents.dooers.ai \
  --project=<PROJECT_ID>
```

After this, the URL Map has: host `agents.dooers.ai` → path matcher `agents-pm` (empty path rules, 404 default). `dooers-push` fills in path rules on each push. It does **not** add a new host rule per agent — that was the old subdomain approach.

---

## Step 6 — Create the Target HTTPS Proxy + Forwarding Rule

These two bind the URL Map to the SSL cert and the static IP.

**Console (Target HTTPS Proxy):**
- Network services → Load balancing → **Target proxies** → **Create target proxy**
- Name: `dooers-agents-https-proxy`
- Type: **Target HTTPS Proxy**
- URL map: `dooers-agents-url-map`
- SSL certificate: `dooers-agents-cert`
- Click **Create**.

**Console (Forwarding Rule):**
- Network services → Load balancing → **Forwarding rules** → **Create forwarding rule**
- Name: `dooers-agents-https-rule`
- Scope: **Global**
- IP address: `dooers-agents-lb-ip` (reserved in Step 2)
- IP protocol: **TCP**
- Port: **443**
- Target: `dooers-agents-https-proxy`
- Click **Create**.

**gcloud:**
```bash
gcloud compute target-https-proxies create dooers-agents-https-proxy \
  --url-map=dooers-agents-url-map \
  --ssl-certificates=dooers-agents-cert \
  --project=<PROJECT_ID>

gcloud compute forwarding-rules create dooers-agents-https-rule \
  --address=dooers-agents-lb-ip \
  --target-https-proxy=dooers-agents-https-proxy \
  --global \
  --ports=443 \
  --project=<PROJECT_ID>
```

---

## Step 7 — DNS: single A record

Create one A record for `agents.dooers.ai` pointing at the static IP reserved in Step 2. No wildcard is needed.

**Console (Cloud DNS):**
- Network services → Cloud DNS → click your `dooers.ai` zone → **Add Record Set**
- DNS name: `agents` (Cloud DNS will append `.dooers.ai.`)
- Resource record type: **A**
- TTL: **300** seconds
- IPv4 address: paste the IP reserved in Step 2 (e.g. `34.120.55.88`)
- Click **Create**.

**gcloud (Cloud DNS):**
```bash
# Replace 34.120.x.x with your actual reserved IP
gcloud dns record-sets create agents.dooers.ai. \
  --zone=dooers-ai \
  --type=A \
  --ttl=300 \
  --rrdatas=34.120.x.x \
  --project=<PROJECT_ID>
```

**If DNS is elsewhere (Cloudflare, Route53, etc.):** create an `A` record for `agents.dooers.ai` pointing to the static IP. TTL ~300s.

Verify within ~5 min:
```bash
dig +short agents.dooers.ai
# Should output your static IP
```

---

## Step 8 — Grant LB permissions to `dooers-push`'s service account

`dooers-push` runs on Cloud Run under `agent-deploy-service@<PROJECT_ID>.iam.gserviceaccount.com`. It needs to create NEGs, Backend Services, and update path rules in the URL Map.

**Console:**
- IAM & Admin → IAM → find `agent-deploy-service@<PROJECT_ID>.iam.gserviceaccount.com` → **Edit**
- Add role: **Compute Load Balancer Admin** (`roles/compute.loadBalancerAdmin`)
- Save.

**gcloud:**
```bash
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member=serviceAccount:agent-deploy-service@<PROJECT_ID>.iam.gserviceaccount.com \
  --role=roles/compute.loadBalancerAdmin
```

*(Tighter alternative for post-POC: replace with `roles/compute.urlMapAdmin` + `roles/compute.backendServiceAdmin` + `roles/compute.networkEndpointGroupAdmin`. The broader role is acceptable for the POC.)*

---

## Step 9 — Wait for the SSL cert to become ACTIVE

After DNS (Step 7) resolves to the LB IP and the LB is serving on port 443, Google provisions the single-domain cert automatically via the load balancer. This typically takes **15–60 min**. (Single-domain certs are faster to provision than wildcard certs — no DNS-01 challenge step.)

Check status:
```bash
gcloud compute ssl-certificates describe dooers-agents-cert \
  --global --project=<PROJECT_ID> --format='value(managed.status)'
```

Status progression: `PROVISIONING` → `ACTIVE`. Do not declare the setup complete until you see `ACTIVE`.

---

## Step 10 — Verification

Once the cert is `ACTIVE`:

```bash
# 1. Verify DNS
dig +short agents.dooers.ai
# → your static IP

# 2. Verify HTTPS handshake (before any agent push — hits the 404 default)
curl -sI https://agents.dooers.ai/anything
# → HTTP/2 with a valid cert; 404-ish body from the placeholder service.

# 3. After a `dooers push`, inspect the URL Map to confirm the path rule was added:
gcloud compute url-maps describe dooers-agents-url-map \
  --format="yaml(pathMatchers)" --project=<PROJECT_ID>
# → you should see a path rule for /ag-xxx and /ag-xxx/* under agents-pm

# 4. Hit the agent through the LB (prefix is stripped before reaching Cloud Run):
curl https://agents.dooers.ai/ag-xxxx-dev/
# → the agent's "/" response
```

If step 2 returns a valid HTTPS handshake (no SSL warnings) and step 4 returns the agent's root response, the setup is complete.

---

## Troubleshooting

**Cert stuck in PROVISIONING for >60 min**

- Confirm DNS A record resolves to your static IP: `dig +short agents.dooers.ai`
- Confirm the Forwarding Rule is using the static IP: `gcloud compute forwarding-rules describe dooers-agents-https-rule --global`
- Cert provisioning requires the LB to be reachable on port 443. Verify with: `curl -k https://<static-ip>` (expect a connect, even if the cert doesn't match yet).

**`curl` returns SSL error before cert is ACTIVE**

- Expected. The default GCP cert (auto-attached temporarily) doesn't match `agents.dooers.ai`. Wait for the managed cert to reach `ACTIVE`.

**`dooers-push` fails with "path matcher agents-pm missing"**

- Step 5's `add-path-matcher` command was not run, or the URL Map was recreated without it. Re-run the `gcloud compute url-maps add-path-matcher` command from Step 5.

**`dooers-push` fails to create a NEG with permission error**

- Confirm Step 8 (IAM role binding) succeeded. Run:
  ```bash
  gcloud projects get-iam-policy <PROJECT_ID> \
    --flatten='bindings[].members' \
    --filter='bindings.members:agent-deploy-service@<PROJECT_ID>.iam.gserviceaccount.com' \
    --format='value(bindings.role)'
  ```
  Should include `roles/compute.loadBalancerAdmin`.

**Want to delete everything and start over**

```bash
gcloud compute forwarding-rules delete dooers-agents-https-rule --global --project=<PROJECT_ID> --quiet
gcloud compute target-https-proxies delete dooers-agents-https-proxy --project=<PROJECT_ID> --quiet
gcloud compute url-maps delete dooers-agents-url-map --project=<PROJECT_ID> --quiet
gcloud compute backend-services delete dooers-agents-default-404-bs --global --project=<PROJECT_ID> --quiet
gcloud compute network-endpoint-groups delete dooers-agents-default-neg --region=us-central1 --project=<PROJECT_ID> --quiet
gcloud run services delete dooers-agents-default-404 --region=us-central1 --project=<PROJECT_ID> --quiet
gcloud compute ssl-certificates delete dooers-agents-cert --global --project=<PROJECT_ID> --quiet
gcloud compute addresses delete dooers-agents-lb-ip --global --project=<PROJECT_ID> --quiet
# DNS record left as exercise — clean up manually in Cloud DNS console.
```

---

## What `dooers-push` will do on every push (after this setup)

For reference only — no manual work needed once the platform resources above exist:

1. Create a Serverless NEG `agent-{safe}-{env}-neg` pointing to the new Cloud Run service `{agent_id}-{env}`.
2. Create a Backend Service `agent-{safe}-{env}-bs` wrapping that NEG.
3. `PATCH` the URL Map `dooers-agents-url-map` to append (or replace) a **path rule** `/{safe}` + `/{safe}/*` in the shared `agents-pm` path matcher, with a `pathPrefixRewrite="/"` so the backend (Cloud Run) receives the path with the agent prefix stripped.
4. Poll the new URL until it returns a non-default response (30–60s typical).
5. Return the URL `https://agents.dooers.ai/{safe}` to the CLI.

**Path-prefix rewrite detail:** a request for `agents.dooers.ai/ag-7q4r-dev/health` matches the path rule `/ag-7q4r-dev/*`; the LB rewrites the path to `/health` before forwarding to Cloud Run. The agent always serves from `/` and never sees the `/{safe}` prefix.

Idempotency: re-pushing the same agent replaces the existing path rule in place; it does NOT create duplicates.

Cleanup: when an agent is deleted (out of POC scope), `dooers-push` removes the path rule from `agents-pm`, then the Backend Service, then the NEG — in that order.

---

## Naming convention summary

| Resource | Name pattern | Created by |
|---|---|---|
| Static IP | `dooers-agents-lb-ip` | DevOps, once (this doc) |
| SSL certificate (single-domain) | `dooers-agents-cert` | DevOps, once |
| URL Map | `dooers-agents-url-map` | DevOps, once |
| Shared path matcher | `agents-pm` (inside URL Map) | DevOps, once (Step 5) |
| Target HTTPS Proxy | `dooers-agents-https-proxy` | DevOps, once |
| Forwarding Rule | `dooers-agents-https-rule` | DevOps, once |
| Default 404 Cloud Run | `dooers-agents-default-404` | DevOps, once |
| Default 404 NEG | `dooers-agents-default-neg` | DevOps, once |
| Default 404 Backend Service | `dooers-agents-default-404-bs` | DevOps, once |
| Per-agent NEG | `agent-{safe}-{env}-neg` | `dooers-push`, per push |
| Per-agent Backend Service | `agent-{safe}-{env}-bs` | `dooers-push`, per push |
| Per-agent path rule | `/{safe}` + `/{safe}/*` in `agents-pm` | `dooers-push`, per push |

Where `{safe}` = `agent_id.lower().replace('_', '-')`. Underscores are invalid in URL path segments used as identifiers here. For non-prod environments the env suffix is appended: `{safe}-{env}` (e.g. `ag-7q4r-dev`).

The code reads `DOOERS_LB_URL_MAP=dooers-agents-url-map` and expects the `agents-pm` path matcher to exist. If either name differs from the defaults, set the corresponding env var on `dooers-push` or rename the resource.

---

## Appendix: deprecated subdomain approach (v1)

The original v1 setup used per-agent subdomains (`ag-7q4r-dev.agents.dooers.ai`) with:

- A wildcard SSL certificate (`*.agents.dooers.ai`) — Google-managed wildcard certs require DNS-01 authorization, which adds extra setup steps and slower provisioning.
- A wildcard DNS A record (`*.agents.dooers.ai`).
- One URL Map **host rule** per agent (added by `dooers-push` on each push), each pointing at a dedicated per-agent path matcher.

This approach is superseded by the path-based setup documented above. The single-domain cert, single A record, and shared `agents-pm` path matcher are strictly simpler. Do not follow this appendix for new deployments.

---

## Contact / questions

Open an issue at the [`Dooers-ai/dooers-cli` repo](https://github.com/Dooers-ai/dooers-cli) or ping the platform team.
