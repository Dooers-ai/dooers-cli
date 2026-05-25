"""`dooers auth` subcommands: login, whoami, logout.

POC scaffold — actual implementation lands in the next milestone.
See docs/superpowers/specs/2026-05-26-dooers-cli-v2-design.md §5.2.
"""

import typer

app = typer.Typer(no_args_is_help=True)


@app.command()
def login(email: str = typer.Option(..., prompt=True, help="Your email address.")) -> None:
    """Authenticate with Dooers via OTP sent to email."""
    typer.echo(f"[stub] would request OTP for {email}")
    raise typer.Exit(code=0)


@app.command()
def whoami() -> None:
    """Show the currently authenticated user."""
    typer.echo("[stub] would call core /session/verify")
    raise typer.Exit(code=0)


@app.command()
def logout() -> None:
    """Clear local credentials."""
    typer.echo("[stub] would remove ~/.dooers/token")
    raise typer.Exit(code=0)
