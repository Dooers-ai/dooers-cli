# GCP Load Balancer — one-time setup for `dooers-push`

**Audience:** Dooers DevOps / platform team
**Status:** Ready to execute
**Estimated time:** ~45 min in the console + 30–60 min waiting for SSL cert to provision

This document describes the **one-time** GCP setup required before the `dooers-push` Cloud Run service can wire newly deployed agents into the global Dooers load balancer. After this setup completes, every `dooers push` will result in the deployed agent being reachable at a stable URL like `https://ag-7q4r-dev.agents.dooers.ai`.

The application code (`dooers-push`) handles the **per-push** LB updates (Serverless NEG creation, Backend Service, host rules). This document covers only the durable platform resources that exist once.

---

## Architecture summary

```
Internet
   │  HTTPS → ag-7q4r-dev.agents.dooers.ai
   ▼
[ Global Anycast IPv4 (static) ]
   ▼
[ Forwarding Rule :443 ]
   ▼
[ Target HTTPS Proxy ]      ──── SSL cert: *.agents.dooers.ai (Google-managed)
   ▼
[ URL Map: dooers-agents-url-map ]
   │   host rules added per push by dooers-push
   │   default → 404 placeholder
   ▼
[ Backend Service per agent ] ── created per push
   ▼
[ Serverless NEG per agent ] ── created per push
   ▼
[ Cloud Run service: {agent_id}-{env} ]
```

Five resources are created in this one-time setup (steps below):

1. Global static IPv4
2. Google-managed wildcard SSL certificate (`*.agents.dooers.ai`)
3. URL Map with default-404 backend
4. Target HTTPS Proxy
5. Global Forwarding Rule

Plus:
- One IAM grant on the `dooers-push` service account
- One DNS wildcard A record

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

## Step 3 — Create the wildcard SSL certificate

We use a classic compute SSL certificate (matches the classic Global External HTTPS LB used below).

**Console:**
- Network security → Certificate Manager → **Classic certificates** tab → **Add certificate**
- Name: `dooers-agents-wildcard-cert`
- Creation mode: **Create Google-managed certificate**
- Domains: `*.agents.dooers.ai`
- Click **Create**.

The certificate's status will start at **PROVISIONING**. It will not become **ACTIVE** until Step 7 (DNS) is complete. That's normal. After DNS lands, provisioning takes 30–60 min to several hours.

**gcloud:**
```bash
gcloud compute ssl-certificates create dooers-agents-wildcard-cert \
  --domains="*.agents.dooers.ai" \
  --global \
  --project=<PROJECT_ID>
```

---

## Step 4 — Create the placeholder default-404 backend

The URL Map requires a default backend service for unmatched hosts. We deploy a tiny placeholder Cloud Run service (which will respond to anyone hitting an unknown agent URL), then wrap it in a NEG and Backend Service.

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

## Step 5 — Create the URL Map

**Console:**
- Network services → Load balancing → **URL maps** → **Create URL map**
- Name: `dooers-agents-url-map`
- Default backend service: `dooers-agents-default-404-bs` (from Step 4c)
- Leave host rules empty for now — `dooers-push` populates them per push.
- Click **Create**.

**gcloud:**
```bash
gcloud compute url-maps create dooers-agents-url-map \
  --default-service=dooers-agents-default-404-bs \
  --project=<PROJECT_ID>
```

---

## Step 6 — Create the Target HTTPS Proxy + Forwarding Rule

These two bind the URL Map to the SSL cert and the static IP.

**Console (Target HTTPS Proxy):**
- Network services → Load balancing → **Target proxies** → **Create target proxy**
- Name: `dooers-agents-https-proxy`
- Type: **Target HTTPS Proxy**
- URL map: `dooers-agents-url-map`
- SSL certificate: `dooers-agents-wildcard-cert`
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
  --ssl-certificates=dooers-agents-wildcard-cert \
  --project=<PROJECT_ID>

gcloud compute forwarding-rules create dooers-agents-https-rule \
  --address=dooers-agents-lb-ip \
  --target-https-proxy=dooers-agents-https-proxy \
  --global \
  --ports=443 \
  --project=<PROJECT_ID>
```

---

## Step 7 — DNS wildcard A record

**Console (Cloud DNS):**
- Network services → Cloud DNS → click your `dooers.ai` zone → **Add Record Set**
- DNS name: `*.agents` (Cloud DNS will append `.dooers.ai.`)
- Resource record type: **A**
- TTL: **300** seconds
- IPv4 address: paste the IP reserved in Step 2 (e.g. `34.120.55.88`)
- Click **Create**.

**gcloud (Cloud DNS):**
```bash
# Replace 34.120.55.88 with your actual reserved IP
gcloud dns record-sets create '*.agents.dooers.ai.' \
  --zone=dooers-ai \
  --type=A \
  --ttl=300 \
  --rrdatas=34.120.55.88 \
  --project=<PROJECT_ID>
