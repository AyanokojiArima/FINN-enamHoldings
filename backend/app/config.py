from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("FINN_DATA_DIR") or BASE_DIR / "data")
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "finn.db"
FRONTEND_DIST = Path(os.getenv("FINN_FRONTEND_DIST") or BASE_DIR.parent / "frontend" / "dist")
for _d in (DATA_DIR, FILES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Any OpenAI-compatible endpoint works; Gemini is the default because its free tier
# has long context (annual reports) and generous limits.
LLM_API_KEY = (os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
LLM_BASE_URL = (os.getenv("LLM_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai/").rstrip("/") + "/"
LLM_MODEL_FAST = (os.getenv("LLM_MODEL_FAST") or "").strip()
LLM_MODEL_SMART = (os.getenv("LLM_MODEL_SMART") or "").strip()
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY") or 2)
# Cap on fresh (uncached) model calls per IST day; 0 = unlimited. Keeps a public demo link from draining the key's quota.
LLM_DAILY_BUDGET = int(os.getenv("LLM_DAILY_BUDGET") or 0)

# FINN_OFFLINE=1 serves everything from the local cache (useful for demos without network).
OFFLINE = os.getenv("FINN_OFFLINE") == "1"
# Expensive requests (research, enrichment, forced refresh) allowed per client IP per 10 minutes; 0 = no limit.
RATE_LIMIT = int(os.getenv("FINN_RATE_LIMIT") or 0)
# Keep downloaded filings on disk after extracting their text (set 0 on hosts with an in-memory filesystem).
KEEP_FILES = os.getenv("FINN_KEEP_FILES", "1") != "0"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
IST = timezone(timedelta(hours=5, minutes=30))
