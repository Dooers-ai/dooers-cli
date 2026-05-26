"""Auditor step — POC version that produces a visible AuditReport.

It scans the uploaded archive (downloaded from GCS) for:
- top-level imports of Python source files
- HTTP endpoint route decorators (FastAPI/Flask)

It does NOT block anything — `passed=True` always. The visible output is
purely for stakeholder demos: "look, the auditor saw your code."
Replace this with real maliciousness rules in a future spec.
"""

import io
import logging
import re
import tarfile
import zipfile

from google.cloud import storage

from dooers_protocol.audit import AuditFinding, AuditReport, InfraManifest
from dooers_protocol.push import BuildStatus
from dooers_push.pipeline.base import PipelineContext, PipelineStep, StepResult

logger = logging.getLogger(__name__)

_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))", re.MULTILINE)
_ROUTE_RE = re.compile(
    r"@\w+\.(?:get|post|put|patch|delete|route)\s*\(\s*['\"]([^'\"]+)['\"]"
)


def _read_text_member(data: bytes, name: str) -> str:
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _scan_text(text: str) -> tuple[set[str], list[str]]:
    imports = set()
    for m in _IMPORT_RE.finditer(text):
        mod = m.group(1) or m.group(2)
        if mod:
            imports.add(mod.split(".")[0])
    endpoints = _ROUTE_RE.findall(text)
    return imports, endpoints


def _scan_archive_bytes(blob_bytes: bytes, archive_name: str) -> tuple[set[str], list[str]]:
    imports: set[str] = set()
    endpoints: list[str] = []
    if archive_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(blob_bytes)) as zf:
            for info in zf.infolist():
                if not info.filename.endswith(".py"):
                    continue
                text = _read_text_member(zf.read(info), info.filename)
                imp, eps = _scan_text(text)
                imports |= imp
                endpoints.extend(eps)
    else:
        with tarfile.open(fileobj=io.BytesIO(blob_bytes), mode="r:*") as tar:
            for member in tar.getmembers():
                if not (member.isfile() and member.name.endswith(".py")):
                    continue
                f = tar.extractfile(member)
                if not f:
                    continue
                text = _read_text_member(f.read(), member.name)
                imp, eps = _scan_text(text)
                imports |= imp
                endpoints.extend(eps)
    return imports, endpoints


class AuditorStep(PipelineStep):
    name = "auditor"

    async def run(self, ctx: PipelineContext) -> StepResult:
        # Download archive from GCS (small enough — POC stance).
        if not ctx.gcs_uri.startswith("gs://"):
            ctx.audit_report = AuditReport(passed=True)
            return StepResult(status=BuildStatus.queued)
        _, rest = ctx.gcs_uri.split("gs://", 1)
        bucket, object_path = rest.split("/", 1)
        client = storage.Client()
        blob_bytes = client.bucket(bucket).blob(object_path).download_as_bytes()

        try:
            imports, endpoints = _scan_archive_bytes(blob_bytes, ctx.gcs_uri)
        except Exception as e:  # noqa: BLE001
            logger.warning("auditor scan failed: %s", e)
            imports, endpoints = set(), []

        findings = [
            AuditFinding(severity="info", category="endpoint", message=f"detected endpoint: {ep}")
            for ep in sorted(set(endpoints))
        ]
        ctx.audit_report = AuditReport(
            passed=True,
            findings=findings,
            required_infra=InfraManifest(
                detected_endpoints=sorted(set(endpoints)),
            ),
        )
        logger.info(
            "auditor: agent=%s endpoints=%d imports=%d (top-level: %s)",
            ctx.agent.agent_id, len(endpoints), len(imports),
            ", ".join(sorted(imports)[:8]) or "—",
        )
        return StepResult(status=BuildStatus.queued)
