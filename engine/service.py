"""The headless service entrypoint (launchd home for the fast loop).

launchd invokes `uv run python -m engine.service` every 5 minutes; each
invocation runs one sweep and exits. Phase 1 runs over fixture data with the real
Claude model, so the whole engine is exercised end to end before any Salesforce,
Clay, or PostHog credential is wired. Swapping StubToolProvider for the real
rep-scoped providers (and StubTaskSource for a Salesforce SOQL poll) is the only
change needed to point this at live data.

`assemble_shell` is factored out so tests can build the same shell with a scripted
model instead of live Claude.
"""

from __future__ import annotations

import os
from pathlib import Path

from engine.adapters import BatchAdapter, StubTaskSource
from engine.cost import BudgetGovernor, ModelPolicy
from engine.hardstops import HardStopConfig
from engine.ledger import Ledger
from engine.providers import FilesystemStagingSink, StubToolProvider
from engine.router import Router
from engine.shell import Shell
from shared.model import ModelClient
from shared.registry import build_default_registry
from shared.tools.fetchers import World

REPO_ROOT = Path(__file__).resolve().parent.parent


def voice_profile_path(repo_root: Path = REPO_ROOT) -> Path:
    """Resolve the active voice profile. The trained profile is local-only
    (gitignored), so a fresh clone has only the shipped template. Prefer, in order:
    an explicit VOICE_PROFILE_PATH, the rep's config/voice/chris.md, then the
    template config/voice/chris.example.md. This keeps a pre-setup checkout (and
    the tests) working before setup.sh seeds the profile."""
    override = os.environ.get("VOICE_PROFILE_PATH")
    if override:
        return Path(override)
    voice_dir = repo_root / "config" / "voice"
    active = voice_dir / "chris.md"
    return active if active.exists() else voice_dir / "chris.example.md"


def _load_exemplar_bank(path: Path) -> dict[str, list[str]]:
    """Load the exemplar bank the bootstrap job produces, if present."""
    if not path.exists():
        return {}
    import json

    return json.loads(path.read_text())


def assemble_shell(
    *,
    ledger: Ledger,
    inner_model: ModelClient,
    world: World | None = None,
    tool_provider=None,
    repo_root: Path = REPO_ROOT,
    per_run_cap_usd: float = 2.0,
    per_day_cap_usd: float = 50.0,
    staging_dir: Path | None = None,
    staging_sink=None,
    notifier=None,
    exemplar_bank: dict[str, list[str]] | None = None,
) -> Shell:
    rubric = (repo_root / "config" / "rubric.md").read_text()
    voice = voice_profile_path(repo_root).read_text()
    if exemplar_bank is None:
        exemplar_bank = _load_exemplar_bank(repo_root / "config" / "exemplars.json")
    if tool_provider is None:
        if world is None:
            raise ValueError("assemble_shell needs either a tool_provider or a world")
        tool_provider = StubToolProvider(
            world, voice_profile=voice, voice_version="v1", exemplar_bank=exemplar_bank
        )
    if staging_sink is None:
        staging_sink = FilesystemStagingSink(staging_dir or (repo_root / "staged_drafts"))
    policy = ModelPolicy()
    kwargs = {}
    if notifier is not None:
        kwargs["notifier"] = notifier
    return Shell(
        ledger=ledger,
        router=Router.from_yaml(repo_root / "qualifiers" / "registry.yaml"),
        registry=build_default_registry(rubric),
        hard_stops=HardStopConfig.from_yaml(repo_root / "config" / "hard_stops.yaml"),
        governor=BudgetGovernor(
            policy, per_run_cap_usd=per_run_cap_usd, per_day_cap_usd=per_day_cap_usd
        ),
        inner_model=inner_model,
        tool_provider=tool_provider,
        staging_sink=staging_sink,
        **kwargs,
    )


