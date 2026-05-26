"""Top-level Typer app — mounts auth, agents, push subcommand groups."""

import typer

from dooers import agents, auth, push
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


app.add_typer(auth.app, name="auth", help="Authenticate with the Dooers core API.")
app.add_typer(agents.app, name="agents", help="List, create, and inspect your agents.")
app.command(name="push", help="Archive cwd and push it as a new build of an agent.")(push.push)


if __name__ == "__main__":
    app()
