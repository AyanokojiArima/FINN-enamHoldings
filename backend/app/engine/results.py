"""Quarterly results card: Revenue / EBITDA / PBT / PAT for this quarter vs QoQ vs YoY.

Source ladder (best first):
  1. NSE integrated-filing XBRL  - the company's own machine-readable filing; exact, free.
  2. Results PDF read by the fast LLM - available minutes after the board meeting, ~1-3h before
     XBRL lands. Every number must literally appear in the PDF or the extraction is rejected.
  3. screener.in standardised quarterly table.
Arithmetic (EBITDA, margins, growth) is always done in code, never by the model.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

from ..llm import llm
from ..sources import nse, screener
from ..sources.pdftext import document_pages, find_pages, has_text
from ..store import store
from ..util import crore, fy_quarter_label, pct_change

log = logging.getLogger("finn.results")

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_QE = {(3, 31), (6, 30), (9, 30), (12, 31)}


def prev_qe(qe: date) -> date:
    return {3: date(qe.year - 1, 12, 31), 6: date(qe.year, 3, 31), 9: date(qe.year, 6, 30),
            12: date(qe.year, 9, 30)}[qe.month]


def yoy_qe(qe: date) -> date:
    return date(qe.year - 1, qe.month, qe.day)


def latest_qe_before(d: date) -> date:
    for year in (d.year, d.year - 1):
        for m, day in sorted(_QE, reverse=True):
            q = date(year, m, day)
            if q < d:
                return q
    return date(d.year - 1, 12, 31)


def quarter_end_from(text: str, ts: datetime | None) -> date:
    t = (text or "").lower()
    found: list[date] = []
    for m in re.finditer(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-\.,/]*([a-z]{3,9})[\s\-\.,/]*(20\d\d)", t):
        mon = _MONTHS.get(m.group(2)[:3])
        if mon:
            found.append((int(m.group(3)), mon, int(m.group(1))))
    for m in re.finditer(r"([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)", t):
        mon = _MONTHS.get(m.group(1)[:3])
        if mon:
            found.append((int(m.group(3)), mon, int(m.group(2))))
    for m in re.finditer(r"(\d{2})[\.\-/](\d{2})[\.\-/](20\d\d)", t):
        found.append((int(m.group(3)), int(m.group(2)), int(m.group(1))))
    ref = ts.date() if ts else date.today()
    qes = []
    for y, mo, dd in found:
        if (mo, dd) in _QE:
            try:
                q = date(y, mo, dd)
            except ValueError:
                continue
            if q <= ref:
                qes.append(q)
    return max(qes) if qes else latest_qe_before(ref)


# ----------------------------------------------------------------------------- headline maths
def headline(v: dict | None, kind: str) -> dict:
    """kind: 'corp' (EBITDA), 'bank' or 'nbfc' (pre-provision operating profit instead of EBITDA)."""
    if not v:
        return {}
    g = lambda k: v.get(k)  # noqa: E731
    out = {"pbt": g("pbt"), "pat": g("pat_owners") if g("pat_owners") is not None else g("pat"),
           "exceptional": g("exceptional")}
    if "ebitda" in v:  # already standardised (screener fallback)
        out.update(revenue=g("revenue"), ebitda=g("ebitda"))
        return out
    base = g("pbt_pre_exc")
    if base is None and g("pbt") is not None:
        base = g("pbt") - (g("exceptional") or 0)
    if kind == "bank":
        out["revenue"] = g("total_income")
        out["ebitda"] = g("operating_profit")
        if g("interest_earned") is not None and g("interest_expended") is not None:
            out["nii"] = g("interest_earned") - g("interest_expended")
    elif kind == "nbfc":
        out["revenue"] = g("revenue")
        if base is not None:
            out["ebitda"] = base + (g("impairment") or 0)
        if g("interest_income") is not None and g("finance_costs") is not None:
            out["nii"] = g("interest_income") - g("finance_costs")
    else:
        out["revenue"] = g("revenue")
        if base is not None:
            out["ebitda"] = base + (g("finance_costs") or 0) + (g("depreciation") or 0) - (g("other_income") or 0)
    return out


def _cr(x: float) -> str:
    return f"{'−' if x < 0 else ''}₹{abs(x):,.0f} Cr"


def _card(symbol: str, qe: date, cur: dict, prev: dict | None, yoy: dict | None, kind: str, basis: str,
          source: dict, notes: list[str]) -> dict:
    fin = kind in ("bank", "nbfc")
    hc, hp, hy = headline(cur, kind), headline(prev, kind), headline(yoy, kind)
    labels = ([("revenue", "Total income" if kind == "bank" else "Revenue"), ("ebitda", "Op. profit (PPOP)"),
               ("pbt", "PBT"), ("pat", "PAT")] if fin
              else [("revenue", "Revenue"), ("ebitda", "EBITDA"), ("pbt", "PBT"), ("pat", "PAT")])
    rows = []
    for key, label in labels:
        c = hc.get(key)
        rows.append({"key": key, "label": label, "value": c, "prev": hp.get(key), "yoyValue": hy.get(key),
                     "qoq": pct_change(c, hp.get(key)), "yoy": pct_change(c, hy.get(key))})

    def margin(h):
        return h["ebitda"] / h["revenue"] * 100 if h.get("ebitda") is not None and h.get("revenue") else None

    m_c, m_p, m_y = margin(hc), margin(hp), margin(hy)
    rev = rows[0]
    pat = rows[3]
    bits = []
    if rev["yoy"] is not None:
        bits.append(f"{rows[0]['label']} {rev['yoy']:+.0f}% YoY")
    if pat["yoy"] is not None:
        bits.append(f"PAT {pat['yoy']:+.0f}% YoY")
    if m_c is not None and m_y is not None and not fin:
        bits.append(f"EBITDA margin {m_c:.1f}% ({(m_c - m_y) * 100:+.0f} bps YoY)")
    if hc.get("exceptional"):
        notes.append(f"Includes exceptional items of {_cr(hc['exceptional'])}")
    tone = None
    if rev["yoy"] is not None and pat["yoy"] is not None:
        tone = "strong" if rev["yoy"] > 12 and pat["yoy"] > 12 else "weak" if pat["yoy"] < -5 or rev["yoy"] < 0 else "steady"
    return {
        "kind": "results", "symbol": symbol, "qe": qe.isoformat(), "quarter": fy_quarter_label(qe),
        "periods": {"current": fy_quarter_label(qe), "prev": fy_quarter_label(prev_qe(qe)), "yoy": fy_quarter_label(yoy_qe(qe))},
        "basis": basis, "bank": fin, "entity": kind, "rows": rows,
        "margin": {"current": m_c, "prev": m_p, "yoy": m_y},
        "nii": {"current": hc.get("nii"), "yoy": pct_change(hc.get("nii"), hy.get("nii"))} if fin and hc.get("nii") else None,
        "verdict": " · ".join(bits), "tone": tone, "source": source, "notes": notes,
    }


# ----------------------------------------------------------------------------- fallbacks
def _screener_quarter(symbol: str, qe: date) -> dict | None:
    try:
        s = screener.company(symbol)
    except Exception:  # noqa: BLE001
        return None
    if not s:
        return None
    q = s["quarters"]
    label = qe.strftime("%b %Y")
    if label not in q["periods"]:
        return None
    i = q["periods"].index(label)
    r = q["rows"]
    get = lambda *names: next((r[n][i] for n in names if n in r and i < len(r[n]) and r[n][i] is not None), None)  # noqa: E731
    return {"revenue": get("Sales", "Revenue"), "ebitda": get("Operating Profit", "Financing Profit"),
            "pbt": get("Profit before tax"), "pat": get("Net Profit")}


SYSTEM_PDF = (
    "You extract figures from Indian listed-company quarterly results filings. Output JSON only. "
    "Copy numbers exactly as printed in the table (same unit, no rescaling); negative numbers in brackets become negative. "
    "Use null for anything not present. Never estimate or compute.")

PROMPT_PDF = """From the results table below, extract the CONSOLIDATED figures if present, otherwise STANDALONE.
Columns needed: the current quarter, the immediately preceding quarter, and the same quarter last year.

