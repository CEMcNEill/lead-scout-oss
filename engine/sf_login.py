"""One-time Salesforce login (web-server / authorization-code flow).

Run once per rep:  uv run python -m engine.sf_login

Opens the External Client App consent screen in a browser, captures the redirect
on a localhost server, exchanges the code for tokens, and stores the refresh
token in the macOS Keychain. After this, the engine refreshes silently and never
needs an interactive step again. The refresh token never touches .env or disk.
"""

from __future__ import annotations

import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from engine.salesforce import build_auth_from_env
from engine.sf_auth import SalesforceAuth


class _CallbackHandler(BaseHTTPRequestHandler):
    captured: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            _CallbackHandler.captured = {
                "code": params["code"][0],
                "state": params.get("state", [""])[0],
            }
            body = b"Salesforce login complete. You can close this tab."
        else:
            body = b"No authorization code received."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence the default stderr logging
        pass


def run_login(auth, redirect_uri: str, *, label: str = "Salesforce") -> str:
    """Drive an interactive authorization-code flow for any auth object exposing
    authorize_url(state) and complete_login(code). Reused for Salesforce and
    Gmail. Returns the authenticated identifier if the token exposes one."""
    parsed = urllib.parse.urlparse(redirect_uri)
    host, port = parsed.hostname or "localhost", parsed.port or 80
    state = secrets.token_urlsafe(16)

    url = auth.authorize_url(state=state)
    print(f"Opening {label} consent in your browser. If it does not open, visit:")
    print(f"  {url}\n")
    webbrowser.open(url)

    server = HTTPServer((host, port), _CallbackHandler)
    print(f"Waiting for the redirect on {redirect_uri} ...")
    server.handle_request()  # serve exactly one request: the OAuth callback
    server.server_close()

    captured = _CallbackHandler.captured
    if not captured.get("code"):
        raise SystemExit("Login failed: no authorization code was captured.")
    if captured.get("state") != state:
        raise SystemExit("Login failed: OAuth state mismatch (possible CSRF).")

    token = auth.complete_login(captured["code"])
    print(f"\n{label} login complete. Refresh token stored in the macOS Keychain.")
    if getattr(token, "instance_url", None):
        print(f"Instance: {token.instance_url}")
    if getattr(token, "user_id", None):
        print(f"Identity: {token.user_id}")
    return getattr(token, "user_id", "") or ""


def main() -> int:
    import os

    from engine.env import load_dotenv

    load_dotenv()
    auth = build_auth_from_env()
    redirect_uri = os.environ.get("SF_REDIRECT_URI", "http://localhost:8765/callback")
    run_login(auth, redirect_uri)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
