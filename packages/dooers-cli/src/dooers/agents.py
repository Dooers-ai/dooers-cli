"""`dooers agents` subcommands: list, create, show.

POC scaffold — actual implementation lands in the next milestone.
"""

import typer

app = typer.Typer(no_args_is_help=True)


@app.command(name="list")
def list_agents() -> None:
    """List the agents owned by the authenticated user."""
    typer.echo("[stub] would call core GET /agents")
    raise typer.Exit(code=0)


@app.command()
def create(
    name: str = typer.Option(..., help="Display name for the new agent."),
    runtime: str = typer.Option("docker", help="docker | python | node"),
) -> None:
    """Create an agent record and write dooers.yaml in cwd."""
    typer.echo(f"[stub] would call core POST /agents (name={name}, runtime={runtime})")
    raise typer.Exit(code=0)


@app.command()
def show(agent_id: str = typer.Argument(..., help="Agent ID (e.g. ag_8h2k).")) -> None:
    """Show details of a single agent."""
    typer.echo(f"[stub] would call core GET /agents/{agent_id}")
    raise typer.Exit(code=0)
