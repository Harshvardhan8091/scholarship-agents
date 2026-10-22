from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Dict, List, Optional, Set
from typing_extensions import TypedDict
from langgraph.graph import END, StateGraph

from agents import (
    ProfileAgent,
    RankerAgent,
    ResearchAgent,
    VerifierAgent,
    WriterAgent,
)
from agents.verifier_agent import CANDIDATE_TIMEOUT_SECONDS
from config import settings
from event_bus import EventBus
from models import (
    CandidateOpportunity,
    RankedOpportunity,
    RankedResults,
    SOPDraft,
    StudentProfile,
    VerifiedOpportunity,
)


class PipelineState(TypedDict, total=False):
    resume_path: Optional[str]
    marksheet_path: Optional[str]
    github_username: Optional[str]
    chosen_pick: int
    profile: Optional[StudentProfile]
    candidates: List[CandidateOpportunity]
    verified_opportunities: List[VerifiedOpportunity]
    ranked_results: Optional[RankedResults]
    sop_draft: Optional[SOPDraft]
    research_round: int
    event_bus: EventBus
    error: Optional[str]


def create_scholarship_graph(
    profile_agent: Optional[ProfileAgent] = None,
    research_agent: Optional[ResearchAgent] = None,
    verifier_agent: Optional[VerifierAgent] = None,
    ranker_agent: Optional[RankerAgent] = None,
    writer_agent: Optional[WriterAgent] = None,
) -> StateGraph:
    p_agent = profile_agent or ProfileAgent()
    r_agent = research_agent or ResearchAgent()
    v_agent = verifier_agent or VerifierAgent()
    rk_agent = ranker_agent or RankerAgent()
    w_agent = writer_agent or WriterAgent()

    workflow = StateGraph(PipelineState)

    # 1. Profile Extraction Node
    async def profile_node(state: PipelineState) -> Dict[str, Any]:
        bus = state.get("event_bus") or EventBus()
        try:
            profile = await p_agent.run(
                resume_path=state.get("resume_path"),
                marksheet_path=state.get("marksheet_path"),
                github_username=state.get("github_username"),
                event_bus=bus,
            )
            return {"profile": profile}
        except Exception as e:
            await bus.emit(
                agent="ProfileAgent",
                event_type="error",
                message=f"Profile extraction failed: {e}",
            )
            return {"error": f"ProfileAgent error: {e}"}

    # 2. Research Node
    async def research_node(state: PipelineState) -> Dict[str, Any]:
        bus = state.get("event_bus") or EventBus()
        profile = state.get("profile") or StudentProfile()
        round_idx = state.get("research_round", 0)

        existing_cands = state.get("candidates", [])
        existing_urls: Set[str] = {c.official_url for c in existing_cands}

        try:
            new_cands = await r_agent.run(
                profile=profile,
                event_bus=bus,
                round_index=round_idx,
                existing_urls=existing_urls,
            )
            combined = existing_cands + new_cands
            return {"candidates": combined, "research_round": round_idx + 1}
        except Exception as e:
            await bus.emit(
                agent="ResearchAgent",
                event_type="error",
                message=f"Research search failed: {e}",
            )
            return {"candidates": existing_cands}

    # 3. Verifier Node
    async def verifier_node(state: PipelineState) -> Dict[str, Any]:
        bus = state.get("event_bus") or EventBus()
        candidates = state.get("candidates", [])
        profile = state.get("profile") or StudentProfile()

        already_verified = state.get("verified_opportunities", [])
        verified_urls = {v.official_url for v in already_verified}

        unverified_cands = [c for c in candidates if c.official_url not in verified_urls]

        try:
            new_verified = await v_agent.run(
                candidates=unverified_cands,
                profile=profile,
                event_bus=bus,
            )
            all_verified = already_verified + new_verified
            return {"verified_opportunities": all_verified}
        except Exception as e:
            await bus.emit(
                agent="VerifierAgent",
                event_type="error",
                message=f"Verification phase error: {e}",
            )
            return {"verified_opportunities": already_verified}

    # 4. Conditional Edge Decision Function
    def check_verification_threshold(state: PipelineState) -> str:
        verified_list = state.get("verified_opportunities", [])
        eligible_candidates = [
            o for o in verified_list if o.match_status in ("eligible", "partially_eligible")
        ]
        round_idx = state.get("research_round", 0)
        max_rounds = settings.MAX_RESEARCH_ROUNDS

        # If we have at least 10 verified eligible/partial candidates, or reached max extra rounds
        if len(eligible_candidates) >= 10 or round_idx >= max_rounds:
            return "ranker"

        # The research node advances research_round in its state update.
        return "research"

    # 5. Ranker Node
    async def ranker_node(state: PipelineState) -> Dict[str, Any]:
        bus = state.get("event_bus") or EventBus()
        verified = state.get("verified_opportunities", [])
        candidates = state.get("candidates", [])
        profile = state.get("profile") or StudentProfile()

        try:
            ranked_results = await rk_agent.run(
                verified_opportunities=verified,
                profile=profile,
                event_bus=bus,
            )
            if len(ranked_results.top_opportunities) < 5:
                official_queries = [
                    "site:daad.de DAAD master's scholarship international students",
                    "site:sbfi.admin.ch Swiss Government Excellence Scholarship master's",
                    "site:erasmus-plus.ec.europa.eu Erasmus Mundus master's scholarship",
                    "site:chevening.org Chevening master's scholarship international students",
                    "site:foreign.fulbrightonline.org Fulbright foreign student master's scholarship",
                    "site:edu master's computer science international student funding scholarship",
                ]
                await bus.emit(
                    agent="ResearchAgent",
                    event_type="recovery",
                    message="Fewer than five distinct open opportunities remain. Running one additional official-source research round.",
                    details={"queries": official_queries},
                )
                new_candidates = await r_agent.run(
                    profile=profile,
                    event_bus=bus,
                    round_index=settings.MAX_RESEARCH_ROUNDS + 1,
                    existing_urls={candidate.official_url for candidate in candidates},
                    query_override=official_queries,
                )
                candidates.extend(new_candidates)
                known_urls = {opportunity.official_url for opportunity in verified}
                candidates_to_verify = [
                    candidate
                    for candidate in new_candidates
                    if candidate.official_url not in known_urls
                ]
                new_verified = await v_agent.run(
                    candidates=candidates_to_verify,
                    profile=profile,
                    event_bus=bus,
                )
                verified.extend(new_verified)
                ranked_results = await rk_agent.run(
                    verified_opportunities=verified,
                    profile=profile,
                    event_bus=bus,
                    allow_curated_fallback=True,
                )
                for ranked_opportunity in ranked_results.top_opportunities:
                    if ranked_opportunity.discovery_source != "curated_fallback":
                        continue
                    candidate = CandidateOpportunity(
                        name=ranked_opportunity.name,
                        provider=ranked_opportunity.provider,
                        country=ranked_opportunity.country,
                        official_url=ranked_opportunity.official_url,
                        degree_level=ranked_opportunity.degree_level,
                        discovery_source="curated_fallback",
                    )
                    try:
                        verified_curated = await asyncio.wait_for(
                            v_agent._verify_candidate(candidate, profile, bus),
                            timeout=CANDIDATE_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        await bus.emit(
                            agent="VerifierAgent",
                            event_type="recovery",
                            message=f"Curated verification timed out after {CANDIDATE_TIMEOUT_SECONDS} seconds for {candidate.name}; keeping fields unverified.",
                        )
                        verified_curated = ranked_opportunity.verified_data.model_copy(
                            update={
                                "verification_mode": "unverified",
                                "eligibility": "unverified",
                                "funding_amount_coverage": "unverified",
                                "deadline": "unverified",
                                "how_to_apply": "unverified",
                                "evidence": {},
                                "missing_information": [
                                    f"Curated verification timed out after {CANDIDATE_TIMEOUT_SECONDS} seconds."
                                ],
                            }
                        )

                    ranked_opportunity.verified_data = verified_curated
                    ranked_opportunity.name = verified_curated.name
                    ranked_opportunity.provider = verified_curated.provider
                    ranked_opportunity.country = verified_curated.country
                    ranked_opportunity.official_url = verified_curated.official_url
                    ranked_opportunity.degree_level = verified_curated.degree_level
                    ranked_opportunity.funding = verified_curated.funding_amount_coverage
                    ranked_opportunity.deadline = verified_curated.deadline
                    ranked_opportunity.evidence = verified_curated.evidence
                    ranked_opportunity.verification_mode = verified_curated.verification_mode
                    ranked_opportunity.deadline_source_url = verified_curated.deadline_source_url
                    ranked_opportunity.last_checked = verified_curated.last_checked
                    verified.append(verified_curated)
            return {
                "ranked_results": ranked_results,
                "candidates": candidates,
                "verified_opportunities": verified,
            }
        except Exception as e:
            await bus.emit(
                agent="RankerAgent",
                event_type="error",
                message=f"Ranking failure: {e}",
            )
            return {"error": f"RankerAgent error: {e}"}

    # 6. Writer Node
    async def writer_node(state: PipelineState) -> Dict[str, Any]:
        bus = state.get("event_bus") or EventBus()
        ranked_results = state.get("ranked_results")
        profile = state.get("profile") or StudentProfile()
        pick_index = state.get("chosen_pick", 1)

        if not ranked_results or not ranked_results.top_opportunities:
            await bus.emit(
                agent="WriterAgent",
                event_type="error",
                message="Cannot draft SOP: No ranked opportunities available.",
            )
            return {}

        # Pick specified index (1-based, default 1)
        target_pick = None
        for opp in ranked_results.top_opportunities:
            if opp.rank == pick_index:
                target_pick = opp
                break
        if not target_pick:
            target_pick = ranked_results.top_opportunities[0]

        try:
            sop_draft = await w_agent.run(
                opportunity=target_pick,
                profile=profile,
                event_bus=bus,
            )
            return {"sop_draft": sop_draft}
        except Exception as e:
            await bus.emit(
                agent="WriterAgent",
                event_type="error",
                message=f"Writer agent error: {e}",
            )
            return {"error": f"WriterAgent error: {e}"}

    # Assemble Graph
    workflow.add_node("profile_agent", profile_node)
    workflow.add_node("research_agent", research_node)
    workflow.add_node("verifier_agent", verifier_node)
    workflow.add_node("ranker_agent", ranker_node)
    workflow.add_node("writer_agent", writer_node)

    workflow.set_entry_point("profile_agent")
    workflow.add_edge("profile_agent", "research_agent")
    workflow.add_edge("research_agent", "verifier_agent")

    # Conditional loop: if fewer than 10 verified candidates, loop back to research_agent
    workflow.add_conditional_edges(
        "verifier_agent",
        check_verification_threshold,
        {
            "research": "research_agent",
            "ranker": "ranker_agent",
        },
    )

    workflow.add_edge("ranker_agent", "writer_agent")
    workflow.add_edge("writer_agent", END)

    return workflow
