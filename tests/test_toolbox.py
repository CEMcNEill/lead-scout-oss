"""Toolbox tests, with the creation-time fact-check invariant front and center.

The key property: a synthesis tool cannot mint a Claim whose cited raw_keys are
not present in the fetcher output. Hallucinated candidates are dropped, never
asserted.
"""

import json

import pytest

from shared.contracts import Product
from shared.model import FakeModel
from shared.tools.fetchers import (
    StubCompanyFetcher,
    StubCrmFetcher,
    StubPersonFetcher,
    StubUsageFetcher,
    World,
)
from shared.tools.grounding import resolve_path
from shared.tools.toolbox import build_toolbox


def _world() -> World:
    return World(
        tasks={
            "task_pl": {
                "task_id": "task_pl",
                "lead": {
                    "name": "Sam Rivera",
                    "email": "sam@acme.com",
                    "title": "Engineer",
                    "company": "Acme",
                    "domain": "acme.com",
                    "lead_source": "plg_signup",
                },
                "account_ref": "acct_acme",
            }
        },
        persons={
            "dana@acme.com": {"name": "Dana Lopez", "title": "VP Engineering"},
        },
        companies={
            "acme.com": {"industry": "devtools", "employees": 800, "stack": ["react"]},
        },
        usage={
            "acct_acme": {
                "events_30d": 4_200_000,
                "products": ["analytics", "replay"],
                "roster": [
                    {"email": "dana@acme.com", "name": "Dana Lopez", "touches": ["analytics"]},
                ],
            }
        },
    )


def _toolbox(model: FakeModel):
    w = _world()
    return build_toolbox(
        crm_fetcher=StubCrmFetcher(w),
        person_fetcher=StubPersonFetcher(w),
        company_fetcher=StubCompanyFetcher(w),
        usage_fetcher=StubUsageFetcher(w),
        model=model,
        voice_profile="Plain prose. No emdashes.",
        exemplars=["Hey, saw you were digging into funnels..."],
        signature="Chris",
    )


def test_resolve_path_nested_list():
    obj = {"roster": [{"email": "a@x.com"}, {"email": "b@x.com"}]}
    assert resolve_path(obj, "roster.1.email") == (True, "b@x.com")
    assert resolve_path(obj, "roster.5.email") == (False, None)
    assert resolve_path(obj, "missing") == (False, None)


def test_crm_context_ground_truth_claims():
    tb = _toolbox(FakeModel())
    res = tb.crm_context.read("task_pl")
    fields = {c.field: c.value for c in res.claims}
    assert fields["contact_email"] == "sam@acme.com"
    assert fields["company_domain"] == "acme.com"
    assert fields["lead_source"] == "plg_signup"
    # every claim traces to the record
    for c in res.claims:
        assert c.source == "crm_context"
        assert c.raw  # non-empty provenance


def test_person_research_drops_hallucinated_claim():
    model = FakeModel()
    # one grounded candidate (cites 'title'), one hallucinated (cites a key the
    # fetcher never returned) -> the hallucinated one must be dropped.
    model.set(
        "person_research.synthesis:dana@acme.com",
        json.dumps(
            [
                {"field": "seniority", "value": "VP", "raw_keys": ["title"], "confidence": 0.9},
                {"field": "likely_pain", "value": "made up",
                 "raw_keys": ["salary"], "confidence": 0.7},
            ]
        ),
    )
    tb = _toolbox(model)
    res = tb.person_research.enrich({"email": "dana@acme.com"})
    assert [c.field for c in res.claims] == ["seniority"]
    assert res.claims[0].raw == {"title": "VP Engineering"}
    assert len(res.rejected) == 1
    assert res.rejected[0].missing_keys == ["salary"]


def test_usage_research_and_roster():
    model = FakeModel()
    model.set(
        "usage_research.synthesis:acct_acme",
        json.dumps(
            [
                {"field": "monthly_event_volume", "value": 4_200_000,
                 "raw_keys": ["events_30d"], "confidence": 0.99},
                {"field": "products_touched", "value": ["analytics", "replay"],
                 "raw_keys": ["products"], "confidence": 0.95},
            ]
        ),
    )
    tb = _toolbox(model)
    res = tb.usage_research.query("acct_acme")
    vols = {c.field: c.value for c in res.claims}
    assert vols["monthly_event_volume"] == 4_200_000
    roster = tb.usage_research.roster(res)
    assert roster[0]["email"] == "dana@acme.com"


def test_use_case_mapping_drops_bad_product_and_grounds():
    model = FakeModel()
    model.set(
        "use_case_mapping.synthesis",
        json.dumps(
            [
                {"use_case": "debug broken funnels", "product": "analytics",
                 "owner_persona": "PM", "raw_keys": ["message"], "confidence": 0.8},
                {"use_case": "nonsense", "product": "teleportation",
                 "owner_persona": None, "raw_keys": ["message"], "confidence": 0.5},
            ]
        ),
    )
    tb = _toolbox(model)
    res = tb.use_case_mapping.map({"message": "our funnels keep breaking"})
    assert len(res.claims) == 1
    claim = res.claims[0]
    assert claim.field == "use_case"
    assert claim.value["product"] == Product.ANALYTICS.value
    assert claim.raw == {"message": "our funnels keep breaking"}
    assert any("teleportation" in m for r in res.rejected for m in r.missing_keys)


