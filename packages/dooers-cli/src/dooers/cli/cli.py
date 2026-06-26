"""Top-level Typer app — login/whoami/logout + agents group + push."""

import warnings

# Global `dooers-agents-server` installs `dooers.protocol.frames` with a legacy
# `schema` field name that Pydantic warns on at import. The CLI does not use WS
# frames; suppress so every command stays quiet when both packages share `dooers.*`.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "schema" in "SettingsPublicSchemaResultPayload" shadows an attribute in parent "BaseModel"',
    category=UserWarning,
)

import typer

from . import agents, auth, org, push, validate
from .settings import Settings

app = typer.Typer(
    name="dooers",
    add_completion=False,
    no_args_is_help=True,
    help="Dooers CLI — push agents to Dooers, manage agent records, authenticate.",
)


@app.callback()
def _root(
    ctx: typer.Context,
    core_url: str | None = typer.Option(None, "--core-url", help="Override core API URL."),
    push_url: str | None = typer.Option(None, "--push-url", help="Override dooers-push URL."),
    env: str | None = typer.Option(None, "--env", help="Target environment: prod | stg | dev."),
) -> None:
    """Resolve global settings once per invocation."""
    ctx.obj = Settings.resolve(core_url=core_url, push_url=push_url, env=env)


app.command(name="login", help="Authenticate with Dooers (OTP via email).")(auth.login)
app.command(name="whoami", help="Show the currently authenticated user.")(auth.whoami)
app.command(name="logout", help="Clear local credentials.")(auth.logout)
app.add_typer(
    agents.app,
    name="agents",
    help="Manage agents — subcommands: list | create | rotate-key | show | delete.",
)
app.add_typer(org.app, name="org", help="Manage organization — subcommands: list | use.")
app.command(
    name="validate",
    help="Validate dooers.yaml, Dockerfile, and SDK dependency/import conventions.",
)(validate.validate)
app.command(name="push", help="Archive cwd and push it as a new build of an agent.")(push.push)


if __name__ == "__main__":
    app()
