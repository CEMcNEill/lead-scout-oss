"""Unclassified product-led qualifier: routed 1:1 from the product-led matching_criteria signal
(fallback: any unmapped product-led value).

A product-led lead whose matching_criteria value is not in the known set. A safety net so new Salesforce values still get a faithful account-first pass; the unmapped value should be triaged into its own signal over time.
"""

from __future__ import annotations

from typing import Any

from qualifiers.plg_base import AccountFirstQualifier


class PlgUnclassifiedQualifier(AccountFirstQualifier):
    name = "plg_unclassified"
    lead_type = "plg_unclassified"
    angle = "plg-use-case-led"
    judge_guidance = (
        "The matching criteria did not map to a known signal. Treat it as a general "
        "product-led account: weight usage and fit holistically and target the right contact "
        "from the roster. "
    )

    def matches(self, record: dict[str, Any]) -> bool:
        """Any product-led lead whose signal did not map to a known one."""
        return record.get("category") == "product-led" and not record.get("signal")
