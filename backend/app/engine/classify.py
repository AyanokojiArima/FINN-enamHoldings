"""Announcement classifier.

Deterministic on purpose: NSE already tags every filing with a controlled-vocabulary subject
("Outcome of Board Meeting", "Credit Rating", ...) and the company writes a one-line summary.
Rules over those two fields classify ~95% of filings for free, instantly and explainably.
The LLM is only used later to *read* the few filings that end up mattering.
"""
from __future__ import annotations

import re

CATEGORIES = {
    "results": "Results",
    "mna": "M&A",
    "fund_raise": "Fund raise",
    "management_change": "Management",
    "litigation": "Litigation / regulatory",
    "insider_trading": "Insider trades",
    "earnings_call": "Earnings call",
    "investor_meetings": "Investor meetings",
    "credit_rating": "Credit rating",
    "dividend": "Dividend",
    "bonus_split": "Bonus / split",
    "capex": "Capex",
    "press_release": "Press release",
    "postal_ballot": "Postal ballot",
    "agm_egm": "AGM / EGM",
    "pledging": "Pledging",
    "unclassified": "Routine",
}

KEY_ROLE = r"\b(ceo|chief executive|managing director|\bmd\b|cfo|chief financial|chair(man|person)|whole[- ]?time director|executive director|(?<!vice )president|coo|chief operating|joint managing)\b"


def _c(category: str, subtype: str, score: int, why: str) -> dict:
    return {"category": category, "subtype": subtype, "score": score, "why": why}


def _money_crore(text: str) -> float | None:
    """Largest ₹ amount mentioned in crore (handles 'Rs. 300 Crore', 'INR 1,250 crores')."""
    best = None
    for m in re.finditer(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr\b|lakh|million|billion|mn|bn)", text, re.I):
        v = float(m.group(1).replace(",", ""))
        unit = m.group(2).lower()
        v = v * {"lakh": 0.01, "million": 0.1, "mn": 0.1, "billion": 100, "bn": 100}.get(unit, 1)
        best = max(best or 0, v)
    return best


