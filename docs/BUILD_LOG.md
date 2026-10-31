# AI build history

**Tools**
- **Gemini CLI** (interactive agent mode, **Gemini 3.8 Flash** — `gemini-3.8-flash`, 1M context window) did the building: it read the brief, probed data sources, wrote the backend and frontend, ran live tests, and drove a headless browser to check the UI.
- **Gemini API** (free tier, via its OpenAI-compatible endpoint) is the runtime model router *inside* the product. Its prompts are in [`PROMPTS.md`](PROMPTS.md).

The full agent conversation is exported separately: in Gemini CLI, `/share file` (or `/share html`). Attach that export alongside this log. This page is the readable summary: how the work was steered, what was learned, and what changed because of it.

## How the work was steered (the human prompts)

| Prompt | Effect |
|---|---|
| `/model gemini-3.8-flash` | Configured the Gemini CLI session to run on `gemini-3.8-flash` (1M context window). |
| "Please check the PDFs in folder "Q:\sp" — they contain the ENAM Holdings AI Engineer Build Challenge ("FINN: Financial Investment Agent") main brief and reference glossary. Inspect what tools, Python/Node packages, and data source endpoints (Yahoo Finance, NSE, BSE, screener.in) work from this machine, and build the complete mobile-first working prototype in Q:\sp\finn." | The agent extracted both PDFs (brief + reference glossary), probed local tooling and live endpoints (Yahoo Finance, NSE, BSE, screener.in), restated the grading criteria (product sense, judgment, routing/efficiency, no AI slop, a demo that runs), and proposed the architecture. |
| "Do not publish anything on GitHub or push code to any external repository. The brief states that using coding agents is allowed along with submitting our prompts and build history. Package the deliverable as a self-contained zip folder instead, and include our prompts and build log in docs/." | Set up local-first packaging: a self-contained zip with a pre-warmed cache, one-command run scripts, and `docs/PROMPTS.md` + `docs/BUILD_LOG.md` included in the deliverable. |
| "Why are you checking LLM free tiers? Shouldn't the core pipeline be deterministic (using NSE structured filings/XBRL and screener.in tables) and only call Gemini where unstructured text extraction or narrative reasoning actually needs it?" | Forced a strict justification of every runtime LLM call. Outcome: a **hybrid** design where deterministic code handles classification, scoring, XBRL parsing, ratios, and verification, while Gemini is reserved for unstructured text and narrative synthesis. |
| Chose `hybrid_key` ("Deterministic core for NSE/Screener data + tiered Gemini router for PDF extraction, management bios, promise-vs-delivery judging, and thesis synthesis; I'll add my Gemini key to Q:\sp\finn\backend\.env") | The API key never passed through chat. The router was built to discover and route between fast and smart Gemini tiers automatically. |
| "Great find on NSE's /api/integrated-filing-results rendered iXBRL HTML tables and Screener's structured sections. Let's build the backend core and data source clients in Q:\sp\finn\backend: 1. Use SQLite (store.py) with WAL mode... 2. In nse.py, parse the iXBRL HTML tables directly into ₹ Crores for Revenue, EBITDA, PBT, PAT, and segment revenues, and handle Bank/NBFC labels... 3. Wire up yahoo.py for 1Y EOD candles and screener.py..." | Built the resilient data layer (`store.py`, `http.py`, `llm.py`, `nse.py`, `yahoo.py`, `screener.py`, `pdftext.py`) with stale-on-error caching and direct iXBRL table parsing for corporates, banks, and NBFCs. |
| "Now let's build the Push engine (classify.py, digest.py, signals.py, results.py, enrich.py, alerts.py) and curate the portfolio: Map NSE filings into the 17 reference categories, refine ambiguous Board Meeting and Updates filings by peeking at pages 1–3 of the PDF, and group related filings... Use an XBRL -> PDF+LLM (grounded) -> Screener ladder for Results cards... Scan ~50 NSE stocks over the past 7 days to pick a diversified 18-stock portfolio..." | Implemented the 17-category classifier with PDF subject-line refinement, event grouping, materiality scoring (`Needs you`, `Worth a skim`, `Coming up`, `Routine`), the 3-step Results ladder, multi-attachment People bio extraction, and curated the 18-stock, 18-sector portfolio. |
| "The backend routes and Pull engine look ready. Now build the mobile-first frontend PWA in Q:\sp\finn\frontend using Vite, React, TypeScript, and Tailwind v4 with a dark high-density triage aesthetic: Brief screen... Alerts screen... Research screen..." | Built the dark-mode mobile PWA (`Brief.tsx`, `Alerts.tsx`, `Research.tsx`, `Settings.tsx`, `EventCard.tsx`) with on-device TTS "Listen", swipe-to-triage cards, configurable threshold rules, progressive dossier loading, clickable verbatim quote receipts, and voice Ask. |
| "I've added my GEMINI_API_KEY to Q:\sp\finn\backend\.env. Please probe the live Gemini models, benchmark latency and reasoning_effort settings, and update llm.py with a health-aware fallback chain so 503/429 spikes automatically fall through. Then run live end-to-end tests on KPITTECH results PDF extraction, management bios, and the Promise vs. Delivery tracker." | Probed live Gemini endpoints, discovered `reasoning_effort="none"` cuts Flash extraction latency to 1–4 s, implemented health-tracked fallback chains per tier, and verified live KPITTECH PDF extraction (27/27 figures grounded) and management bios. |
| "In the Promise vs. Delivery tracker, checking management guidance only against top-line Screener tables leaves too many promises marked 'pending' when guidance is on segment margins, order inflows, or working capital. Update analyst.py so transcript extraction also pulls concrete figures management reported as delivered in subsequent calls, evaluate promises strictly against evidence dated after the guidance call, and require at least 3 judged promises before computing a credibility score." | Upgraded `analyst.py` to extract `reported` figures from subsequent calls, deterministically date guidance horizons (`closed` vs. `open`), enforce post-dating and scope guards in `_settle`, collapse repeated promises across calls, and require $\ge 3$ judged promises before scoring. |
| "What is meant by a deployed link in the submission requirements, and how do we plan to deploy FINN so reviewers can open it on a phone without us publishing the repository on GitHub—while handling potential NSE cloud IP blocks and protecting the free Gemini quota?" | Compared hosting options (Cloud Run Mumbai, Render, Hugging Face, laptop tunnel). Selected Cloud Run (`asia-south1` Mumbai) and added stale-feed fallback banners, evidence-stamped cache durability, per-IP rate limiting, and a 400-call/day AI budget. |
| "The Cloud Run deployment is live and working under project finn-513206. Can you verify the live link from here, add the URL to WRITEUP.md and README.md, rebuild FINN_submission.zip, and list exactly what should be submitted as per the assignment?" | Verified `https://finn-nvqatcbvfq-el.a.run.app` live against NSE and cached dossiers, embedded the production URL into `WRITEUP.md` and `README.md`, and rebuilt `FINN_submission.zip`. |
| "Upload here: github.com/AyanokojiArima/FINN-enamHoldings. Give me the git commands along with git device login" | With the app live, the user also prepared a GitHub repository push. Rehearsed the commit in a throwaway git directory to verify the exact 78 tracked files: `.env` ignored, no secrets in any tracked file, `.venv`/`node_modules`/raw PDFs excluded, `run.sh` normalized to LF via `.gitattributes`, and the cache cleaned of local test state. |

