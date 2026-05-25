"""Common error envelope returned by dooers-push for non-2xx responses."""

from enum import Enum

from pydantic import BaseModel


class ErrorCode(str, Enum):
    unauthenticated = "unauthenticated"
    forbidden = "forbidden"
    not_found = "not_found"
    archive_too_large = "archive_too_large"
    audit_failed = "audit_failed"
    build_failed = "build_failed"
    build_timeout = "build_timeout"
    core_unreachable = "core_unreachable"
    internal = "internal"


class ErrorEnvelope(BaseModel):
    error_code: ErrorCode
    message: str
    correlation_id: str
    details: dict = {}
