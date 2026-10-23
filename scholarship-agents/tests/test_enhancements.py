from __future__ import annotations

import os
import time
from pathlib import Path
import pytest

from models import CandidateOpportunity, VerifiedOpportunity, RankedOpportunity, StudentProfile
from page_cache import get_cached_page, set_cached_page, CACHE_DIR
from startup_check import run_startup_checks
from agents.ranker_agent import RankerAgent
from event_bus import EventBus


def test_page_cache_set_and_get():
    test_url = "https://test-cache-example.org/scholarship"
    test_content = "This is a cached test page for scholarships."
    
    set_cached_page(test_url, test_content)
    cached = get_cached_page(test_url)
    assert cached == test_content

    # Clean up test cache
    import hashlib
    url_hash = hashlib.sha256(test_url.encode()).hexdigest()[:24]
    p = CACHE_DIR / f"{url_hash}.json"
    if p.exists():
        p.unlink()


def test_transparency_and_deadline_fields_on_models():
    cand = CandidateOpportunity(
        name="Test Scholarship",
        provider="Test Provider",
        country="Germany",
        official_url="https://test.edu/scholarship",
        discovery_source="tavily_live",
    )
    assert cand.discovery_source == "tavily_live"

    v = VerifiedOpportunity(
        name=cand.name,
        provider=cand.provider,
        country=cand.country,
        official_url=cand.official_url,
        match_status="eligible",
        discovery_source=cand.discovery_source,
        verification_mode="llm_live",
        deadline="2025-06-30",
        deadline_source_url=cand.official_url,
        last_checked="2025-01-01",
    )
    assert v.discovery_source == "tavily_live"
    assert v.verification_mode == "llm_live"
    assert v.deadline_source_url == cand.official_url
    assert v.last_checked == "2025-01-01"


@pytest.mark.asyncio
async def test_ranker_penalizes_unverified_deadlines():
    bus = EventBus(print_to_terminal=False)
    profile = StudentProfile(
        name="Test Student",
        field="Computer Science",
        cgpa="9.0/10",
        target_countries=["Germany"],
    )
    
    opp_verified_deadline = VerifiedOpportunity(
        name="Scholarship A (Verified Deadline)",
        provider="Provider A",
        country="Germany",
        official_url="https://example.edu/a",
        funding_amount_coverage="Full tuition",
        deadline="May 15, 2025",
        match_status="eligible",
        confidence=0.9,
    )
    
    opp_unverified_deadline = VerifiedOpportunity(
        name="Scholarship B (Unverified Deadline)",
        provider="Provider B",
        country="Germany",
        official_url="https://example.edu/b",
        funding_amount_coverage="Full tuition",
        deadline="unverified",
        match_status="eligible",
        confidence=0.9,
    )

    ranker = RankerAgent()
    # Test heuristic ranking explicitly
    results = ranker._rank_heuristically([opp_unverified_deadline, opp_verified_deadline], profile)
    
    top_picks = results.top_opportunities
    assert len(top_picks) == 2
    # opp_verified_deadline should rank above opp_unverified_deadline because of deadline penalty
    assert top_picks[0].name == "Scholarship A (Verified Deadline)"
    assert top_picks[0].fit_score > top_picks[1].fit_score


@pytest.mark.asyncio
async def test_startup_check_runs():
    res = await run_startup_checks()
    assert isinstance(res, dict)
    assert "gemini_live" in res
    assert "tavily_live" in res
    assert "model" in res


def test_env_loader_handles_bom_quotes_and_whitespace(tmp_path, monkeypatch):
    from config import _sanitize_env_value, load_project_env

    env_path = tmp_path / ".env"
    env_path.write_bytes(
        b'\xef\xbb\xbfGROQ_API_KEY="  groq_test_value  "\r\n'
        b'GEMINI_API_KEY = \'   testgemini  \'\r\n'
        b'TAVILY_API_KEY= "  tvly_test  "\r\n'
        b'GROQ_MODEL = " openai/gpt-oss-120b "\r\n'
        b'GEMINI_MODEL= "gemini-2.5-flash"\r\n'
        b'LLM_PROVIDERS= "groq, gemini"\r\n'
    )

    for key in [
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "TAVILY_API_KEY",
        "GROQ_MODEL",
        "GEMINI_MODEL",
        "LLM_PROVIDERS",
    ]:
        monkeypatch.delenv(key, raising=False)

    load_project_env(env_path)

    assert _sanitize_env_value('  "value"  ') == "value"
    assert _sanitize_env_value("\r\n  'value'\r\n") == "value"
    assert os.environ["GROQ_API_KEY"] == "groq_test_value"
    assert os.environ["GEMINI_API_KEY"] == "testgemini"
    assert os.environ["TAVILY_API_KEY"] == "tvly_test"
    assert os.environ["GROQ_MODEL"] == "openai/gpt-oss-120b"
    assert os.environ["GEMINI_MODEL"] == "gemini-2.5-flash"
    assert os.environ["LLM_PROVIDERS"] == "groq,gemini"


