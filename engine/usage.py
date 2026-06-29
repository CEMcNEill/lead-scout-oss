"""usage_research backed by Salesforce's denormalized PostHog + Vitally fields.

PostHog product usage and Vitally/Stripe billing are already synced onto the
Salesforce Account, so usage is a real synchronous read, no separate API. The
meaningful work is resolving the RIGHT account: a company often has several
Account records with different PostHog org ids, and only one carries real usage
(observed live: three "Champ" accounts, one with 154k events, two empty). So the
fetcher gathers candidate accounts from several signals, never trusting a single
org-id field, and picks the one that actually has usage, recording how it chose.

What it returns feeds the product-led qualifiers: event volume, trajectory
(precomputed momentum), products touched, engagement, and the plan/billing
context (MRR, invoices) that makes "big fish on free" and "rolloff" real,
grounded triggers. The per-user roster is not on the Account; it is a follow-on
via the Vitally API, so v1 leaves the roster empty and PLG targets the named
contact.
"""

from __future__ import annotations

from typing import Any

from engine.salesforce import QueryClient, _soql_escape, adaptive_select

# Denormalized usage + billing fields on the Account (PostHog + Vitally/Stripe).
_ACCOUNT_USAGE_FIELDS = [
    "Id", "Name", "Posthog_Org_ID__c", "Posthog_Associated_Org_IDs__c",
    "posthog_total_events_30d__c", "posthog_events_30d_momentum__c",
    "posthog_active_users_30d__c", "posthog_active_users_30d_momentum__c",
    "posthog_products_30d__c", "posthog_last_login_days__c",
    "Vitally_Forecasted_MRR__c", "vitally_paid_invoice_count__c",
    "vitally_last_invoice_amount__c", "Vitally_account_URL__c",
]


def _events(account: dict[str, Any]) -> float:
    return account.get("posthog_total_events_30d__c") or 0