def test_drafter_addresses_target_and_signs():
    model = FakeModel()
    model.set(
        "drafter",
        json.dumps({"subject": "PostHog at Acme", "body": "Saw your funnels...",
                    "claims_used": ["c1"]}),
    )
    tb = _toolbox(model)
    from shared.contracts import Claim, Disposition, DispositionKind, Target

    dossier = [Claim(id="c1", field="use_case", value={"use_case": "x"},
                     source="use_case_mapping", raw={"message": "..."}, confidence=0.8)]
    disp = Disposition(
        disposition=DispositionKind.CALL,
        reasoning="clears bar (c1)",
        confidence=0.8,
        claim_refs=["c1"],
        target=Target(name="Dana", email="dana@acme.com", is_named_lead=False),
    )
    draft = tb.drafter.draft(dossier, disp, angle="usage-led")
    assert draft.to == "dana@acme.com"
    assert draft.subject == "PostHog at Acme"
    assert draft.body.endswith("Chris")
    assert draft.claims_used == ["c1"]


def test_drafter_blanks_ungrounded_recipient_name():
    # the recipient name is not in any Claim -> do not pass it to the drafter, and
    # tell it to greet without a name. Regression for the "Hey <guessed name>" strip.
    from shared.contracts import Claim, Disposition, DispositionKind, Target
    from shared.tools.drafter import DrafterTool

    model = FakeModel()
    model.set("drafter", json.dumps({"subject": "s", "body": "Hey -\n\nbody", "claims_used": ["c1"]}))
    d = DrafterTool(model, voice_profile="v", exemplars=[], signature="Chris")
    dossier = [Claim(id="c1", field="company_name", value="Acme", source="crm",
                     raw={"m": 1}, confidence=0.9)]
    disp = Disposition(DispositionKind.CALL, "r (c1)", 0.8, ["c1"],
                       Target(name="Robin", email="robin@acme.com"))
    d.draft(dossier, disp, angle="a")
    prompt = model.calls[-1]["prompt"]
    assert "Robin" not in prompt  # the ungrounded name never reaches the drafter
    assert "greet without a name" in prompt.lower()


def test_drafter_keeps_grounded_recipient_name():
    from shared.contracts import Claim, Disposition, DispositionKind, Target
    from shared.tools.drafter import DrafterTool

    model = FakeModel()
    model.set("drafter", json.dumps({"subject": "s", "body": "Hey Dana", "claims_used": ["c1"]}))
    d = DrafterTool(model, voice_profile="v", exemplars=[], signature="Chris")
    # "Dana" appears as a whole word in a Claim value -> grounded, keep it
    dossier = [Claim(id="c1", field="contact_name", value="Dana Lopez", source="crm",
                     raw={"name": "Dana Lopez"}, confidence=0.9)]
    disp = Disposition(DispositionKind.CALL, "r (c1)", 0.8, ["c1"],
                       Target(name="Dana", email="dana@acme.com"))
    d.draft(dossier, disp, angle="a")
    prompt = model.calls[-1]["prompt"]
    assert '"name": "Dana"' in prompt
    assert "greet without a name" not in prompt.lower()


def test_drafter_rewrites_invented_calendar_link_to_configured():
    from shared.contracts import Claim, Disposition, DispositionKind, Target
    from shared.tools.drafter import DrafterTool

    model = FakeModel()
    model.set("drafter", json.dumps({
        "subject": "PostHog",
        "body": "Grab time on my calendar: https://calendly.com/chrisatposthog -- talk soon.",
        "claims_used": ["c1"]}))
    d = DrafterTool(model, voice_profile="v", exemplars=[], signature="Chris",
                    calendar_url="https://calendly.com/chris-m-posthog/30min")
    dossier = [Claim(id="c1", field="x", value="y", source="s", raw={"m": 1}, confidence=0.9)]
    disp = Disposition(DispositionKind.CALL, "r (c1)", 0.8, ["c1"],
                       Target(name="Dana", email="dana@acme.com"))
    draft = d.draft(dossier, disp, angle="a")
    # the invented handle is gone; the configured link is what survives
    assert "calendly.com/chrisatposthog" not in draft.body
    assert "https://calendly.com/chris-m-posthog/30min" in draft.body


def test_drafter_without_calendar_leaves_body_untouched():
    from shared.contracts import Claim, Disposition, DispositionKind, Target
    from shared.tools.drafter import DrafterTool

    model = FakeModel()
    model.set("drafter", json.dumps({"subject": "s", "body": "no link here at all",
                                     "claims_used": ["c1"]}))
    d = DrafterTool(model, voice_profile="v", exemplars=[], signature="Chris", calendar_url="")
    dossier = [Claim(id="c1", field="x", value="y", source="s", raw={"m": 1}, confidence=0.9)]
    disp = Disposition(DispositionKind.CALL, "r (c1)", 0.8, ["c1"],
                       Target(name="Dana", email="dana@acme.com"))
    draft = d.draft(dossier, disp, angle="a")
    assert "no link here at all" in draft.body


def test_unknown_person_returns_honest_thin_record():
    tb = _toolbox(FakeModel({"person_research.synthesis:ghost@nowhere.com": "[]"}))
    res = tb.person_research.enrich({"email": "ghost@nowhere.com"})
    assert res.claims == []
    assert res.raw["found"] is False
