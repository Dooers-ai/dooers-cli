# packages/dooers-cli/tests/test_core_client.py
import httpx
import pytest
import respx

from dooers.core_client import CoreClient, CoreClientError

BASE = "https://core.test"


@respx.mock
def test_send_otp():
    r = respx.post(f"{BASE}/api/v2/auth/email-otp/send-verification-otp").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {}}))
    CoreClient(BASE).send_otp("a@b.c")
    assert r.called


@respx.mock
def test_verify_otp_reads_header():
    respx.post(f"{BASE}/api/v2/auth/sign-in/email-otp").mock(
        return_value=httpx.Response(
            200, headers={"set-auth-token": "TKN"}, json={"success": True, "data": {}}
        )
    )
    token, exp = CoreClient(BASE).verify_otp("a@b.c", "123456")
    assert token == "TKN" and exp > 0


@respx.mock
def test_me():
    respx.get(f"{BASE}/api/v2/identity/me").mock(
        return_value=httpx.Response(
            200, json={"success": True, "data": {"id": "u1", "email": "a@b.c"}}
        )
    )
    me = CoreClient(BASE, token="t").me()
    assert me.user_id == "u1" and me.email == "a@b.c"


@respx.mock
def test_list_organizations():
    respx.get(f"{BASE}/api/v2/organizations").mock(
        return_value=httpx.Response(
            200, json={"success": True, "data": [{"organizationId": "o1", "name": "Org"}]}
        )
    )
    orgs = CoreClient(BASE, token="t").list_organizations()
    assert orgs[0]["organizationId"] == "o1"


@respx.mock
def test_error_envelope_surfaces_message():
    respx.get(f"{BASE}/api/v2/identity/me").mock(
        return_value=httpx.Response(401, json={"success": False, "error": {"message": "nope"}}))
    with pytest.raises(CoreClientError, match="nope"):
        CoreClient(BASE, token="t").me()
