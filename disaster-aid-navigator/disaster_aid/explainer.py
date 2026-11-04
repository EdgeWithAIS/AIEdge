"""Explain a CountyReport in plain English.

* No declarations, or an ambiguous county name: a fixed message, no AI call.
* Otherwise ``ClaudeExplainer`` writes a short answer from the facts JSON,
  or ``TemplateExplainer`` does it offline with no API key.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date

from .fema_client import STATE_NAMES
from .geo import friendly
from .status import APPLY_URL, FEMA_HELPLINE, CountyReport, Declaration

SYSTEM_PROMPT = f"""You explain FEMA disaster news to families. Write so someone who finished 8th grade can understand it.
Use short sentences and everyday words. Do not use "Individual Assistance", "Incident Period", or "Public Assistance".
Use ONLY the facts in the JSON. Never add dates, numbers, or advice that are not in it.
Start with one sentence: can families in this county apply for FEMA help right now?
Then print one block per declaration, with a blank line between blocks, like this:

Severe storms and flooding in Missouri (DR-4885-MO)
This happened from May 23, 2025 to May 26, 2025.
FEMA declared a major disaster on July 22, 2025.
The last day to apply was September 22, 2025. That date has passed.

Rules:
- First line: the title in plain words, "in" the full state name, then the code in parentheses.
- If both incident dates exist and differ: "This happened from {{begin}} to {{end}}."
- If only one incident date exists: "This happened on {{date}}."
- If both are missing, skip that line.
- Write dates like "July 22, 2025".
- Major Disaster: "FEMA declared a major disaster on {{declaration_date}}."
- Emergency: "FEMA declared an emergency on {{declaration_date}}."
- Fire Management: "FEMA declared help for a fire on {{declaration_date}}."
- If ia_status is open and days_left is more than 0: "You can apply until {{date}}. You have {{days_left}} days left."
- If ia_status is open and days_left is 0: "You can apply until {{date}}. That is today."
- If deadline_is_estimate is true, add: "This date is an estimate. FEMA usually gives 60 days, and it has not published an official date."
- If ia_status is closed: "The last day to apply was {{date}}. That date has passed."
- If the only program is Public Assistance: "This money goes to local government, not to families."
End with: To apply, or to check your date, go to {APPLY_URL} or call {FEMA_HELPLINE}."""

DECLARED_LINE = {
    "Major Disaster": "FEMA declared a major disaster on {date}.",
    "Emergency": "FEMA declared an emergency on {date}.",
    "Fire Management": "FEMA declared help for a fire on {date}.",
}


@dataclass
class Explanation:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    used_llm: bool = False


def fmt_date(d: date | str) -> str:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d:%B} {d.day}, {d.year}"


def plain_title(text: str) -> str:
    """'Severe Storms and Flooding' -> 'Severe storms and flooding'."""
    return text[:1].upper() + text[1:].lower() if text else text


def what_happened(begin: date | None, end: date | None) -> str | None:
    if begin and end and begin != end:
        return f"This happened from {fmt_date(begin)} to {fmt_date(end)}."
    if begin or end:
        return f"This happened on {fmt_date(begin or end)}."
    return None


def format_declaration(state: str, d: Declaration) -> str:
    """Plain-language declaration block. Dates and codes stay exact for the fact check."""
    name = STATE_NAMES.get(state.upper(), state)
    lines = [f"{plain_title(d.title)} in {name} ({d.code})"]
    happened = what_happened(d.incident_begin, d.incident_end)
    if happened:
        lines.append(happened)
    declared = DECLARED_LINE.get(d.declaration_type, "FEMA declared this on {date}.")
    lines.append(declared.format(date=fmt_date(d.declaration_date)))
    if d.ia_status == "open":
        if d.days_left == 0:
            line = f"You can apply until {fmt_date(d.ia_deadline)}. That is today."
        else:
            line = f"You can apply until {fmt_date(d.ia_deadline)}. You have {d.days_left} days left."
        if d.deadline_is_estimate:
            line += " This date is an estimate. FEMA usually gives 60 days, and it has not published an official date."
        lines.append(line)
    elif d.ia_status == "closed":
        lines.append(f"The last day to apply was {fmt_date(d.ia_deadline)}. That date has passed.")
    elif d.public_assistance:
        lines.append("This money goes to local government, not to families.")
    else:
        lines.append("This does not include help for families.")
    return "\n".join(lines)


def no_llm_answer(report: CountyReport) -> str | None:
    """Answers that need no AI. Returns None when the AI should explain."""
    if report.ambiguous_areas:
        names = [friendly(a) for a in report.ambiguous_areas]
        return (f"\"{report.county_query}\" could be more than one place in {report.state}: "
                f"{' or '.join(names)}. Please name one, for example \"{names[0]}\".")
    if not report.declarations:
        years = round(report.lookback_days / 365)
        return (f"No FEMA disaster was found for {report.county_query}, {report.state} "
                f"in the last {years} years. Help for families is not open there right now. "
                f"If your home was damaged recently, check {APPLY_URL} or call {FEMA_HELPLINE}. "
                f"A new disaster can be added at any time.")
    return None


class TemplateExplainer:
    """Offline, deterministic baseline."""

    model = "template-baseline"

    def explain(self, report: CountyReport) -> Explanation:
        fixed = no_llm_answer(report)
        if fixed:
            return Explanation(fixed, "rule")
        if report.open_ia:
            lead = f"Yes. Families in {report.county_name} can apply for FEMA help now."
        else:
            lead = f"No. FEMA help for families in {report.county_name} is not open right now."
        blocks = [lead, *(format_declaration(report.state, d) for d in report.declarations)]
        blocks.append(f"To apply, or to check your date, go to {APPLY_URL} or call {FEMA_HELPLINE}.")
        return Explanation("\n\n".join(blocks), self.model)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class ClaudeExplainer:
    """Plain-English answers from Claude, grounded in the facts JSON."""

    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str | None = None, api_key: str | None = None, max_tokens: int = 3000):
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("Set ANTHROPIC_API_KEY to use ClaudeExplainer")
        self.max_tokens = max_tokens

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.API_URL, data=json.dumps(payload).encode(),
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as err:
            body = err.read().decode(errors="replace")
            try:
                message = json.loads(body)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = body
            raise RuntimeError(f"Anthropic API error {err.code} (model={self.model}): {message}") from None

    def explain(self, report: CountyReport) -> Explanation:
        fixed = no_llm_answer(report)
        if fixed:  # Saves a model call and removes any chance of a made-up answer.
            return Explanation(fixed, "rule")
        facts = json.dumps(report.to_payload(), separators=(",", ":"))
        data = self._post({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": f"Facts:\n{facts}"}],
        })
        text = "".join(b.get("text", "") for b in data.get("content", [])).strip()
        u = data.get("usage", {})
        return Explanation(text, self.model, u.get("input_tokens", 0), u.get("output_tokens", 0), True)


def default_explainer():
    return ClaudeExplainer() if os.environ.get("ANTHROPIC_API_KEY") else TemplateExplainer()
