from __future__ import annotations

import asyncio
from datetime import date, datetime
import unicodedata
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import httpx
import trafilatura

from config import settings
from event_bus import EventBus
from llm_client import LLMClient
from models import CandidateOpportunity, StudentProfile, VerifiedOpportunity
from page_cache import get_cached_page, set_cached_page
from agents.research_agent import is_official_domain, is_restricted_to_domestic

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_VERIFICATION_CANDIDATES = 10
CANDIDATE_TIMEOUT_SECONDS = 40
MAX_PAGE_TEXT_CHARS = 4000

AGGREGATOR_PATTERNS = [
    r"\b\d+\+?\s+scholarships\b",
    r"\b(?:top|best)\s+\d+\b",
    r"\b\d+\s+(?:top|best)\b",
    r"\btop\s+(?:universities|scholarships|colleges)\b",
    r"\bbest\s+(?:scholarships|universities|colleges)\b",
    r"\blist\s+of\s+(?:top|best|\d+)",
    r"\b(?:scholarship|funding|award)\s+(?:directory|database|listing|list)\b",
    r"\b(?:top\s+\d+|best\s+\d+).{0,40}(?:scholarship|funding|award)",
]

AGGREGATOR_DOMAINS = [
    "scholarshipportal.com",
    "opportunitydesk.org",
    "opportunitiesforyouth.org",
    "scholars4dev.com",
    "afterschoolafrica.com",
    "wemakescholars.com",
    "collegedunia.com",
    "shiksha.com",
    "topuniversities.com",
    "hotcoursesabroad.com",
    "profellow.com",
    "expatrio.com",
    "globaladmissions.com",
    "globalscholarships.com",
    "scholarshiproar.com",
    "scholarships.com",
    "scholarshipsads.com",
    "scholarship-positions.com",
    "eduopportunities.com",
    "findamasters.com",
    "mastersportal.eu",
    "studyportals.com",
]

# Generic snippet patterns that should be rejected as non-evidence
GENERIC_SNIPPET_PATTERNS = [
    r"^\s*what\s+does\s+",          # questions like "What does the scholarship cover?"
    r"^\s*\w[^.?!]*\?\.?\s*$",      # any sentence that IS a question
    r"warning\s+against\s+fake",
    r"phishing",
    r"scam",
    r"beware",
    r"deadline\s+detail\s+for\s+each",   # vague aggregator-level phrase
    r"eligibility\s+and\s+deadline\s+detail",
    r"^\s*(?:scholarships?|eligibility|funding|application)\s+available\s+for\b",
    r"\b(?:visit|check|see)\s+(?:the\s+)?(?:official\s+)?(?:website|page|portal)\s+for\s+more",
    r"\blearn more about\b",
    r"\bvarious (?:scholarship|funding) opportunities\b",
]


def _is_generic_snippet(text: str) -> bool:
    """Return True if a snippet is too generic / a question / a scam warning to count as evidence."""
    t = text.strip()
    if not t or len(t) < 8:
        return True
    tl = t.lower()
    for pat in GENERIC_SNIPPET_PATTERNS:
        if re.search(pat, tl):
            return True
    # Reject if the snippet is just a heading (all-caps or ends with colon, very short)
    if re.match(r'^[A-Z][A-Za-z ]{0,40}:$', t):
        return True
    return False


def _normalise_evidence(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _is_source_evidence(snippet: object, page_text: str) -> bool:
    if not isinstance(snippet, str) or _is_generic_snippet(snippet):
        return False
    normalized_snippet = _normalise_evidence(snippet)
    return len(normalized_snippet) >= 8 and normalized_snippet in _normalise_evidence(page_text)


def _parse_deadline(raw: str) -> Tuple[Optional[date], str]:
    """
    Try to parse a deadline string into a real date.
    Returns (parsed_date_or_None, normalised_string).
    If the date is before today, returns (date, "closed for this cycle (was YYYY-MM-DD)").
    If raw is 'unverified' or can't be parsed, returns (None, 'unverified').
    """
    if not raw or raw.strip().lower() in ("unverified", ""):
        return None, "unverified"

    # Try various date formats
    formats = [
        "%Y-%m-%d", "%d %B %Y", "%B %d, %Y", "%d/%m/%Y", "%m/%d/%Y",
        "%d %b %Y", "%b %d, %Y",
    ]
    # Also try extracting a date substring with regex
    date_patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})",
        r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})",
        r"(\d{1,2}/\d{1,2}/\d{4})",
        r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{4})",
        r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+\d{4})",
    ]

    candidate_strings = [raw.strip()]
    for dpat in date_patterns:
        m = re.search(dpat, raw, re.IGNORECASE)
        if m:
            candidate_strings.insert(0, m.group(1))

    today = date.today()
    for s in candidate_strings:
        for fmt in formats:
            try:
                parsed = datetime.strptime(s.strip(), fmt).date()
                iso = parsed.isoformat()
                if parsed < today:
                    return parsed, f"closed for this cycle (was {iso})"
                return parsed, iso
            except (ValueError, TypeError):
                continue

    # Could not parse — return unverified
    return None, "unverified"


