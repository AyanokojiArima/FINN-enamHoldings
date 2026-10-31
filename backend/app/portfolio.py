"""Mock portfolio (18 stocks, 18 sectors, mega-cap to small-cap) and user settings."""
from __future__ import annotations

from .store import store

DEFAULT_PORTFOLIO = [
    {"symbol": "RELIANCE", "weight": 10, "sector": "Energy & Digital"},
    {"symbol": "HDFCBANK", "weight": 10, "sector": "Private Bank"},
    {"symbol": "INFY", "weight": 7, "sector": "IT Services"},
    {"symbol": "LT", "weight": 7, "sector": "Engineering & Construction"},
    {"symbol": "BHARTIARTL", "weight": 7, "sector": "Telecom"},
    {"symbol": "BAJFINANCE", "weight": 6, "sector": "NBFC"},
    {"symbol": "ETERNAL", "weight": 6, "sector": "Internet / Quick commerce"},
    {"symbol": "ITC", "weight": 5, "sector": "FMCG"},
    {"symbol": "DRREDDY", "weight": 5, "sector": "Pharma"},
    {"symbol": "MARUTI", "weight": 5, "sector": "Autos"},
    {"symbol": "DIXON", "weight": 5, "sector": "Electronics Manufacturing"},
    {"symbol": "HAVELLS", "weight": 4, "sector": "Consumer Durables"},
    {"symbol": "JSWSTEEL", "weight": 4, "sector": "Metals"},
    {"symbol": "KPITTECH", "weight": 4, "sector": "Auto Software"},
    {"symbol": "CDSL", "weight": 4, "sector": "Market Infrastructure"},
    {"symbol": "SRF", "weight": 4, "sector": "Specialty Chemicals"},
    {"symbol": "PAYTM", "weight": 4, "sector": "Fintech"},
    {"symbol": "SUZLON", "weight": 3, "sector": "Renewables"},
]

# On-demand research demo set: deliberately includes names outside the portfolio.
RESEARCH_PICKS = ["LT", "PIDILITIND", "POLYCAB", "DIXON", "ASIANPAINT", "TRENT"]

DEFAULT_RULES = {
    "priceMovePct": 4.0,      # alert when |daily move| >= this
    "volumeMultiple": 2.0,    # alert when volume >= N x 20-session average
    "volumeLookback": 20,
    "near52w": True,          # alert on fresh 52-week closing highs/lows
    "categories": {
        "results": "alert", "mna": "alert", "management_change": "alert", "fund_raise": "alert",
        "litigation": "alert", "credit_rating": "alert", "pledging": "alert", "bonus_split": "digest",
        "capex": "digest", "dividend": "digest", "press_release": "digest", "insider_trading": "digest",
        "earnings_call": "digest", "investor_meetings": "digest", "agm_egm": "digest",
        "postal_ballot": "digest", "unclassified": "digest",
    },
    "overrides": {},          # per-symbol {"priceMovePct": 6}
}


def portfolio() -> list[dict]:
    return store.state("portfolio", DEFAULT_PORTFOLIO)


def set_portfolio(items: list[dict]) -> list[dict]:
    seen, clean = set(), []
    for it in items:
        sym = str(it.get("symbol", "")).upper().strip()
        if sym and sym not in seen:
            seen.add(sym)
            clean.append({"symbol": sym, "weight": float(it.get("weight") or 0), "sector": it.get("sector") or ""})
    store.set_state("portfolio", clean)
    return clean


def rules() -> dict:
    saved = store.state("rules", {})
    merged = {**DEFAULT_RULES, **saved}
    merged["categories"] = {**DEFAULT_RULES["categories"], **saved.get("categories", {})}
    return merged


def set_rules(new: dict) -> dict:
    cur = rules()
    for k in ("priceMovePct", "volumeMultiple", "volumeLookback", "near52w", "overrides"):
        if k in new:
            cur[k] = new[k]
    if "categories" in new:
        cur["categories"].update({k: v for k, v in new["categories"].items() if v in ("alert", "digest", "mute")})
    store.set_state("rules", cur)
    return cur
