"""Tests for dooers.yaml read/write."""

from pathlib import Path

import pytest
from dooers.protocol import PROTOCOL_VERSION
from dooers.protocol.agents import AgentManifest

from dooers.cli.config import MANIFEST_FILENAME, read_manifest, write_manifest


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
    yaml_content = (
        "protocol_version: '2'\n"
        "agent_id: 550e8400-e29b-41d4-a716-446655440002\n"
        "name: x\n"
        "organization_id: org_1\n"
        "bogus: nope\n"
    )
    (tmp_path / MANIFEST_FILENAME).write_text(yaml_content)
    with pytest.raises(Exception):  # pydantic ValidationError
        read_manifest(directory=tmp_path)
