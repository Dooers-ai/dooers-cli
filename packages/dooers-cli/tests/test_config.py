"""Tests for dooers.yaml read/write."""

from pathlib import Path

import pytest

from dooers.config import MANIFEST_FILENAME, read_manifest, write_manifest
from dooers_protocol import PROTOCOL_VERSION
from dooers_protocol.agents import AgentManifest


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    m = AgentManifest(
        protocol_version=PROTOCOL_VERSION,
        agent_id="550e8400-e29b-41d4-a716-446655440000",
        name="test",
        organization_id="org_1",
    )
    write_manifest(m, directory=tmp_path)
    loaded = read_manifest(directory=tmp_path)
    assert loaded == m


def test_read_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_manifest(directory=tmp_path) is None


def test_write_creates_named_file(tmp_path: Path) -> None:
    m = AgentManifest(
        protocol_version=PROTOCOL_VERSION,
        agent_id="550e8400-e29b-41d4-a716-446655440001",
        name="x",
        organization_id="org_1",
    )
    p = write_manifest(m, directory=tmp_path)
    assert p == tmp_path / MANIFEST_FILENAME
    assert p.exists()


def test_read_rejects_unknown_fields(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text(
        "protocol_version: '2'\nagent_id: 550e8400-e29b-41d4-a716-446655440002\nname: x\norganization_id: org_1\nbogus: nope\n"
    )
    with pytest.raises(Exception):  # pydantic ValidationError
        read_manifest(directory=tmp_path)
