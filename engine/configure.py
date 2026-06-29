"""Config wizard: collect the .env a rep needs and write it.

Three entry points:
  (default)        interactive prompts, for a terminal.
  --from-json FILE non-interactive: merge the given {KEY: value} map and write .env
                   (used by the lead-scout-setup skill so Claude can drive it).
  --fields         print the field schema as JSON, so a caller knows what to ask.

In every mode .env is rendered from the committed .env.example, so keys stay
documented and unprompted keys keep sensible defaults. Precedence when writing:
example default < existing .env < forced (SOURCE/SF_AUTH) < your answer. Re-running
is safe; a blank/absent answer keeps the existing value. Secrets read without echo.

Run with `uv run python -m engine.configure`. Pure stdlib; writes only .env.
"""

from __future__ import annotations

import getpass
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# (key, label, secret, help). The default runtime is agent + MCP, so these core
# fields are all it needs; the headless integrations below are optional.
CORE_FIELDS = [
    ("ANTHROPIC_API_KEY", "Anthropic API key", True,
     "Engine model calls (separate from your Claude subscription). console.anthropic.com. Required."),
    ("SF_INSTANCE_URL", "Salesforce instance URL", False,
     "Used to hyperlink accounts in Slack, e.g. https://yourco.my.salesforce.com"),
    ("SF_TARGET_ORG", "Salesforce org (alias or username)", False,
     "Which org the sf CLI targets. Blank uses your default org."),
    ("SLACK_USER_ID", "Slack user id (DM target for review cards)", False,
     "Your own Slack user id; the review cards DM you here. Required."),
    ("REP_SIGNATURE", "Your name for the email sign-off", False,
     "Signs drafts, e.g. Chris."),
    ("REP_CALENDAR_URL", "Your booking link", False,
     "Used verbatim for any 'my calendar' CTA; the drafter never invents one. Blank omits links."),
]

HEADLESS_FIELDS = [
    ("GMAIL_CLIENT_ID", "Gmail OAuth client id", False,
     "From Google Cloud. Only for the no-Claude daemon."),
    ("GMAIL_CLIENT_SECRET", "Gmail OAuth client secret", True, ""),
    ("GMAIL_ACCOUNT", "Gmail address", False, ""),
    ("SLACK_BOT_TOKEN", "Slack bot token", True, "xoxb-... for the daemon's Slack posts."),
    ("ENRICHMENT_API_KEY", "Enrichment provider API key", True,
     "Person/company enrichment for the daemon (the agent path uses the Clay MCP)."),
]

REQUIRED = {"ANTHROPIC_API_KEY", "SLACK_USER_ID"}
FORCED = {"SOURCE": "salesforce", "SF_AUTH": "cli"}


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE pairs from a .env-style file; comments and blanks ignored."""
    vals: dict[str, str] = {}
    if not path.exists():
        return vals
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        vals[k.strip()] = v.strip()
    return vals


def example_defaults(example_text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in example_text.splitlines():
        m = re.match(r"^\s*([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def render_env(example_text: str, merged: dict[str, str]) -> str:
    """Rewrite each KEY= line in the example with merged[KEY], preserving all
    comments and ordering; append any keys the example does not mention."""
    out: list[str] = []
    seen: set[str] = set()
    for line in example_text.splitlines():
        m = re.match(r"^(\s*)([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m and m.group(2) in merged:
            key = m.group(2)
            out.append(f"{m.group(1)}{key}={merged[key]}")
            seen.add(key)
        else:
            out.append(line)
    extras = [k for k in merged if k not in seen]
    if extras:
        out += ["", "# --- additional ---"] + [f"{k}={merged[k]}" for k in extras]
    return "\n".join(out) + "\n"


def field_schema() -> dict:
    """The questions a caller should ask, plus the values always forced."""
    def shape(items, group):
        return [{"key": k, "label": label, "secret": secret, "help": h, "group": group}
                for (k, label, secret, h) in items]
    return {
        "forced": FORCED,
        "required": sorted(REQUIRED),
        "fields": shape(CORE_FIELDS, "core") + shape(HEADLESS_FIELDS, "headless"),
    }


def write_env(provided: dict[str, str], repo: Path | None = None) -> tuple[Path, list[str]]:
    """Merge example < existing .env < forced < provided, write .env, and return
    (path, still-missing-required)."""
    repo = repo or REPO_ROOT
    example_text = (repo / ".env.example").read_text()
    env_path = repo / ".env"
    current = read_env_file(env_path)
    clean = {k: v for k, v in provided.items() if v is not None}
    merged = {**example_defaults(example_text), **current, **FORCED, **clean}
    env_path.write_text(render_env(example_text, merged))
    missing = sorted(k for k in REQUIRED if not merged.get(k))
    return env_path, missing


def _report(env_path: Path, missing: list[str]) -> None:
    print(f"\nWrote {env_path}")
    if missing:
        print("WARNING: still missing required values: " + ", ".join(missing))
        print("Re-run `uv run python -m engine.configure` to fill them in.")
    else:
        print("Core config looks complete.")


# --- interactive ----------------------------------------------------------


def _ask(prompt: str, secret: bool) -> str:
    try:
        return (getpass.getpass(prompt) if secret else input(prompt)).strip()
    except EOFError:
        return ""


def prompt_field(label: str, secret: bool, helptext: str, current: str) -> str:
    shown = "[keep existing]" if (secret and current) else (f"[{current}]" if current else "[blank]")
    print(f"\n{label}")
    if helptext:
        print(f"  {helptext}")
    answer = _ask(f"  value {shown}: ", secret)
    return answer if answer else current


def _interactive() -> int:
    current = read_env_file(REPO_ROOT / ".env")
    print("lead-scout config wizard")
    print("Press Enter to keep the shown value; secrets are hidden as you type.")
    print("Default runtime is agent + MCP (Clay/Gmail/Slack via Claude Code).")

    collected: dict[str, str] = {}
    print("\n== Core (agent + MCP, the default path) ==")
    for key, label, secret, helptext in CORE_FIELDS:
        collected[key] = prompt_field(label, secret, helptext, current.get(key, ""))

    print("\n== Headless daemon (optional; only if you run without Claude) ==")
    if _ask("  Configure the headless daemon integrations now? [y/N]: ", False).lower() in ("y", "yes"):
        for key, label, secret, helptext in HEADLESS_FIELDS:
            collected[key] = prompt_field(label, secret, helptext, current.get(key, ""))

    _report(*write_env(collected))
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="engine.configure")
    parser.add_argument("--fields", action="store_true",
                        help="print the field schema as JSON and exit")
    parser.add_argument("--from-json", metavar="FILE",
                        help="non-interactive: write .env from a {KEY: value} JSON file")
    args = parser.parse_args(argv)

    if args.fields:
        print(json.dumps(field_schema(), indent=2))
        return 0
    if args.from_json:
        provided = json.loads(Path(args.from_json).read_text())
        _report(*write_env(provided))
        return 0
    return _interactive()


if __name__ == "__main__":
    raise SystemExit(main())
