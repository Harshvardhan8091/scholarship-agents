from __future__ import annotations

from datetime import date, datetime
from difflib import SequenceMatcher
import re
from typing import List, Optional
from urllib.parse import urlsplit, urlunsplit
from config import settings
from event_bus import EventBus
from llm_client import LLMClient
from models import RankedOpportunity, RankedResults, StudentProfile, VerifiedOpportunity
from agents.research_agent import is_official_domain


class RankerAgent:
    THIRD_PARTY_DOMAINS = (
        "experteducation",
        "universityliving",
        "profellow",
        "expatrio",
        "scholars4dev",
        "studyportals",
        "shiksha",
        "leverageedu",
        "collegedunia",
    )
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    @staticmethod
    def _normalize_url(url: str) -> str:
        parsed = urlsplit(url.strip())
        return urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.lower().rstrip("/"), "", "")
        )

    @classmethod
    def _is_third_party(cls, url: str) -> bool:
        parsed = urlsplit(url.lower())
        domain = parsed.netloc
        return "/blog/" in f"/{parsed.path.strip('/')}/" or any(
            blocked in domain for blocked in cls.THIRD_PARTY_DOMAINS
        )

    @staticmethod
    def _is_non_award(name: str, url: str) -> bool:
        path = urlsplit(url.lower()).path
        title = name.lower()
        if any(token in path for token in ("/node/", "/news", "/press", "/blog", "/organisations/")):
            return True
        return (
            re.search(r"\bnew\b.{0,80}\bjoin\b", title) is not None
            or any(token in title for token in ("announces", "catalogue", "catalog"))
        )

    @classmethod
    def _is_closed(cls, opportunity: VerifiedOpportunity) -> bool:
        today = date.today()
        source_text = f"{opportunity.name} {opportunity.official_url}"
        if any(
            int(year) < today.year
            for year in re.findall(r"\b(?:19|20)\d{2}\b", source_text)
        ):
            return True
        deadline = opportunity.deadline.strip()
        if "closed for this cycle" in deadline.lower():
            return True
        match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", deadline)
        if match:
            try:
                return date.fromisoformat(match.group(0)) < today
            except ValueError:
                return False
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(deadline, fmt).date() < today
            except ValueError:
                continue
        return False

    @staticmethod
    def _applicant_restriction_reason(
        opportunity: VerifiedOpportunity,
    ) -> Optional[str]:
        details = " ".join(
            [
                opportunity.name,
                opportunity.eligibility,
                *opportunity.matched_criteria,
                *opportunity.unmet_criteria,
                *opportunity.evidence.values(),
            ]
        )
        if re.search(
            r"(?:not\s+(?:open|available|eligible)\s+(?:to|for)\s+"
            r"(?:international(?:\s*/\s*indian)?|indian)\s+"
            r"(?:students?|applicants?|nationals?)|"
            r"(?:international(?:\s*/\s*indian)?|indian)\s+"
            r"(?:students?|applicants?|nationals?)\s+"
            r"(?:are\s+)?not\s+eligible|"
            r"no\s+(?:international|indian)\s+(?:students?|applicants?)|"
            r"\(a\).{0,80}(?:not open|not eligible|domestic|\bno\b)|"
            r"only\s+(?:domestic|home|uk|us)\s+(?:students|applicants)|"
            r"(?:domestic|home)\s+students?\s+only)",
            details,
            re.I,
        ):
            return "Excluded: not open to international/Indian applicants."

        if re.search(
            r"funding,?\s+tuition fees?\s*(?:and|&)\s*awards?|"
            r"(?:only\s+for|restricted\s+to|available\s+only\s+to|must\s+be)"
            r".{0,80}(?:already\s+)?(?:enrolled|registered)\s+students?|"
            r"must\s+be\s+(?:currently\s+)?(?:enrolled|registered)\s+at\b|"
            r"only\s+(?:current|existing|registered|enrolled)\s+students?\s+"
            r"(?:at|of)\b|students?\s+already\s+enrolled\s+at\b",
            details,
            re.I,
        ):
            return "Excluded: university department funding page for its own enrolled students."
        return None

    @staticmethod
    def _matches_target_country(
        opportunity: VerifiedOpportunity | RankedOpportunity,
        profile: StudentProfile,
    ) -> bool:
        country = opportunity.country.lower()
        return any(
            target.strip().lower() in country
            for target in (profile.target_countries or [])
            if target.strip()
        )

    @staticmethod
    def _normalized_name(name: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", name.lower()))

    @classmethod
    def _same_name(cls, first: str, second: str) -> bool:
        first_normalized = cls._normalized_name(first)
        second_normalized = cls._normalized_name(second)
        if not first_normalized or not second_normalized:
            return False
        if first_normalized == second_normalized:
            return True
        return SequenceMatcher(None, first_normalized, second_normalized).ratio() >= 0.9

    @classmethod
    def _dedupe_ranked(
        cls, ranked: List[RankedOpportunity]
    ) -> List[RankedOpportunity]:
        kept: List[RankedOpportunity] = []
        for opportunity in sorted(ranked, key=lambda item: item.fit_score, reverse=True):
            if any(
                cls._normalize_url(opportunity.official_url)
                == cls._normalize_url(existing.official_url)
                or cls._same_name(opportunity.name, existing.name)
                for existing in kept
            ):
                continue
            kept.append(opportunity)
        return kept

    def _heuristic_score(
        self, opportunity: VerifiedOpportunity, profile: StudentProfile
    ) -> float:
        target_countries = [country.lower() for country in (profile.target_countries or [])]
        target_degree = (profile.target_degree_level or "Master's").lower()
        score = 50.0
        if opportunity.match_status == "eligible":
            score += 15.0
        elif opportunity.match_status == "partially_eligible":
            score += 8.0
        if any(country.strip() in opportunity.country.lower() for country in target_countries):
            score += 10.0
        opportunity_text = f"{opportunity.name} {opportunity.provider}".lower()
        interest_matches = sum(
            1 for interest in profile.interests
            if interest.strip().lower() in opportunity_text
        )
        score += min(interest_matches * 3.0, 9.0)
        if is_official_domain(opportunity.official_url):
            score += 10.0
        text = opportunity_text
        if "master" in target_degree:
            if "bachelor" in text or "undergraduate" in text:
                score -= 30.0
            elif "master" in text or "graduate" in text or "postgraduate" in text:
                score += 8.0
        elif "phd" in target_degree:
            if "bachelor" in text or "undergraduate" in text:
                score -= 30.0
            elif "phd" in text or "doctoral" in text or "research" in text:
                score += 8.0
        if opportunity.funding_amount_coverage and "unverified" not in opportunity.funding_amount_coverage.lower():
            score += 5.0
        score += min(opportunity.confidence * 4.0, 4.0)
        if opportunity.deadline and "unverified" in opportunity.deadline.lower():
            score -= 8.0
        elif opportunity.deadline and "closed" in opportunity.deadline.lower():
            score -= 25.0
        if (
            opportunity.discovery_source == "curated_fallback"
            and opportunity.verification_mode == "unverified"
        ):
            score -= 35.0
        return score

    @staticmethod
    def _matches_target_degree(opportunity: VerifiedOpportunity, target_degree: str) -> bool:
        requested = target_degree.lower()
        evidence = opportunity.degree_level.lower()
        if "phd" in requested or "doctoral" in requested:
            return any(term in evidence for term in ("phd", "doctoral", "doctorate"))
        if "bachelor" in requested or "undergraduate" in requested:
            return any(term in evidence for term in ("bachelor", "undergraduate"))
        return any(term in evidence for term in ("master", "graduate", "postgraduate"))

    @classmethod
    def _curated_fallbacks(
        cls, opportunities: List[VerifiedOpportunity]
    ) -> List[VerifiedOpportunity]:
        from agents.research_agent import CURATED_SCHOLARSHIPS

        fallbacks: List[VerifiedOpportunity] = []
        for item in CURATED_SCHOLARSHIPS:
            if "master" not in item.get("degree_level", "").lower():
                continue
            url = item["official_url"]
            if cls._is_third_party(url) or cls._is_closed(
                VerifiedOpportunity(
                    name=item["name"],
                    provider=item["provider"],
                    country=item["country"],
                    official_url=url,
                )
            ):
                continue
            if any(
                cls._normalize_url(existing.official_url) == cls._normalize_url(url)
                or cls._same_name(existing.name, item["name"])
                for existing in opportunities + fallbacks
            ):
                continue
            fallbacks.append(
                VerifiedOpportunity(
                    name=item["name"],
                    provider=item["provider"],
                    country=item["country"],
                    official_url=url,
                    degree_level="Master's",
                    discovery_source="curated_fallback",
                    verification_mode="unverified",
                    match_status="partially_eligible",
                    unmet_criteria=["Curated fallback; not independently verified"],
                    source_urls=[url],
                )
            )
        return fallbacks

    async def run(
        self,
        verified_opportunities: List[VerifiedOpportunity],
        profile: StudentProfile,
        event_bus: EventBus,
        *,
        allow_curated_fallback: bool = False,
    ) -> RankedResults:
        await event_bus.emit(
            agent="RankerAgent",
            event_type="started",
            message=f"Evaluating and ranking {len(verified_opportunities)} verified opportunities for student profile...",
        )

        for opportunity in verified_opportunities:
            restriction_reason = self._applicant_restriction_reason(opportunity)
            if restriction_reason:
                if restriction_reason not in opportunity.unmet_criteria:
                    opportunity.unmet_criteria.append(restriction_reason)
                opportunity.match_status = "ineligible"
            elif self._is_non_award(opportunity.name, opportunity.official_url):
                if "excluded: non-award" not in opportunity.unmet_criteria:
                    opportunity.unmet_criteria.append("excluded: non-award")
                opportunity.match_status = "aggregator_excluded"
            elif self._is_third_party(opportunity.official_url):
                if "excluded: third-party" not in opportunity.unmet_criteria:
                    opportunity.unmet_criteria.append("excluded: third-party")
                opportunity.match_status = "aggregator_excluded"
            elif not is_official_domain(opportunity.official_url):
                if "excluded: non-official source" not in opportunity.unmet_criteria:
                    opportunity.unmet_criteria.append("excluded: non-official source")
                opportunity.match_status = "aggregator_excluded"
            elif self._is_closed(opportunity):
                if "closed for this cycle" not in opportunity.unmet_criteria:
                    opportunity.unmet_criteria.append("closed for this cycle")
                opportunity.match_status = "ineligible"

        eligible_pool = [
            opportunity
            for opportunity in verified_opportunities
            if opportunity.match_status in ("eligible", "partially_eligible")
            and self._matches_target_degree(
                opportunity, profile.target_degree_level or "Master's"
            )
            and bool(opportunity.official_url.strip())
        ]

        target_degree = profile.target_degree_level or "Master's"
        if (
            allow_curated_fallback
            and len(eligible_pool) < 5
            and "master" in target_degree.lower()
        ):
            eligible_pool.extend(self._curated_fallbacks(eligible_pool))

        if not eligible_pool:
            await event_bus.emit(
                agent="RankerAgent",
                event_type="error",
                message="No candidates had page-supported Master's-level eligibility and an official source URL.",
            )
            return RankedResults(
                top_opportunities=[],
                total_candidates_found=len(verified_opportunities),
                total_verified=len(verified_opportunities),
                ranking_summary="No opportunities met the verified Master's-level and official-source requirements.",
            )

        eligible_pool = self._dedupe_verified(eligible_pool, profile)

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

        results.top_opportunities = self._dedupe_ranked(results.top_opportunities)
        target_country_count = sum(
            self._matches_target_country(opportunity, profile)
            for opportunity in eligible_pool
        )
        if target_country_count >= 3:
            heuristic_results = self._rank_heuristically(eligible_pool, profile)
            results.top_opportunities = self._dedupe_ranked(
                results.top_opportunities + heuristic_results.top_opportunities
            )
        elif len(results.top_opportunities) < 5:
            heuristic_results = self._rank_heuristically(eligible_pool, profile)
            results.top_opportunities = self._dedupe_ranked(
                results.top_opportunities + heuristic_results.top_opportunities
            )
        results.top_opportunities.sort(
            key=lambda opportunity: (
                opportunity.discovery_source == "curated_fallback"
                and opportunity.verification_mode == "unverified",
                not self._matches_target_country(opportunity, profile)
                if target_country_count >= 3
                else False,
                -opportunity.fit_score,
            )
        )
        results.top_opportunities = results.top_opportunities[:5]
        for rank, opportunity in enumerate(results.top_opportunities, start=1):
            opportunity.rank = rank

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

    def _dedupe_verified(
        self,
        opportunities: List[VerifiedOpportunity],
        profile: StudentProfile,
    ) -> List[VerifiedOpportunity]:
        kept: List[VerifiedOpportunity] = []
        target_country_count = sum(
            self._matches_target_country(opportunity, profile)
            for opportunity in opportunities
        )
        for opportunity in sorted(
            opportunities,
            key=lambda item: (
                item.discovery_source == "curated_fallback"
                and item.verification_mode == "unverified",
                not self._matches_target_country(item, profile)
                if target_country_count >= 3
                else False,
                -self._heuristic_score(item, profile),
            ),
        ):
            if any(
                self._normalize_url(opportunity.official_url)
                == self._normalize_url(existing.official_url)
                or self._same_name(opportunity.name, existing.name)
                for existing in kept
            ):
                continue
            kept.append(opportunity)
        return kept

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
Interests: {profile.interests}

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
                        degree_level=v_data.degree_level,
                        fit_score=float(item.get("fit_score", 90.0 - len(ranked_picks) * 3)),
                        reasoning=str(item.get("reasoning", "")),
                        funding=v_data.funding_amount_coverage,
                        deadline=v_data.deadline,
                        evidence=v_data.evidence,
                        application_steps=[v_data.how_to_apply] if v_data.how_to_apply != "unverified" else [],
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
            opp_text = f"{opp.name} {opp.provider}".lower()
            score += min(
                sum(1 for interest in profile.interests if interest.strip().lower() in opp_text) * 3.0,
                9.0,
            )

            # Item 2: Ranking bonus for official domains (.edu, .ac.*, .gov, daad.de, etc.)
            if is_official_domain(opp.official_url):
                score += 10.0

            # Item 3: Degree level check — match with target_degree_level, penalize mismatch
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
                if profile.financial_need == "Full funding needed" and any(
                    term in opp.funding_amount_coverage.lower()
                    for term in ("full", "tuition", "stipend", "fully funded")
                ):
                    score += 4.0

            score += min(opp.confidence * 4.0, 4.0)

            # Item 4: Penalize unverified or closed deadlines
            if opp.deadline and "unverified" in opp.deadline.lower():
                score -= 8.0
            elif opp.deadline and "closed" in opp.deadline.lower():
                score -= 25.0  # Heavy penalty for past/closed deadlines

            scored.append((score, opp))

        target_country_count = sum(
            self._matches_target_country(opportunity, profile)
            for opportunity in opportunities
        )
        scored.sort(
            key=lambda item: (
                item[1].discovery_source == "curated_fallback"
                and item[1].verification_mode == "unverified",
                not self._matches_target_country(item[1], profile)
                if target_country_count >= 3
                else False,
                -item[0],
            )
        )
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
                    degree_level=opp.degree_level,
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