def classify(desc: str, text: str) -> dict:
    d = (desc or "").lower()
    t = (text or "").lower()
    both = f"{d} | {t}"

    def has(pattern: str, s: str = both) -> bool:
        return re.search(pattern, s) is not None

    # ---- routine paperwork first (cheap, very common) --------------------------------------
    if has(r"trading window|depositories and participants|loss of share certificate|duplicate share|"
           r"statement of deviation|monitoring agency|compliance certificate|business responsibility|"
           r"\besg\b|registrar|share transfer agent|investor grievance|integrated filing- governance", d):
        return _c("unclassified", "compliance", 3, "Routine compliance filing")
    if has(r"regulation 57|reg\.? ?57\b|payment of interest|interest payment|redemption of (non-convertible|ncd|debenture)|"
           r"regulation 8\(2\)|fair disclosure|code of (practices|conduct)"):
        return _c("unclassified", "compliance", 4, "Routine compliance filing")
    if has(r"\besop\b|\besos\b|\besps\b|employee stock|stock options?|restricted stock unit|\brsus?\b|options to (the )?(eligible )?employees", both) and not has(r"result"):
        return _c("unclassified", "esop", 4, "ESOP allotment")
    if has(r"strike[- ]off|struck off|dormant|voluntary liquidation|winding up of .*subsidiary"):
        return _c("unclassified", "corporate", 10, "Subsidiary housekeeping")
    if has(r"incorporat(e|ed|ion) (of )?(a |an )?(new )?(wholly[- ]owned |step[- ]down )?subsidiary"):
        return _c("unclassified", "corporate", 15, "New subsidiary incorporated")
    if has(r"initial public offer|\bipo\b|listing of (equity )?shares|trading in (the )?equity shares of .{3,80} (will|shall) commence"):
        return _c("mna", "listing", 80, "Subsidiary IPO / listing - value unlock")
    if has(r"committee", t) and has(r"composition|reconstitut|designation|chairperson|member", t) and not has(KEY_ROLE, t):
        return _c("unclassified", "corporate", 8, "Board committee reshuffle")

    # ---- results family ------------------------------------------------------------------
    if has(r"newspaper|publication", d):
        if has(r"result", t):
            return _c("results", "newspaper", 4, "Newspaper ad of results already filed")
        return _c("unclassified", "newspaper", 3, "Newspaper advertisement")
    if has(r"integrated filing- financial|financial result", d) or (
        has(r"outcome of board meeting|general updates|^updates$", d) and has(r"financial results?|results for the (quarter|period)", t)
        and not has(r"to consider|will be held|is scheduled|prior intimation|intimation of board", t)):
        return _c("results", "filed", 92, "Quarterly results filed")
    if has(r"board meeting intimation|intimation of board meeting", both) or (
        has(r"board meeting", both) and has(r"to consider|will be held|is scheduled|scheduled to be held", t)):
        if has(r"result", t):
            return _c("results", "intimation", 18, "Results date announced")
        return _c("unclassified", "board_intimation", 10, "Board meeting scheduled")
    if has(r"investor presentation", d) or (has(r"presentation", t) and has(r"analyst|investor|earnings|result", t)
                                             and not has(r"transcript|recording", t)):
        return _c("results", "deck", 38, "Results / investor presentation")

    # ---- earnings call & investor meetings ------------------------------------------------
    if has(r"analysts/institutional investor meet|con\. call|earnings call|conference call", both):
        if has(r"transcript", t):
            return _c("earnings_call", "transcript", 45, "Earnings-call transcript is out")
        if has(r"record|audio|video|link of", t):
            return _c("earnings_call", "recording", 12, "Call recording posted")
        if has(r"conference call|earnings call|con\. ?call|concall", t) and not has(r"one[- ]on[- ]one|group meet", t):
            return _c("earnings_call", "notice", 16, "Earnings call scheduled")
        return _c("investor_meetings", "meet", 8, "Investor / analyst meeting")

    # ---- press releases & orders --------------------------------------------------------------
    if has(r"press release", d) and has(r"result|\bq[1-4]\s?fy|\bh[12]\s?fy|\bpat\b|net profit|ebitda|quarter ended", t):
        return _c("results", "press", 55, "Results press release")
    if has(r"monthly business update|business update|operational update|sales (volume|figures|numbers)", both):
        return _c("press_release", "business_update", 40, "Business / operational update")
    if has(r"strikes?/lockouts?|lockout|disturbance|fire at|accident|shutdown|force majeure", both):
        return _c("press_release", "disruption", 64, "Operational disruption")
    if has(r"granting/withdrawal|licen[cs]e|approval from|usfda|us fda|eir\b|warning letter|form 483", both):
        if has(r"withdraw|cancel|suspen|revoc|warning letter|form 483|import alert|oai\b", t):
            return _c("litigation", "regulatory", 72, "Licence / regulatory setback")
        return _c("press_release", "approval", 38, "Regulatory approval / licence")
    if has(r"bagging|receiving of orders", d) or has(r"\b(bags?|bagged|secures?d?|wins?|won|receives?d?)\b.*\b(order|contract)", t):
        amt = _money_crore(text)
        return _c("press_release", "order", 58 if (amt or 0) >= 500 else 48, "Order win" + (f" (₹{amt:,.0f} Cr)" if amt else ""))

    # ---- people ---------------------------------------------------------------------------------
    if has(r"auditor", both) and has(r"resign|cessation|vacat", both):
        return _c("management_change", "auditor_exit", 85, "Auditor resigned - governance red flag")
    if has(r"change in auditors?|appointment of (statutory |secretarial |internal )?auditor", both):
        return _c("unclassified", "auditor_appointment", 8, "Auditor appointment")
    if has(r"change in management|appointment|resignation|cessation|change in director|kmp|demise|"
           r"re-?appointment|retire", d) or has(r"\b(appoint(ed|ment)|resign(ed|ation)|cessation)\b", t):
        exit_ = has(r"resign|cessation|demise|retire|step(s|ped)? down", both)
        if has(r"subsidiary", t) and not has(r"of the company\b.{0,40}(ceo|cfo|managing director)", t):
            return _c("management_change", "subsidiary", 25, "Board change at a subsidiary")
        if has(r"independent director", t) and not has(r"as (the )?(ceo|cfo|managing director|chair(man|person) of the (company|board))", t):
            return _c("management_change", "independent_director", 40, "Independent director change")
        if has(KEY_ROLE, t):
            return _c("management_change", "key_exit" if exit_ else "key_appointment", 92 if exit_ else 86,
                      "Top-management " + ("exit" if exit_ else "appointment"))
        if has(r"company secretary|compliance officer", t):
            return _c("management_change", "cs", 22, "Company secretary change")
        if has(r"senior management|\bsmp\b|vice[- ]president|\bsvp\b|\bevp\b|\bhead\b|chief (?!executive|financial|operating)\w+ officer|\bcto\b|\bchro\b|\bcro\b", t):
            return _c("management_change", "smp", 45, "Senior management change")
        return _c("management_change", "unknown_role", 60, "Management change")

    # ---- deals, money, ratings ---------------------------------------------------------------------
    if has(r"acquisition|amalgamation|merger|scheme of arrangement|sale or disposal|disinvestment|"
           r"joint venture|demerger|takeover|open offer|restructuring", both):
        amt = _money_crore(text)
        small_internal = has(r"wholly[- ]owned subsidiary|\bwos\b", t) and (amt is None or amt < 250)
        if small_internal:
            return _c("mna", "subsidiary_investment", 40, "Investment in own subsidiary")
        return _c("mna", "deal", 82, "Acquisition / merger / divestment" + (f" (₹{amt:,.0f} Cr)" if amt else ""))
    if has(r"buy ?back", both):
        return _c("fund_raise", "buyback", 78, "Share buyback")
    if has(r"qualified institutional|\bqip\b|preferential|rights issue|issue of securities|fund rais|raising of funds|"
           r"commercial paper|debenture|\bncd", both) or (has(r"allotment of securities", d)):
        return _c("fund_raise", "raise", 62, "Fund raise / allotment")
    if has(r"credit rating|rating", d):
        if has(r"downgrad", t):
            return _c("credit_rating", "downgrade", 88, "Credit rating downgrade")
        if has(r"upgrad", t):
            return _c("credit_rating", "upgrade", 60, "Credit rating upgrade")
        if has(r"re-?affirm", t):
            return _c("credit_rating", "reaffirm", 14, "Credit rating reaffirmed")
        return _c("credit_rating", "unknown", 34, "Credit rating action")
    if has(r"bonus|stock split|sub-?division", both):
        return _c("bonus_split", "corporate_action", 64, "Bonus / stock split")
    if has(r"dividend", both):
        return _c("dividend", "declared", 32, "Dividend")
    if has(r"record date", d):
        return _c("dividend", "record_date", 20, "Record date")

    # ---- capex, legal, holdings -------------------------------------------------------------------
    if has(r"capacity addition|commencement of commercial|expansion|new plant|capex|greenfield|brownfield", both):
        return _c("capex", "capacity", 58, "Capacity / capex")
    if has(r"insolvency|cirp|\bnclt\b|fraud|wilful defaulter|default(ed)? (in|on) (the )?(payment|repayment|interest)|"
           r"search (and|&) seizure|\braid\b|forensic", both):
        return _c("litigation", "severe", 88, "Insolvency / fraud / default / search")
    if has(r"litigation|dispute|action\(s\) (taken|initiated)|order(s)? passed|penalty|\bfines?\b|show cause|demand notice|"
           r"\bgst\b|income[- ]tax|regulator|sebi order", both):
        amt = _money_crore(text)
        return _c("litigation", "order", 70 if (amt or 0) >= 100 else 55, "Regulatory / legal action" + (f" (₹{amt:,.0f} Cr)" if amt else ""))
    if has(r"pledg|encumbr|invocation", both):
        if has(r"invocation|invoked", both):
            return _c("pledging", "invocation", 85, "Pledge invoked")
        if has(r"release|revoc", both):
            return _c("pledging", "release", 30, "Pledge released")
        return _c("pledging", "created", 60, "Promoter pledge created")
    if has(r"takeover regulations|insider trading|\bsast\b|reg(ulation)?\.? ?29|reg(ulation)?\.? ?7\(2\)", both):
        return _c("insider_trading", "disclosure", 34, "Promoter / insider holding change")

    # ---- meetings & votes -------------------------------------------------------------------------
    if has(r"postal ballot", both):
        return _c("postal_ballot", "ballot", 9, "Postal ballot")
    if has(r"shareholders meeting|general meeting|\bagm\b|\begm\b|scrutini[sz]er|voting result", both):
        return _c("agm_egm", "meeting", 9, "AGM / EGM")

    # ---- generic news ---------------------------------------------------------------------------
    if has(r"clarification|rumou?r|spurt|news item|media report", both):
        return _c("press_release", "clarification", 58, "Clarification on news / price move")
    if has(r"press release|product launch|launch|partnership|agreement|mou|memorandum", both):
        return _c("press_release", "news", 36, "Company news")
    if has(r"outcome of board meeting", d):
        return _c("press_release", "board_outcome", 34, "Board meeting outcome")
    if has(r"guarantee|indemnity|surety|amendment to (aoa|moa)|change in registered office", both):
        return _c("unclassified", "corporate", 12, "Corporate housekeeping")
    return _c("unclassified", "other", 12, "Other update")
