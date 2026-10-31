from __future__ import annotations

import re
from datetime import date, datetime

from .config import IST

_NUM_RE = re.compile(r"[-+]?\(?[\d,]*\.?\d+\)?")


def num(text: str | None) -> float | None:
    """Parse '78,74,994.00', '(8,244.00)', '3.9%', '₹ 69,691 Cr.' -> float (None if absent)."""
    if text is None:
        return None
    s = str(text).strip().replace("\u2212", "-").replace("₹", "")
    if not s or s in {"-", "--", "NA", "N.A."}:
        return None
    m = _NUM_RE.search(s.replace(" ", ""))
    if not m:
        return None
    tok = m.group(0)
    neg = tok.startswith("(") and tok.endswith(")") or tok.startswith("-")
    tok = tok.strip("()+-").replace(",", "")
    try:
        v = float(tok)
    except ValueError:
        return None
    return -v if neg else v


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


def cagr(end: float | None, start: float | None, years: float) -> float | None:
    if end is None or start is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return ((end / start) ** (1 / years) - 1) * 100.0


def parse_nse_dt(s: str | None) -> datetime | None:
    """'28-Oct-2026 21:20:51' -> aware IST datetime."""
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def parse_day(s: str | None) -> date | None:
    if not s:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d", "%b %Y"):
        try:
            return datetime.strptime(s.strip().title() if "%b" in fmt or "%B" in fmt else s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def now_ist() -> datetime:
    return datetime.now(IST)


def fy_quarter_label(d: date) -> str:
    """Quarter-end date -> Indian fiscal label, e.g. 2026-09-30 -> 'Q2 FY27'."""
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(d.month)
    fy = d.year + 1 if d.month >= 4 else d.year
    return f"Q{q} FY{fy % 100:02d}" if q else d.strftime("%b %Y")


def crore(value: float | None, rounding: str | None) -> float | None:
    """Convert a figure reported at a given rounding level to ₹ crore."""
    if value is None:
        return None
    r = (rounding or "").strip().lower()
    factor = {
        "crores": 1, "crore": 1,
        "lakhs": 1e-2, "lakh": 1e-2,
        "millions": 1e-1, "million": 1e-1,
        "thousands": 1e-4, "thousand": 1e-4,
        "billions": 100, "billion": 100,
        "actual": 1e-7, "rupees": 1e-7, "": 1e-7,
    }.get(r, 1e-7)
    return value * factor


def clip(text: str | None, n: int) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"
