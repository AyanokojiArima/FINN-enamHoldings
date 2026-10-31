# FINN - Financial Investment Agent

**Live demo: https://finn-nvqatcbvfq-el.a.run.app** (open it on a phone).

A mobile-first prototype for a portfolio manager covering Indian equities. Two jobs:

- **Push - "what needs me?"** Everything the exchanges published about your holdings, folded into a short brief: what matters first, why it's there, and the numbers already pulled out.
- **Pull - "should I believe this company?"** An on-demand dossier for any NSE stock, built only from primary sources. At its centre is a **promise tracker**: what management guided on earnings calls vs. what they then delivered, with a receipt for every verdict.

Screenshots are in [`docs/screenshots`](docs/screenshots). The covering note is in [`docs/WRITEUP.md`](docs/WRITEUP.md), the AI build history in [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md), and every runtime prompt in [`docs/PROMPTS.md`](docs/PROMPTS.md).

---

## Run it (about 2 minutes)

Needs **Python 3.10+**. The built frontend is included, so Node isn't needed.

```bash
git clone https://github.com/AyanokojiArima/FINN-enamHoldings.git
cd FINN-enamHoldings
```
```powershell
# Windows
.\run.ps1
```
```bash
# macOS / Linux
chmod +x run.sh && ./run.sh
```

Then open **http://localhost:8000**. The script also prints a LAN address (e.g. `http://192.168.1.20:8000`) to open on a phone on the same Wi-Fi. "Add to Home Screen" installs it as an app (PWA).

