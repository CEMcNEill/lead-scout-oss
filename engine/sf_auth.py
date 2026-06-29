"""Salesforce auth: act-as-rep OAuth via an External Client App.

The engine authenticates as each rep, not as a shared integration user, so
Salesforce's own sharing rules scope each rep's engine to that rep's leads and
attribution stays with the rep. Locally this is the web-server
(authorization-code) flow: the rep approves once in a browser, the refresh token
is stored in the macOS Keychain, and the engine refreshes silently afterward.

Use an External Client App, not a Connected App (Salesforce is disabling new
Connected Apps). Scopes are minimal: `api` plus `refresh_token`/`offline_access`.

The OAuth core (URL building, code exchange, refresh) is pure and testable behind
two seams: a SecretStore (Keychain in production, in-memory in tests) and an
HttpTransport (urllib in production, a fake in tests). The interactive
browser/redirect capture is thin I/O layered on top.
"""

from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

_SCOPES = "api refresh_token offline_access"
_KEYCHAIN_SERVICE = "lead-agent-sf"


# --- seams ----------------------------------------------------------------


class SecretStore(Protocol):
    def get(self, account: str) -> str | None: ...
    def set(self, account: str, secret: str) -> None: ...
    def delete(self, account: str) -> None: ...


class HttpError(Exception):
    """A non-2xx HTTP response. Carries the status so callers can branch (e.g.
    refresh-and-retry on 401)."""

    def __init__(self, status: int, body: str, url: str) -> None:
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")


class HttpTransport(Protocol):
    def post_form(self, url: str, data: dict[str, str]) -> dict: ...
    def get_json(self, url: str, headers: dict[str, str]) -> dict: ...


# --- production seams -----------------------------------------------------


class KeychainSecretStore:
    """Stores secrets in the macOS login Keychain via the `security` CLI."""

    def __init__(self, service: str = _KEYCHAIN_SERVICE) -> None:
        self._service = service

    def get(self, account: str) -> str | None:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", self._service, "-a", account, "-w"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    def set(self, account: str, secret: str) -> None:
        subprocess.run(
            ["security", "add-generic-password", "-U", "-s", self._service,
             "-a", account, "-w", secret],
            check=True, capture_output=True, text=True,
        )

    def delete(self, account: str) -> None:
        subprocess.run(
            ["security", "delete-generic-password", "-s", self._service, "-a", account],
            capture_output=True, text=True,
        )


class UrllibTransport:
    def post_form(self, url: str, data: dict[str, str]) -> dict:
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        return self._send(req, url)

    def get_json(self, url: str, headers: dict[str, str]) -> dict:
        req = urllib.request.Request(url, method="GET")
        for k, v in headers.items():
            req.add_header(k, v)
        return self._send(req, url)

    @staticmethod
    def _send(req: urllib.request.Request, url: str) -> dict:
        import urllib.error

        try:
            with urllib.request.urlopen(req) as resp:  # noqa: S310 - fixed SF endpoints
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:  # surface status for refresh-retry
            raise HttpError(exc.code, exc.read().decode(errors="replace"), url) from exc


# --- in-memory fakes (tests) ----------------------------------------------


class InMemorySecretStore:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self._store.get(account)

    def set(self, account: str, secret: str) -> None:
        self._store[account] = secret

    def delete(self, account: str) -> None:
        self._store.pop(account, None)


# --- tokens ---------------------------------------------------------------


@dataclass
class AccessToken:
    access_token: str
    instance_url: str
    identity_url: str | None = None

    @property
    def user_id(self) -> str | None:
        """The Salesforce user id, parsed from the identity URL
        (.../id/<orgId>/<userId>). This is the authenticated rep under act-as-rep."""
        if not self.identity_url:
            return None
        return self.identity_url.rstrip("/").rsplit("/", 1)[-1]


# --- the auth flow --------------------------------------------------------


class MissingRefreshToken(Exception):
    """No stored refresh token: the rep must complete the one-time browser login."""


class SalesforceAuth:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        sf_username: str,
        login_url: str = "https://login.salesforce.com",
        secret_store: SecretStore | None = None,
        transport: HttpTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._username = sf_username
        self._login_url = login_url.rstrip("/")
        self._store = secret_store or KeychainSecretStore()
        self._http = transport or UrllibTransport()
        self._cached: AccessToken | None = None

    @property
    def _token_endpoint(self) -> str:
        return f"{self._login_url}/services/oauth2/token"

    def authorize_url(self, state: str) -> str:
        """The URL the rep visits once to approve. Opening it and capturing the
        redirected code is the only interactive step."""
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": _SCOPES,
            "state": state,
        }
        return f"{self._login_url}/services/oauth2/authorize?{urllib.parse.urlencode(params)}"

    def complete_login(self, code: str) -> AccessToken:
        """Exchange the authorization code for tokens and persist the refresh
        token in the secret store. Run once per rep."""
        resp = self._http.post_form(
            self._token_endpoint,
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
            },
        )
        refresh = resp.get("refresh_token")
        if not refresh:
            raise RuntimeError("authorization-code exchange returned no refresh_token")
        self._store.set(self._username, refresh)
        token = AccessToken(resp["access_token"], resp["instance_url"], resp.get("id"))
        self._cached = token
        return token

    def access_token(self, *, force_refresh: bool = False) -> AccessToken:
        """Return a usable access token, refreshing silently from the stored
        refresh token. Cached for the process lifetime."""
        if self._cached is not None and not force_refresh:
            return self._cached
        refresh = self._store.get(self._username)
        if not refresh:
            raise MissingRefreshToken(
                f"no stored refresh token for {self._username!r}; run the one-time login"
            )
        resp = self._http.post_form(
            self._token_endpoint,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        self._cached = AccessToken(resp["access_token"], resp["instance_url"], resp.get("id"))
        return self._cached

    def logout(self) -> None:
        self._store.delete(self._username)
        self._cached = None
