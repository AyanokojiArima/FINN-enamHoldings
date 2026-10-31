"""Alerts = events that cross the user's own thresholds (filing types they care about,
price / volume moves beyond their limits). Same engine as the digest, different lens."""
from __future__ import annotations

from datetime import datetime

from ..portfolio import rules
from ..store import store
from ..util import now_ist
from .digest import build_digest, window


def alerts(days: int = 7) -> dict:
    s, e = window(hours=days * 24)
    d = build_digest(s, e)
    r = rules()
    out = []
    for ev in d["top"] + d["watch"]:
        if ev["category"] == "move":
            out.append({**ev, "trigger": "threshold"})
        elif r["categories"].get(ev["category"]) == "alert" and ev.get("base", ev["score"]) >= 55:
            out.append({**ev, "trigger": "category"})
    out.sort(key=lambda x: x["ts"], reverse=True)
    seen = store.state("alerts_seen_at")
    seen_dt = datetime.fromisoformat(seen) if seen else None
    for a in out:
        a["new"] = seen_dt is None or datetime.fromisoformat(a["ts"]) > seen_dt
    return {"alerts": out, "unseen": sum(1 for a in out if a["new"]), "rules": r, "generatedAt": d["generatedAt"],
            "stale": d.get("stale")}


def mark_seen() -> str:
    t = now_ist().isoformat()
    store.set_state("alerts_seen_at", t)
    return t
