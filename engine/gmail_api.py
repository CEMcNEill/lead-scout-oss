"""Headless Gmail client over the Gmail REST API (Google OAuth refresh token).

The agent-runtime Gmail client posts via the claude.ai MCP. For a standalone
service, this uses a Google Cloud OAuth app: the rep consents once
(engine/gmail_login.py), the refresh token is stored in the macOS Keychain, and
the service then creates drafts and reads sent items as that rep, headless,
refreshing silently. Implements the GmailClient seam, so the staging sink and the
slow loop are unchanged. Drafts are created, never sent.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request
from typing import Any, Callable

from engine.gmail import GmailMessage
from engine.sf_auth import KeychainSecretStore, SecretStore, UrllibTransport

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_SCOPE = "https://www.googleapis.com/auth/gmail.modify"  # drafts + read sent
_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_KEYCHAIN_SERVICE = "lead-agent-gmail"


class GoogleTokenProvider:
    """Holds the rep's Gmail OAuth: one-time consent, then silent refresh. The
    refresh token lives in the Keychain, never in .env."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        account: str,
        secret_store: SecretStore | None = None,
        transport=None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._account = account
        self._store = secret_store or KeychainSecretStore(_KEYCHAIN_SERVICE)
        self._http = transport or UrllibTransport()
        self._cached: str | None = None

    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": _SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"

    def complete_login(self, code: str) -> "_GmailIdentity":
        resp = self._http.post_form(
            _TOKEN_URL,
            {
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        refresh = resp.get("refresh_token")
        if not refresh:
            raise RuntimeError("Google token exchange returned no refresh_token")
        self._store.set(self._account, refresh)
        self._cached = resp.get("access_token")
        return _GmailIdentity(self._account)

    def access_token(self, *, force_refresh: bool = False) -> str:
        if self._cached and not force_refresh:
            return self._cached
        refresh = self._store.get(self._account)
        if not refresh:
            raise RuntimeError(
                f"no stored Gmail refresh token for {self._account!r}; run the one-time login"
            )
        resp = self._http.post_form(
            _TOKEN_URL,
            {
                "refresh_token": refresh,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
            },
        )
        self._cached = resp["access_token"]
        return self._cached


class _GmailIdentity:
    """Tiny holder so the shared login CLI can print something."""

    def __init__(self, account: str) -> None:
        self.account = account
        self.instance_url = None
        self.user_id = account


# transport: (http_method, url, json_body|None) -> parsed json. Injectable.
GmailHttp = Callable[[str, str, dict[str, Any] | None], dict[str, Any]]


def default_gmail_http(token_provider: GoogleTokenProvider) -> GmailHttp:
    def call(method: str, url: str, body: dict[str, Any] | None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token_provider.access_token()}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:  # noqa: S310 - fixed Gmail endpoint
            return json.loads(resp.read().decode())

    return call


def _mime(to: list[str], subject: str, body: str) -> str:
    msg = (
        f"To: {', '.join(to)}\r\n"
        f"Subject: {subject}\r\n"
        "Content-Type: text/plain; charset=UTF-8\r\n\r\n"
        f"{body}"
    )
    return base64.urlsafe_b64encode(msg.encode()).decode()


def _header(headers: list[dict], name: str) -> str:
    return next((h["value"] for h in headers if h.get("name", "").lower() == name.lower()), "")


def _decode_part(part: dict) -> str:
    data = (part.get("body") or {}).get("data")
    if data:
        return base64.urlsafe_b64decode(data.encode()).decode(errors="replace")
    for sub in part.get("parts", []) or []:
        if sub.get("mimeType") == "text/plain":
            text = _decode_part(sub)
            if text:
                return text
    return ""


def _parse_message(full: dict) -> GmailMessage:
    payload = full.get("payload", {})
    headers = payload.get("headers", [])
    return GmailMessage(
        id=full.get("id", ""),
        thread_id=full.get("threadId", ""),
        subject=_header(headers, "Subject"),
        to=_header(headers, "To"),
        body=_decode_part(payload),
        date=_header(headers, "Date"),
        from_addr=_header(headers, "From"),
    )


class GmailApiClient:
    """GmailClient backed by the Gmail REST API. Headless."""

    def __init__(self, http: GmailHttp) -> None:
        self._http = http

    def create_draft(self, *, to: list[str], subject: str, body: str) -> str:
        resp = self._http(
            "POST", f"{_BASE}/drafts", {"message": {"raw": _mime(to, subject, body)}}
        )
        return resp.get("id") or (resp.get("message") or {}).get("id", "")

    def find_sent(self, query: str) -> list[GmailMessage]:
        q = urllib.parse.quote(f"in:sent {query}".strip())
        listing = self._http("GET", f"{_BASE}/messages?q={q}&maxResults=10", None)
        out: list[GmailMessage] = []
        for m in listing.get("messages", []):
            full = self._http("GET", f"{_BASE}/messages/{m['id']}?format=full", None)
            out.append(_parse_message(full))
        return out

    def get_thread(self, thread_id: str) -> list[GmailMessage]:
        thread = self._http("GET", f"{_BASE}/threads/{thread_id}?format=full", None)
        return [_parse_message(m) for m in thread.get("messages", [])]


def build_gmail_token_provider_from_env() -> GoogleTokenProvider:
    def required(name: str) -> str:
        v = os.environ.get(name)
        if not v:
            raise RuntimeError(f"{name} is not set; required for the Gmail integration")
        return v

    return GoogleTokenProvider(
        client_id=required("GMAIL_CLIENT_ID"),
        client_secret=required("GMAIL_CLIENT_SECRET"),
        redirect_uri=os.environ.get("GMAIL_REDIRECT_URI", "http://localhost:8765/callback"),
        account=required("GMAIL_ACCOUNT"),
    )


def build_gmail_client_from_env() -> GmailApiClient:
    return GmailApiClient(default_gmail_http(build_gmail_token_provider_from_env()))
