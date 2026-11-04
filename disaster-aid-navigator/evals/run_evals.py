"""Evaluate status logic and answer grounding on fixed scenarios.

    python3 evals/run_evals.py --offline   # template answers, no API key
    python3 evals/run_evals.py             # Claude answers (needs ANTHROPIC_API_KEY)

Runs against the synthetic fixtures with today fixed at 2026-11-03, so
results are reproducible. Writes evals/results/<model>.md and .json.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from disaster_aid import FixtureClient, TemplateExplainer, build_report, check_answer, default_explainer  # noqa: E402

AS_OF = date(2026, 11, 3)
MAX_WORDS = 130
PRICES = {"input": 1.00, "output": 5.00}  # USD per million tokens; update for your model


def status_matches(report, sc) -> list[str]:
    errs = []
    if sc.get("expect_ambiguous"):
        if not report.ambiguous_areas:
            errs.append("expected an ambiguity prompt")
        return errs
    if report.ambiguous_areas:
        return [f"unexpected ambiguity: {report.ambiguous_areas}"]
    got = {d.code: d for d in report.declarations}
    want = {e["code"]: e for e in sc["expect"]}
    if set(got) != set(want):
        errs.append(f"declarations {sorted(got)} != expected {sorted(want)}")
    for code, e in want.items():
        d = got.get(code)
        if not d:
            continue
        if d.ia_status != e["ia_status"]:
            errs.append(f"{code} status {d.ia_status} != {e['ia_status']}")
        if "ia_deadline" in e and (d.ia_deadline is None or d.ia_deadline.isoformat() != e["ia_deadline"]):
            errs.append(f"{code} deadline {d.ia_deadline} != {e['ia_deadline']}")
        if d.deadline_is_estimate != e.get("deadline_is_estimate", False):
            errs.append(f"{code} estimate flag wrong")
    return errs


def run(explainer, scenarios) -> dict:
    client = FixtureClient(ROOT / "fixtures" / "declarations.json")
    rows = []
    for sc in scenarios:
        report = build_report(client, sc["state"], sc["county"], as_of=AS_OF)
        t0 = time.perf_counter()
        ans = explainer.explain(report)
        latency = (time.perf_counter() - t0) * 1000
        rows.append({
            "id": sc["id"], "query": f"{sc['county']}, {sc['state']}", "why": sc["why"],
            "status_errors": status_matches(report, sc),
            "grounding_problems": check_answer(ans.text, report.to_payload()),
            "words": len(ans.text.split()),
            "used_llm": ans.used_llm, "model": ans.model,
            "input_tokens": ans.input_tokens, "output_tokens": ans.output_tokens,
            "latency_ms": round(latency, 1), "answer": ans.text,
        })
    n = len(rows)
    pct = lambda xs: round(100 * sum(xs) / n, 1)
    llm_rows = [r for r in rows if r["used_llm"]]
    tin, tout = sum(r["input_tokens"] for r in rows), sum(r["output_tokens"] for r in rows)
    metrics = {
        "scenarios": n,
        "status_accuracy": pct([not r["status_errors"] for r in rows]),
        "grounded_answers": pct([not r["grounding_problems"] for r in rows]),
        "within_length": pct([r["words"] <= MAX_WORDS for r in rows]),
        "llm_calls": len(llm_rows),
        "llm_calls_avoided": n - len(llm_rows),
        "avg_input_tokens": round(tin / len(llm_rows)) if llm_rows else 0,
        "avg_output_tokens": round(tout / len(llm_rows)) if llm_rows else 0,
        "cost_per_answer_usd": round((tin * PRICES["input"] + tout * PRICES["output"]) / 1e6 / n, 5),
        "median_llm_latency_ms": statistics.median(r["latency_ms"] for r in llm_rows) if llm_rows else 0,
    }
    return {"model": explainer.model, "as_of": AS_OF.isoformat(), "metrics": metrics, "rows": rows}


def to_markdown(rep: dict) -> str:
    m = rep["metrics"]
    out = [f"# Eval results: `{rep['model']}`", "",
           f"{m['scenarios']} scenarios on synthetic fixtures, today fixed at {rep['as_of']}.", "",
           "| Metric | Value | What it measures |", "|---|---|---|",
           f"| Status accuracy | {m['status_accuracy']}% | Declarations, IA status and deadlines computed correctly |",
           f"| Grounded answers | {m['grounded_answers']}% | Every date, code and day count in the answer is in the facts |",
           f"| Within length | {m['within_length']}% | Answer is {MAX_WORDS} words or fewer |",
           f"| AI calls | {m['llm_calls']} of {m['scenarios']} | {m['llm_calls_avoided']} answered by rules with no AI call |",
           f"| Avg tokens per AI call | {m['avg_input_tokens']} in / {m['avg_output_tokens']} out | |",
           f"| Cost per answer | ${m['cost_per_answer_usd']} | |",
           f"| Median AI latency | {m['median_llm_latency_ms']} ms | |",
           "", "## Failures", ""]
    fails = [r for r in rep["rows"] if r["status_errors"] or r["grounding_problems"] or r["words"] > MAX_WORDS]
    out += ["None."] if not fails else []
    for r in fails:
        issues = r["status_errors"] + r["grounding_problems"] + ([f"{r['words']} words"] if r["words"] > MAX_WORDS else [])
        out += [f"- **{r['id']}** {r['query']}: {'; '.join(issues)}  ", f"  Answer: {r['answer'][:300]}"]
    return "\n".join(out) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--offline", action="store_true")
    args = p.parse_args()
    explainer = TemplateExplainer() if args.offline else default_explainer()
    scenarios = json.loads((ROOT / "evals" / "scenarios.json").read_text())
    rep = run(explainer, scenarios)
    out = ROOT / "evals" / "results"
    out.mkdir(exist_ok=True)
    (out / f"{rep['model']}.json").write_text(json.dumps(rep, indent=2))
    (out / f"{rep['model']}.md").write_text(to_markdown(rep))
    print(to_markdown(rep))


if __name__ == "__main__":
    main()