def test_heuristic_verifier_no_fabricated_fields_and_evidence():
    from agents.verifier_agent import VerifierAgent

    agent = VerifierAgent()
    profile = StudentProfile(name="Test Candidate", degree="Bachelor of Science", field="CS")
    cand = CandidateOpportunity(
        name="Generic University Program",
        provider="University X",
        country="Germany",
        official_url="https://example.edu/program",
    )

    # Empty / vague page: must NEVER fabricate funding, deadline, eligibility, or how_to_apply
    empty_res = agent._heuristic_verifier(cand, "Welcome to the campus webpage.", profile)
    assert empty_res.funding_amount_coverage == "unverified"
    assert empty_res.deadline == "unverified"
    assert empty_res.eligibility == "unverified"
    assert empty_res.how_to_apply == "unverified"
    assert len(empty_res.evidence) == 0

    # Page with real text: extracts field and stores snippet in evidence (max 200 chars)
    page_text = (
        "Application deadline: March 31, 2026. "
        "The scholarship provides €1,200 per month stipend and full tuition fee waiver. "
        "Eligibility: Open to all international graduate applicants with a first degree. "
        "How to apply: Submit application via the online admissions portal with references."
    )
    verified_res = agent._heuristic_verifier(cand, page_text, profile)
    assert "1,200" in verified_res.funding_amount_coverage
    assert "closed for this cycle" in verified_res.deadline.lower()
    assert "2026-03-31" in verified_res.deadline
    assert "international" in verified_res.eligibility
    assert "online admissions portal" in verified_res.how_to_apply

    # Verify evidence snippets exist and are <= 200 chars
    assert "funding_amount_coverage" in verified_res.evidence
    assert len(verified_res.evidence["funding_amount_coverage"]) <= 200
    assert "deadline" in verified_res.evidence
    assert len(verified_res.evidence["deadline"]) <= 200
    assert "eligibility" in verified_res.evidence
    assert len(verified_res.evidence["eligibility"]) <= 200
    assert "how_to_apply" in verified_res.evidence
    assert len(verified_res.evidence["how_to_apply"]) <= 200


def test_degree_level_inference():
    from agents.profile_agent import ProfileAgent

    # Bachelor should infer Master's
    assert ProfileAgent.infer_target_degree_level("Bachelor of Technology in CS") == "Master's"
    assert ProfileAgent.infer_target_degree_level("B.Sc Computer Science") == "Master's"
    # Master should infer PhD
    assert ProfileAgent.infer_target_degree_level("Master of Science in AI") == "PhD"
    assert ProfileAgent.infer_target_degree_level("M.Tech Mechanical") == "PhD"


@pytest.mark.asyncio
async def test_quality_filter_domestic_restriction():
    from agents.verifier_agent import VerifierAgent

    agent = VerifierAgent()
    profile = StudentProfile(name="Test", target_countries=["United Kingdom"])
    cand = CandidateOpportunity(
        name="GOV.UK Student Finance",
        provider="UK Government",
        country="United Kingdom",
        official_url="https://www.gov.uk/student-finance",
    )
    res = agent._heuristic_verifier(cand, "GOV.UK Student Finance is only available for UK residents and home fee status students.", profile)
    assert res.match_status == "ineligible"
    assert any("domestic" in u.lower() for u in res.unmet_criteria)


