"""The qualifier registry: binds a qualifier name to its implementation.

The router decides which qualifier name handles a lead (from registry.yaml); this
registry resolves that name to a live instance. Registration is conformance
gated: a qualifier that has not passed the suite is refused, which is what keeps
the trusted interior trustworthy.
"""

from __future__ import annotations

from shared.contracts import Route
from shared.conformance import ConformanceReport
from shared.qualifier import Qualifier


class QualifierNotRegistered(KeyError):
    pass


class NotConformant(Exception):
    pass


class QualifierRegistry:
    def __init__(self) -> None:
        self._qualifiers: dict[str, Qualifier] = {}

    def register(self, qualifier: Qualifier) -> None:
        """Register unconditionally. Use register_conformant to gate on the
        suite; this exists for runtime assembly after CI has proven conformance."""
        self._qualifiers[qualifier.name] = qualifier

    def register_conformant(self, qualifier: Qualifier, report: ConformanceReport) -> None:
        if not report.passed:
            raise NotConformant(
                f"qualifier {qualifier.name!r} failed conformance:\n{report.summary()}"
            )
        self.register(qualifier)

    def get(self, name: str) -> Qualifier:
        if name not in self._qualifiers:
            raise QualifierNotRegistered(f"no qualifier registered as {name!r}")
        return self._qualifiers[name]

    def dispatch(self, route: Route) -> Qualifier:
        return self.get(route.qualifier)

    def names(self) -> list[str]:
        return sorted(self._qualifiers)

    def followup_cadences(self) -> dict[str, list[int]]:
        """lead_type -> follow-up cadence (days), for the slow loop to compute when
        the next touch is due. A lead type with no cadence is single-touch."""
        return {
            q.lead_type: list(getattr(q, "followup_cadence_days", []) or [])
            for q in self._qualifiers.values()
        }


def build_default_registry(rubric: str) -> QualifierRegistry:
    """Assemble every registered qualifier: the inbound, onboarding, and outbound
    handlers plus the product-led signal qualifiers and the product-led fallback.
    Assumes the conformance suite (run in CI / tests) has passed for each; see
    tests/test_conformance.py."""
    from qualifiers.big_fish.qualifier import BigFishQualifier
    from qualifiers.eng_headcount_growth.qualifier import EngHeadcountGrowthQualifier
    from qualifiers.inbound.qualifier import InboundQualifier
    from qualifiers.job_switcher.qualifier import JobSwitcherQualifier
    from qualifiers.lookalike.qualifier import LookalikeQualifier
    from qualifiers.mrr_fit.qualifier import MrrFitQualifier
    from qualifiers.new_customer.qualifier import NewCustomerQualifier
    from qualifiers.onboarding.qualifier import OnboardingQualifier
    from qualifiers.outbound.qualifier import OutboundQualifier
    from qualifiers.plg_unclassified.qualifier import PlgUnclassifiedQualifier
    from qualifiers.recent_fundraise.qualifier import RecentFundraiseQualifier
    from qualifiers.scale_activation.qualifier import ScaleActivationQualifier
    from qualifiers.spend_spike.qualifier import SpendSpikeQualifier
    from qualifiers.startup_rolloff.qualifier import StartupRolloffQualifier
    from qualifiers.trust_center_nda.qualifier import TrustCenterNdaQualifier
    from qualifiers.unmanaged_ticket.qualifier import UnmanagedTicketQualifier

    registry = QualifierRegistry()
    for qualifier in (
        InboundQualifier(rubric),
        OnboardingQualifier(rubric),
        OutboundQualifier(rubric),
        BigFishQualifier(rubric),
        MrrFitQualifier(rubric),
        SpendSpikeQualifier(rubric),
        StartupRolloffQualifier(rubric),
        NewCustomerQualifier(rubric),
        RecentFundraiseQualifier(rubric),
        LookalikeQualifier(rubric),
        TrustCenterNdaQualifier(rubric),
        UnmanagedTicketQualifier(rubric),
        ScaleActivationQualifier(rubric),
        EngHeadcountGrowthQualifier(rubric),
        JobSwitcherQualifier(rubric),
        PlgUnclassifiedQualifier(rubric),
    ):
        registry.register(qualifier)
    return registry
