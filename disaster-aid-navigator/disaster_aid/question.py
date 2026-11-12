"""Turn a free-text question into a county or place lookup.

Keeps the same FEMA pipeline: the model never invents a location. We only
pull out a ZIP code, a street address, or a state + county from what the
person typed. If those are missing, we ask for a county/parish and state.
"""
from __future__ import annotations

import re

from .fema_client import STATE_FIPS, STATE_NAMES

ZIP_ONLY = re.compile(r"^\s*(\d{5})(?:-\d{4})?\s*$")
ZIP_ANY = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
# "Harris County, TX" / "in St. Louis County, MO apply…" / "Orleans Parish, Louisiana"
COUNTY_COMMA_STATE = re.compile(
    r"(?P<county>[A-Za-z][A-Za-z .'-]{1,60}?),\s*(?P<state>[A-Za-z.]{2,}(?:\s+[A-Za-z.]{2,}){0,3})\b",
    re.I,
)
# "Harris County TX" / "St. Louis County Missouri" at the end of the line
COUNTY_STATE_END = re.compile(
    r"(?P<county>[A-Za-z][A-Za-z .'-]{1,60}?)\s+"
    r"(?P<state>[A-Za-z.]{2,}(?:\s+[A-Za-z.]{2,}){0,3})\s*[?.!]?\s*$",
    re.I,
)
# "in Harris County in Texas"
IN_COUNTY_IN_STATE = re.compile(
    r"\b(?:in|for|at|near)\s+(?P<county>[A-Za-z][A-Za-z .'-]{1,60}?)\s+"
    r"in\s+(?P<state>[A-Za-z.]{2,}(?:\s+[A-Za-z.]{2,}){0,3})\b",
    re.I,
)
# County/parish/city named without a state. Lookahead so overlapping
# candidates ("What about Harris County" / "Harris County") can be ranked.
COUNTY_ONLY = re.compile(
    r"(?=(?P<county>\b(?:St\.?\s+)?[A-Za-z][A-Za-z'-]*"
    r"(?:\s+[A-Za-z][A-Za-z'-]*){0,2}\s+"
    r"(?:County|Parish|Borough|City|Municipio|Census Area)\b))",
    re.I,
)
_COUNTY_LEAD_NOISE = {
    "a", "an", "the", "my", "our", "your", "what", "which", "about", "and",
    "for", "in", "near", "at", "of", "or", "how", "can", "could", "do", "does",
}
STREET_HINT = re.compile(
    r"\b(\d+\s+\w+|street|st\.|avenue|ave\.|road|rd\.|drive|dr\.|boulevard|blvd\.|lane|ln\.)\b",
    re.I,
)

_STATE_BY_NAME = {name.lower(): code for code, name in STATE_NAMES.items()}
for code in STATE_FIPS:
    _STATE_BY_NAME[code.lower()] = code
# Common short forms.
_STATE_BY_NAME.update({
    "d.c.": "DC", "dc": "DC", "washington dc": "DC", "washington d.c.": "DC",
})


def _state_code(text: str) -> str | None:
    key = re.sub(r"\s+", " ", text.strip().lower().rstrip("."))
    key = key.replace(".", "")
    if not key:
        return None
    if len(key) == 2:
        return _STATE_BY_NAME.get(key)
    found = _STATE_BY_NAME.get(key)
    if found:
        return found
    # "MO apply for FEMA help" → try the first token as a state code.
    first = key.split()[0]
    if len(first) == 2:
        return _STATE_BY_NAME.get(first)
    return None


def _clean_county(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip(" ,.?!"))
    text = re.sub(
        r"^(?:can|could|may|do|are|is|will)\b.*?\b(?:in|for|at|near)\s+",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"^(?:families|households|people|i|we)\b.*?\b(?:in|for|at|near)\s+",
        "",
        text,
        flags=re.I,
    )
    return text.strip(" ,.?!")


def interpret_question(text: str) -> dict:
    """Return kwargs for ask_place or ask, or a prompt for a missing place.

    Successful keys:
      ``{"place": ...}`` or ``{"state", "county"}``
    Missing place:
      ``{"need_location": True}`` or ``{"need_state": True, "county": ...}``
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Type a place or a question, for example Harris County, TX or 77002.")
    if len(raw) > 400:
        raise ValueError("That question is too long.")

    if ZIP_ONLY.match(raw):
        return {"place": ZIP_ONLY.match(raw).group(1)}

    for pattern in (COUNTY_COMMA_STATE, IN_COUNTY_IN_STATE, COUNTY_STATE_END):
        match = pattern.search(raw)
        if not match:
            continue
        state = _state_code(match.group("state"))
        county = _clean_county(match.group("county"))
        kind = county.lower()
        if state and county and len(county) >= 2 and (
                " " in county or kind.endswith(
                    ("county", "parish", "borough", "city", "municipio", "census area"))):
            return {"state": state, "county": county}
        if state and county and len(county) >= 3 and pattern is not COUNTY_STATE_END:
            return {"state": state, "county": county}

    # A ZIP inside a longer question → look up that ZIP.
    zip_match = ZIP_ANY.search(raw)
    if zip_match and not STREET_HINT.search(raw):
        return {"place": zip_match.group(1)}

    # Street address → Census geocoder.
    if STREET_HINT.search(raw):
        return {"place": raw}

    # County/parish named, but no state yet — pick the shortest clean match.
    county = _county_only(raw)
    if county:
        return {"need_state": True, "county": county}

    # Not enough to look up — ask for county/parish and state in the chat.
    return {"need_location": True}


def _county_only(text: str) -> str | None:
    candidates = []
    for match in COUNTY_ONLY.finditer(text):
        county = _clean_county(match.group("county"))
        words = county.split()
        while words and words[0].lower().rstrip(".") in _COUNTY_LEAD_NOISE:
            words.pop(0)
        if len(words) < 2:
            continue
        if words[-1].lower() not in {
                "county", "parish", "borough", "city", "municipio", "area"}:
            continue
        candidates.append(" ".join(words))
    if not candidates:
        return None
    # Longest clean match keeps "St. Louis County" over "Louis County".
    return max(candidates, key=len)
