from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

import agents.profile_agent as profile_agent_module
from agents.profile_agent import (
    ProfileAgent,
    normalize_github_username,
    valid_github_token,
)
from event_bus import EventBus
from models import StudentProfile


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://github.com/Harshvardhan8091", "Harshvardhan8091"),
        ("github.com/name/", "name"),
        ("@octocat", "octocat"),
        ("octocat", "octocat"),
        ("https://example.com/octocat", None),
        ("invalid name", None),
    ],
)
def test_normalize_github_profile_input(value, expected):
    assert normalize_github_username(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", False),
        ("your_github_token", False),
        ("ghp_your_placeholder", False),
        ("placeholder", False),
        ("ghp_actualformat", True),
        ("github_pat_actualformat", True),
    ],
)
def test_github_token_placeholder_handling(value, expected):
    assert valid_github_token(value) is expected


def test_scanned_pdf_uses_pdfplumber_before_ocr(monkeypatch):
    class FakePage:
        def extract_text(self):
            return "short"

    class FakePdf:
        pages = [FakePage()]

    monkeypatch.setattr(profile_agent_module, "PdfReader", lambda _path: FakePdf())

    class PdfPlumberPage:
        def extract_text(self):
            return "Recovered scanned transcript text " * 3

    class PdfPlumberPdf:
        pages = [PdfPlumberPage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(
        sys.modules,
        "pdfplumber",
        SimpleNamespace(open=lambda _path: PdfPlumberPdf()),
    )
    text, error = ProfileAgent._extract_pdf_text_with_status("scanned.pdf")
    assert len(text) >= 50
    assert not error


def test_scanned_pdf_reports_unreadable_when_optional_extractors_are_missing(monkeypatch):
    class FakePage:
        def extract_text(self):
            return ""

    class FakePdf:
        pages = [FakePage()]

    monkeypatch.setattr(profile_agent_module, "PdfReader", lambda _path: FakePdf())
    monkeypatch.delitem(sys.modules, "pdfplumber", raising=False)
    monkeypatch.delitem(sys.modules, "pytesseract", raising=False)
    monkeypatch.delitem(sys.modules, "pdf2image", raising=False)
    text, error = ProfileAgent._extract_pdf_text_with_status("scanned.pdf")
    assert text == ""
    assert "Marksheet is a scanned image" in error
    assert "text-based PDF or TXT" in error


def test_scanned_pdf_uses_ocr_after_pdfplumber(monkeypatch):
    class EmptyPage:
        def extract_text(self):
            return ""

    class FakePdf:
        pages = [EmptyPage()]

    class PdfPlumberPdf:
        pages = [EmptyPage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(profile_agent_module, "PdfReader", lambda _path: FakePdf())
    monkeypatch.setitem(
        sys.modules,
        "pdfplumber",
        _fake_module("pdfplumber", open=lambda _path: PdfPlumberPdf()),
    )
    monkeypatch.setitem(
        sys.modules,
        "pytesseract",
        _fake_module("pytesseract", image_to_string=lambda _image: "OCR transcript recovered " * 4),
    )
    monkeypatch.setitem(
        sys.modules,
        "pdf2image",
        _fake_module("pdf2image", convert_from_path=lambda *_args, **_kwargs: [object()]),
    )
    text, error = ProfileAgent._extract_pdf_text_with_status("scanned.pdf")
    assert "OCR transcript recovered" in text
    assert len(text) >= 50
    assert not error


def _fake_module(name, **attributes):
    module = ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


@pytest.mark.asyncio
async def test_github_profile_fetch_reports_repositories_and_stars(monkeypatch):
    class FakeResponse:
        def __init__(self, status, payload):
            self.status_code = status
            self.payload = payload

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.responses = [
                FakeResponse(200, {"name": "Octocat", "bio": "Builds things", "public_repos": 2}),
                FakeResponse(
                    200,
                    [
                        {"name": "small", "description": "Small repo", "language": "Python", "stargazers_count": 2, "html_url": "https://github.com/octocat/small"},
                        {"name": "popular", "description": "Popular repo", "language": "Rust", "stargazers_count": 9, "html_url": "https://github.com/octocat/popular"},
                    ],
                ),
            ]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, headers):
            self.calls.append((url, headers))
            return self.responses.pop(0)

    fake_client = FakeClient()
    monkeypatch.setattr(profile_agent_module.settings, "GITHUB_TOKEN", "")
    monkeypatch.setattr(
        profile_agent_module.httpx,
        "AsyncClient",
        lambda **_kwargs: fake_client,
    )
    data = await ProfileAgent()._fetch_github_profile(
        "octocat", EventBus(print_to_terminal=False)
    )

    assert data["status"] == "ok"
    assert data["name"] == "Octocat"
    assert data["public_repos"] == 2
    assert data["repos_found"] == 2
    assert data["stars"] == 11
    assert data["repos_summary"][0]["name"] == "popular"
    assert data["repos_summary"][0]["description"] == "Popular repo"
    assert fake_client.calls[0][0] == "https://api.github.com/users/octocat"
    assert fake_client.calls[1][0] == (
        "https://api.github.com/users/octocat/repos?per_page=100&sort=updated"
    )
    assert fake_client.calls[0][1]["User-Agent"]


@pytest.mark.asyncio
async def test_github_rate_limit_is_not_reported_as_empty_repository_list(monkeypatch):
    class FakeResponse:
        status_code = 403

        @staticmethod
        def json():
            return {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url, headers):
            assert headers["User-Agent"]
            return FakeResponse()

    monkeypatch.setattr(profile_agent_module.settings, "GITHUB_TOKEN", "")
    monkeypatch.setattr(
        profile_agent_module.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeClient(),
    )
    data = await ProfileAgent()._fetch_github_profile(
        "octocat", EventBus(print_to_terminal=False)
    )
    assert data["status"] == "rate_limited"
    assert data["repos_found"] is None
    assert "rate limit" in data["error"].lower()


@pytest.mark.asyncio
async def test_user_supplied_profile_fields_override_inferred_values(tmp_path, monkeypatch):
    resume_path = tmp_path / "resume.txt"
    marksheet_path = tmp_path / "marksheet.txt"
    resume_path.write_text("Resume details " * 8, encoding="utf-8")
    marksheet_path.write_text("Academic transcript " * 8, encoding="utf-8")
    agent = ProfileAgent()
    monkeypatch.setattr(
        agent,
        "_extract_document_text",
        lambda path, _label="Marksheet": (
            ("Resume details " * 8, "") if path.endswith("resume.txt")
            else ("Academic transcript " * 8, "")
        ),
    )
    monkeypatch.setattr(
        agent,
        "_fetch_github_profile",
        lambda username, _bus: asyncio.sleep(
            0,
            result={
                "username": username,
                "status": "ok",
                "name": "GitHub Name",
                "public_repos": 2,
                "repos_found": 2,
                "stars": 7,
                "top_languages": ["Python"],
                "repos_summary": [],
            },
        ),
    )

    async def inferred_profile(**_kwargs):
        return StudentProfile(
            target_degree_level="PhD",
            target_countries=["France"],
            financial_need="Not a concern",
            interests=["inferred interest"],
            gaps_or_missing_info=["Target countries are missing", "Financial need missing"],
        )

    monkeypatch.setattr(agent, "_synthesize_profile_llm", inferred_profile)
    profile = await agent.run(
        str(resume_path),
        str(marksheet_path),
        "https://github.com/octocat",
        EventBus(print_to_terminal=False),
        target_countries="Germany, Canada",
        target_degree_level="Master's",
        financial_need="Full funding needed",
        interests="robotics, public health",
    )
    assert profile.target_countries == ["Germany", "Canada"]
    assert profile.target_degree_level == "Master's"
    assert profile.financial_need == "Full funding needed"
    assert profile.interests == ["robotics", "public health"]
    assert profile.gaps_or_missing_info == []
    assert profile.inputs_read["marksheet"]["characters_read"] > 50
    assert profile.inputs_read["github"]["username"] == "octocat"
    assert set(profile.inputs_read["user_supplied_fields"]) == {
        "target_countries",
        "target_degree_level",
        "financial_need",
        "interests",
    }
