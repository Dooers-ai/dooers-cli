# dooers-push

The Cloud Run service backing `dooers push`. Owns the push pipeline:

```
auditor (stub in POC)  →  provisioner (stub in POC)  →  deployer (Cloud Build + Cloud Run)
```

## Endpoints

- `POST /v1/push/{agent_id}` — accepts the multipart archive, runs the pipeline, blocks until Cloud Build completes, returns `PushResponse` with the live Cloud Run URL.
- `GET /health` — liveness.

## Development

```bash
uv sync --extra dev
uv run poe dev       # check + typecheck + test
uv run poe serve     # uvicorn locally on :8080
```

## Deploy

Built and deployed via Cloud Build. See `Dockerfile` and the `cloudbuild/` directory at repo root (TBD).

## Boundary

This service does NOT host `/agents` CRUD. It reads agent records from core (`api.dooers.ai`) to verify ownership and PATCHes back only the deployed URL after a successful push. Source of truth for agents is core.
