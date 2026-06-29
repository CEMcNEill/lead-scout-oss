"""One-time Gmail consent for the headless service.

Run once per rep:  uv run python -m engine.gmail_login

Opens Google's consent screen, captures the redirect on localhost, and stores
the Gmail refresh token in the macOS Keychain. After this the service drafts and
reads sent mail as the rep without an interactive step. The refresh token never
touches .env.
"""

from __future__ import annotations

import os

from engine.env import load_dotenv
from engine.gmail_api import build_gmail_token_provider_from_env
from engine.sf_login import run_login


def main() -> int:
    load_dotenv()
    provider = build_gmail_token_provider_from_env()
    redirect_uri = os.environ.get("GMAIL_REDIRECT_URI", "http://localhost:8765/callback")
    run_login(provider, redirect_uri, label="Gmail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
