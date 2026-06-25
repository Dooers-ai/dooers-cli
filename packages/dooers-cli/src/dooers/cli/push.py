"""`dooers push` — archive cwd, trigger an async build, poll until terminal."""

import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import typer
from dooers.protocol.push import (
    BuildStatus,
    BuildStatusResponse,
    format_push_failure,
    is_terminal,
)

from dooers.cli import config, ignore
from dooers.cli.manifest_sync import build_agent_patch
from dooers.cli.push_client import PushClient, PushClientError, PushTransientError
from dooers.cli.settings import Settings
from dooers.cli.token_store import TokenStore, is_token_expired

# Max consecutive transient (5xx/network) poll errors before giving up.
_MAX_CONSECUTIVE_TRANSIENT = 10


class PushTimeout(RuntimeError):
    """Raised when a build does not reach a terminal state within the timeout."""


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


def poll_until_terminal(
    client: PushClient,
    build_id: str,
    *,
    sleep: Callable[[float], object] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    timeout_s: float = 1200.0,
    interval_s: float = 4.0,
    on_phase: Callable[[str | None], object] = lambda _p: None,
) -> BuildStatusResponse:
    """Poll get_build_status until the build is terminal, then return it.

    Transient errors (PushTransientError) are retried up to a bound; the wall
    clock (via `now`) is checked against `timeout_s` so callers can inject a fake
    clock for deterministic tests. Raises PushTimeout on timeout, or re-raises
    the last PushTransientError if too many consecutive transient errors occur.
    """
    start = now()
    consecutive_transient = 0
    last_transient: PushTransientError | None = None
    while True:
        if now() - start >= timeout_s:
            raise PushTimeout(f"build {build_id} did not finish within {timeout_s:.0f}s")
        try:
            status = client.get_build_status(build_id)
        except PushTransientError as e:
            consecutive_transient += 1
            last_transient = e
            if consecutive_transient > _MAX_CONSECUTIVE_TRANSIENT:
                raise
            sleep(interval_s)
            continue
        consecutive_transient = 0
        last_transient = None
        on_phase(status.phase)
        if is_terminal(status.status):
            return status
        sleep(interval_s)
    # Unreachable; appeases type checkers that don't see the infinite loop.
    assert last_transient is None  # pragma: no cover


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

    # Trigger the build (returns immediately with a 202 + build_id).
    client = PushClient(base_url=settings.push_url, token=token)
    try:
        accepted = client.push(
            agent_id=agent_id, archive_path=archive_path, tag=tag, env=target_env
        )
    except PushClientError as e:
        typer.echo(f"Push failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    finally:
        os.unlink(archive_path)

    # Report audit findings (computed before the build, returned on the 202).
    if accepted.audit and accepted.audit.required_infra.detected_endpoints:
        endpoints = accepted.audit.required_infra.detected_endpoints
        typer.echo(f"\nAudit: {len(endpoints)} endpoint(s) detected:")
        for ep in endpoints[:10]:
            typer.echo(f"  - {ep}")
        if len(endpoints) > 10:
            typer.echo(f"  … and {len(endpoints) - 10} more")
    elif accepted.audit:
        typer.echo("\nAudit: 0 endpoints detected.")

    # Poll until terminal, relabeling the spinner with the current phase.
    spinner_state: dict[str, Callable[[], None] | str] = {}

    def _start_spinner(label: str) -> None:
        cancel = spinner_state.get("cancel")
        if callable(cancel):
            cancel()
        spinner_state["label"] = label
        spinner_state["cancel"] = _spinner(label)

    def _on_phase(phase: str | None) -> None:
        label = f"Building {agent_id}: {phase}" if phase else f"Building {agent_id}"
        if label != spinner_state.get("label"):
            _start_spinner(label)

    _start_spinner(f"Building {agent_id}")
    try:
        final = poll_until_terminal(client, accepted.build_id, on_phase=_on_phase)
    except PushTimeout:
        _stop_spinner(spinner_state)
        typer.echo(
            "\nStill building — re-run `dooers push` to resume polling, "
            "or check `dooers agents show`.",
            err=True,
        )
        raise typer.Exit(code=1) from None
    except PushClientError as e:
        _stop_spinner(spinner_state)
        typer.echo(f"\nPush failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    else:
        _stop_spinner(spinner_state)

    # Render the terminal result.
    if final.status == BuildStatus.succeeded:
        if final.url:
            typer.echo(f"\nLive at: {final.url}")
            _sync_core_config(settings, token, target_env, final.url)
        else:
            typer.echo("\nBuild succeeded.")
    elif final.status == BuildStatus.failed:
        typer.echo(f"\n{format_push_failure(final)}", err=True)
        raise typer.Exit(code=1)
    else:
        typer.echo(f"\nStatus: {final.status.value}")
        raise typer.Exit(code=0)


def _stop_spinner(spinner_state: dict[str, object]) -> None:
    cancel = spinner_state.get("cancel")
    if callable(cancel):
        cancel()
    spinner_state["cancel"] = None


def _sync_core_config(settings: Settings, token: str, target_env: str, url: str) -> None:
    """Best-effort: write the deployed URL back to the core agent record."""
    if target_env != "prod":
        return
    try:
        manifest = config.read_manifest()
        if manifest is not None:
            patch = build_agent_patch(manifest, url)
            if patch:
                from dooers.cli.agent_store import HTTPCoreAgentStore

                HTTPCoreAgentStore(settings.core_url, token).update(manifest.agent_id, patch)
                typer.echo("Synced agent config to core.")
    except Exception as e:  # noqa: BLE001 — non-fatal: agent is live, sync is best-effort
        typer.echo(f"Warning: could not sync agent config: {e}", err=True)
