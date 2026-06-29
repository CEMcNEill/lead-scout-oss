"""Salesforce OAuth core tests, with fake transport + in-memory secret store.

Covers the one-time code exchange storing the refresh token, silent refresh,
caching, the missing-token error, and authorize-URL shape. No network, no
Keychain.
"""

import urllib.parse

import pytest

from engine.sf_auth import (
    AccessToken,
    InMemorySecretStore,
    MissingRefreshToken,
    SalesforceAuth,
)


class FakeTransport:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        self._n = 0

    def post_form(self, url: str, data: dict) -> dict:
        self.posts.append((url, data))
        self._n += 1
        if data["grant_type"] == "authorization_code":
            return {"access_token": "AT0", "refresh_token": "RT1",
                    "instance_url": "https://na1.salesforce.com"}
        if data["grant_type"] == "refresh_token":
            return {"access_token": f"AT{self._n}", "instance_url": "https://na1.salesforce.com"}
        raise AssertionError(data)

    def get_json(self, url: str, headers: dict) -> dict:
        return {}


def _auth(store=None, transport=None) -> SalesforceAuth:
    return SalesforceAuth(
        client_id="cid", client_secret="csec", redirect_uri="http://localhost:8765/callback",
        sf_username="chris@posthog.com", secret_store=store or InMemorySecretStore(),
        transport=transport or FakeTransport(),
    )


def test_authorize_url_has_code_flow_and_scopes():
    auth = _auth()
    url = auth.authorize_url(state="xyz")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["cid"]
    assert "refresh_token" in q["scope"][0] and "api" in q["scope"][0]
    assert q["state"] == ["xyz"]


def test_complete_login_stores_refresh_token():
    store = InMemorySecretStore()
    transport = FakeTransport()
    auth = _auth(store, transport)
    token = auth.complete_login("authcode")
    assert token == AccessToken("AT0", "https://na1.salesforce.com")
    assert store.get("chris@posthog.com") == "RT1"


def test_access_token_refreshes_and_caches():
    store = InMemorySecretStore()
    store.set("chris@posthog.com", "RT1")
    transport = FakeTransport()
    auth = _auth(store, transport)

    first = auth.access_token()
    assert first.access_token == "AT1"
    # cached: no new POST on the second call
    again = auth.access_token()
    assert again.access_token == "AT1"
    assert len(transport.posts) == 1
    # force refresh issues a new token
    forced = auth.access_token(force_refresh=True)
    assert forced.access_token == "AT2"


def test_missing_refresh_token_raises():
    auth = _auth()  # empty store
    with pytest.raises(MissingRefreshToken):
        auth.access_token()


def test_logout_clears_token():
    store = InMemorySecretStore()
    store.set("chris@posthog.com", "RT1")
    auth = _auth(store)
    auth.access_token()
    auth.logout()
    assert store.get("chris@posthog.com") is None
    with pytest.raises(MissingRefreshToken):
        auth.access_token()
