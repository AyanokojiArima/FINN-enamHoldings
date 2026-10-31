# AI build history

**Tools**
- **GitHub Copilot CLI** (agent mode, Claude model) did the building: it read the brief, probed data sources, wrote the backend and frontend, ran live tests, and drove a headless browser to check the UI.
- **Gemini** (free tier, via its OpenAI-compatible API) is the model *inside* the product. Its prompts are in [`PROMPTS.md`](PROMPTS.md).

The full agent conversation is exported separately: in Copilot CLI, `/share file` (or `/share html`). Attach that export alongside this log. This page is the readable summary: how the work was steered, what was learned, and what changed because of it.

## How the work was steered (the human prompts)

| Prompt | Effect |
|---|---|
| "Please check the PDFs in folder Q:\sp. Do as needed." | The agent extracted both PDFs (brief + reference glossary), restated the grading criteria (product sense, judgment, routing/efficiency, no AI slop, a demo that runs) and proposed a plan. |
| "Do not publish anything in GitHub… use of agents is allowed, along with prompts. Create a zip folder instead." | Local-only delivery: a self-contained zip with a pre-warmed cache and one-command run scripts. Agent history and prompts are part of the deliverable. |
| "Why are you checking LLM free tiers?" | Forced a justification of every LLM use. Outcome: a **hybrid** design where deterministic code does everything it can and the LLM only reads prose. That became the product's core principle. |
| Chose "hybrid with a free Gemini key"; pasted the key into `backend/.env` locally | The key never went through chat. The router was built to discover and route between whatever free models the key can reach. |
| "What is meant by deployed link? How do we plan to deploy?" | The agent explained that a link lets reviewers open the app on a phone with nothing to install, and compared hosts: Cloud Run, Render, Hugging Face, a laptop tunnel. It recommended Cloud Run in Mumbai (Indian location, private code, free tier); the user chose it. That led to the cloud-readiness work in phase 7. |
| "Upload here: github.com/AyanokojiArima/FINN-enamHoldings. Give me the git commands along with git device login" | With the app live, the user reversed the earlier no-GitHub decision. Before handing over device-login and push commands for the user to run, the agent rehearsed the commit in a throwaway git directory and checked what it would hold: the same 78 files as the zip. The key file is ignored and the key appears in no tracked file; the venv, node_modules and downloaded PDFs are excluded; and the cache is cleaned of local test state, as in the zip. It also fixed `run.sh` line endings for macOS/Linux, added `.gitattributes`, and set the commit identity to a GitHub no-reply address. |

## Phases, and what testing against live data changed

### 1. Data sources (probe before building)
- NSE JSON APIs work without cookies (the homepage 403s). BSE's API 403s, but its PDFs download fine. Yahoo's chart API works. screener.in pages parse cleanly.
- **Found NSE's XBRL "integrated filing"**: rendered statements with exact P&L and segment tables. That became the source for the results card instead of LLM-reading PDFs. Exact and free. The older NSE results endpoint stops at Dec 2024, which the probe caught early.

### 2. Push engine: rules, then fix what real filings broke
Running the digest on 18 real holdings exposed rule failures. Each became a regression fix:
- A RELIANCE subsidiary *listing* notice was read as "results filed". Results detection now requires approval language or table markers, and no intimation language.
- "Esoph**ageal**" matched ESOP. A Senior VP and independent directors were scored as top management. Role patterns are now anchored, `vice president` ≠ `president`, and only the subject plus the first 1,500 characters are read, because signature blocks mention MD/CS.
- PAYTM's "Default Loss Guarantee" was flagged as a default. Subsidiary incorporations were scored as M&A. Both categories were narrowed.
- Bank and NBFC results use different line items: total interest earned, operating profit before provisions, impairment on financial instruments. Separate card definitions were added (PPOP, NII).
- Some segment tables were empty (DIXON, ASIANPAINT), so the code walks back up to 4 quarters. Q4 filings carry only 12-month columns, so Q4 is derived as FY minus 9M.
- Unexplained price moves now show the most relevant filing from the prior days as context.

### 3. Frontend
React + Tailwind PWA, checked with Playwright on a 390 px viewport after every change. Full-page captures mis-render the sticky nav, so screenshots are viewport-only. Iterated on density: neutral sparklines, a "why" line on every card, routine filings counted rather than shown.

