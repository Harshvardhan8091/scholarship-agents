from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import pytest
from pypdf import PdfReader

from agents import (
    ProfileAgent,
    RankerAgent,
    ResearchAgent,
    VerifierAgent,
    WriterAgent,
)
from event_bus import EventBus
from models import (
    CandidateOpportunity,
    PipelineEvent,
    StudentProfile,
    StudentProject,
    VerifiedOpportunity,
)
from pipeline import run_pipeline

PROJECT_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = PROJECT_DIR / "samples"


def test_pdf_parsing_sample_resume():
    resume_path = SAMPLES_DIR / "sample_resume.pdf"
    assert resume_path.exists(), "sample_resume.pdf should exist"
    reader = PdfReader(str(resume_path))
    assert len(reader.pages) >= 1
    text = reader.pages[0].extract_text()
    assert "Alex Rivera" in text
    assert "Computer Science" in text
    assert "CGPA" in text or "8.92" in text


def test_pdf_parsing_sample_marksheet():
    marksheet_path = SAMPLES_DIR / "sample_marksheet.pdf"
    assert marksheet_path.exists(), "sample_marksheet.pdf should exist"
    reader = PdfReader(str(marksheet_path))
    assert len(reader.pages) >= 1
    text = reader.pages[0].extract_text()
    assert "TRANSCRIPT" in text or "Alex Rivera" in text
    assert "Algorithms" in text


def test_text_parsing_sample_marksheet():
    txt_path = SAMPLES_DIR / "sample_marksheet.txt"
    assert txt_path.exists(), "sample_marksheet.txt should exist"
    with open(txt_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Alex Rivera" in content
    assert "CGPA: 8.92" in content


@pytest.mark.asyncio
async def test_event_bus_emission_and_streaming():
    bus = EventBus(print_to_terminal=False)
    received_events: list[PipelineEvent] = []

    def handle_event(e: PipelineEvent):
        received_events.append(e)

    bus.subscribe(handle_event)
    queue = bus.create_stream_queue()

    await bus.emit(agent="TestAgent", event_type="started", message="Starting task")
    await bus.emit(agent="TestAgent", event_type="recovery", message="Recovering state")
    await bus.emit(agent="TestAgent", event_type="done", message="Completed task")

    assert len(received_events) == 3
    assert received_events[0].type == "started"
    assert received_events[1].type == "recovery"
    assert received_events[2].type == "done"

    q_ev1 = await queue.get()
    assert q_ev1.type == "started"
    q_ev2 = await queue.get()
    assert q_ev2.type == "recovery"

    bus.remove_stream_queue(queue)


@pytest.mark.asyncio
async def test_research_agent_deduplication():
    bus = EventBus(print_to_terminal=False)
    agent = ResearchAgent()
    profile = StudentProfile(
        name="Alex Rivera",
        degree="B.Tech Computer Science",
        field="Computer Science",
        cgpa="8.92/10",
        target_countries=["Germany", "Switzerland", "United Kingdom"],
    )

    candidates = await agent.run(profile, event_bus=bus, round_index=0)
    assert len(candidates) >= 10, f"Expected at least 10 candidates, got {len(candidates)}"

    urls = [c.official_url for c in candidates]
    assert len(urls) == len(set(urls)), "Candidates must be strictly deduplicated by URL"


@pytest.mark.asyncio
async def test_verifier_agent_processing():
    bus = EventBus(print_to_terminal=False)
    agent = VerifierAgent()
    profile = StudentProfile(
        name="Alex Rivera",
        degree="B.Tech Computer Science",
        field="Computer Science",
        cgpa="8.92/10",
    )
    candidates = [
        CandidateOpportunity(
            name="DAAD EPOS Scholarships",
            provider="DAAD",
            country="Germany",
            official_url="https://www.daad.de/en/study-and-research-in-germany/scholarships/epos/",
            description="Postgraduate scholarships for international students",
        ),
        CandidateOpportunity(
            name="Chevening Scholarships",
            provider="UK Government",
            country="United Kingdom",
            official_url="https://www.chevening.org/scholarships/",
            description="UK government global scholarship programme",
        ),
    ]

    verified = await agent.run(candidates, profile, bus)
    assert len(verified) == 2
    for v in verified:
        assert isinstance(v, VerifiedOpportunity)
        assert v.match_status in ("eligible", "partially_eligible", "unverified", "ineligible")
        assert len(v.source_urls) >= 1


@pytest.mark.asyncio
async def test_ranker_and_writer_agents():
    bus = EventBus(print_to_terminal=False)
    profile = StudentProfile(
        name="Alex Rivera",
        degree="B.Tech Computer Science",
        field="Computer Science",
        cgpa="8.92/10",
        skills=["Python", "Distributed Systems", "Machine Learning"],
        achievements=["National AI Hackathon Winner 2024"],
        target_countries=["Germany", "Switzerland"],
    )
    scholarship_names = [
        "Computer Science Excellence Scholarship",
        "Global Research Master's Fellowship",
        "Applied Data Systems Award",
        "International Engineering Graduate Grant",
        "Digital Innovation Tuition Scholarship",
        "Technology Leadership Funding Program",
        "Academic Merit Graduate Award",
    ]
    verified = [
        VerifiedOpportunity(
            name=name,
            provider=f"University {i}",
            country="Germany" if i % 2 == 0 else "Switzerland",
            official_url=f"https://example.edu/scholarship/{i}",
            degree_level="Master's",
            funding_amount_coverage="Full tuition waiver + stipend",
            deadline="March 31, 2027",
            match_status="eligible",
            confidence=0.9,
            source_urls=[f"https://example.edu/scholarship/{i}"],
        )
        for i, name in enumerate(scholarship_names, start=1)
    ]

    ranker = RankerAgent()
    ranked = await ranker.run(verified, profile, bus)
    assert len(ranked.top_opportunities) == 5
    assert ranked.top_opportunities[0].rank == 1

    writer = WriterAgent()
    sop_draft = await writer.run(ranked.top_opportunities[0], profile, bus)
    assert "[DRAFT - FOR STUDENT REVIEW ONLY" in sop_draft.sop_markdown
    assert "Alex Rivera" in sop_draft.sop_markdown or "Applicant" in sop_draft.sop_markdown
    assert len(sop_draft.grounded_facts_used) >= 1


@pytest.mark.asyncio
async def test_full_pipeline_end_to_end():
    inputs = {
        "resume_path": str(SAMPLES_DIR / "sample_resume.pdf"),
        "marksheet_path": str(SAMPLES_DIR / "sample_marksheet.pdf"),
        "github_username": "torvalds",
        "chosen_pick": 1,
    }

    result = await run_pipeline(inputs, print_to_terminal=False)

    assert result["student_profile"] is not None
    assert result["total_candidates"] >= 10
    assert len(result["all_candidates"]) == result["total_candidates"]
    assert result["total_verified"] >= 10
    assert result["ranked_results"] is not None
    assert len(result["ranked_results"]["top_opportunities"]) == 5
    assert result["sop_draft"] is not None
    assert "DRAFT" in result["sop_draft"]["sop_markdown"]
    assert len(result["events"]) > 5