@pytest.mark.asyncio
async def test_verifier_domestic_restriction_uses_profile_target_degree(monkeypatch):
    from unittest.mock import AsyncMock
    from agents.verifier_agent import VerifierAgent

    agent = VerifierAgent()
    profile = StudentProfile(name="Test", target_degree_level="PhD")
    candidate = CandidateOpportunity(
        name="Domestic-only funding",
        provider="Example Government",
        country="United Kingdom",
        official_url="https://www.gov.uk/student-finance",
    )
    monkeypatch.setattr(
        agent,
        "_fetch_page_content_and_html",
        AsyncMock(
            return_value=(
                "This funding is only available for UK residents and domestic students only.",
                "",
            )
        ),
    )

    result = await agent._verify_candidate(
        candidate, profile, EventBus(print_to_terminal=False)
    )

    assert result.match_status == "ineligible"
    assert result.degree_level == "PhD"
    assert any("domestic" in reason.lower() for reason in result.unmet_criteria)


@pytest.mark.asyncio
async def test_curated_page_retries_once_with_browser_timeout(monkeypatch):
    from unittest.mock import AsyncMock
    from agents.verifier_agent import VerifierAgent

    agent = VerifierAgent()
    profile = StudentProfile(target_degree_level="Master's")
    candidate = CandidateOpportunity(
        name="Curated Scholarship",
        provider="Example University",
        country="Germany",
        official_url="https://example.edu/scholarship",
        discovery_source="curated_fallback",
    )
    monkeypatch.setattr(
        agent,
        "_fetch_page_content_and_html",
        AsyncMock(
            side_effect=[
                ("FAILED_FETCH", ""),
                ("A verified scholarship page with enough readable text for review.", "<html></html>"),
            ]
        ),
    )
    monkeypatch.setattr(
        agent,
        "_verify_with_llm",
        AsyncMock(
            return_value=VerifiedOpportunity(
                name=candidate.name,
                provider=candidate.provider,
                country=candidate.country,
                official_url=candidate.official_url,
                discovery_source="curated_fallback",
                match_status="partially_eligible",
            )
        ),
    )

    result = await agent._verify_candidate(
        candidate, profile, EventBus(print_to_terminal=False)
    )

    assert result.match_status == "partially_eligible"
    assert agent._fetch_page_content_and_html.await_count == 2
    retry = agent._fetch_page_content_and_html.await_args_list[1]
    assert retry.kwargs["timeout"] == 20.0
    assert retry.kwargs["max_attempts"] == 1


@pytest.mark.asyncio
async def test_ranker_excludes_restricted_and_department_pages_and_prioritizes_targets():
    class DisabledLLM:
        is_configured = False

    ranker = RankerAgent(llm_client=DisabledLLM())
    profile = StudentProfile(
        target_degree_level="Master's",
        target_countries=["Germany"],
    )
    target_names = [
        "DAAD Research Excellence Grant",
        "Berlin Engineering Fellowship",
        "Bavarian Graduate Scholars Fund",
    ]
    target_opportunities = [
        VerifiedOpportunity(
            name=name,
            provider=f"University {index}",
            country="Germany",
            official_url=f"https://daad.de/fellowship-{index}",
            degree_level="Master's",
            match_status="eligible",
            discovery_source="tavily_live",
            verification_mode="heuristic",
            funding_amount_coverage="Full tuition",
            confidence=0.9,
        )
        for index, name in enumerate(target_names)
    ]
    outside_opportunity = VerifiedOpportunity(
        name="International Master's Grant",
        provider="University Abroad",
        country="Canada",
        official_url="https://example.edu/grant",
        degree_level="Master's",
        match_status="eligible",
        discovery_source="tavily_live",
        verification_mode="heuristic",
        funding_amount_coverage="Full tuition",
        confidence=1.0,
    )
    curated_unverified = VerifiedOpportunity(
        name="Curated Master's Award",
        provider="Example University",
        country="Germany",
        official_url="https://example.edu/curated",
        degree_level="Master's",
        match_status="partially_eligible",
        discovery_source="curated_fallback",
        verification_mode="unverified",
        confidence=0.0,
    )
    international_restricted = VerifiedOpportunity(
        name="Domestic-only Master's Grant",
        provider="University",
        country="Germany",
        official_url="https://example.edu/domestic",
        degree_level="Master's",
        match_status="eligible",
        eligibility="Not open to Indian applicants.",
    )
    department_page = VerifiedOpportunity(
        name="Funding, Tuition Fees and Awards",
        provider="University",
        country="Germany",
        official_url="https://example.edu/funding",
        degree_level="Master's",
        match_status="eligible",
        eligibility="Only for students already enrolled at this university.",
    )
    candidates = [
        *target_opportunities,
        outside_opportunity,
        curated_unverified,
        international_restricted,
        department_page,
    ]

    results = await ranker.run(
        candidates, profile, EventBus(print_to_terminal=False)
    )

    ranked = results.top_opportunities
    assert all(item.country == "Germany" for item in ranked[:3])
    assert all(
        item.discovery_source != "curated_fallback" for item in ranked[:4]
    ), [(item.name, item.discovery_source, item.verification_mode) for item in ranked]
    assert not any(
        item.name in {"Domestic-only Master's Grant", "Funding, Tuition Fees and Awards"}
        for item in ranked
    )
    assert any("international/indian" in reason.lower() for reason in international_restricted.unmet_criteria)
    assert any("enrolled students" in reason.lower() for reason in department_page.unmet_criteria)


