"""Real Salesforce integration: a REST/SOQL client, the open-lead-task poll, and
the CRM fetcher that replaces the Phase 1 stub.

The engine authenticates as the rep (SalesforceAuth), so every query runs with
the rep's own access and Salesforce sharing rules scope the results to that rep's
leads. The client refreshes silently on a 401 and follows query pagination. The
task source returns the ids of the rep's open lead tasks; the CRM fetcher reads
one task plus its related Lead and shapes it into the record the router,
hard-stops, and crm_context tool already expect.

Org-specific fields (the routing trigger and, for product-led, the account
reference) live on custom fields that vary by org, so they are resolved through a
configurable SfFieldMap rather than hardcoded. Standard fields (name, email,
title, company, lead source, do-not-call, owner) map directly.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Protocol

# Salesforce reports an inaccessible/unknown field as "No such column 'X' on
# entity 'Y'". Used to adaptively drop fields the org's profile cannot read.
_NO_SUCH_COLUMN = re.compile(r"No such column '([^']+)'")

from engine.sf_auth import (
    HttpError,
    HttpTransport,
    KeychainSecretStore,
    SalesforceAuth,
    SecretStore,
    UrllibTransport,
)
from shared.contracts import RepConfig
from shared.signals import (
    OUTBOUND_SUBJECT_TAGS,
    PRODUCT_LED,
    resolve_category,
    resolve_signal,
)

DEFAULT_REDIRECT_URI = "http://localhost:8765/callback"
DEFAULT_API_VERSION = "v60.0"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; required for the Salesforce integration")
    return value


def build_auth_from_env(
    *, secret_store: SecretStore | None = None, transport: HttpTransport | None = None
) -> SalesforceAuth:
    """Assemble SalesforceAuth from the environment. Secrets (the refresh token)
    live in the macOS Keychain, never in .env; only the External Client App's
    client id/secret and the login URL come from the environment."""
    return SalesforceAuth(
        client_id=_required_env("SF_CLIENT_ID"),
        client_secret=_required_env("SF_CLIENT_SECRET"),
        redirect_uri=os.environ.get("SF_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        sf_username=_required_env("SF_USERNAME"),
        login_url=os.environ.get("SF_LOGIN_URL", "https://login.salesforce.com"),
        secret_store=secret_store or KeychainSecretStore(),
        transport=transport or UrllibTransport(),
    )


def build_client_from_env(
    *, secret_store: SecretStore | None = None, transport: HttpTransport | None = None
) -> SalesforceClient:
    return SalesforceClient(
        build_auth_from_env(secret_store=secret_store, transport=transport),
        api_version=os.environ.get("SF_API_VERSION", DEFAULT_API_VERSION),
    )


class QueryClient(Protocol):
    """The minimal client surface the task source and CRM fetcher need. Both the
    REST client (External Client App) and the CLI-backed client satisfy it, so
    the rest of the integration is auth-agnostic."""

    def query(self, soql: str) -> list[dict[str, Any]]: ...
    def current_user_id(self) -> str: ...


class SalesforceClient:
    """A thin rep-scoped SOQL client over the REST API."""

    def __init__(
        self,
        auth: SalesforceAuth,
        *,
        transport: HttpTransport | None = None,
        api_version: str = "v60.0",
    ) -> None:
        self._auth = auth
        self._http = transport or UrllibTransport()
        self._api = api_version

    def current_user_id(self) -> str:
        """The authenticated rep's Salesforce user id (from the token identity)."""
        token = self._auth.access_token()
        if not token.user_id:
            raise RuntimeError(
                "access token has no identity URL; cannot resolve the current user id"
            )
        return token.user_id

    def query(self, soql: str) -> list[dict[str, Any]]:
        """Run a SOQL query and return all records, following pagination. Refreshes
        once on a 401 and retries."""
        return self._query(soql, allow_refresh=True)

    def _query(self, soql: str, *, allow_refresh: bool) -> list[dict[str, Any]]:
        token = self._auth.access_token()
        url = (
            f"{token.instance_url}/services/data/{self._api}/query/"
            f"?q={urllib.parse.quote(soql)}"
        )
        try:
            data = self._http.get_json(url, self._headers(token))
        except HttpError as exc:
            if exc.status == 401 and allow_refresh:
                self._auth.access_token(force_refresh=True)
                return self._query(soql, allow_refresh=False)
            raise

        records = list(data.get("records", []))
        while not data.get("done", True) and data.get("nextRecordsUrl"):
            token = self._auth.access_token()
            data = self._http.get_json(
                f"{token.instance_url}{data['nextRecordsUrl']}", self._headers(token)
            )
            records.extend(data.get("records", []))
        return records

    @staticmethod
    def _headers(token) -> dict[str, str]:
        return {"Authorization": f"Bearer {token.access_token}", "Accept": "application/json"}


