"""Smoke tests — verify each model round-trips JSON serialization."""

from datetime import datetime, timezone

from dooers_protocol import PROTOCOL_VERSION
from dooers_protocol.agents import AgentManifest, AgentRecord, CreateAgentRequest
from dooers_protocol.audit import AuditReport, InfraManifest
from dooers_protocol.errors import ErrorCode, ErrorEnvelope
from dooers_protocol.push import BuildStatus, PushResponse


def test_protocol_version_exported() -> None:
    assert PROTOCOL_VERSION == "1"


def test_agent_record_roundtrip() -> None:
    now = datetime.now(timezone.utc)
    rec = AgentRecord(
        agent_id="ag_8h2k",
        name="customer-support",
        owner_user_id="u_1",
        created_at=now,
        updated_at=now,
    )
    assert AgentRecord.model_validate_json(rec.model_dump_json()) == rec


def test_agent_manifest_rejects_unknown_fields() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AgentManifest(
            protocol_version="1",
            agent_id="ag_8h2k",
            name="x",
            unknown_field="oops",  # type: ignore[call-arg]
        )


def test_push_response_succeeded_has_url() -> None:
    resp = PushResponse(
        agent_id="ag_8h2k",
        build_id="op_1",
        image="us-central1-docker.pkg.dev/p/agents/ag_8h2k:latest",
        status=BuildStatus.succeeded,
        url="https://ag-8h2k-prod-xxx.run.app",
    )
    assert resp.url is not None


def test_audit_report_default_passes() -> None:
    rep = AuditReport(passed=True)
    assert rep.findings == []
    assert rep.required_infra == InfraManifest()


def test_error_envelope() -> None:
    env = ErrorEnvelope(
        error_code=ErrorCode.unauthenticated,
        message="bad token",
        correlation_id="abc",
    )
    assert env.error_code == ErrorCode.unauthenticated


def test_create_agent_request_defaults() -> None:
    req = CreateAgentRequest(name="my-agent")
    assert req.runtime == "docker"
    assert req.env_required == []
