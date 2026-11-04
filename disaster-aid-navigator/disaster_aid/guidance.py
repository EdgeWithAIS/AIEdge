"""Fixed, written-in-code help: a "what to do next" checklist and help after a denial.

This text is general FEMA guidance. It contains no disaster-specific dates or
numbers, so it never needs the AI and never needs the fact check. The one number,
the 60-day appeal window, is FEMA's rule for every disaster (counted from the
date on the decision letter).
"""
from __future__ import annotations

from .status import APPLY_URL, FEMA_HELPLINE, CountyReport

LANGS = ("en", "es")


def check_lang(lang: str | None) -> str:
    lang = (lang or "en").strip().lower()
    if lang not in LANGS:
        raise ValueError("Language must be 'en' (English) or 'es' (Spanish).")
    return lang


_COMMON = {
    "en": {
        "photos": "Take photos and videos of the damage before you clean up or throw things away.",
        "insurance": "If you have insurance, file a claim with your insurance company first. "
                     "FEMA cannot pay for things your insurance already covers.",
        "receipts": "Keep every receipt for repairs, hotel stays, and supplies.",
        "scams": "FEMA never charges a fee to apply. Do not pay anyone who offers to apply for you.",
    },
    "es": {
        "photos": "Tome fotos y videos de los daños antes de limpiar o tirar cosas.",
        "insurance": "Si tiene seguro, haga primero un reclamo con su compañía de seguros. "
                     "FEMA no puede pagar lo que su seguro ya cubre.",
        "receipts": "Guarde todos los recibos de reparaciones, hoteles y materiales.",
        "scams": "FEMA nunca cobra por solicitar ayuda. No le pague a nadie que le ofrezca "
                 "solicitar por usted.",
    },
}

_OPEN = {
    "en": [
        "photos",
        "insurance",
        f"Apply at {APPLY_URL}, in the FEMA App, or by calling {FEMA_HELPLINE}.",
        "Have this ready: a Social Security number for someone in your home, the address of the "
        "damaged home, a phone number, your insurance details, your household income, and your "
        "bank account if you want the money sent straight to it.",
        "Write down your FEMA application number. You will need it every time you talk to FEMA.",
        "FEMA may send an inspector to look at the damage. Answer calls from numbers you do not "
        "know, and ask to see the inspector's FEMA ID badge.",
        "receipts",
        "Read every letter from FEMA. If a letter says no, you can still appeal.",
        "scams",
    ],
    "es": [
        "photos",
        "insurance",
        f"Solicite ayuda en {APPLY_URL}, en la aplicación de FEMA, o llamando al {FEMA_HELPLINE}. "
        "Hay ayuda en español.",
        "Tenga esto a mano: un número de Seguro Social de alguien en su hogar, la dirección de la "
        "casa dañada, un número de teléfono, los datos de su seguro, el ingreso de su hogar y su "
        "cuenta de banco si quiere que le depositen el dinero directamente.",
        "Anote su número de solicitud de FEMA. Lo necesitará cada vez que hable con FEMA.",
        "FEMA puede enviar a un inspector para ver los daños. Conteste las llamadas de números que "
        "no conoce, y pídale al inspector que le muestre su identificación de FEMA.",
        "receipts",
        "Lea todas las cartas de FEMA. Si una carta dice que no, todavía puede apelar.",
        "scams",
    ],
}

_NOT_OPEN = {
    "en": [
        "photos",
        "insurance",
        "receipts",
        "Call 211 to find local help with food, a place to stay, and repairs.",
        "Check back here. FEMA can add a new disaster, or add your county, at any time.",
        "scams",
    ],
    "es": [
        "photos",
        "insurance",
        "receipts",
        "Llame al 211 para encontrar ayuda local con comida, un lugar donde quedarse y reparaciones.",
        "Vuelva a revisar aquí. FEMA puede agregar un desastre nuevo, o agregar su condado, "
        "en cualquier momento.",
        "scams",
    ],
}

_TITLES = {
    "en": {"next": "What to do next", "denial": "Did FEMA say no? How to appeal"},
    "es": {"next": "Qué hacer ahora", "denial": "¿FEMA le dijo que no? Cómo apelar"},
}


def next_steps(report: CountyReport | None, lang: str = "en") -> dict:
    """Checklist that fits the county's situation: apply now, or get ready."""
    lang = check_lang(lang)
    items = _OPEN[lang] if report is not None and report.open_ia else _NOT_OPEN[lang]
    return {"title": _TITLES[lang]["next"], "items": [_COMMON[lang].get(i, i) for i in items]}


def general_next_steps(lang: str = "en") -> dict:
    """The checklist for someone whose county is open for FEMA help, with no county given."""
    lang = check_lang(lang)
    return {"title": _TITLES[lang]["next"], "items": [_COMMON[lang].get(i, i) for i in _OPEN[lang]]}


