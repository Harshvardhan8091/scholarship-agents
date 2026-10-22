from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse
import httpx
from tavily import TavilyClient

from config import settings
from event_bus import EventBus
from llm_client import LLMClient
from models import CandidateOpportunity, StudentProfile

# Authentic real-world scholarships used for fallback queries or offline mode
OFFICIAL_DOMAINS_PATTERNS = [
    r"\.edu(?:\.[a-z]{2})?$",
    r"\.ac\.[a-z]{2}$",
    r"\.gov(?:\.[a-z]{2})?$",
    r"(?:^|\.)daad\.de$",
    r"(?:^|\.)mext\.go\.jp$",
    r"(?:^|\.)chevening\.org$",
    r"(?:^|\.)campusfrance\.org$",
    r"(?:^|\.)fulbrightonline\.org$",
    r"(?:^|\.)europa\.eu$",
    r"(?:^|\.)gatescambridge\.org$",
    r"(?:^|\.)si\.se$",
    r"(?:^|\.)admin\.ch$",
    r"(?:^|\.)dfat\.gov\.au$",
    r"(?:^|\.)ethz\.ch$",
    r"(?:^|\.)tudelft\.nl$",
]

DOMESTIC_RESTRICTION_PATTERNS = [
    r"gov\.uk\s+student\s+finance",
    r"student\s+finance\s+england",
    r"student\s+finance\s+wales",
    r"\bfafsa\b",
    r"us\s+citizens\s+(?:and|or)\s+permanent\s+residents\s+only",
    r"domestic\s+students\s+only",
    r"home\s+students\s+only",
    r"home\s+fee\s+status\s+only",
    r"uk\s+residents\s+only",
    r"not\s+(?:available|open|eligible)\s+for\s+international\s+students",
]


def is_official_domain(url: str) -> bool:
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        for pat in OFFICIAL_DOMAINS_PATTERNS:
            if re.search(pat, domain):
                return True
    except Exception:
        pass
    return False


def is_restricted_to_domestic(text: str) -> bool:
    text_lower = text.lower()
    for pat in DOMESTIC_RESTRICTION_PATTERNS:
        if re.search(pat, text_lower):
            return True
    return False


