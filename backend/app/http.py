"""Shared HTTP client: polite per-host pacing, retries, response caching and
stale-on-error fallback so the demo keeps working when a source blocks us."""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import FILES_DIR, OFFLINE, USER_AGENT
from .store import store

log = logging.getLogger("finn.http")

# Minimum seconds between requests to the same host (be a good citizen).
HOST_INTERVAL = {
    "www.nseindia.com": 0.35,
    "www.screener.in": 1.2,
    "query1.finance.yahoo.com": 0.15,
}


class FetchError(RuntimeError):
    pass


class Http:
    def __init__(self) -> None:
        self.client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=httpx.Timeout(45.0, connect=15.0),
            follow_redirects=True,
        )
        self._host_locks: dict[str, threading.Lock] = {}
        self._last_call: dict[str, float] = {}
        self._meta_lock = threading.Lock()
        self._file_locks: dict[str, threading.Lock] = {}

    def _pace(self, host: str) -> threading.Lock:
        with self._meta_lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def get(self, url: str, headers: dict | None = None, retries: int = 3) -> httpx.Response:
        if OFFLINE:
            raise FetchError("offline mode")
        host = urlparse(url).netloc
        interval = HOST_INTERVAL.get(host, 0.0)
        last_exc: Exception | None = None
        for attempt in range(retries):
            with self._pace(host):
                wait = self._last_call.get(host, 0) + interval - time.time()
                if wait > 0:
                    time.sleep(wait)
                self._last_call[host] = time.time()
            try:
                r = self.client.get(url, headers=headers)
                if r.status_code == 200:
                    return r
                if r.status_code in (404, 410):
                    raise FetchError(f"{r.status_code} for {url}")
                last_exc = FetchError(f"HTTP {r.status_code} for {url}")
            except httpx.HTTPError as e:
                last_exc = e
            except FetchError:
                raise
            time.sleep(0.8 * (2**attempt))
        raise FetchError(str(last_exc))

    # ---- cached helpers ------------------------------------------------------
    def _cached(self, ns: str, url: str, ttl: float, fetch, stale_ok: bool = True) -> Any:
        hit = store.get_with_age(ns, url)
        if hit is not None and hit[1] <= ttl:
            return hit[0]
        try:
            value = fetch()
        except Exception as e:  # noqa: BLE001 - any failure falls back to stale cache
            if hit is not None and stale_ok:
                log.warning("using stale cache for %s (%s)", url, e)
                return hit[0]
            raise FetchError(str(e)) from e
        store.set(ns, url, value)
        return value

    def json(self, url: str, ns: str, ttl: float, headers: dict | None = None, stale_ok: bool = True) -> Any:
        return self._cached(ns, url, ttl, lambda: self.get(url, headers=headers).json(), stale_ok)

    def text(self, url: str, ns: str, ttl: float, headers: dict | None = None) -> str:
        return self._cached(ns, url, ttl, lambda: self.get(url, headers=headers).text)

    def file(self, url: str, headers: dict | None = None) -> Path:
        """Download once, keep forever (filings never change)."""
        digest = hashlib.sha1(url.encode()).hexdigest()
        suffix = Path(urlparse(url).path).suffix.lower()[:5] or ".bin"
        path = FILES_DIR / f"{digest}{suffix}"
        if path.exists() and path.stat().st_size > 0:
            return path
        with self._meta_lock:
            lock = self._file_locks.setdefault(digest, threading.Lock())
        with lock:  # one download per file even when several threads ask at once
            if path.exists() and path.stat().st_size > 0:
                return path
            r = self.get(url, headers=headers)
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(r.content)
            tmp.replace(path)
        return path


http = Http()
