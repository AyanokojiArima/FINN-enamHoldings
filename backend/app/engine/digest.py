"""Push digest: every filing across the portfolio in a time window, triaged.

Pipeline: fetch (parallel, cached) -> classify (rules) -> refine ambiguous ones by peeking at the
PDF (regex, no LLM) -> collapse related filings into one *event* per company story -> score
(category x portfolio weight x market reaction x user preference) -> tier.
Price/volume moves with no filing to explain them become their own events.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta

from ..config import IST
from ..portfolio import portfolio, rules
from ..sources import nse
from ..sources.pdftext import document_pages
from ..store import store
from ..util import clip, fy_quarter_label, now_ist
from .classify import CATEGORIES, KEY_ROLE, classify
from .results import quarter_end_from
from .signals import signals

log = logging.getLogger("finn.digest")

_BOILER = re.compile(
    r"^.*?\b(?:has|have)\s+(?:informed|submitted to|intimated(?: to)?|filed with)\s+(?:the\s+)?exchanges?\s*,?\s*"
    r"(?:regarding|about|that|of)?\s*(?:the\s+)?", re.I)
_PRESS = re.compile(r"^(?:a\s+)?press release dated [^,]+,\s*titled\s*", re.I)
MARKET_CLOSE = time(15, 30)
_GENERIC = re.compile(r"^(please (refer|find)|intimation under|disclosure under|outcome of (the )?board meeting|"
                      r"submission of|compliance under|announcement under|general updates?|updates?$)", re.I)


def clean_headline(text: str, desc: str) -> str | None:
    t = (text or "").strip()
    t = _BOILER.sub("", t, count=1).strip(" '\"-:")
    t = _PRESS.sub("", t).strip(" '\"")
    t = re.sub(r"\.(?=[A-Z])", ". ", t)  # "2026.Further" -> "2026. Further"
    t = re.sub(r"\s+", " ", t).strip(" '\".")
    low = t.lower()
    if len(t) < 14 or low == desc.lower() or low in ("change in management", "credit rating", "updates",
                                                      "general updates", "press release", "transcript"):
        return None
    if _GENERIC.match(low):
        return None
    return clip(t[0].upper() + t[1:], 260)


def split_title(h: str) -> tuple[str, str | None]:
    """Long company headlines -> (first sentence as title, remainder as detail)."""
    if len(h) <= 110:
        return h, None
    m = re.match(r"(.{30,150}?[a-z0-9)\]])[.;]\s+(.+)", h)
    if m:
        return m.group(1), m.group(2)
    return clip(h, 120), h


_DATE_PATTERNS = [
    (re.compile(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-/\.]*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-/\.,]*(20\d\d)", re.I), "dmy"),
    (re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)", re.I), "mdy"),
    (re.compile(r"\b(\d{2})[\-/\.](\d{2})[\-/\.](20\d\d)\b"), "num"),
]
_MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def find_dates(text: str) -> list[date]:
    out = []
    for pat, kind in _DATE_PATTERNS:
        for m in pat.finditer(text or ""):
            try:
                if kind == "dmy":
                    out.append(date(int(m.group(3)), _MON[m.group(2)[:3].lower()], int(m.group(1))))
                elif kind == "mdy":
                    out.append(date(int(m.group(3)), _MON[m.group(1)[:3].lower()], int(m.group(2))))
                else:
                    out.append(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
            except (ValueError, KeyError):
                continue
    return out


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        log.warning("%s%s failed: %s", fn.__name__, a[:1], e)
        return None


# ----------------------------------------------------------------------------- refinement
_APPROVED = re.compile(r"(approved|considered and approved|took on record|taken on record)[^.]{0,160}results", re.I)
_TABLE = re.compile(r"revenue from operations|total income from operations|interest earned", re.I)
_INTIMATION = re.compile(r"(is|are) scheduled|will be held|to be held|to consider and approve|intimation of (the )?board meeting", re.I)
_SUBJECT = re.compile(r"\b(?:sub(?:ject)?|re)\s*[:\-–]\s*(.{12,260}?)(?:\n\s*\n|\bdear\b|\bsir\b|\bmadam\b|\bref(?:erence)?\b|$)", re.I | re.S)


def _refine(item: dict) -> dict:
    """Peek at pages 1-3 of ambiguous filings (regex only, no LLM) to sharpen the classification."""
    pages = document_pages(item["url"], max_pages=3) if item.get("url") else []
    body = " ".join(pages)[:8000]
    if not body:
        return item
    sub = item["subtype"]
    subject = None
    m = _SUBJECT.search(body[:2500])
    if m:
        subject = re.sub(r"\s+", " ", m.group(1)).strip(" .:-")
    looks_filed = (_APPROVED.search(body) or (_TABLE.search(body) and re.search(r"profit before tax", body, re.I))) \
        and not _INTIMATION.search(body[:1500])
    if sub in ("board_outcome", "news", "other") and looks_filed:
        item.update(category="results", subtype="filed" if sub != "news" else "press", score=92 if sub != "news" else 55,
                    why="Quarterly results filed" if sub != "news" else "Results press release")
    elif sub == "unknown_role":
        # Only the subject + opening paragraph: signature blocks always mention the MD/CS.
        lead = (subject or "") + " " + body[:1500]
        top_role = re.search(r"\b(ceo|chief executive|managing director|cfo|chief financial|whole[- ]?time director|chair(man|person))\b", lead, re.I)
        if re.search(r"senior management", lead, re.I) and not top_role:
            item.update(subtype="smp", score=45, why="Senior management change")
        elif re.search(KEY_ROLE, lead, re.I) and not re.search(r"independent director|subsidiary|vice[- ]president", lead, re.I):
            exit_ = re.search(r"resign|cessation|demise|retire|step(s|ped)? down", lead, re.I) is not None
            item.update(subtype="key_exit" if exit_ else "key_appointment", score=92 if exit_ else 86,
                        why="Top-management " + ("exit" if exit_ else "appointment"))
        elif re.search(r"senior management|\bSMP\b|vice[- ]president|\bhead\b", lead, re.I):
            item.update(subtype="smp", score=45, why="Senior management change")
    elif item["category"] == "credit_rating" and sub == "unknown":
        low = body.lower()
        if "downgrad" in low:
            item.update(subtype="downgrade", score=88, why="Credit rating downgrade")
        elif "upgrad" in low:
            item.update(subtype="upgrade", score=60, why="Credit rating upgrade")
        elif re.search(r"re-?affirm", low):
            item.update(subtype="reaffirm", score=14, why="Credit rating reaffirmed")
    elif sub in ("other", "news", "board_outcome") and subject:
        # The letter's own "Sub:" line is the best free label we can get.
        c = classify(item["desc"], subject)
        if c["category"] != "unclassified" or c["score"] < item["score"]:
            item.update(category=c["category"], subtype=c["subtype"], score=c["score"], why=c["why"])
    if subject and not item.get("headline"):
        item["headline"] = clean_headline(subject, item["desc"])
    item["refined"] = True
    return item


# ----------------------------------------------------------------------------- reaction
def _session_for(ts: datetime, days: list[dict]) -> dict | None:
    """First trading session whose close could reflect a filing made at ts."""
    d = ts.date() + (timedelta(days=1) if ts.timetz().replace(tzinfo=None) > MARKET_CLOSE else timedelta(0))
    for day in days:
        if day["d"] >= d.isoformat():
            return day
    return None


# ----------------------------------------------------------------------------- main
DIGEST_VERSION = "d4"


def build_digest(start: datetime, end: datetime, use_cache: bool = True) -> dict:
    cache_key = f"{DIGEST_VERSION}|{start.isoformat()[:16]}|{end.isoformat()[:15]}"
    if use_cache:
        hit = store.get("digest", cache_key, max_age=300)
        if hit:
            return _apply_triage(hit)

    holdings = portfolio()
    weights = {h["symbol"]: h.get("weight", 0) for h in holdings}
    sectors = {h["symbol"]: h.get("sector", "") for h in holdings}
    symbols = list(weights)
    r = rules()
    fetch_from = min(start.date(), (now_ist() - timedelta(days=21)).date())

    with ThreadPoolExecutor(6) as ex:
        fetched = dict(zip(symbols, ex.map(lambda s: _safe(nse.announcements, s, fetch_from, end.date()), symbols)))
        sigs = dict(zip(symbols, ex.map(lambda s: _safe(signals, s, int(r.get("volumeLookback", 20))), symbols)))

    # ---- exchange unreachable (e.g. a cloud host the exchange blocks): fall back to the newest data we hold,
    # and say so, rather than showing an empty brief ------------------------------------------------------
    stale = None
    failed = [s for s, v in fetched.items() if v is None]
    if symbols and len(failed) * 2 >= len(symbols):
        newest = 0.0
        for s in failed:
            lst, created = nse.cached_announcements(s)
            if lst:
                fetched[s] = lst
                newest = max(newest, created or 0)
        as_of = datetime.fromtimestamp(newest, IST) if newest else None
        if not as_of or as_of < end - timedelta(minutes=30):
            if as_of:
                start, end = as_of - (end - start), min(end, as_of)
            stale = {"asOf": as_of.isoformat() if as_of else None,
                     "reason": "The exchange feed isn't reachable from this server right now."}
    anns = {s: v or [] for s, v in fetched.items()}

    names = {s: (sigs[s] or {}).get("name") for s in symbols}
    all_items, upcoming = [], []
    for sym, lst in anns.items():
        for a in lst:
            if not a.get("ts"):
                continue
            c = classify(a["desc"], a["text"])
            it = {**a, **c, "headline": clean_headline(a["text"], a["desc"])}
            names[sym] = names.get(sym) or a["company"]
            all_items.append(it)

    # ---- upcoming events from the last 3 weeks of filings --------------------------------
    today = now_ist().date()
    for it in all_items:
        if it["subtype"] in ("intimation", "notice", "record_date", "board_intimation") or it["category"] in ("agm_egm", "dividend"):
            future = sorted({d for d in find_dates(it["text"]) if today <= d <= today + timedelta(days=45)})
            if future:
                label = {"intimation": "Results (board meeting)", "notice": "Earnings call", "record_date": "Record date",
                         "board_intimation": "Board meeting"}.get(it["subtype"], CATEGORIES[it["category"]])
                upcoming.append({"date": future[0].isoformat(), "symbol": it["symbol"], "label": label,
                                 "detail": it["headline"] or it["why"], "url": it["url"]})
    seen_up = set()
    upcoming = [u for u in sorted(upcoming, key=lambda u: u["date"])
                if not ((u["symbol"], u["label"]) in seen_up or seen_up.add((u["symbol"], u["label"])))][:12]

    items = [it for it in all_items if start <= datetime.fromisoformat(it["ts"]) <= end]

    # ---- refine ambiguous, potentially material filings by peeking at the PDF ------------
    ambiguous = [it for it in items if it.get("url") and (
        it["subtype"] in ("board_outcome", "unknown_role", "other") or
        (it["category"] == "credit_rating" and it["subtype"] == "unknown") or
        (it["subtype"] == "news" and not it["headline"]))][:30]
    if ambiguous:
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(_refine, ambiguous))

    # ---- collapse into events ------------------------------------------------------------
    results_syms = {it["symbol"] for it in items if it["category"] == "results" and it["subtype"] == "filed"}
    groups: dict[tuple, list] = defaultdict(list)
    for it in items:
        cat = it["category"]
        if cat == "earnings_call" and it["symbol"] in results_syms:
            cat = "results"
        fam = cat if cat in ("results", "management_change", "earnings_call", "unclassified", "investor_meetings") else f"{cat}:{it['subtype']}"
        groups[(it["symbol"], fam)].append(it)

    events, muted = [], 0
    for (sym, fam), its in groups.items():
        its.sort(key=lambda x: (x["score"], x["ts"]), reverse=True)
        p = its[0]
        cat = fam.split(":")[0]
        pref = r["categories"].get(cat, "digest")
        if pref == "mute":
            muted += len(its)
            continue
        ts = datetime.fromisoformat(p["ts"])
        w = weights.get(sym, 0)
        score = p["score"] + min(8.0, w * 0.8)
        why = [CATEGORIES.get(cat, cat)]
        if w:
            why.append(f"{w:g}% of portfolio")
        reaction = None
        sig = sigs.get(sym)
        if sig and p["score"] >= 30:
            day = _session_for(ts, sig["days"])
            if day:
                reaction = {"d": day["d"], "ret": day["ret"], "vmult": day["vmult"]}
                big_move = abs(day["ret"]) >= r["priceMovePct"]
                big_vol = (day["vmult"] or 0) >= r["volumeMultiple"]
                if big_move:
                    score += 12
                elif abs(day["ret"]) >= r["priceMovePct"] / 2:
                    score += 5
                if big_vol:
                    score += 8
                if big_move or big_vol:
                    why.append(f"Stock {day['ret']:+.1f}% on {day['vmult'] or 0:.1f}× volume")
        if pref == "alert" and p["score"] >= 45:
            score += 6
        title = p["headline"] or p["why"]
        if cat == "earnings_call":
            title = p["why"]
        title, detail = split_title(title)
        enrich = None
        if cat == "results" and any(x["subtype"] in ("filed", "press") for x in its):
            filed = (next((x for x in its if x["subtype"] == "filed"), None)
                     or next(x for x in its if x["subtype"] == "press"))
            q = quarter_end_from(filed["text"], datetime.fromisoformat(filed["ts"]))
            title, detail = f"{fy_quarter_label(q)} results are out", None
            enrich, p = "results", filed
        elif cat == "management_change" and p["subtype"] not in ("cs",):
            enrich = "people"
        elif p["score"] >= 30 and not p["headline"] and p.get("url") and cat != "earnings_call":
            enrich = "summary"
        tier = "top" if score >= 70 else "watch" if score >= 30 else "routine"
        events.append({
            "id": f"{sym}:{fam}:{p['id']}",
            "symbol": sym, "company": names.get(sym) or p["company"], "sector": sectors.get(sym, ""),
            "category": cat, "categoryLabel": CATEGORIES.get(cat, cat), "subtype": p["subtype"],
            "title": title, "detail": detail, "headline": p["headline"], "rule": p["why"],
            "ts": p["ts"], "url": p["url"], "desc": p["desc"], "text": p["text"],
            "score": round(score, 1), "base": p["score"], "tier": tier, "why": why, "enrich": enrich, "reaction": reaction,
            "related": [{"title": split_title(x["headline"] or x["why"])[0], "subtype": x["subtype"], "ts": x["ts"], "url": x["url"]}
                        for x in sorted(its, key=lambda x: x["ts"]) if x is not p],
        })

    # ---- unexplained price / volume moves (one event per stock, biggest session first) --------
    explained = {(e["symbol"], e["reaction"]["d"]) for e in events if e["reaction"] and e["tier"] != "routine"}
    material = defaultdict(list)
    for it in all_items:
        if it["score"] >= 45:
            material[it["symbol"]].append(it)
    for sym in symbols:
        sig = sigs.get(sym)
        if not sig:
            continue
        thr = {**r, **r.get("overrides", {}).get(sym, {})}
        hits = []
        for day in sig["days"]:
            dd = datetime.combine(date.fromisoformat(day["d"]), MARKET_CLOSE, tzinfo=IST)
            if not (start <= dd <= end) or (sym, day["d"]) in explained:
                continue
            big_move = abs(day["ret"]) >= thr["priceMovePct"]
            big_vol = (day["vmult"] or 0) >= thr["volumeMultiple"]
            extreme = bool(r.get("near52w") and (day["high52"] or day["low52"]))
            if big_move or big_vol or extreme:
                hits.append((big_move, big_vol, extreme, day, dd))
        if not hits:
            continue
        hits.sort(key=lambda h: (h[0], h[1], abs(h[3]["ret"])), reverse=True)
        big_move, big_vol, extreme, day, dd = hits[0]
        parts = ["Flat" if abs(day["ret"]) < 0.05 else f"{'Up' if day['ret'] >= 0 else 'Down'} {abs(day['ret']):.1f}%"]
        if big_vol:
            parts.append(f"{day['vmult']:.1f}× normal volume")
        if extreme:
            parts.append("52-week closing " + ("high" if day["high52"] else "low"))
        # Context: the most recent material filing before the move (if any) - shown, not assumed.
        prior = [it for it in material[sym]
                 if dd - timedelta(days=6) <= datetime.fromisoformat(it["ts"]) <= dd]
        prior.sort(key=lambda it: (it["ts"][:10], it["score"]), reverse=True)
        context = None
        if prior:
            pi = prior[0]
            context = {"title": split_title(pi["headline"] or pi["why"])[0], "ts": pi["ts"], "url": pi["url"],
                       "category": pi["category"]}
        score = (74 if big_move else 62 if big_vol else 46) + min(8.0, weights.get(sym, 0) * 0.8)
        events.append({
            "id": f"{sym}:move:{day['d']}", "symbol": sym, "company": names.get(sym) or sym, "sector": sectors.get(sym, ""),
            "category": "move", "categoryLabel": "Price / volume", "subtype": "move",
            "title": " · ".join(parts), "detail": None, "base": score,
            "headline": None,
            "rule": "No new filing behind this move" if not context else "No filing on the day - see last material filing",
            "ts": dd.isoformat(), "url": None, "score": round(score, 1),
            "tier": "top" if score >= 70 else "watch",
            "why": ["Price / volume", f"{weights.get(sym, 0):g}% of portfolio",
                    "Unexplained by filings" if not context else "Follows a recent filing"],
            "enrich": None, "reaction": {"d": day["d"], "ret": day["ret"], "vmult": day["vmult"]},
            "context": context,
            "related": [{"title": " · ".join([f"{'Up' if h[3]['ret'] >= 0 else 'Down'} {abs(h[3]['ret']):.1f}%"] +
                                             ([f"{h[3]['vmult']:.1f}× vol"] if h[1] else [])),
                         "subtype": "move", "ts": h[4].isoformat(), "url": None} for h in hits[1:]],
            "spark": [d["c"] for d in sig["days"][-30:]],
            "sparkDates": [d["d"] for d in sig["days"][-30:]],
        })

    events.sort(key=lambda e: (e["score"], e["ts"]), reverse=True)
    top = [e for e in events if e["tier"] == "top"]
    watch = [e for e in events if e["tier"] == "watch"]
    routine_groups: dict[str, dict] = {}
    for e in events:
        if e["tier"] != "routine":
            continue
        g = routine_groups.setdefault(e["categoryLabel"], {"label": e["categoryLabel"], "count": 0, "items": []})
        g["count"] += 1 + len(e["related"])
        g["items"].append({"id": e["id"], "symbol": e["symbol"], "title": e["title"], "ts": e["ts"], "url": e["url"]})

    pulse = []
    counts = defaultdict(int)
    for e in top + watch:
        counts[e["symbol"]] += 1
    for h in holdings:
        s = sigs.get(h["symbol"]) or {}
        pulse.append({"symbol": h["symbol"], "name": names.get(h["symbol"]), "weight": h.get("weight"),
                      "sector": h.get("sector"), "price": s.get("price"), "chg": s.get("chg"), "vmult": s.get("vmult"),
                      "asOf": s.get("asOf"), "spark": s.get("spark", []), "events": counts[h["symbol"]]})

    day_chg = sum((p["chg"] or 0) * (p["weight"] or 0) for p in pulse) / max(1, sum(p["weight"] or 0 for p in pulse))
    out = {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "generatedAt": now_ist().isoformat(),
        "stats": {"filings": len(items), "events": len(events), "top": len(top), "watch": len(watch),
                  "routine": sum(g["count"] for g in routine_groups.values()), "muted": muted},
        "portfolioDayChange": round(day_chg, 2),
        "top": top, "watch": watch,
        "routine": sorted(routine_groups.values(), key=lambda g: -g["count"]),
        "pulse": pulse, "upcoming": upcoming, "stale": stale,
    }
    store.set("digest", cache_key, out)
    return _apply_triage(out)


def _apply_triage(d: dict) -> dict:
    tri = store.state("triage", {})
    for e in d["top"] + d["watch"]:
        e["triage"] = (tri.get(e["id"]) or {}).get("state")
    return d


def set_triage(event_id: str, state: str | None) -> dict:
    def upd(t):
        t = t or {}
        if state:
            t[event_id] = {"state": state, "t": now_ist().isoformat()}
        else:
            t.pop(event_id, None)
        return t
    return store.update_state("triage", upd, {})


def window(hours: float | None = None, start: str | None = None, end: str | None = None) -> tuple[datetime, datetime]:
    if start:
        s = datetime.fromisoformat(start)
        s = s if s.tzinfo else s.replace(tzinfo=IST)
        e = datetime.fromisoformat(end) if end else now_ist()
        e = e if e.tzinfo else e.replace(tzinfo=IST)
        return s, e
    e = now_ist()
    return e - timedelta(hours=hours or 24), e
