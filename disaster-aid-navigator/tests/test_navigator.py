"""Tests run offline against synthetic fixtures. No network or API key."""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from disaster_aid import FixtureClient, TemplateExplainer, build_report, check_answer  # noqa: E402
from disaster_aid.explainer import fmt_date  # noqa: E402
from disaster_aid.geo import Area, friendly, match_areas  # noqa: E402

TODAY = date(2026, 11, 3)


@pytest.fixture(scope="module")
def client():
    return FixtureClient(ROOT / "fixtures" / "declarations.json")


def report(client, state, county, as_of=TODAY):
    return build_report(client, state, county, as_of=as_of)


# --- county matching -------------------------------------------------------

@pytest.mark.parametrize("text,base,kind", [
    ("St. Louis County", "st louis", "county"),
    ("Saint Louis", "st louis", None),
    ("st louis city", "st louis", "city"),
    ("Orleans Parish", "orleans", "parish"),
    ("Miami-Dade", "miami dade", None),
])
def test_user_text_parsing(text, base, kind):
    assert Area.from_user(text) == Area(base, kind)


def test_fema_area_parsing():
    assert Area.from_fema("St. Louis (Independent City)") == Area("st louis", "city")
    assert friendly("St. Louis (County)") == "St. Louis County"


def test_same_name_different_kind_is_ambiguous():
    areas = {"Richmond (County)", "Richmond (Independent City)", "Henrico (County)"}
    assert match_areas("Richmond", areas).ambiguous
    assert match_areas("Richmond County", areas).areas == ["Richmond (County)"]


# --- status logic ------------------------------------------------------------

def test_open_deadline_and_days_left(client):
    d = report(client, "MO", "St. Louis County").open_ia[0]
    assert (d.code, d.ia_deadline, d.days_left, d.deadline_is_estimate) == (
        "DR-9901-MO", date(2026, 11, 30), 27, False)


def test_deadline_day_itself_is_still_open(client):
    d = report(client, "MO", "St. Louis County", as_of=date(2026, 11, 30)).open_ia[0]
    assert d.days_left == 0


def test_deadline_passes_the_next_day(client):
    r = report(client, "MO", "St. Louis County", as_of=date(2026, 12, 1))
    assert r.open_ia == []


def test_missing_filing_date_is_estimated_and_flagged(client):
    d = report(client, "KY", "Pike").declarations[0]
    assert d.ia_deadline == date(2026, 11, 19) and d.deadline_is_estimate


def test_city_and_county_are_kept_apart(client):
    assert report(client, "MO", "St. Louis City").declarations[0].ia_status == "not_available"


def test_statewide_declarations_apply_to_every_county(client):
    assert [d.code for d in report(client, "CA", "Fresno").declarations] == ["EM-9905-CA"]


def test_duplicate_rows_merge(client):
    decls = report(client, "LA", "Orleans Parish").declarations
    assert len(decls) == 1 and decls[0].individual_assistance and decls[0].public_assistance


def test_lookback_window_excludes_old_disasters(client):
    assert report(client, "FL", "Miami-Dade").declarations == []


def test_bad_state_code_rejected(client):
    with pytest.raises(ValueError):
        report(client, "Missouri", "St. Louis")


# --- answers and grounding -------------------------------------------------

def test_no_declaration_answer_needs_no_ai(client):
    ans = TemplateExplainer().explain(report(client, "NY", "Kings"))
    assert ans.model == "rule" and not ans.used_llm


def test_template_answers_are_grounded(client):
    for state, county in [("MO", "St. Louis County"), ("KY", "Pike"), ("TX", "Harris")]:
        r = report(client, state, county)
        assert check_answer(TemplateExplainer().explain(r).text, r.to_payload()) == []


@pytest.fixture
def stl_payload(client):
    return report(client, "MO", "St. Louis County").to_payload()


GOOD = ("Yes. DR-9901-MO (Severe Storms) is open: apply by November 30, 2026 (27 days left). "
        "Apply at https://www.disasterassistance.gov or 1-800-621-3362.")


def test_checker_accepts_a_correct_answer(stl_payload):
    assert check_answer(GOOD, stl_payload) == []


@pytest.mark.parametrize("bad,expected", [
    (GOOD.replace("November 30, 2026", "December 30, 2026"), "date not in facts"),
    (GOOD.replace("DR-9901-MO", "DR-4821-MO"), "declaration code not in facts"),
    (GOOD.replace("27 days", "30 days"), "day count not in facts"),
    ("Yes, households can apply. Apply at https://www.disasterassistance.gov.", "open deadline missing"),
    (GOOD.replace("November 30, 2026", "Nov. 31, 2026").replace("Nov. 31", "Dec 1"), "date not in facts"),
])
def test_checker_catches_made_up_facts(stl_payload, bad, expected):
    problems = check_answer(bad, stl_payload)
    assert any(expected in p for p in problems), problems


def test_checker_requires_estimate_wording(client):
    r = report(client, "KY", "Pike")
    answer = "DR-9904-KY: apply by November 19, 2026 (16 days left)."
    assert any("estimate" in p for p in check_answer(answer, r.to_payload()))
    assert check_answer(answer + " This date is an estimate.", r.to_payload()) == []


def test_date_formats_recognized():
    assert fmt_date(date(2026, 11, 3)) == "November 3, 2026"


def test_payload_is_small(client):
    """Keep prompts cheap: facts for a county should stay well under 2,000 characters."""
    payload = json.dumps(report(client, "MO", "St. Louis County").to_payload(), separators=(",", ":"))
    assert len(payload) < 2000


# --- eval quality gate --------------------------------------------------------

def test_offline_evals_pass():
    sys.path.insert(0, str(ROOT / "evals"))
    from run_evals import run
    scenarios = json.loads((ROOT / "evals" / "scenarios.json").read_text())
    m = run(TemplateExplainer(), scenarios)["metrics"]
    assert m["status_accuracy"] == 100 and m["grounded_answers"] == 100


# --- live client (network mocked) ------------------------------------------

def test_live_client_builds_query_and_pages(monkeypatch):
    import io
    import urllib.parse
    from disaster_aid import fema_client

    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def fake_urlopen(req, timeout=0, context=None):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        calls.append(q)
        n = fema_client.PAGE_SIZE if q["$skip"] == ["0"] else 3  # two pages
        return Resp(json.dumps({"DisasterDeclarationsSummaries": [{}] * n}).encode())

    monkeypatch.setattr(fema_client.urllib.request, "urlopen", fake_urlopen)
    rows = fema_client.FemaClient().declarations("mo", date(2025, 1, 1))
    assert len(rows) == fema_client.PAGE_SIZE + 3
    assert calls[0]["$filter"] == ["fipsStateCode eq '29' and declarationDate ge '2025-01-01T00:00:00.000Z'"]
    assert calls[1]["$skip"] == [str(fema_client.PAGE_SIZE)]
