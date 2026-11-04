"""The local page uses the same offline pipeline as the CLI. No network."""
import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from disaster_aid import FixtureClient, TemplateExplainer  # noqa: E402
from disaster_aid.fema_client import STATE_FIPS  # noqa: E402
from disaster_aid.web import STATE_NAMES, App, answer_county, build_server  # noqa: E402

TODAY = date(2026, 11, 3)
FIXTURES = ROOT / "fixtures" / "declarations.json"


def app():
    return App(FixtureClient(FIXTURES), TemplateExplainer(), as_of=TODAY, source="fixtures")


def test_state_list_matches_fips_codes():
    assert set(STATE_NAMES) == set(STATE_FIPS)


def test_open_deadline_matches_the_cli_facts():
    data = answer_county(
        "MO", "St. Louis County",
        client=FixtureClient(FIXTURES), explainer=TemplateExplainer(), as_of=TODAY,
    )
    assert data["grounding_ok"] is True
    assert data["declarations"][0]["code"] == "DR-9901-MO"
    assert data["declarations"][0]["days_left"] == 27
    assert "November 30, 2026" in data["text"]
    assert "27 days left" in data["text"]


def test_ambiguous_place_offers_both_names():
    data = answer_county(
        "MO", "St. Louis",
        client=FixtureClient(FIXTURES), explainer=TemplateExplainer(), as_of=TODAY,
    )
    assert data["ambiguous_areas"] == ["St. Louis County", "St. Louis city"]
    assert data["declarations"] == []


def test_areas_use_friendly_names():
    assert "Harris County" in app().areas("TX")
    assert "St. Louis city" in app().areas("MO")


def test_http_page_and_ask():
    httpd = build_server(app(), "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    base = f"http://{host}:{port}"
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as response:
            page = response.read().decode()
        assert "Disaster Aid Navigator" in page
        assert 'id="county"' in page

        request = urllib.request.Request(
            base + "/api/ask",
            data=json.dumps({"state": "TX", "county": "Harris County"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.loads(response.read())
        assert data["grounding_ok"] is True
        assert data["declarations"][0]["ia_status"] == "closed"
        assert "August 14, 2026" in data["text"]

        with urllib.request.urlopen(base + "/api/areas?state=MO", timeout=5) as response:
            areas = json.loads(response.read())["areas"]
        assert "St. Louis County" in areas
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_rejects_a_bad_state():
    httpd = build_server(app(), "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = "http://%s:%s" % httpd.server_address[:2]
    try:
        request = urllib.request.Request(
            base + "/api/ask",
            data=json.dumps({"state": "Texas", "county": "Harris County"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=5)
            raise AssertionError("expected a 400")
        except urllib.error.HTTPError as err:
            assert err.code == 400
            assert "two-letter" in json.loads(err.read())["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()