### 4. LLM routing: driven by what the free tier actually does
- Several older model ids returned 404 (retired). The newest ones were often overloaded (503). Some timed out.
- Fast models answered in 1-5 s once reasoning was switched off. Lite models reject the reasoning parameter, so it is stripped per model.
- Result: a **router**, not a model name. It discovers models, probes latency, tracks health (dead, cooldown, daily quota) and falls through.
- Later, live runs showed a burst of extraction calls exhausting the stronger models' quota just before the judge needed them. The tiers were split: lite models for bulk reading, full models reserved for judgement.

### 5. Promise tracker: the hardest part, and the most iterated

| Symptom in live runs | Root cause | Fix |
|---|---|---|
| L&T: every promise "unverifiable" | The model wrapped the JSON object in an array | Unwrap one-element arrays. Validate a required key per call; on malformed output, try the next model |
| DIXON: everything "pending", even FY26 capex | The model's own sense of today's date (pre-2026) | Code dates each horizon ("FY26", "H2 FY27", "Mar-26", "next 2-3 years") and tells the judge which promises have closed. "Today" is stated explicitly |
| Segment margin guidance "on track" against the company-wide margin | No notion of scope | Extraction tags scope. Code rejects company-level evidence for segment promises |
| A verdict cited FY25 numbers for a promise made in May 2025 | Evidence predating the promise | Code parses cited periods and report ids, and the evidence must post-date the call |
| "On track" at 7.2% against a ~10% target | Lenient judge | Numeric check for higher-is-better percentage targets: below target means behind; below 85% means missed |
| One revenue miss counted four times | The same guidance repeated on four calls | Collapse repeats into one promise with "said 4×" |
| Summary praised "reliability" next to a 40% score | Prose and verdicts generated together | The pattern is computed from the score. The summary is a second small call written from the settled ledger |
| Capex 2× guidance judged "delivered" | Ambiguous direction | Explicit, documented rule: within ~15% of plan is delivered, otherwise missed (over or under) |

Also added: the actuals table gained year-to-date rows (so "on track" is measurable mid-year) plus capex and borrowings columns. The window went from 4 to 6 calls so that guidance for the last four reported quarters is in view from the call where it was first given.

### 6. Packaging
- Background pre-warming of the brief's cards and the research picks.
- Labelled replay of cached AI results when no key is configured.
- Run scripts, a cleaned pre-warmed cache, and docs. Nothing was published anywhere.

### 7. Making a public link survive real conditions
Thinking through how Cloud Run actually behaves surfaced four problems before deployment, each fixed and tested locally:

| Risk on a hosted link | Fix |
|---|---|
| NSE often blocks cloud servers. Worse, the HTTP layer's silent stale-cache fallback would have shown hours-old filings as if live | Announcements now fail loudly. The brief falls back to the newest filings held, shifts its window and says so ("24 hours to 28 Oct, 11:20 pm" plus an amber note). Tested with NSE forced offline: the same 16 events, clearly labelled |
| Instances sleep and restart from the image, and analyses expired after 12 h, so every cold start would have re-run the judge (about 30 s, plus quota) | Analyses stay valid until new evidence (a newer call or a newly reported quarter). Tested: six picks aged three days were served in about 1 s each with zero model calls |
| A public link can drain the free AI quota | A daily cap on fresh model calls; over it, the last good analysis is shown with its date. Plus a per-IP limit on expensive requests (returns 429 with a friendly message) |
| A rule-based card from a momentary AI failure was cached under the AI key forever | Cards are cached under the method that produced them, so the AI is retried next time |

Deployment itself is one script (`deploy-cloudrun.ps1`). The key goes into Secret Manager rather than the image. A cleaned cache snapshot is baked in, and the service is capped at one small instance. The container layout was rehearsed locally with the exact production flags before handing over.

## Working style
- **Probe, then build.** Every source and model was hit with a small live request before code depended on it.
- **Test on real data, not fixtures.** Most fixes above came from reading actual outputs for 18 holdings and 6 research names and asking "would a PM trust this?"
- **Let code police the model.** Every LLM output that reaches the screen passes a deterministic check: quote verification, id validation, date and scope rules, numeric comparisons.
