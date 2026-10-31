"""screener.in: standardised multi-year financials + links to primary documents
(annual reports and concall transcripts hosted by BSE / the company)."""
from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..http import FetchError, http
from ..util import num

BASE = "https://www.screener.in"
SECTIONS = ("quarters", "profit-loss", "balance-sheet", "cash-flow", "ratios")


def _label(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip().rstrip("+").strip()


def _table(soup: BeautifulSoup, section: str) -> dict:
    t = soup.select_one(f"section#{section} table")
    if not t:
        return {"periods": [], "rows": {}}
    periods = [th.get_text(strip=True) for th in t.select("thead th")][1:]
    rows: dict[str, list] = {}
    links: dict[str, list] = {}
    for tr in t.select("tbody tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        label = _label(tds[0].get_text(" ", strip=True))
        if label.lower() == "raw pdf":
            links["raw_pdf"] = [(td.select_one("a") or {}).get("href") if td.select_one("a") else None for td in tds[1:]]
            continue
        rows[label] = [num(td.get_text(strip=True)) for td in tds[1:]]
    return {"periods": periods, "rows": rows, "links": links}


def _shareholding(soup: BeautifulSoup) -> dict:
    t = soup.select_one("#quarterly-shp table") or soup.select_one("section#shareholding table")
    if not t:
        return {"periods": [], "rows": {}}
    periods = [th.get_text(strip=True) for th in t.select("thead th")][1:]
    rows = {}
    for tr in t.select("tbody tr"):
        tds = tr.find_all("td")
        if tds:
            rows[_label(tds[0].get_text(" ", strip=True))] = [num(td.get_text(strip=True)) for td in tds[1:]]
    return {"periods": periods, "rows": rows}


def _documents(soup: BeautifulSoup) -> dict:
    annual = []
    for a in soup.select(".annual-reports ul li a"):
        title = a.get_text(" ", strip=True)
        m = re.search(r"(20\d\d)", title)
        annual.append({"title": title, "year": int(m.group(1)) if m else None, "url": a.get("href")})
    concalls = []
    for li in soup.select(".concalls ul li"):
        period_el = li.select_one("div")
        period = period_el.get_text(strip=True) if period_el else ""
        item = {"period": period, "transcript": None, "ppt": None, "rec": None, "notes": None}
        try:
            item["date"] = datetime.strptime(period, "%b %Y").date().isoformat()
        except ValueError:
            item["date"] = None
        for a in li.select("a"):
            kind = a.get_text(strip=True).lower()
            if kind.startswith("transcript"):
                item["transcript"] = a.get("href")
            elif kind == "ppt":
                item["ppt"] = a.get("href")
            elif kind == "rec":
                item["rec"] = a.get("href")
            elif kind == "notes":
                item["notes"] = a.get("href")
        concalls.append(item)
    return {"annual_reports": annual, "concalls": concalls}


def _ratios(soup: BeautifulSoup) -> dict:
    out = {}
    for li in soup.select("#top-ratios li"):
        name = li.select_one(".name")
        value = li.select_one(".value")
        if not name or not value:
            continue
        key = _label(name.get_text(" ", strip=True))
        text = re.sub(r"\s+", " ", value.get_text(" ", strip=True))
        if "/" in text and key.lower().startswith("high"):
            hi, lo = text.split("/", 1)
            out["High"], out["Low"] = num(hi), num(lo)
        else:
            out[key] = num(text)
    return out


def parse(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    about_el = soup.select_one(".company-profile .about") or soup.select_one(".about")
    about = re.sub(r"\[\d+\]", "", about_el.get_text(" ", strip=True)) if about_el else ""
    about = re.sub(r"\s+", " ", about).strip()
    links = [a.get("href") for a in soup.select(".company-links a") if a.get("href")]
    cid = soup.select_one("[data-company-id]")
    return {
        "name": (soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else None),
        "about": about,
        "website": next((l for l in links if "screener" not in l and "bseindia" not in l and "nseindia" not in l), None),
        "companyId": cid.get("data-company-id") if cid else None,
        "top": _ratios(soup),
        **{s.replace("-", "_"): _table(soup, s) for s in SECTIONS},
        "shareholding": _shareholding(soup),
        **_documents(soup),
    }


def company(symbol: str) -> dict | None:
    """Prefer consolidated numbers; fall back to standalone when a company doesn't consolidate."""
    best = None
    for path, basis in ((f"/company/{symbol}/consolidated/", "consolidated"), (f"/company/{symbol}/", "standalone")):
        try:
            html = http.text(BASE + path, ns="screener", ttl=6 * 3600)
        except FetchError:
            continue
        data = parse(html)
        data["basis"] = basis
        data["url"] = BASE + path
        rows = data["quarters"]["rows"]
        first = next(iter(rows.values()), [])
        if any(v is not None for v in first):
            return data
        best = best or data
    return best
