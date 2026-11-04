"""Local web page for the disaster-aid chatbot.

Same facts as the CLI. By default this is live FEMA data plus the offline
template, which is what ``--offline`` does:

    python3 -m disaster_aid.web

Then open http://127.0.0.1:8080
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import traceback
import urllib.error
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .explainer import TemplateExplainer, default_explainer
from .fema_client import STATE_FIPS, STATE_NAMES, FemaClient, FixtureClient
from .geo import friendly, is_statewide
from .grounding import check_answer
from .status import DEFAULT_LOOKBACK_DAYS, build_report

INDEX = Path(__file__).resolve().parent / "static" / "index.html"


class _CachingClient:
    """Remember one FEMA pull per state so the county list and the answer share it."""

    def __init__(self, inner):
        self.inner = inner
        self._cache: dict[tuple[str, str], list] = {}
        self._lock = threading.Lock()

    def declarations(self, state: str, since: date) -> list:
        key = (state.strip().upper(), since.isoformat())
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        rows = self.inner.declarations(state, since)
        with self._lock:
            self._cache.setdefault(key, rows)
            return self._cache[key]


def _refresh_day(value: str | None) -> str | None:
    if not value:
        return None
    day = value[:10]
    return day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else None


def list_areas(client, state: str, as_of: date | None = None) -> list[str]:
    """Friendly names of places named in a declaration during the lookback window."""
    today = as_of or date.today()
    rows = client.declarations(state, today - timedelta(days=DEFAULT_LOOKBACK_DAYS))
    labels = {
        friendly(row["designatedArea"])
        for row in rows
        if row.get("designatedArea") and not is_statewide(row["designatedArea"])
    }
    return sorted(labels, key=str.lower)


def answer_county(state: str, county: str, *, client, explainer, as_of: date | None = None) -> dict:
    """Run the CLI pipeline and return JSON the page can render."""
    county = county.strip()
    if not county:
        raise ValueError("Enter a county, parish, or city.")
    if len(county) > 80:
        raise ValueError("That place name is too long.")
    report = build_report(client, state, county, as_of=as_of)
    answer = explainer.explain(report)
    payload = report.to_payload()
    problems = check_answer(answer.text, payload)
    return {
        "text": answer.text,
        "model": answer.model,
        "grounding_ok": not problems,
        "problems": problems,
        "data_refreshed": _refresh_day(report.data_refreshed),
        "county_name": report.county_name,
        "state": report.state,
        "ambiguous_areas": [friendly(area) for area in report.ambiguous_areas],
        "declarations": payload["declarations"],
        "apply_url": payload["apply_url"],
        "helpline": payload["helpline"],
        "today": payload["today"],
    }


class App:
    def __init__(self, client, explainer, as_of: date | None = None, source: str = "live"):
        self.client = _CachingClient(client)
        self.explainer = explainer
        self.as_of = as_of
        self.source = source

    def config(self) -> dict:
        states = [
            {"code": code, "name": STATE_NAMES[code]}
            for code in sorted(STATE_NAMES, key=lambda code: STATE_NAMES[code])
        ]
        return {
            "source": self.source,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "explainer": getattr(self.explainer, "model", "template"),
            "states": states,
        }

    def areas(self, state: str) -> list[str]:
        return list_areas(self.client, state, as_of=self.as_of)

    def ask(self, state: str, county: str) -> dict:
        return answer_county(state, county, client=self.client, explainer=self.explainer, as_of=self.as_of)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            print(f"  {self.address_string()} {fmt % args}")

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._page()
            elif path == "/api/config":
                self._json(200, app.config())
            elif path == "/api/areas":
                self._areas()
            else:
                self._json(404, {"error": "Not found."})

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/ask":
                self._json(404, {"error": "Not found."})
                return
            try:
                body = self._read_json()
                result = app.ask(str(body.get("state", "")), str(body.get("county", "")))
            except Exception as err:
                self._fail(err)
            else:
                self._json(200, result)

        def _areas(self) -> None:
            state = (parse_qs(urlparse(self.path).query).get("state") or [""])[0]
            try:
                areas = app.areas(state)
            except Exception as err:
                self._fail(err)
            else:
                self._json(200, {"areas": areas})

        def _fail(self, err: Exception) -> None:
            if isinstance(err, ValueError):
                self._json(400, {"error": str(err)})
            elif isinstance(err, RuntimeError):
                self._json(502, {"error": str(err)})
            elif isinstance(err, urllib.error.URLError):
                self._json(502, {"error": "Could not reach FEMA. Check the connection and try again."})
            else:
                traceback.print_exc()
                self._json(500, {"error": "Something went wrong while checking FEMA data."})

        def _page(self) -> None:
            try:
                body = INDEX.read_bytes()
            except OSError:
                self._json(500, {"error": "The page file is missing."})
                return
            self._send(200, body, "text/html; charset=utf-8")

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length < 0 or length > 20_000:
                raise ValueError("Request is too large.")
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw.decode() or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("Send JSON with a state and a county.") from None
            if not isinstance(data, dict):
                raise ValueError("Send JSON with a state and a county.")
            return data

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def build_server(app: App, host: str = "127.0.0.1", port: int = 8080) -> Server:
    return Server((host, port), make_handler(app))


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Web page: can this county get FEMA disaster aid?")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--claude", action="store_true",
                        help="Use Claude when ANTHROPIC_API_KEY is set. Default is the offline template.")
    parser.add_argument("--fixtures", help="Read declarations from a JSON file instead of FEMA")
    parser.add_argument("--as-of", help="Pretend today is this date (YYYY-MM-DD)")
    args = parser.parse_args(argv)
    if args.as_of:
        try:
            date.fromisoformat(args.as_of)
        except ValueError:
            parser.error("--as-of must be YYYY-MM-DD")
    if set(STATE_NAMES) != set(STATE_FIPS):
        parser.error("State list is out of date.")
    return args


def main(argv: list[str] | None = None) -> None:
    args = _parse(argv)
    client = FixtureClient(args.fixtures) if args.fixtures else FemaClient()
    explainer = default_explainer() if args.claude else TemplateExplainer()
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    app = App(client, explainer, as_of=as_of, source="fixtures" if args.fixtures else "live")
    try:
        httpd = build_server(app, args.host, args.port)
    except OSError as err:
        raise SystemExit(f"Could not listen on {args.host}:{args.port} ({err.strerror}).") from err
    host, port = httpd.server_address[:2]
    where = "synthetic fixtures" if args.fixtures else "live FEMA data"
    when = f", as of {as_of.isoformat()}" if as_of else ""
    print(f"Disaster Aid Navigator  http://{host}:{port}", flush=True)
    print(f"  {where}{when}, answers from {getattr(explainer, 'model', 'template')}", flush=True)
    print("  Ctrl+C to stop", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
