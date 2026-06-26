"""`dooers agents delete` — orchestration over core + dooers-push."""

import httpx
import respx
from typer.testing import CliRunner

from dooers.cli.cli import app

runner = CliRunner()

CORE = "https://core.test"
PUSH = "https://push.test"
A = "550e8400-e29b-41d4-a716-446655440000"
ROOT = ["--core-url", CORE, "--push-url", PUSH, "--env", "dev"]


def _auth(monkeypatch):
    class _Tok:
        def load(self):
            return "tok"

    monkeypatch.setattr("dooers.cli.agents.TokenStore", _Tok)
    monkeypatch.setattr("dooers.cli.agents.is_token_expired", lambda token, store: False)


def _agent_get(status=None, name="my-agent"):
    data = {"agentId": A, "name": name, "organizationId": "o1"}
    if status is not None:
        data["status"] = status
    return httpx.Response(200, json={"success": True, "data": data})


def _teardown_ok():
    return httpx.Response(
        200,
        json={
            "agent_id": A,
            "service_deleted": True,
            "lb_rule_removed": True,
            "service_name": "svc",
        },
    )


def _core_delete_ok():
    return httpx.Response(200, json={"success": True, "message": "Agent deleted"})


def test_agents_help_lists_delete():
    result = runner.invoke(app, ["agents", "--help"])
    assert result.exit_code == 0
    assert "delete" in result.stdout


@respx.mock
def test_delete_inactive_with_yes(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(return_value=_agent_get(status="archived"))
    td = respx.delete(f"{PUSH}/v1/agents/{A}").mock(return_value=_teardown_ok())
    dele = respx.delete(f"{CORE}/api/v2/agents/{A}").mock(return_value=_core_delete_ok())

    result = runner.invoke(app, ROOT + ["agents", "delete", A, "--yes"])

    assert result.exit_code == 0, result.output
    assert td.called and dele.called
    assert "Deleted agent" in result.output
    assert "Cloud Run service deleted; load-balancer rule removed." in result.output


@respx.mock
def test_active_without_archive_fast_fails(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(return_value=_agent_get(status="active"))
    td = respx.delete(f"{PUSH}/v1/agents/{A}").mock(return_value=_teardown_ok())
    dele = respx.delete(f"{CORE}/api/v2/agents/{A}").mock(return_value=_core_delete_ok())

    result = runner.invoke(app, ROOT + ["agents", "delete", A, "--yes"])

    assert result.exit_code == 1
    assert "is active" in result.output
    assert not td.called and not dele.called


@respx.mock
def test_active_with_archive_then_delete(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(return_value=_agent_get(status="active"))
    arch = respx.post(f"{CORE}/api/v2/agents/{A}/archive").mock(
        return_value=httpx.Response(
            200, json={"success": True, "data": {"agentId": A, "name": "my-agent"}}
        )
    )
    td = respx.delete(f"{PUSH}/v1/agents/{A}").mock(return_value=_teardown_ok())
    dele = respx.delete(f"{CORE}/api/v2/agents/{A}").mock(return_value=_core_delete_ok())

    result = runner.invoke(app, ROOT + ["agents", "delete", A, "--archive", "--yes"])

    assert result.exit_code == 0, result.output
    assert arch.called and td.called and dele.called


@respx.mock
def test_teardown_failure_aborts_before_record_delete(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(return_value=_agent_get(status="archived"))
    respx.delete(f"{PUSH}/v1/agents/{A}").mock(
        return_value=httpx.Response(
            500, json={"error_code": "internal", "message": "teardown boom", "correlation_id": "c1"}
        )
    )
    dele = respx.delete(f"{CORE}/api/v2/agents/{A}").mock(return_value=_core_delete_ok())

    result = runner.invoke(app, ROOT + ["agents", "delete", A, "--yes"])

    assert result.exit_code == 1
    assert "Teardown failed" in result.output
    assert "teardown boom" in result.output
    assert not dele.called  # record must NOT be deleted when teardown failed


@respx.mock
def test_record_delete_422_after_teardown(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(return_value=_agent_get(status="archived"))
    respx.delete(f"{PUSH}/v1/agents/{A}").mock(return_value=_teardown_ok())
    respx.delete(f"{CORE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(
            422, json={"success": False, "error": {"message": "agent has products"}}
        )
    )

    result = runner.invoke(app, ROOT + ["agents", "delete", A, "--yes"])

    assert result.exit_code == 1
    assert "Service torn down, but the core record was not deleted" in result.output
    assert "agent has products" in result.output


@respx.mock
def test_confirm_decline_makes_no_mutating_calls(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(return_value=_agent_get(status="archived"))
    td = respx.delete(f"{PUSH}/v1/agents/{A}").mock(return_value=_teardown_ok())
    dele = respx.delete(f"{CORE}/api/v2/agents/{A}").mock(return_value=_core_delete_ok())

    result = runner.invoke(app, ROOT + ["agents", "delete", A], input="n\n")

    assert result.exit_code == 1  # typer.confirm(abort=True) → Abort → exit 1
    assert not td.called and not dele.called


@respx.mock
def test_delete_not_found(monkeypatch):
    _auth(monkeypatch)
    respx.get(f"{CORE}/api/v2/agents/{A}").mock(
        return_value=httpx.Response(404, json={"success": False, "error": {"message": "nope"}})
    )

    result = runner.invoke(app, ROOT + ["agents", "delete", A, "--yes"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()
