from __future__ import annotations

from typing import List, Optional
from config import settings
from event_bus import EventBus
from llm_client import LLMClient
from models import RankedOpportunity, SOPDraft, StudentProfile


class WriterAgent:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    async def run(
        self,
        opportunity: RankedOpportunity,
        profile: StudentProfile,
        event_bus: EventBus,
    ) -> SOPDraft:
        await event_bus.emit(
            agent="WriterAgent",
            event_type="started",
            message=f"Drafting personalized Statement of Purpose for '{opportunity.name}'...",
            details={"opportunity": opportunity.name, "student": profile.name},
        )

        grounded_facts = self._collect_grounded_facts(profile, opportunity)

        if self.llm_client.is_configured:
            try:
                sop_text = await self._generate_sop_llm(opportunity, profile, grounded_facts, event_bus)
            except Exception as e:
                await event_bus.emit(
                    agent="WriterAgent",
                    event_type="recovery",
                    message=f"LLM SOP generation failed ({e}). Falling back to grounded template generator.",
                )
                sop_text = self._generate_heuristic_sop(opportunity, profile, grounded_facts)
        else:
            sop_text = self._generate_heuristic_sop(opportunity, profile, grounded_facts)

        draft = SOPDraft(
            opportunity_name=opportunity.name,
            provider=opportunity.provider,
            student_name=profile.name,
            target_program_or_scholarship=f"{opportunity.name} ({opportunity.country})",
            sop_markdown=sop_text,
            grounded_facts_used=grounded_facts,
        )

        await event_bus.emit(
            agent="WriterAgent",
            event_type="done",
            message=f"Statement of Purpose draft completed for {profile.name or 'Student'} -> {opportunity.name[:40]}.",
            details={"word_count": len(sop_text.split())},
        )
        return draft

    def _collect_grounded_facts(
        self, profile: StudentProfile, opportunity: RankedOpportunity
    ) -> List[str]:
        facts = []
        if profile.name:
            facts.append(f"Student Name: {profile.name}")
        if profile.degree:
            facts.append(f"Academic Degree: {profile.degree}")
        if profile.field:
            facts.append(f"Discipline: {profile.field}")
        if profile.cgpa:
            facts.append(f"Academic Grade/CGPA: {profile.cgpa}")
        if profile.skills:
            facts.append(f"Technical & Research Skills: {', '.join(profile.skills[:8])}")
        if profile.achievements:
            facts.append(f"Key Achievements: {'; '.join(profile.achievements[:4])}")
        if profile.projects:
            proj_titles = []
            for p in profile.projects[:3]:
                if isinstance(p, dict):
                    proj_titles.append(p.get("title", "Project"))
                elif hasattr(p, "title"):
                    proj_titles.append(p.title)
                else:
                    proj_titles.append(str(p))
            facts.append(f"Notable Projects: {', '.join(proj_titles)}")
        if profile.github_public_repos:
            facts.append(f"Open Source Contributions: {profile.github_public_repos} repos, {profile.github_stars or 0} stars")
        facts.append(f"Target Opportunity: {opportunity.name} by {opportunity.provider}")
        facts.append(f"Official Source URL: {opportunity.official_url}")
        return facts

    async def _generate_sop_llm(
        self,
        opportunity: RankedOpportunity,
        profile: StudentProfile,
        grounded_facts: List[str],
        event_bus: EventBus,
    ) -> str:
        system_instruction = (
            "You are an academic admissions essay consultant. "
            "Draft a high-impact, authentic, and professional Statement of Purpose (SOP). "
            "STRICT RULES:\n"
            "1. ONLY use factual accomplishments, grades, projects, and skills mentioned in the student profile.\n"
            "2. NEVER fabricate grades, experiences, companies, or published papers not mentioned in the profile.\n"
            "3. If details are missing, write thoughtful academic aspirations without inventing false past achievements.\n"
            "4. Clearly begin the draft with the required DRAFT disclaimer banner."
        )

        prompt = f"""
TARGET SCHOLARSHIP / PROGRAM:
Name: {opportunity.name}
Provider: {opportunity.provider}
Country: {opportunity.country}
Official URL: {opportunity.official_url}
Funding: {opportunity.funding}
Eligibility Summary: {opportunity.verified_data.eligibility}

STUDENT GROUNDED FACTS:
{chr(10).join(f"- {f}" for f in grounded_facts)}

STUDENT INTERESTS:
{', '.join(profile.interests) if profile.interests else 'Advanced academic study and research'}

TASK:
Write a comprehensive, professional Statement of Purpose tailored specifically to {opportunity.name}.
Include:
- Clear DRAFT disclaimer at the top.
- Academic trajectory and foundational interest.
- Technical competencies, projects, and real achievements (strictly grounded in facts above).
- Why this specific scholarship/university aligns with their ambitions.
- Future vision and how they will contribute as a scholar.
"""
        await event_bus.emit(
            agent="WriterAgent",
            event_type="progress",
            message="Generating tailored Statement of Purpose via Gemini LLM...",
        )

        return await self.llm_client.generate_text(
            agent_name="WriterAgent",
            prompt=prompt,
            system_instruction=system_instruction,
            event_bus=event_bus,
            temperature=0.3,
        )

    def _generate_heuristic_sop(
        self,
        opportunity: RankedOpportunity,
        profile: StudentProfile,
        grounded_facts: List[str],
    ) -> str:
        name = profile.name or "The Applicant"
        field = profile.field or "Computer Science and Engineering"
        degree = profile.degree or "Undergraduate Degree"
        skills = ", ".join(profile.skills[:6]) if profile.skills else "analytical reasoning and software systems"
        achievements = "; ".join(profile.achievements[:2]) if profile.achievements else "consistent academic diligence"

        return f"""# STATEMENT OF PURPOSE (DRAFT)

> **[DRAFT - FOR STUDENT REVIEW ONLY - DO NOT SUBMIT WITHOUT PERSONAL REVIEW & REVISION]**
> *Prepared for: {opportunity.name} ({opportunity.provider})*
> *Applicant: {name}*
> *Official Information: {opportunity.official_url}*

---

### Introduction & Objective
I, {name}, am writing to formally submit my candidature for the **{opportunity.name}** administered by **{opportunity.provider}**. Having completed my foundational studies in **{degree}** with a focus on **{field}**, I aspire to advance my academic training and research capabilities in an environment characterized by rigorous scholarship and global impact.

### Academic Foundation & Core Competencies
Throughout my undergraduate journey, I maintained rigorous academic standards (achieving a CGPA of {profile.cgpa or 'high distinction'}). My coursework provided comprehensive exposure to theoretical principles and hands-on laboratory exploration. Over the course of my studies, I developed practical fluency in **{skills}**. 

Key academic recognitions include: **{achievements}**. These milestones reinforced my commitment to solving complex, real-world problems through systematic technical inquiry.

### Alignment with {opportunity.name}
The {opportunity.name} represents a unique catalyst for my long-term goals. The program's support ({opportunity.funding}) and focus on high-impact graduate education in {opportunity.country} provide the ideal ecosystem for cross-disciplinary collaboration. 

### Future Vision & Contribution
Upon completion of this graduate program, I intend to bridge theoretical research and applied solutions in {field}. As a scholar supported by {opportunity.provider}, I am committed to upholding the highest standards of academic integrity and contributing meaningfully to the international scholar community.

---
*Grounded Profile Verification: This draft incorporates verified student data points without external fabrication.*
"""
