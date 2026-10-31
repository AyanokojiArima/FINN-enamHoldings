"""End-of-day prices from Yahoo's public chart endpoint (same data yfinance uses, minus pandas)."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from ..config import IST
from ..http import http


def history(symbol: str, rng: str = "1y") -> dict | None:
    ysym = symbol if symbol.endswith((".NS", ".BO")) else f"{symbol}.NS"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ysym)}?range={rng}&interval=1d"
    data = http.json(url, ns="yahoo", ttl=1800)
    try:
        res = data["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return None
    q = res.get("indicators", {}).get("quote", [{}])[0]
    by_day: dict[str, dict] = {}
    for i, t in enumerate(res.get("timestamp") or []):
        c = (q.get("close") or [None])[i]
        if c is None:
            continue
        d = datetime.fromtimestamp(t, IST).date().isoformat()
        by_day[d] = {
            "d": d,
            "o": round(q["open"][i] or c, 2),
            "h": round(q["high"][i] or c, 2),
            "l": round(q["low"][i] or c, 2),
            "c": round(c, 2),
            "v": int(q["volume"][i] or 0),
        }
    meta = res.get("meta", {})
    return {
        "symbol": symbol,
        "name": meta.get("longName") or meta.get("shortName"),
        "currency": meta.get("currency", "INR"),
        "price": meta.get("regularMarketPrice"),
        "high52": meta.get("fiftyTwoWeekHigh"),
        "low52": meta.get("fiftyTwoWeekLow"),
        "marketTime": meta.get("regularMarketTime"),
        "candles": sorted(by_day.values(), key=lambda c: c["d"]),
    }
