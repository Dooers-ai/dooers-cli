# packages/dooers-push/tests/test_auth.py
import httpx
import pytest
import respx
from fastapi import HTTPException
from starlette.requests import Request

from dooers_push.auth import verify_session
from dooers_push.settings import Settings


def _req(token: str | None) -> Request:
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    return Request({"type": "http", "headers": headers})


def _settings() -> Settings:
    import os

    os.environ.update(GCP_PROJECT_ID="p", BUCKET_NAME="b")
    return Settings.from_env()


@pytest.mark.asyncio
@respx.mock
async def test_verify_session_ok() -> None:
    s = _settings()
    respx.get(f"{s.core_api_url}/api/v2/identity/me").mock(
        return_value=httpx.Response(
            200, json={"success": True, "data": {"id": "u1", "email": "a@b.c"}}
        )
    )
    sess = await verify_session(_req("tok"), s)
    assert sess.user_id == "u1" and sess.email == "a@b.c"


@pytest.mark.asyncio
@respx.mock
async def test_verify_session_401() -> None:
    s = _settings()
    respx.get(f"{s.core_api_url}/api/v2/identity/me").mock(
        return_value=httpx.Response(401, json={})
    )
    with pytest.raises(HTTPException) as e:
        await verify_session(_req("tok"), s)
    assert e.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_session_missing_bearer() -> None:
    with pytest.raises(HTTPException) as e:
        await verify_session(_req(None), _settings())
    assert e.value.status_code == 401
