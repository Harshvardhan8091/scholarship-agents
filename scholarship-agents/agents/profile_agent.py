from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import httpx
from pypdf import PdfReader

from config import settings
from event_bus import EventBus
from llm_client import LLMClient
from models import StudentProfile, StudentProject


def normalize_github_username(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    candidate = value.strip()
    if candidate.startswith("@"):
        candidate = candidate[1:]
    if "://" in candidate:
        parsed = urlparse(candidate)
        if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
            return None
        candidate = parsed.path.strip("/").split("/")[0]
    elif candidate.lower().startswith(("github.com/", "www.github.com/")):
        candidate = candidate.split("/", 1)[1].strip("/").split("/")[0]
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", candidate):
        return None
    return candidate


def valid_github_token(value: Optional[str]) -> bool:
    token = (value or "").strip()
    return (
        bool(token)
        and "your" not in token.lower()
        and token.startswith(("ghp_", "github_pat_"))
    )


class ProfileAgent:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    async def run(
        self,
        resume_path: Optional[str],
        marksheet_path: Optional[str],
        github_username: Optional[str],
        event_bus: EventBus,
        target_countries: Optional[List[str] | str] = None,
        target_degree_level: Optional[str] = None,
        financial_need: Optional[str] = None,
        interests: Optional[str] = None,
    ) -> StudentProfile:
        await event_bus.emit(
            agent="ProfileAgent",
            event_type="started",
            message="Starting student profile extraction from documents and GitHub...",
        )

        # 1. Parse resume
        resume_text = ""
        resume_error = "No resume file was provided."
        if resume_path and os.path.exists(resume_path):
            await event_bus.emit(
                agent="ProfileAgent",
                event_type="progress",
                message=f"Parsing resume PDF from {Path(resume_path).name}...",
            )
            resume_text, resume_error = self._extract_document_text(resume_path, "Resume")
        else:
            await event_bus.emit(
                agent="ProfileAgent",
                event_type="progress",
                message="No resume file provided or file not found.",
            )

        # 2. Parse marksheet
        marksheet_text = ""
        marksheet_error = "No marksheet file was provided."
        if marksheet_path and os.path.exists(marksheet_path):
            await event_bus.emit(
                agent="ProfileAgent",
                event_type="progress",
                message=f"Parsing marksheet from {Path(marksheet_path).name}...",
            )
            marksheet_text, marksheet_error = self._extract_document_text(marksheet_path, "Marksheet")
        else:
            await event_bus.emit(
                agent="ProfileAgent",
                event_type="progress",
                message="No marksheet file provided or file not found.",
            )

        # 3. Fetch GitHub data
        github_data: Dict[str, Any] = {}
        normalized_username = normalize_github_username(github_username)
        if github_username and not normalized_username:
            github_data = {
                "username": None,
                "status": "invalid_username",
                "error": "Enter a valid GitHub profile URL or username.",
            }
            await event_bus.emit(
                agent="ProfileAgent",
                event_type="recovery",
                message=github_data["error"],
            )
        elif normalized_username:
            await event_bus.emit(
                agent="ProfileAgent",
                event_type="progress",
                message=f"Fetching public profile and repositories for GitHub user: @{normalized_username}...",
            )
            github_data = await self._fetch_github_profile(normalized_username, event_bus)

        # 4. Use an LLM, when available, to structure StudentProfile
        self.llm_client.last_provider = None
        profile = await self._synthesize_profile_llm(
            resume_text=resume_text,
            marksheet_text=marksheet_text,
            github_data=github_data,
            event_bus=event_bus,
            target_countries=target_countries,
            target_degree_level=target_degree_level,
            financial_need=financial_need,
            interests=interests,
        )
        await event_bus.emit(
            agent="ProfileAgent",
            event_type="progress",
            message="Structured StudentProfile extraction completed.",
        )
        supplied_countries = self._parse_list(target_countries)
        if supplied_countries:
            profile.target_countries = supplied_countries
        if target_degree_level:
            profile.target_degree_level = target_degree_level
        if financial_need:
            profile.financial_need = financial_need
        supplied_interests = self._parse_list(interests)
        if supplied_interests:
            profile.interests = supplied_interests

        profile.github_username = github_data.get("username")
        profile.github_repos_found = github_data.get("repos_found")
        profile.inputs_read = {
            "resume": {
                "filename": Path(resume_path).name if resume_path else None,
                "characters_read": len(resume_text),
                "reason": resume_error if not resume_text else None,
            },
            "marksheet": {
                "filename": Path(marksheet_path).name if marksheet_path else None,
                "characters_read": len(marksheet_text),
                "reason": marksheet_error if not marksheet_text else None,
            },
            "github": {
                "username": github_data.get("username"),
                "repos_found": github_data.get("repos_found"),
                "public_repos": github_data.get("public_repos"),
                "stars": github_data.get("stars"),
                "status": github_data.get("status", "not_provided"),
                "reason": github_data.get("error"),
            },
            "user_supplied_fields": [
                key
                for key, value in (
                    ("target_countries", supplied_countries),
                    ("target_degree_level", target_degree_level),
                    ("financial_need", financial_need),
                    ("interests", supplied_interests),
                )
                if value
            ],
            "extracted_fields": [
                key for key, value in (
                    ("resume", resume_text),
                    ("marksheet", marksheet_text),
                    ("github", github_data.get("profile_fetched", False)),
                ) if value
            ],
        }
        profile.gaps_or_missing_info = self._remove_supplied_gaps(
            profile.gaps_or_missing_info,
            profile,
            resume_text,
            marksheet_text,
            github_data,
        )

        await event_bus.emit(
            agent="ProfileAgent",
            event_type="done",
            message=f"Successfully extracted profile for student: {profile.name or 'Unknown'} (Field: {profile.field or 'General'}, CGPA: {profile.cgpa or 'N/A'}).",
            details={
                "name": profile.name,
                "degree": profile.degree,
                "field": profile.field,
                "cgpa": profile.cgpa,
                "skills_count": len(profile.skills),
                "projects_count": len(profile.projects),
            },
        )
        return profile

    @staticmethod
    def _parse_list(value: Optional[List[str] | str]) -> List[str]:
        if isinstance(value, str):
            values = re.split(r"[,;\n]+", value)
        else:
            values = value or []
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))

    @staticmethod
    def _remove_supplied_gaps(
        gaps: List[str],
        profile: StudentProfile,
        resume_text: str,
        marksheet_text: str,
        github_data: Dict[str, Any],
    ) -> List[str]:
        source_text = f"{resume_text}\n{marksheet_text}".lower()
        supplied = {
            "countr": bool(profile.target_countries) or "target countries" in source_text,
            "financial": bool(profile.financial_need) or "financial need" in source_text,
            "interest": bool(profile.interests) or "interest" in source_text,
            "marksheet": bool(marksheet_text),
            "transcript": bool(marksheet_text),
            "gpa": bool(profile.cgpa and str(profile.cgpa).lower() not in {"n/a", "unknown"}),
            "grade": bool(profile.cgpa and str(profile.cgpa).lower() not in {"n/a", "unknown"}),
            "github": bool(github_data.get("username")),
            "repository": github_data.get("status") in ("ok", "no_public_repositories"),
            "repo": github_data.get("status") in ("ok", "no_public_repositories"),
        }
        return [
            gap for gap in gaps
            if not any(term in gap.lower() and present for term, present in supplied.items())
        ]

    @staticmethod
    def _extract_pdf_text(pdf_path: str) -> str:
        return ProfileAgent._extract_pdf_text_with_status(pdf_path)[0]

    @staticmethod
    def _extract_pdf_text_with_status(
        pdf_path: str, document_label: str = "Marksheet"
    ) -> tuple[str, str]:
        try:
            reader = PdfReader(pdf_path)
            pages_text = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text()
                if text:
                    pages_text.append(text)
            text = "\n\n".join(pages_text).strip()
        except Exception:
            text = ""
        if len(text) >= 50:
            return text, ""

        try:
            import pdfplumber

            with pdfplumber.open(pdf_path) as pdf:
                fallback = "\n\n".join(page.extract_text() or "" for page in pdf.pages).strip()
            if len(fallback) > len(text):
                text = fallback
        except ImportError:
            pass
        except Exception:
            pass
        if len(text) >= 50:
            return text, ""

        try:
            import pytesseract
            from pdf2image import convert_from_path

            images = convert_from_path(pdf_path, first_page=1, last_page=3)
            ocr_text = "\n\n".join(pytesseract.image_to_string(image) for image in images).strip()
            if len(ocr_text) > len(text):
                text = ocr_text
        except ImportError:
            pass
        except Exception:
            pass
        if len(text) >= 50:
            return text, ""
        return "", f"{document_label} is a scanned image and no text could be read; please upload a text-based PDF or TXT."

    @staticmethod
    def _extract_document_text(
        file_path: str, document_label: str = "Marksheet"
    ) -> tuple[str, str]:
        path = Path(file_path)
        if path.suffix.lower() == ".pdf":
            return ProfileAgent._extract_pdf_text_with_status(file_path, document_label)
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            try:
                from PIL import Image
                import pytesseract

                text = pytesseract.image_to_string(Image.open(file_path)).strip()
                if text:
                    return text, ""
            except ImportError:
                return "", "Image text extraction is unavailable; install pytesseract and Pillow with Tesseract OCR."
            except Exception:
                return "", "No text could be read from the uploaded image."
            return "", "No text could be read from the uploaded image."
        if path.suffix.lower() == ".txt":
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as file:
                    return file.read().strip(), ""
            except OSError:
                return "", "The uploaded text file could not be read."
        return "", f"Unsupported file type: {path.suffix or 'unknown'}."

    def _extract_marksheet_text(self, file_path: str) -> str:
        return self._extract_document_text(file_path)[0]

    async def _fetch_github_profile(
        self, username: str, event_bus: EventBus
    ) -> Dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ScholarshipFinder-Agent/1.0",
        }
        has_token = valid_github_token(settings.GITHUB_TOKEN)
        if has_token:
            headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"

        result: Dict[str, Any] = {
            "username": username,
            "name": None,
            "bio": None,
            "public_repos": None,
            "repos_found": None,
            "stars": 0,
            "top_languages": [],
            "repos_summary": [],
            "status": "request_failed",
            "profile_fetched": False,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                user_url = f"https://api.github.com/users/{username}"
                user_resp = await self._github_get(client, user_url, headers, has_token)
                if user_resp.status_code == 200:
                    u_data = user_resp.json()
                    result["name"] = u_data.get("name")
                    result["bio"] = u_data.get("bio")
                    result["public_repos"] = u_data.get("public_repos", 0)
                    result["profile_fetched"] = True
                elif user_resp.status_code == 404:
                    result.update(status="not_found", error="GitHub user not found")
                    await event_bus.emit(
                        agent="ProfileAgent",
                        event_type="recovery",
                        message="GitHub user not found",
                    )
                    return result
                elif user_resp.status_code in (403, 429):
                    result.update(status="rate_limited", error="GitHub API rate limit reached.")
                    await event_bus.emit(
                        agent="ProfileAgent",
                        event_type="recovery",
                        message="GitHub API rate limit reached. Proceeding without GitHub repository data.",
                    )
                    return result
                elif user_resp.status_code >= 400:
                    result["error"] = f"GitHub profile request failed ({user_resp.status_code})."
                    return result

                repos_resp = await self._github_get(
                    client,
                    f"https://api.github.com/users/{username}/repos?per_page=100&sort=updated",
                    headers,
                    has_token,
                )
                if repos_resp.status_code == 200:
                    repos = repos_resp.json()
                    if not isinstance(repos, list):
                        result.update(
                            status="repositories_failed",
                            error="GitHub returned an unexpected repositories response.",
                        )
                        return result
                    total_stars = 0
                    lang_counts: Dict[str, int] = {}
                    repos_summary = []

                    for r in repos:
                        if not isinstance(r, dict):
                            continue
                        stars = r.get("stargazers_count", 0)
                        total_stars += stars
                        lang = r.get("language")
                        if lang:
                            lang_counts[lang] = lang_counts.get(lang, 0) + 1
                        repos_summary.append({
                            "name": r.get("name"),
                            "description": r.get("description"),
                            "language": lang,
                            "stars": stars,
                            "url": r.get("html_url"),
                        })

                    sorted_langs = sorted(
                        lang_counts.keys(), key=lambda l: lang_counts[l], reverse=True
                    )
                    repos_summary.sort(key=lambda repo: repo["stars"], reverse=True)
                    result["stars"] = total_stars
                    result["repos_found"] = len(repos)
                    result["top_languages"] = sorted_langs[:5]
                    result["repos_summary"] = repos_summary[:8]
                    result["status"] = "no_public_repositories" if len(repos) == 0 else "ok"
                elif repos_resp.status_code in (403, 429):
                    result.update(status="rate_limited", error="GitHub API rate limit reached.")
                elif repos_resp.status_code == 404:
                    result.update(status="not_found", error="GitHub user not found")
                else:
                    result.update(
                        status="repositories_failed",
                        error=f"GitHub repositories request failed ({repos_resp.status_code}).",
                    )

            except (httpx.HTTPError, ValueError) as e:
                result.update(status="request_failed", error="GitHub request failed.")
                await event_bus.emit(
                    agent="ProfileAgent",
                    event_type="recovery",
                    message="GitHub request failed. Continuing with document data only.",
                )

        return result

    @staticmethod
    async def _github_get(
        client: httpx.AsyncClient,
        url: str,
        headers: Dict[str, str],
        has_token: bool,
    ) -> httpx.Response:
        response = await client.get(url, headers=headers)
        if response.status_code == 401 and has_token:
            fallback_headers = {key: value for key, value in headers.items() if key != "Authorization"}
            response = await client.get(url, headers=fallback_headers)
        return response

    @staticmethod
    def infer_target_degree_level(degree: Optional[str]) -> str:
        """Infers the next level up from student's current degree."""
        if not degree:
            return "Master's"
        d = degree.lower()
        if any(w in d for w in ["bachelor", "b.tech", "btech", "b.e.", "be", "b.sc", "bsc", "b.com", "bcom", "undergraduate", "bba"]):
            return "Master's"
        if any(w in d for w in ["master", "m.tech", "mtech", "m.sc", "msc", "m.e.", "postgraduate", "mba", "meng"]):
            return "PhD"
        if any(w in d for w in ["high school", "secondary", "12th", "diploma"]):
            return "Bachelor's"
        return "Master's"

    async def _synthesize_profile_llm(
        self,
        resume_text: str,
        marksheet_text: str,
        github_data: Dict[str, Any],
        event_bus: EventBus,
        target_countries: Optional[List[str] | str] = None,
        target_degree_level: Optional[str] = None,
        financial_need: Optional[str] = None,
        interests: Optional[str] = None,
    ) -> StudentProfile:
        system_instruction = (
            "You are an expert academic evaluator and resume parser. "
            "Your job is to extract a strictly grounded StudentProfile JSON from provided academic texts. "
            "CRITICAL RULES:\n"
            "1. NEVER invent or hallucinate facts, grades, colleges, or projects.\n"
            "2. If an attribute (e.g. CGPA, financial_need, target_countries) is not explicitly stated in the input text, leave it null or an empty list.\n"
            "3. Any missing key requirements (like missing target countries, unstated GPA, missing test scores) must be recorded in 'gaps_or_missing_info'.\n"
            "4. Infer 'target_degree_level' as the next level up (e.g. a final-year bachelor's student targeting master's should have 'Master's').\n"
            "5. Return ONLY a valid JSON object matching the requested schema."
        )

        prompt = f"""
EXTRACT STUDENT PROFILE FROM THE FOLLOWING SOURCES:

=== RESUME TEXT ===
{resume_text if resume_text else "[No resume content provided]"}

=== MARKSHEET / ACADEMIC RECORDS ===
{marksheet_text if marksheet_text else "[No marksheet content provided]"}

=== GITHUB PROFILE DATA ===
Username: {github_data.get('username')}
Name: {github_data.get('name')}
Bio: {github_data.get('bio')}
Public Repos: {github_data.get('public_repos')}
Total Stars: {github_data.get('stars')}
Top Languages: {github_data.get('top_languages')}
Notable Repos: {github_data.get('repos_summary')}

USER-SUPPLIED PREFERENCES (authoritative; do not replace with inferred values):
Target countries: {target_countries}
Target degree level: {target_degree_level}
Financial need: {financial_need}
Interests or research areas: {interests}

OUTPUT SCHEMA:
Return a single JSON object with these keys:
{{
  "name": string or null,
  "degree": string or null (e.g. "Bachelor of Technology in Computer Science"),
  "target_degree_level": string or null (e.g. "Master's" for a Bachelor's student, "PhD" for a Master's student),
  "field": string or null (e.g. "Computer Science / Artificial Intelligence"),
  "cgpa": string or float or null (e.g. "8.9/10" or 3.8),
  "skills": [string],
  "projects": [
     {{"title": "...", "description": "...", "technologies": ["..."], "github_url": "..."}}
  ],
  "achievements": [string],
  "interests": [string],
  "target_countries": [string],
  "financial_need": string or null (e.g. "high", "moderate", or null if not stated),
  "gaps_or_missing_info": [string]
}}
"""
        # If Gemini is not configured, generate fallback extraction from text
        if not self.llm_client.is_configured:
            await event_bus.emit(
                agent="ProfileAgent",
                event_type="recovery",
                message="No LLM provider is configured; using rule-based profile extraction.",
            )
            return self._heuristic_profile_extraction(
                resume_text, marksheet_text, github_data
            )

        try:
            json_data = await self.llm_client.generate_json(
                agent_name="ProfileAgent",
                prompt=prompt,
                system_instruction=system_instruction,
                event_bus=event_bus,
                temperature=0.1,
            )

            if isinstance(json_data, dict):
                # inject github extra fields
                json_data["github_bio"] = github_data.get("bio")
                json_data["github_username"] = github_data.get("username")
                json_data["github_public_repos"] = github_data.get("public_repos")
                json_data["github_repos_found"] = github_data.get("repos_found")
                json_data["github_stars"] = github_data.get("stars")
                json_data["github_top_languages"] = github_data.get("top_languages", [])
                if not json_data.get("name") and github_data.get("name"):
                    json_data["name"] = github_data["name"]
                profile = StudentProfile.model_validate(json_data)
                if not profile.target_degree_level:
                    profile.target_degree_level = self.infer_target_degree_level(profile.degree)
                return profile
        except Exception as e:
            await event_bus.emit(
                agent="ProfileAgent",
                event_type="recovery",
                message=f"LLM profile extraction failed ({e}). Falling back to heuristic profile extraction.",
            )
            return self._heuristic_profile_extraction(
                resume_text, marksheet_text, github_data
            )

        return self._heuristic_profile_extraction(
            resume_text, marksheet_text, github_data
        )

    def _heuristic_profile_extraction(
        self, resume_text: str, marksheet_text: str, github_data: Dict[str, Any]
    ) -> StudentProfile:
        """Dynamic rule-based parser used when offline or testing without LLM key."""
        combined_text = f"{resume_text}\n{marksheet_text}"

        # 1. Name extraction
        name = "Student Candidate"
        m_name = re.search(r"(?:Candidate|Applicant|Name)[:\s]+([A-Za-z\s]+?)(?:\||\n|$)", combined_text, re.IGNORECASE)
        if m_name and len(m_name.group(1).strip()) > 2:
            name = m_name.group(1).strip()
        else:
            lines = [l.strip() for l in resume_text.splitlines() if l.strip()]
            if lines:
                name = lines[0]
            elif github_data.get("name"):
                name = github_data["name"]

        # 2. Field & Degree extraction
        lower_comb = combined_text.lower()
        if "mechanical" in lower_comb:
            field = "Mechanical Engineering"
            degree = "Bachelor of Engineering in Mechanical Engineering"
        elif "commerce" in lower_comb or "finance" in lower_comb or "accounting" in lower_comb:
            field = "Commerce & Finance"
            degree = "Bachelor of Commerce (Honours) in Finance & Accounting"
        elif "computer" in lower_comb or "software" in lower_comb:
            field = "Computer Science"
            degree = "Bachelor of Science in Computer Science"
        else:
            m_deg = re.search(r"(Bachelor|Master|B\.E\.|B\.Tech|B\.Com|B\.Sc)[^\n,;]+", combined_text, re.IGNORECASE)
            if m_deg:
                degree = m_deg.group(0).strip()
                field = degree
            else:
                field = "General Studies"
                degree = "Bachelor's Degree"

        # 3. CGPA extraction
        cgpa = "N/A"
        m_cgpa = re.search(r"(?:Cumulative\s+)?(?:CGPA|GPA)[:\s]+([0-9.]+(?:\s*/\s*[0-9.]+)?)|([0-9]\.[0-9]{1,2}\s*/\s*10(?:\.00?)?)", combined_text, re.IGNORECASE)
        if m_cgpa:
            cgpa = (m_cgpa.group(1) or m_cgpa.group(2)).strip()
        else:
            m_score = re.search(r"([0-9]\.[0-9]{1,2})\s*/\s*10", combined_text)
            if m_score:
                cgpa = f"{m_score.group(1)}/10"

        # 4. Target Countries extraction
        target_countries = []
        m_tc = re.search(r"Target Countries[^\n:]*:\s*([^\n]+)", combined_text, re.IGNORECASE)
        if m_tc:
            raw_countries = m_tc.group(1).strip()
            target_countries = [c.strip() for c in re.split(r"[,;/]+", raw_countries) if c.strip()]

        if not target_countries:
            known = ["Japan", "South Korea", "Canada", "Germany", "United Kingdom", "United States", "France", "Australia", "Switzerland", "Sweden", "Netherlands"]
            for k in known:
                if re.search(rf"\b{k}\b", combined_text, re.IGNORECASE) and k not in target_countries:
                    target_countries.append(k)

        # 5. Financial Need
        financial_need = None
        m_fn = re.search(r"Financial Need:\s*([^\n]+)", combined_text, re.IGNORECASE)
        if m_fn:
            financial_need = m_fn.group(1).strip()

        # 6. Skills extraction
        skills = []
        m_skills = re.search(r"(?:TECHNICAL\s+)?SKILLS\s*\n[^\n]*\n([\s\S]*?)(?=\n[A-Z\s]{4,}|\Z)", resume_text)
        if m_skills:
            raw_skills_block = m_skills.group(1).strip()
            for line in raw_skills_block.splitlines():
                parts = re.split(r"[:,]+", line)
                for p in parts:
                    clean = p.strip()
                    if clean and len(clean) < 40 and not clean.startswith("-"):
                        skills.append(clean)
        if not skills:
            skills = ["Problem Solving", "Analytical Thinking", "Technical Communication"]

        # 7. Achievements extraction
        achievements = []
        m_ach = re.search(r"ACHIEVEMENTS\s*\n[^\n]*\n([\s\S]*?)(?=\n[A-Z\s]{4,}|\Z)", resume_text)
        if m_ach:
            raw_ach_block = m_ach.group(1).strip()
            for line in raw_ach_block.splitlines():
                clean = line.strip().lstrip("-*•123456789. ")
                if clean:
                    achievements.append(clean)
        if not achievements:
            achievements = ["Consistent Academic Standing"]

        # 8. Projects
        projects = []
        for repo in github_data.get("repos_summary", []):
            projects.append(
                StudentProject(
                    title=repo.get("name", "Project"),
                    description=repo.get("description") or "Open source project on GitHub",
                    technologies=[repo.get("language")] if repo.get("language") else [],
                    github_url=repo.get("url"),
                )
            )

        target_degree_level = self.infer_target_degree_level(degree)

        return StudentProfile(
            name=name,
            degree=degree,
            target_degree_level=target_degree_level,
            field=field,
            cgpa=cgpa,
            skills=skills[:8],
            projects=projects,
            achievements=achievements[:5],
            interests=[],
            target_countries=target_countries,
            financial_need=financial_need,
            gaps_or_missing_info=[],
            github_bio=github_data.get("bio"),
            github_username=github_data.get("username"),
            github_public_repos=github_data.get("public_repos"),
            github_repos_found=github_data.get("repos_found"),
            github_stars=github_data.get("stars"),
            github_top_languages=github_data.get("top_languages", []),
        )
