"""Recovery plans for agent mode: intake in fixed fields, plan steps checked in code.

A plan is the riskiest thing the agent writes: a list of things to do, some
with deadlines. So the same rule as the rest of the app, made stricter:

* **Intake is a fixed form.** Claude records what the person said with
  ``update_situation``; code keeps only known fields and known values. The
  browser keeps the record and sends it back each turn (it is only context:
  every fact in a plan is looked up again that turn).
* **Claude never writes a deadline.** A step that has one names the FEMA
  declaration (``deadline_code``); code looks it up and adds the date and days
  left. A date or day count typed into a step is rejected.
* **Every step has a source**: passages it cites, or a FEMA declaration. Every
  number and web address in a step must be in the passages it cites.
* **No open deadline can be left out.** If households can apply under any
  declaration the FEMA lookup returned, the plan must have a step for it.

A rejected plan goes back to Claude as a tool error listing each problem, so
it can fix the plan in the same answer.
"""
from __future__ import annotations

from .explainer import fmt_date
from .question import _state_code

MIN_STEPS, MAX_STEPS = 2, 8
MAX_STEP_CHARS = 280

HOUSING = ("renter", "owner", "other")
NEEDS = ("shelter", "repairs", "lost_documents", "lost_income", "food", "business_loss",
         "taxes", "health", "insurance_claim")

TOOL_SITUATION = {
    "name": "update_situation",
    "description": (
        "Record what the person has told you about their situation, for their recovery plan. Send "
        "only fields they actually told you; leave out anything they did not say. Returns the whole "
        "record so far and which fields the plan still needs."),
    "input_schema": {
        "type": "object",
        "properties": {
            "state": {"type": "string", "description": "Two-letter state code, e.g. TX"},
            "county": {"type": "string", "description": "County, parish or city, e.g. Harris County"},
            "housing": {"type": "string", "enum": list(HOUSING)},
            "insured": {"type": "boolean", "description": "Has insurance that may cover the damage"},
            "home_livable": {"type": "boolean", "description": "Can they safely live in their home now"},
            "has_business": {"type": "boolean", "description": "Owns a business that was damaged"},
            "needs": {"type": "array", "items": {"type": "string", "enum": list(NEEDS)}},
        },
        "additionalProperties": False,
    },
}

TOOL_PLAN = {
    "name": "write_plan",
    "description": (
        "Save the person's recovery plan: 2 to 8 steps, most urgent first. Call check_fema_aid for "
        "their county first in this same turn, and search_help_pages for the help they need. Each "
        "step is one plain sentence. Each step needs a source: cites (passage numbers it uses) and/or "
        "deadline_code (a FEMA declaration code whose application deadline applies). Never write a "
        "date or a day count in a step: code adds deadlines from deadline_code. Every declaration "
        "households can apply under must have a step with its deadline_code."),
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "string", "description": "What to do, in one plain sentence"},
                        "cites": {"type": "array", "items": {"type": "integer"}},
                        "deadline_code": {"type": "string", "description": "e.g. DR-4877-MO"},
                    },
                    "required": ["step"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["steps"],
        "additionalProperties": False,
    },
}

PLAN_TEXT = {
    "en": {"title": "Your recovery plan",
           "deadline": "Apply by {date}. {days} days left.",
           "deadline_today": "Apply by {date}. Today is the last day.",
           "estimate": "Apply by about {date} (an estimate). About {days} days left."},
    "es": {"title": "Su plan de recuperación",
           "deadline": "Solicite antes del {date}. Quedan {days} días.",
           "deadline_today": "Solicite antes del {date}. Hoy es el último día.",
           "estimate": "Solicite antes del {date}, aproximadamente (es un estimado). Quedan unos {days} días."},
}


# --- Intake ------------------------------------------------------------------------------