def build_sf_client():
    """The Salesforce client for the configured auth mode (CLI by default)."""
    if os.environ.get("SF_AUTH", "cli").lower() == "cli":
        from engine.sf_cli import build_cli_client

        return build_cli_client()
    from engine.salesforce import build_client_from_env

    return build_client_from_env()


def _research_fetchers(repo_root: Path, clay_caller):
    """person/company fetchers, in preference order: configured REST enrichment
    provider (headless) > Clay MCP (agent runtime) > stub."""
    from engine.enrichment import build_enrichment_fetchers
    from engine.providers import stub_research_fetchers

    provider_fetchers = build_enrichment_fetchers(repo_root / "config" / "enrichment.yaml")
    if provider_fetchers is not None:
        return provider_fetchers
    if clay_caller is not None:
        from engine.clay import ClayCompanyFetcher, ClayPersonFetcher

        return ClayPersonFetcher(clay_caller), ClayCompanyFetcher(clay_caller)
    person_stub, company_stub, _ = stub_research_fetchers()
    return person_stub, company_stub


def build_salesforce_runtime(repo_root: Path = REPO_ROOT, *, clay_caller=None, client=None):
    """Assemble the Salesforce-backed tool provider and task source. crm + the
    open-task poll + usage are real Salesforce; person/company use the configured
    enrichment provider, else Clay, else stub. Returns (provider, task_source,
    client)."""
    from engine.providers import CompositeToolProvider
    from engine.salesforce import SalesforceCrmFetcher, SalesforceTaskSource, SfFieldMap
    from engine.usage import SalesforceUsageFetcher

    client = client or build_sf_client()
    field_map = SfFieldMap(
        trigger_task_field=os.environ.get("SF_TRIGGER_TASK_FIELD") or None,
        trigger_contact_field=os.environ.get("SF_TRIGGER_CONTACT_FIELD") or None,
        account_ref_contact_field=os.environ.get("SF_ACCOUNT_REF_CONTACT_FIELD") or None,
    )
    crm_fetcher = SalesforceCrmFetcher(client, field_map=field_map)
    task_source = SalesforceTaskSource(
        client,
        status=os.environ.get("SF_LEAD_TASK_STATUS", "Open"),
        extra_where=os.environ.get("SF_LEAD_TASK_FILTER") or None,
    )
    person_fetcher, company_fetcher = _research_fetchers(repo_root, clay_caller)
    voice = voice_profile_path(repo_root).read_text()
    provider = CompositeToolProvider(
        crm_fetcher=crm_fetcher,
        person_fetcher=person_fetcher,
        company_fetcher=company_fetcher,
        usage_fetcher=SalesforceUsageFetcher(client),
        voice_profile=voice,
        exemplar_bank=_load_exemplar_bank(repo_root / "config" / "exemplars.json"),
    )
    return provider, task_source, client


def build_staging_sink(repo_root: Path = REPO_ROOT):
    """Gmail draft staging when Gmail is configured, else a local filesystem sink."""
    if os.environ.get("GMAIL_CLIENT_ID") and os.environ.get("GMAIL_ACCOUNT"):
        from engine.gmail import GmailStagingSink
        from engine.gmail_api import build_gmail_client_from_env

        return GmailStagingSink(build_gmail_client_from_env())
    return FilesystemStagingSink(repo_root / "staged_drafts")


def build_notifier():
    """Slack bot notifier when a bot token is set, else a no-op."""
    if os.environ.get("SLACK_BOT_TOKEN"):
        from engine.slack import SlackNotifier
        from engine.slack_api import build_slack_client_from_env

        return SlackNotifier(
            build_slack_client_from_env(), sf_account_base=os.environ.get("SF_INSTANCE_URL")
        )
    from engine.providers import NullNotifier

    return NullNotifier()


