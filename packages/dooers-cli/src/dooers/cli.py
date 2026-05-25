"""Top-level Typer app — mounts auth, agents, push subcommand groups."""

import typer

from dooers import agents, auth, push

app = typer.Typer(
    name="dooers",
    add_completion=False,
    no_args_is_help=True,
    help="Dooers CLI — push agents to Dooers, manage agent records, authenticate.",
)

app.add_typer(auth.app, name="auth", help="Authenticate with the Dooers core API.")
app.add_typer(agents.app, name="agents", help="List, create, and inspect your agents.")
app.command(name="push", help="Archive cwd and push it as a new build of an agent.")(push.push)


if __name__ == "__main__":
    app()
