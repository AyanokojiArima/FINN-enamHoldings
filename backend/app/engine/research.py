"""Pull: deterministic research dossier for any NSE ticker.

Numbers come from primary data (exchange XBRL for the latest quarter + segment mix) and screener's
standardised statements (which are themselves compiled from company filings). No LLM here: every
figure is computed in code so it is exact, instant and free. Interpretation lives in promises.py /
thesis.py and must cite these facts.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from ..sources import nse, screener
from ..store import store
from ..util import cagr, fy_quarter_label, pct_change
from .results import latest_qe_before, prev_qe, yoy_qe
from .signals import signals

log = logging.getLogger("finn.research")


def _row(table: dict, *names: str) -> list:
    for n in names:
        if n in table.get("rows", {}):
            return table["rows"][n]
    return []


def _series(table: dict, *names: str, drop_ttm: bool = True) -> list[dict]:
    vals = _row(table, *names)
    out = []
    for p, v in zip(table.get("periods", []), vals):
        if drop_ttm and p.upper() == "TTM":
            continue
        out.append({"p": p, "v": v})
    return out


def _last(series: list[dict], n: int = 1):
    vals = [x["v"] for x in series if x["v"] is not None]
    return vals[-n] if len(vals) >= n else None


def _cagr_from(series: list[dict], years: int):
    vals = [x for x in series if x["v"] is not None]
    if len(vals) <= years:
        return None
    return cagr(vals[-1]["v"], vals[-1 - years]["v"], years)


def _segments(symbol: str) -> dict:
    """Latest quarter whose exchange XBRL filing carries a segment-revenue note (walks back up to 4 quarters)."""
    try:
        filings = nse.integrated_filings(symbol)
    except Exception:  # noqa: BLE001
        return {}
    for qe in list(dict.fromkeys(f["qe"] for f in filings))[:4]:
        cur = nse.quarter_figures(symbol, qe, filings=filings)
        if cur and cur.get("segments"):
            break
    else:
        return {}
    latest = date.fromisoformat(qe)
    yoy = nse.quarter_figures(symbol, yoy_qe(latest).isoformat(), prefer=cur["basis"], filings=filings)
    prev_map = {s["name"].lower(): s["revenue"] for s in (yoy or {}).get("segments", [])}
    total = sum(s["revenue"] for s in cur["segments"] if s["revenue"] > 0) or 1
    segs = [{"name": s["name"], "revenue": s["revenue"], "share": s["revenue"] / total * 100,
             "yoy": pct_change(s["revenue"], prev_map.get(s["name"].lower()))} for s in cur["segments"]]
    segs.sort(key=lambda s: -s["revenue"])
    return {"quarter": fy_quarter_label(latest), "basis": cur["basis"], "items": segs,
            "source": {"label": f"Exchange XBRL segment note, {fy_quarter_label(latest)}", "url": cur["source"]["url"]}}


DOSSIER_VERSION = "r3"


def dossier(symbol: str) -> dict | None:
    sym = symbol.upper().strip()
    cached = store.get("dossier", f"{DOSSIER_VERSION}|{sym}", max_age=3 * 3600)
    if cached:
        return cached
    with ThreadPoolExecutor(3) as ex:
        f_s = ex.submit(screener.company, sym)
        f_sig = ex.submit(signals, sym)
        f_seg = ex.submit(_segments, sym)
        s, sig, seg = f_s.result(), f_sig.result(), f_seg.result()
    if not s or not s.get("profit_loss", {}).get("periods"):
        return None
    pl, q, bs, cf, rt, top = s["profit_loss"], s["quarters"], s["balance_sheet"], s["cash_flow"], s["ratios"], s["top"]
    is_fin = "Financing Profit" in q.get("rows", {}) or "Revenue" in pl.get("rows", {}) and "Sales" not in pl.get("rows", {})

    sales = _series(pl, "Sales", "Revenue")
    op = _series(pl, "Operating Profit", "Financing Profit")
    opm = _series(pl, "OPM %", "Financing Margin %")
    np_ = _series(pl, "Net Profit")
    ttm = lambda *names: (_row(pl, *names)[-1] if pl["periods"] and pl["periods"][-1].upper() == "TTM" and _row(pl, *names) else None)  # noqa: E731

    # ---- financial snapshot -------------------------------------------------------------------
    equity = [(a or 0) + (b or 0) for a, b in zip(_row(bs, "Equity Capital"), _row(bs, "Reserves"))]
    borrow = _row(bs, "Borrowings")
    de = [round(b / e, 2) if e and b is not None else None for b, e in zip(borrow, equity)]
    snapshot = {
        "marketCap": top.get("Market Cap"), "price": top.get("Current Price") or (sig or {}).get("price"),
        "pe": top.get("Stock P/E"), "bookValue": top.get("Book Value"), "divYield": top.get("Dividend Yield"),
        "roce": top.get("ROCE"), "roe": top.get("ROE"), "high52": top.get("High"), "low52": top.get("Low"),
        "ttmRevenue": ttm("Sales", "Revenue"), "ttmOpProfit": ttm("Operating Profit", "Financing Profit"),
        "ttmPat": ttm("Net Profit"),
        "ttmOpm": (ttm("Operating Profit", "Financing Profit") / ttm("Sales", "Revenue") * 100
                   if not is_fin and ttm("Operating Profit", "Financing Profit") and ttm("Sales", "Revenue") else None),
        "debtToEquity": de[-1] if de else None,
        "isFinancial": is_fin,
    }

    # ---- trajectory ------------------------------------------------------------------------------
    qs = _series(q, "Sales", "Revenue")
    qop = _series(q, "OPM %", "Financing Margin %")
    qnp = _series(q, "Net Profit")
    quarters = []
    for i, x in enumerate(qs):
        prev_year = qs[i - 4]["v"] if i >= 4 else None
        quarters.append({"p": x["p"], "sales": x["v"], "opm": qop[i]["v"] if i < len(qop) else None,
                         "pat": qnp[i]["v"] if i < len(qnp) else None,
                         "salesYoY": pct_change(x["v"], prev_year),
                         "patYoY": pct_change(qnp[i]["v"], qnp[i - 4]["v"]) if i >= 4 and i < len(qnp) else None})
    trajectory = {
        "years": [x["p"] for x in sales],
        "sales": [x["v"] for x in sales], "opProfit": [x["v"] for x in op], "opm": [x["v"] for x in opm],
        "pat": [x["v"] for x in np_],
        "cagr": {f"sales{n}": _cagr_from(sales, n) for n in (3, 5, 10)} | {f"pat{n}": _cagr_from(np_, n) for n in (3, 5, 10)},
        "quarters": quarters[-12:],
    }
    signals_t = []
    s3, p3 = trajectory["cagr"]["sales3"], trajectory["cagr"]["pat3"]
    if s3 is not None:
        signals_t.append(f"Revenue compounded {s3:.0f}% a year over 3 years" + (f" ({trajectory['cagr']['sales5']:.0f}% over 5)" if trajectory["cagr"]["sales5"] else ""))
    if s3 is not None and p3 is not None:
        if p3 > s3 + 3:
            signals_t.append(f"Profit grew faster than revenue ({p3:.0f}% vs {s3:.0f}% CAGR) - operating leverage")
        elif p3 < s3 - 3:
            signals_t.append(f"Profit lagged revenue ({p3:.0f}% vs {s3:.0f}% CAGR) - margins under pressure")
    opm_vals = [x["v"] for x in opm if x["v"] is not None]
    if len(opm_vals) >= 4 and not is_fin:
        lo, hi = min(opm_vals[-5:]), max(opm_vals[-5:])
        direction = "expanding" if opm_vals[-1] > opm_vals[-4] + 1 else "compressing" if opm_vals[-1] < opm_vals[-4] - 1 else "stable"
        signals_t.append(f"Operating margin {direction}: {opm_vals[-1]:.0f}% now vs {opm_vals[-4]:.0f}% three years ago (5-yr range {lo:.0f}-{hi:.0f}%)")
    recent = [x for x in quarters[-4:] if x["salesYoY"] is not None]
    if recent:
        avg = sum(x["salesYoY"] for x in recent) / len(recent)
        signals_t.append(f"Last {len(recent)} quarters: revenue {avg:+.0f}% YoY on average; latest {recent[-1]['p']} {recent[-1]['salesYoY']:+.0f}%")
    trajectory["signals"] = signals_t

    # ---- balance sheet -----------------------------------------------------------------------------
    bs_years = bs.get("periods", [])
    wc = {k: _row(rt, k) for k in ("Debtor Days", "Inventory Days", "Days Payable", "Cash Conversion Cycle", "Working Capital Days")}
    balance = {
        "years": bs_years, "borrowings": borrow, "equity": equity, "debtToEquity": de,
        "cwip": _row(bs, "CWIP"), "investments": _row(bs, "Investments"), "totalAssets": _row(bs, "Total Assets"),
        "workingCapital": {"years": rt.get("periods", []), **wc},
        "signals": [],
    }
    if de and de[-1] is not None:
        tag = "lightly geared" if de[-1] < 0.3 else "moderately geared" if de[-1] < 1 else "highly geared"
        trend = ""
        if len(de) >= 4 and de[-4] is not None:
            trend = f", {'down' if de[-1] < de[-4] else 'up'} from {de[-4]:.2f}× three years ago"
        balance["signals"].append(f"Debt/equity {de[-1]:.2f}× - {tag}{trend}" if not is_fin else
                                  f"Borrowings/equity {de[-1]:.1f}× (a lender: leverage is the business model)")
    ccc = [v for v in wc["Cash Conversion Cycle"] if v is not None]
    if len(ccc) >= 4:
        balance["signals"].append(f"Cash conversion cycle {ccc[-1]:.0f} days vs {ccc[-4]:.0f} three years ago"
                                  + (" - lengthening, cash getting tied up" if ccc[-1] > ccc[-4] + 10 else
                                     " - improving" if ccc[-1] < ccc[-4] - 10 else ""))
    dd = [v for v in wc["Debtor Days"] if v is not None]
    if len(dd) >= 2 and dd[-1] > dd[-2] * 1.25 and dd[-1] - dd[-2] > 10:
        balance["signals"].append(f"Receivable days jumped to {dd[-1]:.0f} from {dd[-2]:.0f} - check revenue quality")

    # ---- cash quality ------------------------------------------------------------------------------
    cfo = _row(cf, "Cash from Operating Activity")
    fcf = _row(cf, "Free Cash Flow")
    cf_years = cf.get("periods", [])
    pat_by_year = {x["p"]: x["v"] for x in np_}
    conv = []
    for y, c in zip(cf_years, cfo):
        p = pat_by_year.get(y)
        conv.append(round(c / p, 2) if c is not None and p and p > 0 else None)
    cash = {"years": cf_years, "cfo": cfo, "fcf": fcf, "pat": [pat_by_year.get(y) for y in cf_years],
            "conversion": conv, "signals": []}
    if len(cfo) >= 5 and not is_fin:
        c5 = sum(v for v in cfo[-5:] if v is not None)
        p5 = sum(v for v in (pat_by_year.get(y) for y in cf_years[-5:]) if v is not None)
        if p5 > 0:
            ratio = c5 / p5
            cash["cumulative5"] = round(ratio, 2)
            verdict = ("profits are turning into cash" if ratio >= 0.9 else
                       "a meaningful share of profit is not showing up as cash" if ratio < 0.7 else "decent but not full conversion")
            cash["signals"].append(f"5-year operating cash flow = {ratio:.0%} of reported profit: {verdict}")
        f5 = [v for v in fcf[-5:] if v is not None]
        if f5:
            neg = sum(1 for v in f5 if v < 0)
            cash["signals"].append(f"Free cash flow positive in {len(f5) - neg} of last {len(f5)} years; 5-yr total ₹{sum(f5):,.0f} Cr")
    elif is_fin:
        cash["signals"].append("For lenders, operating cash flow mostly reflects loan-book growth - judge by asset quality instead")

    # ---- shareholding ----------------------------------------------------------------------------------
    sh = s["shareholding"]
    promoters = _row(sh, "Promoters")
    holding = {"periods": sh.get("periods", []), "promoters": promoters, "fii": _row(sh, "FIIs"), "dii": _row(sh, "DIIs"),
               "public": _row(sh, "Public"), "signals": []}
    if len(promoters) >= 5 and promoters[-1] is not None and promoters[-5] is not None:
        delta = promoters[-1] - promoters[-5]
        if abs(delta) >= 0.5:
            holding["signals"].append(f"Promoter holding {'up' if delta > 0 else 'down'} {abs(delta):.1f}pp over the last year to {promoters[-1]:.1f}%")

    latest_q = latest_qe_before(date.today())
    out = {
        "symbol": sym, "name": s["name"], "basis": s["basis"], "about": s["about"], "website": s.get("website"),
        "quote": {"price": (sig or {}).get("price"), "chg": (sig or {}).get("chg"), "spark": (sig or {}).get("spark", []),
                  "ret1y": (sig or {}).get("ret1y"), "asOf": (sig or {}).get("asOf")},
        "snapshot": snapshot, "segments": seg, "trajectory": trajectory, "balance": balance, "cash": cash,
        "holding": holding,
        "documents": {"concalls": s["concalls"][:8], "annualReports": s["annual_reports"][:4]},
        "sources": [
            {"id": "SCR", "label": f"Standardised financials ({s['basis']}), compiled from company filings", "url": s["url"]},
            *([{"id": "XBRL", "label": seg["source"]["label"], "url": seg["source"]["url"]}] if seg else []),
        ],
        "latestQuarter": fy_quarter_label(latest_q),
        "prevQuarter": fy_quarter_label(prev_qe(latest_q)),
    }
    store.set("dossier", f"{DOSSIER_VERSION}|{sym}", out)
    return out