def clean_situation(raw) -> dict:
    """Keep only known fields with allowed values. Anything else is dropped, not trusted."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    state = _state_code(str(raw.get("state") or "")) if raw.get("state") else None
    if state:
        out["state"] = state
    county = str(raw.get("county") or "").strip()
    if county and len(county) <= 80:
        out["county"] = county
    if raw.get("housing") in HOUSING:
        out["housing"] = raw["housing"]
    for key in ("insured", "home_livable", "has_business"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    needs = raw.get("needs")
    if isinstance(needs, list):
        kept = [n for n in NEEDS if n in needs]
        if kept:
            out["needs"] = kept
    return out


def merge_situation(current: dict, update: dict) -> dict:
    """New answers replace old ones; needs are added to, not replaced."""
    merged = {**current, **{k: v for k, v in update.items() if k != "needs"}}
    needs = list(current.get("needs", []))
    needs += [n for n in update.get("needs", []) if n not in needs]
    if needs:
        merged["needs"] = [n for n in NEEDS if n in needs]
    return merged


def missing_for_plan(situation: dict) -> list[str]:
    missing = []
    if not (situation.get("state") and situation.get("county")):
        missing.append("location (county and state, or a ZIP code)")
    if "housing" not in situation:
        missing.append("housing (renter or owner)")
    return missing


# --- Plan check --------------------------------------------------------------------------

def check_plan(steps, *, declarations: list[dict] | None, passages: list, lang: str,
               check_text) -> tuple[list[dict], list[str]]:
    """(plan steps ready to show, problems). Steps are only returned when there are no problems.

    ``declarations`` are FEMA rows from this turn's lookups (None when FEMA
    was not checked; empty when the county has no recent declarations); ``passages`` the
    numbered page passages Claude was shown; ``check_text(text, passages)`` is
    the agent's fact check, run on each step with its citations.
    """
    if not isinstance(steps, list) or not MIN_STEPS <= len(steps) <= MAX_STEPS:
        return [], [f"A plan needs {MIN_STEPS} to {MAX_STEPS} steps."]
    if declarations is None:
        return [], ["Call check_fema_aid for the person's county before writing the plan."]
    by_code = {d["code"]: d for d in declarations}
    open_codes = [d["code"] for d in declarations if d.get("ia_status") == "open"]
    t = PLAN_TEXT[lang]
    problems, out, used_codes = [], [], set()

    for i, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            problems.append(f"Step {i} is not an object.")
            continue
        text = " ".join(str(raw.get("step") or "").split())
        cites = raw.get("cites") or []
        code = str(raw.get("deadline_code") or "").strip().upper() or None
        if not text or len(text) > MAX_STEP_CHARS:
            problems.append(f"Step {i} must be one sentence of at most {MAX_STEP_CHARS} characters.")
            continue
        if not isinstance(cites, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in cites):
            problems.append(f"Step {i}: cites must be a list of passage numbers.")
            continue
        if not cites and not code:
            problems.append(f"Step {i} has no source. Cite a passage or give a deadline_code.")
        deadline = None
        if code:
            row = by_code.get(code)
            if row is None:
                problems.append(f"Step {i}: {code} is not a declaration from check_fema_aid.")
            elif row.get("ia_status") != "open":
                problems.append(f"Step {i}: {code} is not open for household applications.")
            else:
                used_codes.add(code)
                deadline = _deadline(row, t, lang)
        cited_text = text + "".join(f" [{n}]" for n in cites)
        for p in check_text(cited_text, passages):
            problems.append(f"Step {i}: {p}")
        out.append({"n": i, "step": text, "cites": sorted(set(cites)), "deadline": deadline})

    for code in open_codes:
        if code not in used_codes:
            problems.append(f"Households can apply under {code}: add a step with deadline_code {code}.")
    return (out, []) if not problems else ([], problems)


def _deadline(row: dict, t: dict, lang: str) -> dict:
    when = fmt_date(row["ia_deadline"], lang)
    days = row.get("days_left", 0)
    if row.get("deadline_is_estimate"):
        text = t["estimate"].format(date=when, days=days)
    elif days == 0:
        text = t["deadline_today"].format(date=when)
    else:
        text = t["deadline"].format(date=when, days=days)
    return {"code": row["code"], "date": row["ia_deadline"], "days_left": days,
            "estimate": bool(row.get("deadline_is_estimate")), "text": text}


def plan_payload(steps: list[dict], lang: str) -> dict:
    return {"title": PLAN_TEXT[lang]["title"], "steps": steps}
