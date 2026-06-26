"""`dooers agents` subcommands: list, create, show, delete (core v2)."""

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
from dooers.protocol.teardown import format_teardown_result

from dooers.cli import config
from dooers.cli.agent_store import AgentStoreError, HTTPCoreAgentStore
from dooers.cli.env_prod import upsert_agent_seed_secret
from dooers.cli.org import resolve_org_for_cli
from dooers.cli.push_client import PushClient, PushClientError
from dooers.cli.settings import Settings
from dooers.cli.token_store import TokenStore, is_token_expired

app = typer.Typer(no_args_is_help=True)


def _store(ctx: typer.Context) -> tuple[HTTPCoreAgentStore, Settings, str]:
    settings: Settings = ctx.obj
    store_token = TokenStore()
    token = store_token.load()
    if not token or is_token_expired(token, store=store_token):
        typer.echo("Not authenticated. Run `dooers login`.", err=True)
        raise typer.Exit(code=1)
    return HTTPCoreAgentStore(settings.core_url, token), settings, token


@app.command()
def create(
    ctx: typer.Context,
    name: str = typer.Option(..., help="Display name for the new agent."),
    org: str | None = typer.Option(None, "--org", help="Organization id (else resolved/prompted)."),
    description: str | None = typer.Option(
        None, "--description", help="Short description of the agent."
    ),
) -> None:
    store, settings, _ = _store(ctx)
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
    if rec.runtime_api_key:
        env_path = upsert_agent_seed_secret(Path.cwd(), rec.runtime_api_key)
        typer.echo(f"Wrote AGENT_SEED_SECRET to {env_path.name} (shown once — store it safely).")
    else:
        typer.echo(
            "Warning: core did not return a runtime API key. "
            "Set Agent access key in Studio and AGENT_SEED_SECRET in env.prod before push.",
            err=True,
        )
    typer.echo("Edit dooers.yaml (message_path, whatsapp, profile) then run dooers push.")


@app.command("rotate-key")
def rotate_key(
    ctx: typer.Context,
    agent_id: str = typer.Argument(..., help="Agent id to rotate the runtime API key for."),
) -> None:
    store, _, _ = _store(ctx)
    try:
        rec = store.regenerate_runtime_api_key(agent_id)
    except AgentStoreError as e:
        typer.echo(f"rotate-key failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    if rec.runtime_api_key:
        env_path = upsert_agent_seed_secret(Path.cwd(), rec.runtime_api_key)
        typer.echo(f"Wrote AGENT_SEED_SECRET to {env_path.name} (shown once — store it safely).")
    else:
        typer.echo(
            "Warning: core did not return a runtime API key. Check Studio for the new key.",
            err=True,
        )
    typer.echo("Run dooers push to deploy the updated secret to your agent host.")


@app.command(name="list")
def list_agents(ctx: typer.Context, org: str | None = typer.Option(None, "--org")) -> None:
    store, settings, _ = _store(ctx)
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
    store, _, _ = _store(ctx)
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


@app.command()
def delete(
    ctx: typer.Context,
    agent_id: str = typer.Argument(..., help="Agent id to delete."),
    archive: bool = typer.Option(
        False, "--archive", help="Archive an active agent first, then delete."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    store, settings, token = _store(ctx)

    # 1. Fetch the record (existence + status + name).
    try:
        rec = store.get(agent_id)
    except KeyError:
        typer.echo(f"Agent {agent_id} not found.", err=True)
        raise typer.Exit(code=1)
    except AgentStoreError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    # 2. Active-state pre-check — fail fast before touching any infra.
    if rec.status == "active" and not archive:
        typer.echo(
            f"Agent {agent_id} is active; pass --archive to archive-then-delete, "
            "or archive it first.",
            err=True,
        )
        raise typer.Exit(code=1)

    # 3. Confirm (abort=True raises typer.Abort → exit 1, no further calls).
    if not yes:
        typer.confirm(
            f"Delete agent {rec.name or agent_id} ({agent_id})? "
            "This deletes the record and tears down its deployed service. "
            "This cannot be undone.",
            abort=True,
        )

    # 4. Archive an active agent if requested (clears core's active-state delete guard).
    if rec.status == "active" and archive:
        try:
            store.archive(agent_id)
        except AgentStoreError as e:
            typer.echo(f"Archive failed: {e}", err=True)
            raise typer.Exit(code=1) from e

    # 5. Tear down infra (Cloud Run + LB rule) via dooers-push — BEFORE the record delete.
    push = PushClient(base_url=settings.push_url, token=token)
    try:
        teardown = push.teardown(agent_id, env=settings.env)
    except PushClientError as e:
        typer.echo(f"Teardown failed: {e}", err=True)
        raise typer.Exit(code=1) from e

    # 6. Delete the core record.
    try:
        store.delete(agent_id)
    except AgentStoreError as e:
        typer.echo(f"Service torn down, but the core record was not deleted: {e}", err=True)
        raise typer.Exit(code=1) from e

    # 7. Summary.
    typer.echo(f"Deleted agent {agent_id} ({rec.name}).")
    typer.echo(format_teardown_result(teardown))
