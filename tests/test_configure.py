"""Config-wizard tests. Pure: REPO_ROOT is pointed at a tmp dir and input/getpass
are scripted, so nothing touches a real .env."""

import getpass
import json

from engine import configure as cfg

EXAMPLE = """\
# header comment
ANTHROPIC_API_KEY=
SOURCE=fixtures
SF_AUTH=cli
SF_INSTANCE_URL=https://posthog.my.salesforce.com
SF_TARGET_ORG=
SLACK_USER_ID=
# rep identity
REP_SIGNATURE=
REP_CALENDAR_URL=
GMAIL_CLIENT_ID=
"""


def test_example_defaults_and_render_preserve_comments():
    defs = cfg.example_defaults(EXAMPLE)
    assert defs["SOURCE"] == "fixtures"
    assert defs["SF_INSTANCE_URL"] == "https://posthog.my.salesforce.com"
    merged = {**defs, "SOURCE": "salesforce", "SLACK_USER_ID": "U1", "NEW_KEY": "x"}
    out = cfg.render_env(EXAMPLE, merged)
    assert "# header comment" in out and "# rep identity" in out  # comments kept
    assert "SOURCE=salesforce" in out and "SOURCE=fixtures" not in out  # value rewritten
    assert "SLACK_USER_ID=U1" in out
    assert "NEW_KEY=x" in out  # key absent from the example is appended


def _script(monkeypatch, inputs, secrets):
    it_in, it_se = iter(inputs), iter(secrets)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it_in))
    monkeypatch.setattr(getpass, "getpass", lambda *a, **k: next(it_se))


def test_wizard_precedence_keep_override_and_force(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "REPO_ROOT", tmp_path)
    (tmp_path / ".env.example").write_text(EXAMPLE)
    # an existing .env: a secret to keep, a slack id to override
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=old-key\nSLACK_USER_ID=Uold\nSOURCE=fixtures\n")

    # core prompts in order: SF_INSTANCE_URL, SF_TARGET_ORG, SLACK_USER_ID,
    # SLACK_DM_CHANNEL_ID, REP_SIGNATURE, REP_CALENDAR_URL, then the headless y/N
    inputs = ["https://acme.my.salesforce.com", "", "U123", "D123", "Dana",
              "https://calendly.com/dana/30min", "n"]
    secrets = [""]  # blank -> keep the existing ANTHROPIC_API_KEY
    _script(monkeypatch, inputs, secrets)

    assert cfg.main([]) == 0
    env = cfg.read_env_file(tmp_path / ".env")
    assert env["ANTHROPIC_API_KEY"] == "old-key"   # blank secret kept the existing
    assert env["SLACK_USER_ID"] == "U123"          # answer overrode the existing
    assert env["SOURCE"] == "salesforce"           # forced by the wizard
    assert env["SF_AUTH"] == "cli"
    assert env["SF_INSTANCE_URL"] == "https://acme.my.salesforce.com"
    assert env["SLACK_DM_CHANNEL_ID"] == "D123"
    assert env["REP_SIGNATURE"] == "Dana"
    assert env["REP_CALENDAR_URL"] == "https://calendly.com/dana/30min"
    assert env["GMAIL_CLIENT_ID"] == ""            # headless skipped, stays blank


def test_fields_dump_lists_questions_and_forced(capsys):
    assert cfg.main(["--fields"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["forced"]["SOURCE"] == "salesforce"
    assert "ANTHROPIC_API_KEY" in schema["required"]
    keys = {f["key"] for f in schema["fields"]}
    assert {"ANTHROPIC_API_KEY", "SLACK_USER_ID", "REP_CALENDAR_URL"} <= keys
    # the secret flag is exposed so a caller can hide those inputs
    assert any(f["key"] == "ANTHROPIC_API_KEY" and f["secret"] for f in schema["fields"])


def test_from_json_writes_env_noninteractive(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "REPO_ROOT", tmp_path)
    (tmp_path / ".env.example").write_text(EXAMPLE)
    (tmp_path / "in.json").write_text(json.dumps({
        "ANTHROPIC_API_KEY": "sk-xyz", "SLACK_USER_ID": "U9",
        "REP_CALENDAR_URL": "https://calendly.com/x/30min"}))
    assert cfg.main(["--from-json", str(tmp_path / "in.json")]) == 0
    env = cfg.read_env_file(tmp_path / ".env")
    assert env["ANTHROPIC_API_KEY"] == "sk-xyz"
    assert env["SLACK_USER_ID"] == "U9"
    assert env["SOURCE"] == "salesforce"   # forced
    assert env["SF_AUTH"] == "cli"         # forced


def test_wizard_collects_headless_when_opted_in(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "REPO_ROOT", tmp_path)
    (tmp_path / ".env.example").write_text(EXAMPLE)
    inputs = ["https://acme.my.salesforce.com", "", "U123", "D123", "Dana", "", "y",
              "gmail-client-id", "me@acme.com"]  # SF_INSTANCE, target, slack, dm-channel, sig, cal, y/N, then 2 non-secret headless
    secrets = ["sk-key", "gmail-secret", "xoxb-token", "enrich-key"]  # ANTHROPIC + 3 headless secrets
    _script(monkeypatch, inputs, secrets)
    assert cfg.main([]) == 0
    env = cfg.read_env_file(tmp_path / ".env")
    assert env["GMAIL_CLIENT_ID"] == "gmail-client-id"
    assert env["GMAIL_CLIENT_SECRET"] == "gmail-secret"
    assert env["GMAIL_ACCOUNT"] == "me@acme.com"
    assert env["SLACK_BOT_TOKEN"] == "xoxb-token"
    assert env["ENRICHMENT_API_KEY"] == "enrich-key"
