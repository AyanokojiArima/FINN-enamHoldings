"""Pull: the analyst layer on top of the deterministic dossier.

Grounding discipline (primary sources only):
  * Transcripts come straight from the exchange/company (links listed by screener.in).
  * The LLM only sees excerpts we select + numbers we computed; it must quote verbatim and cite
    page numbers / fact ids. We verify quotes against the transcript and drop anything uncited.
Model routing:
  * fast tier  -> per-transcript extraction (bulk reading, 4 calls)
  * smart tier -> promise-vs-delivery judgement and bull/bear synthesis (small, dense prompts)
  * no key     -> extractive fallbacks (quote mining + rules), clearly labelled.
"""
from __future__ import annotations

import calendar
import logging
import math
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from ..llm import llm
from ..sources import screener
from ..sources.pdftext import document_pages, has_text
from ..store import store
from ..util import clip, fy_quarter_label, pct_change
from .research import dossier
from .results import latest_qe_before

log = logging.getLogger("finn.analyst")

PROMPT_VERSION = "v6"
_FWD = re.compile(r"\b(guid\w*|expect\w*|outlook|target\w*|aim\w*|aspir\w*|plan(?:s|ning)? to|going forward|next (?:year|quarter|fiscal|few)|"
                  r"fy ?'?2\d|fy ?\d\d|h[12]\b|by (?:the )?end of|over the next|we will|we should|should be|would be|likely to|"
                  r"anticipat\w*|estimat\w*|visibility|pipeline|capex|capacity|commission\w*|ramp[- ]?up|double)\b", re.I)
_NUM = re.compile(r"\d+(?:\.\d+)?\s?(?:%|percent|per cent|crore|crores|cr\b|bps|basis points|x\b|billion|million|lakh|mw|gw|units|tonnes|mtpa)", re.I)
_PAST = re.compile(r"\b(we (?:delivered|achieved|reported|grew|posted|clocked)|grew|declined|this quarter|during the quarter|year[- ]on[- ]year)\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def call_quarter(c: dict) -> str:
    d = date.fromisoformat(c["date"]) if c.get("date") else None
    return fy_quarter_label(latest_qe_before(d)) if d else c.get("period", "")


def transcripts(symbol: str, n: int = 4) -> list[dict]:
    s = screener.company(symbol)
    if not s:
        return []
    calls = [c for c in s["concalls"] if c.get("transcript")][: n + 2]
    with ThreadPoolExecutor(4) as ex:  # cold cache: fetch PDFs in parallel; document_pages caches each one
        all_pages = list(ex.map(lambda c: document_pages(c["transcript"]), calls))
    out = []
    for c, pages in zip(calls, all_pages):
        if not has_text(pages):
            continue
        out.append({"id": f"T{len(out) + 1}", "period": c["period"], "date": c.get("date"), "quarter": call_quarter(c),
                    "url": c["transcript"], "pages": pages})
        if len(out) >= n:
            break
    return out


def _paragraphs(t: dict) -> list[dict]:
    paras = []
    for pno, text in enumerate(t["pages"], start=1):
        for chunk in re.split(r"\n\s*\n|\n(?=[A-Z][A-Za-z\.\s]{2,40}:)", text):
            c = re.sub(r"\s+", " ", chunk).strip()
            if len(c) > 60:
                paras.append({"t": t["id"], "page": pno, "text": c})
    return paras


def _candidates(t: dict, budget: int = 11000) -> str:
    scored = []
    for p in _paragraphs(t):
        score = 2 * len(_FWD.findall(p["text"])) + 2 * len(_NUM.findall(p["text"])) + len(_PAST.findall(p["text"]))
        if score >= 3 and not re.match(r"(moderator|operator)\s*:", p["text"], re.I):
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    picked, used = [], 0
    for _, p in scored:
        if used + len(p["text"]) > budget:
            continue
        picked.append(p)
        used += len(p["text"])
    picked.sort(key=lambda p: p["page"])
    return "\n".join(f"[p.{p['page']}] {p['text']}" for p in picked)


def _verify_quote(quote: str, full_norm: str) -> bool:
    q = _norm(quote)
    if len(q) < 12:
        return False
    if q in full_norm:
        return True
    words = q.split()
    # tolerate small transcription differences: require two 6-word shingles to match
    shingles = [" ".join(words[i:i + 6]) for i in range(0, max(1, len(words) - 5), 3)]
    return sum(1 for s in shingles if s in full_norm) >= min(2, len(shingles))


# ----------------------------------------------------------------------------- extraction (fast tier)
SYSTEM_EXTRACT = ("You help an equity analyst read Indian earnings-call transcripts. Use ONLY the excerpts given. "
                  "Only management statements count (not analysts' questions). Quotes must be copied verbatim. Output JSON only.")

PROMPT_EXTRACT = """Company: {name}. Earnings call held {period}, discussing {quarter} results. Excerpts are tagged [p.N] with page numbers.

Return JSON:
{{"guidance": [ up to 6 forward-looking, measurable commitments by management on headline metrics (revenue or volume growth,
   margins, segment targets, capex/capacity, debt), most material first:
   {{"metric": "revenue_growth|margin|profit|capex|volume|debt|capacity|other",
     "scope": "company, unless the target is for one segment / business line / product / JV - then its name",
     "statement": "<=20 words paraphrase", "quote": "verbatim <=40 words", "page": N,
     "horizon": "e.g. Q3 FY27, FY27, H2 FY27, FY28, next 2-3 years",
     "target": "e.g. 15-17% growth, ~12% margin, Rs 1,000 Cr capex, or null"}} ],
 "reported": [ up to 6 concrete results management reports for {quarter} or the year just ended, with numbers
   (segment revenue, volumes, margins, capacity commissioned, order book):
   {{"statement": "<=20 words including the number", "quote": "verbatim <=40 words", "page": N}} ],
 "claims": [ up to 5 statements characterising the business today (demand, pricing, competition, margins, balance sheet):
   {{"claim": "<=20 words", "quote": "verbatim <=40 words", "page": N}} ],
 "tone": "confident|balanced|cautious|defensive", "tone_reason": "<=15 words"}}

Excerpts:
{text}"""


def _extract_one(name: str, t: dict) -> dict:
    full_norm = _norm(" ".join(t["pages"]))
    excerpt = _candidates(t)
    out = {"id": t["id"], "period": t["period"], "quarter": t["quarter"], "url": t["url"], "pages": len(t["pages"]),
           "guidance": [], "claims": [], "reported": [], "tone": None, "toneReason": None, "method": "extractive"}
    res = None
    if llm.enabled and excerpt:
        res = llm.json("fast", SYSTEM_EXTRACT, PROMPT_EXTRACT.format(name=name, period=t["period"], quarter=t["quarter"],
                                                                       text=excerpt), purpose="transcript_extract", max_tokens=4000,
                       expect="guidance")
    if isinstance(res, dict):
        out["method"] = "llm"
        meta = {"call": t["id"], "callQuarter": t["quarter"], "callPeriod": t["period"], "callDate": t.get("date"), "url": t["url"]}
        for i, g in enumerate(res.get("guidance") or []):
            if isinstance(g, dict) and g.get("quote") and _verify_quote(g["quote"], full_norm):
                out["guidance"].append({**g, "id": f"{t['id']}-G{i + 1}", **meta})
        for i, r in enumerate(res.get("reported") or []):
            if isinstance(r, dict) and r.get("quote") and _verify_quote(r["quote"], full_norm):
                out["reported"].append({**r, "id": f"{t['id']}-R{i + 1}", **meta})
        for i, c in enumerate(res.get("claims") or []):
            if isinstance(c, dict) and c.get("quote") and _verify_quote(c["quote"], full_norm):
                out["claims"].append({**c, "id": f"{t['id']}-C{i + 1}", **meta})
        out["tone"], out["toneReason"] = res.get("tone"), res.get("tone_reason")
        total = sum(len(res.get(k) or []) for k in ("guidance", "reported", "claims"))
        out["dropped"] = total - len(out["guidance"]) - len(out["claims"]) - len(out["reported"])
    else:
        # Extractive fallback: highest-signal forward-looking sentences, verbatim.
        sents = []
        for p in _paragraphs(t):
            for s in re.split(r"(?<=[.!?])\s+", p["text"]):
                if (50 < len(s) < 320 and _FWD.search(s) and _NUM.search(s) and not s.rstrip().endswith("?")
                        and not re.match(r"(moderator|operator)", s, re.I)):
                    sents.append((len(_NUM.findall(s)) + len(_FWD.findall(s)), p["page"], s))
        sents.sort(key=lambda x: -x[0])
        for i, (_, page, s) in enumerate(sents[:6]):
            metric = ("margin" if re.search(r"margin|ebitda", s, re.I) else "capex" if re.search(r"capex|capacity|plant", s, re.I)
                      else "revenue_growth" if re.search(r"revenue|growth|top ?line|sales", s, re.I) else "other")
            out["guidance"].append({"id": f"{t['id']}-G{i + 1}", "metric": metric, "statement": clip(s, 140), "quote": s,
                                    "page": page, "horizon": None, "target": None, "call": t["id"],
                                    "callQuarter": t["quarter"], "callPeriod": t["period"], "callDate": t.get("date"),
                                    "url": t["url"]})
    return out


# ----------------------------------------------------------------------------- actuals table
def _fmt(v, pct: bool = False, dec: int = 0) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.1f}%" if pct else f"{v:,.{dec}f}"


