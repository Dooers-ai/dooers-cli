# dooers/dooers-cli

The Dooers creator-facing CLI and the push service it talks to. Monorepo with three sibling packages under `packages/`:

- **`dooers-cli/`** — `dooers` on PyPI. The Typer-based CLI that creators install (`pip install dooers`).
- **`dooers-push/`** — the Cloud Run service that owns the push pipeline (auditor → provisioner → deployer).
- **`dooers-protocol/`** — `dooers-protocol` on PyPI. Shared Pydantic models defining the wire contract between any client and `dooers-push`.

## Design docs

- `docs/stakeholders/` — visual overview for stakeholders / product / leadership.
- `docs/superpowers/specs/` — full implementation spec.

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
