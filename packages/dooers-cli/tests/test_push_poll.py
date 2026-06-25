"""poll_until_terminal: phase callbacks, transient retry, timeout — no real waiting."""

from collections.abc import Iterator

import pytest
from dooers.protocol.push import BuildStatus, BuildStatusResponse

from dooers.cli.push import PushTimeout, poll_until_terminal
from dooers.cli.push_client import PushTransientError


def _status(status: BuildStatus, phase: str | None = None, **kw: object) -> BuildStatusResponse:
    return BuildStatusResponse(
        build_id="build-1", agent_id="ag-1", status=status, phase=phase, **kw
    )


class _FakeClient:
    """Yields a scripted sequence of get_build_status outcomes.

    Each item is either a BuildStatusResponse to return or an Exception to raise.
    """

    def __init__(self, script: list[object]) -> None:
        self._it: Iterator[object] = iter(script)
        self.calls = 0

    def get_build_status(self, build_id: str) -> BuildStatusResponse:
        self.calls += 1
        item = next(self._it)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, BuildStatusResponse)
        return item


def _counter_now() -> "callable":  # type: ignore[valid-type]
    """A monotonic clock that advances 1.0 per call, starting at 0.0."""
    state = {"t": -1.0}

    def now() -> float:
        state["t"] += 1.0
        return state["t"]

    return now


def test_poll_reaches_succeeded() -> None:
    phases: list[str | None] = []
    client = _FakeClient(
        [
            _status(BuildStatus.building, "cloud_build"),
            _status(BuildStatus.deploying, "load_balancer"),
            _status(BuildStatus.succeeded, "done", url="https://x.run.app"),
        ]
    )
    result = poll_until_terminal(
        client,
        "build-1",
        sleep=lambda *_: None,
        now=_counter_now(),
        on_phase=phases.append,
    )
    assert result.status == BuildStatus.succeeded
    assert result.url == "https://x.run.app"
    # Phase callback fired for the non-terminal phases at least.
    assert "cloud_build" in phases
    assert "load_balancer" in phases


def test_poll_reaches_user_failure() -> None:
    client = _FakeClient(
        [
            _status(BuildStatus.building, "cloud_build"),
            _status(
                BuildStatus.failed,
                "cloud_build",
                error="docker build failed",
                failed_step="build image",
                error_class="user",
            ),
        ]
    )
    result = poll_until_terminal(
        client, "build-1", sleep=lambda *_: None, now=_counter_now()
    )
    assert result.status == BuildStatus.failed
    assert result.error_class == "user"


def test_poll_retries_transient_error() -> None:
    client = _FakeClient(
        [
            _status(BuildStatus.building, "cloud_build"),
            PushTransientError("upstream 503"),
            _status(BuildStatus.succeeded, "done", url="https://x.run.app"),
        ]
    )
    result = poll_until_terminal(
        client, "build-1", sleep=lambda *_: None, now=_counter_now()
    )
    assert result.status == BuildStatus.succeeded
    assert client.calls == 3  # the transient one did not abort the loop


def test_poll_times_out() -> None:
    # now() advances 1.0 per call; with timeout_s=2 the loop must give up.
    client = _FakeClient([_status(BuildStatus.building, "cloud_build")] * 50)
    with pytest.raises(PushTimeout):
        poll_until_terminal(
            client,
            "build-1",
            sleep=lambda *_: None,
            now=_counter_now(),
            timeout_s=2,
        )


def test_poll_bounded_transient_retries_eventually_raise() -> None:
    # A flood of transient errors must not loop forever; it surfaces as transient.
    client = _FakeClient([PushTransientError("503")] * 1000)
    with pytest.raises(PushTransientError):
        poll_until_terminal(
            client, "build-1", sleep=lambda *_: None, now=_counter_now()
        )
