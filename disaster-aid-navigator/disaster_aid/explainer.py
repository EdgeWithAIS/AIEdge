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

from .geo import friendly
from .status import APPLY_URL, FEMA_HELPLINE, CountyReport

SYSTEM_PROMPT = f"""You explain FEMA disaster declarations to residents in plain English.
Use ONLY the facts in the JSON you are given. Never add dates, numbers, programs or advice that are not in it.
Rules:
- At most 120 words. No headings. Short sentences a 9th grader can follow.
- Start with the answer: can households in this county apply for FEMA Individual Assistance right now?
- For each declaration, name it by its code (for example DR-1234-MO) and title.
- Give deadlines exactly as the date in the JSON, written like "November 30, 2026", plus days_left when present.
- If deadline_is_estimate is true, say the date is an estimate based on FEMA's usual 60-day window and that FEMA has not published an official date.
- "Public Assistance" helps local governments, not households. Say so if it is the only program.
- End with: Apply or check your deadline at {APPLY_URL} or {FEMA_HELPLINE}."""


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


def no_llm_answer(report: CountyReport) -> str | None:
    """Answers that need no AI. Returns None when the AI should explain."""
    if report.ambiguous_areas:
        names = [friendly(a) for a in report.ambiguous_areas]
        return (f"\"{report.county_query}\" matches more than one area in {report.state}: "
                f"{' or '.join(names)}. Please say which one, for example \"{names[0]}\".")
    if not report.declarations:
        years = round(report.lookback_days / 365)
        return (f"No federal disaster declarations were found for {report.county_query}, "
                f"{report.state} in the last {years} years, so FEMA Individual Assistance is not "
                f"available there right now. If you had damage recently, check {APPLY_URL} or call "
                f"{FEMA_HELPLINE}, because new declarations can be added at any time.")
    return None


class TemplateExplainer:
    """Offline, deterministic baseline."""

    model = "template-baseline"

    def explain(self, report: CountyReport) -> Explanation:
        fixed = no_llm_answer(report)
        if fixed:
            return Explanation(fixed, "rule")
        parts = []
        open_ia = report.open_ia
        if open_ia:
            parts.append(f"Yes. Households in {report.county_name} can apply for FEMA Individual Assistance now.")
        else:
            parts.append(f"No. FEMA Individual Assistance is not open for {report.county_name} right now.")
        for d in report.declarations:
            line = f"{d.code} ({d.title}, declared {fmt_date(d.declaration_date)}): "
            if d.ia_status == "open":
                line += f"apply by {fmt_date(d.ia_deadline)} ({d.days_left} days left)."
                if d.deadline_is_estimate:
                    line += " This date is an estimate based on FEMA's usual 60-day window; FEMA has not published an official date."
            elif d.ia_status == "closed":
                line += f"the deadline to apply was {fmt_date(d.ia_deadline)}."
            elif d.public_assistance:
                line += "Public Assistance only, which helps local governments, not households."
            else:
                line += "no assistance for households."
            parts.append(line)
        parts.append(f"Apply or check your deadline at {APPLY_URL} or {FEMA_HELPLINE}.")
        return Explanation(" ".join(parts), self.model)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class ClaudeExplainer:
    """Plain-English answers from Claude, grounded in the facts JSON."""

    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str | None = None, api_key: str | None = None, max_tokens: int = 300):
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
