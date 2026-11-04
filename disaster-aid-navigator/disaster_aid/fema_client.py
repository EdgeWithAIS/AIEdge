"""Fetch disaster declarations from OpenFEMA (no API key needed).

Dataset: Disaster Declarations Summaries v2, one row per declared county
(or "Statewide") per disaster. FEMA refreshes it about every 20 minutes.
https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

API_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"

# Only the fields the app uses. Smaller responses, and a smaller prompt later.
FIELDS = [
    "femaDeclarationString", "disasterNumber", "state", "declarationType",
    "declarationDate", "incidentType", "declarationTitle", "ihProgramDeclared",
    "iaProgramDeclared", "paProgramDeclared", "hmProgramDeclared",
    "incidentBeginDate", "incidentEndDate", "fipsStateCode", "fipsCountyCode",
    "designatedArea", "lastIAFilingDate", "lastRefresh",
]

PAGE_SIZE = 1000

# Two-letter code -> FIPS state code. Filtering on fipsStateCode avoids
# depending on how the free-text "state" field is written.
STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
    "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
    "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
    "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56", "AS": "60", "GU": "66", "MP": "69", "PR": "72", "VI": "78",
}


def _ssl_context() -> ssl.SSLContext:
    """Use certifi's CA bundle when installed (fixes macOS python.org builds)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _check_state(state: str) -> str:
    state = state.strip().upper()
    if state not in STATE_FIPS:
        raise ValueError(f"Use a two-letter state code like MO or TX, not {state!r}")
    return STATE_FIPS[state]


class FemaClient:
    """Live OpenFEMA client."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def _get(self, params: dict) -> dict:
        url = f"{API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=_ssl_context()) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as err:
            raise RuntimeError(f"OpenFEMA error {err.code}: {err.read()[:300]!r}") from None

    def declarations(self, state: str, since: date) -> list[dict]:
        """All declaration rows for a state declared on or after ``since``."""
        fips = _check_state(state)
        flt = f"fipsStateCode eq '{fips}' and declarationDate ge '{since.isoformat()}T00:00:00.000Z'"
        rows, skip = [], 0
        while True:
            data = self._get({
                "$filter": flt,
                "$select": ",".join(FIELDS),
                "$orderby": "declarationDate desc",
                "$top": PAGE_SIZE,
                "$skip": skip,
            })
            page = data.get("DisasterDeclarationsSummaries", [])
            rows.extend(page)
            if len(page) < PAGE_SIZE:
                return rows
            skip += PAGE_SIZE


class FixtureClient:
    """Offline client that serves records from a JSON file in OpenFEMA's format.

    Used by tests, evals and CI so they run without network access.
    """

    def __init__(self, path: str | Path):
        self.rows = json.loads(Path(path).read_text())

    def declarations(self, state: str, since: date) -> list[dict]:
        fips = _check_state(state)
        return [
            r for r in self.rows
            if r["fipsStateCode"] == fips and r["declarationDate"][:10] >= since.isoformat()
        ]