def default_rep_config():
    from shared.contracts import RepConfig

    return RepConfig(
        rep_id=os.environ.get("REP_ID", "rep_chris"),
        sf_user_id=os.environ.get("SF_USERNAME", "unknown"),
        sf_credential_ref="keychain:lead-agent:sf",
        gmail_account=os.environ.get("GMAIL_ACCOUNT", "chris.m@posthog.com"),
        voice_profile_ref=str(voice_profile_path()),
        signature=os.environ.get("REP_SIGNATURE", "Chris"),
        slack_post_target=os.environ.get("SLACK_USER_ID", ""),
        budget_cap_usd=float(os.environ.get("REP_BUDGET_CAP_USD", "50")),
        calendar_url=os.environ.get("REP_CALENDAR_URL", ""),
    )


def build_slow_loop(ledger: Ledger, client=None, repo_root: Path = REPO_ROOT):
    """Assemble the nightly slow loop from headless clients: Gmail (sent items),
    Slack (thread replies), the model, and the Salesforce writer for account
    corrections. Falls back to no-op clients when an integration isn't configured."""
    from engine.anthropic_model import AnthropicModel
    from engine.gmail import RecordedGmailClient
    from engine.loop_slow import SlowLoop
    from engine.slack import RecordedSlackClient

    gmail = RecordedGmailClient()
    if os.environ.get("GMAIL_CLIENT_ID") and os.environ.get("GMAIL_ACCOUNT"):
        from engine.gmail_api import build_gmail_client_from_env

        gmail = build_gmail_client_from_env()
    slack = RecordedSlackClient()
    if os.environ.get("SLACK_BOT_TOKEN"):
        from engine.slack_api import build_slack_client_from_env

        slack = build_slack_client_from_env()
    return SlowLoop(
        ledger=ledger, gmail=gmail, slack=slack, model=AnthropicModel(ModelPolicy()),
        rep_config=default_rep_config(),
        proposals_dir=repo_root / "config" / "proposals",
        voice_profile_path=voice_profile_path(repo_root),
        rubric_path=repo_root / "config" / "rubric.md",
        sf_writer=client,  # the SF client writes account corrections
    )


def _run_fast(ledger, inner_model) -> str:
    source = os.environ.get("SOURCE", "fixtures").lower()
    if source == "salesforce":
        provider, task_source, _ = build_salesforce_runtime()
        shell = assemble_shell(
            ledger=ledger, inner_model=inner_model, tool_provider=provider,
            staging_sink=build_staging_sink(), notifier=build_notifier(),
        )
    else:
        world = World.load(os.environ.get("FIXTURES_PATH", str(REPO_ROOT / "fixtures" / "world.json")))
        shell = assemble_shell(ledger=ledger, inner_model=inner_model, world=world)
        task_source = StubTaskSource.from_world(world)
    result = BatchAdapter(shell, task_source).sweep(default_rep_config())
    return (
        f"fast sweep: {result.staged} staged, {len(result.processed)} processed, "
        f"{len(result.skipped)} skipped"
        + (f", HALTED ({result.halt_reason})" if result.halted else "")
    )


def _run_slow(ledger) -> str:
    import datetime as _dt

    client = build_sf_client() if os.environ.get("SOURCE", "").lower() == "salesforce" else None
    stamp = _dt.datetime.now().strftime("%Y-%m-%d")
    result = build_slow_loop(ledger, client=client).run_nightly(stamp)
    return (
        f"slow loop: {len(result.voice_edits)} voice edits, "
        f"{len(result.disagreements)} disagreements, "
        f"{len(result.account_corrections)} account corrections"
    )


def main() -> int:
    from engine.anthropic_model import AnthropicModel
    from engine.env import load_dotenv

    load_dotenv()
    ledger = Ledger(os.environ.get("LEDGER_PATH", str(REPO_ROOT / "ledger" / "lead_runs.db")))
    mode = os.environ.get("MODE", "fast").lower()
    if mode == "slow":
        print(_run_slow(ledger))
    else:
        print(_run_fast(ledger, AnthropicModel(ModelPolicy())))
    ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
