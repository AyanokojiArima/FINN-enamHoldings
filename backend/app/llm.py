"""LLM router.

* One OpenAI-compatible client (Gemini by default; Groq/OpenRouter work by changing .env).
* Two tiers, routed by *thinking budget* as well as model:
    fast  -> flash-class model with reasoning off: bulk extraction, ~1-5 s, no thinking tokens
    smart -> flash-class model with low reasoning: judgement over small, pre-computed fact packs
* Tiers are *chains*, not single models. Models are discovered from the provider, probed for
  latency in the background, and any 503 / 429 / timeout falls straight through to the next
  model (the failing one sits out for a few minutes). Model churn never breaks the app.
* Every call is cached by content hash and metered so the UI can show what an answer cost.
* Never raises into the product: callers get ``None`` and fall back to deterministic logic.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import httpx

from . import config
from .store import store

log = logging.getLogger("finn.llm")

_SKIP = ("embedding", "tts", "image", "audio", "live", "vision", "robotics", "computer-use", "native", "aqa",
         "learnlm", "nano", "gemma", "imagen", "veo", "transcribe", "translate", "customtools", "omni", "banana")
EFFORT = {"fast": "none", "smart": "low"}
TIMEOUT = {"fast": 60.0, "smart": 120.0}


def _version(model_id: str) -> float:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", model_id)
    return float(m.group(1)) if m else 0.0


def _today() -> str:
    return datetime.now(config.IST).date().isoformat()


def _quality_rank(mid: str) -> tuple:
    m = mid.lower()
    stable = not any(x in m for x in ("preview", "exp"))
    return (stable, _version(m), not m.endswith("-latest"))


def text_models(ids: list[str]) -> list[str]:
    return [i for i in ids if i.lower().startswith("gemini") and "flash" in i.lower()
            and not any(s in i.lower() for s in _SKIP)]


def _parse_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        if not starts:
            raise
        # Parse the first complete JSON value and ignore any trailing chatter.
        value, _ = json.JSONDecoder().raw_decode(text[min(starts):])
        return value


def _unwrap(value: Any) -> Any:
    # Some models wrap the requested object in a one-element array.
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        return value[0]
    return value


class LLM:
    def __init__(self) -> None:
        self._client = httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0))
        self._sem = threading.Semaphore(config.LLM_MAX_CONCURRENCY)
        self._lock = threading.Lock()
        self._ids: list[str] | None = None
        self._health: dict[str, dict] = {}   # model -> {until, dead, lat, no_effort}
        self._probed_at = 0.0
        self.last_error: str | None = None

    # ---- configuration -------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(config.LLM_API_KEY)

    def available(self) -> bool:
        """Configured and still within today's call budget."""
        return self.enabled and self._budget_left()

    def _budget_left(self) -> bool:
        if not config.LLM_DAILY_BUDGET:
            return True
        b = store.state("llm_budget") or {}
        return b.get("day") != _today() or b.get("calls", 0) < config.LLM_DAILY_BUDGET

    def _charge(self) -> None:
        day = _today()
        store.update_state("llm_budget", lambda b: {"day": day, "calls": ((b or {}).get("calls", 0) if (b or {}).get("day") == day else 0) + 1})

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {config.LLM_API_KEY}", "Content-Type": "application/json"}

    def _model_ids(self) -> list[str]:
        if self._ids is not None:
            return self._ids
        ids = store.get("llm_models", config.LLM_BASE_URL, max_age=86400)
        if ids is None:
            try:
                r = self._client.get(config.LLM_BASE_URL + "models", headers=self._headers(), timeout=20)
                r.raise_for_status()
                ids = [m["id"].removeprefix("models/") for m in r.json().get("data", [])]
                store.set("llm_models", config.LLM_BASE_URL, ids)
            except Exception as e:  # noqa: BLE001
                self.last_error = f"model discovery failed: {e}"
                log.warning(self.last_error)
                ids = []
        self._ids = ids
        return ids

    def _ok(self, mid: str) -> bool:
        h = self._health.get(mid, {})
        return not h.get("dead") and h.get("until", 0) < time.time()

    def chain(self, tier: str) -> list[str]:
        """Ordered candidates for a tier: overrides first, then healthy models (fast: by latency,
        smart: by quality), then everything else as a last resort."""
        override = config.LLM_MODEL_FAST if tier == "fast" else config.LLM_MODEL_SMART
        cands = text_models(self._model_ids())
        if not cands and override:
            return [override]
        full = [m for m in cands if "lite" not in m.lower()]
        lite = [m for m in cands if "lite" in m.lower()]
        lat = lambda m: self._health.get(m, {}).get("lat", 999.0)  # noqa: E731
        if tier == "fast":
            # bulk extraction: lite models first (fast, higher free-tier rate limits), keeping the
            # stronger models' quota free for judgement; then full models by measured latency
            ordered = sorted(lite, key=lambda m: (lat(m), -_version(m))) + sorted(full, key=lambda m: (lat(m), -_version(m)))
        else:
            # judgement: best quality among models that answered quickly, then the rest
            quick = [m for m in full if lat(m) <= 15]
            ordered = sorted(quick, key=_quality_rank, reverse=True) + \
                sorted([m for m in full if m not in quick], key=lambda m: (lat(m), -_version(m))) + sorted(lite, key=lat)
        if override:
            ordered = [override] + [m for m in ordered if m != override]
        healthy = [m for m in ordered if self._ok(m)]
        return healthy + [m for m in ordered if m not in healthy]

    def models(self) -> dict[str, str]:
        if not self.enabled:
            return {}
        return {"fast": (self.chain("fast") or ["?"])[0], "smart": (self.chain("smart") or ["?"])[0]}

    # ---- health probe ----------------------------------------------------------
    def warmup(self) -> None:
        """Measure latency of each candidate with a tiny request (background, ~10 tokens each)."""
        if not self.enabled or time.time() - self._probed_at < 3600:
            return
        self._probed_at = time.time()
        cands = text_models(self._model_ids())[:12]

        def probe(mid: str):
            body = {"model": mid, "messages": [{"role": "user", "content": "Reply with OK"}], "max_tokens": 16}
            if "lite" not in mid:
                body["reasoning_effort"] = "none"
            t = time.time()
            try:
                r = self._client.post(config.LLM_BASE_URL + "chat/completions", headers=self._headers(), json=body, timeout=20)
                self._record(mid, r.status_code, time.time() - t, r.text)
            except httpx.HTTPError:
                self._record(mid, 599, time.time() - t, "timeout")

        with ThreadPoolExecutor(6) as ex:
            list(ex.map(probe, cands))
        log.info("LLM probe: %s", {m: (round(h.get("lat", 0), 1), "ok" if self._ok(m) else "down")
                                   for m, h in self._health.items()})

    def _record(self, mid: str, status: int, seconds: float, text: str = "") -> None:
        h = self._health.setdefault(mid, {})
        if status == 200:
            h["lat"] = seconds if "lat" not in h else 0.6 * h["lat"] + 0.4 * seconds
            h["until"] = 0
        elif status == 404:
            h["dead"] = True
        elif status == 400 and "reasoning" in text.lower():
            h["no_effort"] = True
        elif status in (429,):
            daily = re.search(r"per ?day|perday|daily", text, re.I) is not None
            h["until"] = time.time() + (3600 if daily else 65)
        elif status >= 500 or status == 599:
            h["until"] = time.time() + 240
            h["lat"] = 999.0 if status == 599 else max(h.get("lat", 0), 30.0)

    # ---- calls ---------------------------------------------------------------
    def json(self, tier: str, system: str, prompt: str, *, purpose: str, max_tokens: int = 4096,
             temperature: float = 0.1, expect: str | None = None) -> Any | None:
        """Return parsed JSON from the model, or None if the LLM is unavailable/failed.
        `expect` names a top-level key the answer must contain; otherwise the next model in the chain is tried."""
        if not self.enabled:
            return None
        key = hashlib.sha256(f"{tier}\n{system}\n{prompt}".encode()).hexdigest()
        cached = store.get("llm", key)
        if cached is not None:
            self._meter(tier, cached.get("_model", "?") if isinstance(cached, dict) else "?", purpose, cached=True)
            return _unwrap(cached.get("result") if isinstance(cached, dict) and "result" in cached else cached)

        def parse(content: str) -> Any:
            result = _unwrap(_parse_json(content))
            if expect and not (isinstance(result, dict) and result.get(expect) is not None):
                raise ValueError(f"answer lacks '{expect}'")
            return result

        if not self._budget_left():
            self.last_error = "Daily AI budget reached - serving cached and rule-based results until tomorrow"
            return None
        self._charge()
        t0 = time.time()
        data, model, result = self._call(tier, system, prompt, max_tokens, temperature, parse)
        if data is None:
            log.warning("LLM %s gave no usable answer for %s: %s", tier, purpose, self.last_error)
            return None
        self._meter(tier, model, purpose, cached=False, usage=data.get("usage") or {}, ms=int((time.time() - t0) * 1000))
        store.set("llm", key, {"result": result, "_model": model})
        return result

    def _call(self, tier: str, system: str, prompt: str, max_tokens: int, temperature: float, parse):
        url = config.LLM_BASE_URL + "chat/completions"
        if tier == "smart":
            # Judgement quality matters more than a minute of latency: if every full model is only briefly
            # rate-limited, wait for the first one rather than dropping to a lite model.
            full = [m for m in self.chain(tier) if "lite" not in m.lower() and not self._health.get(m, {}).get("dead")]
            if full and not any(self._ok(m) for m in full):
                wait = min(self._health.get(m, {}).get("until", 0) for m in full) - time.time()
                if 0 < wait <= 70:
                    log.info("LLM smart: all full models rate-limited, waiting %.0fs", wait)
                    time.sleep(wait + 1)
        for attempt in range(2):
            for mid in self.chain(tier):
                if attempt == 0 and not self._ok(mid):
                    continue
                h = self._health.get(mid, {})
                body: dict[str, Any] = {
                    "model": mid, "temperature": temperature, "max_tokens": max_tokens,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                }
                if not h.get("no_effort") and "lite" not in mid:
                    body["reasoning_effort"] = EFFORT[tier]
                for _ in range(3):  # same model: only to strip unsupported params
                    t = time.time()
                    with self._sem:
                        try:
                            r = self._client.post(url, headers=self._headers(), json=body, timeout=TIMEOUT[tier])
                        except httpx.HTTPError as e:
                            self._record(mid, 599, time.time() - t)
                            self.last_error = f"{mid}: network/timeout ({e.__class__.__name__})"
                            break
                    if r.status_code == 200:
                        payload = r.json()
                        content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
                        if not content:  # thinking ate the budget: try next model
                            self.last_error = f"{mid}: empty completion"
                            break
                        self._record(mid, 200, time.time() - t)
                        try:
                            result = parse(content)
                        except Exception as e:  # noqa: BLE001 - malformed/incomplete answer: try next model
                            self.last_error = f"{mid}: unusable output ({e.__class__.__name__}: {str(e)[:80]})"
                            log.warning("LLM %s: %s - falling through", tier, self.last_error)
                            break
                        self.last_error = None
                        return payload, mid, result
                    text = r.text[:400]
                    if r.status_code in (401, 403):
                        self.last_error = f"LLM auth failed ({r.status_code}) - check GEMINI_API_KEY"
                        log.error(self.last_error)
                        return None, None, None
                    if r.status_code == 400 and "reasoning_effort" in body:
                        self._health.setdefault(mid, {})["no_effort"] = True
                        body.pop("reasoning_effort")
                        continue
                    if r.status_code == 400 and "response_format" in body:
                        body.pop("response_format")
                        continue
                    self._record(mid, r.status_code, time.time() - t, text)
                    self.last_error = f"{mid}: HTTP {r.status_code}"
                    log.warning("LLM %s failed on %s (HTTP %s) - falling through", tier, mid, r.status_code)
                    break
            time.sleep(3)
        return None, None, None

    # ---- metering ------------------------------------------------------------
    def _meter(self, tier: str, model: str, purpose: str, *, cached: bool, usage: dict | None = None,
               ms: int = 0) -> None:
        usage = usage or {}

        def update(u: dict | None) -> dict:
            u = u or {"calls": 0, "cached": 0, "input_tokens": 0, "output_tokens": 0, "by_purpose": {}, "recent": []}
            p = u["by_purpose"].setdefault(purpose, {"calls": 0, "cached": 0, "tokens": 0, "tier": tier})
            if cached:
                u["cached"] += 1
                p["cached"] += 1
            else:
                u["calls"] += 1
                p["calls"] += 1
                u["input_tokens"] += usage.get("prompt_tokens", 0)
                u["output_tokens"] += usage.get("completion_tokens", 0)
                p["tokens"] += usage.get("total_tokens", 0)
            u["recent"] = ([{"t": time.time(), "tier": tier, "model": model, "purpose": purpose, "cached": cached,
                             "tokens": usage.get("total_tokens", 0), "ms": ms}] + u["recent"])[:40]
            return u

        store.update_state("llm_usage", update)

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "provider": "gemini" if "generativelanguage" in config.LLM_BASE_URL else config.LLM_BASE_URL,
            "models": self.models() if self.enabled else None,
            "chains": {t: self.chain(t)[:4] for t in ("fast", "smart")} if self.enabled else None,
            "health": {m: {"lat": round(h.get("lat", 0), 1), "ok": self._ok(m)} for m, h in self._health.items()},
            "usage": store.state("llm_usage", {}),
            "budget": {"limit": config.LLM_DAILY_BUDGET or None,
                       "used": (lambda b: b.get("calls", 0) if b.get("day") == _today() else 0)(store.state("llm_budget") or {})},
            "lastError": self.last_error,
        }


llm = LLM()
