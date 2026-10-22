# 🎓 AI Scholarship & University Program Finder

A robust, production-grade autonomous multi-agent system built with **Python 3.11+**, **LangGraph**, **OpenAI-compatible Groq + Google Gemini (`google-genai` SDK)**, and **FastAPI**. 

Given a student's resume PDF, academic transcript/marksheet (PDF or plain text), and GitHub profile, a coordinated team of specialized AI agents searches the live internet for authentic scholarships, verifies eligibility criteria against official web pages, ranks the top 5 matches, and drafts a personalized, strictly-grounded Statement of Purpose (SOP).

---

## 🌟 Key Features

- **Zero Hardcoding**: Evaluates any student profile dynamically. Tested across arbitrary degree disciplines and geographies.
- **Strict Grounding & Verification**: Never invents scholarships, deadlines, or student achievements. Every single verified opportunity links to an official source URL.
- **Full Failure Recovery & Self-Healing**:
  - **Provider Fallback**: Ordered LLM fallback via `LLM_PROVIDERS` (`groq,gemini` by default), with automatic switch on 429/quota/timeout and invalid JSON.
  - **LLM Invalid JSON**: 3-stage iterative feedback loop feeding back exact parse errors to the model.
  - **Web Scraping Timeouts / SSL / Cloudflare Blocks**: Automatic backoff retry, fallback to contextual search snippet data, or explicit tagging as `"unverified"` without crashing.
  - **Insufficient Candidate Recovery**: If fewer than 10 candidates qualify after verification, the orchestrator triggers an automatic recovery loop broadening the search scope (up to 2 additional rounds).
- **Asynchronous Event Streaming**: Emits structured pipeline events (`agent`, `type: started|progress|recovery|done|error`, `message`, `timestamp`, `details`) through an async event bus ready for SSE streaming via FastAPI and terminal visualization with Rich.
- **Draft Guarantee**: SOP outputs are explicitly marked with prominent draft warnings and include an audit trail of grounded profile facts.

---

## 🏗️ Architecture

```mermaid
flowchart TD
    subgraph Inputs
        R[Resume PDF]
        M[Marksheet PDF / TXT]
        G[GitHub Username]
    end

    subgraph Agents
        PA[ProfileAgent]
        RA[ResearchAgent]
        VA[VerifierAgent]
        RKA[RankerAgent]
        WA[WriterAgent]
    end

    subgraph "Failure Recovery Loops"
        EB[Async Event Bus]
        LOOP{Verified >= 10 or Max Rounds?}
    end

    R --> PA
    M --> PA
    G --> PA

    PA -->|Structured StudentProfile| RA
    RA -->|10-15 Candidate Opportunities| VA
    VA -->|Verified Opportunities| LOOP
    LOOP -- "< 10 Candidates (Max 2 Retries)" -->|Recovery: Broader Queries| RA
    LOOP -- ">= 10 or Max Rounds" --> RKA
    RKA -->|Top 5 Ranked Matches| WA
    WA -->|Draft Statement of Purpose| OUT[output/result.json]

    PA -.->|Emit Events| EB
    RA -.->|Emit Events| EB
    VA -.->|Emit Events| EB
    RKA -.->|Emit Events| EB
    WA -.->|Emit Events| EB
```

---

## 🤖 Agents Breakdown

| Agent | Module | Responsibilities |
|---|---|---|
| **ProfileAgent** | `agents/profile_agent.py` | Extracts raw text from Resume (PDF via `pypdf`) and Marksheet (PDF or TXT). Queries GitHub REST API for repositories, top languages, and stars. Synthesizes a structured `StudentProfile` JSON. Leaves unknown fields as `null`. |
| **ResearchAgent** | `agents/research_agent.py` | Formulates targeted queries based on student's field, degree, and target countries. Queries Tavily API, performs deduplication, and returns 10–15 candidate opportunities with official URLs. |
| **VerifierAgent** | `agents/verifier_agent.py` | Fetches live web pages in parallel using bounded concurrency (`asyncio.Semaphore`). Extracts page text using `trafilatura` (falling back to `BeautifulSoup`). Audits eligibility, funding, deadlines, and requirements against student profile. Sets unverified fields to `"unverified"`. |
| **RankerAgent** | `agents/ranker_agent.py` | Scores verified opportunities on academic fit, funding coverage, deadline feasibility, and confidence. Selects and ranks the **Top 5** opportunities with personalized reasoning and application steps. |
| **WriterAgent** | `agents/writer_agent.py` | Drafts a comprehensive Statement of Purpose tailored to the chosen program. Uses **ONLY** verified facts from the student profile (no hallucinations) and marks document prominently as `[DRAFT - FOR STUDENT REVIEW ONLY]`. |
| **Orchestrator** | `graph.py` | LangGraph `StateGraph` managing state transitions, conditional edges, retry loops, and event emission. |

