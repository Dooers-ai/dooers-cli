"""Tests for LB naming helpers — pure functions."""

import pytest

from dooers_push.gcp.loadbalancer import (
    SHARED_PATH_MATCHER,
    bs_name,
    neg_name,
    path_segment_for,
    safe_agent_id,
)


def test_safe_agent_id_lowercases_and_replaces_underscores() -> None:
    assert safe_agent_id("ag_7q4r") == "ag-7q4r"
    assert safe_agent_id("AG_7Q4R") == "ag-7q4r"
    assert safe_agent_id("ag-already-safe") == "ag-already-safe"


def test_safe_agent_id_rejects_whitespace_and_empty() -> None:
    with pytest.raises(ValueError):
        safe_agent_id(" ag_7q4r ")
    with pytest.raises(ValueError):
        safe_agent_id("")


def test_path_segment_for_prod_drops_env_suffix() -> None:
    assert path_segment_for("ag_7q4r", "prod") == "ag-7q4r"


def test_path_segment_for_non_prod_keeps_env_suffix() -> None:
    assert path_segment_for("ag_7q4r", "dev") == "ag-7q4r-dev"
    assert path_segment_for("ag_7q4r", "stg") == "ag-7q4r-stg"


def test_neg_and_bs_names_keep_env() -> None:
    assert neg_name("ag_7q4r", "prod") == "agent-ag-7q4r-prod-neg"
    assert bs_name("ag_7q4r", "dev") == "agent-ag-7q4r-dev-bs"


def test_shared_path_matcher_constant() -> None:
    assert SHARED_PATH_MATCHER == "agents-pm"


from dooers_push.gcp.cloudbuild import cloud_run_service_name  # noqa: E402

UUID = "550e8400-e29b-41d4-a716-446655440000"


def test_service_name_starts_with_letter_for_uuid() -> None:
    name = cloud_run_service_name(UUID, "prod")
    assert name == f"agent-{UUID}-prod"
    assert name[0].isalpha()  # Cloud Run names must start with a letter
    assert len(name) <= 63


def test_neg_targets_same_service_name() -> None:
    from dooers_push.gcp import loadbalancer as lb

    # NEG cloud_run_service must equal the deployed service name
    assert lb._cloud_run_service(UUID, "dev") == cloud_run_service_name(UUID, "dev")
