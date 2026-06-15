# dooers/dooers-cli

The Dooers creator-facing CLI and the shared wire protocol it speaks. Monorepo with two sibling packages under `packages/`:

- **`dooers-cli/`** — `dooers` on PyPI. The Typer-based CLI that creators install (`pip install dooers`).
- **`dooers-protocol/`** — `dooers-protocol` on PyPI. Shared Pydantic models defining the wire contract between any client and `dooers-push`.

The push service (`dooers-push`) — the Cloud Run service that owns the push pipeline (auditor → provisioner → deployer) — lives in its own private repo (`Dooers-ai/dooers-push`) and consumes `dooers-protocol` from PyPI.

## Design docs

- `docs/dooers-cli.md` — stakeholder-facing overview / status.
- `docs/superpowers/specs/` — full implementation spec.
- `docs/superpowers/plans/` — implementation plan(s) derived from the spec.

## Quickstart (per-package)

Each package is independent. There is no top-level orchestrator.

```bash
cd packages/<pkg>
uv sync --extra dev
uv run poe dev        # check + typecheck + test
```

See each package's `README.md` for details.

## License

MIT — see [`LICENSE`](./LICENSE).
