"""Headless person/company enrichment via a configurable REST provider.

Replaces the Clay MCP for the standalone service. Because enrichment vendors
differ, this is config-driven rather than hardcoded to one: a small YAML names
the endpoints, auth header, and a mapping from the vendor's JSON to the
normalized fields the synthesis layer grounds against (name, title, industry,
employee_count, ...). Swapping vendors is a config edit; the fetchers and
qualifiers are untouched. Implements the toolbox Person/Company fetcher seams.

Fill config/enrichment.yaml from config/enrichment.example.yaml and set the API
key in ENRICHMENT_API_KEY. Until configured, the service falls back to the stub.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from shared.tools.grounding import resolve_path

# transport: (url, headers) -> parsed json. Injectable for tests.
EnrichHttp = Callable[[str, dict[str, str]], dict[str, Any]]


def _urllib_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - configured provider endpoint
        return json.loads(resp.read().decode())


@dataclass
class EnrichmentConfig:
    company_url: str  # template with {domain}
    person_url: str  # template with {name}, {domain}, {email}
    auth_header: str  # e.g. "Authorization" or "X-API-KEY"
    auth_value: str  # template with {key}, e.g. "Bearer {key}"
    company_map: dict[str, str]  # our_field -> dotted path into the vendor response
    person_map: dict[str, str]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "EnrichmentConfig":
        data = yaml.safe_load(Path(path).read_text())
        return cls(
            company_url=data["company_url"],
            person_url=data["person_url"],
            auth_header=data.get("auth_header", "Authorization"),
            auth_value=data.get("auth_value", "Bearer {key}"),
            company_map=data.get("company_map", {}),
            person_map=data.get("person_map", {}),
        )


def _apply_map(resp: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field, path in mapping.items():
        found, value = resolve_path(resp, path)
        if found:
            out[field] = value
    return out


class _ProviderBase:
    def __init__(self, config: EnrichmentConfig, api_key: str, http: EnrichHttp | None = None):
        self._config = config
        self._key = api_key
        self._http = http or _urllib_get

    def _headers(self) -> dict[str, str]:
        return {self._config.auth_header: self._config.auth_value.format(key=self._key)}


class ProviderCompanyFetcher(_ProviderBase):
    def enrich(self, domain: str) -> dict[str, Any]:
        url = self._config.company_url.format(domain=urllib.parse.quote(domain))
        resp = self._http(url, self._headers())
        out = _apply_map(resp, self._config.company_map)
        out["found"] = bool(out)  # before adding domain, which is always present
        out["domain"] = domain
        return out


class ProviderPersonFetcher(_ProviderBase):
    def enrich(self, person_ref: dict[str, Any]) -> dict[str, Any]:
        email = person_ref.get("email") or ""
        domain = person_ref.get("domain") or (email.split("@", 1)[1] if "@" in email else "")
        name = person_ref.get("name") or ""
        url = self._config.person_url.format(
            name=urllib.parse.quote(str(name)),
            domain=urllib.parse.quote(str(domain)),
            email=urllib.parse.quote(str(email)),
        )
        resp = self._http(url, self._headers())
        out = _apply_map(resp, self._config.person_map)
        out["found"] = bool(out)
        return out


def build_enrichment_fetchers(
    config_path: str | Path, *, http: EnrichHttp | None = None
) -> tuple[ProviderPersonFetcher, ProviderCompanyFetcher] | None:
    """Build (person, company) provider fetchers if a config + API key exist;
    else None so the caller falls back to the stub."""
    api_key = os.environ.get("ENRICHMENT_API_KEY")
    if not api_key or not Path(config_path).exists():
        return None
    config = EnrichmentConfig.from_yaml(config_path)
    return (
        ProviderPersonFetcher(config, api_key, http),
        ProviderCompanyFetcher(config, api_key, http),
    )
