"""Search capabilities (plan §11.2): filter + optional semantic ranking.

Search is a read-only discovery query over the production channel: it lists
active releases (revocation-aware via live pointers, §46) and narrows by
kind/domain facets, then ranks by the retrieval stage when a query is given.
It is not a routing decision — the full eligibility policy (ADR-009) runs
only in the routing pipeline.
"""

from aci.application.list_candidates import ProductionCandidateLoader
from aci.application.protocols import CandidateRetriever, CapabilityRepository
from aci.domain.capability.models import SearchCapabilitiesQuery
from aci.domain.routing.models import CapabilitySearchResult, ScoredCandidate


class SearchCapabilitiesService:
    def __init__(
        self,
        loader: ProductionCandidateLoader,
        retriever: CandidateRetriever,
        capabilities: CapabilityRepository,
    ) -> None:
        self._loader = loader
        self._retriever = retriever
        self._capabilities = capabilities

    def search(self, query: SearchCapabilitiesQuery) -> list[CapabilitySearchResult]:
        candidates = self._loader.load()

        if query.kinds:
            candidates = [c for c in candidates if c.kind in set(query.kinds)]
        if query.domains:
            wanted = set(query.domains)
            candidates = [c for c in candidates if wanted & set(c.facets.get("domain", []))]

        if query.query:
            retrieval = self._retriever.retrieve(query.query, candidates, limit=query.limit)
            scored = retrieval.candidates
        else:
            scored = [ScoredCandidate(candidate=c, score=0.0) for c in candidates[: query.limit]]

        results: list[CapabilitySearchResult] = []
        for item in scored:
            version = self._capabilities.get_version(
                item.candidate.capability_id, item.candidate.version
            )
            results.append(
                CapabilitySearchResult(
                    capability_id=item.candidate.capability_id,
                    version=item.candidate.version,
                    digest=item.candidate.digest,
                    kind=item.candidate.kind,
                    display_name=version.display_name if version else "",
                    description=version.description if version else "",
                    facets=item.candidate.facets,
                    score=item.score,
                )
            )
        return results
