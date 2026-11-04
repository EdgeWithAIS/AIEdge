"""Find a county from a ZIP code or a street address. Uses free U.S. Census data.

- ZIP code: the Census 2020 ZIP Code Tabulation Area (ZCTA) to county file.
  It is downloaded once (about 7 MB) and cached as a small JSON file.
- Street address: the Census Geocoder, which returns the county for an address.

A ZIP code can cross county lines. When it does, every county with a real share of
the ZIP code's land is returned, largest first, so the person can pick theirs.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from .fema_client import STATE_FIPS, STATE_NAMES, _ssl_context

ZCTA_URL = ("https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
            "tab20_zcta520_county20_natl.txt")
GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
FIPS_STATE = {fips: code for code, fips in STATE_FIPS.items()}
ZIP_RE = re.compile(r"^\s*(\d{5})(?:-\d{4})?\s*$")
MIN_SHARE = 0.01  # Ignore slivers under 1% of a ZIP code's land.


def default_cache_dir() -> Path:
    return Path(os.environ.get("DISASTER_AID_CACHE", Path.home() / ".cache" / "disaster-aid-navigator"))


@dataclass
class Place:
    state: str  # two-letter code, e.g. "MO"
    county: str  # Census county name, e.g. "St. Louis County" or "St. Louis city"
    share: float = 1.0  # Share of the ZIP code's land in this county. 1.0 for addresses.

    @property
    def label(self) -> str:
        return f"{self.county}, {STATE_NAMES.get(self.state, self.state)}"

    def to_dict(self) -> dict:
        return {**asdict(self), "label": self.label}


@dataclass
class Location:
    query: str
    kind: str  # "zip" or "address"
    places: list[Place]
    matched_address: str | None = None


def parse_zcta_file(lines) -> dict[str, list[list]]:
    """Census relationship file -> {zip: [[county_geoid, county_name, land_area], ...]}."""
    out: dict[str, list[list]] = {}
    header = None
    for raw in lines:
        cols = raw.rstrip("\r\n").lstrip("﻿").split("|")
        if header is None:
            header = {name: i for i, name in enumerate(cols)}
            continue
        zcta = cols[header["GEOID_ZCTA5_20"]]
        if not zcta:
            continue  # County rows with no ZIP code (water, empty land).
        land = int(cols[header["AREALAND_PART"]] or 0)
        out.setdefault(zcta, []).append(
            [cols[header["GEOID_COUNTY_20"]], cols[header["NAMELSAD_COUNTY_20"]], land])
    return out


def places_for_zip(zip_code: str, table: dict[str, list[list]]) -> list[Place]:
    parts = table.get(zip_code, [])
    total = sum(land for _, _, land in parts)
    places = []
    for geoid, name, land in parts:
        state = FIPS_STATE.get(geoid[:2])
        share = land / total if total else 1.0 / len(parts)
        if state and share >= MIN_SHARE:
            places.append(Place(state, name, round(share, 3)))
    return sorted(places, key=lambda p: -p.share)


class Locator:
    """Turn a ZIP code or an address into one or more (state, county) places."""

    def __init__(self, zcta_file: str | Path | None = None, cache_dir: Path | None = None,
                 geocode=None, timeout: int = 30):
        self.zcta_file = Path(zcta_file) if zcta_file else None
        self.cache_dir = cache_dir or default_cache_dir()
        self.timeout = timeout
        self._geocode = geocode or self._census_geocode
        self._zips: dict[str, list[list]] | None = None

    # --- ZIP codes ------------------------------------------------------------

    def _zip_table(self) -> dict[str, list[list]]:
        if self._zips is not None:
            return self._zips
        if self.zcta_file:
            with self.zcta_file.open(encoding="utf-8-sig") as f:
                self._zips = parse_zcta_file(f)
            return self._zips
        cached = self.cache_dir / "zcta_county_2020.json"
        if cached.exists():
            self._zips = json.loads(cached.read_text())
            return self._zips
        req = urllib.request.Request(ZCTA_URL, headers={"User-Agent": "disaster-aid-navigator"})
        try:
            with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as r:
                text = r.read().decode("utf-8-sig")
        except urllib.error.URLError as err:
            raise RuntimeError("Could not download the Census ZIP code list. Try a county name.") from err
        self._zips = parse_zcta_file(text.splitlines())
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(self._zips, separators=(",", ":")))
        return self._zips

    # --- street addresses -------------------------------------------------------

    def _census_geocode(self, address: str) -> dict:
        params = urllib.parse.urlencode({
            "address": address, "benchmark": "Public_AR_Current",
            "vintage": "Current_Current", "layers": "Counties", "format": "json",
        })
        req = urllib.request.Request(f"{GEOCODER_URL}?{params}",
                                     headers={"User-Agent": "disaster-aid-navigator"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=_ssl_context()) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as err:
            raise RuntimeError(f"Census address lookup error {err.code}. Try a ZIP code or county.") from None

    def _address(self, address: str) -> Location:
        data = self._geocode(address)
        matches = (data.get("result") or {}).get("addressMatches") or []
        places, seen, matched = [], set(), None
        for m in matches:
            for county in (m.get("geographies") or {}).get("Counties", []):
                state = FIPS_STATE.get(county.get("STATE", ""))
                key = (state, county.get("NAME"))
                if state and county.get("NAME") and key not in seen:
                    seen.add(key)
                    places.append(Place(state, county["NAME"]))
                    matched = matched or m.get("matchedAddress")
        return Location(address, "address", places, matched)

    # --- entry point ----------------------------------------------------------

    def locate(self, text: str) -> Location:
        text = (text or "").strip()
        if not text:
            raise ValueError("Enter a ZIP code or a street address.")
        if len(text) > 200:
            raise ValueError("That address is too long.")
        zip_match = ZIP_RE.match(text)
        if zip_match:
            code = zip_match.group(1)
            return Location(code, "zip", places_for_zip(code, self._zip_table()))
        if re.fullmatch(r"\d{1,9}", text):
            raise ValueError("A ZIP code has 5 numbers, for example 77002.")
        return self._address(text)
