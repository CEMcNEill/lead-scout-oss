"""Config-driven enrichment provider tests (fake HTTP, no live calls)."""

from engine.enrichment import (
    EnrichmentConfig,
    ProviderCompanyFetcher,
    ProviderPersonFetcher,
)

CONFIG = EnrichmentConfig(
    company_url="https://api.test/company?domain={domain}",
    person_url="https://api.test/person?name={name}&company={domain}",
    auth_header="X-API-KEY",
    auth_value="{key}",
    company_map={"name": "company.name", "industry": "company.industry",
                 "employee_count": "company.size.employees"},
    person_map={"name": "data.0.full_name", "title": "data.0.job_title",
                "seniority": "data.0.seniority"},
)


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        for key, resp in self.responses.items():
            if key in url:
                return resp
        return {}


def test_company_fetcher_maps_nested_fields():
    http = FakeHttp({"/company": {"company": {"name": "Acme",
                                              "industry": "Software",
                                              "size": {"employees": 900}}}})
    raw = ProviderCompanyFetcher(CONFIG, "secret-key", http).enrich("acme.com")
    assert raw["found"] is True
    assert raw["name"] == "Acme"
    assert raw["industry"] == "Software"
    assert raw["employee_count"] == 900
    assert raw["domain"] == "acme.com"
    # auth header applied from template
    assert http.calls[0][1] == {"X-API-KEY": "secret-key"}


def test_person_fetcher_maps_list_indexed_fields():
    http = FakeHttp({"/person": {"data": [{"full_name": "Dana Lopez",
                                           "job_title": "VP Engineering",
                                           "seniority": "vp"}]}})
    raw = ProviderPersonFetcher(CONFIG, "k", http).enrich(
        {"email": "dana@acme.com", "name": "Dana Lopez"}
    )
    assert raw["found"] is True
    assert raw["title"] == "VP Engineering"
    assert raw["seniority"] == "vp"
    # domain derived from email for the URL
    assert "company=acme.com" in http.calls[0][0]


def test_unmatched_response_is_thin_not_found():
    http = FakeHttp({"/company": {}})
    raw = ProviderCompanyFetcher(CONFIG, "k", http).enrich("unknown.com")
    assert raw["found"] is False
    assert raw["domain"] == "unknown.com"


def test_fetchers_satisfy_synthesis_grounding():
    """Provider output grounds Claims through the normal synthesis path."""
    import json

    from shared.model import FakeModel
    from shared.tools.company import CompanyResearchTool
    from shared.tools.grounding import IdAllocator

    http = FakeHttp({"/company": {"company": {"name": "Acme", "industry": "Software",
                                              "size": {"employees": 900}}}})
    model = FakeModel({
        "company_research.synthesis:acme.com": json.dumps([
            {"field": "segment", "value": "mid-market", "raw_keys": ["employee_count"],
             "confidence": 0.8}])
    })
    tool = CompanyResearchTool(ProviderCompanyFetcher(CONFIG, "k", http), model, IdAllocator())
    result = tool.enrich("acme.com")
    assert result.claims[0].raw == {"employee_count": 900}
