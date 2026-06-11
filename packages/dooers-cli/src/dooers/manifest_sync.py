"""Build the core PATCH body from a manifest + the deployed URL."""

from dooers_protocol.agents import AgentManifest


def _host_and_seg(deployed_url: str) -> str:
    """'https://agents.dooers.ai/ag-x' -> 'agents.dooers.ai/ag-x' (no scheme, no trailing /)."""
    return deployed_url.split("://", 1)[-1].rstrip("/")


def _norm_path(p: str) -> str:
    return p if p.startswith("/") else "/" + p


def build_agent_patch(manifest: AgentManifest, deployed_url: str) -> dict:
    """Map the declarative manifest to a core v2 PATCH /agents/:id body.

    Derives serverConfig.apiMessagesUrl (and whatsapp inbound) from the
    deployed host + the declared paths. Only includes keys the creator set.
    """
    host = _host_and_seg(deployed_url)
    patch: dict = {}

    # Only sync a non-empty description. An empty/absent one means "leave as-is"
    # — never send null, which would wipe a description set elsewhere (e.g. Studio).
    if manifest.description:
        patch["description"] = manifest.description

    if manifest.message_path:
        path = _norm_path(manifest.message_path)
        url = f"{manifest.message_scheme}://{host}{path}".rstrip("/")
        patch["serverConfig"] = {"apiMessagesUrl": url}

    if manifest.whatsapp and manifest.whatsapp.enabled:
        wpath = _norm_path(manifest.whatsapp.path or "/whatsapp/inbound")
        patch.setdefault("settings", {}).setdefault("integration_settings", {})["whatsapp"] = {
            "enabled": True,
            "inbound_http_url": f"https://{host}{wpath}",
        }

    if manifest.profile:
        prof: dict = {}
        p = manifest.profile
        if p.summary is not None:
            prof["summary"] = p.summary or None
        if p.image_url is not None:
            prof["imageUrl"] = p.image_url or None
        if p.capabilities:
            prof["capabilities"] = p.capabilities
        if p.tools:
            prof["tools"] = p.tools
        if p.usage_limits:
            prof["usageLimits"] = p.usage_limits
        if prof:
            patch["profile"] = prof

    return patch
