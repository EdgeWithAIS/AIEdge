"""Command line.

    python3 -m disaster_aid.cli MO "St. Louis County"            # live FEMA data
    python3 -m disaster_aid.cli MO "St. Louis County" --offline  # no API key needed
    python3 -m disaster_aid.cli MO "St. Louis County" --fixtures fixtures/declarations.json --as-of 2026-11-03
"""
from __future__ import annotations

import argparse
from datetime import date

from .explainer import TemplateExplainer, default_explainer
from .fema_client import FemaClient, FixtureClient
from .grounding import check_answer
from .status import build_report


def main() -> None:
    p = argparse.ArgumentParser(description="Can households in my county get FEMA disaster aid?")
    p.add_argument("state", help="Two-letter state code, e.g. MO")
    p.add_argument("county", help='County name, e.g. "St. Louis County"')
    p.add_argument("--offline", action="store_true", help="Template answer instead of Claude")
    p.add_argument("--fixtures", help="Read declarations from a JSON file instead of FEMA")
    p.add_argument("--as-of", help="Pretend today is this date (YYYY-MM-DD)")
    p.add_argument("--facts", action="store_true", help="Also print the facts sent to the model")
    args = p.parse_args()

    client = FixtureClient(args.fixtures) if args.fixtures else FemaClient()
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    report = build_report(client, args.state, args.county, as_of=as_of)
    explainer = TemplateExplainer() if args.offline else default_explainer()
    ans = explainer.explain(report)

    print(f"\n{ans.text}\n")
    problems = check_answer(ans.text, report.to_payload())
    print(f"  grounding check: {'PASS' if not problems else 'FAIL ' + '; '.join(problems)}")
    print(f"  model={ans.model}  tokens={ans.input_tokens}+{ans.output_tokens}"
          f"  FEMA data refreshed={report.data_refreshed or 'n/a'}")
    if args.facts:
        import json
        print(json.dumps(report.to_payload(), indent=2))


if __name__ == "__main__":
    main()