def _quarter_rows(d: dict) -> list[dict]:
    rows = []
    for q in d["trajectory"]["quarters"]:
        try:
            qd = datetime.strptime(q["p"], "%b %Y").date()
            label = fy_quarter_label(qd.replace(day=calendar.monthrange(qd.year, qd.month)[1]))
        except ValueError:
            label = q["p"]
        rows.append({"label": label, **q})
    return rows


def _annual_rows(d: dict, n: int = 5) -> list[dict]:
    t, out = d["trajectory"], []
    cash, bal = d.get("cash") or {}, d.get("balance") or {}
    capex = {y: (c - f) for y, c, f in zip(cash.get("years", []), cash.get("cfo", []), cash.get("fcf", []))
             if c is not None and f is not None}  # screener FCF = CFO - fixed-asset purchases
    debt = {y: b for y, b in zip(bal.get("years", []), bal.get("borrowings", [])) if b is not None}
    for idx in range(max(0, len(t["years"]) - n), len(t["years"])):
        y, cur = t["years"][idx], t["sales"][idx]
        prev = t["sales"][idx - 1] if idx > 0 else None
        out.append({"label": f"FY{y[-2:]}" if y[-2:].isdigit() else y, "period": y, "sales": cur,
                    "salesYoY": pct_change(cur, prev),
                    "opm": t["opm"][idx] if idx < len(t["opm"]) else None,
                    "pat": t["pat"][idx] if idx < len(t["pat"]) else None,
                    "capex": capex.get(y), "borrowings": debt.get(y)})
    return out


def _ytd_row(rows: list[dict]) -> dict | None:
    """Fiscal year-to-date vs the same quarters last year, so full-year guidance can be tracked mid-year."""
    m = re.match(r"Q([1-3]) FY(\d{2})$", rows[-1]["label"]) if rows else None
    if not m:
        return None
    n, fy = int(m.group(1)), int(m.group(2))
    pick = lambda f: [r for r in rows if re.match(rf"Q[1-{n}] FY{f:02d}$", r["label"])]  # noqa: E731
    cur, prev = pick(fy), pick(fy - 1)
    if len(cur) != n or len(prev) != n or any(r["sales"] is None for r in cur + prev):
        return None
    s_cur, s_prev = sum(r["sales"] for r in cur), sum(r["sales"] for r in prev)
    opm = sum(r["sales"] * r["opm"] for r in cur) / s_cur if s_cur and all(r["opm"] is not None for r in cur) else None
    has_pat = all(r["pat"] is not None for r in cur + prev)
    p_cur = sum(r["pat"] for r in cur) if has_pat else None
    p_prev = sum(r["pat"] for r in prev) if has_pat else None
    return {"label": f"FY{fy:02d} YTD (Q1-Q{n})", "sales": s_cur, "salesYoY": pct_change(s_cur, s_prev), "opm": opm,
            "pat": p_cur, "patYoY": pct_change(p_cur, p_prev)}


