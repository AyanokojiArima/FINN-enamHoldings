"""Enrichment for pushed filings that need reading: management changes and generic headlines."""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from ..llm import llm
from ..sources.pdftext import document_pages
from ..store import store
from ..util import clip

# --------------------------------------------------------------------------- management change
SYSTEM_PEOPLE = (
    "You read Indian stock-exchange filings about board / key-management changes. Output JSON only. "
    "Use ONLY facts stated in the filing (SEBI requires a 'brief profile' for appointments). Never use outside knowledge.")

PROMPT_PEOPLE = """Company: {company}
Exchange headline: {headline}

Filing text:
{body}

Return JSON:
{{"changes": [{{
  "name": "full name without honorific",
  "role": "designation, e.g. Chief Financial Officer",
  "action": "appointed|resigned|retired|ceased|re-appointed|redesignated|demise|other",
  "effective": "YYYY-MM-DD or null",
  "reason": "short reason if stated, else null",
  "key_role": true if CEO/MD/CFO/Chairperson/Whole-time or Executive Director/President/COO,
  "profile": {{"summary": "<=35 words from the brief profile, or null",
               "prior_roles": ["Organisation - role", "..."],
               "education": ["..."],
               "experience_years": number or null}}
}}],
 "one_line": "<=20 words: what changed and why it matters"}}"""

_HON = r"(?:mr\.?|ms\.?|mrs\.?|dr\.?|shri|smt\.?|prof\.?|ca|cs)\s+"
_NAME = r"([A-Z][A-Za-z\.]+(?:\s+[A-Z][A-Za-z\.]+){0,4})"
_ACTIONS = {"appointment": "appointed", "resignation": "resigned", "cessation": "ceased", "re-appointment": "re-appointed",
            "demise": "demise", "retirement": "retired"}


def linkedin_search(name: str, company: str) -> str:
    return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(f'{name} {company}')}"


def _regex_people(headline: str, body: str) -> list[dict]:
    """No-LLM fallback: 'appointment of Mr X as Y' / 'Mr X has resigned as Y' from headline, then the filing body."""
    out: list[dict] = []
    seen = set()
    profile = None
    pm = re.search(r"brief profile[^\n]*\n(.{80,900}?)(?:\n\s*\n|$)", body, re.I | re.S)
    if pm:
        profile = clip(pm.group(1), 260)
    text = headline + "\n" + re.sub(r"\s+", " ", body[:8000])
    pats = [
        (re.compile(r"(?i:(appointment|resignation|cessation|re-appointment|demise|retirement) of)\s+" + f"(?i:{_HON})?" + _NAME +
                    r"(?:\s*\([^)]{0,40}\))?\s*(?:,|(?i:as))\s+(?i:the\s+|an?\s+)?([A-Za-z&\-\s]{3,70}?)"
                    r"(?=\s+(?i:of the company|w\.?e\.?f|with effect|on|from|effective)|[,\.;]|$)"), "fwd"),
        (re.compile(f"(?i:{_HON})" + _NAME + r",?\s+(?i:the\s+)?([A-Za-z&\-\s]{3,60}?)\s+"
                    r"(?i:(?:of the company\s+)?has\s+(?:tendered|submitted|given)\s+(?:his|her)\s+resignation)"), "resign"),
        (re.compile(f"(?i:{_HON})" + _NAME + r"\s+(?i:(?:has\s+)?(resigned|retired|ceased|stepped down|been appointed|been re-appointed|been designated)"
                    r"\s+(?:from\s+the\s+(?:position|post|office)\s+of|as|to\s+be)\s+(?:the\s+|an?\s+)?)([A-Za-z&\-\s]{3,70}?)(?=[,\.;\(]|\s+(?i:of the company|w\.?e\.?f|with effect|effective))"), "verb"),
    ]
    verb_map = {"resigned": "resigned", "retired": "retired", "ceased": "ceased", "stepped down": "resigned",
                "been appointed": "appointed", "been re-appointed": "re-appointed", "been designated": "redesignated"}
    for pat, kind in pats:
        for m in pat.finditer(text):
            if kind == "fwd":
                action, name, role = _ACTIONS[m.group(1).lower()], m.group(2).strip(), (m.group(3) or "").strip()
            elif kind == "resign":
                action, name, role = "resigned", m.group(1).strip(), (m.group(2) or "").strip()
            else:
                action, name, role = verb_map.get(m.group(2).lower(), "other"), m.group(1).strip(), (m.group(3) or "").strip()
            if len(name.split()) < 2 or name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append({"name": name, "role": role or None, "action": action, "effective": None, "reason": None,
                        "key_role": False,
                        "profile": {"summary": profile if action in ("appointed", "re-appointed") else None,
                                    "prior_roles": [], "education": [], "experience_years": None}})
            if len(out) >= 3:
                return out
    return out


def _cached_card(ns: str, ident: str) -> dict | None:
    """The AI card if we have one (marked as a replay when no key is configured). A rule-based card is reused
    only while the AI can't be used; otherwise we try the AI again rather than pin a degraded answer."""
    hit = store.get(ns, "llm|" + ident)
    if hit is not None:
        return hit if llm.enabled else {**hit, "replay": True}
    return None if llm.available() else store.get(ns, "rules|" + ident)