CURATED_SCHOLARSHIPS: List[Dict[str, str]] = [
    {
        "name": "DAAD Development-Related Postgraduate Courses (EPOS)",
        "provider": "German Academic Exchange Service (DAAD)",
        "country": "Germany",
        "official_url": "https://www.daad.de/en/study-and-research-in-germany/scholarships/epos/",
        "description": "Scholarships for postgraduate courses with relevance to developing countries at German universities.",
        "degree_level": "Master / PhD",
    },
    {
        "name": "Chevening Scholarships",
        "provider": "UK Foreign, Commonwealth and Development Office",
        "country": "United Kingdom",
        "official_url": "https://www.chevening.org/scholarships/",
        "description": "The UK government's global scholarship programme, funded by the FCDO and partner organisations.",
        "degree_level": "Master",
    },
    {
        "name": "Fulbright Foreign Student Program",
        "provider": "U.S. Department of State",
        "country": "United States",
        "official_url": "https://foreign.fulbrightonline.org/",
        "description": "Enables graduate students, young professionals and artists from abroad to study and conduct research in the United States.",
        "degree_level": "Master / PhD",
    },
    {
        "name": "Erasmus Mundus Joint Masters Scholarships",
        "provider": "European Commission",
        "country": "European Union",
        "official_url": "https://erasmus-plus.ec.europa.eu/opportunities/individuals/students/erasmus-mundus-joint-masters-scholarships",
        "description": "High-level integrated study programmes at master level, jointly delivered by an international partnership of HEIs.",
        "degree_level": "Master",
    },
    {
        "name": "Gates Cambridge Scholarship",
        "provider": "Bill and Melinda Gates Foundation / University of Cambridge",
        "country": "United Kingdom",
        "official_url": "https://www.gatescambridge.org/apply/overview/",
        "description": "Full-cost scholarships for outstanding applicants from countries outside the UK to pursue a postgraduate degree at Cambridge.",
        "degree_level": "Master / PhD",
    },
    {
        "name": "Knight-Hennessy Scholars",
        "provider": "Stanford University",
        "country": "United States",
        "official_url": "https://knight-hennessy.stanford.edu/admission/planning-apply",
        "description": "Multidisciplinary, multicultural community of graduate students at Stanford University with full funding.",
        "degree_level": "Master / PhD",
    },
    {
        "name": "Swedish Institute Scholarship for Global Professionals (SISGP)",
        "provider": "Swedish Institute",
        "country": "Sweden",
        "official_url": "https://si.se/en/apply/scholarships/swedish-institute-scholarships-for-global-professionals/",
        "description": "Full scholarship for master's studies in Sweden aiming to develop future global leaders.",
        "degree_level": "Master",
    },
    {
        "name": "Eiffel Excellence Scholarship Programme",
        "provider": "Campus France / French Ministry for Europe and Foreign Affairs",
        "country": "France",
        "official_url": "https://www.campusfrance.org/en/the-eiffel-scholarship-program",
        "description": "Enables French higher education institutions to attract top foreign students for master's and doctoral courses.",
        "degree_level": "Master / PhD",
    },
    {
        "name": "Swiss Government Excellence Scholarships",
        "provider": "Federal Commission for Scholarships for Foreign Students (FCS)",
        "country": "Switzerland",
        "official_url": "https://www.sbfi.admin.ch/sbfi/en/home/education/scholarships-and-grants/swiss-government-excellence-scholarships.html",
        "description": "Postgraduate scholarships for foreign scholars and researchers wishing to study at Swiss universities.",
        "degree_level": "PhD / Postdoc / Research",
    },
    {
        "name": "ETH Zurich Excellence Scholarship & Opportunity Programme (ESOP)",
        "provider": "ETH Zurich",
        "country": "Switzerland",
        "official_url": "https://ethz.ch/students/en/studies/financial/scholarships/excellencescholarships.html",
        "description": "Covers the full study and living costs during the Master's degree course at ETH Zurich.",
        "degree_level": "Master",
    },
    {
        "name": "TU Delft Excellence Scholarships (Justus & Louise van Effen)",
        "provider": "Delft University of Technology",
        "country": "Netherlands",
        "official_url": "https://www.tudelft.nl/en/education/practical-matters/scholarships/justus-louise-van-effen-excellence-scholarships",
        "description": "Full scholarships for excellent international students admitted to TU Delft MSc programmes.",
        "degree_level": "Master",
    },
    {
        "name": "Australia Awards Scholarships",
        "provider": "Department of Foreign Affairs and Trade (DFAT), Australia",
        "country": "Australia",
        "official_url": "https://www.dfat.gov.au/people-to-people/australia-awards/australia-awards-scholarships",
        "description": "Long-term development awards administered by the Department of Foreign Affairs and Trade.",
        "degree_level": "Bachelor / Master / PhD",
    },
    {
        "name": "MEXT Japanese Government Scholarship",
        "provider": "Ministry of Education, Culture, Sports, Science and Technology, Japan",
        "country": "Japan",
        "official_url": "https://www.studyinjapan.go.jp/en/planning/scholarship/",
        "description": "Scholarships for international students who wish to study in graduate courses at Japanese universities.",
        "degree_level": "Master / PhD",
    },
    {
        "name": "KAIST International Graduate Scholarship",
        "provider": "Korea Advanced Institute of Science and Technology",
        "country": "South Korea",
        "official_url": "https://admission.kaist.ac.kr/intl-graduate/Scholarships",
        "description": "Full tuition waiver and monthly stipend for international graduate students in STEM disciplines.",
        "degree_level": "Master / PhD",
    },
]


