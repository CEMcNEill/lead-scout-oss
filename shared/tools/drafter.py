"""drafter — the shared drafting tool.

Given the dossier, the disposition, and a framing angle, produces a draft in the
rep's voice addressed to the disposition's target. Voice is a living rules doc
(per rep) plus a bank of labeled real sends retrieved by lead type: rules anchor,
examples teach cadence. The draft leads with the use case and the pain it solves,
not a feature list.

In Phase 1 this returns a Draft object; the shell writes it to a stub sink. In
Phase 1.5 the same object becomes a real Gmail draft in the rep's account.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from shared.contracts import Claim, Disposition, Draft
from shared.model import ModelClient, ModelTier, parse_json

_SYSTEM = (
    "You are a drafting tool writing outreach in a specific rep's voice. You are "
    "given the rep's voice rules, a few exemplar sends, a grounded dossier of "
    "Claims, the disposition, and a framing angle. Write a short email that leads "
    "with the use case and the pain it solves, not a feature list. Use ONLY facts "
    "present in the dossier Claims; cite the claim ids you relied on. Match the "
    "voice rules and the cadence of the exemplars. For any scheduling or booking "
    "link (for example 'my calendar'), use the rep's booking link EXACTLY as given, "
    "verbatim. Never invent, guess, or alter a booking URL; if no booking link is "
    "given, do not include one. Address the recipient by first name only if a name "
    "is given in the Target contact; if the name is null or unknown, greet without a "
    "name (for example \"Hey -\"). Never guess a name from an email address. Return "
    'JSON: {"subject": str, "body": str, "claims_used": [claim_id, ...]}.'
)


def _name_is_grounded(first_name: str, dossier: list[Claim]) -> bool:
    """True if the recipient's first name appears as a whole word in some Claim
    value. Mirrors what the boundary fact-check gate would accept, so the drafter
    only greets by name when that name is grounded."""
    fn = first_name.strip().lower()
    if not fn:
        return False
    pattern = re.compile(rf"\b{re.escape(fn)}\b")
    for c in dossier:
        if pattern.search(str(c.value).lower()):
            return True
    return False


class DrafterTool:
    def __init__(
        self,
        model: ModelClient,
        *,
        voice_profile: str,
        exemplars: list[str],
        signature: str = "",
        calendar_url: str = "",
    ) -> None:
        self._model = model
        self._voice_profile = voice_profile
        self._exemplars = exemplars
        self._signature = signature
        self._calendar_url = calendar_url.strip()

    def draft(self, dossier: list[Claim], disposition: Disposition, angle: str) -> Draft:
        prompt = self._build_prompt(dossier, disposition, angle)
        resp = self._model.complete(
            system=_SYSTEM,
            prompt=prompt,
            tier=ModelTier.DRAFTER,
            step="drafter",
        )
        payload = parse_json(resp.text)
        body = self._fix_calendar_links(payload["body"])
        if self._signature and self._signature not in body:
            body = f"{body}\n\n{self._signature}"
        target_email = disposition.target.email if disposition.target else None
        return Draft(
            to=target_email,
            subject=payload["subject"],
            body=body,
            angle=angle,
            claims_used=[str(c) for c in payload.get("claims_used", [])],
        )

    def _fix_calendar_links(self, body: str) -> str:
        """Deterministic guard: the booking link is the rep's own, not a fact the
        gate checks, so the model can invent a handle on the right provider. If a
        link is configured, rewrite any URL on the same host to the exact one."""
        if not self._calendar_url:
            return body
        host = urlsplit(self._calendar_url).netloc
        if not host:
            return body
        pattern = re.compile(
            r"https?://" + re.escape(host) + r"(?:/[^\s)>\]\"']*)?", re.IGNORECASE
        )
        return pattern.sub(self._calendar_url, body)

    def _build_prompt(
        self, dossier: list[Claim], disposition: Disposition, angle: str
    ) -> str:
        exemplar_block = (
            "\n\n---\n\n".join(self._exemplars) if self._exemplars else "(none yet)"
        )
        target = disposition.target.to_dict() if disposition.target else None
        disp_dict = disposition.to_dict()
        name_note = ""
        if target and target.get("name"):
            first = str(target["name"]).split()[0]
            if not _name_is_grounded(first, dossier):
                # the recipient's name is not grounded in any Claim, so it cannot be
                # used (the boundary gate would strip a draft that asserts it). Blank
                # it everywhere it reaches the model and tell it to greet without a
                # name.
                target = {**target, "name": None}
                if disp_dict.get("target"):
                    disp_dict["target"] = {**disp_dict["target"], "name": None}
                name_note = (
                    "The recipient's name is not grounded in the dossier, so it is "
                    'unknown. Greet without a name (e.g. "Hey -").\n\n'
                )
        booking = (
            f"Rep booking link (use verbatim for any scheduling CTA): {self._calendar_url}"
            if self._calendar_url
            else "Rep booking link: none configured; do not include a scheduling link."
        )
        return (
            f"Voice rules:\n{self._voice_profile}\n\n"
            f"{booking}\n\n"
            f"Exemplar sends (match this cadence):\n{exemplar_block}\n\n"
            f"Framing angle: {angle}\n\n"
            f"{name_note}"
            f"Target contact: {json.dumps(target)}\n\n"
            f"Disposition: {json.dumps(disp_dict)}\n\n"
            f"Grounded dossier Claims:\n```json\n"
            f"{json.dumps([c.to_dict() for c in dossier], indent=2)}\n```"
        )
