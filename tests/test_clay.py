"""Clay integration tests against recorded Clay MCP responses (no live calls).

Covers company normalization, contact found / not-found, name derivation from a
placeholder email, and the grounded synthesis on top of real Clay data.
"""

import json
from pathlib import Path

from engine.clay import (
    ClayCompanyFetcher,
    ClayPersonFetcher,
    RecordedClayCaller,
    name_from_email,
    normalize_company,
    normalize_contact,
)
from shared.model import FakeModel
from shared.tools.company import CompanyResearchTool
from shared.tools.grounding import IdAllocator
from shared.tools.person import PersonResearchTool

FIX = Path(__file__).resolve().parent / "fixtures" / "clay"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def _caller() -> RecordedClayCaller:
    return RecordedClayCaller(
        companies={"acme.com": _load("company_acme.json")},
        contacts={
            ("Dana Lopez", "acme.com"): _load("contact_found.json"),
            ("Jordan Avery", "acme.com"): _load("contact_notfound.json"),
        },
    )


# --- normalizers ----------------------------------------------------------


def test_normalize_company_maps_real_fields():
    raw = normalize_company(_load("company_acme.json"), "acme.com")
    assert raw["found"] is True
    assert raw["name"] == "Acme"
    assert raw["industry"] == "Software Development"
    assert raw["employee_count"] == 197
    assert raw["funding_range_usd"] == "$25M - $50M"
    # the company enrich also surfaces a roster (useful for buying-group discovery)
    titles = [p["title"] for p in raw["roster"]]
    assert "Chief Product and Technology Officer" in titles


def test_normalize_contact_found_and_not_found():
    found = normalize_contact(_load("contact_found.json"), "Dana Lopez", "acme.com")
    assert found["found"] is True
    assert found["title"] == "Chief Product and Technology Officer"

    missing = normalize_contact(_load("contact_notfound.json"), "Jordan Avery", "acme.com")
    assert missing["found"] is False


def test_name_from_email():
    assert name_from_email("jordan.avery@acme.com") == "Jordan Avery"
    assert name_from_email("itops@acme.com") == "Itops"
    assert name_from_email(None) is None


# --- fetchers -------------------------------------------------------------


def test_company_fetcher_returns_normalized():
    raw = ClayCompanyFetcher(_caller()).enrich("acme.com")
    assert raw["name"] == "Acme" and raw["employee_count"] == 197


def test_person_fetcher_derives_name_from_placeholder_email():
    # SF gives a placeholder name; the fetcher derives "Jordan Avery" from the email
    raw = ClayPersonFetcher(_caller()).enrich(
        {"email": "jordan.avery@acme.com", "name": "- jordan.avery@acme.com"}
    )
    assert raw["found"] is False  # Jordan Avery not on LinkedIn


def test_person_fetcher_found_contact():
    raw = ClayPersonFetcher(_caller()).enrich({"name": "Dana Lopez", "domain": "acme.com"})
    assert raw["found"] is True and "Officer" in raw["title"]


def test_company_fetcher_unknown_domain_is_thin():
    raw = ClayCompanyFetcher(RecordedClayCaller()).enrich("unknown.com")
    assert raw["found"] is False


# --- grounded synthesis on real Clay data --------------------------------


def test_company_synthesis_grounds_in_clay_fields():
    """The synthesis tool may only assert Claims whose raw_keys exist in the
    normalized Clay output."""
    ids = IdAllocator()
    model = FakeModel({
        "company_research.synthesis:acme.com": json.dumps([
            {"field": "icp_industry_fit", "value": "strong (B2B software)",
             "raw_keys": ["industry"], "confidence": 0.8},
            {"field": "segment", "value": "mid-market",
             "raw_keys": ["employee_count"], "confidence": 0.85},
            {"field": "made_up", "value": "x", "raw_keys": ["revenue_growth"], "confidence": 0.5},
        ])
    })
    tool = CompanyResearchTool(ClayCompanyFetcher(_caller()), model, ids)
    result = tool.enrich("acme.com")
    fields = {c.field for c in result.claims}
    assert fields == {"icp_industry_fit", "segment"}  # made_up dropped (ungrounded)
    seg = next(c for c in result.claims if c.field == "segment")
    assert seg.raw == {"employee_count": 197}
