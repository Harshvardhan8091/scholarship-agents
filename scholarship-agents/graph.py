from __future__ import annotations

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
            return {"candidates": combined}
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

        # Otherwise, increment round and trigger research again
        state["research_round"] = round_idx + 1
        return "research"

    # 5. Ranker Node
    async def ranker_node(state: PipelineState) -> Dict[str, Any]:
        bus = state.get("event_bus") or EventBus()
        verified = state.get("verified_opportunities", [])
        profile = state.get("profile") or StudentProfile()

        try:
            ranked_results = await rk_agent.run(
                verified_opportunities=verified,
                profile=profile,
                event_bus=bus,
            )
            return {"ranked_results": ranked_results}
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
