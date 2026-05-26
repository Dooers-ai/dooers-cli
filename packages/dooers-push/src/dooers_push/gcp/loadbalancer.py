"""LBManager + naming helpers + LBError.

Owns per-agent Load Balancer registration (Serverless NEG + Backend
Service + URL Map host rule).

All operations are idempotent: re-registering the same agent updates
the existing NEG to point at the latest Cloud Run revision; it does
not create duplicates.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Naming helpers (pure functions)
# ---------------------------------------------------------------------------

def safe_agent_id(agent_id: str) -> str:
    """Convert an agent_id to a DNS- and GCP-safe form.

    'ag_7q4r' → 'ag-7q4r'.  Lowercases, replaces underscores.
    Raises ValueError on empty input or input containing whitespace.
    """
    if not agent_id:
        raise ValueError("agent_id must not be empty")
    if any(c.isspace() for c in agent_id):
        raise ValueError(f"agent_id must not contain whitespace: {agent_id!r}")
    return agent_id.lower().replace("_", "-")


def host_for(agent_id: str, env: str, lb_domain: str) -> str:
    """Return the per-agent LB hostname.

    Prod drops the env suffix; non-prod keeps it.
    host_for('ag_7q4r', 'prod', 'agents.dooers.ai')
    → 'ag-7q4r.agents.dooers.ai'
    host_for('ag_7q4r', 'dev', 'agents.dooers.ai')
    → 'ag-7q4r-dev.agents.dooers.ai'
    """
    safe = safe_agent_id(agent_id)
    if env == "prod":
        return f"{safe}.{lb_domain}"
    return f"{safe}-{env}.{lb_domain}"


def neg_name(agent_id: str, env: str) -> str:
    """Internal resource name; keeps env in all envs for easy filtering."""
    return f"agent-{safe_agent_id(agent_id)}-{env}-neg"


def bs_name(agent_id: str, env: str) -> str:
    return f"agent-{safe_agent_id(agent_id)}-{env}-bs"


def path_matcher_name(agent_id: str, env: str) -> str:
    return f"agent-{safe_agent_id(agent_id)}-{env}-pm"
