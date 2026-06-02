"""Top-level Typer app — login/whoami/logout + agents group + push."""

import typer

from dooers import agents, auth, org, push
from dooers.settings import Settings

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
app.add_typer(agents.app, name="agents", help="List, create, and inspect your agents.")
app.add_typer(org.app, name="org", help="List and select your organization.")
app.command(name="push", help="Archive cwd and push it as a new build of an agent.")(push.push)


if __name__ == "__main__":
    app()
