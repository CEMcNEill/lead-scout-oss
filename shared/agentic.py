"""AgenticQualifier: the per-play interior as a tool-calling agent.

Overrides only `gather()`: it loads the play's SKILL.md charter, exposes the
toolbox research tools, and lets the model decide which to call until it has
enough — then `BaseQualifier.judge`/`draft` run unchanged on the accumulated
dossier. Grounding is preserved because the agent only ORCHESTRATES the real
tools (which mint Claims with provenance); it never authors a Claim. Bounded by
`max_tool_calls` (turns), `max_tool_invocations` (dispatched calls), and the
per-run cost cap (`MeteredModel`, which trips mid-loop).
"""

from __future__ import annotations

import json
from typing import Any

from shared.contracts import Claim
from shared.model import ModelTier
from shared.qualifier import BaseQualifier, dedup_candidates
from shared.skills import load_charter
from shared.tools.schemas import TOOL_SCHEMAS, dispatch_tool, new_accumulator
from shared.tools.toolbox import Toolbox

_CONTRACT = (
    "\n\n---\nYou are the research interior for this lead type. Call the tools to "
    "build a grounded dossier, then STOP (end your turn with no tool call) once you "
    "have enough to judge. Only call the provided tools. Enrich only the named lead "
    "or contacts on the usage roster. Do not write the email; a separate step drafts."
)


def _dedup_claims(claims: list[Claim]) -> list[Claim]:
    seen: set[str] = set()
    out: list[Claim] = []
    for c in claims:
        if c.id in seen:
            continue
        seen.add(c.id)
        out.append(c)
    return out


class AgenticQualifier(BaseQualifier):
    """Tool-calling interior. Concrete plays set name/lead_type/angle; the SKILL.md
    charter (qualifiers/<name>/SKILL.md) supplies judge + draft guidance, with the
    class attrs as a migration fallback."""

    max_tool_calls: int = 12
    max_tool_invocations: int = 12

    def __init__(self, rubric: str, *, skill_name: str | None = None) -> None:
        super().__init__(rubric)
        self._charter = load_charter(skill_name or self.name)
        # SKILL.md sections become the source of truth for guidance.
        self.judge_guidance = self._charter.sections.get("how to judge") or self.judge_guidance
        self.draft_guidance = self._charter.sections.get("how to draft") or self.draft_guidance

    def _system(self) -> str:
        return (self._charter.raw or f"You handle {self.lead_type} leads.") + _CONTRACT

    def _kickoff(self, record: dict[str, Any]) -> str:
        lead = record.get("lead", {}) or {}
        ctx = {
            "lead_type": self.lead_type,
            "signal": record.get("signal"),
            "lead": {"name": lead.get("name"), "email": lead.get("email"),
                     "title": lead.get("title"), "company": lead.get("company"),
                     "domain": lead.get("domain")},
            "inbound_message": record.get("inbound_message"),
            "has_account": bool(record.get("account_ref")),
        }
        return ("Research this lead with the tools, then stop when you have enough.\n"
                f"Lead context:\n```json\n{json.dumps(ctx, indent=2)}\n```")

    def gather(self, task_id: str, record: dict[str, Any], tools: Toolbox):
        acc = new_accumulator(record, task_id)
        # seed crm ground truth (c1) deterministically before the loop
        acc.dossier += tools.crm_context.read(task_id).claims
        messages: list[dict[str, Any]] = [{"role": "user", "content": self._kickoff(record)}]
        for _ in range(self.max_tool_calls):
            turn = tools.model.run_turn(
                system=self._system(), messages=messages, tools=TOOL_SCHEMAS,
                tier=ModelTier.AGENT_ORCHESTRATION, step=f"{self.lead_type}.agent",
            )
            if turn.stop_reason != "tool_use" or not turn.tool_calls:
                break
            messages.append({"role": "assistant", "content": turn.assistant_content})
            results: list[dict[str, Any]] = []
            for call in turn.tool_calls:
                if acc.invocations >= self.max_tool_invocations:
                    content = "tool budget exhausted; stop and judge with what you have"
                else:
                    content = dispatch_tool(call.name, call.input, tools, acc)
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": content})
            messages.append({"role": "user", "content": results})
        return _dedup_claims(acc.dossier), dedup_candidates(acc.candidates)
