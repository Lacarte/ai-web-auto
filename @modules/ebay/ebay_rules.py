"""eBay decision rules as pure functions (no browser): exclusion, prices, landed cost, seller trust.

knowledge/rules.md describes every rule here; change both together.
"""
from __future__ import annotations

import re
from typing import Any

# Hard exclusion: title or condition says the item is not a working, complete product.
EXCLUDE_TITLE = re.compile(
    r"for\s*parts|parts\s*only|parts\s*/\s*repair|not\s*working|non[-\s]?working|does\s*n[o']t\s*work|"
    r"\bbroken\b|\bdefective\b|\bfaulty\b|\bdead\b|\bas[-\s]is\b|for\s*repair|\bbox\s*only\b|empty\s*box|"
    r"\bno\s*(display|video|signal|post)\b|\bartifact(s|ing)?\b|\bread\s*(the\s*)?desc|\bbad\b|\bcracked\b|"
    r"\buntested\b|\bscratch\s*and\s*dent\b|\bmissing\b|\bfail(ed|ing|s)?\b|\bdamaged?\b|"
    r"\b(fan|cooler|heat\s*sink|heatsink|bracket|shroud|backplate)\s*only\b|\bno\s*(processor|cpu|gpu)\b",
    re.I)
EXCLUDE_CONDITION = re.compile(r"parts|not working", re.I)
# In a seller's description, the same words are often boilerplate ("returns accepted if not working"),
# so they flag the listing for review instead of excluding it.
DESC_RISK = re.compile(
    r"for\s*parts|parts\s*only|not\s*working|does\s*n[o']t\s*work|doesn't\s*work|no\s*(display|video|signal|post)|"
    r"artifact|\bas[-\s]is\b|no\s*returns|untested|not\s*tested|unable\s*to\s*test|missing|crack|damage|"
    r"bent\s*pin|no\s*cables?|cables?\s*not\s*included|without\s*cables?|mining|\bmined\b|stock\s*photo",
    re.I)
TESTED = re.compile(r"\btested\b|fully\s*functional|works\s*(great|perfectly|100%)|stress\s*test|benchmark|"
                    r"furmark|memtest|3dmark|cinebench|occt|working\s*pull|pulled\s*from\s*working", re.I)

FL_SALES_TAX = 0.07  # 6% Florida + 1% Miami-Dade surtax; eBay collects it as marketplace facilitator


def money(text: str | None) -> float | None:
    m = re.search(r"\$\s?([\d,]+(?:\.\d{2})?)", text or "")
    return float(m.group(1).replace(",", "")) if m else None


def exclusion_reason(title: str, condition: str | None) -> str | None:
    """Why a listing is excluded outright (None = keep)."""
    label = (condition or "").strip().splitlines()[0] if condition else ""
    if label and EXCLUDE_CONDITION.search(label):
        return f"condition '{label}'"
    m = EXCLUDE_TITLE.search(title or "")
    return f"title says '{m.group(0)}'" if m else None


def description_flags(desc: str) -> list[str]:
    """Risky phrases in the description, each with a little context for the human to judge."""
    out = []
    for m in DESC_RISK.finditer(desc or ""):
        a, b = max(0, m.start() - 50), min(len(desc), m.end() + 50)
        out.append(re.sub(r"\s+", " ", desc[a:b]).strip())
    return out[:8]


def tested_evidence(*texts: str) -> list[str]:
    hits = []
    for t in texts:
        for m in TESTED.finditer(t or ""):
            a, b = max(0, m.start() - 40), min(len(t), m.end() + 60)
            hits.append(re.sub(r"\s+", " ", t[a:b]).strip())
    return hits[:5]


