"""Pre-push validation for agent projects (`dooers validate`)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import typer
from pydantic import ValidationError

from dooers.config import MANIFEST_FILENAME, read_manifest

_LEGACY_SDK_DEP = re.compile(r"dooers-agents-server")
_LEGACY_IMPORT = re.compile(
    r"^\s*(?:from dooers(?:\.|\s+import)|import dooers(?:\s|$))",
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
                    "pyproject.toml still depends on `dooers-agents-server` — "
                    "rename to `dooers-agents>=0.11.0` (SDK import is now `dooers_agents`)",
                )
            )
        elif "dooers-agents" not in text:
            issues.append(
                ValidationIssue(
                    "warning",
                    "pyproject.toml has no `dooers-agents` dependency — "
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
                    f"{rel} uses legacy `dooers` imports — "
                    "update to `from dooers_agents import ...`",
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
