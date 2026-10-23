# Architecture

```mermaid
flowchart TD
    R[Resume PDF] --> API[FastAPI or CLI]
    M[Marksheet PDF or TXT] --> API
    G[Optional GitHub username] --> API
    API --> P[ProfileAgent]
    P -->|StudentProfile| S[ResearchAgent]
    S <-->|Queries and results| T[Tavily Search]
    S -->|Unique candidate URLs| V[VerifierAgent]
    V <-->|Official page fetch and evidence| WEB[Official scholarship pages]
    V -->|Verified or explicitly unverified fields| Q{Enough eligible candidates or research limit reached?}
    Q -->|No| S
    Q -->|Yes| K[RankerAgent]
    K -->|At most five distinct recommendations| W[WriterAgent]
    W --> D[Statement-of-purpose draft]
    P -. LLM calls .-> L[LLMClient: configured Groq/Gemini provider and model fallbacks]
    V -. LLM calls .-> L
    K -. LLM calls .-> L
    W -. LLM calls .-> L
    L --> C[(LLM response cache)]
    V --> PC[(Fetched-page cache)]
    P -. status events .-> E[EventBus]
    S -. status events .-> E
    V -. status events .-> E
    K -. status events .-> E
    W -. status events .-> E
    E --> SSE[GET /api/events SSE]
    E --> CLI[Rich CLI output]
    API --> UI[static/index.html]
    UI -->|Multipart upload and replay| API
```

The graph is implemented in `graph.py`; FastAPI and CLI entry points are `api.py` and `run_cli.py`. Supporting modules stay at the repository root to preserve their existing import paths.
