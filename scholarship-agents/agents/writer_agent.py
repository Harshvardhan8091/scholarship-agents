from __future__ import annotations

from typing import List

from event_bus import EventBus
from models import RankedOpportunity, SOPDraft, StudentProfile


class WriterAgent:
    DRAFT_DISCLAIMER = "[DRAFT - FOR STUDENT REVIEW ONLY - DO NOT SUBMIT WITHOUT PERSONAL REVIEW & REVISION]"

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

        grounded_facts = self._collect_grounded_facts(profile)
        sop_text = self._generate_grounded_sop(opportunity, profile, grounded_facts)
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

    @staticmethod
    def _collect_grounded_facts(profile: StudentProfile) -> List[str]:
        facts: List[str] = []
        if profile.name:
            facts.append(f"Name: {profile.name}")
        if profile.degree:
            facts.append(f"Academic degree: {profile.degree}")
        if profile.field:
            facts.append(f"Field: {profile.field}")
        if profile.cgpa:
            facts.append(f"CGPA: {profile.cgpa}")
        if profile.skills:
            facts.append(f"Skills: {', '.join(profile.skills[:8])}")
        if profile.achievements:
            facts.append(f"Achievements: {'; '.join(profile.achievements[:4])}")
        if profile.projects:
            project_titles = []
            for project in profile.projects[:3]:
                if isinstance(project, dict):
                    title = project.get("title")
                    if title:
                        project_titles.append(str(title))
                elif hasattr(project, "title"):
                    project_titles.append(project.title)
                else:
                    project_titles.append(str(project))
            if project_titles:
                facts.append(f"Projects: {', '.join(project_titles)}")
        if profile.github_bio:
            facts.append(f"GitHub bio: {profile.github_bio}")
        if profile.github_public_repos is not None:
            facts.append(f"Public GitHub repositories: {profile.github_public_repos}")
        if profile.github_stars is not None:
            facts.append(f"GitHub stars: {profile.github_stars}")
        if profile.github_top_languages:
            facts.append(f"GitHub languages: {', '.join(profile.github_top_languages[:8])}")
        if profile.target_degree_level:
            facts.append(f"Target degree level: {profile.target_degree_level}")
        if profile.target_countries:
            facts.append(f"Target countries: {', '.join(profile.target_countries)}")
        if profile.interests:
            facts.append(f"Interests: {', '.join(profile.interests)}")
        if profile.financial_need:
            facts.append(f"Financial need: {profile.financial_need}")
        return facts

    def _generate_grounded_sop(
        self,
        opportunity: RankedOpportunity,
        profile: StudentProfile,
        grounded_facts: List[str],
    ) -> str:
        personal_details = "\n".join(f"- {fact}" for fact in grounded_facts)
        if not personal_details:
            personal_details = "- [Add relevant personal and academic details here]"
        applicant_name = profile.name or "[Add your name here]"

        return f"""# STATEMENT OF PURPOSE (DRAFT)

> **{self.DRAFT_DISCLAIMER}**
>
> **Drafting note:** Personal intentions are placeholders, not inferred facts. Complete [Add your own academic motivation here], [Add your own career goal here], and [Add how you plan to contribute here]. Check the official page for work-experience requirements, eligibility, required documents, and deadlines.
>
> *Prepared for: {opportunity.name} ({opportunity.provider})*
> *Applicant: {applicant_name}*
> *Official information: {opportunity.official_url}*

## Applicant information
{personal_details}

## Statement
I am applying for **{opportunity.name}**. My reasons for applying are [Add your own academic motivation here].

My academic and project background is summarized in the applicant information above. I would like to pursue [Add your own academic goal here] and plan to use this opportunity to [Add how you plan to contribute here].

My career goal is [Add your own career goal here]. I will revise this draft to describe my personal experiences and intentions accurately before using it.
"""