def _actuals(d: dict) -> tuple[str, list[dict]]:
    rows = _quarter_rows(d)
    ytd = _ytd_row(rows)
    lines = ["Quarter | Revenue (Rs Cr) | Rev YoY | Op. margin % | PAT (Rs Cr) | PAT YoY"]
    for r in rows + ([ytd] if ytd else []):
        lines.append(f"{r['label']} | {_fmt(r['sales'])} | {_fmt(r['salesYoY'], True)} | {_fmt(r['opm'], dec=1)} | "
                     f"{_fmt(r['pat'])} | {_fmt(r['patYoY'], True)}")
    lines += ["", "Fiscal year | Revenue (Rs Cr) | Rev YoY | Op. margin % | PAT (Rs Cr) | Capex (Rs Cr) | Borrowings at year end (Rs Cr)"]
    for y in _annual_rows(d):
        span = "last 4 quarters" if y["label"] == "TTM" else f"year to {y['period']}"
        lines.append(f"{y['label']} ({span}) | {_fmt(y['sales'])} | {_fmt(y['salesYoY'], True)} | {_fmt(y['opm'], dec=1)} | "
                     f"{_fmt(y['pat'])} | {_fmt(y['capex'])} | {_fmt(y['borrowings'])}")
    return "\n".join(lines), rows


# ----------------------------------------------------------------------------- guidance horizons (deterministic)
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"


def _year(s: str) -> int:
    y = int(s)
    return y + 2000 if y < 100 else y


def _q_end(q: int, fy: int) -> date:
    return (date(fy - 1, 6, 30), date(fy - 1, 9, 30), date(fy - 1, 12, 31), date(fy, 3, 31))[q - 1]


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _fy_of(d: date) -> int:
    return d.year + 1 if d.month >= 4 else d.year


def _label_end(label: str | None) -> date | None:
    m = re.match(r"Q([1-4]) FY(\d{2})$", label or "")
    return _q_end(int(m.group(1)), _year(m.group(2))) if m else None


