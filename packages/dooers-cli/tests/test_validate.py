"""Tests for `dooers validate`."""

from pathlib import Path

from dooers.cli.validate import collect_issues


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_validate_passes_minimal_agent_project(tmp_path: Path) -> None:
    _write(
        tmp_path / "dooers.yaml",
        """
protocol_version: "2"
agent_id: "550e8400-e29b-41d4-a716-446655440000"
name: test
organization_id: "org_1"
hosting: true
""".strip()
        + "\n",
    )
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    _write(
        tmp_path / "pyproject.toml",
        '[project]\ndependencies = ["dooers-agents-server>=0.12.0"]\n',
    )
    _write(
        tmp_path / "src" / "main.py",
        "from dooers.agents.server import AgentServer\n",
    )

    issues = collect_issues(tmp_path)
    assert not any(i.level == "error" for i in issues)


def test_validate_flags_legacy_sdk_dependency(tmp_path: Path) -> None:
    _write(
        tmp_path / "dooers.yaml",
        """
protocol_version: "2"
agent_id: "550e8400-e29b-41d4-a716-446655440000"
name: test
organization_id: "org_1"
""".strip()
        + "\n",
    )
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    _write(
        tmp_path / "pyproject.toml",
        'dependencies = ["dooers-agents>=0.11.0"]\n',
    )

    issues = collect_issues(tmp_path)
    assert any("legacy `dooers-agents`" in i.message for i in issues)


def test_validate_flags_legacy_imports(tmp_path: Path) -> None:
    _write(
        tmp_path / "dooers.yaml",
        """
protocol_version: "2"
agent_id: "550e8400-e29b-41d4-a716-446655440000"
name: test
organization_id: "org_1"
""".strip()
        + "\n",
    )
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    _write(
        tmp_path / "pyproject.toml",
        'dependencies = ["dooers-agents-server>=0.12.0"]\n',
    )
    _write(
        tmp_path / "src" / "agent.py",
        "from dooers import AgentServer\n",
    )

    issues = collect_issues(tmp_path)
    assert any("legacy SDK imports" in i.message for i in issues)


def test_validate_warns_on_localhost_database_host(tmp_path: Path) -> None:
    _write(
        tmp_path / "dooers.yaml",
        """
protocol_version: "2"
agent_id: "550e8400-e29b-41d4-a716-446655440000"
name: test
organization_id: "org_1"
hosting: true
""".strip()
        + "\n",
    )
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    _write(
        tmp_path / "pyproject.toml",
        '[project]\ndependencies = ["dooers-agents-server>=0.12.0"]\n',
    )
    _write(tmp_path / ".env", "AGENT_DATABASE_HOST=127.0.0.1\n")

    issues = collect_issues(tmp_path)
    assert any("localhost" in i.message.lower() for i in issues)


def test_validate_skips_localhost_db_warning_when_managed(tmp_path: Path) -> None:
    """With `database.type: dooers` the platform provisions the DB, so a local
    localhost AGENT_DATABASE_HOST (used for `postgres` local dev) must not warn."""
    _write(
        tmp_path / "dooers.yaml",
        """
protocol_version: "2"
agent_id: "550e8400-e29b-41d4-a716-446655440000"
name: test
organization_id: "org_1"
hosting: true
database:
  type: dooers
""".strip()
        + "\n",
    )
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    _write(
        tmp_path / "pyproject.toml",
        '[project]\ndependencies = ["dooers-agents-server>=0.12.0"]\n',
    )
    _write(tmp_path / ".env", "AGENT_DATABASE_HOST=127.0.0.1\n")

    issues = collect_issues(tmp_path)
    assert not any("localhost" in i.message.lower() for i in issues)


def test_validate_warns_when_hosting_without_env_prod(tmp_path: Path) -> None:
    _write(
        tmp_path / "dooers.yaml",
        """
protocol_version: "2"
agent_id: "550e8400-e29b-41d4-a716-446655440000"
name: test
organization_id: "org_1"
hosting: true
""".strip()
        + "\n",
    )
    _write(tmp_path / "Dockerfile", "FROM python:3.12-slim\n")
    _write(
        tmp_path / "pyproject.toml",
        '[project]\ndependencies = ["dooers-agents-server>=0.12.0"]\n',
    )

    issues = collect_issues(tmp_path)
    assert any("env.prod is missing" in i.message for i in issues)
