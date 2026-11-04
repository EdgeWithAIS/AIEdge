"""Spanish answers, ZIP/address lookup, next-step checklist and denial help. No network."""
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from disaster_aid import FixtureClient, TemplateExplainer, build_report, check_answer  # noqa: E402
from disaster_aid.explainer import fmt_date  # noqa: E402
from disaster_aid.guidance import denial_help, next_steps, offers_denial_help  # noqa: E402
from disaster_aid.locate import Locator  # noqa: E402
from disaster_aid.web import App, answer_place  # noqa: E402

TODAY = date(2026, 11, 3)
FIXTURES = ROOT / "fixtures" / "declarations.json"
ZIPS = ROOT / "fixtures" / "zcta_county_sample.txt"


@pytest.fixture(scope="module")
def client():
    return FixtureClient(FIXTURES)


def report(client, state, county):
    return build_report(client, state, county, as_of=TODAY)


def fake_geocoder(county="Harris County", state="48", matched="1001 AVENIDA DE LAS AMERICAS, HOUSTON, TX, 77010"):
    def geocode(address):
        if "nowhere" in address.lower():
            return {"result": {"addressMatches": []}}
        return {"result": {"addressMatches": [{
            "matchedAddress": matched,
            "geographies": {"Counties": [{"NAME": county, "STATE": state, "COUNTY": "201"}]},
        }]}}
    return geocode


def locator(**kw):
    return Locator(zcta_file=ZIPS, geocode=fake_geocoder(**kw))


# --- Spanish -----------------------------------------------------------------

def test_spanish_dates():
    assert fmt_date(date(2026, 11, 30), "es") == "30 de noviembre de 2026"


def test_spanish_answer_is_spanish_and_grounded(client):
    r = report(client, "MO", "St. Louis County")
    text = TemplateExplainer().explain(r, "es").text
    assert text.startswith("Sí. Las familias de St. Louis County")
    assert "en Misuri (DR-9901-MO)" in text
    assert "Puede pedir ayuda hasta el 30 de noviembre de 2026. Le quedan 27 días." in text
    assert check_answer(text, r.to_payload()) == []


@pytest.mark.parametrize("state,county", [("KY", "Pike"), ("TX", "Harris"), ("MO", "St. Louis"),
                                          ("NY", "Kings"), ("MO", "St. Louis City"), ("LA", "Orleans Parish")])
def test_every_spanish_answer_passes_the_fact_check(client, state, county):
    r = report(client, state, county)
    assert check_answer(TemplateExplainer().explain(r, "es").text, r.to_payload()) == []


def test_spanish_estimate_is_flagged(client):
    text = TemplateExplainer().explain(report(client, "KY", "Pike"), "es").text
    assert "Esta fecha es estimada" in text


def test_checker_catches_a_wrong_spanish_date(client):
    r = report(client, "MO", "St. Louis County")
    text = TemplateExplainer().explain(r, "es").text
    bad = text.replace("30 de noviembre de 2026", "30 de diciembre de 2026")
    problems = check_answer(bad, r.to_payload())
    assert any("date not in facts" in p for p in problems)
    assert any("open deadline missing" in p for p in problems)
    assert any("day count" in p for p in check_answer(text.replace("27 días", "30 días"), r.to_payload()))


def test_unknown_language_rejected(client):
    with pytest.raises(ValueError):
        TemplateExplainer().explain(report(client, "MO", "St. Louis County"), "fr")


# --- next steps and denial help ----------------------------------------------

def test_checklist_fits_the_situation(client):
    open_steps = next_steps(report(client, "MO", "St. Louis County"))["items"]
    closed_steps = next_steps(report(client, "TX", "Harris"))["items"]
    assert any("application number" in s for s in open_steps)
    assert not any("application number" in s for s in closed_steps)
    assert any("211" in s for s in closed_steps)
    assert next_steps(None, "es")["title"] == "Qué hacer ahora"