---

## 🛠️ Tech Stack

- **Runtime**: Python 3.11+
- **Orchestration**: LangGraph
- **LLM**: OpenAI-compatible Groq and Google Gemini via `openai` + `google-genai` SDK (`GROQ_MODEL` / `GEMINI_MODEL`)
- **Web Search**: Tavily Search API (`tavily-python`)
- **Document & Web Parsing**: `pypdf`, `httpx`, `trafilatura`, `beautifulsoup4`
- **Backend API**: FastAPI, Uvicorn, Server-Sent Events (SSE)
- **Validation**: Pydantic v2
- **Terminal UI**: Rich

---

## 🚀 Setup & Installation

### 1. Clone & Set Up Virtual Environment

```bash
cd scholarship-agents
uv venv --python 3.11 .venv
# On Windows:
.venv\Scripts\activate
# On Linux / macOS:
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
uv pip install -r requirements.txt
# or standard pip:
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` with your API keys:

```env
# Ordered LLM fallback providers
LLM_PROVIDERS=groq,gemini

# Groq API (OpenAI-compatible endpoint)
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=openai/gpt-oss-120b
GROQ_MODEL_FALLBACKS=openai/gpt-oss-20b

# Google Gemini API (optional fallback provider)
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
GEMINI_MODEL_FALLBACKS=gemini-2.5-flash,gemini-2.0-flash

# Tavily Web Search API Key (Required for live web discovery)
TAVILY_API_KEY=your_tavily_api_key_here

# Optional GitHub Token (improves rate limits for GitHub API)
GITHUB_TOKEN=

# Concurrency & Retries
VERIFIER_CONCURRENCY=4
MAX_LLM_RETRIES=3
MAX_RESEARCH_ROUNDS=2
REQUEST_TIMEOUT=8.0
```

> **Note on Offline / Testing Mode**: When `GEMINI_API_KEY` or `TAVILY_API_KEY` are not set, the system automatically activates its built-in rule-based heuristic extraction, real verified scholarship knowledge base, and mock verification fallbacks, allowing full end-to-end testing without external API failures.

---

## 💻 Running the CLI

Run the full autonomous pipeline with the provided sample files or your own documents:

```bash
python run_cli.py \
  --resume samples/sample_resume.pdf \
  --marksheet samples/sample_marksheet.pdf \
  --github torvalds \
  --pick 1
```

### CLI Arguments:
- `--resume`: Path to the student's resume PDF.
- `--marksheet`: Path to the student's marksheet / transcript (`.pdf` or `.txt`).
- `--github`: Public GitHub username (optional).
- `--pick`: Rank index (1 to 5) of the opportunity for which to draft the SOP (default: 1).
- `--output`: Filepath to save the complete JSON results (default: `output/result.json`).

---

## 🌐 Running the FastAPI Server

Start the backend API server:

```bash
python -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

### Endpoints:
1. `GET /api/health`: Health status and configuration check.
2. `GET /api/events`: Real-time Server-Sent Events (SSE) stream for frontend UI integrations.
3. `POST /api/find-scholarships`: Accepts multipart form uploads (`resume`, `marksheet`, `github_username`, `pick`) and returns full JSON analysis.

---

## 🧪 Testing

Run the automated test suite:

```bash
pytest tests/ -v
```

The test suite validates:
- Resume & marksheet PDF text extraction with `pypdf`.
- Plain text transcript parsing.
- EventBus event broadcasting and streaming queue.
- ResearchAgent query generation and candidate deduplication.
- VerifierAgent bounded concurrency and page fetching fallback.
- RankerAgent deterministic top-5 ranking and scoring bounds.
- WriterAgent fact grounding and draft disclaimer insertion.
- Full end-to-end pipeline execution and output formatting.
- FastAPI health check endpoints.

---

## 🛡️ Safety & Ethics Principles

- **No Hallucinated Credentials**: The system only grounds SOP drafts in accomplishments explicitly extracted from the student's documents.
- **Draft Disclaimers**: All generated application materials are explicitly watermarked and disclaimed for human review.
- **No Direct Submissions**: The software drafts and recommends opportunities; it never automatically submits university or scholarship applications.
