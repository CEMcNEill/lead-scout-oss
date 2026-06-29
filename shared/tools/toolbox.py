"""The toolbox the shell hands each qualifier.

Built fresh per run so its IdAllocator yields run-unique, deterministic Claim
ids. Bundles the six shared primitives plus the rep-scoped drafter (voice
profile, exemplars, signature). A qualifier decides which tools to call and when
it has enough; the toolbox just makes the grounded primitives available.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.model import ModelClient
from shared.tools.company import CompanyResearchTool
from shared.tools.crm_context import CrmContextTool
from shared.tools.drafter import DrafterTool
from shared.tools.fetchers import CompanyFetcher, CrmFetcher, PersonFetcher, UsageFetcher
from shared.tools.grounding import IdAllocator
from shared.tools.person import PersonResearchTool
from shared.tools.usage import UsageResearchTool
from shared.tools.use_case import UseCaseMappingTool


@dataclass
class Toolbox:
    crm_context: CrmContextTool
    person_research: PersonResearchTool
    company_research: CompanyResearchTool
    usage_research: UsageResearchTool
    use_case_mapping: UseCaseMappingTool
    drafter: DrafterTool
    ids: IdAllocator
    model: ModelClient  # the metered client, for a qualifier's own judgment calls


def build_toolbox(
    *,
    crm_fetcher: CrmFetcher,
    person_fetcher: PersonFetcher,
    company_fetcher: CompanyFetcher,
    usage_fetcher: UsageFetcher,
    model: ModelClient,
    voice_profile: str,
    exemplars: list[str],
    signature: str = "",
    calendar_url: str = "",
) -> Toolbox:
    ids = IdAllocator()
    return Toolbox(
        crm_context=CrmContextTool(crm_fetcher, ids),
        person_research=PersonResearchTool(person_fetcher, model, ids),
        company_research=CompanyResearchTool(company_fetcher, model, ids),
        usage_research=UsageResearchTool(usage_fetcher, model, ids),
        use_case_mapping=UseCaseMappingTool(model, ids),
        drafter=DrafterTool(
            model, voice_profile=voice_profile, exemplars=exemplars,
            signature=signature, calendar_url=calendar_url,
        ),
        ids=ids,
        model=model,
    )