def seller_stats(text: str) -> dict[str, Any]:
    """'L Theory Inc (8956) 99.8% positive' -> count, percent."""
    pct = re.search(r"([\d.]+)%\s*positive", text or "")
    count = re.search(r"\(([\d,.]+)\s*([KkMm])?\)", text or "")
    n = None
    if count:
        n = float(count.group(1).replace(",", ""))
        n = int(n * {"k": 1e3, "m": 1e6}.get((count.group(2) or "").lower(), 1))
    return {"feedback_pct": float(pct.group(1)) if pct else None, "feedback_count": n}


def return_days(text: str) -> int | None:
    if re.search(r"no returns|does not accept returns|seller does not accept", text or "", re.I):
        return 0
    m = re.search(r"(\d+)\s*days?\s*returns?", text or "", re.I)
    return int(m.group(1)) if m else None


def shipping_cost(text: str) -> float | None:
    """Shipping to the configured ZIP from the shipping section; None when not shown."""
    if not text:
        return None
    if re.search(r"does not ship|doesn't ship|cannot ship|not available", text, re.I):
        return float("inf")
    # the "Shipping:" line decides; a "Free local pickup" line above it is not free shipping (live 2026-10-29: item
    # 158164238777 read as free, the page said US $25.43 FedEx)
    line = re.search(r"Shipping:\s*([^\n]{0,120})", text)
    part = line.group(1) if line else re.sub(r"Free local pickup[^\n]*", "", text, flags=re.I)
    if re.match(r"\s*Free\b", part, re.I) or re.search(r"Free (shipping|delivery|standard|economy|USPS|UPS|FedEx)", part, re.I):
        return 0.0
    m = re.search(r"(?:US\s*)?\$\s?([\d,]+\.\d{2})", part)
    return float(m.group(1).replace(",", "")) if m else money(part)


def landed_cost(price: float | None, shipping: float | None, tax_rate: float = FL_SALES_TAX) -> float | None:
    """Price + shipping + estimated sales tax. None when the price or the shipping is unknown."""
    if price is None or shipping is None or shipping == float("inf"):
        return None
    return round((price + shipping) * (1 + tax_rate), 2)


def trust(listing: dict[str, Any]) -> dict[str, Any]:
    """Seller/listing trust score 0-100 with the reasons; 'reject' for hard risk."""
    score, why, reject = 50, [], []
    pct, n = listing.get("feedback_pct"), listing.get("feedback_count")
    if pct is None or n is None:
        reject.append("seller feedback not shown")
    else:
        if n < 25:
            reject.append(f"only {n} feedback ratings")
        elif n < 100:
            score -= 10; why.append(f"few ratings ({n})")
        elif n >= 1000:
            score += 10; why.append(f"{n} ratings")
        if pct < 97:
            reject.append(f"{pct}% positive")
        elif pct < 98.5:
            score -= 10; why.append(f"{pct}% positive")
        elif pct >= 99.5:
            score += 10; why.append(f"{pct}% positive")
    days = listing.get("return_days")
    if days == 0:
        score -= 20; why.append("no returns (only eBay Money Back Guarantee)")
    elif days and days >= 30:
        score += 10; why.append(f"{days}-day returns" + (" (free)" if listing.get("free_returns") else ""))
    elif days:
        why.append(f"{days}-day returns")
    if listing.get("top_rated"):
        score += 5; why.append("Top Rated Plus")
    if listing.get("photos", 0) < 3:
        score -= 10; why.append(f"only {listing.get('photos', 0)} photo(s)")
    if listing.get("tested"):
        score += 10; why.append("testing described")
    if listing.get("auction_only"):
        reject.append("auction only - current bid is not a purchase price")
    if listing.get("desc_flags"):
        score -= 5 * min(3, len(listing["desc_flags"])); why.append(f"{len(listing['desc_flags'])} risk phrase(s) in description")
    if listing.get("mismatches"):
        score -= 15; why.append("listing inconsistencies: " + "; ".join(listing["mismatches"]))
    loc = listing.get("location") or ""
    if loc and "United States" not in loc:
        score -= 10; why.append(f"ships from {loc}")
    return {"score": max(0, min(100, score)), "why": why, "reject": reject}
