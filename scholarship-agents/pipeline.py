from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional, Union

from event_bus import EventBus
from graph import PipelineState, create_scholarship_graph
from llm_client import LLM_CALL_COUNTS, reset_call_counts
from models import PipelineEvent


async def run_pipeline(
    inputs: Dict[str, Any],
    on_event: Optional[Union[Callable[[PipelineEvent], Any], Callable[[PipelineEvent], Coroutine[Any, Any, Any]]]] = None,
    print_to_terminal: bool = True,
) -> Dict[str, Any]:
    """
    Main asynchronous pipeline entry point.
    Can be invoked from CLI (run_cli.py) or FastAPI endpoints.
    """
    event_bus = EventBus(print_to_terminal=print_to_terminal)
    if on_event:
        event_bus.subscribe(on_event)

    await event_bus.emit(
        agent="Orchestrator",
        event_type="started",
        message="AI Scholarship Finder Multi-Agent Pipeline initiated.",
        details={
            "resume": inputs.get("resume_path"),
            "marksheet": inputs.get("marksheet_path"),
            "github": inputs.get("github_username"),
            "target_countries": inputs.get("target_countries"),
            "target_degree_level": inputs.get("target_degree_level"),
            "financial_need": inputs.get("financial_need"),
            "interests": inputs.get("interests"),
            "chosen_pick": inputs.get("chosen_pick", 1),
        },
    )

    reset_call_counts()
    workflow = create_scholarship_graph()
    app = workflow.compile()

    initial_state: PipelineState = {
        "resume_path": inputs.get("resume_path"),
        "marksheet_path": inputs.get("marksheet_path"),
        "github_username": inputs.get("github_username"),
        "target_countries": inputs.get("target_countries"),
        "target_degree_level": inputs.get("target_degree_level"),
        "financial_need": inputs.get("financial_need"),
        "interests": inputs.get("interests"),
        "chosen_pick": int(inputs.get("chosen_pick", 1)),
        "candidates": [],
        "verified_opportunities": [],
        "research_round": 0,
        "event_bus": event_bus,
    }

    final_state = await app.ainvoke(initial_state)

    await event_bus.emit(
        agent="Orchestrator",
        event_type="done",
        message="Pipeline finished all stages successfully.",
    )

    ranked_res = final_state.get("ranked_results")
    sop_res = final_state.get("sop_draft")
    profile_res = final_state.get("profile")

    provider_counts = dict(LLM_CALL_COUNTS["providers"])
    model_counts = dict(LLM_CALL_COUNTS["models"])

    result = {
        "student_profile": profile_res.model_dump() if profile_res else None,
        "total_candidates": len(final_state.get("candidates", [])),
        "total_verified": len(final_state.get("verified_opportunities", [])),
        "all_candidates": [
            candidate.model_dump()
            for candidate in final_state.get("candidates", [])
        ],
        "all_verified_opportunities": [
            opportunity.model_dump()
            for opportunity in final_state.get("verified_opportunities", [])
        ],
        "ranked_results": ranked_res.model_dump() if ranked_res else None,
        "sop_draft": sop_res.model_dump() if sop_res else None,
        "llm_provider_calls": provider_counts,
        "llm_model_calls": model_counts,
        "events": [e.model_dump() for e in event_bus.get_history()],
    }

    return result
