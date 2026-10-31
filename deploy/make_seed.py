"""Snapshot the local cache into deploy/seed.db for the container image.

Keeps what makes a fresh instance fast and useful: cached AI answers, filing text, exchange data and prices
(the exchange data also backs the "latest data we hold" fallback if a cloud host can't reach NSE).
Drops personal state (usage meter, triage, alert marks, portfolio edits) and short-lived entries.
"""
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "backend" / "data" / "finn.db"
OUT = HERE / "seed.db"

if not SRC.exists():
    sys.exit(f"No cache at {SRC} - run FINN locally once first.")

tmp = OUT.with_suffix(".tmp")
tmp.unlink(missing_ok=True)
src, dst = sqlite3.connect(SRC), sqlite3.connect(tmp)
src.backup(dst)  # consistent snapshot even while a local server is writing
src.close()
dst.execute("PRAGMA journal_mode=DELETE")
dst.execute("DELETE FROM state")
dst.execute("DELETE FROM cache WHERE ns IN ('digest', 'llm_models')")
dst.commit()
dst.execute("VACUUM")
counts = dict(dst.execute("SELECT ns, COUNT(*) FROM cache GROUP BY ns ORDER BY ns").fetchall())
dst.close()
tmp.replace(OUT)
print(f"seed: {OUT} ({OUT.stat().st_size / 1e6:.1f} MB) {counts}")
