"""`dooers push` — archive cwd and POST to dooers-push."""

import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import typer

from dooers import config, ignore
from dooers.manifest_sync import build_agent_patch
from dooers.push_client import PushClient, PushClientError
from dooers.settings import Settings
from dooers.token_store import TokenStore, is_token_expired


def _spinner(message: str) -> Callable[[], None]:
    """Background spinner on stderr. Returns a stopper function."""
    stop = threading.Event()
    frames = "|/-\\"

    def run() -> None:
        i = 0
        while not stop.is_set():
            sys.stderr.write(f"\r{message} {frames[i % 4]}")
            sys.stderr.flush()
            i += 1
            time.sleep(0.1)
        sys.stderr.write("\r" + " " * (len(message) + 4) + "\r")
        sys.stderr.flush()

    t = threading.Thread(target=run, daemon=True)
    t.start()

    def cancel() -> None:
        stop.set()
        t.join(timeout=1)

    return cancel


def push(
    ctx: typer.Context,
    agent_id: str | None = typer.Argument(
        None,
        help="Agent ID. If omitted, reads agent_id from ./dooers.yaml.",
    ),
    tag: str = typer.Option("latest", help="Docker image tag."),
    env: str | None = typer.Option(
        None, help="Target environment: prod | stg | dev (overrides --env on the root)."
    ),
) -> None:
    """Push the current directory as a new build of an agent."""
    settings: Settings = ctx.obj
    target_env = env or settings.env

    # Resolve agent_id from arg or manifest.
    manifest = config.read_manifest()
    if agent_id is None:
        if manifest is None:
            typer.echo(
                f"Missing {config.MANIFEST_FILENAME}. Run `dooers agents create` first "
                f"or pass an agent_id explicitly.",
                err=True,
            )
            raise typer.Exit(code=1)
        agent_id = manifest.agent_id

    # Client-side hosting guard. The dooers-push server is the authoritative
    # gate (it checks the org's hosting plan feature); this is a fast local
    # check so `hosting: false` in dooers.yaml never even uploads.
    if manifest is not None and not manifest.hosting:
        typer.echo(
            "hosting is disabled in dooers.yaml (hosting: false). Aborting push.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Auth.
    store = TokenStore()
    token = store.load()
    if not token or is_token_expired(token, store=store):
        typer.echo("Not authenticated. Run `dooers login`.", err=True)
        raise typer.Exit(code=1)

    # Archive cwd.
    typer.echo("Archiving …")
    archive_path = Path(ignore.make_archive("."))
    size_mb = archive_path.stat().st_size / (1024 * 1024)
    typer.echo(f"Archive: {archive_path.name} ({size_mb:.1f} MB)")

    # Push.
    client = PushClient(base_url=settings.push_url, token=token)
    cancel_spinner = _spinner(f"Pushing {agent_id} (this can take 3-5 min)")
    try:
        resp = client.push(agent_id=agent_id, archive_path=archive_path, tag=tag, env=target_env)
    except PushClientError as e:
        cancel_spinner()
        typer.echo(f"Push failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    finally:
        cancel_spinner()
        os.unlink(archive_path)

    # Report.
    if resp.audit and resp.audit.required_infra.detected_endpoints:
        endpoints = resp.audit.required_infra.detected_endpoints
        typer.echo(f"\nAudit: {len(endpoints)} endpoint(s) detected:")
        for ep in endpoints[:10]:
            typer.echo(f"  - {ep}")
        if len(endpoints) > 10:
            typer.echo(f"  … and {len(endpoints) - 10} more")
    elif resp.audit:
        typer.echo("\nAudit: 0 endpoints detected.")
    if resp.status.value == "succeeded" and resp.url:
        typer.echo(f"\nLive at: {resp.url}")
        if target_env == "prod":
            try:
                manifest = config.read_manifest()
                if manifest is not None:
                    patch = build_agent_patch(manifest, resp.url)
                    if patch:
                        from dooers.agent_store import HTTPCoreAgentStore

                        HTTPCoreAgentStore(settings.core_url, token).update(
                            manifest.agent_id, patch
                        )
                        typer.echo("Synced agent config to core.")
            except Exception as e:  # noqa: BLE001 — non-fatal: agent is live, sync is best-effort
                typer.echo(f"Warning: could not sync agent config: {e}", err=True)
    else:
        typer.echo(f"\nStatus: {resp.status.value}")
        if resp.error:
            typer.echo(f"Error: {resp.error}", err=True)
        if resp.build_id:
            typer.echo(f"Build ID: {resp.build_id}")
        raise typer.Exit(code=1 if resp.status.value == "failed" else 0)
