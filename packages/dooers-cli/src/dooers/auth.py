"""`dooers login` / `whoami` / `logout` command implementations.

Registered as top-level commands from cli.py.
"""

import typer

from dooers.core_client import CoreClient, CoreClientError
from dooers.settings import Settings
from dooers.token_store import TokenStore, is_token_expired


def _settings(ctx: typer.Context) -> Settings:
    s = ctx.obj
    if not isinstance(s, Settings):
        typer.echo("internal: settings not resolved", err=True)
        raise typer.Exit(code=1)
    return s


def login(
    ctx: typer.Context,
    email: str | None = typer.Argument(None, help="Your email address. Prompted if omitted."),
) -> None:
    """Authenticate with Dooers via OTP sent to email."""
    settings = _settings(ctx)
    if not email:
        email = typer.prompt("Email")
    store = TokenStore()

    existing = store.load()
    if existing and not is_token_expired(existing):
        typer.echo("Already authenticated. Run `dooers logout` first to re-login.")
        raise typer.Exit(code=0)

    client = CoreClient(base_url=settings.core_url)
    try:
        typer.echo("Requesting verification code…")
        email_id = client.login_request_otp(email)
        typer.echo("Verification code sent to your email.")
        code = typer.prompt("Enter the code")
        token = client.login_verify_otp(email_id=email_id, code=code)
    except CoreClientError as e:
        typer.echo(f"Authentication failed: {e}", err=True)
        raise typer.Exit(code=1) from e

    store.save(token)
    typer.echo("Authenticated.")


def whoami(ctx: typer.Context) -> None:
    """Show the currently authenticated user."""
    settings = _settings(ctx)
    store = TokenStore()
    token = store.load()
    if not token:
        typer.echo("Not authenticated. Run `dooers login`.", err=True)
        raise typer.Exit(code=1)
    if is_token_expired(token):
        typer.echo("Session expired. Run `dooers login`.", err=True)
        store.clear()
        raise typer.Exit(code=1)

    client = CoreClient(base_url=settings.core_url, token=token)
    try:
        me = client.whoami()
    except CoreClientError as e:
        typer.echo(f"whoami failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"Authenticated as {me.email} (user_id={me.user_id})")


def logout(ctx: typer.Context) -> None:
    """Clear local credentials."""
    settings = _settings(ctx)
    store = TokenStore()
    token = store.load()
    if token:
        CoreClient(base_url=settings.core_url, token=token).logout()
    store.clear()
    typer.echo("Logged out.")