def test_writer_uses_profile_facts_and_personal_intent_placeholders():
    from agents.writer_agent import WriterAgent

    profile = StudentProfile(
        name="Test Applicant",
        degree="Bachelor of Science",
        field="Computer Science",
        skills=["Python"],
    )
    verified = VerifiedOpportunity(
        name="Example Master's Scholarship",
        provider="Example University",
        country="Germany",
        official_url="https://example.edu/scholarship",
    )
    opportunity = RankedOpportunity(
        rank=1,
        name=verified.name,
        provider=verified.provider,
        country=verified.country,
        official_url=verified.official_url,
        fit_score=80.0,
        reasoning="Test opportunity.",
        funding="unverified",
        deadline="unverified",
        verified_data=verified,
    )

    writer = WriterAgent()
    facts = writer._collect_grounded_facts(profile)
    draft = writer._generate_grounded_sop(opportunity, profile, facts)

    assert "Test Applicant" in draft
    assert "Bachelor of Science" in draft
    assert "Python" in draft
    assert "[Add your own career goal here]" in draft
    assert "work-experience requirements" in draft
    assert "high distinction" not in draft
    assert "derived solely from my academic and professional record" not in draft


def test_official_domain_ranking_bonus():
    from agents.research_agent import is_official_domain
    from agents.ranker_agent import RankerAgent

    assert is_official_domain("https://cs.stanford.edu/admissions") is True
    assert is_official_domain("https://www.daad.de/scholarships") is True
    assert is_official_domain("https://www.random-blog-123.com/post") is False

    ranker = RankerAgent()
    profile = StudentProfile(name="Test", target_degree_level="Master's", target_countries=["Germany"])
    opp_official = VerifiedOpportunity(
        name="Official Program",
        provider="TU Munich",
        country="Germany",
        official_url="https://tum.edu/scholarship",
        funding_amount_coverage="Full tuition",
        match_status="eligible",
        confidence=0.8,
    )
    opp_unofficial = VerifiedOpportunity(
        name="Unofficial Program",
        provider="Unknown",
        country="Germany",
        official_url="https://random-site.net/info",
        funding_amount_coverage="Full tuition",
        match_status="eligible",
        confidence=0.8,
    )
    res = ranker._rank_heuristically([opp_unofficial, opp_official], profile)
    # opp_official gets +15 bonus and should rank first
    assert res.top_opportunities[0].official_url == "https://tum.edu/scholarship"


def test_llm_cache_on_disk():
    from llm_client import LLMClient
    from config import settings

    client = LLMClient()
    prompt = "TEST_PROMPT_UNIQUE_12345678"
    p_hash = client._compute_prompt_hash(prompt)

    client._set_cached_response(p_hash, "CACHED_OUTPUT", "gemini-test")
    retrieved = client._get_cached_response(p_hash)
    assert retrieved == "CACHED_OUTPUT"

    # Clean up
    cache_file = settings.LLM_CACHE_DIR / f"{p_hash}.json"
    if cache_file.exists():
        cache_file.unlink()


