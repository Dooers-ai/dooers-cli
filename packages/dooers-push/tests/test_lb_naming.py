"""Tests for LB naming helpers — pure functions."""

import pytest

from dooers_push.gcp.loadbalancer import (
    bs_name,
    host_for,
    neg_name,
    path_matcher_name,
    safe_agent_id,
)


def test_safe_agent_id_lowercases_and_replaces_underscores() -> None:
    assert safe_agent_id("ag_7q4r") == "ag-7q4r"
    assert safe_agent_id("AG_7Q4R") == "ag-7q4r"
    assert safe_agent_id("ag-already-safe") == "ag-already-safe"


def test_safe_agent_id_rejects_whitespace() -> None:
    with pytest.raises(ValueError):
        safe_agent_id(" ag_7q4r ")
    with pytest.raises(ValueError):
        safe_agent_id("ag 7q4r")


def test_safe_agent_id_rejects_empty() -> None:
    with pytest.raises(ValueError):
        safe_agent_id("")


def test_host_for_prod_drops_env_suffix() -> None:
    assert host_for("ag_7q4r", "prod", "agents.dooers.ai") == "ag-7q4r.agents.dooers.ai"


def test_host_for_non_prod_keeps_env_suffix() -> None:
    assert host_for("ag_7q4r", "dev", "agents.dooers.ai") == "ag-7q4r-dev.agents.dooers.ai"
    assert host_for("ag_7q4r", "stg", "agents.dooers.ai") == "ag-7q4r-stg.agents.dooers.ai"


def test_neg_name_keeps_env_in_all_envs() -> None:
    # Internal resource names are symmetric (env always present) for easy filtering.
    assert neg_name("ag_7q4r", "prod") == "agent-ag-7q4r-prod-neg"
    assert neg_name("ag_7q4r", "dev") == "agent-ag-7q4r-dev-neg"


def test_bs_name_keeps_env_in_all_envs() -> None:
    assert bs_name("ag_7q4r", "prod") == "agent-ag-7q4r-prod-bs"
    assert bs_name("ag_7q4r", "dev") == "agent-ag-7q4r-dev-bs"


def test_path_matcher_name() -> None:
    assert path_matcher_name("ag_7q4r", "prod") == "agent-ag-7q4r-prod-pm"
    assert path_matcher_name("ag_7q4r", "dev") == "agent-ag-7q4r-dev-pm"
