"""`dooers push` — archive cwd and POST to dooers-push.

POC scaffold — actual implementation lands in the next milestone.
"""

import typer


def push(
    agent_id: str | None = typer.Argument(
        None,
        help="Agent ID. If omitted, reads agent_id from ./dooers.yaml.",
    ),
    tag: str = typer.Option("latest", help="Docker image tag."),
    env: str = typer.Option("prod", help="Target environment: prod | stg | dev"),
    no_build: bool = typer.Option(False, "--no-build", help="Upload only; do not build."),
) -> None:
    """Push the current directory as a new build of an agent."""
    typer.echo(
        f"[stub] would archive cwd, POST to dooers-push "
        f"(agent_id={agent_id}, tag={tag}, env={env}, no_build={no_build})"
    )
    raise typer.Exit(code=0)
