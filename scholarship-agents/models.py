from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field


class StudentProject(BaseModel):
    title: str
    description: Optional[str] = None
    technologies: List[str] = Field(default_factory=list)
    github_url: Optional[str] = None


class StudentProfile(BaseModel):
    name: Optional[str] = None
    degree: Optional[str] = None
    target_degree_level: Optional[str] = None
    field: Optional[str] = None
    cgpa: Optional[Union[float, str]] = None
    skills: List[str] = Field(default_factory=list)
    projects: List[Union[StudentProject, Dict[str, Any], str]] = Field(default_factory=list)
    achievements: List[str] = Field(default_factory=list)
    interests: List[str] = Field(default_factory=list)
    target_countries: List[str] = Field(default_factory=list)
    financial_need: Optional[str] = None
    gaps_or_missing_info: List[str] = Field(default_factory=list)
    github_bio: Optional[str] = None
    github_public_repos: Optional[int] = None
    github_stars: Optional[int] = None
    github_top_languages: List[str] = Field(default_factory=list)


class CandidateOpportunity(BaseModel):
    name: str
    provider: str
    country: str
    official_url: str
    description: Optional[str] = None
    degree_level: Optional[str] = None
    source_query: Optional[str] = None
    # Item 3: Source transparency
    discovery_source: Literal["tavily_live", "curated_fallback"] = "curated_fallback"


class VerifiedOpportunity(BaseModel):
    name: str
    provider: str
    country: str
    official_url: str
    degree_level: str = "unverified"
    eligibility: str = "unverified"
    funding_amount_coverage: str = "unverified"
    deadline: str = "unverified"
    required_documents: List[str] = Field(default_factory=list)
    how_to_apply: str = "unverified"
    evidence: Dict[str, str] = Field(default_factory=dict)
    match_status: Literal["eligible", "ineligible", "partially_eligible", "unverified", "aggregator_excluded"] = "unverified"
    matched_criteria: List[str] = Field(default_factory=list)
    unmet_criteria: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_urls: List[str] = Field(default_factory=list)
    # Item 3: Source transparency
    discovery_source: Literal["tavily_live", "curated_fallback"] = "curated_fallback"
    verification_mode: Literal["llm_live", "heuristic", "unverified"] = "unverified"
    # Item 4: Deadline tracking
    deadline_source_url: Optional[str] = None
    last_checked: Optional[str] = Field(default_factory=lambda: date.today().isoformat())
    student_fit_reason: Optional[str] = None


class RankedOpportunity(BaseModel):
    rank: int
    name: str
    provider: str
    country: str
    official_url: str
    degree_level: str = "unverified"
    fit_score: float = Field(ge=0.0, le=100.0)
    reasoning: str
    funding: str
    deadline: str
    evidence: Dict[str, str] = Field(default_factory=dict)
    application_steps: List[str] = Field(default_factory=list)
    verified_data: VerifiedOpportunity
    # Item 3: Source transparency (propagated from verified_data)
    discovery_source: Literal["tavily_live", "curated_fallback"] = "curated_fallback"
    verification_mode: Literal["llm_live", "heuristic", "unverified"] = "unverified"
    # Item 4: Deadline tracking (propagated from verified_data)
    deadline_source_url: Optional[str] = None
    last_checked: Optional[str] = None


class RankedResults(BaseModel):
    top_opportunities: List[RankedOpportunity] = Field(default_factory=list)
    total_candidates_found: int = 0
    total_verified: int = 0
    ranking_summary: str = ""


class SOPDraft(BaseModel):
    opportunity_name: str
    provider: str
    student_name: Optional[str] = None
    target_program_or_scholarship: str
    sop_markdown: str
    grounded_facts_used: List[str] = Field(default_factory=list)
    disclaimer: str = "[DRAFT - FOR STUDENT REVIEW ONLY - DO NOT SUBMIT WITHOUT PERSONAL REVIEW & REVISION]"


class PipelineEvent(BaseModel):
    agent: str
    type: Literal["started", "progress", "recovery", "done", "error"]
    message: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: Optional[Dict[str, Any]] = None
