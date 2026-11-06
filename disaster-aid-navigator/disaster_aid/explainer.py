"""Explain a CountyReport in plain English or Spanish.

* No declarations, or an ambiguous county name: a fixed message, no AI call.
* Otherwise ``ClaudeExplainer`` writes a short answer from the facts JSON,
  or ``TemplateExplainer`` does it offline with no API key.

``lang`` is "en" (English, the default) or "es" (Spanish). Dates and
declaration codes stay exact in both, so the same fact check runs on both.
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
from .guidance import check_lang
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

# One language per answer: the rules for the chosen language go last.
ENGLISH_RULES = """

LANGUAGE: Write the whole answer in English only. Do not add Spanish or any other language."""

SPANISH_RULES = f"""

LANGUAGE: Write the whole answer in simple Spanish (español sencillo), using "usted".
Write only in Spanish. Do not copy FEMA's English title or any other English words, and do not repeat anything in English.
Only declaration codes, the web address and the phone number stay exactly as they are.
Write every date like "22 de julio de 2025".
Use these sentences in place of the English ones:
- First sentence: "Sí. Las familias de {{county}} pueden pedir ayuda de FEMA ahora." or "No. La ayuda de FEMA para familias de {{county}} no está abierta ahora."
- Block first line: the kind of disaster in Spanish (from incident_type), "en" the state name in Spanish, then the code. Example: "Tormentas fuertes en Misuri (DR-4885-MO)".
- "Esto pasó del {{begin}} al {{end}}." or "Esto pasó el {{date}}."
- "FEMA declaró un desastre mayor el {{date}}." / "FEMA declaró una emergencia el {{date}}." / "FEMA declaró ayuda por un incendio el {{date}}."
- "Puede pedir ayuda hasta el {{date}}. Le quedan {{days_left}} días." or, when days_left is 0, "Puede pedir ayuda hasta el {{date}}. Hoy es el último día."
- Estimate: "Esta fecha es estimada. FEMA suele dar 60 días y no ha publicado una fecha oficial."
- "El último día para pedir ayuda fue el {{date}}. Esa fecha ya pasó."
- "Este dinero es para el gobierno local, no para las familias."
End with: Para pedir ayuda o revisar su fecha, visite {APPLY_URL} o llame al {FEMA_HELPLINE}. Hay ayuda en español."""

MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]

# Spanish names where they differ from English.
STATE_NAMES_ES = {
    "DC": "el Distrito de Columbia", "HI": "Hawái", "LA": "Luisiana", "MI": "Míchigan",
    "MS": "Misisipi", "MO": "Misuri", "NJ": "Nueva Jersey", "NM": "Nuevo México",
    "NY": "Nueva York", "NC": "Carolina del Norte", "SC": "Carolina del Sur",
    "ND": "Dakota del Norte", "SD": "Dakota del Sur", "OR": "Oregón", "PA": "Pensilvania",
    "WV": "Virginia Occidental", "AS": "Samoa Estadounidense", "MP": "las Islas Marianas del Norte",
    "VI": "las Islas Vírgenes de EE. UU.",
}

# OpenFEMA incidentType -> Spanish. FEMA titles are free text, so Spanish answers
# name the kind of disaster in Spanish and show FEMA's official English title too.
INCIDENT_ES = {
    "Biological": "Emergencia biológica", "Chemical": "Emergencia química",
    "Coastal Storm": "Tormenta costera", "Dam/Levee Break": "Rotura de presa o dique",
    "Drought": "Sequía", "Earthquake": "Terremoto", "Fire": "Incendio",
    "Fishing Losses": "Pérdidas de pesca", "Flood": "Inundaciones", "Freezing": "Heladas",
    "Hurricane": "Huracán", "Mud/Landslide": "Deslaves", "Severe Ice Storm": "Tormenta de hielo",
    "Severe Storm": "Tormentas fuertes", "Snowstorm": "Tormenta de nieve",
    "Straight-Line Winds": "Vientos fuertes", "Tornado": "Tornados",
    "Tropical Storm": "Tormenta tropical", "Tsunami": "Tsunami", "Typhoon": "Tifón",
    "Volcanic Eruption": "Erupción volcánica", "Winter Storm": "Tormenta invernal",
}

TEXT = {
    "en": {
        "declared": {
            "Major Disaster": "FEMA declared a major disaster on {date}.",
            "Emergency": "FEMA declared an emergency on {date}.",
            "Fire Management": "FEMA declared help for a fire on {date}.",
        },
        "declared_other": "FEMA declared this on {date}.",
        "range": "This happened from {begin} to {end}.",
        "on": "This happened on {date}.",
        "open": "You can apply until {date}. You have {n} days left.",
        "open_one": "You can apply until {date}. You have 1 day left.",
        "open_today": "You can apply until {date}. That is today.",
        "estimate": "This date is an estimate. FEMA usually gives 60 days, and it has not published an official date.",
        "closed": "The last day to apply was {date}. That date has passed.",
        "pa_only": "This money goes to local government, not to families.",
        "none": "This does not include help for families.",
        "yes": "Yes. Families in {county} can apply for FEMA help now.",
        "no": "No. FEMA help for families in {county} is not open right now.",
        "end": f"To apply, or to check your date, go to {APPLY_URL} or call {FEMA_HELPLINE}.",
        "ambiguous": "\"{query}\" could be more than one place in {state}: {names}. "
                     "Please name one, for example \"{first}\".",
        "or": " or ",
        "no_decl": (f"No FEMA disaster was found for {{query}}, {{state}} in the last {{years}} years. "
                    f"Help for families is not open there right now. If your home was damaged recently, "
                    f"check {APPLY_URL} or call {FEMA_HELPLINE}. A new disaster can be added at any time."),
    },
    "es": {
        "declared": {
            "Major Disaster": "FEMA declaró un desastre mayor el {date}.",
            "Emergency": "FEMA declaró una emergencia el {date}.",
            "Fire Management": "FEMA declaró ayuda por un incendio el {date}.",
        },
        "declared_other": "FEMA hizo esta declaración el {date}.",
        "range": "Esto pasó del {begin} al {end}.",
        "on": "Esto pasó el {date}.",
        "open": "Puede pedir ayuda hasta el {date}. Le quedan {n} días.",
        "open_one": "Puede pedir ayuda hasta el {date}. Le queda 1 día.",
        "open_today": "Puede pedir ayuda hasta el {date}. Hoy es el último día.",
        "estimate": "Esta fecha es estimada. FEMA suele dar 60 días y no ha publicado una fecha oficial.",
        "closed": "El último día para pedir ayuda fue el {date}. Esa fecha ya pasó.",
        "pa_only": "Este dinero es para el gobierno local, no para las familias.",
        "none": "Esto no incluye ayuda para familias.",
        "yes": "Sí. Las familias de {county} pueden pedir ayuda de FEMA ahora.",
        "no": "No. La ayuda de FEMA para familias de {county} no está abierta ahora.",
        "end": (f"Para pedir ayuda o revisar su fecha, visite {APPLY_URL} o llame al {FEMA_HELPLINE}. "
                "Hay ayuda en español."),
        "ambiguous": "\"{query}\" puede ser más de un lugar en {state}: {names}. "
                     "Por favor, elija uno; por ejemplo, \"{first}\".",
        "or": " o ",
        "no_decl": (f"No se encontró ningún desastre de FEMA para {{query}}, {{state}} en los últimos "
                    f"{{years}} años. La ayuda para familias no está abierta allí ahora. Si su casa sufrió "
                    f"daños hace poco, visite {APPLY_URL} o llame al {FEMA_HELPLINE}. FEMA puede agregar "
                    f"un desastre nuevo en cualquier momento."),
    },
}

# Kept for callers that used the English table directly.
DECLARED_LINE = TEXT["en"]["declared"]


@dataclass
class Explanation:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    used_llm: bool = False


def fmt_date(d: date | str, lang: str = "en") -> str:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    if lang == "es":
        return f"{d.day} de {MONTHS_ES[d.month - 1]} de {d.year}"
    return f"{d:%B} {d.day}, {d.year}"


def plain_title(text: str) -> str:
    """'Severe Storms and Flooding' -> 'Severe storms and flooding'."""
    return text[:1].upper() + text[1:].lower() if text else text


def state_name(state: str, lang: str = "en") -> str:
    code = state.upper()
    if lang == "es" and code in STATE_NAMES_ES:
        return STATE_NAMES_ES[code]
    return STATE_NAMES.get(code, state)


def what_happened(begin: date | None, end: date | None, lang: str = "en") -> str | None:
    t = TEXT[lang]
    if begin and end and begin != end:
        return t["range"].format(begin=fmt_date(begin, lang), end=fmt_date(end, lang))
    if begin or end:
        return t["on"].format(date=fmt_date(begin or end, lang))
    return None


def status_line(d: Declaration, lang: str = "en") -> str:
    """Can families apply for this declaration, and until when."""
    t = TEXT[lang]
    if d.ia_status == "open":
        key = "open_today" if d.days_left == 0 else "open_one" if d.days_left == 1 else "open"
        line = t[key].format(date=fmt_date(d.ia_deadline, lang), n=d.days_left)
        if d.deadline_is_estimate:
            line += " " + t["estimate"]
        return line
    if d.ia_status == "closed":
        return t["closed"].format(date=fmt_date(d.ia_deadline, lang))
    if d.public_assistance:
        return t["pa_only"]
    return t["none"]


def declaration_heading(state: str, d: Declaration, lang: str = "en") -> str:
    """'Severe storms in Missouri' / 'Tormentas fuertes en Misuri'. Spanish never shows FEMA's English title."""
    if lang == "es":
        return f"{INCIDENT_ES.get(d.incident_type, 'Desastre')} en {state_name(state, lang)}"
    return f"{plain_title(d.title)} in {state_name(state)}"


