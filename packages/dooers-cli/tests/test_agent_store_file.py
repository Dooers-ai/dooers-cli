"""Tests for the local-file shim used when core's /agents endpoints are unavailable."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from dooers.agent_store import FileShimAgentStore
from dooers_protocol.agents import CreateAgentRequest


def test_create_then_list_returns_record(tmp_path: Path) -> None:
    store = FileShimAgentStore(path=tmp_path / "agents.json", owner_user_id="u_1")
    record = store.create(CreateAgentRequest(name="my-agent"))

    assert record.name == "my-agent"
    assert record.owner_user_id == "u_1"
    assert record.agent_id.startswith("ag_")

    listed = store.list()
    assert len(listed) == 1
    assert listed[0].agent_id == record.agent_id


def test_create_assigns_unique_ids(tmp_path: Path) -> None:
    store = FileShimAgentStore(path=tmp_path / "agents.json", owner_user_id="u_1")
    a = store.create(CreateAgentRequest(name="a"))
    b = store.create(CreateAgentRequest(name="b"))
    assert a.agent_id != b.agent_id


def test_get_returns_match(tmp_path: Path) -> None:
    store = FileShimAgentStore(path=tmp_path / "agents.json", owner_user_id="u_1")
    a = store.create(CreateAgentRequest(name="a"))
    fetched = store.get(a.agent_id)
    assert fetched == a


def test_get_missing_raises(tmp_path: Path) -> None:
    store = FileShimAgentStore(path=tmp_path / "agents.json", owner_user_id="u_1")
    with pytest.raises(KeyError):
        store.get("ag_missing")


def test_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "agents.json"
    FileShimAgentStore(path=path, owner_user_id="u_1").create(
        CreateAgentRequest(name="persist")
    )
    listed = FileShimAgentStore(path=path, owner_user_id="u_1").list()
    assert len(listed) == 1
    assert listed[0].name == "persist"
