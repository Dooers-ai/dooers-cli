"""Pre-push validation for agent projects (`dooers validate`)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import typer
from pydantic import ValidationError

from dooers.cli.config import MANIFEST_FILENAME, read_manifest

_LEGACY_SDK_DEP = re.compile(r"dooers-agents(?!-server|-client)")
_LEGACY_IMPORT = re.compile(
    r"^\s*(?:"
    r"from dooers_agents(?:\.|\s+import)|"
    r"from dooers import|"
    r"import dooers_agents(?:\s|$)|"
    r"import dooers(?:\s|$)"
    r")",
    re.MULTILINE,
)


@dataclass(frozen=True)
class ValidationIssue:
    level: str  # "error" | "warning"
    message: str


def collect_issues(root: Path | None = None) -> list[ValidationIssue]:
    """Return validation findings for an agent repo rooted at `root` (default: cwd)."""
    base = root or Path.cwd()
    issues: list[ValidationIssue] = []

    manifest_path = base / MANIFEST_FILENAME
    if not manifest_path.exists():
        issues.append(
            ValidationIssue(
                "error",
                f"missing {MANIFEST_FILENAME} — run `dooers agents create`",
            )
        )
    else:
        try:
            read_manifest(directory=base)
        except ValidationError as e:
            issues.append(ValidationIssue("error", f"{MANIFEST_FILENAME} is invalid: {e}"))

    dockerfile = base / "Dockerfile"
    if not dockerfile.exists():
        issues.append(ValidationIssue("error", "missing Dockerfile"))

    pyproject = base / "pyproject.toml"
    if not pyproject.exists():
        issues.append(ValidationIssue("warning", "missing pyproject.toml"))
    else:
        text = pyproject.read_text()
        if _LEGACY_SDK_DEP.search(text):
            issues.append(
                ValidationIssue(
                    "error",
                    "pyproject.toml still depends on legacy `dooers-agents` — "
                    "use `dooers-agents-server` with `from dooers.agents.server import ...`",
                )
            )
        elif "dooers-agents-server" not in text:
            issues.append(
                ValidationIssue(
                    "warning",
                    "pyproject.toml has no `dooers-agents-server` dependency — "
                    "agent projects usually depend on the SDK",
                )
            )

    scan_roots = [p for p in (base / "src", base) if p.is_dir()]
    py_files: list[Path] = []
    for scan_root in scan_roots:
        py_files.extend(scan_root.rglob("*.py"))
    seen: set[Path] = set()
    for path in sorted(py_files):
        if path in seen or ".venv" in path.parts:
            continue
        seen.add(path)
        if _LEGACY_IMPORT.search(path.read_text()):
            rel = path.relative_to(base)
            issues.append(
                ValidationIssue(
                    "error",
                    f"{rel} uses legacy SDK imports — "
                    "update to `from dooers.agents.server import ...`",
                )
            )

    manifest = read_manifest(directory=base) if manifest_path.exists() else None

    # A `dooers`-managed DB is provisioned by the platform and connected via IAM,
    # so a local localhost AGENT_DATABASE_HOST (for `postgres` local dev) is expected.
    managed_db = manifest is not None and manifest.database.type == "dooers"
    if not managed_db:
        for env_path in [base / ".env", *base.glob("env.*")]:
            if not env_path.is_file():
                continue
            for raw in env_path.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip().upper() in {
                    "AGENT_DATABASE_HOST",
                    "DATABASE_HOST",
                    "POSTGRES_HOST",
                } and value.strip().lower() in {"127.0.0.1", "localhost", "0.0.0.0"}:
                    issues.append(
                        ValidationIssue(
                            "warning",
                            f"{env_path.name} sets {key.strip()} to localhost — "
                            "hosted deploys need a cloud database configured in Dooers Studio",
                        )
                    )

    if manifest is not None and manifest.hosting and not (base / "env.prod").is_file():
        issues.append(
            ValidationIssue(
                "warning",
                "hosting is enabled but env.prod is missing — "
                "create env.prod with production runtime variables (OPENAI_API_KEY, "
                "AGENT_DATABASE_*, etc.) for Cloud Run deploys",
            )
        )

    return issues


def validate(ctx: typer.Context) -> None:
    """Validate dooers.yaml, Dockerfile, and SDK dependency/import conventions."""
    _ = ctx
    issues = collect_issues()
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    for issue in issues:
        prefix = "Error" if issue.level == "error" else "Warning"
        typer.echo(f"{prefix}: {issue.message}", err=issue.level == "error")

    if errors:
        raise typer.Exit(code=1)
    if warnings:
        typer.echo("Validation passed with warnings.")
    else:
        typer.echo("Validation passed.")
