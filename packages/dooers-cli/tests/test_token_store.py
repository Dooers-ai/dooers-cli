"""Tests for token persistence and expiry."""

import time
from pathlib import Path

from dooers.token_store import TokenStore, is_token_expired


def test_roundtrip_with_expiry(tmp_path: Path):
    p = tmp_path / "token"
    s = TokenStore(path=p)
    s.save("abc", expires_at=int(time.time()) + 3600)
    assert s.load() == "abc"
    assert not is_token_expired("abc", store=s)


def test_expired(tmp_path: Path):
    p = tmp_path / "token"
    s = TokenStore(path=p)
    s.save("abc", expires_at=int(time.time()) - 1)
    assert is_token_expired("abc", store=s)


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    store = TokenStore(path=tmp_path / "missing")
    assert store.load() is None


def test_clear_removes_file(tmp_path: Path) -> None:
    p = tmp_path / "token"
    store = TokenStore(path=p)
    store.save("x", expires_at=int(time.time()) + 3600)
    assert p.exists()
    store.clear()
    assert not p.exists()


def test_save_uses_0600_permissions(tmp_path: Path) -> None:
    p = tmp_path / "token"
    store = TokenStore(path=p)
    store.save("x", expires_at=int(time.time()) + 3600)
    mode = p.stat().st_mode & 0o777
    assert mode == 0o600


def test_no_token_is_expired(tmp_path: Path) -> None:
    s = TokenStore(path=tmp_path / "missing")
    assert is_token_expired(None, store=s)
