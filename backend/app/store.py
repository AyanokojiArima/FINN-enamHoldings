"""Tiny SQLite-backed cache + app state. One file, no server, safe across threads."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from .config import DB_PATH


class Store:
    def __init__(self, path=DB_PATH):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cache (ns TEXT, key TEXT, value TEXT, created REAL, PRIMARY KEY (ns, key))"
        )
        self._db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")

    # ---- cache -------------------------------------------------------------
    def get(self, ns: str, key: str, max_age: float | None = None) -> Any | None:
        """Return cached JSON value, or None if missing / older than max_age seconds."""
        hit = self.get_with_age(ns, key)
        if hit is None:
            return None
        value, age = hit
        if max_age is not None and age > max_age:
            return None
        return value

    def get_with_age(self, ns: str, key: str) -> tuple[Any, float] | None:
        with self._lock:
            row = self._db.execute("SELECT value, created FROM cache WHERE ns=? AND key=?", (ns, key)).fetchone()
        if not row:
            return None
        return json.loads(row[0]), time.time() - row[1]

    def set(self, ns: str, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO cache (ns, key, value, created) VALUES (?, ?, ?, ?)",
                (ns, key, payload, time.time()),
            )

    def delete_ns(self, ns: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM cache WHERE ns=?", (ns,))

    def scan(self, ns: str, like: str) -> list[tuple[str, Any, float]]:
        """All (key, value, created) entries in a namespace whose key matches a SQL LIKE pattern."""
        with self._lock:
            rows = self._db.execute("SELECT key, value, created FROM cache WHERE ns=? AND key LIKE ?", (ns, like)).fetchall()
        return [(k, json.loads(v), c) for k, v, c in rows]

    # ---- state -------------------------------------------------------------
    def state(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, json.dumps(value, default=str))
            )

    def update_state(self, key: str, fn, default: Any = None) -> Any:
        with self._lock:
            value = fn(self.state(key, default))
            self.set_state(key, value)
            return value


store = Store()
