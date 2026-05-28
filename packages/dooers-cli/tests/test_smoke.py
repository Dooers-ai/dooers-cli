"""Smoke tests — every command wires through Typer correctly."""

from typer.testing import CliRunner

from dooers.cli import app

runner = CliRunner()


def test_root_help_lists_top_level_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = result.stdout
    for cmd in ("login", "whoami", "logout", "agents", "push"):
        assert cmd in out


def test_login_help_has_positional_email() -> None:
    result = runner.invoke(app, ["login", "--help"])
    assert result.exit_code == 0
    assert "email" in result.stdout.lower()


def test_agents_help() -> None:
    result = runner.invoke(app, ["agents", "--help"])
    assert result.exit_code == 0
    for cmd in ("list", "create", "show"):
        assert cmd in result.stdout


def test_push_help() -> None:
    result = runner.invoke(app, ["push", "--help"])
    assert result.exit_code == 0
    assert "agent" in result.stdout.lower()
