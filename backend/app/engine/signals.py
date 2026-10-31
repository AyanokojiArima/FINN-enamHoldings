"""End-of-day signals: daily move, volume vs. its own recent average, 52-week extremes."""
from __future__ import annotations

from ..sources import yahoo


def signals(symbol: str, lookback: int = 20) -> dict | None:
    try:
        h = yahoo.history(symbol, "1y")
    except Exception:  # noqa: BLE001
        return None
    if not h or len(h["candles"]) < 2:
        return None
    cs = h["candles"]
    days = []
    for i in range(1, len(cs)):
        cur, prev = cs[i], cs[i - 1]
        window = cs[max(0, i - lookback):i]
        avg_v = sum(c["v"] for c in window) / len(window) if window else 0
        prior = cs[max(0, i - 252):i]
        hi = max(c["c"] for c in prior) if prior else None
        lo = min(c["c"] for c in prior) if prior else None
        days.append({
            "d": cur["d"],
            "c": cur["c"],
            "v": cur["v"],
            "ret": round((cur["c"] / prev["c"] - 1) * 100, 2) if prev["c"] else 0.0,
            "vmult": round(cur["v"] / avg_v, 2) if avg_v else None,
            "high52": bool(hi and cur["c"] > hi and len(prior) >= 200),
            "low52": bool(lo and cur["c"] < lo and len(prior) >= 200),
        })
    last = days[-1]
    closes = [c["c"] for c in cs]
    return {
        "symbol": symbol,
        "name": h["name"],
        "price": last["c"],
        "chg": last["ret"],
        "vmult": last["vmult"],
        "asOf": last["d"],
        "high52": max(closes[-252:]),
        "low52": min(closes[-252:]),
        "ret1m": round((closes[-1] / closes[-22] - 1) * 100, 1) if len(closes) > 22 else None,
        "ret1y": round((closes[-1] / closes[0] - 1) * 100, 1),
        "spark": closes[-30:],
        "days": days[-90:],
    }