_DENIAL = {
    "en": {
        "intro": "A \"no\" from FEMA is often not the end. Many people are turned down at first "
                 "only because FEMA is missing a paper.",
        "steps": [
            "Read the letter closely. It says why FEMA said no.",
            f"Call {FEMA_HELPLINE} or log in at {APPLY_URL} and ask what is missing. Sometimes you "
            "only need to send one paper, and you do not need to appeal.",
            "To appeal, write a letter that says why you think FEMA is wrong. Sign it. Put your "
            "full name, your FEMA application number, and the disaster number from your letter "
            "on every page.",
            "Add copies of papers that prove your case. Keep the originals.",
            "Send it within 60 days of the date on FEMA's letter. You can upload it at "
            f"{APPLY_URL}, mail it, fax it, or bring it to a Disaster Recovery Center.",
            "Keep a copy of everything you send, and write down the date you sent it.",
        ],
        "reasons_title": "Common reasons, and what to send",
        "reasons": [
            "Insurance: send the letter from your insurance company that shows what it paid "
            "or that it said no.",
            "FEMA could not confirm who you are: send a copy of a photo ID or a Social Security card.",
            "Proof you own the home: send a deed, property tax bill, mortgage statement, "
            "or homeowner's insurance papers.",
            "Proof you lived there: send a utility bill, lease, bank statement, or driver's "
            "license with that address.",
            "\"Not enough damage\": ask for a new inspection, and send photos and repair estimates.",
            "Someone else at your address already applied: explain who lives in your home, "
            "and send proof.",
        ],
        "legal": "Free legal help may be available after a disaster. Call FEMA and ask about "
                 "Disaster Legal Services.",
    },
    "es": {
        "intro": "Un \"no\" de FEMA muchas veces no es el final. A muchas personas les dicen que "
                 "no al principio solo porque a FEMA le falta un papel.",
        "steps": [
            "Lea la carta con cuidado. Dice por qué FEMA le dijo que no.",
            f"Llame al {FEMA_HELPLINE} o entre a {APPLY_URL} y pregunte qué falta. A veces solo "
            "necesita enviar un papel, y no tiene que apelar.",
            "Para apelar, escriba una carta que diga por qué cree que FEMA se equivocó. Fírmela. "
            "Ponga su nombre completo, su número de solicitud de FEMA y el número del desastre "
            "que aparece en su carta en cada página.",
            "Agregue copias de los papeles que prueban su caso. Guarde los originales.",
            "Envíela dentro de los 60 días desde la fecha de la carta de FEMA. Puede subirla en "
            f"{APPLY_URL}, enviarla por correo o por fax, o llevarla a un Centro de Recuperación "
            "por Desastre.",
            "Guarde una copia de todo lo que envíe, y anote la fecha en que lo envió.",
        ],
        "reasons_title": "Razones comunes, y qué enviar",
        "reasons": [
            "Seguro: envíe la carta de su compañía de seguros que muestra lo que pagó o que le "
            "dijo que no.",
            "FEMA no pudo confirmar quién es usted: envíe una copia de una identificación con "
            "foto o de su tarjeta de Seguro Social.",
            "Prueba de que la casa es suya: envíe una escritura, una factura de impuestos de la "
            "propiedad, un estado de cuenta de la hipoteca o los papeles del seguro de la casa.",
            "Prueba de que vivía allí: envíe una factura de luz o agua, un contrato de alquiler, "
            "un estado de cuenta del banco o una licencia de conducir con esa dirección.",
            "\"No hay suficientes daños\": pida una nueva inspección, y envíe fotos y "
            "presupuestos de reparación.",
            "Otra persona en su dirección ya solicitó: explique quién vive en su casa, y envíe "
            "pruebas.",
        ],
        "legal": "Puede haber ayuda legal gratis después de un desastre. Llame a FEMA y pregunte "
                 "por los Servicios Legales por Desastre.",
    },
}


def denial_help(lang: str = "en") -> dict:
    """What to do after a FEMA 'no' letter. The same for every disaster."""
    lang = check_lang(lang)
    return {"title": _TITLES[lang]["denial"], **_DENIAL[lang]}


def offers_denial_help(report: CountyReport) -> bool:
    """Show appeal help when families could have applied for any declaration found."""
    return any(d.individual_assistance for d in report.declarations)


def checklist_text(report: CountyReport | None, lang: str = "en") -> str:
    """Plain-text checklist for the command line."""
    block = next_steps(report, lang)
    return "\n".join([block["title"], *(f"  [ ] {item}" for item in block["items"])])


def denial_text(lang: str = "en") -> str:
    """Plain-text appeal help for the command line."""
    h = denial_help(lang)
    lines = [h["title"], h["intro"], ""]
    lines += [f"  {i}. {step}" for i, step in enumerate(h["steps"], 1)]
    lines += ["", h["reasons_title"], *(f"  - {r}" for r in h["reasons"]), "", h["legal"]]
    return "\n".join(lines)