def horizon_end(h: str | None, said_on: date | None = None) -> date | None:
    """End date of a guidance horizon such as 'Q3 FY27', 'H2 FY26', 'FY27-28', 'by March 2026' or 'next 2-3 years'.
    Ranges resolve to their last period. Returns None when the horizon is too vague to date."""
    if not h:
        return None
    s, ends = str(h), []

    def take(pattern: str, fn) -> None:
        nonlocal s
        for m in re.finditer(pattern, s, re.I):
            ends.append(fn(m))
        s = re.sub(pattern, " ", s, flags=re.I)

    take(r"\bQ([1-4])\s*(?:of\s*)?(?:FY)?\s*'?(\d{4}|\d{2})\b", lambda m: _q_end(int(m.group(1)), _year(m.group(2))))
    take(r"\b(?:H([12])|(first|1st|second|2nd) half(?: of)?)\s*(?:FY)?\s*'?(\d{4}|\d{2})\b",
         lambda m: date(_year(m.group(3)) - 1, 9, 30) if (m.group(1) == "1" or (m.group(2) or "").lower() in ("first", "1st"))
         else date(_year(m.group(3)), 3, 31))
    take(r"\bFY\s*'?(\d{4}|\d{2})(?:\s*(?:-|–|/|to|and)\s*(?:FY)?\s*'?(\d{4}|\d{2}))?\b",
         lambda m: date(_year(m.group(2) or m.group(1)), 3, 31))
    take(r"\bCY\s*'?(\d{4}|\d{2})\b", lambda m: date(_year(m.group(1)), 12, 31))
    take(rf"\b{_MONTH_RE}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
         lambda m: _month_end(int(m.group(2)), _MONTHS[m.group(1).lower()[:3]]))  # "March 31, 2026"
    take(rf"\b{_MONTH_RE}[\s,.'’-]*(\d{{4}}|\d{{2}})\b", lambda m: _month_end(_year(m.group(2)), _MONTHS[m.group(1).lower()[:3]]))
    take(r"\b(20[2-4]\d)\b", lambda m: date(int(m.group(1)), 3, 31))
    if ends:
        return max(ends)
    if not said_on:
        return None
    if m := re.search(r"(\d+)\s*(?:(?:-|–|to)\s*(\d+)\s*)?years?", s, re.I):
        return date(said_on.year + int(m.group(2) or m.group(1)), said_on.month, 28)
    if re.search(r"\bnext (?:fiscal |financial )?year\b", s, re.I):
        return date(_fy_of(said_on) + 1, 3, 31)
    if re.search(r"\b(?:this|current|full|the) (?:fiscal |financial )?year\b|\bfull[- ]year\b|\byear[- ]end\b|\bannual\b", s, re.I):
        return date(_fy_of(said_on), 3, 31)
    if re.search(r"\b(?:next|coming|current|this) quarter\b", s, re.I):
        mo = ((said_on.month - 1) // 3 + 1) * 3
        return _month_end(said_on.year, mo)
    if re.search(r"medium[- ]term", s, re.I):
        return date(said_on.year + 3, said_on.month, 28)
    if re.search(r"long[- ]term", s, re.I):
        return date(said_on.year + 5, said_on.month, 28)
    return None


def _evidence_end(d: dict, extracted: list[dict]) -> date | None:
    """Last quarter-end for which we hold evidence (filings table or a call that discussed it)."""
    ends = [e for e in (_label_end(x["quarter"]) for x in extracted) if e]
    if d["trajectory"]["quarters"]:
        try:
            x = datetime.strptime(d["trajectory"]["quarters"][-1]["p"], "%b %Y").date()
            ends.append(_month_end(x.year, x.month))
        except ValueError:
            pass
    return max(ends) if ends else None


def _iso(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


SYSTEM_JUDGE = ("You audit management credibility for a portfolio manager. Judge each guidance item ONLY with the evidence "
                "provided: management's own reported numbers on LATER calls, or the financial actuals table. No outside "
                "knowledge, and trust the stated dates over your own sense of the current date. Output JSON only.")

PROMPT_JUDGE = """Company: {name}. Today is {today}. Indian fiscal years run April-March (FY26 = Apr 2025 to Mar 2026).
Evidence runs up to {latest} (quarter ended {ev_end}). Calls, oldest first: {calls}.

A. CLOSED GUIDANCE - the period has ended. Verdict: delivered, partly, missed, or unverifiable (no evidence covers it).
id | said on | scope | horizon | target | statement
{closed}

B. OPEN GUIDANCE - the period is still running. Verdict: on_track or behind when year-to-date numbers or later
statements show progress either way; delivered if already achieved early; otherwise pending.
id | said on | scope | horizon | target | statement
{open}

EVIDENCE 1 - results management reported on each call (id | call | statement):
{reported}

EVIDENCE 2 - financial actuals from company filings (YoY precomputed - do not recompute):
{actuals}

EVIDENCE 3 - segment table from the exchange filing:
{segments}

Rules:
- Evidence must come from AFTER the call where the guidance was given: a later call's reported id, or a table row for a later period.
- Compare like with like - this applies to on_track/behind too. Guidance whose scope is a segment or business line can only be
  judged from a reported id about that segment or the segment table, never from company-level rows. The cited number must
  measure the target's own metric: growth vs YoY growth, margin vs margin, capex vs the Capex column.
- on_track only if the year-to-date figure is inside the guided range or better; short of it is behind.
- partly = at least ~85% of a numeric target, or most of a non-numeric one (e.g. 13% growth against 15% guided, one of
  two plants commissioned). Below that is missed.
- Capex / investment plans: within ~15% of the guided amount is delivered; far above or below is missed (say over or under) -
  either way the plan changed.
- "actual" must give the number and cite its source: a reported id (e.g. T1-R2), a table row (e.g. "FY26 row",
  "FY27 YTD row") or "segment table".
- If the evidence is indirect or you are unsure, use pending (open) or unverifiable (closed). The reason must agree with the verdict.

Return JSON with exactly one ledger entry per guidance id:
{{"ledger": [{{"id": "<guidance id>", "verdict": "<one verdict word>", "actual": "<=20 words: number + source", "reason": "<=15 words"}}]}}"""

SYSTEM_TRACK = ("You write for a portfolio manager. Using ONLY the settled ledger given, describe management's guidance track "
                "record in at most 2 short sentences: where they deliver and where they over-promise. Name metrics and "
                "numbers. No adjectives the ledger doesn't support. Output JSON only.")

_SCORED = ("delivered", "partly", "missed")
_RID = re.compile(r"\bT(\d+)-R\d+\b")
_PCT = re.compile(r"(-?\d+(?:\.\d+)?)\s?%")
_HIGHER_BETTER = {"margin", "revenue_growth", "profit", "volume"}


def _is_company(scope) -> bool:
    s = str(scope or "").strip().lower()
    return not s or re.match(r"(company|group|consolidated|overall|standalone|total|entire|whole)\b", s) is not None


def _short_of_target(g: dict, actual: str) -> tuple[float, float] | None:
    """(best cited %, lowest target %) when a higher-is-better percentage target was clearly not reached."""
    tgt = g.get("target") or ""
    if g.get("metric") not in _HIGHER_BETTER or re.search(r"sub|<|below|under|less than|up ?to|max|cap|bps", tgt, re.I):
        return None
    t_nums = [float(x) for x in _PCT.findall(tgt)]
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s?%", tgt):
        t_nums += [float(m.group(1)), float(m.group(2))]
    a_nums = [float(x) for x in _PCT.findall(_RID.sub(" ", actual)) if float(x) not in t_nums]  # ignore restated targets
    if not t_nums or not a_nums:
        return None
    best, lo = max(a_nums), min(t_nums)
    return (best, lo) if best < lo - 0.25 else None


def _settle(g: dict, j: dict | None, reported: dict[str, dict]) -> None:
    """Apply the judge's verdict to a guidance item, enforcing horizon status and receipts deterministically."""
    closed = g["status"] == "closed"
    v = str((j or {}).get("verdict") or "").strip().lower().replace(" ", "_").replace("-", "_")
    actual, reason = (j or {}).get("actual") or None, (j or {}).get("reason") or None
    if closed:
        v = v if v in ("delivered", "partly", "missed", "unverifiable") else "unverifiable"
    else:
        v = {"missed": "behind", "unverifiable": "pending"}.get(v, v)
        v = v if v in ("delivered", "on_track", "behind", "pending") else "pending"
    g_call = int(g["call"][1:])
    cited = [m.group(0) for m in _RID.finditer(actual or "")]
    valid = [r for r in cited if r in reported and int(r.split("-")[0][1:]) < g_call]
    if v in _SCORED + ("on_track", "behind"):
        fallback = "unverifiable" if closed else "pending"
        call_end = _label_end(g.get("callQuarter"))
        period_end = horizon_end(_RID.sub(" ", actual or ""))  # latest period named in the citation, if any
        table_ok = bool(period_end) and (call_end is None or period_end > call_end)
        segment_ok = "segment" in (actual or "").lower()
        bare = re.sub(r"\bT\d+(?:-[RGC]\d+)?\b|\b(?:Q[1-4]|H[12])?\s*FY\s?'?\d{2,4}\b", " ", actual or "")
        if not re.search(r"\d", bare):
            v, reason = fallback, "No number cited as evidence"
        elif not (valid or table_ok or segment_ok):
            v, reason = fallback, "Cited evidence does not post-date the guidance"
        elif not _is_company(g.get("scope")) and not (valid or segment_ok):
            v, reason = fallback, f"Guidance is for {g.get('scope')}; later calls gave no segment number to test it"
        elif v in ("delivered", "on_track", "partly") and (short := _short_of_target(g, actual)):
            best, lo = short
            graded = "behind" if not closed else "partly" if best >= 0.85 * lo else "missed"
            if v != "partly" or graded == "missed":
                v, reason = graded, f"Best cited figure {best:g}% is below the {lo:g}% target"
    g["verdict"], g["actual"], g["reason"] = v, actual, reason
    g["evidence"] = [{k: reported[r].get(k) for k in ("id", "statement", "quote", "page", "url", "callQuarter")} for r in valid]


def _collapse(items: list[dict]) -> list[dict]:
    """Guidance repeated on later calls is one promise: keep the oldest statement, list every call that repeated it,
    and carry the most decisive judgement among the repeats. Returns the primary items."""
    groups: dict[tuple, list[dict]] = {}
    primaries = []
    for g in sorted(items, key=lambda x: -int(x["call"][1:])):  # oldest call first
        nums = "-".join(re.findall(r"\d+(?:\.\d+)?", g.get("target") or ""))
        scope = "company" if _is_company(g.get("scope")) else " ".join(_norm(g.get("scope")).split()[:2])
        key = (g.get("metric"), scope, g.get("horizonEnd"), nums)
        if g.get("horizonEnd") and nums and key in groups:
            groups[key].append(g)
            continue
        groups.setdefault(key if g.get("horizonEnd") and nums else (g["id"],), []).append(g)
        primaries.append(g)
    rank = {"delivered": 0, "partly": 0, "missed": 0, "behind": 1, "on_track": 1, "unverifiable": 2, "pending": 3}
    for grp in groups.values():
        p, repeats = grp[0], grp[1:]
        p["saidOn"] = [x["callQuarter"] for x in grp]
        if not repeats:
            continue
        for r in repeats:
            r["repeatOf"] = p["id"]
        # a scored judgement beats an open one; among equals the latest statement (closest to the outcome) wins
        best = min(reversed(grp), key=lambda x: rank.get(x["verdict"], 9))
        if best is not p:
            p.update(verdict=best["verdict"], actual=best["actual"], reason=best["reason"], evidence=best["evidence"])
    return primaries


def _track_summary(name: str, primaries: list[dict], counts: Counter, judged: int) -> tuple[str | None, str]:
    open_bits = ", ".join(f"{counts[k]} {k.replace('_', ' ')}" for k in ("on_track", "behind", "pending") if counts[k])
    if judged == 0:
        return (f"Nothing measurable has come due yet{' - open guidance: ' + open_bits if open_bits else ''}.", "rules")
    scored = [g for g in primaries if g["verdict"] in _SCORED + ("behind",)]
    ledger = "\n".join(f"- {g['statement']} (target {g.get('target') or '-'}, {g.get('horizon') or '-'}; said on "
                       f"{len(g.get('saidOn') or [1])} call(s)) -> {g['verdict']}: {g.get('actual') or '-'}" for g in scored)
    res = llm.json("fast", SYSTEM_TRACK, f"Company: {name}\nSettled ledger:\n{ledger}\n\nReturn JSON: {{\"summary\": \"...\"}}",
                   purpose="promise_summary", max_tokens=600, expect="summary")
    if isinstance(res, dict) and isinstance(res.get("summary"), str) and res["summary"].strip():
        return res["summary"].strip(), "llm"
    return (f"{counts['delivered']} delivered, {counts['partly']} partly and {counts['missed']} missed of {judged} promises "
            f"that have come due{'; open guidance: ' + open_bits if open_bits else ''}.", "rules")


def _replay(ns: str, sym: str, reason: str) -> dict | None:
    """Serve the last AI analysis computed for this stock, labelled with its date, when fresh AI isn't possible."""
    hit = store.get_with_age(ns, f"{sym}|{PROMPT_VERSION}|llm")
    if not hit:
        return None
    value, age = hit
    return {**value, "replay": True, "replayReason": reason,
            "replayedFrom": datetime.fromtimestamp(datetime.now().timestamp() - age).date().isoformat()}


def _no_ai_reason() -> str:
    return "no LLM key is set" if not llm.enabled else "today's AI budget is used up"


def _same_evidence(cached: dict, ts: list[dict], ev_end: date | None) -> bool:
    """Verdicts only change when new evidence arrives: a newer call or a newly reported quarter."""
    calls = cached.get("calls") or []
    return bool(calls) and calls[0].get("url") == ts[0]["url"] and cached.get("evidenceTo") == (ev_end.isoformat() if ev_end else None)


def promises(symbol: str) -> dict:
    sym = symbol.upper()
    key = f"{sym}|{PROMPT_VERSION}|{'llm' if llm.enabled else 'none'}"
    hit = store.get_with_age("promises", key)
    if hit and hit[1] <= 12 * 3600:
        return hit[0]
    if not llm.enabled and (replay := _replay("promises", sym, _no_ai_reason())):
        return replay
    d = dossier(sym)
    if not d:
        return {"symbol": sym, "error": "No fundamentals found for this ticker"}
    ts = transcripts(sym, 6)  # ~18 months of calls, so annual guidance has time to come due
    if not ts:
        return {"symbol": sym, "calls": [], "items": [], "claims": [], "message": "No readable concall transcripts found."}
    ev_end = _evidence_end(d, ts)
    if hit and hit[1] <= 30 * 86400 and _same_evidence(hit[0], ts, ev_end):
        store.set("promises", key, hit[0])  # still current: refresh its age
        return hit[0]
    if not llm.available() and (replay := _replay("promises", sym, _no_ai_reason())):
        return replay
    with ThreadPoolExecutor(3) as ex:
        extracted = list(ex.map(lambda t: _extract_one(d["name"], t), ts))
    items = [g for e in extracted for g in e["guidance"]]
    claims = [c for e in extracted for c in e["claims"]]
    reported = [r for e in extracted for r in e.get("reported", [])]
    actuals_text, _ = _actuals(d)
    latest = d["trajectory"]["quarters"][-1]["p"] if d["trajectory"]["quarters"] else "n/a"
    for g in items:
        end = horizon_end(g.get("horizon"), _iso(g.get("callDate")))
        g["horizonEnd"] = end.isoformat() if end else None
        g["status"] = "closed" if end and ev_end and end <= ev_end else "open"

    ledger, judged_by = None, None
    if llm.enabled and items:
        row = lambda g: (f"{g['id']} | {g['callQuarter']} call ({g['callPeriod']}) | {g.get('scope') or 'company'} | "  # noqa: E731
                         f"{g.get('horizon') or '-'} | {g.get('target') or '-'} | {g['statement']}")
        closed = [row(g) for g in items if g["status"] == "closed"]
        open_ = [row(g) for g in items if g["status"] == "open"]
        seg = d.get("segments") or {}
        seg_text = (f"{seg['quarter']} segment revenue: " + "; ".join(
            f"{x['name']} Rs {_fmt(x['revenue'])} Cr ({_fmt(x.get('yoy'), True)} YoY)" for x in seg["items"])
            if seg.get("items") else "none")
        res = llm.json("smart", SYSTEM_JUDGE, PROMPT_JUDGE.format(
            name=d["name"], today=date.today().strftime("%d %b %Y"), latest=latest,
            ev_end=ev_end.strftime("%d %b %Y") if ev_end else "n/a",
            calls=", ".join(f"{e['id']}={e['quarter']} call ({e['period']})" for e in reversed(extracted)),
            closed="\n".join(closed) or "none", open="\n".join(open_) or "none",
            reported="\n".join(f"{r['id']} | {r['callQuarter']} call | {r['statement']}" for r in reported) or "none",
            actuals=actuals_text, segments=seg_text), purpose="promise_vs_delivery", max_tokens=8000, expect="ledger")
        if isinstance(res, dict) and isinstance(res.get("ledger"), list):
            ledger = {x["id"]: x for x in res["ledger"] if isinstance(x, dict) and x.get("id")}
            judged_by = "llm"
    rep = {r["id"]: r for r in reported}
    for g in items:
        if ledger is None:
            g.update(verdict="unjudged", actual=None, evidence=[],
                     reason="AI judge unavailable - quotes shown unscored" if llm.enabled else "Add an LLM key to score guidance")
        else:
            _settle(g, ledger.get(g["id"]), rep)
    primaries = _collapse(items)
    counts = Counter(g["verdict"] for g in primaries)
    judged = sum(counts[v] for v in _SCORED)
    score = round((counts["delivered"] + 0.5 * counts["partly"]) / judged * 100) if judged >= 3 else None
    pattern = (None if ledger is None else "too early to tell" if judged == 0 else "limited track record" if score is None else
               "reliable" if score >= 75 else "mixed" if score >= 50 else "over-promises")
    summary, summary_by = _track_summary(d["name"], primaries, counts, judged) if ledger is not None else (None, None)
    out = {
        "symbol": sym, "name": d["name"],
        "calls": [{k: e[k] for k in ("id", "period", "quarter", "url", "pages", "tone", "toneReason", "method")} |
                  {"guidance": len(e["guidance"]), "claims": len(e["claims"]), "reported": len(e.get("reported", [])),
                   "dropped": e.get("dropped", 0)} for e in extracted],
        "items": items, "claims": claims, "reported": reported,
        "score": {"credibility": score, "counts": dict(counts), "judged": judged, "promises": len(primaries),
                  "statements": len(items)},
        "pattern": pattern, "summary": summary, "evidenceTo": ev_end.isoformat() if ev_end else None,
        "method": {"extract": extracted[0]["method"] if extracted else None, "judge": judged_by, "summary": summary_by,
                   "models": llm.models() if llm.enabled else None},
        "actualsTable": actuals_text,
    }
    degraded = llm.enabled and (ledger is None or any(e["method"] != "llm" for e in extracted))
    if degraded:  # don't pin a degraded answer; show the last good one if we have it and retry next time
        return _replay("promises", sym, "the AI models are unavailable right now") or out
    store.set("promises", key, out)
    return out


# ----------------------------------------------------------------------------- thesis (smart tier)
SYSTEM_THESIS = ("You are a sceptical buy-side analyst. Use ONLY the numbered facts and quotes provided - never outside knowledge "
                 "about the company or its sector. Every point must cite ids. Be concrete and short. Output JSON only.")

PROMPT_THESIS = """Company: {name}

FACTS (computed from filings):
{facts}

MANAGEMENT SAID (verbatim, from earnings calls):
{claims}

PROMISE TRACK RECORD: {track}

Return JSON:
{{"narrative": [ 3-4 items comparing what management says with what the numbers show:
    {{"claim": "<=16 words", "numbers": "<=22 words", "verdict": "consistent|diverges|too early", "refs": ["C..", "F.."]}} ],
 "bull": [ 3 items {{"point": "<=22 words", "refs": ["F..", "C.."]}} ],
 "bear": [ 3 items {{"point": "<=22 words", "refs": ["F..", "C.."]}} ],
 "watch": [ 2-3 things to check next quarter, <=14 words each ],
 "oneLiner": "<=22 words: the investment debate in one line"}}"""


def _facts(d: dict) -> list[tuple[str, str]]:
    facts = []
    add = lambda text: facts.append((f"F{len(facts) + 1}", text))  # noqa: E731
    sn = d["snapshot"]
    if sn.get("marketCap"):
        add(f"Market cap Rs {sn['marketCap']:,.0f} Cr; P/E {sn.get('pe') or 'n/a'}; ROCE {sn.get('roce') or 'n/a'}%; ROE {sn.get('roe') or 'n/a'}%")
    if sn.get("ttmRevenue"):
        add(f"TTM revenue Rs {sn['ttmRevenue']:,.0f} Cr, TTM PAT Rs {(sn.get('ttmPat') or 0):,.0f} Cr, TTM operating margin {sn.get('ttmOpm') or 0:.1f}%")
    for s in d["trajectory"]["signals"] + d["balance"]["signals"] + d["cash"]["signals"] + d["holding"]["signals"]:
        add(s)
    seg = d.get("segments") or {}
    if seg.get("items"):
        parts = ", ".join(f"{x['name']} {x['share']:.0f}%" + (f" ({x['yoy']:+.0f}% YoY)" if x.get("yoy") is not None else "")
                          for x in seg["items"][:6])
        add(f"Segment revenue mix {seg['quarter']}: {parts}")
    for q in d["trajectory"]["quarters"][-4:]:
        if q["sales"] is not None:
            add(f"{q['p']} quarter: revenue Rs {q['sales']:,.0f} Cr ({_fmt(q['salesYoY'], True)} YoY), "
                f"op. margin {_fmt(q['opm'], dec=1)}%, PAT Rs {_fmt(q['pat'])} Cr ({_fmt(q['patYoY'], True)} YoY)")
    return facts


def thesis(symbol: str) -> dict:
    sym = symbol.upper()
    key = f"{sym}|{PROMPT_VERSION}|{'llm' if llm.enabled else 'none'}"
    hit = store.get_with_age("thesis", key)
    if hit and hit[1] <= 12 * 3600:
        return hit[0]
    if not llm.enabled and (replay := _replay("thesis", sym, _no_ai_reason())):
        return replay
    d = dossier(sym)
    if not d:
        return {"symbol": sym, "error": "No fundamentals found for this ticker"}
    p = promises(sym)
    basis = [p.get("evidenceTo"), ((p.get("calls") or [{}])[0]).get("url")]
    if hit and hit[1] <= 30 * 86400 and hit[0].get("basis") == basis:
        store.set("thesis", key, hit[0])  # same evidence as when it was written: still current
        return hit[0]
    if not llm.available() and (replay := _replay("thesis", sym, _no_ai_reason())):
        return replay
    facts = _facts(d)
    claims = [(f"C{i + 1}", c) for i, c in enumerate((p.get("claims") or [])[:10])]
    track = (f"{p['score']['credibility']}% of judged guidance delivered ({p['score']['judged']} judged); pattern: {p.get('pattern')}"
             if p.get("score", {}).get("credibility") is not None else "not enough judged guidance")
    out = {"symbol": sym, "facts": [{"id": i, "text": t} for i, t in facts],
           "claims": [{"id": i, "text": c.get("claim"), "quote": c.get("quote"), "page": c.get("page"), "call": c.get("callQuarter"),
                       "url": c.get("url")} for i, c in claims],
           "narrative": [], "bull": [], "bear": [], "watch": [], "oneLiner": None, "method": "rules", "basis": basis}
    valid = {i for i, _ in facts} | {i for i, _ in claims}
    if llm.enabled:
        res = llm.json("smart", SYSTEM_THESIS, PROMPT_THESIS.format(
            name=d["name"], facts="\n".join(f"{i}: {t}" for i, t in facts),
            claims="\n".join(f"{i} ({c.get('callQuarter')} call, p.{c.get('page')}): \"{c.get('quote')}\"" for i, c in claims) or "none",
            track=track), purpose="thesis", max_tokens=3000, temperature=0.2, expect="bull")
        if isinstance(res, dict):
            def keep(items):
                good = []
                for it in items or []:
                    if isinstance(it, dict):
                        refs = [r for r in it.get("refs") or [] if r in valid]
                        if refs:
                            good.append({**it, "refs": refs})
                return good
            out.update(narrative=keep(res.get("narrative")), bull=keep(res.get("bull")), bear=keep(res.get("bear")),
                       watch=res.get("watch") or [], oneLiner=res.get("oneLiner"), method="llm")
    if out["method"] == "rules":
        # Deterministic fallback: sort computed facts into supportive vs worrying by keywords.
        for fid, t in facts:
            low = t.lower()
            if any(k in low for k in ("pressure", "not showing up", "lengthening", "jumped", "highly geared",
                                      "compressing", "holding down", "lagged")):
                out["bear"].append({"point": t, "refs": [fid]})
            elif any(k in low for k in ("operating leverage", "turning into cash", "improving", "lightly geared",
                                        "compounded", "expanding", "holding up")):
                out["bull"].append({"point": t, "refs": [fid]})
    if llm.enabled and out["method"] == "rules":  # the LLM merely failed: prefer the last good analysis, don't pin this
        return _replay("thesis", sym, "the AI models are unavailable right now") or out
    store.set("thesis", key, out)
    return out


# ----------------------------------------------------------------------------- ask (BM25 + LLM)
_TOK = re.compile(r"[a-z0-9]+")
_STOP = set("the a an and or of to in on for is are was were be been it this that with as at by from we our you they their its has have had will would can could should what did does do about how why which".split())
_SYN = {
    "smartphone": ["mobile", "phone", "phones", "handset", "smartphones"], "smartphones": ["mobile", "phone", "phones", "handset"],
    "mobile": ["smartphone", "phone", "handset"], "phones": ["mobile", "smartphone", "handset"],
    "margin": ["margins", "ebitda", "profitability", "opm"], "margins": ["margin", "ebitda", "profitability"],
    "revenue": ["sales", "topline", "turnover", "revenues"], "growth": ["grow", "growing", "increase"],
    "capex": ["capital", "investment", "capacity", "plant", "expansion"], "debt": ["borrowing", "borrowings", "leverage"],
    "guidance": ["expect", "target", "outlook", "guide"], "volume": ["volumes", "units", "million"],
    "volumes": ["volume", "units", "million"], "demand": ["consumption", "orders", "offtake"],
    "fy27": ["fy", "27", "2027", "year"], "fy26": ["fy", "26", "2026", "year"], "fy28": ["fy", "28", "2028"],
}


def _tokens(s: str) -> list[str]:
    return [w for w in _TOK.findall(s.lower()) if w not in _STOP and len(w) > 1]


def _expand(q: str) -> str:
    toks = _tokens(q)
    return " ".join(toks + [s for t in toks for s in _SYN.get(t, [])])


def _chunks(t: dict, words: int = 90) -> list[dict]:
    out = []
    for pno, text in enumerate(t["pages"], start=1):
        sents = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text))
        buf: list[str] = []
        for s in sents:
            buf.append(s)
            if sum(len(x.split()) for x in buf) >= words:
                out.append({"page": pno, "text": " ".join(buf)})
                buf = buf[-1:]
        if buf and sum(len(x.split()) for x in buf) > 15:
            out.append({"page": pno, "text": " ".join(buf)})
    return out


