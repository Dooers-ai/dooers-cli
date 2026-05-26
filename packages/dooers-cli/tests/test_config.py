"""Tests for dooers.yaml read/write."""

from pathlib import Path

import pytest

from dooers.config import MANIFEST_FILENAME, read_manifest, write_manifest
from dooers_protocol import PROTOCOL_VERSION
from dooers_protocol.agents import AgentManifest


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    m = AgentManifest(
        protocol_version=PROTOCOL_VERSION,
        agent_id="ag_8h2k",
        name="test",
        runtime="docker",
        env_required=["FOO", "BAR"],
    )
    write_manifest(m, directory=tmp_path)
    loaded = read_manifest(directory=tmp_path)
    assert loaded == m


def test_read_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_manifest(directory=tmp_path) is None


def test_write_creates_named_file(tmp_path: Path) -> None:
    m = AgentManifest(
        protocol_version=PROTOCOL_VERSION,
        agent_id="ag_x",
        name="x",
    )
    p = write_manifest(m, directory=tmp_path)
    assert p == tmp_path / MANIFEST_FILENAME
    assert p.exists()


def test_read_rejects_unknown_fields(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        "protocol_version: '1'\nagent_id: ag_x\nname: x\nbogus: nope\n"
    )
    with pytest.raises(Exception):  # pydantic ValidationError
        read_manifest(directory=tmp_path)
