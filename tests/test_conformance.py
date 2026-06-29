"""Conformance gate: every registered qualifier must pass; the gate must bite on
bad ones. Each qualifier is resolved from the default registry so the test covers
exactly what ships."""

import pytest

from qualifiers.inbound.qualifier import InboundQualifier
from shared.conformance import run_conformance
from shared.contracts import Claim, Disposition, DispositionKind, Draft, RunResult
from shared.registry import NotConformant, QualifierRegistry, build_default_registry
from tests.fixtures.conformance_cases import all_cases

RUBRIC = "Holistic. No single axis disqualifies."

_REGISTRY = build_default_registry(RUBRIC)
_NAMES = _REGISTRY.names()


def test_every_registered_qualifier_has_conformance_cases():
    # a qualifier with no fixed test set cannot be proven; require one per name
    missing = [n for n in _NAMES if n not in all_cases()]
    assert not missing, f"qualifiers without conformance cases: {missing}"


@pytest.mark.parametrize("name", _NAMES)
def test_each_qualifier_passes_conformance(name):
    qualifier = _REGISTRY.get(name)
    cases = all_cases()[name]
    report = run_conformance(qualifier, cases)
    assert report.passed, report.summary()
    # every case stayed within (trivial, fake-priced) cost bounds
    assert all(c.cost_usd >= 0 for c in report.cases)


def test_registry_accepts_conformant_qualifier():
    qualifier = InboundQualifier(RUBRIC)
    report = run_conformance(qualifier, all_cases()["inbound"])
    reg = QualifierRegistry()
    reg.register_conformant(qualifier, report)
    assert "inbound" in reg.names()


# --- negative cases: the gate must reject bad interiors -------------------


class _UngroundedDraftQualifier(InboundQualifier):
    """A qualifier whose draft asserts something no Claim grounds. The boundary
    gate must catch it in conformance."""

    name = "broken_ungrounded"
    lead_type = "inbound"

    def run(self, task_id, record, tools):
        result = super().run(task_id, record, tools)
        # force a call disposition with a draft, regardless of the judge
        disp = Disposition(disposition=DispositionKind.CALL, reasoning="forced",
                           confidence=0.9, claim_refs=[c.id for c in result.dossier][:1],
                           target=result.disposition.target)
        draft = Draft(to="sam@acme.com", subject="x",
                      body="You just raised a Series C.", angle="x")
        return RunResult(dossier=result.dossier, disposition=disp, draft=draft)


def test_conformance_rejects_ungrounded_draft():
    qualifier = _UngroundedDraftQualifier(RUBRIC)
    # scripted fact-check reports the assertion as ungrounded
    cases = all_cases()["inbound"][:1]
    cases[0].model.set("factcheck",
                       '[{"assertion": "You just raised a Series C.", "claim_ref": null}]')
    report = run_conformance(qualifier, cases)
    assert not report.passed
    assert any("grounding gate" in v for c in report.cases for v in c.violations)


class _EmptyDossierQualifier(InboundQualifier):
    name = "broken_empty"
    lead_type = "inbound"

    def run(self, task_id, record, tools):
        disp = Disposition(disposition=DispositionKind.NURTURE, reasoning="n",
                           confidence=0.3, claim_refs=[])
        return RunResult(dossier=[], disposition=disp, draft=None)


def test_conformance_rejects_empty_dossier_and_registry_refuses():
    qualifier = _EmptyDossierQualifier(RUBRIC)
    report = run_conformance(qualifier, all_cases()["inbound"][:1])
    assert not report.passed
    assert any("empty dossier" in v for c in report.cases for v in c.violations)
    reg = QualifierRegistry()
    with pytest.raises(NotConformant):
        reg.register_conformant(qualifier, report)
