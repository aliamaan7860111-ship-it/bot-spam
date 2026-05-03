"""Extract a Filex-spec city name from a free-text UAE address."""

import re
from rapidfuzz import fuzz

# Canonical key = exact spelling Filex's API expects.
# Values = lowercase aliases customers commonly write.
ALIAS_TO_FILEX: dict[str, list[str]] = {
    "Dubai":          ["dubai", "dxb", "doboi", "duba", "dubaai", "dubia"],
    "Abu Dhabi":      ["abu dhabi", "abudhabi", "abu-dhabi", "auh", "abudabi", "abu dabi"],
    "Sharjah":        ["sharjah", "shj", "sharja"],
    "Ajman":          ["ajman", "ajm", "ajaman"],
    "Al Ain":         ["al ain", "alain", "al-ain", "aln"],
    "Fujeriah":       ["fujairah", "fujarah", "fujaira", "fuj", "fujeriah"],
    "Um Al Qwain":    ["umm al quwain", "um al qwain", "uaq", "umalquwain"],
    "Ras Al Khaimah": ["ras al khaimah", "ras al-khaimah", "rak", "raskh"],
}

# Build flat list of (alias, filex_name) pairs for ordered scanning.
_FLAT_ALIASES: list[tuple[str, str]] = [
    (alias, filex_name)
    for filex_name, aliases in ALIAS_TO_FILEX.items()
    for alias in aliases
]
# Sort by alias length descending so "abu dhabi" matches before "abu" prefix attempts.
_FLAT_ALIASES.sort(key=lambda x: -len(x[0]))

FUZZY_THRESHOLD = 85  # rapidfuzz ratio out of 100


def normalize_city(address: str | None) -> str | None:
    """
    Return the Filex-spec city name found in `address`, or None.

    Strategy:
      1. Direct word-boundary alias match. If multiple cities are present,
         the FIRST one encountered (by position in the address) wins.
      2. Fuzzy fallback per-token using rapidfuzz ratio >= 85 against
         all aliases.

    Args:
        address: free-text address string from the Notion FULL ADDRESS field.
                 None or empty returns None.

    Returns:
        Filex-spec city name (e.g. "Fujeriah") or None if nothing matches.
    """
    if not address:
        return None
    text = address.lower()

    # Step 1: find all alias hits with their position
    hits: list[tuple[int, str]] = []
    for alias, filex_name in _FLAT_ALIASES:
        for m in re.finditer(rf"\b{re.escape(alias)}\b", text):
            hits.append((m.start(), filex_name))
    if hits:
        hits.sort(key=lambda x: x[0])  # first-position wins
        return hits[0][1]

    # Step 2: fuzzy match per token
    tokens = re.findall(r"[a-z]+", text)
    best_score = 0
    best_filex_name: str | None = None
    for token in tokens:
        if len(token) < 4:
            continue  # avoid false positives on tiny tokens
        for alias, filex_name in _FLAT_ALIASES:
            if len(alias) < 4:
                continue
            score = fuzz.ratio(token, alias)
            if score >= FUZZY_THRESHOLD and score > best_score:
                best_score = score
                best_filex_name = filex_name
    return best_filex_name