def format_declaration(state: str, d: Declaration, lang: str = "en") -> str:
    """Plain-language declaration block. Dates and codes stay exact for the fact check."""
    t = TEXT[lang]
    lines = [f"{declaration_heading(state, d, lang)} ({d.code})"]
    happened = what_happened(d.incident_begin, d.incident_end, lang)
    if happened:
        lines.append(happened)
    declared = t["declared"].get(d.declaration_type, t["declared_other"])
    lines.append(declared.format(date=fmt_date(d.declaration_date, lang)))
    lines.append(status_line(d, lang))
    return "\n".join(lines)


def no_llm_answer(report: CountyReport, lang: str = "en") -> str | None:
    """Answers that need no AI. Returns None when the AI should explain."""
    t = TEXT[lang]
    if report.ambiguous_areas:
        names = [friendly(a) for a in report.ambiguous_areas]
        return t["ambiguous"].format(query=report.county_query, state=report.state,
                                     names=t["or"].join(names), first=names[0])
    if not report.declarations:
        years = round(report.lookback_days / 365)
        return t["no_decl"].format(query=report.county_query, state=report.state, years=years)
    return None


class TemplateExplainer:
    """Offline, deterministic baseline."""

    model = "template-baseline"

    def explain(self, report: CountyReport, lang: str = "en") -> Explanation:
        lang = check_lang(lang)
        fixed = no_llm_answer(report, lang)
        if fixed:
            return Explanation(fixed, "rule")
        t = TEXT[lang]
        lead = (t["yes"] if report.open_ia else t["no"]).format(county=report.county_name)
        blocks = [lead, *(format_declaration(report.state, d, lang) for d in report.declarations)]
        blocks.append(t["end"])
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

    def explain(self, report: CountyReport, lang: str = "en") -> Explanation:
        lang = check_lang(lang)
        fixed = no_llm_answer(report, lang)
        if fixed:  # Saves a model call and removes any chance of a made-up answer.
            return Explanation(fixed, "rule")
        facts = json.dumps(report.to_payload(), separators=(",", ":"))
        data = self._post({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT + (SPANISH_RULES if lang == "es" else ENGLISH_RULES),
            "messages": [{"role": "user", "content": f"Facts:\n{facts}"}],
        })
        text = "".join(b.get("text", "") for b in data.get("content", [])).strip()
        u = data.get("usage", {})
        return Explanation(text, self.model, u.get("input_tokens", 0), u.get("output_tokens", 0), True)


def default_explainer():
    return ClaudeExplainer() if os.environ.get("ANTHROPIC_API_KEY") else TemplateExplainer()
