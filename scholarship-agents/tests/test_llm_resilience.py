from types import SimpleNamespace

import pytest

from config import settings
from agents.ranker_agent import RankerAgent
from agents.verifier_agent import VerifierAgent
from event_bus import EventBus
from llm_client import LLMClient, LLM_CALL_COUNTS, reset_call_counts
from models import CandidateOpportunity, StudentProfile, VerifiedOpportunity


def _response(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


@pytest.mark.asyncio
async def test_gpt_oss_uses_reasoning_and_completion_budget(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_MODEL", "openai/gpt-oss-120b")
    reset_call_counts()
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return _response('{"ok": true}')

    client = LLMClient(api_key="")
    client._groq_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = await client._call_groq(
        "return json", None, 0.0, 128, "openai/gpt-oss-120b",
        json_mode=True, event_bus=None, agent_name="test",
    )

    assert result == '{"ok": true}'
    assert captured["reasoning_effort"] == "low"
    assert captured["max_completion_tokens"] >= 8192
    assert captured["response_format"] == {"type": "json_object"}
    assert "max_tokens" not in captured
    assert LLM_CALL_COUNTS["models"]["openai/gpt-oss-120b"] == 1


@pytest.mark.asyncio
async def test_unsupported_json_parameter_retries_without_it():
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        if "response_format" in kwargs:
            raise ValueError("Unsupported parameter: response_format")
        return _response('{"ok": true}')

    client = LLMClient(api_key="")
    client._groq_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = await client._call_groq(
        "return json", None, 0.0, 4096, "llama-test",
        json_mode=True, event_bus=None, agent_name="test",
    )

    assert result == '{"ok": true}'
    assert len(requests) == 2
    assert "response_format" in requests[0]
    assert "response_format" not in requests[1]


@pytest.mark.asyncio
async def test_model_not_found_moves_to_groq_fallback(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDERS", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GROQ_MODEL", "missing-model")
    monkeypatch.setattr(settings, "GROQ_MODEL_FALLBACKS", "working-model")
    requests = []

    def create(**kwargs):
        requests.append(kwargs["model"])
        if kwargs["model"] == "missing-model":
            raise ValueError("404 model not found")
        return _response("answer")

    client = LLMClient(api_key="")
    client._groq_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    text = await client._generate_text_for_provider(
        "groq", "test", "prompt", None, None, 0.0, 1
    )

    assert text == "answer"
    assert requests == ["missing-model", "working-model"]
    assert client.model == "working-model"


@pytest.mark.asyncio
async def test_generate_json_retries_with_parse_feedback_and_json_mode(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDERS", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GROQ_MODEL", "llama-test")
    monkeypatch.setattr(settings, "GROQ_MODEL_FALLBACKS", "")
    requests = []
    responses = iter(["not json", '```json\n{"ok": true}\n```'])

    def create(**kwargs):
        requests.append(kwargs)
        return _response(next(responses))

    client = LLMClient(api_key="")
    client._groq_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = await client.generate_json("test", "Produce an object")

    assert result == {"ok": True}
    assert len(requests) == 2
    assert all(item["response_format"] == {"type": "json_object"} for item in requests)
    assert "Return only valid JSON" in requests[0]["messages"][0]["content"]
    assert "CRITICAL FEEDBACK" in requests[1]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_empty_gpt_oss_response_retries_with_larger_budget(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_MODEL", "openai/gpt-oss-120b")
    captured_budgets = []
    responses = iter(["", '{"ok": true}'])

    def create(**kwargs):
        captured_budgets.append(kwargs["max_completion_tokens"])
        return _response(next(responses))

    client = LLMClient(api_key="")
    client._groq_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    text = await client._generate_text_for_provider(
        "groq", "test", "prompt", None, None, 0.0, 2, json_mode=True
    )

    assert text == '{"ok": true}'
    assert captured_budgets == [8192, 16384]


@pytest.mark.asyncio
async def test_verifier_rejects_answers_without_exact_page_evidence():
    class FakeLLM:
        is_configured = True

        async def generate_json(self, **kwargs):
            return {
                "degree_level": "Master's",
                "eligibility": "Open to all international applicants",
                "funding_amount_coverage": "Full tuition and stipend",
                "deadline": "2027-01-15",
                "how_to_apply": "Apply online",
                "required_documents": ["Transcript"],
                "evidence": {
                    "degree_level": "The Master of Science programme is two years.",
                    "eligibility": "Open to all international applicants.",
                    "funding_amount_coverage": "A full tuition scholarship and stipend are awarded.",
                    "deadline": "Applications close on 15 January 2027.",
                    "how_to_apply": "Apply online through the university portal.",
                    "required_documents": "Submit a CV and motivation letter.",
                },
                "match_status": "eligible",
            }

    page = (
        "The Master of Science programme is two years. "
        "Applications close on 15 January 2027."
    )
    agent = VerifierAgent(llm_client=FakeLLM())
    verified = await agent._verify_with_llm(
        candidate=CandidateOpportunity(
            name="Test Master's Scholarship",
            provider="Example University",
            country="Germany",
            official_url="https://example.edu/program",
        ),
        page_text=page,
        profile=StudentProfile(target_degree_level="Master's"),
        event_bus=EventBus(print_to_terminal=False),
    )

    assert verified.degree_level == "Master's"
    assert verified.funding_amount_coverage == "unverified"
    assert verified.eligibility == "unverified"
    assert verified.deadline == "2027-01-15"
    assert verified.how_to_apply == "unverified"
    assert verified.required_documents == []
    assert set(verified.evidence) == {"degree_level", "deadline"}


def test_scholarship_directory_is_classified_as_aggregator():
    assert VerifierAgent._is_aggregator(
        "Scholarships in Switzerland 2027",
        "https://www.globaladmissions.com/scholarships/country/switzerland",
    )


@pytest.mark.asyncio
async def test_ranker_only_includes_page_verified_masters():
    class OfflineLLM:
        is_configured = False

    profile = StudentProfile(target_degree_level="Master's")
    master = VerifiedOpportunity(
        name="Master of Science scholarship",
        provider="University",
        country="Germany",
        official_url="https://example.edu/masters",
        degree_level="Master's",
        funding_amount_coverage="Full tuition",
        evidence={
            "degree_level": "The Master of Science programme is two years.",
            "funding_amount_coverage": "Full tuition is covered.",
        },
        match_status="eligible",
    )
    unconfirmed_degree = VerifiedOpportunity(
        name="Postgraduate award",
        provider="University",
        country="Germany",
        official_url="https://example.edu/award",
        funding_amount_coverage="Full tuition",
        match_status="eligible",
    )
    results = await RankerAgent(llm_client=OfflineLLM()).run(
        [master, unconfirmed_degree],
        profile,
        EventBus(print_to_terminal=False),
    )

    assert [item.name for item in results.top_opportunities] == [
        "Master of Science scholarship"
    ]
    assert results.top_opportunities[0].degree_level == "Master's"
