"""NSE: corporate announcements, integrated-filing financials (structured), equity master."""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..http import http
from ..store import store
from ..util import crore, num, parse_day, parse_nse_dt

log = logging.getLogger("finn.nse")

API = "https://www.nseindia.com/api"
HDRS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}


# --------------------------------------------------------------------------- announcements
def _parse_announcements(raw: list, symbol: str) -> list[dict]:
    out = []
    for x in raw or []:
        ts = parse_nse_dt(x.get("an_dt") or x.get("sort_date"))
        out.append({
            "id": str(x.get("seq_id") or f"{x.get('symbol')}-{x.get('dt')}"),
            "symbol": x.get("symbol") or symbol,
            "company": x.get("sm_name") or symbol,
            "industry": x.get("smIndustry"),
            "ts": ts.isoformat() if ts else None,
            "desc": (x.get("desc") or "").strip(),
            "text": (x.get("attchmntText") or "").strip(),
            "url": x.get("attchmntFile") or None,
            "size": x.get("attFileSize"),
        })
    return out


def announcements(symbol: str, start: date, end: date) -> list[dict]:
    url = (f"{API}/corporate-announcements?index=equities&symbol={quote(symbol)}"
           f"&from_date={start:%d-%m-%Y}&to_date={end:%d-%m-%Y}")
    ttl = 600 if end >= date.today() else 7 * 86400
    # stale_ok=False: if the exchange can't be reached the digest falls back explicitly and labels the data's age
    return _parse_announcements(http.json(url, ns="nse_ann", ttl=ttl, headers=HDRS, stale_ok=False) or [], symbol)


def cached_announcements(symbol: str) -> tuple[list[dict], float | None]:
    """Fallback when NSE can't be reached: every announcement we have ever fetched for a symbol,
    merged across cached date ranges, plus the epoch time of the newest fetch."""
    merged: dict[str, dict] = {}
    newest = None
    for _, raw, created in store.scan("nse_ann", f"%symbol={quote(symbol)}&%"):
        for x in raw or []:
            merged[str(x.get("seq_id") or f"{x.get('symbol')}-{x.get('dt')}")] = x
        newest = max(newest or 0, created)
    return _parse_announcements(list(merged.values()), symbol), newest


# --------------------------------------------------------------------------- equity master
def equity_master() -> list[dict]:
    text = http.text("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", ns="nse_master", ttl=7 * 86400)
    rows = csv.DictReader(io.StringIO(text))
    out = []
    for r in rows:
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        if r.get("SERIES") in ("EQ", "BE", "BZ", "SM", "ST"):
            out.append({"symbol": r["SYMBOL"], "name": r["NAME OF COMPANY"], "isin": r.get("ISIN NUMBER")})
    return out


# --------------------------------------------------------------------------- integrated filings
def integrated_filings(symbol: str) -> list[dict]:
    """All structured 'Integrated Filing - Financials' for a symbol (newest first)."""
    url = (f"{API}/integrated-filing-results?index=equities&symbol={quote(symbol)}"
           f"&type=Integrated%20Filing-%20Financials")
    data = http.json(url, ns="nse_ifr", ttl=1800, headers=HDRS) or {}
    out = []
    for x in data.get("data", []) if isinstance(data, dict) else []:
        qe = parse_day(x.get("qe_Date"))
        if not qe or not x.get("ixbrl"):
            continue
        out.append({
            "qe": qe.isoformat(),
            "basis": x.get("consolidated") or "",
            "audited": x.get("audited"),
            "broadcast": (parse_nse_dt(x.get("broadcast_Date")) or parse_nse_dt(x.get("creation_Date"))),
            "seq": int(x.get("seq_Id") or 0),
            "url": x["ixbrl"],
        })
    out.sort(key=lambda r: (r["qe"], r["seq"]), reverse=True)
    for r in out:
        r["broadcast"] = r["broadcast"].isoformat() if r["broadcast"] else None
    return out


def pick_filing(filings: list[dict], qe: str, prefer: str = "Consolidated") -> dict | None:
    same_q = [f for f in filings if f["qe"] == qe]
    for basis in (prefer, "Standalone" if prefer == "Consolidated" else "Consolidated"):
        cands = [f for f in same_q if f["basis"].lower().startswith(basis.lower()[:5])]
        if cands:
            return max(cands, key=lambda f: f["seq"])
    return None


_PL_KEYS = {
    "revenue": [r"^revenue from operations$", r"^total revenue from operations$"],
    "interest_earned": [r"^total interest earned$"],
    "interest_income": [r"^interest income$"],
    "impairment": [r"^impairment on financial instruments$"],
    "interest_expended": [r"^interest expen(ses|ded)$"],
    "other_income": [r"^other income$"],
    "total_income": [r"^total income$"],
    "finance_costs": [r"^finance costs?$"],
    "depreciation": [r"^depreciation"],
    "pbt_pre_exc": [r"^total profit before exceptional items and tax$",
                    r"^profit \(loss\) from ordinary activities before exceptional items and tax$"],
    "exceptional": [r"^exceptional items$"],
    "pbt": [r"^total profit before tax$", r"^(total )?profit \(loss\) from ordinary activities before tax$",
            r"^profit before tax$"],
    "tax": [r"^total tax expenses?$", r"^provision for tax$"],
    "pat": [r"^total profit \(loss\) for period$", r"^net profit \(loss\) for the period$",
            r"^net profit loss for the period from continuing operations$"],
    "pat_owners": [r"^profit or loss,? attributable to owners of parent$",
                   r"^net profit \(loss\) after taxes minority interest and share of profit"],
    "operating_profit": [r"^operating profit before provisions? and contingencies$"],
    "provisions": [r"^provisions? other than tax and contingencies$"],
    "eps": [r"^basic earnings \(loss\) per share from continuing operations$",
            r"^basic earnings per share before extraordinary items$"],
}


