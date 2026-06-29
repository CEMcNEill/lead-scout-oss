"""Clay enrichment via the connected Clay MCP.

Clay has no generic synchronous REST enrichment API, so the engine uses the
Clay MCP tools (find-and-enrich-company, find-and-enrich-list-of-contacts). Those
are the deterministic fetcher layer for person_research and company_research; the
synthesis layer then grounds Claims against what Clay actually returned.

The MCP boundary sits behind a ClayCaller so the rest of the engine (and the
tests) never call the MCP directly. In an agent / Claude Code runtime an
MCP-backed caller is injected; tests use a recorded caller over captured Clay
responses. (A fully headless deploy needs the MCP server reachable, or a swap to
Clay's Enterprise API behind this same interface.)

These tools are asynchronous in Clay: the find-and-enrich call starts a task and
returns base fields, and get-task-context returns enrichment values. A ClayCaller
implementation is responsible for hiding that (start + poll) and returning the
merged result; the normalizers here only care about the final shape.
"""

from __future__ import annotations

import re
from typing import Any, Protocol


class ClayCaller(Protocol):
    def enrich_company(self, domain: str) -> dict[str, Any]:
        """find-and-enrich-company(domain) result."""
        ...

    def enrich_contact(self, name: str, domain: str) -> dict[str, Any]:
        """find-and-enrich-list-of-contacts([{name, domain}]) result."""
        ...


# --- name / domain helpers ----------------------------------------------


def _looks_like_real_name(name: str | None) -> bool:
    if not name:
        return False
    name = name.strip()
    if "@" in name or name.startswith("-"):
        return False
    return " " in name  # a real contact name has a first and last part


def name_from_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local = email.split("@", 1)[0]
    parts = re.split(r"[._\-]+", local)
    parts = [p for p in parts if p and not p.isdigit()]
    return " ".join(p.capitalize() for p in parts) if parts else None


def _domain_of(email: str | None) -> str | None:
    if email and "@" in email:
        return email.rsplit("@", 1)[1].strip().lower()
    return None


# --- normalizers: Clay response -> raw dict the synthesis layer grounds on ---


def normalize_company(resp: dict[str, Any], domain: str) -> dict[str, Any]:
    companies = resp.get("companies") or {}
    company = companies.get(domain)
    if company is None and companies:
        company = next(iter(companies.values()))
    if not company:
        return {"domain": domain, "found": False}
    roster = [
        {
            "name": c.get("name"),
            "title": c.get("latest_experience_title"),
            "company": c.get("latest_experience_company"),
            "linkedin_url": c.get("url"),
            "location": c.get("location_name"),
        }
        for c in resp.get("contacts", [])
    ]
    return {
        "found": True,
        "name": company.get("name"),
        "domain": company.get("domain") or domain,
        "industry": company.get("industry"),
        "employee_count": company.get("employee_count"),
        "size": company.get("size"),
        "type": company.get("type"),
        "country": company.get("country"),
        "locality": company.get("locality"),
        "annual_revenue": company.get("annual_revenue"),
        "funding_range_usd": company.get("total_funding_amount_range_usd"),
        "description": company.get("description"),
        "linkedin_url": company.get("url"),
        "website": company.get("website"),
        "roster": roster,
    }


def normalize_contact(resp: dict[str, Any], name: str, domain: str) -> dict[str, Any]:
    contacts = resp.get("contacts") or []
    if not contacts:
        return {"name": name, "domain": domain, "found": False}
    c = contacts[0]
    return {
        "found": True,
        "name": c.get("name"),
        "title": c.get("latest_experience_title"),
        "company": c.get("latest_experience_company"),
        "linkedin_url": c.get("url"),
        "location": c.get("location_name"),
        "domain": c.get("domain") or domain,
    }


# --- fetchers (implement the toolbox fetcher protocols) ------------------


class ClayCompanyFetcher:
    def __init__(self, caller: ClayCaller) -> None:
        self._caller = caller

    def enrich(self, domain: str) -> dict[str, Any]:
        return normalize_company(self._caller.enrich_company(domain), domain)


class ClayPersonFetcher:
    def __init__(self, caller: ClayCaller) -> None:
        self._caller = caller

    def enrich(self, person_ref: dict[str, Any]) -> dict[str, Any]:
        email = person_ref.get("email")
        domain = person_ref.get("domain") or _domain_of(email) or ""
        name = person_ref.get("name")
        if not _looks_like_real_name(name):
            name = name_from_email(email) or (name or "")
        if not name or not domain:
            return {"name": name, "domain": domain, "found": False}
        return normalize_contact(self._caller.enrich_contact(name, domain), name, domain)


# --- a recorded caller, for tests and for replaying captured Clay data ----


class RecordedClayCaller:
    """Returns pre-captured Clay responses, keyed by domain / (name, domain).
    Used in tests and to replay real Clay MCP output without re-calling it."""

    def __init__(
        self,
        companies: dict[str, dict[str, Any]] | None = None,
        contacts: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self._companies = companies or {}
        self._contacts = contacts or {}

    def enrich_company(self, domain: str) -> dict[str, Any]:
        return self._companies.get(domain, {"companies": {}, "contacts": []})

    def enrich_contact(self, name: str, domain: str) -> dict[str, Any]:
        return self._contacts.get(
            (name, domain), {"contacts": [], "notFoundContacts": [{"contactName": name}]}
        )
