# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

Monorepo with two sibling packages under `packages/`:

- `dooers-cli/` — published as `dooers` on PyPI. Typer-based CLI installed by agent creators (`pip install dooers`). Commands: `dooers login`, `dooers logout`, `dooers whoami`, `dooers agents list|create|show`, `dooers push`.
- `dooers-protocol/` — published as `dooers-protocol` on PyPI. Shared Pydantic models defining the wire contract between any client and `dooers-push`. Both `dooers-cli` and `dooers-push` import from it.

The push service (`dooers-push`) lives in its own private repo (`Dooers-ai/dooers-push`), split out of this monorepo. It consumes `dooers-protocol` from PyPI rather than the editable sibling.

`dooers-cli` references `dooers-protocol` via `tool.uv.sources` with editable path (`path = "../dooers-protocol"`). If `uv sync` fails resolving `dooers-protocol`, run it from `packages/dooers-protocol/` first or check the sibling path.

## Common commands

Each package is independent — `cd packages/<pkg>` first. There is no top-level orchestrator.

### Python packages (both)

All packages use `uv` + `poethepoet`. Tasks are defined in each `pyproject.toml` under `[tool.poe.tasks]`.

```bash
uv sync --extra dev
uv run poe dev          # check + typecheck + test
uv run poe check        # ruff lint
uv run poe check:fix    # ruff lint --fix
uv run poe format       # ruff format
uv run poe typecheck    # mypy on src/
uv run poe test         # pytest
uv run poe test:cov     # pytest with coverage
uv run poe build        # python -m build
```

Run a single test: `uv run pytest tests/path/to/test_x.py::test_name -x`.

## Architecture notes

- **The CLI talks to exactly two services**: `core API` (auth + agent metadata) and `dooers-push` (the push pipeline). Anything else is a design violation.
- **`dooers-push` does NOT host `/agents` CRUD.** It reads agent records from core to verify ownership and PATCHes back only the deployed URL after a successful push. Source of truth for agents is core.
- **Every CLI ↔ `dooers-push` request/response is a Pydantic model in `dooers-protocol`.** New endpoints add new models there first.
- **The push pipeline in `dooers-push` is three sequential steps** behind a common `PipelineStep.run(ctx)` interface. In the POC, `auditor` and `provisioner` are no-op stubs. They have *typed* interfaces (see `dooers-protocol/audit.py`) so future real implementations drop in without touching the rest of the pipeline.
- **Push is synchronous in the POC.** CLI shows a spinner for ~3–5 min while `dooers-push` polls Cloud Build, then prints the URL. Async push + status polling is deferred to v2.

## Design docs

Before changing anything non-trivial, read:
- `docs/dooers-cli.md` — stakeholder-facing overview / status.
- `docs/superpowers/specs/2026-05-26-dooers-cli-v2-design.md` — full implementation spec.
- `docs/superpowers/plans/2026-05-26-dooers-cli-v2-poc.md` — task-by-task implementation plan.

## Environment

- Python 3.10+ for `dooers-cli` and `dooers-protocol` (broader compat for end users).
- `uv` for dependency management.
- `ruff` for lint/format.
- `mypy` for typechecking.
- `pytest` for tests.
- `hatchling` build backend (`python -m build`).
