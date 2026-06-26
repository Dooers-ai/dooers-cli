"""Teardown wire models + display helper shared with the CLI."""

from dooers.protocol.teardown import (
    TeardownRequest,
    TeardownResponse,
    format_teardown_result,
)


def test_teardown_request_env_defaults_to_prod() -> None:
    assert TeardownRequest(agent_id="a1").env == "prod"


def test_teardown_response_roundtrip() -> None:
    resp = TeardownResponse.model_validate(
        {"agent_id": "a1", "service_deleted": True, "lb_rule_removed": True, "service_name": "svc"}
    )
    assert resp.service_deleted is True
    assert resp.lb_rule_removed is True
    assert resp.service_name == "svc"
    assert resp.error is None


def test_format_both_removed() -> None:
    resp = TeardownResponse(agent_id="a1", service_deleted=True, lb_rule_removed=True)
    assert format_teardown_result(resp) == "Cloud Run service deleted; load-balancer rule removed."


def test_format_service_only() -> None:
    resp = TeardownResponse(agent_id="a1", service_deleted=True, lb_rule_removed=False)
    assert format_teardown_result(resp) == "Cloud Run service deleted."


def test_format_nothing_removed() -> None:
    resp = TeardownResponse(agent_id="a1", service_deleted=False, lb_rule_removed=False)
    assert format_teardown_result(resp) == "No deployed service found — record only."
