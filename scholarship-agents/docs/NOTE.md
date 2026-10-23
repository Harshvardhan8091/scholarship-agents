# Project note

The most interesting design choice is treating `unverified` as a legitimate result rather than filling gaps with a plausible guess. The verifier keeps a funding, deadline, eligibility, degree, or application value only when its evidence snippet appears in the fetched page; dates must parse to real calendar dates. Search snippets and curated fallback data do not become proof by themselves.

The biggest technical challenge is making a live LLM pipeline resilient to quota and network failures. The code keeps a configurable Groq/Gemini provider and model fallback chain, caches responses and fetched pages, limits verifier concurrency, and bounds each candidate to 40 seconds. Recovery and timeout events are visible to the terminal and SSE clients, and failed candidates continue as unverified.

In this run, no provider keys were available in the process environment, and `.env` was intentionally not read. The startup check therefore reported offline fallback mode; the three bundled profiles completed through the offline path. No live LLM response or live verification count is claimed.
