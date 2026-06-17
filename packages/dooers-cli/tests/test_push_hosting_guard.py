"""`dooers push` honors the local manifest's hosting flag; scaffold defaults true."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
import yaml
from dooers_protocol import PROTOCOL_VERSION
from dooers_protocol.agents import AgentManifest

from dooers import config
from dooers.push import push


def _write_manifest(directory: Path, *, hosting: bool) -> None:
    config.write_manifest(
        AgentManifest(
            protocol_version=PROTOCOL_VERSION,
            agent_id="a1",
            name="A",
            organization_id="o1",
            hosting=hosting,
        ),
        directory=directory,
    )


def test_push_aborts_when_hosting_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_manifest(tmp_path, hosting=False)
    monkeypatch.chdir(tmp_path)
    # env="dev" short-circuits before ctx.obj is used, so a minimal ctx is enough.
    ctx = SimpleNamespace(obj=None)
    with pytest.raises(typer.Exit) as exc:
        push(ctx, agent_id=None, tag="latest", env="dev")  # type: ignore[arg-type]
    assert exc.value.exit_code == 1
    # Must abort *at the hosting guard*, not later at the auth/archive step.
    assert "hosting is disabled in dooers.yaml" in capsys.readouterr().err


def test_scaffolded_manifest_has_hosting_true(tmp_path: Path) -> None:
    _write_manifest(tmp_path, hosting=True)
    raw = yaml.safe_load((tmp_path / config.MANIFEST_FILENAME).read_text())
    assert raw["hosting"] is True