def test_denial_help_in_both_languages():
    en, es = denial_help("en"), denial_help("es")
    assert len(en["steps"]) == len(es["steps"]) and len(en["reasons"]) == len(es["reasons"])
    assert any("60 days" in s for s in en["steps"])
    assert any("60 días" in s for s in es["steps"])


def test_denial_help_only_when_families_could_apply(client):
    assert offers_denial_help(report(client, "TX", "Harris"))  # closed IA: an appeal can still be open
    assert not offers_denial_help(report(client, "MO", "St. Louis City"))  # local government only


# --- ZIP code and address lookup ----------------------------------------------

def test_zip_in_one_county():
    loc = locator().locate("77002")
    assert loc.kind == "zip" and [(p.state, p.county) for p in loc.places] == [("TX", "Harris County")]


def test_zip_plus_four_is_accepted():
    assert locator().locate("70112-1234").places[0].county == "Orleans Parish"


def test_zip_across_county_lines_lists_all_largest_first():
    places = locator().locate("63026").places
    assert [p.county for p in places] == ["Jefferson County", "St. Louis County"]
    assert places[0].share > places[1].share


def test_short_number_is_not_a_zip():
    with pytest.raises(ValueError, match="5 numbers"):
        locator().locate("6302")


def test_address_uses_the_geocoder():
    loc = locator().locate("1001 Avenida de las Americas, Houston, TX")
    assert loc.kind == "address" and loc.places[0].state == "TX"
    assert loc.matched_address.startswith("1001 AVENIDA")


def test_place_answer_runs_the_county_pipeline(client):
    data = answer_place("63105", locator=locator(), client=client, explainer=TemplateExplainer(),
                        as_of=TODAY)
    assert len(data["located"]["choices"]) == 2  # St. Louis County and a slice of St. Louis city
    data = answer_place("63101", locator=locator(), client=client, explainer=TemplateExplainer(),
                        as_of=TODAY, lang="es")
    assert data["located_text"] == "El código postal 63101 está en St. Louis city, Misuri."
    assert data["declarations"][0]["ia_status"] == "not_available"
    assert data["grounding_ok"] and data["denial_help"] is None


def test_address_answer_has_steps_and_denial_help(client):
    app = App(client, TemplateExplainer(), as_of=TODAY, source="fixtures", locator=locator())
    data = app.ask_place("1001 Avenida de las Americas, Houston, TX")
    assert data["county_name"] == "Harris County"
    assert "August 14, 2026" in data["text"] and data["grounding_ok"]
    assert data["next_steps"]["items"] and data["denial_help"]["steps"]
    assert data["declarations"][0]["status_line"].startswith("The last day to apply was")


def test_unknown_place_gives_a_clear_error(client):
    with pytest.raises(ValueError, match="not in the small sample ZIP list"):
        answer_place("99999", locator=locator(), client=client, explainer=TemplateExplainer(), as_of=TODAY)
    with pytest.raises(ValueError, match="could not find that address"):
        answer_place("nowhere at all", locator=locator(), client=client, explainer=TemplateExplainer(),
                     as_of=TODAY)


def test_http_place_spanish_and_help(client):
    import json
    import threading
    import urllib.request
    from disaster_aid.web import build_server

    app = App(client, TemplateExplainer(), as_of=TODAY, source="fixtures", locator=locator())
    httpd = build_server(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://%s:%s" % httpd.server_address[:2]
    try:
        req = urllib.request.Request(base + "/api/ask", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"place": "77002", "lang": "es"}).encode())
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        assert data["lang"] == "es" and data["county_name"] == "Harris County"
        assert "14 de agosto de 2026" in data["text"] and data["grounding_ok"]
        with urllib.request.urlopen(base + "/api/help?lang=es", timeout=5) as r:
            assert json.loads(r.read())["title"].startswith("¿FEMA")
        with urllib.request.urlopen(base + "/api/steps?lang=en", timeout=5) as r:
            steps = json.loads(r.read())
        assert steps["title"] == "What to do next"
        assert any("application number" in item for item in steps["items"])
    finally:
        httpd.shutdown()
        httpd.server_close()
