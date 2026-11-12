"""Match what a person types ("St. Louis County") to FEMA's designatedArea values.

FEMA writes areas as "Name (Kind)", for example "St. Louis (County)",
"St. Louis (Independent City)" or "Orleans (Parish)". Missouri has both a
St. Louis County and a separate St. Louis City, so the kind matters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

KIND_WORDS = {
    "county": "county", "parish": "parish", "borough": "borough",
    "city": "city", "independent city": "city", "census area": "census area",
    "city and borough": "city and borough",
    "municipio": "municipio", "municipality": "municipality",
}
STATEWIDE = "statewide"


def _norm(text: str) -> str:
    text = text.lower().replace("saint ", "st ").replace("ste. ", "ste ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class Area:
    base: str  # normalized name, e.g. "st louis"
    kind: str | None  # "county", "city", "parish", ... or None

    @classmethod
    def from_fema(cls, designated_area: str) -> "Area":
        m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", designated_area)
        if not m:
            return cls(_norm(designated_area), None)
        kind = _norm(m.group(2))
        return cls(_norm(m.group(1)), KIND_WORDS.get(kind, kind))

    @classmethod
    def from_user(cls, text: str) -> "Area":
        name = _norm(text)
        # Longest kind phrase first so "independent city" wins over "city".
        for phrase in sorted(KIND_WORDS, key=len, reverse=True):
            if name.endswith(" " + phrase):
                return cls(name[: -len(phrase) - 1].strip(), KIND_WORDS[phrase])
        return cls(name, None)


@dataclass
class MatchResult:
    areas: list[str]  # FEMA designatedArea values that match
    ambiguous: bool  # same name exists as different kinds, and the user didn't say which


def match_areas(user_text: str, designated_areas: set[str]) -> MatchResult:
    """Return the FEMA areas that the user's county text refers to."""
    want = Area.from_user(user_text)
    hits = [a for a in designated_areas
            if _norm(a) != STATEWIDE and Area.from_fema(a).base == want.base]
    if want.kind:
        hits = [a for a in hits if Area.from_fema(a).kind == want.kind]
    kinds = {Area.from_fema(a).kind for a in hits}
    return MatchResult(sorted(hits), ambiguous=want.kind is None and len(kinds) > 1)


def is_statewide(designated_area: str) -> bool:
    return _norm(designated_area) == STATEWIDE


def friendly(designated_area: str) -> str:
    """'St. Louis (County)' -> 'St. Louis County'; 'Richmond (Independent City)' -> 'Richmond city'."""
    m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", designated_area)
    if not m:
        return designated_area
    kind = m.group(2)
    return f"{m.group(1)} {'city' if 'city' in kind.lower() else kind}"
