# FINN - covering note

**Try it:** https://finn-nvqatcbvfq-el.a.run.app (best on a phone; "Add to Home Screen" installs it; the first open after a quiet spell takes ~10 seconds to wake the server). **Code:** https://github.com/AyanokojiArima/FINN-enamHoldings

**What I built.** FINN is a mobile-first web app (installable PWA) for a PM covering Indian equities. It has two halves:

- **Push:** a brief that folds the day's exchange filings for the portfolio into a few stories. Stories are ranked by what the filing is, how big the position is and how the stock reacted, and each says why it's there.
- **Pull:** an on-demand dossier for any NSE stock, built from primary sources only.

The part I care most about is the **promise tracker**. FINN reads the last six earnings-call transcripts, pulls out management's measurable guidance as verified quotes, and dates every horizon. It then checks each promise that has come due against what management later reported and what was filed. On L&T, for example: "15% revenue growth for FY26", said on four consecutive calls, came in at 11.8%, a miss. Order inflow and working-capital guidance were delivered. Every verdict opens a receipt: the original quote with a page link, plus the later evidence.

**Key decisions and trade-offs**
- **Code first, model second.** Classification of 17 filing types, scoring, XBRL parsing, ratios, horizon dating and verdict validation are deterministic: free, instant, testable. The LLM only reads prose.
- **Verdicts are checked by code, not trusted.** Code enforces that evidence must post-date the promise and that a number must be cited. Segment guidance can't be "proven" with company totals, and percentage targets are compared numerically. Guidance repeated on four calls is one promise, not four. The credibility score and pattern are computed, and the prose summary is written *from* the settled ledger, so it can't contradict it. Without these checks, a capable model still produced confident, wrong verdicts. The fixes are recorded in the build log.
- **Routing for cost and quality.**
  - Fast tier: lite models do the bulk reading.
  - Smart tier: stronger models judge only small, pre-computed fact packs.
  - The router probes models, tracks rate limits and outages, and falls through to the next model on errors or malformed output.
  - Every answer is cached by prompt hash.

  A full first-time dossier costs ~35k tokens; repeat views are free. Everything ran on the Gemini free tier.
- **Results from the exchange, not the PDF.** The results card reads NSE's XBRL filing (exact, free). The PDF+LLM path is only a fallback for the hours before XBRL lands, and every number must be found in the PDF text.
- **Works without AI.** With no key, the brief, alerts, results tables and dossier numbers all work, and cached AI analyses are replayed with their date. A demo that doesn't run is the worst outcome, so the code ships with a pre-warmed cache.
- **Hosted link built to keep working.** It runs as one small Cloud Run instance in Mumbai with the cache snapshot baked in.
  - Analyses stay valid until new evidence (a new call or quarter) arrives, so a fresh instance doesn't redo AI work.
  - A per-IP rate limit and a daily AI-call budget protect the free quota.
  - If the exchange blocks the cloud server, the brief shows the newest filings it holds and says so.
- **Interaction model:**
  - A brief you can **listen** to.
  - Swipe-to-triage cards.
  - Threshold alerts with device notifications.
  - "Ask the filings" by voice or text, with tappable citations.
  - Receipts everywhere instead of chat transcripts.

**What I deliberately left out**
- Real-time prices and server push (prices are EOD; alerts re-evaluate every 15 minutes).
- Accounts and multi-user support.
- News and broker research. That's out of scope for the pull side by design; push-side LinkedIn enrichment is a search link, not scraping.
- Annual-report deep reading (the documents are linked and indexed; transcripts gave a higher signal per token).
- A native app: a PWA was the fastest way to put it on a phone.

**Where I'd take it next**
1. Annual-report reading for the tracker: capex plans, segment targets, related-party changes.
2. A "what changed since last quarter" diff of management language.
3. Real-time price and filing streams with push notifications.
4. A small labelled set of promise verdicts to measure judge accuracy, and to tune routing on evidence rather than intuition.
5. Peer views: the same promise tracker across a sector.

*Note on data:* sources are NSE APIs and archives, BSE-hosted PDFs, Yahoo EOD prices, and screener.in as an index of company documents and standardised filed financials. Nothing here is investment advice.