def _bm25(query: str, docs: list[dict], k: int = 8) -> list[dict]:
    q = _tokens(query)
    toks = [_tokens(d["text"]) for d in docs]
    n = len(docs)
    avg = sum(len(t) for t in toks) / max(1, n)
    df = Counter(w for t in toks for w in set(t))
    scored = []
    for d, t in zip(docs, toks):
        tf = Counter(t)
        s = 0.0
        for w in q:
            if w in tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(t) / avg))
        if s > 0:
            scored.append((s * d.get("boost", 1.0), d))
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:k]]


SYSTEM_ASK = ("You answer a portfolio manager's question using ONLY the numbered sources (earnings-call transcripts, "
              "numbers computed from filings, and the promise tracker). Cite the source id in brackets after each claim, "
              "e.g. [T1 p.4], [F3], [FY26], [T3-G1]. If the sources don't contain the answer, say exactly what is missing. "
              "<=120 words. Output JSON only.")


def ask(symbol: str, question: str) -> dict:
    sym = symbol.upper()
    d = dossier(sym)
    if not d:
        return {"answer": "I couldn't find fundamentals for this ticker.", "sources": []}
    docs = []
    for t in transcripts(sym, 4):
        for c in _chunks(t):
            docs.append({"id": f"{t['id']} p.{c['page']}", "label": f"{t['quarter']} call transcript, p.{c['page']}",
                         "url": t["url"], "text": c["text"]})
    # Structured guidance / reported numbers already extracted are dense, high-signal documents.
    cached_p = store.get("promises", f"{sym}|{PROMPT_VERSION}|{'llm' if llm.enabled else 'none'}")
    for g in (cached_p or {}).get("items", []) + (cached_p or {}).get("reported", []):
        page = g.get("page")
        docs.append({"id": f"{g['call']} p.{page}" if page else g["call"], "label": f"{g['callQuarter']} call" + (f", p.{page}" if page else ""),
                     "url": g["url"], "text": f"{g['statement']} {g.get('target') or ''} {g.get('horizon') or ''}. \"{g['quote']}\"", "boost": 1.4})
    facts = _facts(d)
    for fid, text in facts:
        docs.append({"id": fid, "label": "Computed from filings", "url": None, "text": text})
    for y in _annual_rows(d):
        if y["sales"] is not None and y["label"] != "TTM":
            docs.append({"id": y["label"], "label": f"{y['label']} annual results (filings)", "url": None,
                         "text": f"{y['label']} full year annual results (year to {y['period']}): revenue sales Rs {_fmt(y['sales'])} Cr, "
                                 f"revenue growth {_fmt(y['salesYoY'], True)} YoY, operating margin {_fmt(y['opm'], dec=1)}%, "
                                 f"PAT net profit Rs {_fmt(y['pat'])} Cr"})
    for g in (cached_p or {}).get("items", []):
        if g.get("verdict") in _SCORED + ("on_track", "behind") and not g.get("repeatOf"):
            docs.append({"id": g["id"], "label": f"Promise tracker: {g['callQuarter']} call guidance", "url": g["url"],
                         "text": f"Guidance on the {g['callQuarter']} call: {g['statement']} (target {g.get('target') or '-'}, "
                                 f"horizon {g.get('horizon') or '-'}). Verdict: {g['verdict'].replace('_', ' ')}. "
                                 f"Actual: {g.get('actual') or '-'}. {g.get('reason') or ''}", "boost": 1.6})
    hits = _bm25(_expand(question), docs, 10)
    seen, uniq = set(), []
    for h in hits:
        if (h["id"], h["text"][:60]) not in seen:
            seen.add((h["id"], h["text"][:60]))
            uniq.append(h)
    hits = uniq[:9]
    if not hits:
        return {"answer": "Nothing in the transcripts or filings matches that question.", "sources": [], "method": "bm25"}
    if llm.enabled:
        ctx = "\n".join(f"[{h['id']}] {clip(h['text'], 700)}" for h in hits)
        res = llm.json("fast", SYSTEM_ASK, f"Company: {d['name']}\nQuestion: {question}\n\nSources:\n{ctx}\n\n"
                       'Return JSON: {"answer": "...", "used": ["T1 p.4", "F3"]}', purpose="ask", max_tokens=1200, expect="answer")
        if isinstance(res, dict) and res.get("answer"):
            used = set(res.get("used") or [])
            src, seen_ids = [], set()
            for h in hits:
                if (not used or h["id"] in used) and h["id"] not in seen_ids:
                    seen_ids.add(h["id"])
                    src.append({"id": h["id"], "label": h["label"], "url": h["url"], "text": clip(h["text"], 320)})
            return {"answer": res["answer"], "method": "llm", "sources": src}
    return {"answer": "Most relevant passages from the primary sources:", "method": "bm25",
            "sources": [{"id": h["id"], "label": h["label"], "url": h["url"], "text": clip(h["text"], 320)} for h in hits[:4]]}
