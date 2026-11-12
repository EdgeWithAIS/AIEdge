"""Fixed, written-in-code help: a "what to do next" checklist and help after a denial.

This text is general FEMA guidance. It contains no disaster-specific dates or
numbers, so it never needs the AI and never needs the fact check. The one number,
the 60-day appeal window, is FEMA's rule for every disaster (counted from the
date on the decision letter).
"""
from __future__ import annotations

import re

from .status import APPLY_URL, FEMA_HELPLINE, CountyReport

LANGS = ("en", "es")

# Spanish markers in a free-text question. Asking in Spanish (or asking for
# Spanish) selects the Spanish answer; otherwise English.
_SPANISH_CHARS = re.compile(r"[¿¡áéíóúüñÁÉÍÓÚÜÑ]")
_SPANISH_WORDS = re.compile(
    r"\b(español|espanol|familia[s]?|pueden|puedo|puede|ayuda|desastre|"
    r"condado|parroquia|ciudad|código|codigo|postal|después|despues|"
    r"qué|que|cómo|como|cuándo|cuando|dónde|donde|hasta|cuánto|cuanto|"
    r"necesito|quiero|solicitar|reclamo|negaron|dijo)\b",
    re.I,
)
_WANT_SPANISH = re.compile(r"\b(in\s+spanish|en\s+espa[nñ]ol)\b", re.I)


def check_lang(lang: str | None) -> str:
    lang = (lang or "en").strip().lower()
    if lang not in LANGS:
        raise ValueError("Language must be 'en' (English) or 'es' (Spanish).")
    return lang


def detect_lang(text: str | None) -> str:
    """Pick answer language from what the person wrote.

    Spanish answers when the question is in Spanish or asks for Spanish;
    otherwise English.
    """
    raw = (text or "").strip()
    if not raw:
        return "en"
    if _WANT_SPANISH.search(raw) or _SPANISH_CHARS.search(raw) or _SPANISH_WORDS.search(raw):
        return "es"
    return "en"


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


