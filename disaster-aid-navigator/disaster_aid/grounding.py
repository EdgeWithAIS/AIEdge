"""Check an answer against the facts it was written from.

A wrong deadline could cost someone their disaster aid, so every answer is
checked in code before it is trusted:

1. Every date in the answer must appear in the facts.
2. Every declaration code (DR-/EM-/FM-) must appear in the facts.
3. Every "N days" count must match a days_left value.
4. Every open deadline must be stated in the answer.
5. Estimated deadlines must be called estimates.

English and Spanish answers are both checked ("30 de noviembre de 2026", "27 días").
"""
from __future__ import annotations

import re
from datetime import date, datetime

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
SHORT = "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
DATE_PATTERNS = [
    (rf"\b(?:{MONTHS})\s+\d{{1,2}},\s*\d{{4}}", ["%B %d, %Y", "%B %d,%Y"]),
    (rf"\b(?:{SHORT})\.?\s+\d{{1,2}},\s*\d{{4}}", ["%b %d, %Y", "%b. %d, %Y"]),
    (r"\b\d{4}-\d{2}-\d{2}\b", ["%Y-%m-%d"]),
    (r"\b\d{1,2}/\d{1,2}/\d{4}\b", ["%m/%d/%Y"]),
]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
SPANISH_DATE_RE = re.compile(rf"\b(\d{{1,2}})\s+de\s+({'|'.join(MESES)})\s+de\s+(\d{{4}})\b", re.I)
CODE_RE = re.compile(r"\b(?:DR|EM|FM)-\d{3,5}(?:-[A-Z]{2})?\b")
DAYS_RE = re.compile(r"\b(\d{1,3})\s+(?:days?|d[ií]as?)\b", re.I)


def _parse(text: str, formats: list[str]) -> date | None:
    text = re.sub(r"\s+", " ", text.replace("Sept", "Sep"))
    for f in formats:
        try:
            return datetime.strptime(text, f).date()
        except ValueError:
            pass
    return None


def dates_in(text: str) -> set[date]:
    found = set()
    for pattern, formats in DATE_PATTERNS:
        for m in re.finditer(pattern, text):
            d = _parse(m.group(0), formats)
            if d:
                found.add(d)
    for m in SPANISH_DATE_RE.finditer(text):
        try:
            found.add(date(int(m.group(3)), MESES.index(m.group(2).lower()) + 1, int(m.group(1))))
        except ValueError:
            pass
    return found


def check_answer(answer: str, payload: dict) -> list[str]:
    """Return a list of problems. An empty list means the answer is grounded."""
    problems = []
    decls = payload.get("declarations", [])

    allowed_dates = {date.fromisoformat(payload["today"])}
    for d in decls:
        for key, value in d.items():
            if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                allowed_dates.add(date.fromisoformat(value))
    for d in sorted(dates_in(answer) - allowed_dates):
        problems.append(f"date not in facts: {d.isoformat()}")

    allowed_codes = {d["code"] for d in decls}
    allowed_short = {c.rsplit("-", 1)[0] for c in allowed_codes}
    for code in sorted(set(CODE_RE.findall(answer))):
        if code not in allowed_codes and code not in allowed_short:
            problems.append(f"declaration code not in facts: {code}")

    allowed_days = {d["days_left"] for d in decls if "days_left" in d}
    allowed_days.add(60)  # FEMA's usual IA application window cited in estimate wording
    for n in sorted({int(x) for x in DAYS_RE.findall(answer)}):
        if n not in allowed_days:
            problems.append(f"day count not in facts: {n} days")

    for d in decls:
        if d.get("ia_status") == "open":
            if date.fromisoformat(d["ia_deadline"]) not in dates_in(answer):
                problems.append(f"open deadline missing for {d['code']}")
            if d.get("deadline_is_estimate") and "estima" not in answer.lower():
                problems.append(f"estimated deadline for {d['code']} not called an estimate")
    return problems
