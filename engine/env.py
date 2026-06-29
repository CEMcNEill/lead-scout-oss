"""Minimal .env loader (stdlib only).

uv run does not load .env by default, so the entrypoints call this to populate
os.environ from the repo's .env before reading config. Existing environment
variables always win, so you can still override per-invocation on the command
line. Secrets stay in .env (gitignored); this never logs values.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: str | Path | None = None) -> None:
    """Load KEY=VALUE lines from .env into os.environ without overriding values
    already set. Tolerates comments, blank lines, `export ` prefixes, and quotes."""
    env_path = Path(path) if path is not None else _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
