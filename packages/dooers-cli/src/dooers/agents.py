"""`dooers agents` subcommands: list, create, show.

By default uses FileShimAgentStore (no core dependency required).
Set DOOERS_USE_CORE_AGENTS=1 to switch to HTTPCoreAgentStore.
"""

import os
from pathlib import Path

import typer

from dooers import config
from dooers.agent_store import (
    AgentStore,
    FileShimAgentStore,
    HTTPCoreAgentStore,
)
from dooers.core_client import CoreClient, CoreClientError
from dooers.settings import Settings
from dooers.token_store import TokenStore, is_token_expired
from dooers_protocol import PROTOCOL_VERSION
from dooers_protocol.agents import AgentManifest, CreateAgentRequest, Runtime

app = typer.Typer(no_args_is_help=True)


def _ensure_authenticated() -> tuple[str, str]:
    """Returns (token, user_id) or exits."""
    store = TokenStore()
    token = store.load()
    if not token or is_token_expired(token):
        typer.echo("Not authenticated. Run `dooers auth login`.", err=True)
        raise typer.Exit(code=1)
    return token, ""  # user_id filled per-call below


def _resolve_store(ctx: typer.Context) -> AgentStore:
    settings: Settings = ctx.obj
    token, _ = _ensure_authenticated()
    if os.environ.get("DOOERS_USE_CORE_AGENTS") == "1":
        return HTTPCoreAgentStore(base_url=settings.core_url, token=token)
    # Shim mode: derive owner_user_id from whoami.
    try:
        me = CoreClient(base_url=settings.core_url, token=token).whoami()
    except CoreClientError as e:
        typer.echo(f"whoami failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    return FileShimAgentStore(owner_user_id=me.user_id)


@app.command(name="list")
def list_agents(ctx: typer.Context) -> None:
    """List the agents owned by the authenticated user."""
    store = _resolve_store(ctx)
    records = store.list()
    if not records:
        typer.echo("No agents yet. Try `dooers agents create --name my-agent`.")
        return
    typer.echo(f"{'ID':<14}{'NAME':<32}{'STATUS':<12}URL")
    for r in records:
        status = "deployed" if r.deployed_url else "draft"
        url = r.deployed_url or "—"
        typer.echo(f"{r.agent_id:<14}{r.name:<32}{status:<12}{url}")


@app.command()
def create(
    ctx: typer.Context,
    name: str = typer.Option(..., help="Display name for the new agent."),
    runtime: Runtime = typer.Option("docker", help="docker | python | node"),
) -> None:
    """Create an agent record and write dooers.yaml in cwd."""
    store = _resolve_store(ctx)
    record = store.create(CreateAgentRequest(name=name, runtime=runtime))
    manifest = AgentManifest(
        protocol_version=PROTOCOL_VERSION,
        agent_id=record.agent_id,
        name=record.name,
        runtime=record.runtime,
        env_required=record.env_required,
    )
    config.write_manifest(manifest, directory=Path.cwd())
    typer.echo(f"Created {record.agent_id}. {config.MANIFEST_FILENAME} written.")


@app.command()
def show(
    ctx: typer.Context,
    agent_id: str = typer.Argument(..., help="Agent ID (e.g. ag_8h2k)."),
) -> None:
    """Show details of a single agent."""
    store = _resolve_store(ctx)
    try:
        r = store.get(agent_id)
    except KeyError:
        typer.echo(f"Agent {agent_id} not found.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"ID:          {r.agent_id}")
    typer.echo(f"Name:        {r.name}")
    typer.echo(f"Runtime:     {r.runtime}")
    typer.echo(f"Env needed:  {', '.join(r.env_required) or '—'}")
    typer.echo(f"Status:      {'deployed' if r.deployed_url else 'draft'}")
    typer.echo(f"URL:         {r.deployed_url or '—'}")
    typer.echo(f"Created:     {r.created_at.isoformat()}")
