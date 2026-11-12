"""Command line.

    python3 -m disaster_aid.cli MO "St. Louis County"            # live FEMA data
    python3 -m disaster_aid.cli MO "St. Louis County" --offline  # no API key needed
    python3 -m disaster_aid.cli MO "St. Louis County" --fixtures fixtures/declarations.json --as-of 2026-11-03
    python3 -m disaster_aid.cli --place 77002                    # ZIP code
    python3 -m disaster_aid.cli --place "1001 Avenida de las Americas, Houston, TX"
    python3 -m disaster_aid.cli MO "St. Louis County" --lang es --steps   # Spanish, with checklist
    python3 -m disaster_aid.cli --denied                         # how to appeal a FEMA "no"
"""
from __future__ import annotations

import argparse
import json
from datetime import date

from .explainer import TemplateExplainer, default_explainer
from .fema_client import FemaClient, FixtureClient
from .grounding import check_answer
from .guidance import LANGS, checklist_text, denial_text
from .locate import Locator
from .status import build_report


def _pick_place(args, parser) -> tuple[str, str]:
    """Return (state, county) from the arguments or from a ZIP code / address lookup."""
    if not args.place:
        if not (args.state and args.county):
            parser.error("Give a state and a county, or --place with a ZIP code or address.")
        return args.state, args.county
    try:
        loc = Locator(zcta_file=args.zip_file).locate(args.place)
    except (ValueError, RuntimeError) as err:
        parser.exit(1, f"{err}\n")
    if not loc.places:
        parser.exit(1, f"Could not find a county for {args.place!r}.\n")
    if len(loc.places) > 1:
        lines = "\n".join(f"  {p.state} \"{p.county}\"  ({p.share:.0%} of the ZIP code)" for p in loc.places)
        parser.exit(1, f"{loc.query} covers more than one county. Run again with one of these:\n{lines}\n")
    place = loc.places[0]
    where = loc.matched_address or loc.query
    print(f"\n  {where} -> {place.label}")
    return place.state, place.county


def main() -> None:
    p = argparse.ArgumentParser(description="Can families in my county get FEMA disaster aid?")
    p.add_argument("state", nargs="?", help="Two-letter state code, e.g. MO")
    p.add_argument("county", nargs="?", help='County name, e.g. "St. Louis County"')
    p.add_argument("--place", help="ZIP code or street address instead of a state and county")
    p.add_argument("--zip-file", help="Census ZIP-to-county file to use instead of downloading it")
    p.add_argument("--lang", choices=LANGS, default="en", help="Answer language: en or es (Spanish)")
    p.add_argument("--steps", action="store_true", help='Also print the "what to do next" checklist')
    p.add_argument("--denied", action="store_true", help='Print how to appeal a FEMA "no" letter')
    p.add_argument("--offline", action="store_true", help="Template answer instead of Claude")
    p.add_argument("--fixtures", help="Read declarations from a JSON file instead of FEMA")
    p.add_argument("--as-of", help="Pretend today is this date (YYYY-MM-DD)")
    p.add_argument("--facts", action="store_true", help="Also print the facts sent to the model")
    args = p.parse_args()

    if args.denied and not (args.state or args.place):
        print(f"\n{denial_text(args.lang)}\n")
        return

    state, county = _pick_place(args, p)
    client = FixtureClient(args.fixtures) if args.fixtures else FemaClient()
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    report = build_report(client, state, county, as_of=as_of)
    explainer = TemplateExplainer() if args.offline else default_explainer()
    ans = explainer.explain(report, args.lang)

    print(f"\n{ans.text}\n")
    problems = check_answer(ans.text, report.to_payload())
    print(f"  grounding check: {'PASS' if not problems else 'FAIL ' + '; '.join(problems)}")
    print(f"  model={ans.model}  tokens={ans.input_tokens}+{ans.output_tokens}"
          f"  FEMA data refreshed={report.data_refreshed or 'n/a'}")
    if args.steps and not report.ambiguous_areas:
        print(f"\n{checklist_text(report, args.lang)}")
    if args.denied:
        print(f"\n{denial_text(args.lang)}")
    if args.facts:
        print(json.dumps(report.to_payload(), indent=2))


if __name__ == "__main__":
    main()
