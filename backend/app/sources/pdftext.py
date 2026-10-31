"""PDF / ZIP attachment -> per-page text, cached. Filings are immutable so we extract once."""
from __future__ import annotations

import io
import logging
import re
import zipfile

import pymupdf

from .. import config
from ..http import http
from ..store import store

log = logging.getLogger("finn.pdf")


def _pdf_bytes_pages(data: bytes, max_pages: int | None = None) -> list[str]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = []
    for i, page in enumerate(doc):
        if max_pages and i >= max_pages:
            break
        pages.append(page.get_text("text"))
    return pages


def document_pages(url: str, max_pages: int | None = None) -> list[str]:
    """Text of each page of a filing (PDF, or PDFs inside a ZIP). Empty list if unreadable."""
    key = f"{url}|{max_pages or 0}"
    cached = store.get("pdftext", key)
    if cached is not None:
        return cached
    try:
        path = http.file(url)
        data = path.read_bytes()
        if not config.KEEP_FILES:
            path.unlink(missing_ok=True)  # the text is cached below; the file itself is no longer needed
        if data[:4] == b"%PDF":
            pages = _pdf_bytes_pages(data, max_pages)
        elif data[:2] == b"PK":
            pages = []
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for name in sorted(zf.namelist()):
                    if name.lower().endswith(".pdf"):
                        pages += _pdf_bytes_pages(zf.read(name), max_pages)
        else:
            pages = []
    except Exception as e:  # noqa: BLE001
        log.warning("could not read %s: %s", url, e)
        return []
    store.set("pdftext", key, pages)
    return pages


def has_text(pages: list[str]) -> bool:
    return sum(len(p.strip()) for p in pages) > 200


def find_pages(pages: list[str], patterns: list[str], top: int = 5) -> list[int]:
    """Indices of the pages that best match the given regex patterns (case-insensitive)."""
    scored = []
    for i, text in enumerate(pages):
        score = sum(len(re.findall(p, text, re.I)) for p in patterns)
        if score:
            scored.append((score, i))
    scored.sort(reverse=True)
    return sorted(i for _, i in scored[:top])
