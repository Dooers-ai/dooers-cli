"""`dooers agents` subcommands: list, create, show (core v2)."""

from pathlib import Path

import typer

from dooers import config
from dooers.agent_store import AgentStoreError, HTTPCoreAgentStore
from dooers.org import resolve_org_for_cli
from dooers.settings import Settings
from dooers.token_store import TokenStore, is_token_expired
from dooers_protocol import PROTOCOL_VERSION
from dooers_protocol.agents import AgentManifest, CreateAgentRequest

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
        ),
        directory=Path.cwd(),
    )
    typer.echo(f"Created {rec.agent_id}. {config.MANIFEST_FILENAME} written.")


@app.command(name="list")
def list_agents(ctx: typer.Context, org: str | None = typer.Option(None, "--org")) -> None:
    store, settings = _store(ctx)
    organization_id = resolve_org_for_cli(settings, org)
    records = store.list_by_org(organization_id)
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
    typer.echo(f"ID:    {r.agent_id}\nName:  {r.name}\nOrg:   {r.organization_id}\nURL:   {r.host_url or '—'}")
