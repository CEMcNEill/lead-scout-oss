"""Thin SKILL.md loader: the per-play charter the agentic qualifier runs.

Reads qualifiers/<name>/SKILL.md and splits it on `## ` headers so the agentic
interior can treat sections (How to judge, How to draft, How to follow up) as the
source of truth, with the qualifier's class attrs as a fallback during migration.
The SPEC's "thin loader reads the SKILL.md and injects it" on the raw messages API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

QUALIFIERS_DIR = Path(__file__).resolve().parent.parent / "qualifiers"


@dataclass
class SkillCharter:
    name: str
    raw: str
    sections: dict[str, str] = field(default_factory=dict)


def _split_sections(text: str) -> dict[str, str]:
    """Map each `## Header` (lowercased) to its body text."""
    sections: dict[str, str] = {}
    header: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if header is not None:
                sections[header] = "\n".join(buf).strip()
            header = line[3:].strip().lower()
            buf = []
        elif header is not None:
            buf.append(line)
    if header is not None:
        sections[header] = "\n".join(buf).strip()
    return sections


@lru_cache(maxsize=None)
def load_charter(name: str, root: Path = QUALIFIERS_DIR) -> SkillCharter:
    """Load a play's charter. A missing file yields an empty charter so an
    unconverted play still runs off its class attrs."""
    path = Path(root) / name / "SKILL.md"
    text = path.read_text() if path.exists() else ""
    return SkillCharter(name=name, raw=text, sections=_split_sections(text))
