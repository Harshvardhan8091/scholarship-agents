# Five-minute screen-recording script

## 0:00–0:35 — Intro

Show the Scholarship Research Desk. Explain that it turns a resume and marksheet into a shortlist of research leads and a reviewable statement-of-purpose draft. Point out the LIVE/OFFLINE badge; state which mode this recording actually uses.

## 0:35–1:10 — Architecture

Show `docs/architecture.md` or the README diagram. Introduce Profile, Research, Verifier, Ranker, and Writer. Explain that Research may use Tavily, while the verifier checks fetched official-page text and stores supporting snippets.

## 1:10–2:15 — Run a profile and narrate recovery

Choose a bundled sample resume and marksheet, then submit. Narrate the events as they appear. If a provider or page fails, point to the actual recovery event and explain that an affected candidate may remain unverified while the rest of the pipeline continues. If this run is offline, say so explicitly; do not describe it as a live LLM run.

## 2:15–3:15 — Results and evidence

Review the top opportunities, official-page links, discovery-source and verification badges, country, fit reason, and deadline/funding/eligibility fields. Expand the candidates table and show any excluded entry and its recorded reason. Explain that grey `unverified` values are intentionally not guesses.

## 3:15–4:05 — Statement-of-purpose draft

Open the SOP panel. Point out “DRAFT - nothing has been submitted,” then demonstrate Copy or Download. Remind viewers that the draft requires personal review and is never sent by the application.

## 4:05–4:40 — Second profile

Run a different bundled profile (mechanical engineering with a lower CGPA, or commerce/business). Compare its field, country preferences, and shortlist with the first. Explain any overlap as broad or curated fallback results, not proof that all candidates suit every student.

## 4:40–5:00 — Closing

Summarize the evidence-first behavior, current run mode, and limits: credentials, rate limits, page availability, and changing deadlines. Ask viewers to open official pages and confirm eligibility before applying.
