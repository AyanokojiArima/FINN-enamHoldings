from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .engine import analyst, research
from .engine.alerts import alerts, mark_seen
from .engine.digest import build_digest, set_triage, window
from .engine.enrich import people_card, summary_card
from .engine.results import results_card
from .llm import llm
from .portfolio import RESEARCH_PICKS, portfolio, rules, set_portfolio, set_rules
from .sources import nse
from .util import now_ist

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("finn")

app = FastAPI(title="FINN - Financial Investment Agent", version="1.0")
app.add_middleware(GZipMiddleware, minimum_size=800)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def _expensive(request: Request) -> bool:
    p = request.url.path
    return (p.startswith("/api/research/") or p == "/api/enrich"
            or (p == "/api/digest" and request.query_params.get("fresh") in ("1", "true")))


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    """On a public link (FINN_RATE_LIMIT set): cap per-IP requests that read filings or call the AI, per 10 minutes."""
    if config.RATE_LIMIT and _expensive(request):
        ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else "-")
        now = time.time()
        with _hits_lock:
            if len(_hits) > 5000:
                for k in [k for k, q in _hits.items() if not q or q[-1] < now - 600]:
                    del _hits[k]
            q = _hits[ip]
            while q and q[0] < now - 600:
                q.popleft()
            if len(q) >= config.RATE_LIMIT:
                return JSONResponse({"detail": "Too many requests from your network - please try again in a few minutes."},
                                    status_code=429, headers={"Retry-After": "120"})
            q.append(now)
    return await call_next(request)


# ----------------------------------------------------------------------------- meta & settings
@app.get("/api/meta")
def meta():
    return {"now": now_ist().isoformat(), "llm": llm.status(), "portfolio": portfolio(), "researchPicks": RESEARCH_PICKS,
            "rules": rules(), "offline": config.OFFLINE}


@app.get("/api/portfolio")
def get_portfolio():
    return portfolio()


@app.put("/api/portfolio")
def put_portfolio(items: list[dict] = Body(...)):
    return set_portfolio(items)


@app.get("/api/rules")
def get_rules():
    return rules()


@app.put("/api/rules")
def put_rules(body: dict = Body(...)):
    return set_rules(body)


@app.get("/api/search")
def search(q: str):
    q = q.strip().upper()
    if not q:
        return []
    try:
        master = nse.equity_master()
    except Exception:  # noqa: BLE001
        master = []
    exact = [m for m in master if m["symbol"] == q]
    starts = [m for m in master if m["symbol"].startswith(q) and m["symbol"] != q]
    named = [m for m in master if q in m["name"].upper() and m not in starts and m not in exact]
    return (exact + starts + named)[:12]


# ----------------------------------------------------------------------------- push
@app.get("/api/digest")
def digest(hours: float = 24, start: str | None = None, end: str | None = None, fresh: bool = False):
    s, e = window(hours=hours, start=start, end=end)
    return build_digest(s, e, use_cache=not fresh)


@app.post("/api/enrich")
def enrich(body: dict = Body(...)):
    kind = body.get("kind")
    sym = body.get("symbol", "")
    if kind == "results":
        return results_card(sym, body.get("text", ""), body.get("ts"), body.get("url"))
    if kind == "people":
        extra = [u for u in body.get("urls") or [] if u and u != body.get("url")]
        return people_card(sym, body.get("company", sym), body.get("text", ""), body.get("url"), extra)
    if kind == "summary":
        return summary_card(sym, body.get("company", sym), body.get("desc", ""), body.get("text", ""), body.get("url"))
    raise HTTPException(400, "unknown enrichment kind")


@app.post("/api/triage")
def triage(body: dict = Body(...)):
    return set_triage(body["id"], body.get("state"))


@app.get("/api/alerts")
def get_alerts(days: int = 7):
    return alerts(days)


@app.post("/api/alerts/seen")
def alerts_seen():
    return {"seenAt": mark_seen()}


# ----------------------------------------------------------------------------- pull
@app.get("/api/research/{symbol}")
def research_dossier(symbol: str):
    d = research.dossier(symbol)
    if not d:
        raise HTTPException(404, f"No fundamentals found for {symbol.upper()} - is it an NSE symbol?")
    return d


@app.get("/api/research/{symbol}/promises")
def research_promises(symbol: str):
    return analyst.promises(symbol)


@app.get("/api/research/{symbol}/thesis")
def research_thesis(symbol: str):
    return analyst.thesis(symbol)


@app.post("/api/research/{symbol}/ask")
def research_ask(symbol: str, body: dict = Body(...)):
    q = (body.get("q") or "").strip()
    if not q:
        raise HTTPException(400, "empty question")
    return analyst.ask(symbol, q)


# ----------------------------------------------------------------------------- background refresh
def _prewarm(dig: dict) -> None:
    """Pre-compute the slow cards (results tables, bios, filing summaries) for what the brief shows first."""
    for e in (dig.get("top") or []) + (dig.get("watch") or []):
        if not e.get("enrich"):
            continue
        try:
            enrich({"kind": e["enrich"], "symbol": e["symbol"], "company": e.get("company"), "url": e.get("url"),
                    "text": e.get("text") or "", "ts": e.get("ts"), "desc": e.get("desc") or "",
                    "urls": [e.get("url")] + [r.get("url") for r in e.get("related") or []]})
        except Exception as ex:  # noqa: BLE001
            log.info("prewarm skipped %s: %s", e.get("id"), ex)


def _prewarm_research() -> None:
    for sym in RESEARCH_PICKS:
        try:
            if research.dossier(sym):
                analyst.promises(sym)
                analyst.thesis(sym)
        except Exception as ex:  # noqa: BLE001
            log.info("research prewarm skipped %s: %s", sym, ex)


def _refresher():
    """Keeps the 24h digest, its cards, alerts and the research picks warm so the app opens instantly."""
    interval = int(os.getenv("FINN_REFRESH_SECONDS") or 900)
    last_research = 0.0
    while True:
        try:
            llm.warmup()
            s, e = window(hours=24)
            dig = build_digest(s, e, use_cache=False)
            alerts(7)
            _prewarm(dig)
            if time.time() - last_research > 6 * 3600:
                last_research = time.time()
                _prewarm_research()
        except Exception as ex:  # noqa: BLE001
            log.warning("background refresh failed: %s", ex)
        time.sleep(interval)


@app.on_event("startup")
def _start():
    if os.getenv("FINN_NO_BACKGROUND") != "1" and not config.OFFLINE:
        threading.Thread(target=_refresher, daemon=True, name="finn-refresher").start()
    elif llm.enabled:
        threading.Thread(target=llm.warmup, daemon=True, name="finn-llm-probe").start()


# ----------------------------------------------------------------------------- frontend
if config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        root = config.FRONTEND_DIST.resolve()
        f = (root / path).resolve()
        if path and f.is_file() and root in f.parents:
            return FileResponse(f)
        return FileResponse(root / "index.html")
