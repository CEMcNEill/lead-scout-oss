"""Round-trip tests for the core contracts.

Everything the ledger persists must survive to_dict -> json -> from_dict
unchanged, because the ledger stores these as JSON blobs.
"""

import json

from shared.contracts import (
    Claim,
    Cost,
    CostEntry,
    Disposition,
    DispositionKind,
    Draft,
    LeadRun,
    Outcome,
    Product,
    Route,
    RunResult,
    RunStatus,
    Target,
    TriggerSource,
    UseCaseClaim,
)


def _sample_dossier() -> list[Claim]:
    return [
        Claim(
            id="c1",
            field="seniority",
            value="VP Engineering",
            source="person_research",
            raw={"title": "VP Engineering", "linkedin": "..."},
            confidence=0.9,
        ),
        Claim(
            id="c2",
            field="monthly_event_volume",
            value=4_200_000,
            source="usage_research",
            raw={"events_30d": 4200000},
            confidence=0.99,
        ),
    ]


def _sample_run() -> LeadRun:
    return LeadRun(
        id="run_1",
        task_id="00T000000000001",
        rep_id="rep_chris",
        trigger_source=TriggerSource.BATCH,
        ts="2026-06-28T12:00:00Z",
        route=Route(lead_type="big_fish", qualifier="big_fish"),
        status=RunStatus.STAGED_FOR_REVIEW,
        dossier=_sample_dossier(),
        hard_stops=[],
        disposition=Disposition(
            disposition=DispositionKind.CALL,
            reasoning="Usage trajectory clears the bar (c2) despite weak firmographics.",
            confidence=0.82,
            claim_refs=["c1", "c2"],
            target=Target(
                name="Dana Lopez",
                email="dana@acme.com",
                role="VP Engineering",
                is_named_lead=False,
            ),
        ),
        staged_draft=Draft(
            to="dana@acme.com",
            subject="PostHog at Acme",
            body="Saw your team is leaning on analytics heavily...",
            angle="usage-led",
            claims_used=["c2"],
        ),
        cost=Cost(
            entries=[
                CostEntry(step="qualifier_judgment", kind="model", detail="opus",
                          tokens_in=1200, tokens_out=300, usd=0.045),
                CostEntry(step="person_research", kind="tool", detail="clay", usd=0.01),
            ]
        ),
        voice_profile_version="v1",
        rubric_version="v1",
        model_policy_version="v1",
    )


def test_claim_round_trip():
    c = _sample_dossier()[0]
    assert Claim.from_dict(json.loads(json.dumps(c.to_dict()))) == c


def test_use_case_claim_round_trip():
    u = UseCaseClaim(
        id="u1",
        use_case="debug broken funnels",
        product=Product.ANALYTICS,
        evidence="funnels viewed 40x in 7d",
        owner_persona="product manager",
        confidence=0.7,
    )
    assert UseCaseClaim.from_dict(json.loads(json.dumps(u.to_dict()))) == u


def test_run_result_round_trip():
    rr = RunResult(
        dossier=_sample_dossier(),
        disposition=Disposition(
            disposition=DispositionKind.NURTURE,
            reasoning="Not ready, revisit (c2).",
            confidence=0.6,
            claim_refs=["c2"],
        ),
        draft=None,
    )
    back = RunResult.from_dict(json.loads(json.dumps(rr.to_dict())))
    assert back == rr


def test_lead_run_round_trip():
    run = _sample_run()
    blob = json.dumps(run.to_dict())
    back = LeadRun.from_dict(json.loads(blob))
    assert back == run


def test_touch_round_trip_and_lead_run_touches():
    from shared.contracts import Touch

    t = Touch(n=1, subject="PostHog at Acme", body="hi", staged_at="2026-06-01T00:00:00Z",
              sent_at="2026-06-01T10:00:00+00:00", draft_ref="https://draft/1")
    assert Touch.from_dict(json.loads(json.dumps(t.to_dict()))) == t

    run = _sample_run()
    assert run.touches == [] and run.next_touch_due is None  # defaults
    run.touches = [t]
    run.next_touch_due = "2026-06-05T10:00:00+00:00"
    back = LeadRun.from_dict(json.loads(json.dumps(run.to_dict())))
    assert back.touches == [t]
    assert back.next_touch_due == "2026-06-05T10:00:00+00:00"
    # an old blob without the keys still loads
    blob = json.loads(json.dumps(run.to_dict()))
    del blob["touches"]
    del blob["next_touch_due"]
    loaded = LeadRun.from_dict(blob)
    assert loaded.touches == [] and loaded.next_touch_due is None


def test_lead_run_thread_id_round_trips_and_defaults_none():
    run = _sample_run()
    assert run.thread_id is None  # default
    run.thread_id = "thread-abc"
    back = LeadRun.from_dict(json.loads(json.dumps(run.to_dict())))
    assert back.thread_id == "thread-abc"
    # an old blob without the key still loads
    blob = json.loads(json.dumps(run.to_dict()))
    del blob["thread_id"]
    assert LeadRun.from_dict(blob).thread_id is None


def test_lead_run_defaults_round_trip():
    """A blocked run with no disposition/draft still round-trips."""
    run = LeadRun(
        id="run_2",
        task_id="00T000000000002",
        rep_id="rep_chris",
        trigger_source=TriggerSource.MANUAL,
        ts="2026-06-28T12:05:00Z",
        route=Route(lead_type="inbound", qualifier="inbound"),
        status=RunStatus.BLOCKED,
        hard_stops=["competitor"],
    )
    back = LeadRun.from_dict(json.loads(json.dumps(run.to_dict())))
    assert back == run
    assert back.outcome == Outcome()
    assert back.cost.total_usd == 0.0


def test_cost_total():
    cost = _sample_run().cost
    assert cost.total_usd == 0.055