# FEMA's own reason categories, from its "Understanding Your FEMA Determination Letter"
# guidance (fema.gov/press-release/20260430/understanding-your-fema-determination-letter),
# plus the two other common reasons: damage and a shared household. Each one says, in
# plain words, what the reason means and what papers answer it. "name" is FEMA's own
# wording, so people can match it to their letter.
_REASONS = {
    "en": [
        {
            "id": "identity",
            "name": "Proof of identity",
            "means": "FEMA could not confirm that you are who you say you are. This often "
                     "happens when a name or Social Security number was typed differently "
                     "from FEMA's records. It is not an accusation.",
            "send": [
                "A copy of a driver's license, state ID, passport, or military ID.",
                "A copy of your Social Security card, or a tax form (W-2 or tax return) that "
                "shows your Social Security number.",
                "If your name changed, a marriage or divorce paper that shows both names.",
            ],
        },
        {
            "id": "occupancy",
            "name": "Proof of occupancy",
            "means": "FEMA could not confirm that you were living in the damaged home when the "
                     "disaster happened. Renters get this too: FEMA still needs to see that you "
                     "lived there.",
            "send": [
                "A utility bill (power, water, gas) with your name and that address.",
                "A lease, or a letter from your landlord or mobile home park owner.",
                "A bank statement, pay stub, driver's license, or car registration with "
                "that address.",
                "A letter from your employer, your child's school, a doctor's office, or a "
                "government office that shows that address.",
            ],
        },
        {
            "id": "ownership",
            "name": "Proof of ownership",
            "means": "FEMA could not confirm that you own the home, so it cannot pay to repair "
                     "it. This is common when a home was passed down in a family without a "
                     "deed in your name. FEMA now accepts more kinds of papers for that.",
            "send": [
                "A deed, title, mortgage statement, or property tax bill.",
                "Homeowner's insurance papers for the home.",
                "For a mobile home: the title, or the bill of sale.",
                "For a home you inherited: a will, a court paper, or receipts showing that you "
                "have paid the taxes or made major repairs. If you have none, ask FEMA whether "
                "you can sign a statement that explains how you came to own the home.",
            ],
        },
        {
            "id": "primary_residence",
            "name": "Primary residence",
            "means": "FEMA helps with the home you live in most of the year, not a second home, "
                     "vacation home, or rental you own. FEMA could not confirm that the damaged "
                     "home is your main home.",
            "send": [
                "The same papers as proof of occupancy, with that address.",
                "If you were away for a while (military service, a hospital stay, work), "
                "a short letter that explains why, and a paper that shows it.",
            ],
        },
        {
            "id": "insurance_coverage",
            "name": "Proof of insurance coverage",
            "means": "Your application says you have insurance, and FEMA needs to see what it "
                     "covers. FEMA cannot pay for anything your insurance pays for, so it waits "
                     "to see the policy.",
            "send": [
                "The page of your insurance policy that lists what is covered (often called "
                "the declarations page).",
                "If you have no insurance for this kind of damage (for example, flood), say so "
                "in writing.",
            ],
        },
        {
            "id": "insurance_settlement",
            "name": "Insurance settlement or denial",
            "means": "FEMA is waiting to hear what your insurance company decided. FEMA can only "
                     "help with what insurance did not pay.",
            "send": [
                "The letter from your insurance company that shows what it paid, or that it "
                "said no.",
                "If your insurance company is taking a long time, tell FEMA. FEMA may be able "
                "to help in the meantime, but you will have to pay FEMA back from the "
                "insurance money.",
            ],
        },
        {
            "id": "insufficient_damage",
            "name": "Insufficient damage",
            "means": "The inspector decided your home is still safe to live in. Inspectors can "
                     "miss damage that is hidden or that shows up later, like mold, a roof "
                     "leak, or wiring problems.",
            "send": [
                "Ask FEMA for a new inspection.",
                "Photos and videos of all the damage, including anything new since the "
                "inspection.",
                "Written repair estimates from a contractor.",
            ],
        },
        {
            "id": "shared_household",
            "name": "Shared household",
            "means": "Someone else at your address already applied, and FEMA counts you as one "
                     "household. If you live separately (for example, roommates who each pay "
                     "their own rent), you may be able to get your own help.",
            "send": [
                "A short letter that explains who lives in the home and how you live "
                "separately.",
                "Your own lease, rent receipts, or bills in your name.",
            ],
        },
        {
            "id": "amount",
            "name": "Amount or type of help",
            "means": "FEMA said yes, but you think the amount is too low or the wrong kind of "
                     "help. You can appeal this too.",
            "send": [
                "A letter that lists what FEMA did not cover.",
                "Repair estimates, receipts, and bills that show the real costs.",
            ],
        },
    ],
    "es": [
        {
            "id": "identity",
            "name": "Prueba de identidad",
            "means": "FEMA no pudo confirmar que usted es quien dice ser. Esto pasa muchas veces "
                     "cuando un nombre o un número de Seguro Social se escribió diferente a los "
                     "registros de FEMA. No es una acusación.",
            "send": [
                "Una copia de una licencia de conducir, identificación del estado, pasaporte "
                "o identificación militar.",
                "Una copia de su tarjeta de Seguro Social, o un formulario de impuestos (W-2 o "
                "declaración de impuestos) que muestre su número de Seguro Social.",
                "Si su nombre cambió, un papel de matrimonio o divorcio que muestre los dos "
                "nombres.",
            ],
        },
        {
            "id": "occupancy",
            "name": "Prueba de ocupación",
            "means": "FEMA no pudo confirmar que usted vivía en la casa dañada cuando ocurrió el "
                     "desastre. A los inquilinos también les pasa: FEMA necesita ver que usted "
                     "vivía allí.",
            "send": [
                "Una factura de luz, agua o gas con su nombre y esa dirección.",
                "Un contrato de alquiler, o una carta de su casero o del dueño del parque de "
                "casas móviles.",
                "Un estado de cuenta del banco, un talón de pago, una licencia de conducir o "
                "el registro de su carro con esa dirección.",
                "Una carta de su empleador, de la escuela de su hijo, de un consultorio médico "
                "o de una oficina del gobierno que muestre esa dirección.",
            ],
        },
        {
            "id": "ownership",
            "name": "Prueba de propiedad",
            "means": "FEMA no pudo confirmar que la casa es suya, así que no puede pagar para "
                     "repararla. Esto es común cuando una casa pasó de familia en familia sin una "
                     "escritura a su nombre. Ahora FEMA acepta más tipos de papeles para eso.",
            "send": [
                "Una escritura, título, estado de cuenta de la hipoteca o factura de impuestos "
                "de la propiedad.",
                "Los papeles del seguro de la casa.",
                "Para una casa móvil: el título o el comprobante de compra.",
                "Para una casa que heredó: un testamento, un papel de la corte, o recibos que "
                "muestren que usted ha pagado los impuestos o hecho reparaciones grandes. Si no "
                "tiene ninguno, pregúntele a FEMA si puede firmar una declaración que explique "
                "cómo llegó a ser dueño de la casa.",
            ],
        },
        {
            "id": "primary_residence",
            "name": "Residencia principal",
            "means": "FEMA ayuda con la casa donde usted vive la mayor parte del año, no con una "
                     "segunda casa, una casa de vacaciones o una casa que alquila a otros. FEMA no "
                     "pudo confirmar que la casa dañada es su casa principal.",
            "send": [
                "Los mismos papeles que para la prueba de ocupación, con esa dirección.",
                "Si estuvo fuera por un tiempo (servicio militar, hospital, trabajo), una carta "
                "corta que explique por qué, y un papel que lo muestre.",
            ],
        },
        {
            "id": "insurance_coverage",
            "name": "Prueba de cobertura del seguro",
            "means": "Su solicitud dice que usted tiene seguro, y FEMA necesita ver qué cubre. "
                     "FEMA no puede pagar nada que su seguro pague, así que espera a ver la póliza.",
            "send": [
                "La página de su póliza que dice qué está cubierto (muchas veces se llama la "
                "página de declaraciones).",
                "Si no tiene seguro para este tipo de daño (por ejemplo, inundación), dígalo "
                "por escrito.",
            ],
        },
        {
            "id": "insurance_settlement",
            "name": "Pago o rechazo del seguro",
            "means": "FEMA está esperando saber qué decidió su compañía de seguros. FEMA solo "
                     "puede ayudar con lo que el seguro no pagó.",
            "send": [
                "La carta de su compañía de seguros que muestra lo que pagó, o que le dijo "
                "que no.",
                "Si su compañía de seguros se está tardando mucho, dígaselo a FEMA. FEMA puede "
                "ayudarle mientras tanto, pero tendrá que devolverle el dinero a FEMA cuando "
                "le pague el seguro.",
            ],
        },
        {
            "id": "insufficient_damage",
            "name": "Daños insuficientes",
            "means": "El inspector decidió que su casa todavía es segura para vivir. Los "
                     "inspectores pueden no ver daños escondidos o que aparecen después, como "
                     "moho, goteras en el techo o problemas eléctricos.",
            "send": [
                "Pídale a FEMA una nueva inspección.",
                "Fotos y videos de todos los daños, incluso lo nuevo desde la inspección.",
                "Presupuestos de reparación por escrito de un contratista.",
            ],
        },
        {
            "id": "shared_household",
            "name": "Hogar compartido",
            "means": "Otra persona en su dirección ya solicitó, y FEMA los cuenta como un solo "
                     "hogar. Si ustedes viven por separado (por ejemplo, compañeros de casa que "
                     "pagan su propio alquiler), usted puede recibir su propia ayuda.",
            "send": [
                "Una carta corta que explique quién vive en la casa y cómo viven por separado.",
                "Su propio contrato de alquiler, recibos de renta o facturas a su nombre.",
            ],
        },
        {
            "id": "amount",
            "name": "Cantidad o tipo de ayuda",
            "means": "FEMA le dijo que sí, pero usted cree que la cantidad es muy baja o que no es "
                     "el tipo de ayuda correcto. También puede apelar esto.",
            "send": [
                "Una carta con la lista de lo que FEMA no cubrió.",
                "Presupuestos de reparación, recibos y facturas que muestren los costos reales.",
            ],
        },
    ],
}

