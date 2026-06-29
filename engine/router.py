"""Router: declarative, data-driven dispatch from a registry of rules.

Reads qualifiers/registry.yaml and returns the Route (lead_type + qualifier
name) for a CRM record. Deterministic: criteria read only fields already on the
record, evaluated top to bottom, first match wins. Binding a qualifier name to
its implementation happens in the qualifier registry, after conformance; routing
only decides which name handles the lead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from shared.contracts import Route
from shared.tools.grounding import resolve_path


def _check_condition(cond: dict[str, Any], record: dict[str, Any]) -> bool:
    field = cond["field"]
    found, value = resolve_path(record, field)
    if "exists" in cond:
        return found == bool(cond["exists"])
    if "equals" in cond:
        return found and value == cond["equals"]
    if "in" in cond:
        return found and value in cond["in"]
    raise ValueError(f"unrecognized criterion: {cond!r}")


def _match(criteria: dict[str, Any], record: dict[str, Any]) -> bool:
    if "all" in criteria:
        return all(_check_condition(c, record) for c in criteria["all"])
    if "any" in criteria:
        conds = criteria["any"]
        return any(_check_condition(c, record) for c in conds) if conds else True
    raise ValueError(f"criteria must have 'all' or 'any': {criteria!r}")


@dataclass(frozen=True)
class Rule:
    qualifier: str
    lead_type: str
    criteria: dict[str, Any]


class Router:
    def __init__(self, rules: list[Rule]) -> None:
        if not rules:
            raise ValueError("router needs at least one rule")
        self._rules = rules

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Router":
        data = yaml.safe_load(Path(path).read_text())
        rules = [
            Rule(qualifier=r["qualifier"], lead_type=r["lead_type"], criteria=r["criteria"])
            for r in data["rules"]
        ]
        return cls(rules)

    def route(self, record: dict[str, Any]) -> Route:
        for rule in self._rules:
            if _match(rule.criteria, record):
                return Route(lead_type=rule.lead_type, qualifier=rule.qualifier)
        # rules should always include a catch-all; if not, that is a config error
        raise LookupError("no routing rule matched and no catch-all is configured")
