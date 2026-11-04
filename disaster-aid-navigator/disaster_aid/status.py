"""Turn raw FEMA rows into plain facts about one county. No AI in this step.

Everything a resident relies on (is there a declaration, can households
apply, what is the deadline, how many days are left) is computed here in
code, so the language model only explains these facts, it never works them out.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from .geo import friendly, is_statewide, match_areas

DECLARATION_TYPES = {"DR": "Major Disaster", "EM": "Emergency", "FM": "Fire Management"}
DEFAULT_IA_WINDOW_DAYS = 60  # FEMA's usual registration window when no date is published
DEFAULT_LOOKBACK_DAYS = 730
FEMA_HELPLINE = "1-800-621-3362"
APPLY_URL = "https://www.disasterassistance.gov"


SMALL_WORDS = {"and", "or", "of", "the", "in", "on", "a", "an", "to", "for"}


def _title(text: str) -> str:
    words = text.lower().split()
    return " ".join(w if i and w in SMALL_WORDS else w.capitalize() for i, w in enumerate(words))


def _d(value: str | None) -> date | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date() if value else None


@dataclass
class Declaration:
    code: str  # e.g. "DR-4567-MO"
    disaster_number: int
    declaration_type: str  # "Major Disaster", "Emergency", "Fire Management"
    title: str
    incident_type: str
    declaration_date: date
    incident_begin: date | None
    incident_end: date | None
    areas: list[str]
    individual_assistance: bool
    public_assistance: bool
    hazard_mitigation: bool
    ia_status: str  # "open", "closed" or "not_available"
    ia_deadline: date | None = None
    deadline_is_estimate: bool = False
    days_left: int | None = None
    url: str = ""


@dataclass
class CountyReport:
    state: str
    county_query: str
    as_of: date
    lookback_days: int
    declarations: list[Declaration] = field(default_factory=list)
    ambiguous_areas: list[str] = field(default_factory=list)
    data_refreshed: str | None = None
    county_name: str = ""  # FEMA's name for the county when matched, e.g. "Pike County"

    def __post_init__(self):
        self.county_name = self.county_name or self.county_query

    @property
    def open_ia(self) -> list[Declaration]:
        return [d for d in self.declarations if d.ia_status == "open"]

    def to_payload(self) -> dict:
        """Compact JSON-safe facts: the only thing the explainer may use."""
        out = {
            "state": self.state,
            "county": self.county_name,
            "today": self.as_of.isoformat(),
            "searched_last_days": self.lookback_days,
            "helpline": FEMA_HELPLINE,
            "apply_url": APPLY_URL,
            "declarations": [],
        }
        for d in self.declarations:
            row = {k: v for k, v in asdict(d).items() if v not in (None, "", [])}
            for k, v in row.items():
                if isinstance(v, date):
                    row[k] = v.isoformat()
            out["declarations"].append(row)
        return out


def _ia_status(decl_date: date, ia: bool, deadline: date | None, today: date):
    """Return (status, deadline, is_estimate, days_left)."""
    if not ia:
        return "not_available", None, False, None
    estimate = deadline is None
    if estimate:
        deadline = decl_date + timedelta(days=DEFAULT_IA_WINDOW_DAYS)
    days_left = (deadline - today).days
    return ("open" if days_left >= 0 else "closed"), deadline, estimate, max(days_left, 0)


def build_report(client, state: str, county: str, as_of: date | None = None,
                 lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> CountyReport:
    today = as_of or date.today()
    rows = client.declarations(state, today - timedelta(days=lookback_days))
    report = CountyReport(state.upper(), county, today, lookback_days)
    if rows:
        report.data_refreshed = max(r.get("lastRefresh") or "" for r in rows) or None

    match = match_areas(county, {r["designatedArea"] for r in rows})
    if match.ambiguous:
        report.ambiguous_areas = match.areas
        return report

    wanted = set(match.areas)
    if match.areas:
        report.county_name = friendly(match.areas[0])
    relevant = [r for r in rows if r["designatedArea"] in wanted or is_statewide(r["designatedArea"])]

    # FEMA can list the same disaster more than once for an area (for example
    # when a program is added later). Merge rows per disaster.
    merged: dict[int, dict] = {}
    for r in relevant:
        m = merged.setdefault(r["disasterNumber"], {**r, "_areas": set()})
        m["_areas"].add(r["designatedArea"])
        for flag in ("ihProgramDeclared", "iaProgramDeclared", "paProgramDeclared", "hmProgramDeclared"):
            m[flag] = bool(m.get(flag)) or bool(r.get(flag))
        dates = [x for x in (m.get("lastIAFilingDate"), r.get("lastIAFilingDate")) if x]
        m["lastIAFilingDate"] = max(dates) if dates else None

    for m in merged.values():
        decl_date = _d(m["declarationDate"])
        ia = m["ihProgramDeclared"] or m["iaProgramDeclared"]
        status, deadline, estimate, days_left = _ia_status(decl_date, ia, _d(m["lastIAFilingDate"]), today)
        report.declarations.append(Declaration(
            code=m["femaDeclarationString"],
            disaster_number=m["disasterNumber"],
            declaration_type=DECLARATION_TYPES.get(m["declarationType"], m["declarationType"]),
            title=_title(m["declarationTitle"]),
            incident_type=m["incidentType"],
            declaration_date=decl_date,
            incident_begin=_d(m.get("incidentBeginDate")),
            incident_end=_d(m.get("incidentEndDate")),
            areas=sorted(m["_areas"]),
            individual_assistance=ia,
            public_assistance=m["paProgramDeclared"],
            hazard_mitigation=m["hmProgramDeclared"],
            ia_status=status,
            ia_deadline=deadline,
            deadline_is_estimate=estimate,
            days_left=days_left if status == "open" else None,
            url=f"https://www.fema.gov/disaster/{m['disasterNumber']}",
        ))
    # Open applications first, then newest.
    report.declarations.sort(key=lambda d: (d.ia_status != "open", -d.declaration_date.toordinal()))
    return report
