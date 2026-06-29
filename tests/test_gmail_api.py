"""Headless Gmail API client tests: draft MIME, sent parsing, OAuth refresh.
Fake HTTP, no live calls."""

import base64

import pytest

from engine.gmail_api import GmailApiClient, GoogleTokenProvider
from engine.sf_auth import InMemorySecretStore


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


class FakeGmailHttp:
    def __init__(self, list_resp=None, messages=None):
        self.calls = []
        self._list = list_resp or {"messages": []}
        self._messages = messages or {}

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        if method == "POST" and url.endswith("/drafts"):
            return {"id": "draft-123", "message": {"id": "msg-1"}}
        if "/messages/" in url:
            mid = url.split("/messages/")[1].split("?")[0]
            return self._messages[mid]
        if "/messages?" in url:
            return self._list
        raise AssertionError(f"unexpected {method} {url}")


def test_create_draft_builds_mime_and_returns_id():
    http = FakeGmailHttp()
    draft_id = GmailApiClient(http).create_draft(
        to=["dana@acme.com"], subject="PostHog at Acme", body="Saw your funnels."
    )
    assert draft_id == "draft-123"
    method, url, body = http.calls[0]
    raw = body["message"]["raw"]
    decoded = base64.urlsafe_b64decode(raw.encode()).decode()
    assert "To: dana@acme.com" in decoded
    assert "Subject: PostHog at Acme" in decoded
    assert decoded.endswith("Saw your funnels.")


def test_find_sent_parses_messages():
    http = FakeGmailHttp(
        list_resp={"messages": [{"id": "m1"}]},
        messages={"m1": {
            "id": "m1", "threadId": "t1",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "PostHog at Acme"},
                    {"name": "To", "value": "dana@acme.com"},
                    {"name": "Date", "value": "Mon, 01 Jun 2026 10:00:00 -0700"},
                ],
                "body": {"data": _b64("Saw your funnels, edited and sent.")},
            },
        }},
    )
    msgs = GmailApiClient(http).find_sent("to:dana@acme.com")
    assert len(msgs) == 1
    assert msgs[0].subject == "PostHog at Acme"
    assert msgs[0].to == "dana@acme.com"
    assert "edited and sent" in msgs[0].body
    # the query was scoped to sent
    assert "in%3Asent" in http.calls[0][1]


def test_find_sent_handles_multipart_body():
    http = FakeGmailHttp(
        list_resp={"messages": [{"id": "m2"}]},
        messages={"m2": {
            "id": "m2", "threadId": "t2",
            "payload": {
                "headers": [{"name": "Subject", "value": "Re: hi"}],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64("plain text body")}},
                    {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
                ],
            },
        }},
    )
    msgs = GmailApiClient(http).find_sent("")
    assert msgs[0].body == "plain text body"


# --- token provider ------------------------------------------------------


class FakeTransport:
    def __init__(self):
        self.posts = 0

    def post_form(self, url, data):
        self.posts += 1
        if data["grant_type"] == "authorization_code":
            return {"access_token": "AT0", "refresh_token": "RT1"}
        return {"access_token": f"AT{self.posts}"}


def _provider(store, transport):
    return GoogleTokenProvider(
        client_id="cid", client_secret="csec", redirect_uri="http://localhost:8765/callback",
        account="chris.m@posthog.com", secret_store=store, transport=transport,
    )


def test_consent_stores_refresh_token():
    store = InMemorySecretStore()
    _provider(store, FakeTransport()).complete_login("authcode")
    assert store.get("chris.m@posthog.com") == "RT1"


def test_access_token_refreshes_and_caches():
    store = InMemorySecretStore()
    store.set("chris.m@posthog.com", "RT1")
    transport = FakeTransport()
    provider = _provider(store, transport)
    assert provider.access_token() == "AT1"
    assert provider.access_token() == "AT1"  # cached
    assert transport.posts == 1


def test_missing_refresh_token_raises():
    with pytest.raises(RuntimeError):
        _provider(InMemorySecretStore(), FakeTransport()).access_token()


def test_authorize_url_requests_offline_consent():
    url = _provider(InMemorySecretStore(), FakeTransport()).authorize_url("xyz")
    assert "access_type=offline" in url
    assert "scope=" in url and "gmail.modify" in url
    assert "state=xyz" in url
