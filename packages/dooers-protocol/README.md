# dooers-protocol

Shared Pydantic models defining the wire contract between any Dooers client (`dooers-cli`, future SDKs) and the `dooers-push` Cloud Run service.

`PROTOCOL_VERSION` is exported from the package root.

## Modules

- `auth` — `AuthSession`, `WhoamiResponse`.
- `agents` — `AgentRecord`, `CreateAgentRequest`, `AgentManifest` (the `dooers.yaml` schema).
- `push` — `PushRequest`, `PushResponse`, `BuildStatus`.
- `audit` — `AuditReport`, `AuditFinding`, `InfraManifest` (used by the auditor pipeline step).
- `errors` — `ErrorCode`, `ErrorEnvelope`.

## Use

```python
from dooers_protocol import PROTOCOL_VERSION
from dooers_protocol.push import PushResponse, BuildStatus
```