def test_deadline_parsing_and_closed_detection():
    from agents.verifier_agent import _parse_deadline
    from datetime import date

    # Unverified / empty
    parsed, norm = _parse_deadline("unverified")
    assert parsed is None
    assert norm == "unverified"

    parsed, norm = _parse_deadline("")
    assert parsed is None

    # Vague text with no parseable date
    parsed, norm = _parse_deadline("deadline detail for each")
    assert parsed is None
    assert norm == "unverified"

    # Valid future date (2027-01-15)
    parsed, norm = _parse_deadline("2027-01-15")
    assert parsed is not None
    assert norm == "2027-01-15"
    assert parsed >= date.today()

    # Past date — should be labelled closed
    parsed, norm = _parse_deadline("2024-06-30")
    assert parsed is not None
    assert "closed" in norm
    assert "2024-06-30" in norm

    # Date embedded in text
    parsed, norm = _parse_deadline("Application deadline: September 30, 2025")
    assert parsed is not None
    assert "closed" in norm  # Sep 30 2025 is before Oct 20 2026


def test_evidence_sanity_rejects_generic_snippets():
    """Generic/question/scam snippets must be rejected and field set to unverified."""
    from agents.verifier_agent import _is_generic_snippet

    assert _is_generic_snippet("What does the scholarship cover?") is True
    assert _is_generic_snippet("Warning against fake scholarship offers") is True
    assert _is_generic_snippet("phishing attack on scholarship portal") is True
    assert _is_generic_snippet("deadline detail for each") is True
    assert _is_generic_snippet("eligibility and deadline detail") is True
    assert _is_generic_snippet("Eligibility:") is True  # heading-only
    assert _is_generic_snippet("") is True

    # Real evidence snippets must NOT be rejected
    assert _is_generic_snippet("Full tuition waiver and \u20ac1,200/month stipend") is False
    assert _is_generic_snippet("Open to all international students from 183 countries") is False
    assert _is_generic_snippet("Submit application through the online portal by January 2027") is False


@pytest.mark.asyncio
async def test_aggregator_excluded_not_in_top5():
    """Candidates marked aggregator_excluded must be filtered from top-5 by the ranker."""
    from agents.ranker_agent import RankerAgent

    ranker = RankerAgent()
    profile = StudentProfile(name="Test", target_degree_level="Master's", target_countries=["Germany"])

    agg_excl = VerifiedOpportunity(
        name="59+ Scholarships in Europe | AggregatorSite",
        provider="AggregatorSite",
        country="Germany",
        official_url="https://aggregatorsite.com/list",
        match_status="aggregator_excluded",
        funding_amount_coverage="unverified",
    )
    real_opp = VerifiedOpportunity(
        name="DAAD Research Grant",
        provider="DAAD",
        country="Germany",
        official_url="https://daad.de/scholarships",
        degree_level="Master's",
        funding_amount_coverage="Full tuition + monthly stipend",
        match_status="eligible",
        confidence=0.9,
    )

    bus = EventBus(print_to_terminal=False)
    results = await ranker.run([agg_excl, real_opp], profile, bus)
    top_names = [o.name for o in results.top_opportunities]
    assert "DAAD Research Grant" in top_names
    assert "59+ Scholarships in Europe | AggregatorSite" not in top_names


def test_heuristic_eligibility_fit_checks():
    """Heuristic verifier must answer 3 eligibility questions and populate matched/unmet correctly."""
    from agents.verifier_agent import VerifierAgent

    agent = VerifierAgent()
    profile = StudentProfile(
        name="Test",
        degree="Bachelor of Science in CS",
        target_degree_level="Master's",
        field="Computer Science",
        cgpa="8.5/10",
        target_countries=["Germany"],
    )
    cand = CandidateOpportunity(
        name="Graduate Masters Scholarship",
        provider="University of Munich",
        country="Germany",
        official_url="https://lmu.de/scholarship",
    )

    page_international = (
        "This graduate scholarship is open to international students from all countries worldwide. "
        "It provides full tuition coverage and a monthly stipend of \u20ac850. "
        "Applications are for students intending to enroll in a Master's program."
    )
    res = agent._heuristic_verifier(cand, page_international, profile)

    # (a) must detect international eligibility
    assert any("(a) Open to international" in m for m in res.matched_criteria)
    # (b) must confirm prospective student
    assert any("(b) Open to prospective" in m for m in res.matched_criteria)
    # (c) degree level must match
    assert any("(c) Degree level matches" in m for m in res.matched_criteria)

    # Test domestic-only page: (a) must appear in unmet
    page_domestic = (
        "This program is only for domestic students who are US citizens only. "
        "Full tuition reimbursement available."
    )
    res2 = agent._heuristic_verifier(cand, page_domestic, profile)
    assert any("(a) NOT open" in u or "domestic" in u.lower() for u in res2.unmet_criteria)