**AI is optional.** Put a free Gemini key in `backend/.env` (`GEMINI_API_KEY=...`, from https://aistudio.google.com/apikey) to switch on transcript reading, verdicts, bios and Ask.
- Without a key, FINN runs in **rules mode**: digest, scoring, results tables, alerts and dossier numbers all still work.
- AI analyses already in the shipped cache are replayed, labelled with their date: the research picks LT, PIDILITIND, POLYCAB, DIXON, ASIANPAINT, TRENT.
- Any OpenAI-compatible endpoint works instead of Gemini (Groq, OpenRouter, local Ollama); see `backend/.env.example`.

`backend/data/finn.db` is a pre-warmed cache, so the first screen opens instantly. Set `FINN_OFFLINE=1` to demo purely from it.

Frontend development: `cd frontend && npm install && npm run dev` (proxies `/api` to port 8000).

## Put it online (public link on Google Cloud Run, Mumbai)

FINN ships as one container (`Dockerfile`): the API and the built app. `deploy-cloudrun.ps1` deploys it straight from a local copy (source upload; no CI to set up).

1. Install the Google Cloud CLI (`winget install Google.CloudSDK`), open a new terminal, and run `gcloud auth login`.
2. Create a new project and link a billing account to it. Keep it separate from the project that owns your Gemini key: enabling billing on that one moves the key from the free tier to paid usage. Light demo traffic stays inside Cloud Run's free tier.
3. Run `powershell -ExecutionPolicy Bypass -File .\deploy-cloudrun.ps1 -Project <project-id>`. It takes 3-6 minutes and prints the URL.

The script:
- enables the APIs;
- stores the key from `backend/.env` in Secret Manager (never printed, never in the image);
- snapshots the local cache into the image (`deploy/make_seed.py`), so the research picks open instantly on a fresh instance;
- deploys one small instance (1 vCPU, 1 GiB, max 1 instance).

Safeguards on the public link:
- a per-IP rate limit on expensive requests (`FINN_RATE_LIMIT`, 150 per 10 minutes);
- a daily cap on fresh AI calls (`LLM_DAILY_BUDGET`, 400). Over the cap, FINN serves the last good analysis, dated.

If NSE blocks the cloud server, the brief falls back to the newest filings in the snapshot and says so with an amber note, rather than showing an empty screen.

---

## What's in it

### Push
| | |
|---|---|
| **Brief** | 24h / 3D / 7D / custom window over the portfolio's NSE filings and EOD prices. A results day's 5-8 filings (outcome, press release, deck, call invite, newspaper ad) fold into one story. Each story is scored as *category weight × position size × how the stock reacted × your alert preferences*, then tiered into **Needs you / Worth a skim / Routine** (routine is counted, not shown). Every card says *why* it's there. |
| **Cards do the reading** | **Results:** Revenue / EBITDA / PBT / PAT with QoQ and YoY from the exchange XBRL filing. Bank and NBFC formats are handled. A PDF fallback via LLM, with a grounding check, covers the hours before XBRL lands. **Management change:** who, the role, prior roles and disclosed relationships from the filing, plus a LinkedIn search link. **Other filings:** one specific line. **Price moves with no filing behind them** are flagged; those are the ones to chase. |
| **Listen** | The brief read aloud as a short spoken summary (on-device TTS), for the drive in. |
| **Alerts** | Your thresholds: ±% move, volume × 20-day average, 52-week high/low, filing categories. A feed of what crossed them, and web notifications on the device. |
| **Triage** | Swipe a card to mark it done or pin it. |

### Pull (any NSE ticker; nothing is hard-wired to the demo set)
| | |
|---|---|
| **Promised vs delivered** | Reads the last six earnings-call transcripts and extracts management's measurable guidance as verbatim quotes, each verified against the transcript. Code dates each horizon ("FY26", "H2 FY27", "next 2-3 years") and splits promises into *came due* vs *in progress*. A judge model rules each promise against what management reported on *later* calls and the filed numbers. Code then re-checks every verdict. The output is a credibility score, the misses first, "said 4×" for reiterated guidance, and a receipt with the original quote, page link and later evidence. |
| **FINN's take** | The debate in one line, bull and bear, narrative vs numbers. Every point cites a numbered fact (computed from filings) or a verbatim quote; uncited points are dropped. |
| **Numbers** | Snapshot, segment mix (exchange XBRL), revenue/margin/PAT trajectory, balance sheet and working-capital days, cash conversion, shareholding. Rule-generated signals sit on top ("receivable days jumped", "a meaningful share of profit is not showing up as cash"). |
| **Ask the filings** | Questions in text or voice, answered only from the company's transcripts, computed facts and the promise ledger, with citations you can tap. |

---

## Architecture

```
 Sources (all public, no auth)                 Backend (FastAPI, Python)                      Frontend (React PWA)
 ─────────────────────────────                 ─────────────────────────                      ────────────────────
 NSE announcements API ───────┐                engine/classify   17 categories, rules         Brief   (push)
 NSE XBRL integrated filings ─┤                engine/digest     fold, score, tier, explain   Alerts  (push)
 Filing PDFs (NSE / BSE) ─────┼──► sources/ ──► engine/results    XBRL -> PDF+LLM -> table     Research (pull)
 Yahoo EOD prices ────────────┤                engine/enrich     bios, summaries              Settings
 screener.in (doc index,      │                engine/research   deterministic dossier
   standardised financials) ──┘                engine/analyst    promises, thesis, ask  ◄── llm.py router
                                               store.py          SQLite cache + state
```

- **Deterministic first.** Classification, scoring, XBRL parsing, ratios, signals, horizon dating and verdict validation are plain code: free, instant, testable. The LLM only does what code can't, which is reading prose.
- **Model routing** (`backend/app/llm.py`):
  - **Fast tier:** lite models for bulk reading (transcripts, filings, results PDFs).
  - **Smart tier:** full flash models, only for judgement over small fact packs (promise judge, thesis).
  - The router discovers available models and probes latency hourly. It tracks health (404 = dead, 429 = cooldown, daily-quota detection, 5xx = back off) and falls through on errors, empty answers, or JSON missing the required key.
  - The smart tier briefly waits for a rate-limited full model rather than silently downgrading.
- **Caching everywhere.** Every LLM answer is cached by prompt hash. Filings and PDF text are cached forever, digests for 5 min, dossiers for 3 h, verdicts for 12 h. A background refresher keeps the 24 h brief and its cards warm, plus the research picks. The HTTP layer paces each host and serves stale data on error.
- **Cost.** Measured averages: transcript extraction ~4.3k tokens per call, judge ~5.8k, thesis ~1.8k, Ask ~1k. A full first-time dossier is ~35k tokens; repeat views cost nothing. Everything was built and run on the free tier.

### Grounding rules (pull side)
- Primary sources only: exchange filings and XBRL, earnings-call transcripts, annual reports. screener.in is used as an index of those documents and for its standardised compilation of the filed financial statements; no news, broker notes or model background knowledge.
- Quotes are verified against the source text; failures are dropped and counted.
- Promise verdicts are re-checked by code:
  - the verdict must fit whether the horizon has ended;
  - a number must be cited;
  - the evidence must post-date the promise;
  - segment guidance needs segment evidence;
  - percentage targets are compared numerically;
  - capex counts as delivered within ~15% of plan.

  Reiterated guidance is collapsed into one promise.

---

## Layout

```
backend/
  app/main.py            API + static frontend + background refresher
  app/llm.py             model router, health, caching, metering
  app/store.py           SQLite cache/state (one file)
  app/sources/           nse.py, yahoo.py, screener.py, pdftext.py
  app/engine/            classify, digest, signals, results, enrich, alerts, research, analyst
  data/finn.db           pre-warmed cache (downloaded PDFs are not shipped; their text is in the cache)
frontend/src/            App.tsx, screens/{Brief,Alerts,Research,Settings}.tsx, components/, lib/
docs/                    WRITEUP.md, BUILD_LOG.md, PROMPTS.md, screenshots/
Dockerfile, deploy-cloudrun.ps1, deploy/make_seed.py, .gcloudignore   container + one-command Cloud Run deploy
```

API: `GET /api/digest?hours=24`, `GET /api/alerts`, `POST /api/enrich`, `GET /api/research/{SYM}`, `.../promises`, `.../thesis`, `POST .../ask`, `GET/PUT /api/portfolio`, `GET/PUT /api/rules`, `GET /api/meta`.

## Limits worth knowing
- Prices are end-of-day (Yahoo). Alerts are evaluated on refresh (15 min) and notify while the app is open; there is no server push yet.
- On Cloud Run the cache lives in the instance, which sleeps when idle. Each fresh start begins from the deploy-time snapshot. Analyses stay valid until a new call or quarter arrives, so only genuinely new work is redone.
- The promise tracker needs published transcripts. Some companies (e.g. Trent) hold no calls.
- NSE's XBRL "integrated filing" starts around Mar 2025; older quarters come from screener's compilation.
- Free-tier rate limits are real. The router handles them, but judge quality varies with the model that answers; the code checks are the safety net.
- Single user, no auth. Not investment advice.
