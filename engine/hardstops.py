"""Eligibility hard-stops: a small set of categorical correctness checks the
shell runs before invoking a qualifier.

Do-not-contact, a competitor, an account already managed by a teammate, a
non-business/personal address, or an outbound lead still in a live external
sequence. Rare, binary, decided off data. Everything that is not a hard-stop is
evidence for the qualifier to weigh, not grounds to stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from shared.tools.grounding import resolve_path


@dataclass
class HardStopConfig:
    personal_email_domains: set[str] = field(default_factory=set)
    competitor_domains: set[str] = field(default_factory=set)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "HardStopConfig":
        data = yaml.safe_load(Path(path).read_text()) or {}
        return cls(
            personal_email_domains={d.lower() for d in data.get("personal_email_domains", [])},
            competitor_domains={d.lower() for d in data.get("competitor_domains", [])},
        )


def _domain_of(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower()


def check_hard_stops(record: dict[str, Any], config: HardStopConfig) -> list[str]:
    """Return the hard-stop labels that fire for this lead. Usually empty."""
    stops: list[str] = []

    found, dnc = resolve_path(record, "lead.do_not_contact")
    if found and dnc:
        stops.append("do_not_contact")

    found, is_comp = resolve_path(record, "lead.is_competitor")
    domain = _domain_of(resolve_path(record, "lead.email")[1])
    company_domain = (resolve_path(record, "lead.domain")[1] or "")
    company_domain = company_domain.strip().lower() if isinstance(company_domain, str) else ""
    if (found and is_comp) or (domain in config.competitor_domains) or (
        company_domain in config.competitor_domains
    ):
        stops.append("competitor")

    found, other_rep = resolve_path(record, "lead.owner_other_rep")
    if found and other_rep:
        stops.append("teammate_managed")

    # outbound leads already live in an external lemlist/slack sequence; never
    # stage a parallel human touch into one that is still running.
    found, in_seq = resolve_path(record, "lead.active_sequence")
    if found and in_seq:
        stops.append("active_sequence")

    if domain and domain in config.personal_email_domains:
        stops.append("personal_address")

    return stops
