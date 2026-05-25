"""Smoke tests — every subcommand wires through Typer correctly."""

from typer.testing import CliRunner

from dooers.cli import app

runner = CliRunner()


def test_root_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "dooers" in result.stdout.lower()


def test_auth_help() -> None:
    result = runner.invoke(app, ["auth", "--help"])
    assert result.exit_code == 0
    assert "login" in result.stdout
    assert "logout" in result.stdout
    assert "whoami" in result.stdout


def test_agents_help() -> None:
    result = runner.invoke(app, ["agents", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout
    assert "create" in result.stdout
    assert "show" in result.stdout


def test_push_help() -> None:
    result = runner.invoke(app, ["push", "--help"])
    assert result.exit_code == 0
    assert "agent_id" in result.stdout.lower() or "agent-id" in result.stdout.lower()