def people_card(symbol: str, company: str, headline: str, url: str | None, extra_urls: list[str] | None = None) -> dict:
    ident = url or f"{symbol}|{headline}"
    cached = _cached_card("people", ident)
    if cached is not None:
        return cached
    body = ""
    for u in [url, *(extra_urls or [])][:3]:
        if u:
            body += "\n".join(document_pages(u, max_pages=15)) + "\n\n"
    changes, one_line, method = None, None, "rules"
    if llm.enabled and (body.strip() or headline):
        res = llm.json("fast", SYSTEM_PEOPLE, PROMPT_PEOPLE.format(company=company, headline=headline, body=body[:16000]),
                       purpose="mgmt_change_extract", max_tokens=2000, expect="changes")
        if isinstance(res, dict) and isinstance(res.get("changes"), list):
            src = (headline + "\n" + body).lower()
            # anti-hallucination: keep people whose surname actually appears in the filing
            changes = [c for c in res["changes"] if c.get("name") and c["name"].split()[-1].lower() in src]
            one_line = res.get("one_line")
            method = "llm"
    if not changes:
        changes = _regex_people(headline, body)
        method = "rules"
    for c in changes:
        c["linkedin"] = linkedin_search(c["name"], company)
    card = {"kind": "people", "symbol": symbol, "changes": changes, "oneLine": one_line, "method": method,
            "source": {"url": url}}
    store.set("people", f"{method}|{ident}", card)
    return card


# --------------------------------------------------------------------------- generic summary
SYSTEM_SUM = ("You summarise Indian stock-exchange filings for a busy portfolio manager. Output JSON only. "
              "Use only the filing text. Be specific: numbers, counterparties, dates. No fluff.")

PROMPT_SUM = """Company: {company}
Exchange category: {desc}
Exchange headline: {headline}

Filing text:
{body}

Return JSON: {{"summary": "<=28 words, the specific news", "signal": "positive|negative|neutral",
"materiality": 1-5 (5 = could move the stock), "amount_crore": number or null}}"""


def summary_card(symbol: str, company: str, desc: str, headline: str, url: str | None) -> dict:
    ident = url or f"{symbol}|{headline}"
    cached = _cached_card("summary", ident)
    if cached is not None:
        return cached
    pages = document_pages(url, max_pages=6) if url else []
    body = "\n".join(pages)
    card = {"kind": "summary", "symbol": symbol, "summary": None, "signal": "neutral", "materiality": None,
            "method": "rules", "source": {"url": url}}
    if llm.enabled and body.strip():
        res = llm.json("fast", SYSTEM_SUM, PROMPT_SUM.format(company=company, desc=desc, headline=headline,
                                                            body=body[:9000]), purpose="filing_summary", max_tokens=400,
                       expect="summary")
        if isinstance(res, dict) and res.get("summary"):
            card.update(summary=res["summary"], signal=res.get("signal") or "neutral",
                        materiality=res.get("materiality"), amount=res.get("amount_crore"), method="llm")
    if not card["summary"] and body:
        card["summary"] = _extractive_summary(body)
    store.set("summary", f"{card['method']}|{ident}", card)
    return card


_ABBR = re.compile(r"\b(i\.e|e\.g|ltd|no|nos|rs|mr|ms|mrs|dr|co|pvt|viz|etc|w\.e\.f|approx|inc|sr|jr|st|regn|reg)\.", re.I)
_NOISE = re.compile(r"registered office|regd\.? office|corporate office|\bcin\b|tel\s*[:.]|fax|e-?mail|www\.|website|"
                    r"exchange plaza|dalal street|phiroze|bandra|scrip code|symbol\s*:|^to,?$|listing department|"
                    r"dear sir|dear madam|yours (faithfully|truly|sincerely)|thanking you|company secretary", re.I)


def _extractive_summary(body: str) -> str:
    """No-LLM fallback: the letter's subject line if specific, else its first substantive sentence."""
    from .digest import _SUBJECT, clean_headline  # local import to avoid a cycle
    m = _SUBJECT.search(body[:3000])
    if m:
        subj = clean_headline(re.sub(r"\s+", " ", m.group(1)), "")
        if subj and not re.search(r"^(intimation|disclosure) under regulation \d+\s*$", subj, re.I):
            return clip(subj, 200)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip() and not _NOISE.search(ln)]
    text = re.sub(r"\s+", " ", " ".join(lines))
    text = _ABBR.sub(lambda x: x.group(0).replace(".", "§"), text)
    for s in re.split(r"(?<=[.!?])\s+", text):
        s = s.replace("§", ".").strip()
        s = re.sub(r"^(we\s+)?(?:wish to|are pleased to|hereby|would like to)\s+inform\s+(?:you\s+)?(?:that\s+)?", "", s, flags=re.I)
        s = re.sub(r"^(in terms of|pursuant to|further to)[^,]{0,160},\s*", "", s, flags=re.I)
        if 60 <= len(s) <= 400 and not re.match(r"(this is for your|kindly take|please take|request you to)", s, re.I):
            return clip(s[0].upper() + s[1:], 220)
    return clip(text, 200)
