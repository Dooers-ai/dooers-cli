"""Teardown request/response for dooers-push agent deletion."""

from pydantic import BaseModel


class TeardownRequest(BaseModel):
    # agent_id is sent in the path and env in the query string; this model
    # documents the contract (mirrors PushRequest alongside POST /v1/push/{id}).
    agent_id: str
    env: str = "prod"


class TeardownResponse(BaseModel):
    agent_id: str
    service_deleted: bool  # Cloud Run service existed and was deleted
    lb_rule_removed: bool  # load-balancer path rule existed and was removed
    service_name: str | None = None
    error: str | None = None


def format_teardown_result(resp: TeardownResponse) -> str:
    """One-line human summary of what teardown removed (used by `dooers agents delete`)."""
    if not resp.service_deleted and not resp.lb_rule_removed:
        return "No deployed service found — record only."
    parts = []
    if resp.service_deleted:
        parts.append("Cloud Run service deleted")
    if resp.lb_rule_removed:
        parts.append("load-balancer rule removed")
    return "; ".join(parts) + "."
