"""Audit + infra-manifest shapes used by the auditor pipeline step.

In the POC, the auditor step returns AuditReport(passed=True, findings=[]).
These shapes are committed now so the future real auditor implementation
drops in without touching callers.
"""

from typing import Literal

from pydantic import BaseModel


class AuditFinding(BaseModel):
    severity: Literal["info", "warning", "error"]
    category: str
    message: str
    file: str | None = None
    line: int | None = None


class InfraManifest(BaseModel):
    """What infrastructure the agent code declares (or is detected to need)."""

    needs_db: bool = False
    needs_redis: bool = False
    detected_endpoints: list[str] = []


class AuditReport(BaseModel):
    passed: bool
    findings: list[AuditFinding] = []
    required_infra: InfraManifest = InfraManifest()