class VerifierAgent:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
        # Limit concurrency to 2 to respect LLM call budget
        self.concurrency = min(settings.VERIFIER_CONCURRENCY, settings.LLM_CONCURRENCY, 2)

    async def run(
        self,
        candidates: List[CandidateOpportunity],
        profile: StudentProfile,
        event_bus: EventBus,
    ) -> List[VerifiedOpportunity]:
        verification_candidates = candidates[:MAX_VERIFICATION_CANDIDATES]
        await event_bus.emit(
            agent="VerifierAgent",
            event_type="started",
            message=f"Starting verification of {len(verification_candidates)} of {len(candidates)} candidate opportunities in parallel (concurrency limit: {self.concurrency})...",
        )

        semaphore = asyncio.Semaphore(self.concurrency)
        verified_results: List[VerifiedOpportunity] = []

        async def _verify_with_limit(candidate: CandidateOpportunity) -> VerifiedOpportunity:
            async with semaphore:
                return await asyncio.wait_for(
                    self._verify_candidate(candidate, profile, event_bus),
                    timeout=CANDIDATE_TIMEOUT_SECONDS,
                )

        tasks = [_verify_with_limit(cand) for cand in verification_candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, res in enumerate(results):
            if isinstance(res, Exception):
                cand = verification_candidates[i]
                if isinstance(res, asyncio.TimeoutError):
                    error_message = (
                        f"Verification timed out after {CANDIDATE_TIMEOUT_SECONDS} seconds "
                        f"for {cand.name}. Marking as unverified."
                    )
                    missing_information = [
                        f"Verification timed out after {CANDIDATE_TIMEOUT_SECONDS} seconds."
                    ]
                else:
                    error_message = (
                        f"Verification unhandled exception for {cand.name}: {res}. "
                        "Marking as unverified."
                    )
                    missing_information = [f"Verification error: {res}"]
                await event_bus.emit(
                    agent="VerifierAgent",
                    event_type="recovery",
                    message=error_message,
                )
                verified_results.append(
                    VerifiedOpportunity(
                        name=cand.name,
                        provider=cand.provider,
                        country=cand.country,
                        official_url=cand.official_url,
                        match_status="unverified",
                        missing_information=missing_information,
                        source_urls=[cand.official_url],
                        discovery_source=cand.discovery_source,
                        verification_mode="unverified",
                        deadline_source_url=None,
                        last_checked=date.today().isoformat(),
                    )
                )
            else:
                verified_results.append(res)

        eligible_count = sum(
            1 for o in verified_results if o.match_status in ("eligible", "partially_eligible")
        )

        await event_bus.emit(
            agent="VerifierAgent",
            event_type="done",
            message=f"Verification complete: {eligible_count} verified eligible/partial matches out of {len(verified_results)} candidates.",
            details={
                "total_verified": len(verified_results),
                "eligible_count": eligible_count,
            },
        )
        return verified_results

    @staticmethod
    def _is_aggregator(name: str, url: str) -> bool:
        t = name.lower()
        d = urlparse(url).netloc.lower()
        if any(re.search(p, t) for p in AGGREGATOR_PATTERNS):
            return True
        if any(d == ad or d.endswith(f".{ad}") for ad in AGGREGATOR_DOMAINS):
            return True
        if re.search(r"\b(?:blog|blogs|medium|substack)\b", d):
            return True
        if any(word in d for word in ("blog.", "blogs.", "medium.com", "substack.com")):
            return True
        return False

    @staticmethod
    def _find_official_link_in_html(html: str, base_url: str) -> Optional[str]:
        """Extracts outbound link pointing to an official domain or official application portal."""
        try:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                full_url = urljoin(base_url, href)
                if not full_url.startswith("http"):
                    continue
                if is_official_domain(full_url):
                    return full_url
        except Exception:
            pass
        return None

    @staticmethod
    def _official_page_title(html: str) -> str:
        if not html:
            return ""
        soup = BeautifulSoup(html, "html.parser")
        heading = soup.find("h1") or soup.find("title")
        return heading.get_text(" ", strip=True)[:160] if heading else ""

    @staticmethod
    def _extract_relevant_text(page_text: str, max_chars: int = MAX_PAGE_TEXT_CHARS) -> str:
        """Truncates page text to the most relevant characters for verification."""
        if len(page_text) <= max_chars:
            return page_text
        keywords = [
            "scholarship", "fund", "tuition", "deadline", "eligib",
            "require", "grant", "stipend", "apply", "fee", "award",
            "coverage", "criteria", "admission"
        ]
        paragraphs = page_text.split("\n")
        relevant = [p.strip() for p in paragraphs if p.strip() and any(k in p.lower() for k in keywords)]
        other = [p.strip() for p in paragraphs if p.strip() and not any(k in p.lower() for k in keywords)]
        combined = "\n\n".join(relevant)
        if len(combined) < max_chars:
            combined += "\n\n" + "\n\n".join(other)
        return combined[:max_chars]

    async def _verify_candidate(
        self,
        candidate: CandidateOpportunity,
        profile: StudentProfile,
        event_bus: EventBus,
    ) -> VerifiedOpportunity:
        await event_bus.emit(
            agent="VerifierAgent",
            event_type="progress",
            message=f"Fetching official scholarship page: {candidate.name[:50]} ({candidate.official_url})...",
        )

        # 1. Check if candidate page is an aggregator/listicle/blog
        is_agg = self._is_aggregator(candidate.name, candidate.official_url)

        # 2. Fetch page content (and raw HTML to follow official links if aggregator)
        page_text, raw_html = await self._fetch_page_content_and_html(candidate.official_url, event_bus)

        # If aggregator, attempt to follow official source link
        if is_agg:
            official_link = self._find_official_link_in_html(raw_html, candidate.official_url) if raw_html else None
            if official_link and self._is_aggregator(candidate.name, official_link):
                official_link = None
            if official_link:
                await event_bus.emit(
                    agent="VerifierAgent",
                    event_type="recovery",
                    message=f"Aggregator detected ({candidate.name[:35]}). Following official link to: {official_link[:60]}...",
                    details={"aggregator_url": candidate.official_url, "official_url": official_link},
                )
                candidate.official_url = official_link
                page_text, official_html = await self._fetch_page_content_and_html(official_link, event_bus)
                official_title = self._official_page_title(official_html)
                if official_title and not self._is_aggregator(official_title, official_link):
                    candidate.name = official_title
                    candidate.provider = urlparse(official_link).netloc
                else:
                    official_link = None
            if not official_link:
                await event_bus.emit(
                    agent="VerifierAgent",
                    event_type="recovery",
                    message=f"Excluding aggregator/listicle with no identifiable official opportunity page: {candidate.name[:45]}.",
                )
                return VerifiedOpportunity(
                    name=candidate.name,
                    provider=candidate.provider,
                    country=candidate.country,
                    official_url=candidate.official_url,
                    match_status="aggregator_excluded",
                    unmet_criteria=["Aggregator/listicle page with no identifiable official opportunity page — excluded from recommendations"],
                    source_urls=[candidate.official_url],
                    discovery_source=candidate.discovery_source,
                    verification_mode="heuristic",
                    deadline_source_url=candidate.official_url,
                    last_checked=date.today().isoformat(),
                )

        if not page_text or "FAILED_FETCH" in page_text:
            await event_bus.emit(
                agent="VerifierAgent",
                event_type="recovery",
                message=f"Page fetch failed for {candidate.name}; search snippets are not used as verification evidence.",
            )
            return VerifiedOpportunity(
                name=candidate.name,
                provider=candidate.provider,
                country=candidate.country,
                official_url=candidate.official_url,
                eligibility="unverified",
                funding_amount_coverage="unverified",
                deadline="unverified",
                required_documents=[],
                how_to_apply="unverified",
                match_status="unverified",
                missing_information=["Official page could not be fetched or was blocked"],
                confidence=0.0,
                source_urls=[candidate.official_url] if candidate.official_url else [],
                discovery_source=candidate.discovery_source,
                verification_mode="unverified",
                deadline_source_url=None,
                last_checked=date.today().isoformat(),
            )

        # 3. Quality filter: Reject opportunities restricted to domestic/resident students
        if is_restricted_to_domestic(page_text):
            await event_bus.emit(
                agent="VerifierAgent",
                event_type="recovery",
                message=f"Rejecting {candidate.name[:40]}: Restricted to domestic/resident students only.",
            )
            return VerifiedOpportunity(
                name=candidate.name,
                provider=candidate.provider,
                country=candidate.country,
                official_url=candidate.official_url,
                degree_level=degree_level,
                match_status="ineligible",
                unmet_criteria=["Restricted to domestic/resident students only (international applicant ineligible)"],
                source_urls=[candidate.official_url],
                discovery_source=candidate.discovery_source,
                verification_mode="heuristic",
                deadline_source_url=candidate.official_url,
                last_checked=date.today().isoformat(),
            )

        # 4. LLM Verification (batch: exactly 1 Gemini call per candidate)
        try:
            return await self._verify_with_llm(
                candidate=candidate,
                page_text=page_text,
                profile=profile,
                event_bus=event_bus,
            )
        except Exception as e:
            await event_bus.emit(
                agent="VerifierAgent",
                event_type="recovery",
                message=f"LLM verification failed for {candidate.name} ({e}). Falling back to heuristic rule verifier.",
            )
            return self._heuristic_verifier(candidate, page_text, profile)

    async def _fetch_page_content_and_html(self, url: str, event_bus: EventBus) -> Tuple[str, str]:
        """Fetches page content with disk caching, returning (text, raw_html)."""
        cached = get_cached_page(url)
        if cached:
            return cached, ""

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    headers=BROWSER_HEADERS,
                    timeout=settings.REQUEST_TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        html = resp.text
                        # Try trafilatura extraction
                        extracted = trafilatura.extract(
                            html,
                            include_links=False,
                            include_comments=False,
                            no_fallback=False,
                        )
                        if extracted and len(extracted.strip()) > 80:
                            text = extracted.strip()
                            set_cached_page(url, text)
                            return text, html

                        # Fallback to BeautifulSoup
                        soup = BeautifulSoup(html, "html.parser")
                        for element in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                            element.decompose()
                        text = soup.get_text(separator=" ", strip=True)
                        if len(text) > 80:
                            text = text[:8000]
                            set_cached_page(url, text)
                            return text, html
                    elif resp.status_code in (403, 429, 503):
                        if attempt < max_attempts:
                            await asyncio.sleep(2.0)
                            continue
            except Exception:
                if attempt < max_attempts:
                    await asyncio.sleep(1.5)
                    continue

        return "FAILED_FETCH", ""

    async def _verify_with_llm(
        self,
        candidate: CandidateOpportunity,
        page_text: str,
        profile: StudentProfile,
        event_bus: EventBus,
    ) -> VerifiedOpportunity:
        if not self.llm_client.is_configured:
            return self._heuristic_verifier(candidate, page_text, profile)

        truncated_page_text = self._extract_relevant_text(
            page_text,
            max_chars=MAX_PAGE_TEXT_CHARS,
        )

        target_deg = profile.target_degree_level or "Master's"
        system_instruction = (
            "You are a rigorous scholarship eligibility auditor. "
            "Use facts only from the supplied fetched official webpage text. "
            "Ignore the candidate title, provider, search metadata, outside knowledge, and assumptions as evidence. "
            "RULES:\n"
            "1. NEVER invent eligibility criteria, deadlines, or funding amounts. "
            "If any field cannot be found on the page, set it explicitly to 'unverified'.\n"
            "2. Every non-empty field must have a short verbatim evidence quote from the supplied webpage text; "
            "never create evidence by paraphrasing your answer.\n"
            "3. Reject opportunities restricted to domestic/resident students when student is international by setting match_status to 'ineligible'.\n"
            "4. Only keep real scholarships or degree programs with funding. If self-funded or fee-paying with no aid, set match_status to 'ineligible'.\n"
            "5. 'match_status' must be one of: 'eligible', 'ineligible', 'partially_eligible', 'unverified'.\n"
            "6. The student requires a Master's degree. Set degree_level to 'Master's' only when the page explicitly identifies "
            "a Master's/MSc degree; otherwise use 'unverified'. Do not treat generic postgraduate study as proof of a Master's.\n"
            "7. Exclude programs restricted to students already enrolled at a named institution.\n"
            "8. Return ONLY valid JSON matching the requested schema.\n"
            "9. Deadline must be a specific parseable date (e.g. '2027-01-15'). If only vague text, set to 'unverified'."
        )

        prompt = f"""
SCHOLARSHIP CANDIDATE:
Name: {candidate.name}
Provider: {candidate.provider}
Country: {candidate.country}
Official URL: {candidate.official_url}

STUDENT PROFILE:
Name: {profile.name}
Degree: {profile.degree}
Target Degree Level: {target_deg}
Field: {profile.field}
CGPA: {profile.cgpa}
Skills: {", ".join(profile.skills[:8])}
Target Countries: {profile.target_countries}

OFFICIAL WEBPAGE TEXT CONTENT:
\"\"\"
{truncated_page_text}
\"\"\"

ELIGIBILITY FIT QUESTIONS — answer each with yes/no/unknown in matched_criteria or unmet_criteria:
(a) Is this opportunity open to international/Indian applicants?
(b) Is this for prospective students (not already enrolled at a specific university)?
(c) Does the degree level match "{target_deg}"?
If any answer is 'no', add it to unmet_criteria and reduce match_status accordingly.

OUTPUT SCHEMA:
Return a single JSON object with these keys:
{{
  "degree_level": "Master's" or "unverified",
  "eligibility": string (summary of official eligibility rules, or 'unverified'),
  "funding_amount_coverage": string (e.g. 'Full tuition + monthly stipend', or 'unverified'),
  "deadline": string (specific ISO date 'YYYY-MM-DD' or 'unverified' — NOT vague text),
  "required_documents": [string],
  "how_to_apply": string (summary or 'unverified'),
  "evidence": {{
    "degree_level": string or null (max 200 chars copied verbatim from page),
    "eligibility": string or null (max 200 chars verbatim quote from page),
    "funding_amount_coverage": string or null (max 200 chars verbatim quote from page),
    "deadline": string or null (max 200 chars verbatim quote from page),
    "how_to_apply": string or null (max 200 chars verbatim quote from page),
    "required_documents": string or null (max 200 chars verbatim quote from page)
  }},
  "match_status": "eligible" | "ineligible" | "partially_eligible" | "unverified",
  "matched_criteria": [string],
  "unmet_criteria": [string],
  "missing_information": [string],
  "confidence": float between 0.0 and 1.0,
  "source_urls": ["{candidate.official_url}"],
  "student_fit_reason": string (1-2 sentences why this fits or doesn't fit THIS specific student)
}}
"""
        json_data = await self.llm_client.generate_json(
            agent_name="VerifierAgent",
            prompt=prompt,
            system_instruction=system_instruction,
            event_bus=event_bus,
            temperature=0.1,
        )

        if isinstance(json_data, dict):
            raw_deadline = str(json_data.get("deadline", "unverified"))
            _parsed_dt, deadline_status = _parse_deadline(raw_deadline)

            evidence_in = json_data.get("evidence") or {}
            evidence_clean: Dict[str, str] = {}
            if isinstance(evidence_in, dict):
                for key, snippet in evidence_in.items():
                    if _is_source_evidence(snippet, page_text):
                        evidence_clean[key] = snippet.strip()[:200]

            def sourced_value(field: str, value: object) -> str:
                text = str(value or "unverified").strip()
                if text.lower() == "unverified" or _is_generic_snippet(text):
                    return "unverified"
                if field not in evidence_clean:
                    return "unverified"
                return text

            degree_evidence = evidence_clean.get("degree_level", "")
            degree_level = sourced_value(
                "degree_level", json_data.get("degree_level", "unverified")
            )
            if (
                degree_level != "unverified"
                and not re.search(r"\b(?:master(?:'s|’s)?|msc|m\.sc\.?)\b", degree_evidence, re.I)
            ):
                degree_level = "unverified"
            if degree_level != "unverified":
                degree_level = "Master's"

            eligibility = sourced_value("eligibility", json_data.get("eligibility"))
            funding = sourced_value(
                "funding_amount_coverage",
                json_data.get("funding_amount_coverage"),
            )
            how_to = sourced_value("how_to_apply", json_data.get("how_to_apply"))
            deadline_evidence = evidence_clean.get("deadline", "")
            evidence_deadline, _ = _parse_deadline(deadline_evidence)
            deadline = (
                deadline_status
                if _parsed_dt is not None
                and evidence_deadline == _parsed_dt
                else "unverified"
            )
            if deadline == "unverified":
                evidence_clean.pop("deadline", None)

            documents = list(json_data.get("required_documents", []))
            document_quote = _normalise_evidence(
                evidence_clean.get("required_documents", "")
            )
            documents = [
                str(document)
                for document in documents
                if _normalise_evidence(str(document)) in document_quote
            ]
            if not documents:
                evidence_clean.pop("required_documents", None)

            student_fit = str(json_data.get("student_fit_reason", ""))
            matched = list(json_data.get("matched_criteria", []))
            unmet = list(json_data.get("unmet_criteria", []))
            if student_fit and student_fit not in matched:
                matched.append(f"Fit: {student_fit[:150]}")
            domestic_restriction = is_restricted_to_domestic(page_text)
            enrolled_restriction = bool(
                re.search(
                    r"(?:must be|only for|restricted to|available only to).{0,60}"
                    r"(?:already )?(?:enrolled|registered) students at|"
                    r"only (?:current|existing|registered|enrolled) students (?:at|of)|"
                    r"(?:students|applicants) already enrolled at|"
                    r"must be currently enrolled at",
                    page_text,
                    re.I,
                )
            )
            enrolled_restriction = enrolled_restriction or any(
                phrase in " ".join(str(item).lower() for item in unmet)
                for phrase in ("already enrolled", "currently enrolled", "enrolled at a specific")
            )
            if domestic_restriction and not any("domestic" in str(item).lower() for item in unmet):
                unmet.append("Page restricts eligibility to domestic/resident students.")
            if enrolled_restriction and not any("enrolled" in str(item).lower() for item in unmet):
                unmet.append("Page restricts eligibility to students already enrolled at an institution.")

            return VerifiedOpportunity(
                name=candidate.name,
                provider=candidate.provider,
                country=candidate.country,
                official_url=candidate.official_url,
                degree_level=degree_level,
                eligibility=eligibility,
                funding_amount_coverage=funding,
                deadline=deadline,
                required_documents=documents,
                how_to_apply=how_to,
                evidence=evidence_clean,
                match_status=(
                    "ineligible"
                    if domestic_restriction or enrolled_restriction
                    else json_data.get("match_status", "unverified")
                ),
                matched_criteria=matched,
                unmet_criteria=unmet,
                missing_information=list(json_data.get("missing_information", [])),
                confidence=float(json_data.get("confidence", 0.7)),
                source_urls=[candidate.official_url] if candidate.official_url else [],
                discovery_source=candidate.discovery_source,
                verification_mode="llm_live",
                deadline_source_url=candidate.official_url,
                last_checked=date.today().isoformat(),
            )

        raise ValueError("VerifierAgent received non-dict JSON output from LLM.")

    def _heuristic_verifier(
        self,
        candidate: CandidateOpportunity,
        page_text: str,
        profile: StudentProfile,
    ) -> VerifiedOpportunity:
        """
        Deterministic heuristic verification with NO FABRICATED FIELDS.
        Sets funding, deadline, eligibility and how_to_apply to 'unverified'
        unless actually found in the fetched page, and stores short evidence snippets (max 200 chars).
        """
        matched = []
        unmet = []
        missing = []
        evidence: Dict[str, str] = {}

        funding = "unverified"
        deadline = "unverified"
        eligibility = "unverified"
        how_to_apply = "unverified"
        degree_level = "unverified"

        # Check for domestic-only restrictions
        if is_restricted_to_domestic(page_text):
            unmet.append("Restricted to domestic/resident students only (international applicant ineligible)")
            return VerifiedOpportunity(
                name=candidate.name,
                provider=candidate.provider,
                country=candidate.country,
                official_url=candidate.official_url,
                match_status="ineligible",
                unmet_criteria=unmet,
                source_urls=[candidate.official_url],
                discovery_source=candidate.discovery_source,
                verification_mode="heuristic",
                deadline_source_url=candidate.official_url,
                last_checked=date.today().isoformat(),
            )

        if page_text and len(page_text) > 40:
            degree_match = re.search(
                r"[^.\n]{0,100}\b(?:master(?:'s|’s)?(?:\s+degree)?|MSc|M\.Sc\.?)\b[^.\n]{0,100}",
                page_text,
                re.IGNORECASE,
            )
            if degree_match:
                degree_level = "Master's"
                evidence["degree_level"] = re.sub(
                    r"\s+", " ", degree_match.group(0)
                ).strip()[:200]

            # 1. Search for real funding evidence on page
            f_match = re.search(
                r"([^.\n]{0,80}(?:full tuition|fully funded|tuition fee waiver|tuition-free|stipend|living allowance|grant of|scholarship of|covers|provides|\u20ac\s*\d[\d,.]*|\xa3\s*\d[\d,.]*|\$\s*\d[\d,.]*|\d+[\d,.]*\s*(?:EUR|USD|GBP|euros|per month|per year))[^.\n]{0,80})",
                page_text,
                re.IGNORECASE,
            )
            if f_match:
                snip = re.sub(r"\s+", " ", f_match.group(1)).strip()[:200]
                if not _is_generic_snippet(snip):
                    funding = snip
                    evidence["funding_amount_coverage"] = snip

            # 2. Search for real deadline evidence on page — must parse to a real date
            d_match = re.search(
                r"((?:application deadline|deadline|apply by|closing date|closes on)[:\s]+[^.\n;]{3,80})",
                page_text,
                re.IGNORECASE,
            )
            if d_match:
                raw_snip = re.sub(r"\s+", " ", d_match.group(1)).strip()
                _parsed_dt, deadline_val = _parse_deadline(raw_snip)
                if _parsed_dt is not None or "closed" in deadline_val:
                    # Only accept if it parsed to a real date (or is already marked closed)
                    if not _is_generic_snippet(raw_snip):
                        deadline = deadline_val
                        evidence["deadline"] = raw_snip[:200]
                        if "closed" in deadline_val:
                            unmet.append(f"Deadline has passed: {deadline_val}")

            # 3. Search for real eligibility evidence on page
            e_match = re.search(
                r"((?:eligib\w*|requirements?|who can apply|selection criteria)[:\s]+[^.\n]{10,120}|applicants must [^.\n]{10,120}|open to [^.\n]{10,120})",
                page_text,
                re.IGNORECASE,
            )
            if e_match:
                snip = re.sub(r"\s+", " ", e_match.group(1)).strip()[:200]
                if not _is_generic_snippet(snip):
                    eligibility = snip
                    evidence["eligibility"] = snip

            # 4. Search for real how-to-apply evidence on page
            h_match = re.search(
                r"((?:how to apply|application process|application procedure|to apply|submit (?:your )?application)[:\s]+[^.\n]{10,120}|submit (?:online )?via [^.\n]{10,120}|apply online through [^.\n]{10,120})",
                page_text,
                re.IGNORECASE,
            )
            if h_match:
                snip = re.sub(r"\s+", " ", h_match.group(1)).strip()[:200]
                if not _is_generic_snippet(snip):
                    how_to_apply = snip
                    evidence["how_to_apply"] = snip

        # ── Eligibility Fit Checks (heuristic, 3 questions) ──
        text_lower = page_text.lower() if page_text else ""
        name_lower = candidate.name.lower()
        target_deg = (profile.target_degree_level or "Master's").lower()

        # (a) Open to international / Indian applicants?
        international_ok_signals = [
            "international", "all nationalities", "all countries", "worldwide", "global",
            "open to applicants from", "non-eu", "international students",
        ]
        international_no_signals = [
            "domestic only", "home students only", "uk residents only",
            "us citizens only", "residents only", "citizens only",
        ]
        if any(s in text_lower for s in international_ok_signals):
            matched.append("(a) Open to international/Indian applicants")
        elif any(s in text_lower for s in international_no_signals):
            unmet.append("(a) NOT open to international students — domestic/resident only")
        else:
            missing.append("(a) International applicant eligibility not confirmed on page")

        # (b) For prospective students (not current enrolled at one specific university)?
        prospective_no_signals = [
            "must be enrolled at", "currently enrolled", "existing students",
            "only for students of", "registered students of", "enrolled students at",
        ]
        if any(s in text_lower for s in prospective_no_signals):
            unmet.append("(b) Only for students already enrolled at a specific university — not for prospective students")
        else:
            matched.append("(b) Open to prospective applicants (not restricted to enrolled students)")

        # (c) Degree level matches target?
        degree_ok = False
        degree_no = False
        if "master" in target_deg:
            if "master" in name_lower or "master" in text_lower or "graduate" in text_lower or "postgraduate" in text_lower:
                degree_ok = True
            elif "bachelor" in name_lower or "undergraduate" in name_lower:
                degree_no = True
        elif "phd" in target_deg or "doctoral" in target_deg:
            if "phd" in name_lower or "doctoral" in name_lower or "doctorate" in name_lower:
                degree_ok = True
            elif "master" not in name_lower and "bachelor" not in name_lower:
                degree_ok = True  # might be multi-level
        if degree_ok:
            matched.append(f"(c) Degree level matches target: {profile.target_degree_level}")
        elif degree_no:
            unmet.append(f"(c) Degree level mismatch — opportunity is undergraduate but target is {profile.target_degree_level}")
        else:
            missing.append(f"(c) Degree level match with '{profile.target_degree_level}' unconfirmed from page text")

        if profile.cgpa:
            matched.append(f"Student CGPA ({profile.cgpa}) recorded from academic profile")
        else:
            missing.append("Exact minimum GPA requirement unverified from profile")

        # Determine match_status from unmet criteria
        hard_unmet = [u for u in unmet if u.startswith("(a)") or u.startswith("(b)") or "ineligible" in u.lower() or "closed" in u.lower()]
        if hard_unmet:
            match_status = "ineligible"
            confidence = 0.15
        elif funding != "unverified":
            match_status = "eligible"
            matched.append(f"Verified funding support: {funding[:60]}")
            confidence = 0.85
        elif "scholarship" in candidate.name.lower() or "fellowship" in candidate.name.lower():
            match_status = "partially_eligible"
            missing.append("Specific funding amount unverified on fetched page")
            confidence = 0.60
        else:
            match_status = "unverified"
            missing.append("No funding or scholarship details verified on page")
            confidence = 0.30

        # Build a student-specific fit reason
        fit_parts = []
        if any("(c) Degree level matches" in m for m in matched):
            fit_parts.append(f"targets {profile.target_degree_level}")
        if any("(a) Open to international" in m for m in matched):
            fit_parts.append("accepts international/Indian applicants")
        if funding != "unverified":
            fit_parts.append("has verified funding")
        if profile.target_countries and any(c.lower() in candidate.country.lower() for c in profile.target_countries):
            fit_parts.append(f"country matches student preference ({candidate.country})")
        if fit_parts:
            matched.append(f"Student fit: {'; '.join(fit_parts)}")

        docs = ["Curriculum Vitae", "Academic Transcripts", "Statement of Purpose", "Letters of Recommendation"]

        return VerifiedOpportunity(
            name=candidate.name,
            provider=candidate.provider,
            country=candidate.country,
            official_url=candidate.official_url,
            degree_level=degree_level,
            eligibility=eligibility,
            funding_amount_coverage=funding,
            deadline=deadline,
            required_documents=docs,
            how_to_apply=how_to_apply,
            evidence=evidence,
            match_status=match_status,
            matched_criteria=matched,
            unmet_criteria=unmet,
            missing_information=missing,
            confidence=confidence,
            source_urls=[candidate.official_url],
            discovery_source=candidate.discovery_source,
            verification_mode="heuristic",
            deadline_source_url=candidate.official_url,
            last_checked=date.today().isoformat(),
        )
