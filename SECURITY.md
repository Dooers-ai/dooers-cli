# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in any package in this repository (`dooers`, `dooers-protocol`, or the `dooers-push` service), please report it privately.

- Email: security@dooers.ai
- GitHub: open a private security advisory at https://github.com/Dooers-ai/dooers-cli/security/advisories/new

Please include:

- The affected package(s) and version(s).
- A clear description of the issue and its impact.
- Reproduction steps or a proof-of-concept where possible.
- Whether you've disclosed the issue elsewhere.

We aim to acknowledge reports within 3 business days and to ship a fix or mitigation within 30 days for critical issues.

## In scope

- Authentication / authorization bypass on the `dooers-push` API.
- Tenant isolation failures — one creator's agents, archives, or build artifacts visible or mutable by another.
- Code execution via the push pipeline — malicious archives gaining execution on shared infrastructure (the auditor step is the intended mitigation; bypasses are in scope).
- Wire protocol issues — `dooers-protocol` parsers trusting malformed input in a way that yields RCE or DoS.
- Token / credential exposure — `~/.dooers/token` handling, leakage to logs, etc.

## Out of scope

- Vulnerabilities in third-party dependencies that are already fixed upstream — please report those upstream first.
- DoS at the transport layer that is bounded by GCP infrastructure (Cloud Run, Cloud Build rate limits).
- Issues in deployed user agents themselves (those are the creator's responsibility).

## Disclosure timeline

- Day 0: Report received, acknowledged.
- Day 1–14: Triage, reproduction, fix scoped.
- Day 14–30: Patch released, advisory published with credits.
- Day 30+ (if needed): Coordinated disclosure with downstream packagers.