def _soql_escape(value: str) -> str:
    """Escape a value for a single-quoted SOQL literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _subject_tag(subject: str | None) -> str | None:
    """The leading bracketed tag in a Task subject, e.g. "[Product-led] ..." ->
    "Product-led". This org tags the lead source there; it is the routing token
    when no custom field carries it."""
    match = re.match(r"\s*\[([^\]]+)\]", subject or "")
    return match.group(1).strip() if match else None


def adaptive_select(client: QueryClient, fields: list[str], suffix: str) -> list[dict[str, Any]]:
    """Run a SELECT, adaptively dropping any field the org reports as inaccessible
    (field-level security varies by org/profile) and retrying. Keeps the
    integration working across orgs without hardcoding a schema."""
    fields = list(fields)
    while fields:
        try:
            return client.query(f"SELECT {', '.join(fields)} {suffix}")
        except Exception as exc:  # noqa: BLE001 - inspect message for missing column
            match = _NO_SUCH_COLUMN.search(str(exc))
            if not match:
                raise
            bad = match.group(1).lower()
            pruned = [f for f in fields if f.split(".")[-1].lower() != bad]
            if pruned == fields:  # nothing to drop -> avoid an infinite loop
                raise
            fields = pruned
    return []


class SalesforceTaskSource:
    """Polls the rep's open lead tasks.

    In this org a "lead" is a Task related to a Contact (not a Lead object) that
    the rep treats as a lead. So this polls open Tasks owned by the rep whose Who
    is a Contact. `extra_where` lets the org narrow that to the subset the rep
    actually considers leads (e.g. a Task Type or a custom field)."""

    def __init__(
        self, client: QueryClient, *, status: str = "Open", extra_where: str | None = None
    ) -> None:
        self._client = client
        self._status = status
        self._extra_where = extra_where

    def poll(self, rep_config: RepConfig) -> list[str]:
        # only Status = 'Open' tasks: In Progress / Nurturing / Completed are
        # excluded (In Progress and Nurturing handled in a later phase).
        owner_id = self._client.current_user_id()
        clauses = [
            f"OwnerId = '{_soql_escape(owner_id)}'",
            f"Status = '{_soql_escape(self._status)}'",
            "Who.Type = 'Contact'",
        ]
        # outbound is rep/tool initiated; this engine never handles it, so exclude
        # those tasks here rather than route them away later.
        for tag in OUTBOUND_SUBJECT_TAGS:
            clauses.append(f"(NOT Subject LIKE '[{_soql_escape(tag)}]%')")
        if self._extra_where:
            clauses.append(f"({self._extra_where})")
        soql = "SELECT Id FROM Task WHERE " + " AND ".join(clauses) + " ORDER BY CreatedDate ASC"
        return [r["Id"] for r in self._client.query(soql)]


@dataclass
class SfFieldMap:
    """Org-specific field resolution. The routing trigger and the product-led
    account reference may live on custom fields whose API names vary by org; set
    them here. Defaults are conservative: no custom trigger field (routing falls
    back to LeadSource and inbound-message presence) and the account reference
    defaults to the Contact's AccountId."""

    trigger_task_field: str | None = None  # e.g. a custom "Trigger__c" on the Task
    trigger_contact_field: str | None = None  # e.g. a custom field on the Contact
    account_ref_contact_field: str | None = None  # else falls back to AccountId


# Contact fields (and the related Account) the CRM read pulls. Standard fields,
# read directly from the Contact since Who is always a Contact here.
_CONTACT_FIELDS = [
    "Id", "Name", "Email", "Title", "LeadSource", "OwnerId",
    "DoNotCall", "HasOptedOutOfEmail", "AccountId",
    "Account.Name", "Account.Website", "Account.Industry",
    "Posthog_Org_ID__c",  # org hint for usage account resolution
]


