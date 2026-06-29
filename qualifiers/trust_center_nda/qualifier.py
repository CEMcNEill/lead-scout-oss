"""Trust-center / NDA qualifier: routed 1:1 from the product-led matching_criteria signal
trust_center_nda.

A prospect who requested trust center access and signed an NDA, a late-stage buying-intent signal.
"""

from __future__ import annotations

from qualifiers.plg_base import ProspectQualifier


class TrustCenterNdaQualifier(ProspectQualifier):
    name = "trust_center_nda"
    lead_type = "trust_center_nda"
    signal = "trust_center_nda"
    angle = "trust-center-intent-led"
    use_usage = True
    judge_guidance = (
        "That is late-stage intent, usually a security or procurement step. Weight the "
        "seriousness of the signal; target the evaluation owner and meet them at the stage "
        "they are in. "
    )