Return JSON:
{{"basis": "consolidated|standalone", "unit": "crore|lakh|million|thousand|rupees", "is_bank": false,
  "current": {{"period_end": "YYYY-MM-DD", "revenue": n, "other_income": n, "total_income": n, "finance_costs": n,
              "depreciation": n, "pbt_before_exceptional": n, "exceptional": n, "pbt": n, "pat": n, "pat_owners": n,
              "operating_profit_before_provisions": n, "interest_earned": n, "interest_expended": n}},
  "previous_quarter": {{...same keys...}},
  "year_ago_quarter": {{...same keys...}}}}

Filing text:
{text}"""


def _grounded(value: float, haystack: set[float]) -> bool:
    return any(abs(abs(value) - h) < 0.011 for h in haystack)


def from_pdf(url: str) -> tuple[dict, dict, dict, dict] | None:
    pages = document_pages(url, max_pages=40)
    if not has_text(pages) or not llm.enabled:
        return None
    idx = find_pages(pages, [r"revenue from operations", r"profit before tax", r"total income", r"quarter ended",
                             r"interest earned", r"consolidated"], top=4)
    if not idx:
        return None
    text = "\n\n".join(f"[page {i + 1}]\n{pages[i][:7000]}" for i in idx)
    res = llm.json("fast", SYSTEM_PDF, PROMPT_PDF.format(text=text), purpose="results_pdf_extract", max_tokens=3000,
                   expect="current")
    if not isinstance(res, dict) or not isinstance(res.get("current"), dict):
        return None
    numbers = {float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", text.replace("\u2212", "-"))
               if x.replace(",", "").replace(".", "", 1).isdigit()}
    unit = res.get("unit") or "crore"
    total = ok = 0
    mapped = {}
    keymap = {"revenue": "revenue", "other_income": "other_income", "total_income": "total_income",
              "finance_costs": "finance_costs", "depreciation": "depreciation", "pbt_before_exceptional": "pbt_pre_exc",
              "exceptional": "exceptional", "pbt": "pbt", "pat": "pat", "pat_owners": "pat_owners",
              "operating_profit_before_provisions": "operating_profit", "interest_earned": "interest_earned",
              "interest_expended": "interest_expended"}
    for col in ("current", "previous_quarter", "year_ago_quarter"):
        src = res.get(col) or {}
        vals = {}
        for k_in, k_out in keymap.items():
            v = src.get(k_in)
            if isinstance(v, (int, float)):
                total += 1
                if _grounded(float(v), numbers) or v == 0:
                    ok += 1
                    vals[k_out] = crore(float(v), unit)
        mapped[col] = vals
    if total == 0 or ok / total < 0.8:
        log.warning("rejecting PDF extraction for %s: only %s/%s numbers grounded", url, ok, total)
        return None
    meta = {"basis": (res.get("basis") or "").title() or "Consolidated", "bank": bool(res.get("is_bank")),
            "grounded": f"{ok}/{total}"}
    return mapped["current"], mapped["previous_quarter"], mapped["year_ago_quarter"], meta


# ----------------------------------------------------------------------------- entry point
def results_card(symbol: str, text: str = "", ts: str | None = None, url: str | None = None) -> dict | None:
    when = datetime.fromisoformat(ts) if ts else None
    qe = quarter_end_from(text, when)
    key = f"{symbol}|{qe.isoformat()}"
    hit = store.get_with_age("results", key)
    if hit and (hit[0].get("source", {}).get("type") == "nse_xbrl" or hit[1] < 1800):
        return hit[0]

    notes: list[str] = []
    card = None
    try:
        filings = nse.integrated_filings(symbol)
    except Exception:  # noqa: BLE001
        filings = []
    cur = nse.quarter_figures(symbol, qe.isoformat(), filings=filings) if filings else None
    if cur:
        basis = cur["basis"]
        kind = "bank" if cur["bank"] else "nbfc" if cur.get("nbfc") else "corp"
        p = nse.quarter_figures(symbol, prev_qe(qe).isoformat(), prefer=basis, filings=filings)
        y = nse.quarter_figures(symbol, yoy_qe(qe).isoformat(), prefer=basis, filings=filings)
        pv = p["values"] if p else _screener_quarter(symbol, prev_qe(qe))
        yv = y["values"] if y else _screener_quarter(symbol, yoy_qe(qe))
        if (not p and pv) or (not y and yv):
            notes.append("Comparison quarter from screener.in (older filing not in XBRL)")
        card = _card(symbol, qe, cur["values"], pv, yv, kind, basis,
                     {"type": "nse_xbrl", "label": "Exchange XBRL filing", "url": cur["source"]["url"], "verified": True},
                     notes)
        card["segments"] = cur.get("segments") or []
    elif url:
        pdf = from_pdf(url)
        if pdf:
            c, p, y, meta = pdf
            card = _card(symbol, qe, c, p, y, "bank" if meta["bank"] else "corp", meta["basis"],
                         {"type": "pdf_llm", "label": "AI first cut from results PDF", "url": url,
                          "verified": False, "grounded": meta["grounded"]}, notes)
    if card is None:
        c = _screener_quarter(symbol, qe)
        if c and c.get("revenue") is not None:
            card = _card(symbol, qe, c, _screener_quarter(symbol, prev_qe(qe)), _screener_quarter(symbol, yoy_qe(qe)),
                         "corp", "Consolidated",
                         {"type": "screener", "label": "screener.in standardised", "url": f"https://www.screener.in/company/{symbol}/consolidated/",
                          "verified": False}, notes)
    if card is None:
        return {"kind": "results", "symbol": symbol, "qe": qe.isoformat(), "quarter": fy_quarter_label(qe),
                "pending": True, "message": "Numbers not machine-readable yet - open the filing.", "source": {"url": url}}
    store.set("results", key, card)
    return card
