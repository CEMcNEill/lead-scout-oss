"""Gmail draft staging tests (fixture/recorded client, no live calls)."""

from engine.gmail import GmailMessage, GmailStagingSink, RecordedGmailClient, draft_url
from shared.contracts import Draft, RepConfig

REP = RepConfig(
    rep_id="rep_chris", sf_user_id="x", sf_credential_ref="cli",
    gmail_account="chris.m@posthog.com", voice_profile_ref="v", signature="Chris",
    slack_post_target="U1", budget_cap_usd=50.0,
)


def _draft() -> Draft:
    return Draft(to="dana@acme.com", subject="PostHog at Acme", body="Saw your funnels...",
                 angle="usage-led", claims_used=["c1"])


def test_staging_creates_draft_and_returns_url():
    client = RecordedGmailClient()
    ref = GmailStagingSink(client).stage("run_1", REP, _draft())
    assert len(client.created) == 1
    created = client.created[0]
    assert created["to"] == ["dana@acme.com"]
    assert created["subject"] == "PostHog at Acme"
    assert ref == draft_url(created["id"])
    assert ref.startswith("https://mail.google.com/")


def test_staging_handles_missing_recipient():
    client = RecordedGmailClient()
    d = _draft()
    d.to = None
    GmailStagingSink(client).stage("run_1", REP, d)
    assert client.created[0]["to"] == []


def test_find_sent_matches_recipient():
    msgs = [GmailMessage("m1", "t1", "PostHog at Acme", "dana@acme.com",
                         "Saw your funnels, edited.", "2026-06-01")]
    client = RecordedGmailClient(sent={"dana@acme.com": msgs})
    assert client.find_sent("in:sent to:dana@acme.com")[0].body == "Saw your funnels, edited."
    assert client.find_sent("in:sent to:nobody@x.com") == []


def test_recorded_get_thread_replays_messages_and_from_addr():
    thread = [
        GmailMessage("m1", "t1", "PostHog at Acme", "dana@acme.com", "first touch",
                     "2026-06-01", from_addr="chris.m@posthog.com"),
        GmailMessage("m2", "t1", "Re: PostHog at Acme", "chris.m@posthog.com",
                     "interested", "2026-06-02", from_addr="dana@acme.com"),
    ]
    client = RecordedGmailClient(threads={"t1": thread})
    msgs = client.get_thread("t1")
    assert [m.id for m in msgs] == ["m1", "m2"]
    assert msgs[1].from_addr == "dana@acme.com"
    assert client.get_thread("unknown") == []


def test_staging_sink_satisfies_shell(tmp_path):
    """GmailStagingSink drops into the shell exactly where FilesystemStagingSink did."""
    import json

    from engine.ledger import Ledger
    from engine.providers import FixedClock, StubToolProvider
    from engine.service import assemble_shell
    from shared.contracts import RunStatus, TriggerMeta, TriggerSource
    from shared.model import FakeModel
    from shared.tools.fetchers import World

    world = World(
        tasks={"t1": {"task_id": "t1", "trigger": "demo_request",
                      "inbound_message": "funnels broken",
                      "lead": {"name": "Sam", "email": "sam@acme.com", "title": "Head of Product",
                               "company": "Acme", "domain": "acme.com", "lead_source": "demo_request"}}},
        persons={"sam@acme.com": {"name": "Sam", "title": "Head of Product"}},
        companies={"acme.com": {"industry": "saas"}}, usage={},
    )
    model = FakeModel({
        "person_research.synthesis:sam@acme.com": "[]",
        "company_research.synthesis:acme.com": "[]",
        "use_case_mapping.synthesis": json.dumps([{"use_case": "debug funnels", "product": "analytics",
            "owner_persona": "PM", "raw_keys": ["message"], "confidence": 0.8}]),
        "inbound.judgment": json.dumps({"disposition": "call", "reasoning": "fit (c1)",
            "confidence": 0.8, "claim_refs": ["c1"], "target_email": "sam@acme.com"}),
        "drafter": json.dumps({"subject": "PostHog", "body": "funnels", "claims_used": ["c1"]}),
        "factcheck": "[]",
    })
    gmail = RecordedGmailClient()
    ledger = Ledger(tmp_path / "l.db")
    shell = assemble_shell(ledger=ledger, inner_model=model, world=world,
                           staging_sink=GmailStagingSink(gmail))
    shell.clock = FixedClock()
    run = shell.process_lead_run("t1", REP, TriggerMeta(TriggerSource.MANUAL, ""))
    assert run.status == RunStatus.STAGED_FOR_REVIEW
    assert len(gmail.created) == 1  # a real Gmail draft was created
    assert run.staged_draft_ref.startswith("https://mail.google.com/")
    ledger.close()
