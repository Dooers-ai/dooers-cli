"""`dooers agents` subcommands: list, create, show (core v2)."""

from pathlib import Path

import typer
from dooers.protocol import PROTOCOL_VERSION
from dooers.protocol.agents import (
    AgentManifest,
    CreateAgentRequest,
    ProfileConfig,
    UiConfig,
    WhatsAppConfig,
)

from dooers.cli import config
from dooers.cli.agent_store import AgentStoreError, HTTPCoreAgentStore
from dooers.cli.org import resolve_org_for_cli
from dooers.cli.settings import Settings
from dooers.cli.token_store import TokenStore, is_token_expired

app = typer.Typer(no_args_is_help=True)


def _store(ctx: typer.Context) -> tuple[HTTPCoreAgentStore, Settings]:
    settings: Settings = ctx.obj
    store_token = TokenStore()
    token = store_token.load()
    if not token or is_token_expired(token, store=store_token):
        typer.echo("Not authenticated. Run `dooers login`.", err=True)
        raise typer.Exit(code=1)
    return HTTPCoreAgentStore(settings.core_url, token), settings


@app.command()
def create(
    ctx: typer.Context,
    name: str = typer.Option(..., help="Display name for the new agent."),
    org: str | None = typer.Option(None, "--org", help="Organization id (else resolved/prompted)."),
    description: str | None = typer.Option(
        None, "--description", help="Short description of the agent."
    ),
) -> None:
    store, settings = _store(ctx)
    organization_id = resolve_org_for_cli(settings, org)
    try:
        rec = store.create(CreateAgentRequest(organization_id=organization_id, name=name))
    except AgentStoreError as e:
        typer.echo(f"create failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    config.write_manifest(
        AgentManifest(
            protocol_version=PROTOCOL_VERSION,
            agent_id=rec.agent_id,
            name=rec.name,
            organization_id=rec.organization_id or organization_id,
            description=description,
            message_path="/",
            message_scheme="wss",
            whatsapp=WhatsAppConfig(enabled=False, path=None),
            profile=ProfileConfig(),
            ui=UiConfig(),
        ),
        directory=Path.cwd(),
    )
    typer.echo(f"Created {rec.agent_id}. {config.MANIFEST_FILENAME} written.")
    typer.echo("Edit dooers.yaml (message_path, whatsapp, profile) then run dooers push.")


@app.command(name="list")
def list_agents(ctx: typer.Context, org: str | None = typer.Option(None, "--org")) -> None:
    store, settings = _store(ctx)
    organization_id = resolve_org_for_cli(settings, org)
    try:
        records = store.list_by_org(organization_id)
    except AgentStoreError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    if not records:
        typer.echo("No agents yet. Try `dooers agents create --name my-agent`.")
        return
    typer.echo(f"{'ID':<38}{'NAME':<24}URL")
    for r in records:
        typer.echo(f"{r.agent_id:<38}{r.name:<24}{r.host_url or '—'}")


@app.command()
def show(ctx: typer.Context, agent_id: str = typer.Argument(...)) -> None:
    store, _ = _store(ctx)
    try:
        r = store.get(agent_id)
    except KeyError:
        typer.echo(f"Agent {agent_id} not found.", err=True)
        raise typer.Exit(code=1)
    except AgentStoreError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"ID:    {r.agent_id}")
    typer.echo(f"Name:  {r.name}")
    typer.echo(f"Org:   {r.organization_id}")
    typer.echo(f"URL:   {r.host_url or '—'}")