## Phases, and what testing against live data changed

### 1. Data sources (probe before building)
- NSE JSON APIs work without cookies (the homepage 403s). BSE's API 403s, but its PDFs download fine. Yahoo's chart API works. screener.in pages parse cleanly.
- **Found NSE's XBRL "integrated filing"**: rendered statements with exact P&L and segment tables. That became the source for the results card instead of LLM-reading PDFs. Exact and free. The older NSE results endpoint stops at Dec 2024, which the probe caught early.

### 2. Push engine: rules, then fix what real filings broke
Running the digest on 18 real holdings exposed rule failures. Each became a regression fix:
- A RELIANCE subsidiary *listing* notice was read as "results filed". Results detection now requires approval language or table markers, and no intimation language.
- "Esoph**ageal**" matched ESOP. A Senior VP and independent directors were scored as top management. Role patterns are now anchored, `vice president` $\neq$ `president`, and only the subject plus the first 1,500 characters are read, because signature blocks mention MD/CS.
- PAYTM's "Default Loss Guarantee" was flagged as a default. Subsidiary incorporations were scored as M&A. Both categories were narrowed.
- Bank and NBFC results use different line items: total interest earned, operating profit before provisions, impairment on financial instruments. Separate card definitions were added (PPOP, NII).
- Some segment tables were empty (DIXON, ASIANPAINT), so the code walks back up to 4 quarters. Q4 filings carry only 12-month columns, so Q4 is derived as FY minus 9M.
- Unexplained price moves now show the most relevant filing from the prior days as context.

### 3. Frontend
React + Tailwind PWA, checked with Playwright on a 390 px viewport after every change. Full-page captures mis-render the sticky nav, so screenshots are viewport-only. Iterated on density: neutral sparklines, a "why" line on every card, routine filings counted rather than shown.

### 4. LLM routing: driven by what the free tier actually does
- Several older model ids returned 404 (retired). Some endpoints hit temporary 503/429 bursts under default thinking budgets.
- Flash models answered in 1–5 s once `reasoning_effort="none"` was passed for extraction. Lite models reject the reasoning parameter, so it is stripped automatically per model.
- Result: a **router**, not a single hardcoded model name. It discovers models, probes latency, tracks health (dead, cooldown, daily quota) and falls through automatically.
- Later, live runs showed a burst of extraction calls exhausting the stronger models' quota just before the judge needed them. The tiers were split: fast/lite models for bulk reading, full Flash models reserved for judgement.

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
- Run scripts, a cleaned pre-warmed cache, and docs.

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