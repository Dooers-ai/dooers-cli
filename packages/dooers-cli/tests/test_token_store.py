"""Tests for token persistence and JWT expiry parsing."""

import base64
import json
import time
from pathlib import Path

import pytest

from dooers.token_store import TokenStore, is_token_expired


def _make_jwt(exp_offset_s: int) -> str:
    """Forge a JWT-shaped string with a given exp offset from now."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = {"exp": int(time.time()) + exp_offset_s}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{payload_b64}.sig"


def test_is_token_expired_returns_true_for_past_exp() -> None:
    assert is_token_expired(_make_jwt(-60)) is True


def test_is_token_expired_returns_false_for_future_exp() -> None:
    assert is_token_expired(_make_jwt(3600)) is False


def test_is_token_expired_returns_true_for_malformed() -> None:
    assert is_token_expired("not-a-jwt") is True
    assert is_token_expired("") is True


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    store = TokenStore(path=tmp_path / "token")
    store.save("abc123")
    assert store.load() == "abc123"


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    store = TokenStore(path=tmp_path / "missing")
    assert store.load() is None


def test_clear_removes_file(tmp_path: Path) -> None:
    p = tmp_path / "token"
    store = TokenStore(path=p)
    store.save("x")
    assert p.exists()
    store.clear()
    assert not p.exists()


def test_save_uses_0600_permissions(tmp_path: Path) -> None:
    p = tmp_path / "token"
    store = TokenStore(path=p)
    store.save("x")
    # mask off file type bits; require read+write for owner only
    mode = p.stat().st_mode & 0o777
    assert mode == 0o600
