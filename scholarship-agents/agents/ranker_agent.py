from __future__ import annotations

from typing import List, Optional
from config import settings
from event_bus import EventBus
from llm_client import LLMClient
from models import RankedOpportunity, RankedResults, StudentProfile, VerifiedOpportunity
from agents.research_agent import is_official_domain


class RankerAgent:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    async def run(
        self,
        verified_opportunities: List[VerifiedOpportunity],
        profile: StudentProfile,
        event_bus: EventBus,
    ) -> RankedResults:
        await event_bus.emit(
            agent="RankerAgent",
            event_type="started",
            message=f"Evaluating and ranking {len(verified_opportunities)} verified opportunities for student profile...",
        )

        if not verified_opportunities:
            await event_bus.emit(
                agent="RankerAgent",
                event_type="error",
                message="No verified opportunities available to rank.",
            )
            return RankedResults(
                top_opportunities=[],
                total_candidates_found=0,
                total_verified=0,
                ranking_summary="No opportunities were found or verified.",
            )

        # Filter out ineligible candidates (e.g. domestic-only, rejected listicles, aggregators)
        non_excluded = [o for o in verified_opportunities if o.match_status not in ("ineligible", "aggregator_excluded")]
        eligible_pool = [
            o for o in non_excluded if o.match_status in ("eligible", "partially_eligible")
        ]
        if not eligible_pool:
            eligible_pool = non_excluded if non_excluded else [
                o for o in verified_opportunities if o.match_status != "aggregator_excluded"
            ]

        # If LLM configured, generate rankings and tailored reasoning
        if self.llm_client.is_configured:
            try:
                results = await self._rank_with_llm(eligible_pool, profile, event_bus)
            except Exception as e:
                await event_bus.emit(
                    agent="RankerAgent",
                    event_type="recovery",
                    message=f"LLM ranking failed ({e}). Falling back to deterministic heuristic ranking.",
                )
                results = self._rank_heuristically(eligible_pool, profile)
        else:
            results = self._rank_heuristically(eligible_pool, profile)

        results.total_verified = len(verified_opportunities)

        top_names = [f"#{o.rank} {o.name[:35]}" for o in results.top_opportunities]
        await event_bus.emit(
            agent="RankerAgent",
            event_type="done",
            message=f"Selected top {len(results.top_opportunities)} opportunities: {', '.join(top_names)}.",
            details={
                "top_count": len(results.top_opportunities),
                "top_picks": [
                    {"rank": o.rank, "name": o.name, "score": o.fit_score}
                    for o in results.top_opportunities
                ],
            },
        )
        return results

    async def _rank_with_llm(
        self,
        opportunities: List[VerifiedOpportunity],
        profile: StudentProfile,
        event_bus: EventBus,
    ) -> RankedResults:
        target_deg = profile.target_degree_level or "Master's"
        system_instruction = (
            "You are an academic admissions and scholarship advisor. "
            "Select and rank the TOP 5 best-fit scholarship opportunities for this student. "
            "Evaluation criteria:\n"
            f"1. Target Degree Fit: Student is targeting {target_deg} studies. Match with {target_deg} programs/scholarships, NOT lower degree levels (never recommend Bachelor programs to a student targeting Master's).\n"
            "2. Official Domains: Prefer official university/government domains (.edu, .ac.*, .gov, daad.de, etc.) and give them a ranking bonus.\n"
            "3. Funding Coverage: Only rank real scholarships or degree programs with verified funding support (tuition waivers, stipends).\n"
            "4. Country Alignment: Preference for student's target countries if specified.\n"
            "CRITICAL: Never invent facts. Return valid JSON containing exactly the top 5 picks."
        )

        opps_summary = []
        for i, o in enumerate(opportunities[:15]):
            opps_summary.append({
                "index": i,
                "name": o.name,
                "provider": o.provider,
                "country": o.country,
                "official_url": o.official_url,
                "is_official_domain": is_official_domain(o.official_url),
                "funding": o.funding_amount_coverage,
                "deadline": o.deadline,
                "eligibility": o.eligibility,
                "matched_criteria": o.matched_criteria,
                "unmet_criteria": o.unmet_criteria,
                "match_status": o.match_status,
                "confidence": o.confidence,
                "how_to_apply": o.how_to_apply,
            })

        prompt = f"""
STUDENT PROFILE:
Name: {profile.name}
Degree: {profile.degree}
Target Degree Level: {target_deg}
Field: {profile.field}
CGPA: {profile.cgpa}
Skills: {profile.skills}
Achievements: {profile.achievements}
Target Countries: {profile.target_countries}
Financial Need: {profile.financial_need}

VERIFIED OPPORTUNITIES (Candidate Pool):
{opps_summary}

TASK:
Select the TOP 5 scholarship opportunities matching target degree level {target_deg}. Rank them from 1 to 5.
For each pick, write a concise reasoning explaining why it is an ideal match for this student's specific background.

OUTPUT JSON SCHEMA:
{{
  "ranking_summary": string,
  "top_5": [
     {{
        "rank": 1,
        "opportunity_index": int (index from Candidate Pool above),
        "fit_score": float (e.g. 95.0),
        "reasoning": string,
        "funding": string,
        "deadline": string,
        "application_steps": [string]
     }}
  ]
}}
"""
        json_data = await self.llm_client.generate_json(
            agent_name="RankerAgent",
            prompt=prompt,
            system_instruction=system_instruction,
            event_bus=event_bus,
            temperature=0.2,
        )

        ranked_picks: List[RankedOpportunity] = []
        if isinstance(json_data, dict) and "top_5" in json_data:
            for item in json_data["top_5"]:
                idx = item.get("opportunity_index", 0)
                if 0 <= idx < len(opportunities):
                    v_data = opportunities[idx]
                else:
                    v_data = opportunities[0]

                ranked_picks.append(
                    RankedOpportunity(
                        rank=item.get("rank", len(ranked_picks) + 1),
                        name=v_data.name,
                        provider=v_data.provider,
                        country=v_data.country,
                        official_url=v_data.official_url,
                        fit_score=float(item.get("fit_score", 90.0 - len(ranked_picks) * 3)),
                        reasoning=str(item.get("reasoning", "")),
                        funding=str(item.get("funding", v_data.funding_amount_coverage)),
                        deadline=str(item.get("deadline", v_data.deadline)),
                        evidence=v_data.evidence,
                        application_steps=list(item.get("application_steps", [v_data.how_to_apply])),
                        verified_data=v_data,
                        discovery_source=v_data.discovery_source,
                        verification_mode=v_data.verification_mode,
                        deadline_source_url=v_data.deadline_source_url,
                        last_checked=v_data.last_checked,
                    )
                )

        if not ranked_picks:
            return self._rank_heuristically(opportunities, profile)

        return RankedResults(
            top_opportunities=ranked_picks[:5],
            total_candidates_found=len(opportunities),
            total_verified=len(opportunities),
            ranking_summary=str(json_data.get("ranking_summary", "Top scholarship recommendations based on student academic profile and verified eligibility.")),
        )

    def _rank_heuristically(
        self,
        opportunities: List[VerifiedOpportunity],
        profile: StudentProfile,
    ) -> RankedResults:
        """Deterministic scoring algorithm for offline runs."""
        scored = []
        target_countries = [c.lower() for c in (profile.target_countries or [])]
        target_deg = (profile.target_degree_level or "Master's").lower()

        for opp in opportunities:
            score = 50.0
            if opp.match_status == "eligible":
                score += 15.0
            elif opp.match_status == "partially_eligible":
                score += 8.0

            # Target country bonus
            if any(tc in opp.country.lower() for tc in target_countries):
                score += 10.0

            # Item 2: Ranking bonus for official domains (.edu, .ac.*, .gov, daad.de, etc.)
            if is_official_domain(opp.official_url):
                score += 10.0

            # Item 3: Degree level check — match with target_degree_level, penalize mismatch
            opp_text = f"{opp.name} {opp.provider}".lower()
            if "master" in target_deg:
                if "bachelor" in opp_text or "undergraduate" in opp_text:
                    score -= 30.0  # Penalize Bachelor programs for Master's target
                elif "master" in opp_text or "graduate" in opp_text or "postgraduate" in opp_text:
                    score += 8.0
            elif "phd" in target_deg:
                if "bachelor" in opp_text or "undergraduate" in opp_text:
                    score -= 30.0
                elif "phd" in opp_text or "doctoral" in opp_text or "research" in opp_text:
                    score += 8.0

            # Only keep programs with verified or authentic funding
            if opp.funding_amount_coverage and "unverified" not in opp.funding_amount_coverage.lower():
                score += 5.0

            score += min(opp.confidence * 4.0, 4.0)

            # Item 4: Penalize unverified or closed deadlines
            if opp.deadline and "unverified" in opp.deadline.lower():
                score -= 8.0
            elif opp.deadline and "closed" in opp.deadline.lower():
                score -= 25.0  # Heavy penalty for past/closed deadlines

            scored.append((score, opp))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_picks: List[RankedOpportunity] = []

        target_label = profile.target_degree_level or "Master's"
        for rank, (score, opp) in enumerate(scored[:5], start=1):
            steps = [
                f"Verify requirements on official portal: {opp.official_url}",
                "Prepare transcripts, CV, and Statement of Purpose",
                "Submit online application before published deadline",
            ]

            # Build rich reasoning from matched/unmet criteria
            matched_short = [m for m in opp.matched_criteria if not m.startswith("Student CGPA") and not m.startswith("Verified funding")][:3]
            unmet_short = opp.unmet_criteria[:2]
            fit = opp.student_fit_reason or ""
            # Pull student-fit line from matched_criteria if present
            fit_lines = [m for m in opp.matched_criteria if m.startswith("Student fit:") or m.startswith("Fit:")]
            if fit_lines:
                fit = fit_lines[0].replace("Student fit: ", "").replace("Fit: ", "")

            reasoning_parts = [f"Target {target_label} alignment in {opp.country}."]
            if matched_short:
                reasoning_parts.append("Matched: " + "; ".join(matched_short) + ".")
            if unmet_short:
                reasoning_parts.append("Unmet: " + "; ".join(unmet_short) + ".")
            if fit:
                reasoning_parts.append(f"Why this fits: {fit}.")
            if opp.funding_amount_coverage and opp.funding_amount_coverage != "unverified":
                reasoning_parts.append(f"Funding: {opp.funding_amount_coverage[:80]}.")

            top_picks.append(
                RankedOpportunity(
                    rank=rank,
                    name=opp.name,
                    provider=opp.provider,
                    country=opp.country,
                    official_url=opp.official_url,
                    fit_score=round(min(max(score, 0.0), 100.0), 1),
                    reasoning=" ".join(reasoning_parts),
                    funding=opp.funding_amount_coverage,
                    deadline=opp.deadline,
                    evidence=opp.evidence,
                    application_steps=steps,
                    verified_data=opp,
                    discovery_source=opp.discovery_source,
                    verification_mode=opp.verification_mode,
                    deadline_source_url=opp.deadline_source_url,
                    last_checked=opp.last_checked,
                )
            )

        return RankedResults(
            top_opportunities=top_picks,
            total_candidates_found=len(opportunities),
            total_verified=len(opportunities),
            ranking_summary="Top 5 verified scholarships ranked by academic fit, country alignment, and funding coverage.",
        )
