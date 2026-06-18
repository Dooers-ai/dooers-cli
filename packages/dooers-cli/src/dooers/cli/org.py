"""Org resolution + `dooers org list|use`."""

from collections.abc import Callable

import typer

from dooers.cli.core_client import CoreClient, CoreClientError
from dooers.cli.settings import Settings
from dooers.cli.token_store import TokenStore, is_token_expired
from dooers.cli.user_config import UserConfig

app = typer.Typer(no_args_is_help=True)


def resolve_org(
    *,
    orgs: list[dict],
    explicit: str | None,
    default: str | None,
    prompt: Callable[[list[dict]], str],
) -> str:
    """Precedence: explicit flag > saved default > single-org auto > prompt."""
    if explicit:
        return explicit
    ids = {o["organizationId"] for o in orgs}
    if default and default in ids:
        return default
    if len(orgs) == 1:
        return orgs[0]["organizationId"]
    return prompt(orgs)


def _token(settings: Settings) -> str:
    store = TokenStore()
    token = store.load()
    if not token or is_token_expired(token, store=store):
        typer.echo("Not authenticated. Run `dooers login`.", err=True)
        raise typer.Exit(code=1)
    return token


def resolve_org_for_cli(settings: Settings, explicit: str | None) -> str:
    """Fetch orgs, apply precedence, persist the chosen default when prompting."""
    token = _token(settings)
    try:
        orgs = CoreClient(base_url=settings.core_url, token=token).list_organizations()
    except CoreClientError as e:
        typer.echo(f"could not list organizations: {e}", err=True)
        raise typer.Exit(code=1) from e
    if not orgs:
        typer.echo("You don't belong to any organization.", err=True)
        raise typer.Exit(code=1)
    cfg = UserConfig()

    def _prompt(options: list[dict]) -> str:
        typer.echo("Multiple organizations — choose one:")
        for i, o in enumerate(options, 1):
            typer.echo(f"  {i}. {o.get('name', o['organizationId'])} ({o['organizationId']})")
        idx = typer.prompt("Number", type=int)
        chosen = options[idx - 1]["organizationId"]
        cfg.set_default_org(chosen)
        typer.echo(f"Saved default org: {chosen}")
        return chosen

    return resolve_org(orgs=orgs, explicit=explicit, default=cfg.get_default_org(), prompt=_prompt)


@app.command(name="list")
def list_orgs(ctx: typer.Context) -> None:
    settings: Settings = ctx.obj
    token = _token(settings)
    orgs = CoreClient(base_url=settings.core_url, token=token).list_organizations()
    default = UserConfig().get_default_org()
    for o in orgs:
        mark = " (default)" if o["organizationId"] == default else ""
        typer.echo(f"{o['organizationId']}  {o.get('name', '')}{mark}")


@app.command()
def use(ctx: typer.Context, organization_id: str = typer.Argument(...)) -> None:
    UserConfig().set_default_org(organization_id)
    typer.echo(f"Default org set to {organization_id}")