```

**If DNS is elsewhere (Cloudflare, Route53, etc.):** create an `A` record for `*.agents.dooers.ai` pointing to the static IP. TTL ~300s.

Verify within ~5 min:
```bash
dig +short ag-any.agents.dooers.ai
# Should output your static IP
```

---

## Step 8 — Grant LB permissions to `dooers-push`'s service account

`dooers-push` runs on Cloud Run under `agent-deploy-service@<PROJECT_ID>.iam.gserviceaccount.com`. It needs to create NEGs, Backend Services, and update the URL Map.

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

This is the longest single step. After DNS (Step 7) is complete, Google's cert provisioning checks the domain ownership by HTTP-01 challenge through the LB. It typically takes **30–60 min**, sometimes a few hours, occasionally up to 24 hours.

Check status:
```bash
gcloud compute ssl-certificates describe dooers-agents-wildcard-cert \
  --global --project=<PROJECT_ID> --format='value(managed.status)'
```

Status progression: `PROVISIONING` → `ACTIVE`. Do not declare the setup complete until you see `ACTIVE`.

---

## Step 10 — Verification

Once the cert is `ACTIVE`:

```bash
# 1. Verify wildcard DNS
dig +short ag-test.agents.dooers.ai
# → your static IP

# 2. Verify HTTPS handshake
curl -sI https://ag-test.agents.dooers.ai
# → HTTP/2 404 (or 200 from the placeholder; depends on Step 4a service)
# The critical part is the HTTPS handshake succeeding (no cert errors).

# 3. Verify LB resources exist
gcloud compute url-maps describe dooers-agents-url-map --project=<PROJECT_ID>
gcloud compute backend-services list --global --project=<PROJECT_ID>
gcloud compute network-endpoint-groups list --project=<PROJECT_ID>
```

If all three return without errors and `curl` shows a valid HTTPS handshake (no SSL warnings), the setup is complete and `dooers-push` can start adding per-agent backends.

---

## Troubleshooting

**Cert stuck in PROVISIONING for >24 hours**

- Confirm DNS A record actually resolves to your static IP: `dig +short ag-test.agents.dooers.ai`
- Confirm the Forwarding Rule is using the static IP: `gcloud compute forwarding-rules describe dooers-agents-https-rule --global`
- Cert provisioning fails if Google can't reach the LB IP for the HTTP-01 challenge. Make sure the LB is fully created and serving traffic on port 443 (`curl -k https://<static-ip>` should at least connect, even if it returns a cert mismatch).

**`curl` returns SSL error before cert is ACTIVE**

- Expected. The default GCP cert (auto-attached temporarily) doesn't match `*.agents.dooers.ai`. Wait for the managed cert.

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
gcloud compute ssl-certificates delete dooers-agents-wildcard-cert --global --project=<PROJECT_ID> --quiet
gcloud compute addresses delete dooers-agents-lb-ip --global --project=<PROJECT_ID> --quiet
# DNS record left as exercise — clean up manually in Cloud DNS console.
```

---

## What `dooers-push` will do on every push (after this setup)

For reference only — no manual work needed once the platform resources above exist:

1. Create a Serverless NEG `agent-{agent_id}-{env}-neg` pointing to the new Cloud Run service `{agent_id}-{env}`.
2. Create a Backend Service `agent-{agent_id}-{env}-bs` wrapping that NEG.
3. `PATCH` the URL Map `dooers-agents-url-map` to add a host rule `{agent_id_safe}-{env}.agents.dooers.ai` → that Backend Service.
4. Poll the new URL until it returns a non-default response (30-60s typical).
5. Return the URL to the CLI.

Idempotency: re-pushing the same agent updates the existing NEG to point at the latest Cloud Run revision; it does NOT create duplicates.

Cleanup: when an agent is deleted (out of POC scope), `dooers-push` will remove the host rule, Backend Service, and NEG — in that order.

---

## Naming convention summary

| Resource | Name pattern | Created |
|---|---|---|
| Static IP | `dooers-agents-lb-ip` | Once (this doc) |
| SSL certificate | `dooers-agents-wildcard-cert` | Once |
| URL Map | `dooers-agents-url-map` | Once |
| Target HTTPS Proxy | `dooers-agents-https-proxy` | Once |
| Forwarding Rule | `dooers-agents-https-rule` | Once |
| Default 404 Cloud Run | `dooers-agents-default-404` | Once |
| Default 404 NEG | `dooers-agents-default-neg` | Once |
| Default 404 Backend Service | `dooers-agents-default-404-bs` | Once |
| Per-agent NEG | `agent-{agent_id_safe}-{env}-neg` | Per push (by `dooers-push`) |
| Per-agent Backend Service | `agent-{agent_id_safe}-{env}-bs` | Per push (by `dooers-push`) |
| Per-agent host rule | `{agent_id_safe}-{env}.agents.dooers.ai` | Per push (by `dooers-push`) |

Where `agent_id_safe` = `agent_id.lower().replace('_', '-')`. Underscores are invalid in DNS hostnames.

---

## Contact / questions

Open an issue at the [`Dooers-ai/dooers-cli` repo](https://github.com/Dooers-ai/dooers-cli) or ping the platform team.
