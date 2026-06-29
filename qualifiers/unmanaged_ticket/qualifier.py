"""Unmanaged-ticket qualifier: routed 1:1 from the product-led matching_criteria signal
unmanaged_ticket.

A large customer (20k+) with no CSM who just raised a support ticket.
"""

from __future__ import annotations

from qualifiers.plg_base import AccountFirstQualifier


class UnmanagedTicketQualifier(AccountFirstQualifier):
    name = "unmanaged_ticket"
    lead_type = "unmanaged_ticket"
    signal = "unmanaged_ticket"
    angle = "plg-unmanaged-ticket-led"
    judge_guidance = (
        "The ticket is an opening to offer hands-on help. Weight account size and the "
        "ticket's substance; target the person who can own the relationship. "
    )
