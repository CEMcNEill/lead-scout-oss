"""Boundary fact-check gate tests. Zero ungrounded facts may pass clean.

The deterministic verification step is tested directly; the full gate is tested
with a scripted extractor (FakeModel) so the model step is removed from the
equation.
"""

import json

from engine.factcheck import factcheck, verify_assertions
from shared.contracts import Claim, Draft
from shared.model import FakeModel


def _dossier() -> list[Claim]:
    return [
        Claim(id="c1", field="monthly_event_volume", value=4_200_000,
              source="usage_research", raw={"events_30d": 4200000}, confidence=0.99),
        Claim(id="c2", field="seniority", value="VP Engineering",
              source="person_research", raw={"title": "VP Engineering"}, confidence=0.9),
    ]


# --- deterministic verification ------------------------------------------


def test_verify_grounded_and_ungrounded():
    ids = {"c1", "c2"}
    checked = verify_assertions(
        [
            {"assertion": "you process 4.2M events a month", "claim_ref": "c1"},
            {"assertion": "you raised a Series C last week", "claim_ref": None},
            {"assertion": "you use Kubernetes", "claim_ref": "c9"},  # non-existent
        ],
        ids,
    )
    assert checked[0].grounded is True
    assert checked[1].grounded is False
    assert checked[2].grounded is False


# --- full gate ------------------------------------------------------------


def _draft() -> Draft:
    return Draft(to="dana@acme.com", subject="PostHog at Acme",
                 body="Saw you're at ~4.2M events/mo and leading eng.", angle="usage-led")


def test_gate_passes_when_all_grounded():
    model = FakeModel({
        "factcheck": json.dumps([
            {"assertion": "~4.2M events/mo", "claim_ref": "c1"},
            {"assertion": "leading eng", "claim_ref": "c2"},
        ])
    })
    result = factcheck(_draft(), _dossier(), model)
    assert result.passed
    assert result.ungrounded == []


def test_gate_fails_on_ungrounded_assertion():
    model = FakeModel({
        "factcheck": json.dumps([
            {"assertion": "~4.2M events/mo", "claim_ref": "c1"},
            {"assertion": "you just closed a Series C", "claim_ref": None},
        ])
    })
    result = factcheck(_draft(), _dossier(), model)
    assert not result.passed
    assert len(result.ungrounded) == 1
    assert "Series C" in result.ungrounded[0].text


def test_gate_fails_on_fabricated_claim_ref():
    model = FakeModel({
        "factcheck": json.dumps([{"assertion": "you use Datadog", "claim_ref": "c99"}])
    })
    result = factcheck(_draft(), _dossier(), model)
    assert not result.passed


def test_gate_passes_vacuously_with_no_assertions():
    model = FakeModel({"factcheck": "[]"})
    result = factcheck(_draft(), _dossier(), model)
    assert result.passed
    assert result.assertions == []