class SalesforceUsageFetcher:
    """Implements the UsageFetcher protocol over Salesforce account data.
    `query` accepts the CRM record (for resolution signals); a bare account id is
    also accepted for convenience."""

    def __init__(self, client: QueryClient) -> None:
        self._client = client

    def query(self, ref: Any) -> dict[str, Any]:
        record = {"account_ref": ref} if isinstance(ref, str) else (ref or {})

        # Trust the PostHog org id when present: resolve the account directly by
        # it, no name/domain cross-check and no ambiguity prompt. The multi-
        # candidate resolution below is only the fallback for a missing org id.
        trusted = self._resolve_by_org(record.get("org_hints") or [])
        if trusted is not None:
            return self._shape(trusted, [trusted], False, "resolved by trusted PostHog org id")

        candidates = self._gather_candidates(record)
        if not candidates:
            return {"account_ref": record.get("account_ref"), "found": False, "roster": []}
        accounts = self._read_accounts(candidates)
        chosen, ambiguous, reason = self._choose(accounts, record.get("account_ref"))
        if chosen is None:
            return {"account_ref": record.get("account_ref"), "found": False, "roster": []}
        return self._shape(chosen, accounts, ambiguous, reason)

    # --- resolution ------------------------------------------------------

    def _resolve_by_org(self, org_hints: list[str]) -> dict[str, Any] | None:
        """The account for a trusted PostHog org id. Returns None if no org id is
        given or none matches, so the caller falls back to cross-checking."""
        ids: set[str] = set()
        for org in org_hints:
            rows = adaptive_select(
                self._client, ["Id"],
                f"FROM Account WHERE Posthog_Org_ID__c = '{_soql_escape(org)}' "
                f"OR Posthog_Associated_Org_IDs__c LIKE '%{_soql_escape(org)}%'",
            )
            ids.update(r["Id"] for r in rows)
        if not ids:
            return None
        accounts = self._read_accounts(ids)
        if not accounts:
            return None
        # one account per org id is expected; if several, prefer one with usage
        with_usage = [a for a in accounts if _events(a) > 0]
        return max(with_usage, key=_events) if with_usage else accounts[0]

    def _gather_candidates(self, record: dict[str, Any]) -> set[str]:
        ids: set[str] = set()
        if record.get("account_ref"):
            ids.add(record["account_ref"])

        lead = record.get("lead", {})
        for org in record.get("org_hints", []):
            rows = adaptive_select(
                self._client, ["Id"],
                f"FROM Account WHERE Posthog_Org_ID__c = '{_soql_escape(org)}' "
                f"OR Posthog_Associated_Org_IDs__c LIKE '%{_soql_escape(org)}%'",
            )
            ids.update(r["Id"] for r in rows)

        company = lead.get("company")
        if company:
            rows = adaptive_select(
                self._client, ["Id"], f"FROM Account WHERE Name = '{_soql_escape(company)}'"
            )
            ids.update(r["Id"] for r in rows)

        domain = lead.get("domain")
        if domain:
            rows = adaptive_select(
                self._client, ["AccountId"],
                f"FROM Contact WHERE Email LIKE '%@{_soql_escape(domain)}' AND AccountId != null",
            )
            ids.update(r["AccountId"] for r in rows if r.get("AccountId"))
        return ids

    def _read_accounts(self, ids: set[str]) -> list[dict[str, Any]]:
        quoted = ", ".join(f"'{_soql_escape(i)}'" for i in ids)
        return adaptive_select(
            self._client, _ACCOUNT_USAGE_FIELDS, f"FROM Account WHERE Id IN ({quoted})"
        )

    def _choose(
        self, accounts: list[dict[str, Any]], linked_id: str | None
    ) -> tuple[dict[str, Any] | None, bool, str]:
        with_usage = [a for a in accounts if _events(a) > 0]
        if with_usage:
            chosen = max(with_usage, key=_events)
            return chosen, len(with_usage) > 1, "highest 30d events among candidates with usage"
        # no usage on any candidate: prefer the linked account, else the first
        linked = next((a for a in accounts if a.get("Id") == linked_id), None)
        if linked is not None:
            return linked, False, "no candidate had usage; used the linked account"
        return (accounts[0] if accounts else None), False, "no usage; used first candidate"

    def _shape(
        self, account: dict[str, Any], all_accounts: list[dict[str, Any]],
        ambiguous: bool, reason: str,
    ) -> dict[str, Any]:
        products = account.get("posthog_products_30d__c")
        return {
            "found": True,
            "account_id": account.get("Id"),
            "account_name": account.get("Name"),
            "org_id": account.get("Posthog_Org_ID__c"),
            "events_30d": account.get("posthog_total_events_30d__c"),
            "events_30d_momentum_pct": account.get("posthog_events_30d_momentum__c"),
            "active_users_30d": account.get("posthog_active_users_30d__c"),
            "active_users_30d_momentum_pct": account.get("posthog_active_users_30d_momentum__c"),
            "products_30d": [p.strip() for p in products.split(",")] if products else [],
            "last_login_days": account.get("posthog_last_login_days__c"),
            "forecasted_mrr": account.get("Vitally_Forecasted_MRR__c"),
            "paid_invoice_count": account.get("vitally_paid_invoice_count__c"),
            "last_invoice_amount": account.get("vitally_last_invoice_amount__c"),
            "vitally_account_url": account.get("Vitally_account_URL__c"),
            "resolution": {
                "chosen_account_id": account.get("Id"),
                "reason": reason,
                "ambiguous": ambiguous,
                "candidates": [
                    {"id": a.get("Id"), "name": a.get("Name"),
                     "org_id": a.get("Posthog_Org_ID__c"),
                     "events_30d": a.get("posthog_total_events_30d__c")}
                    for a in all_accounts
                ],
            },
            "roster": [],  # not on the Account; future via the Vitally API
        }