def _norm(label: str) -> str:
    return re.sub(r"\s+", " ", label.replace("\xa0", " ")).strip().lower()


def parse_integrated(url: str) -> dict:
    """Parse NSE's rendered XBRL page into {meta, columns, pl, segments} (values in ₹ crore)."""
    html = http.text(url, ns="nse_ifr_html", ttl=365 * 86400)
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    meta: dict[str, str] = {}
    for tr in tables[0].find_all("tr") if tables else []:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) >= 2:
            meta[_norm(cells[0])] = cells[1]
    rounding = next((v for k, v in meta.items() if k.startswith("level of rounding")), "")
    is_bank = False
    result: dict = {"meta": {
        "company": meta.get("name of company"),
        "basis": next((v for k, v in meta.items() if k.startswith("nature of report")), None),
        "quarter": meta.get("reporting quarter"),
        "audited": next((v for k, v in meta.items() if k.startswith("whether results are audited")), None),
        "rounding": rounding,
        "board_meeting": next((v for k, v in meta.items() if k.startswith("date of board meeting")), None),
        "url": url,
    }, "columns": [], "pl": {}, "segments": []}

    def rows_of(t):
        for tr in t.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if cells:
                yield cells

    def label_and_values(cells):
        # rows look like [serial, label, v1, v2] or [label, v1, v2]; serials are '', '1', '(a)', 'iv'...
        if len(cells) >= 2 and re.fullmatch(r"\(?(\d{1,2}|[a-z]|[ivx]{1,4})?\)?", cells[0].lower()):
            return cells[1], cells[2:]
        return cells[0], cells[1:]

    starts: list[str] = []
    for t in tables[1:]:
        rows = list(rows_of(t))
        labels = [_norm(label_and_values(c)[0]) for c in rows]
        if result["pl"] == {} and any(l in ("revenue from operations", "interest earned") for l in labels):
            is_bank = "interest earned" in labels
            for cells in rows:
                label, vals = label_and_values(cells)
                nl = _norm(label)
                if nl.startswith("date of start of reporting"):
                    starts = vals
                elif nl.startswith("date of end of reporting"):
                    result["columns"] = [{"start": s, "end": e} for s, e in zip(starts, vals)]
                for key, pats in _PL_KEYS.items():
                    if key in result["pl"]:
                        continue
                    if any(re.search(p, nl) for p in pats):
                        parsed = [num(v) for v in vals]
                        if all(v is None for v in parsed):
                            continue
                        if key == "eps":
                            result["pl"][key] = parsed
                        else:
                            result["pl"][key] = [crore(v, rounding) for v in parsed]
        elif not result["segments"] and any(l.startswith("segment revenue") for l in labels):
            in_rev = False
            for cells in rows:
                label, vals = label_and_values(cells)
                nl = _norm(label)
                if nl.startswith("segment revenue"):
                    in_rev = True
                    continue
                if in_rev and (nl.startswith("total segment revenue") or nl.startswith("segment result")
                               or nl.startswith("less: inter")):
                    break
                if in_rev and vals and not nl.startswith(("add:", "less:", "(net sale")):
                    v = crore(num(vals[0]), rounding)
                    if v is not None and v != 0:
                        result["segments"].append({"name": label.strip(), "revenue": v})
    result["meta"]["bank"] = is_bank
    result["meta"]["nbfc"] = not is_bank and "impairment" in result["pl"]
    for c in result["columns"]:
        s, e = parse_day(c["start"]), parse_day(c["end"])
        c["start"], c["end"] = (s.isoformat() if s else None), (e.isoformat() if e else None)
        c["days"] = (e - s).days + 1 if s and e else None
    return result


def quarter_figures(symbol: str, qe: str, prefer: str = "Consolidated", filings: list[dict] | None = None) -> dict | None:
    """Headline numbers for one quarter (₹ crore) straight from the exchange XBRL filing."""
    filings = filings if filings is not None else integrated_filings(symbol)
    f = pick_filing(filings, qe, prefer)
    if not f:
        return None
    try:
        parsed = parse_integrated(f["url"])
    except Exception as e:  # noqa: BLE001
        log.warning("integrated parse failed for %s %s: %s", symbol, qe, e)
        return None
    pl, cols = parsed["pl"], parsed["columns"]
    if not pl or not cols:
        return None
    first = 0
    vals = {k: (v[first] if isinstance(v, list) and len(v) > first else None) for k, v in pl.items()}
    # Q4 filings sometimes only carry the full year: derive the quarter as FY minus 9M YTD.
    if cols[0].get("days") and cols[0]["days"] > 120:
        dec = (datetime.fromisoformat(qe).date().replace(day=1) - timedelta(days=80))
        dec_qe = date(dec.year, 12, 31).isoformat()
        prev = pick_filing(filings, dec_qe, f["basis"])
        if not prev:
            return None
        p = parse_integrated(prev["url"])
        ytd_idx = len(p["columns"]) - 1
        for k, v in list(vals.items()):
            pv = p["pl"].get(k)
            if v is not None and pv and len(pv) > ytd_idx and pv[ytd_idx] is not None and k != "eps":
                vals[k] = v - pv[ytd_idx]
    return {
        "qe": qe,
        "basis": parsed["meta"]["basis"] or f["basis"],
        "bank": parsed["meta"]["bank"],
        "nbfc": parsed["meta"].get("nbfc", False),
        "audited": parsed["meta"]["audited"],
        "values": vals,
        "segments": parsed["segments"],
        "source": {"type": "nse_xbrl", "url": f["url"], "broadcast": f["broadcast"]},
    }