class ResearchAgent:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
        self._tavily: Optional[TavilyClient] = None
        if settings.TAVILY_API_KEY:
            try:
                self._tavily = TavilyClient(api_key=settings.TAVILY_API_KEY)
            except Exception:
                self._tavily = None

    async def run(
        self,
        profile: StudentProfile,
        event_bus: EventBus,
        round_index: int = 0,
        existing_urls: Optional[Set[str]] = None,
        query_override: Optional[List[str]] = None,
    ) -> List[CandidateOpportunity]:
        is_retry = round_index > 0
        if is_retry:
            await event_bus.emit(
                agent="ResearchAgent",
                event_type="recovery",
                message=f"Fewer than 10 verified opportunities. Expanding search queries with broader terms (round {round_index})...",
                details={"round": round_index},
            )
        else:
            await event_bus.emit(
                agent="ResearchAgent",
                event_type="started",
                message="Generating targeted scholarship search queries from student profile...",
            )

        queries = query_override or await self._generate_queries(profile, round_index, event_bus)

        await event_bus.emit(
            agent="ResearchAgent",
            event_type="progress",
            message=f"Formulated {len(queries)} search queries across countries and fields.",
            details={"queries": queries},
        )

        candidates: List[CandidateOpportunity] = []
        seen_urls: Set[str] = {
            self._normalize_url(url) for url in (existing_urls or set())
        }

        # Search via Tavily if configured
        if self._tavily is not None:
            for q in queries:
                await event_bus.emit(
                    agent="ResearchAgent",
                    event_type="progress",
                    message=f"Querying web via Tavily: '{q}'...",
                )
                try:
                    search_results = await self._run_tavily_search(q, event_bus)
                    parsed_candidates = self._parse_search_results(
                        search_results, q, profile
                    )
                    for cand in parsed_candidates:
                        cand.discovery_source = "tavily_live"
                        norm_url = self._normalize_url(cand.official_url)
                        if norm_url and norm_url not in seen_urls:
                            seen_urls.add(norm_url)
                            candidates.append(cand)
                except Exception as e:
                    await event_bus.emit(
                        agent="ResearchAgent",
                        event_type="recovery",
                        message=f"Tavily search error for '{q}': {e}. Continuing with remaining queries...",
                    )

            # Item 3: If Tavily returned fewer than 5, supplement with curated and emit recovery
            if len(candidates) < 5:
                await event_bus.emit(
                    agent="ResearchAgent",
                    event_type="recovery",
                    message=f"Tavily returned only {len(candidates)} results (< 5). Supplementing with curated fallback list.",
                )
                for c in self._match_curated(profile, round_index):
                    c.discovery_source = "curated_fallback"
                    norm_url = self._normalize_url(c.official_url)
                    if norm_url not in seen_urls:
                        seen_urls.add(norm_url)
                        candidates.append(c)
        else:
            await event_bus.emit(
                agent="ResearchAgent",
                event_type="recovery",
                message="TAVILY_API_KEY not set; using verified global scholarship knowledge base and web discovery.",
            )
            # Supplement from curated database matching student profile
            curated_matches = self._match_curated(profile, round_index)
            for c in curated_matches:
                c.discovery_source = "curated_fallback"
                norm_url = self._normalize_url(c.official_url)
                if norm_url not in seen_urls:
                    seen_urls.add(norm_url)
                    candidates.append(c)

        # Ensure we return at least 10-15 candidates
        if len(candidates) < 10:
            await event_bus.emit(
                agent="ResearchAgent",
                event_type="recovery",
                message=f"Only {len(candidates)} candidates so far. Supplementing with broader curated list.",
            )
            for c in self._match_curated(profile, round_index=2):
                c.discovery_source = "curated_fallback"
                norm_url = self._normalize_url(c.official_url)
                if norm_url not in seen_urls:
                    seen_urls.add(norm_url)
                    candidates.append(c)
                if len(candidates) >= 15:
                    break

        # Prefer official domains (.edu, .ac.*, .gov, daad.de, etc.) by sorting them to the front
        candidates.sort(
            key=lambda c: (1 if is_official_domain(c.official_url) else 0),
            reverse=True,
        )

        await event_bus.emit(
            agent="ResearchAgent",
            event_type="done",
            message=f"Discovered {len(candidates)} candidate scholarship opportunities.",
            details={"total_candidates": len(candidates)},
        )
        return candidates

    async def _generate_queries(
        self, profile: StudentProfile, round_index: int, event_bus: EventBus
    ) -> List[str]:
        field = profile.field or "Computer Science STEM"
        countries = profile.target_countries or ["Germany", "USA", "UK", "Europe"]
        target_deg = profile.target_degree_level or "Master's"

        if round_index == 0:
            # Specific targeted queries using target degree level (e.g. Master's for a bachelor student)
            q_list = [
                f"{countries[0] if countries else 'international'} {target_deg} scholarship {field} official website",
                f"government fully funded scholarships {target_deg} international students {field}",
                f"merit based scholarships {field} {target_deg} eligibility requirements",
                f"top university tuition waiver {target_deg} scholarships {field}",
            ]
            if len(countries) > 1:
                q_list.append(f"{countries[1]} university scholarships for international students {field}")
            return q_list

        elif round_index == 1:
            # Broader national and regional queries
            return [
                f"international graduate scholarships {field} {target_deg} fully funded",
                f"DAAD Erasmus Chevening Fulbright {field} eligibility",
                f"European university master scholarships international students {field}",
                f"global excellence scholarships graduate study in {field}",
                "international fellowship master doctoral degree full funding",
            ]
        else:
            # Broadest global queries
            return [
                "top international scholarships for developing countries master degree",
                "fully funded government scholarships abroad university programs",
                "open university master scholarships international students official portal",
                f"scholarships for {target_deg} students abroad application requirements",
            ]

    async def _run_tavily_search(
        self, query: str, event_bus: EventBus
    ) -> List[Dict[str, Any]]:
        import asyncio

        def _call_tavily():
            return self._tavily.search(
                query=query,
                search_depth="basic",
                max_results=6,
                include_answer=False,
            )

        res = await asyncio.to_thread(_call_tavily)
        return res.get("results", [])

    def _parse_search_results(
        self, results: List[Dict[str, Any]], query: str, profile: StudentProfile
    ) -> List[CandidateOpportunity]:
        target_deg = profile.target_degree_level or "Master's"
        candidates: List[CandidateOpportunity] = []
        for r in results:
            url = r.get("url", "")
            title = r.get("title", "")
            content = r.get("content", "")

            # Exclude aggregator spam or generic search listings if possible
            if not url or "wikipedia.org" in url:
                continue

            # Reject opportunities restricted to domestic/resident students when applicant is international
            if is_restricted_to_domestic(f"{title} {content}"):
                continue

            # Infer country and provider from title or url
            provider = self._infer_provider(title, url)
            country = self._infer_country(title, content, profile)

            candidates.append(
                CandidateOpportunity(
                    name=title,
                    provider=provider,
                    country=country,
                    official_url=url,
                    description=content[:250],
                    degree_level=target_deg,
                    source_query=query,
                )
            )
        return candidates

    def _infer_provider(self, title: str, url: str) -> str:
        domain = urlparse(url).netloc.replace("www.", "")
        if "|" in title:
            parts = title.split("|")
            return parts[-1].strip()
        if "-" in title:
            parts = title.split("-")
            return parts[-1].strip()
        return domain

    def _infer_country(
        self, title: str, content: str, profile: StudentProfile
    ) -> str:
        text = f"{title} {content}".lower()
        if "germany" in text or "german" in text or "daad" in text:
            return "Germany"
        if "united kingdom" in text or "uk" in text or "chevening" in text:
            return "United Kingdom"
        if "united states" in text or "usa" in text or "fulbright" in text:
            return "United States"
        if "switzerland" in text or "eth" in text:
            return "Switzerland"
        if "sweden" in text or "swedish" in text:
            return "Sweden"
        if "france" in text or "french" in text:
            return "France"
        if "netherlands" in text or "holland" in text:
            return "Netherlands"
        if "australia" in text:
            return "Australia"
        if "japan" in text or "mext" in text:
            return "Japan"
        if profile.target_countries:
            return profile.target_countries[0]
        return "International"

    def _match_curated(
        self, profile: StudentProfile, round_index: int
    ) -> List[CandidateOpportunity]:
        target_countries = [c.lower() for c in (profile.target_countries or [])]
        target_deg = (profile.target_degree_level or "Master's").lower()
        candidates = []

        for item in CURATED_SCHOLARSHIPS:
            item_country = item["country"].lower()
            item_deg = item.get("degree_level", "").lower()

            # Check target degree level compatibility
            deg_match = True
            if "master" in target_deg and "master" not in item_deg and "phd" not in item_deg:
                deg_match = False
            elif "phd" in target_deg and "phd" not in item_deg and "research" not in item_deg:
                deg_match = False

            if not deg_match and round_index == 0:
                continue

            # Match target countries if present, otherwise include all
            is_country_match = (
                not target_countries
                or any(tc in item_country or item_country in tc for tc in target_countries)
                or item["country"] in ["European Union", "International"]
            )
            if is_country_match or round_index >= 1:
                candidates.append(
                    CandidateOpportunity(
                        name=item["name"],
                        provider=item["provider"],
                        country=item["country"],
                        official_url=item["official_url"],
                        description=item["description"],
                        degree_level=item["degree_level"],
                        source_query="Curated verified academic knowledge base",
                    )
                )
        return candidates

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url.strip())
        return f"{parsed.netloc}{parsed.path}".rstrip("/")