_DENIAL = {
    "en": {
        "intro": "A \"no\" from FEMA is often not the end. If your letter says you are \"not "
                 "currently eligible\", that is not a final denial: FEMA is usually missing a "
                 "paper.",
        "steps": [
            "Read the letter closely. It says why FEMA said no. Find the reason in the list "
            "below.",
            f"Call {FEMA_HELPLINE} or log in at {APPLY_URL} and ask what is missing. Sometimes you "
            "only need to send one paper, and you do not need to appeal.",
            "To appeal, use the appeal form that comes with your FEMA letter, or write a letter "
            "that says why you think FEMA is wrong. Sign it. Put your "
            "full name, your FEMA application number, and the disaster number from your letter "
            "on every page.",
            "Add copies of papers that prove your case. Keep the originals.",
            "Send it within 60 days of the date on FEMA's letter. You can upload it at "
            f"{APPLY_URL}, mail it, fax it, or bring it to a Disaster Recovery Center.",
            "Keep a copy of everything you send, and write down the date you sent it.",
        ],
        "reasons_title": "Why FEMA said no, and what to send",
        "reasons": _REASONS["en"],
        "legal": "Free legal help may be available after a disaster. Call FEMA and ask about "
                 "Disaster Legal Services.",
    },
    "es": {
        "intro": "Un \"no\" de FEMA muchas veces no es el final. Si su carta dice que \"por "
                 "ahora no es elegible\", eso no es un rechazo final: muchas veces a FEMA le "
                 "falta un papel.",
        "steps": [
            "Lea la carta con cuidado. Dice por qué FEMA le dijo que no. Busque la razón en la "
            "lista de abajo.",
            f"Llame al {FEMA_HELPLINE} o entre a {APPLY_URL} y pregunte qué falta. A veces solo "
            "necesita enviar un papel, y no tiene que apelar.",
            "Para apelar, use el formulario de apelación que viene con su carta de FEMA, o "
            "escriba una carta que diga por qué cree que FEMA se equivocó. Fírmela. "
            "Ponga su nombre completo, su número de solicitud de FEMA y el número del desastre "
            "que aparece en su carta en cada página.",
            "Agregue copias de los papeles que prueban su caso. Guarde los originales.",
            "Envíela dentro de los 60 días desde la fecha de la carta de FEMA. Puede subirla en "
            f"{APPLY_URL}, enviarla por correo o por fax, o llevarla a un Centro de Recuperación "
            "por Desastre.",
            "Guarde una copia de todo lo que envíe, y anote la fecha en que lo envió.",
        ],
        "reasons_title": "Por qué FEMA le dijo que no, y qué enviar",
        "reasons": _REASONS["es"],
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
    lines += ["", h["reasons_title"]]
    for r in h["reasons"]:
        lines += ["", f"  {r['name']}", f"    {r['means']}"]
        lines += [f"    - {item}" for item in r["send"]]
    lines += ["", h["legal"]]
    return "\n".join(lines)


def denial_reason(reason_id: str, lang: str = "en") -> dict:
    """One FEMA reason by its id (for example "ownership"), for a letter reader to point at."""
    for r in _REASONS[check_lang(lang)]:
        if r["id"] == reason_id:
            return r
    raise KeyError(f"Unknown FEMA reason: {reason_id}")