class SalesforceCrmFetcher:
    """Reads one Task plus its related Contact (and the Contact's Account) and
    shapes it into the engine record. Implements the CrmFetcher protocol,
    replacing StubCrmFetcher. Two queries: the Task (for subject, description, and
    the Contact id) and the Contact (for the person and company)."""

    def __init__(self, client: QueryClient, *, field_map: SfFieldMap | None = None) -> None:
        self._client = client
        self._map = field_map or SfFieldMap()

    def read(self, task_id: str) -> dict[str, Any]:
        task_fields = [
            "Id", "Subject", "Description", "WhoId", "posthog_org_id__c",
            "Lead_Source__c",  # top-level category signal (this org's routing field)
            "matching_criteria__c",  # product-led sub-signal (closed set)
        ]
        if self._map.trigger_task_field:
            task_fields.append(self._map.trigger_task_field)
        task_rows = self._select(task_fields, f"FROM Task WHERE Id = '{_soql_escape(task_id)}'")
        if not task_rows:
            raise KeyError(f"no Salesforce Task with id {task_id!r}")
        task = task_rows[0]

        contact: dict[str, Any] = {}
        who_id = task.get("WhoId")
        if who_id:
            contact_fields = list(_CONTACT_FIELDS)
            if self._map.trigger_contact_field:
                contact_fields.append(self._map.trigger_contact_field)
            if self._map.account_ref_contact_field:
                contact_fields.append(self._map.account_ref_contact_field)
            contact_rows = self._select(
                contact_fields, f"FROM Contact WHERE Id = '{_soql_escape(who_id)}'"
            )
            if contact_rows:
                contact = contact_rows[0]
        return self._shape(task, contact)

    def _select(self, fields: list[str], suffix: str) -> list[dict[str, Any]]:
        return adaptive_select(self._client, fields, suffix)

    def _shape(self, task: dict[str, Any], contact: dict[str, Any]) -> dict[str, Any]:
        account = contact.get("Account") or {}
        owner_id = contact.get("OwnerId")
        my_id = self._safe_user_id()
        email = contact.get("Email")

        org_hints = [
            v for v in (task.get("posthog_org_id__c"), contact.get("Posthog_Org_ID__c")) if v
        ]
        category = self._resolve_category(task, contact)
        record: dict[str, Any] = {
            "task_id": task.get("Id"),
            "subject": task.get("Subject"),
            "trigger": self._resolve_trigger(task, contact),
            "category": category,  # top-level: product-led | inbound | onboarding | outbound
            # product-led sub-signal (None for non-product-led or an unmapped value)
            "signal": resolve_signal(task.get("matching_criteria__c"))
            if category == PRODUCT_LED
            else None,
            "account_ref": self._resolve_account_ref(contact),
            "org_hints": org_hints,  # PostHog org ids to seed usage account resolution
            "lead": {
                "name": contact.get("Name"),
                "email": email,
                "title": contact.get("Title"),
                "company": account.get("Name"),
                "domain": _domain_from(email, account.get("Website")),
                "lead_source": contact.get("LeadSource"),
                "do_not_contact": bool(
                    contact.get("DoNotCall") or contact.get("HasOptedOutOfEmail")
                ),
                "owner_other_rep": bool(owner_id and my_id and owner_id != my_id),
            },
        }
        description = task.get("Description")
        if description:
            record["inbound_message"] = description
        return record

    def _resolve_trigger(self, task: dict[str, Any], contact: dict[str, Any]) -> str | None:
        if self._map.trigger_task_field and task.get(self._map.trigger_task_field):
            return str(task[self._map.trigger_task_field]).strip().lower()
        if self._map.trigger_contact_field and contact.get(self._map.trigger_contact_field):
            return str(contact[self._map.trigger_contact_field]).strip().lower()
        # this org tags the source in the Task subject, e.g. "[Product-led] ...".
        # the leading bracketed tag is the routing signal.
        tag = _subject_tag(task.get("Subject"))
        return tag.lower() if tag else None

    def _resolve_category(self, task: dict[str, Any], contact: dict[str, Any]) -> str | None:
        """Top-level category from the source token, tried in order of fidelity:
        the Task Lead_Source__c, then the bracketed Subject tag, then the Contact
        LeadSource. The first that maps to a known category wins."""
        for token in (
            task.get("Lead_Source__c"),
            _subject_tag(task.get("Subject")),
            contact.get("LeadSource"),
        ):
            category = resolve_category(token)
            if category:
                return category
        return None

    def _resolve_account_ref(self, contact: dict[str, Any]) -> str | None:
        field = self._map.account_ref_contact_field
        if field and contact.get(field):
            return contact[field]
        return contact.get("AccountId")

    def _safe_user_id(self) -> str | None:
        try:
            return self._client.current_user_id()
        except RuntimeError:
            return None


def _domain_from(email: str | None, website: str | None) -> str | None:
    if email and "@" in email:
        return email.rsplit("@", 1)[1].strip().lower()
    if website:
        host = website.strip().lower()
        host = host.split("://", 1)[-1]  # drop scheme
        host = host.split("/", 1)[0]  # drop path
        return host[4:] if host.startswith("www.") else host
    return None
