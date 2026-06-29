"""usage_research tests: Salesforce-backed account resolution + denormalized
PostHog/Vitally fields, and grounded synthesis. Fake query client, no live API.

Models the real situation: the lead's linked account has no usage, and the
account that actually carries usage must be found via the org-id hint and the
email domain (the "three Champ accounts" problem)."""

import json
import re

from engine.usage import SalesforceUsageFetcher
from shared.model import FakeModel
from shared.tools.grounding import IdAllocator
from shared.tools.usage import UsageResearchTool

# account records keyed by Id (subset of the denormalized Account fields)
ACCOUNTS = {
    "001AAA": {"Id": "001AAA", "Name": "Acme", "Posthog_Org_ID__c": "old-org",
               "posthog_total_events_30d__c": 0},
    "001B": {"Id": "001B", "Name": "Champ", "Posthog_Org_ID__c": "01934d12",
             "posthog_total_events_30d__c": 154568, "posthog_events_30d_momentum__c": -12.33,
             "posthog_products_30d__c": "error_tracking,feature_flags,recordings",
             "vitally_paid_invoice_count__c": 1, "vitally_last_invoice_amount__c": 5.93,
             "Vitally_Forecasted_MRR__c": None,
             "Vitally_account_URL__c": "https://vitally.io/accounts/champ"},
}
CONTACTS_BY_DOMAIN = {"acme.com": ["001AAA", "001B"]}
ORG_TO_ACCOUNT = {"01934d12": ["001B"]}


class FakeQueryClient:
    """Pattern-matches the SalesforceUsageFetcher's SOQL and returns canned rows."""

    def __init__(self, accounts=None, by_domain=None, by_org=None):
        self.accounts = accounts if accounts is not None else ACCOUNTS
        self.by_domain = by_domain if by_domain is not None else CONTACTS_BY_DOMAIN
        self.by_org = by_org if by_org is not None else ORG_TO_ACCOUNT

    def current_user_id(self) -> str:
        return "005ME"

    def query(self, soql: str):
        if "FROM Contact WHERE Email LIKE" in soql:
            domain = re.search(r"@([^']+)'", soql).group(1)
            return [{"AccountId": a} for a in self.by_domain.get(domain, [])]
        if "FROM Account WHERE Posthog_Org_ID__c =" in soql:
            org = re.search(r"Posthog_Org_ID__c = '([^']+)'", soql).group(1)
            return [{"Id": a} for a in self.by_org.get(org, [])]
        if "FROM Account WHERE Name =" in soql:
            name = re.search(r"Name = '([^']+)'", soql).group(1).lower()
            return [{"Id": a["Id"]} for a in self.accounts.values()
                    if (a.get("Name") or "").lower() == name]
        if "FROM Account WHERE Id IN" in soql:
            ids = re.findall(r"'([^']+)'", soql.split("Id IN", 1)[1])
            return [self.accounts[i] for i in ids if i in self.accounts]
        raise AssertionError(f"unexpected SOQL: {soql}")


def _record():
    return {
        "account_ref": "001AAA",  # linked account has no usage
        "org_hints": ["01934d12"],  # task/contact org id points at the real one
        "lead": {"company": "Acme", "domain": "acme.com"},
    }


def test_trusted_org_id_resolves_directly_no_ambiguity():
    # the PostHog org id is trusted: resolve straight to its account, not the
    # linked 001AAA, and no candidate cross-check / ambiguity prompt.
    raw = SalesforceUsageFetcher(FakeQueryClient()).query(_record())
    assert raw["found"] is True
    assert raw["account_id"] == "001B"
    assert raw["events_30d"] == 154568
    assert raw["events_30d_momentum_pct"] == -12.33
    assert raw["products_30d"] == ["error_tracking", "feature_flags", "recordings"]
    assert raw["last_invoice_amount"] == 5.93
    assert raw["vitally_account_url"] == "https://vitally.io/accounts/champ"
    res = raw["resolution"]
    assert res["chosen_account_id"] == "001B"
    assert res["ambiguous"] is False
    assert {c["id"] for c in res["candidates"]} == {"001B"}  # single, trusted
    assert "trusted" in res["reason"]


def test_falls_back_to_linked_account_when_no_usage():
    accounts = {"001AAA": {"Id": "001AAA", "Name": "Acme",
                           "posthog_total_events_30d__c": 0}}
    client = FakeQueryClient(accounts=accounts, by_domain={"acme.com": ["001AAA"]},
                             by_org={})
    raw = SalesforceUsageFetcher(client).query(_record())
    assert raw["found"] is True
    assert raw["account_id"] == "001AAA"
    assert "no candidate had usage" in raw["resolution"]["reason"]


def test_ambiguous_only_when_no_org_id_falls_back_to_cross_check():
    # no org id -> fall back to name/domain cross-check, which can be ambiguous
    accounts = dict(ACCOUNTS)
    accounts["001D"] = {"Id": "001D", "Name": "Champ", "posthog_total_events_30d__c": 99999}
    client = FakeQueryClient(accounts=accounts,
                             by_domain={"acme.com": ["001AAA", "001B", "001D"]})
    record = {"account_ref": "001AAA", "lead": {"company": "Acme",
                                                "domain": "acme.com"}}  # no org_hints
    raw = SalesforceUsageFetcher(client).query(record)
    assert raw["account_id"] == "001B"  # highest events wins
    assert raw["resolution"]["ambiguous"] is True


def test_no_candidates_returns_not_found():
    client = FakeQueryClient(accounts={}, by_domain={}, by_org={})
    raw = SalesforceUsageFetcher(client).query(
        {"account_ref": None, "org_hints": [], "lead": {}}
    )
    assert raw["found"] is False


def test_synthesis_grounds_usage_and_billing_claims():
    ids = IdAllocator()
    model = FakeModel({
        "usage_research.synthesis:001AAA": json.dumps([
            {"field": "monthly_event_volume", "value": 154568, "raw_keys": ["events_30d"],
             "confidence": 0.99},
            {"field": "trajectory", "value": "declining ~12% MoM",
             "raw_keys": ["events_30d_momentum_pct"], "confidence": 0.9},
            {"field": "products_touched", "value": ["error_tracking", "feature_flags", "recordings"],
             "raw_keys": ["products_30d"], "confidence": 0.95},
            {"field": "plan_and_billing", "value": "PAYG, tiny invoices (~$6)",
             "raw_keys": ["paid_invoice_count", "last_invoice_amount"], "confidence": 0.85},
        ])
    })
    tool = UsageResearchTool(SalesforceUsageFetcher(FakeQueryClient()), model, ids)
    result = tool.query(_record())  # label uses account_ref 001AAA
    fields = {c.field: c for c in result.claims}
    # the four model-derived claims; no resolution claim, since the trusted org id
    # resolves to a single account (no ambiguity to surface)
    assert {"monthly_event_volume", "trajectory", "products_touched",
            "plan_and_billing"} <= set(fields)
    assert "usage_account_resolution" not in fields
    assert fields["trajectory"].raw == {"events_30d_momentum_pct": -12.33}
    assert fields["plan_and_billing"].raw["last_invoice_amount"] == 5.93
