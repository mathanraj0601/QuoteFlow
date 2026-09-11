"""The two LLM steps of the pipeline: classify+extract, and draft supplier outreach.

Three interchangeable providers behind one interface:

  StubProvider     - deterministic regex/keyword logic. No network, no key.
                     Keeps the whole pipeline demoable and the tests fast.
  AnthropicProvider - real Claude (`claude-opus-5` by default). Used when a key
                     or `ant auth` profile resolves, or LLM_PROVIDER=anthropic.
  OllamaProvider   - a local model served by Ollama (LLM_PROVIDER=ollama).
                     Same prompts; OLLAMA_MODEL must be pulled first.

`get_provider()` picks one from settings (`auto` | `anthropic` | `ollama` | `stub`).

draft_supplier_outreach() returns the shape Plan B §5 specifies:
    {to, subject, body_template, fields: [{key,label,value,tag,confidence}], body}
where `body` == `body_template` with every `{{key}}` replaced by its field value,
so the stored `draft_body` is always the literal text a send would transmit.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from gmail_trigger_poc.config import Settings, get_settings

logger = logging.getLogger(__name__)

VALID_TAGS = {"AI-GENERATED", "FROM-DATABASE", "NEEDS-REVIEW", "MANUAL"}
SCENARIOS = {"client_enquiry", "supplier_response", "other"}


class LLMError(RuntimeError):
    """Raised when a real LLM call fails (network, auth, unparseable output)."""


@dataclass
class Extraction:
    scenario: str = "other"
    product_description: str = ""
    quantity: int | None = None
    unit_of_measure: str | None = None
    delivery_terms: str | None = None
    destination: str | None = None
    specifications: str | None = None
    client_name: str | None = None
    client_company: str | None = None
    urgency: str = "normal"
    confidence: float = 0.5

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Substitution helper — the single source of truth for {{token}} -> value
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def substitute(template: str, mapping: dict[str, str]) -> str:
    return _TOKEN_RE.sub(lambda m: str(mapping.get(m.group(1), m.group(0))), template)


def render_body(body_template: str, fields: list[dict]) -> str:
    """`body_template` with every field's {{key}} replaced by its current value."""
    return substitute(body_template, {f["key"]: f.get("value", "") for f in fields})


# Tokens the SYSTEM fills (never operator-editable fields). {{supplier_name}} and
# {{company}} are deliberately excluded: they stay live in a shared draft and are
# resolved per recipient at send. `client_name` / `client_company` are NOT here
# on purpose — the supplier email must never identify the end client (see
# _LEAK_KEY_RE below), so those tokens are never given a real value to expand to.
_SYSTEM_TOKENS = frozenset({
    "product_name", "broker_name", "broker_email",
})


# The house shared-outreach body. Used by the stub, and as the fallback when a
# real model returns an empty / unusable body_template. {{supplier_name}} is left
# live (filled per recipient at send); the rest are substituted before storage.
# Deliberately minimal: stock/availability, the product, and where it ships to —
# nothing about the client is asked for or mentioned.
_SHARED_BODY_TEMPLATE = (
    "Hi {{supplier_name}} team,\n\n"
    "We'd like to check availability and get your best quotation for the "
    "following.\n\n"
    "- Product: {{product_name}}\n"
    "- Quantity needed: {{quantity}}\n"
    "- Shipping location: {{shipping_location}}\n\n"
    "Please confirm whether you have this in stock, along with your pricing, "
    "MOQ and lead time.\n\n"
    "Best regards,\n{{broker_name}}\n{{broker_email}}"
)


def _canonical_shared_fields(enquiry: "Extraction", context: dict | None = None) -> list[dict]:
    """The fixed, deliberately short set of operator-editable fields for a shared
    outreach draft: just what a supplier needs to check stock and quote —
    quantity and shipping location. Nothing about the client (name, company, who
    they are) is ever a field or a token here; see _LEAK_KEY_RE for the backstop
    that scrubs it even if a model tries to slip it into the free-text body. A
    value we can't fill is left EMPTY and tagged NEEDS-REVIEW so the UI prompts
    the operator to type it in, rather than showing a raw {{token}}.
    """
    ctx = context or {}
    uom = enquiry.unit_of_measure or ctx.get("unit_of_measure") or "unit"
    qty = f"{enquiry.quantity:,} {uom}" if enquiry.quantity else ""
    shipping_location = enquiry.destination or ctx.get("destination") or ""

    def mk(key: str, label: str, value: str) -> dict:
        return {
            "key": key, "label": label, "value": value,
            "tag": "AI-GENERATED" if value else "NEEDS-REVIEW",
            "confidence": 0.7 if value else None,
        }

    return [
        mk("quantity", "Quantity needed", qty),
        mk("shipping_location", "Shipping location", shipping_location),
    ]


# --------------------------------------------------------------------------- #
# Provider interface
# --------------------------------------------------------------------------- #


class Provider(Protocol):
    name: str

    def classify_and_extract(self, email_data: dict) -> Extraction: ...

    def draft_supplier_outreach(
        self, enquiry: Extraction, candidate: dict, settings: Settings
    ) -> dict: ...

    def draft_shared_outreach(
        self, enquiry: Extraction, context: dict, settings: Settings
    ) -> dict: ...


# --------------------------------------------------------------------------- #
# Stub provider
# --------------------------------------------------------------------------- #

_ENQUIRY_CUES = (
    "quotation", "quote", "pricing", "price", "enquiry", "inquiry", "rfq",
    "looking for", "sourcing", "interested in", "purchase", "we need", "we are sourcing",
    "requirement", "request a quote", "can you supply", "supply of",
)
_AUTO_REPLY_CUES = (
    "out of office", "automatic reply", "auto-reply", "autoreply", "undeliverable",
    "delivery status notification", "unsubscribe", "do not reply", "no-reply",
    "read receipt", "vacation",
)
_SUPPLIER_REPLY_CUES = ("re: quote request", "our quotation", "quotation details", "moq")
_INCOTERM_RE = re.compile(
    r"\b(FOB|CIF|CFR|EXW|DAP|DDP|FCA|FAS|CIP|CPT)\b[ ]+([A-Z][A-Za-z]+(?:[ ][A-Z][A-Za-z]+)?)"
)
_UNITS = (
    "pieces", "piece", "pcs", "units", "unit", "kg", "kgs", "kilograms", "tonnes", "tons",
    "metres", "meters", "meter", "m", "litres", "liters", "liter", "l", "boxes", "box",
    "kits", "kit", "rolls", "roll", "sets", "set", "pairs",
)
_QTY_RE = re.compile(
    r"(?:quantity|qty|need(?:ed)?|looking for|about|approximately|approx|around|of)?\s*"
    r"([0-9][0-9,\.]{0,12})\s*"
    r"(pieces|piece|pcs|units|unit|kgs|kg|kilograms|tonnes|tons|metres|meters|meter|litres|"
    r"liters|liter|boxes|box|kits|kit|rolls|roll|sets|set|pairs|m|l)\b",
    re.IGNORECASE,
)
_URGENCY_HIGH = ("urgent", "asap", "as soon as possible", "quickly", "prompt", "rush",
                   "time-sensitive", "time sensitive", "expedite")
_URGENCY_LOW = ("no rush", "no hurry", "not urgent", "exploring", "whenever", "no immediate")


def _first(*vals: str | None) -> str | None:
    for v in vals:
        if v:
            return v.strip()
    return None


def _clean_subject(subject: str) -> str:
    s = subject or ""
    s = re.sub(r"^\s*(re|fwd|fw)\s*:\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*(enquiry|inquiry|quote request|quotation|rfq)\s*[:\-]\s*", "", s,
               flags=re.IGNORECASE)
    s = re.sub(r"[—\-]\s*[\d,]+\s*\w+\s*(needed|required)?\s*$", "", s, flags=re.IGNORECASE)
    return s.strip()


def _parse_qty(text: str) -> tuple[int | None, str | None]:
    best: tuple[int, str] | None = None
    for m in _QTY_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            val = round(float(raw))
        except ValueError:
            continue
        if val <= 0:
            continue
        if best is None or val > best[0]:
            best = (val, m.group(2).lower())
    if best is None:
        return None, None
    uom = {
        "pcs": "piece", "pieces": "piece", "unit": "piece", "units": "piece",
        "kgs": "kg", "kilograms": "kg", "tons": "tonnes",
        "metres": "meter", "meters": "meter", "m": "meter",
        "litres": "liter", "liters": "liter", "l": "liter",
        "box": "box", "boxes": "box", "kits": "kit", "rolls": "roll", "sets": "set",
    }.get(best[1], best[1])
    return best[0], uom


class StubProvider:
    name = "stub"

    def classify_and_extract(self, email_data: dict) -> Extraction:
        subject = (email_data.get("subject") or "").strip()
        body = (email_data.get("body") or "").strip()
        blob = f"{subject}\n{body}".lower()

        if any(c in blob for c in _AUTO_REPLY_CUES):
            return Extraction(scenario="other", confidence=0.8,
                              product_description=_clean_subject(subject))
        if any(c in blob for c in _SUPPLIER_REPLY_CUES) and blob.startswith("re:"):
            return Extraction(scenario="supplier_response", confidence=0.6)
        if not any(c in blob for c in _ENQUIRY_CUES):
            return Extraction(scenario="other", confidence=0.55,
                              product_description=_clean_subject(subject))

        qty, uom = _parse_qty(blob)
        incoterm = _INCOTERM_RE.search(f"{subject}\n{body}")
        delivery_terms = incoterm.group(0) if incoterm else None

        m_specs = re.search(r"(?:^|\n)\s*-?\s*(?:specifications?|specs?)\s*[:\-]\s*(.+)", body,
                            re.IGNORECASE)
        m_dest = re.search(
            r"(?:delivery location|destination|deliver(?:ed)? to|shipped to|landed in)\s*[:\-]?\s*"
            r"([A-Za-z .,'-]{3,60})", body, re.IGNORECASE)
        m_prod = re.search(r"(?:^|\n)\s*-?\s*product\s*[:\-]\s*(.+)", body, re.IGNORECASE)
        m_company = re.search(
            r"(?:represent|on behalf of|from|for)\s+([A-Z][\w&.,' -]{2,60}?(?:Ltd|LLC|Inc|"
            r"GmbH|Co\.?|Corp|Group|B\.V\.|AB|Pty|Company)\.?)", f"{body}")
        m_name = re.search(r"(?:my name is|i am|i'm)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})", body)

        product_description = _first(
            m_prod.group(1) if m_prod else None,
            _clean_subject(subject),
            body[:120],
        ) or ""

        if any(c in blob for c in _URGENCY_HIGH):
            urgency = "high"
        elif any(c in blob for c in _URGENCY_LOW):
            urgency = "low"
        else:
            urgency = "normal"

        from_hdr = email_data.get("from") or ""
        m_from_name = re.match(r"\s*\"?([^\"<]+?)\"?\s*<", from_hdr)

        return Extraction(
            scenario="client_enquiry",
            product_description=product_description.strip()[:200],
            quantity=qty,
            unit_of_measure=uom,
            delivery_terms=delivery_terms,
            destination=(m_dest.group(1).strip().rstrip(".") if m_dest else None),
            specifications=(m_specs.group(1).strip() if m_specs else None),
            client_name=_first(m_name.group(1) if m_name else None,
                               m_from_name.group(1) if m_from_name else None),
            client_company=(m_company.group(1).strip() if m_company else None),
            urgency=urgency,
            confidence=0.6,
        )

    def draft_supplier_outreach(
        self, enquiry: Extraction, candidate: dict, settings: Settings
    ) -> dict:
        uom = enquiry.unit_of_measure or candidate.get("unit_of_measure") or "unit"
        qty_val = (
            f"{enquiry.quantity:,} {uom}" if enquiry.quantity else "(quantity to confirm)"
        )
        price = candidate.get("unit_price")
        price_val = f"${price:,.4f} / {uom}" if price is not None else "(no catalogue price on file)"
        specs_val = _first(
            enquiry.specifications, candidate.get("product_specifications")
        ) or "(as per client enquiry)"
        delivery_val = " / ".join(
            p for p in (enquiry.delivery_terms, enquiry.destination) if p
        ) or "(delivery terms to confirm)"
        lead = candidate.get("lead_time_days")
        lead_val = f"{lead} business days (indicative)" if lead else "(please advise)"

        fields = [
            {"key": "quantity", "label": "Quantity requested", "value": qty_val,
             "tag": "AI-GENERATED" if enquiry.quantity else "NEEDS-REVIEW",
             "confidence": 0.9 if enquiry.quantity else 0.3},
            {"key": "target_unit_price", "label": "Reference unit price", "value": price_val,
             "tag": "FROM-DATABASE" if price is not None else "NEEDS-REVIEW",
             "confidence": None if price is not None else 0.3},
            {"key": "specifications", "label": "Specifications", "value": specs_val,
             "tag": "AI-GENERATED" if enquiry.specifications else "FROM-DATABASE",
             "confidence": 0.7 if enquiry.specifications else None},
            {"key": "delivery", "label": "Requested delivery", "value": delivery_val,
             "tag": "AI-GENERATED" if (enquiry.delivery_terms or enquiry.destination)
             else "NEEDS-REVIEW",
             "confidence": 0.7 if (enquiry.delivery_terms or enquiry.destination) else 0.3},
            {"key": "lead_time", "label": "Indicative lead time", "value": lead_val,
             "tag": "FROM-DATABASE" if lead else "NEEDS-REVIEW",
             "confidence": None if lead else 0.3},
        ]

        product_name = candidate.get("product_name") or enquiry.product_description
        fixed = {
            "supplier_name": candidate.get("supplier_name") or "team",
            "product_name": product_name,
            "broker_name": settings.broker_name,
            "broker_email": settings.broker_email,
        }
        body_template = substitute(
            "Hi {{supplier_name}} team,\n\n"
            "We have a client enquiry that matches your catalogue and would like your "
            "best quotation.\n\n"
            "- Product: {{product_name}}\n"
            "- Specifications: {{specifications}}\n"
            "- Quantity requested: {{quantity}}\n"
            "- Reference unit price: {{target_unit_price}}\n"
            "- Requested delivery: {{delivery}}\n"
            "- Indicative lead time: {{lead_time}}\n\n"
            "Please confirm your current unit pricing, MOQ, shipping cost, lead time and "
            "any applicable certifications for this requirement.\n\n"
            "Best regards,\n{{broker_name}}\n{{broker_email}}",
            fixed,
        )
        qty_subject = f" - Qty {enquiry.quantity:,}" if enquiry.quantity else ""
        return {
            "to": candidate.get("supplier_email") or "",
            "subject": f"RFQ - {product_name}{qty_subject}",
            "body_template": body_template,
            "fields": fields,
            "body": render_body(body_template, fields),
        }

    def draft_shared_outreach(
        self, enquiry: Extraction, context: dict, settings: Settings
    ) -> dict:
        """One template for ALL matched suppliers: {{supplier_name}} stays a live
        token (filled per recipient at send), and there is NO price line/field."""
        fields = _canonical_shared_fields(enquiry, context)
        product_name = context.get("product_name") or enquiry.product_description
        body_template = substitute(_SHARED_BODY_TEMPLATE, {
            "product_name": product_name,
            "broker_name": settings.broker_name,
            "broker_email": settings.broker_email,
        })
        qty_subject = f" - Qty {enquiry.quantity:,}" if enquiry.quantity else ""
        return {
            "to": "{{supplier_name}}",
            "subject": f"RFQ - {product_name}{qty_subject}",
            "body_template": body_template,
            "fields": fields,
            "body": render_body(body_template, fields),
        }


# --------------------------------------------------------------------------- #
# Anthropic provider
# --------------------------------------------------------------------------- #

_EXTRACT_SYSTEM = """You classify and extract fields from a single inbound email at a \
procurement broker. Reply with ONE JSON object and nothing else.

Schema:
  scenario: "client_enquiry" | "supplier_response" | "other"
  product_description: string   (what the client wants, for catalogue search; "" if n/a)
  quantity: integer | null
  unit_of_measure: string | null
  delivery_terms: string | null (e.g. "FOB Shanghai")
  destination: string | null
  specifications: string | null
  client_name: string | null
  client_company: string | null
  urgency: "low" | "normal" | "high"
  confidence: number 0..1

"client_enquiry" = a prospective buyer asking us to source/price a product.
Auto-replies, newsletters, internal mail, supplier price replies are NOT client_enquiry."""

_DRAFT_SYSTEM = """You draft a quote-request email from a procurement broker to ONE supplier, \
based on a client enquiry and that supplier's catalogue row. Reply with ONE JSON object only:

  subject: string
  body_template: string  - the email body. Use {{key}} tokens for every value that the
                           operator may edit; every token MUST have a matching entry in
                           "fields". You may also use {{supplier_name}}, {{product_name}},
                           {{broker_name}}, {{broker_email}} which are filled by the system.
  fields: array of { key, label, value, tag, confidence }
          tag in "AI-GENERATED" | "FROM-DATABASE" | "NEEDS-REVIEW"
          confidence: number 0..1 or null
          Use FROM-DATABASE for values taken from the supplier catalogue row,
          AI-GENERATED for values inferred from the enquiry,
          NEEDS-REVIEW when a value is missing or uncertain.

Do not invent prices. Ask the supplier to confirm pricing, MOQ, lead time, certifications."""

_DRAFT_SHARED_SYSTEM = """You draft ONE stock-check / quote-request email from a procurement broker, \
to be sent to SEVERAL matched suppliers at once. Reply with ONE JSON object only, nothing before \
or after it:

  subject: string
  body_template: string  - the email body, plain text with {{tokens}}.

ALLOWED TOKENS - use ONLY these, exactly as written. Every one is substituted automatically, so \
never expand them yourself and never invent, rename, or add any other {{token}}:
  {{supplier_name}}   - the recipient; keep the greeting generic, never name one supplier
  {{product_name}}    - system value, the product being sourced
  {{broker_name}}     - system value, your sign-off name
  {{broker_email}}    - system value, your sign-off email
  {{quantity}}        - how much is needed (the operator can edit this)
  {{shipping_location}} - where it needs to ship to (the operator can edit this)

WHAT THE EMAIL MAY ASK FOR - nothing more than: (1) whether the supplier has the product in \
stock / can supply the quantity, (2) their pricing, MOQ, lead time and certifications.

HARD RULE - NEVER MENTION THE CLIENT. This is a broker relationship: the supplier must never \
learn who the end client is, or anything about them. Do NOT write the client's name, company, \
industry, location, or the word "client enquiry" tied to any identifying detail. It is fine to \
say generic things like "we are sourcing" or "we'd like to check availability" - just never say \
whose order this is for. Do NOT output a {{client_name}}, {{client_company}} or similar token.

Do NOT output a "fields" array. Do NOT put any reference / target / catalogue unit price - a \
line, a token or a field - anywhere: pricing is exactly what we are asking the suppliers for.

EXAMPLE of a correct body_template (yours should follow this shape, adjusted to the product):
"Hi {{supplier_name}} team,\\n\\nWe'd like to check availability and get your best quotation for \
the following.\\n\\n- Product: {{product_name}}\\n- Quantity needed: {{quantity}}\\n- Shipping \
location: {{shipping_location}}\\n\\nPlease confirm whether you have this in stock, along with \
your pricing, MOQ and lead time.\\n\\nBest regards,\\n{{broker_name}}\\n{{broker_email}}\""""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


# --------------------------------------------------------------------------- #
# Prompt / response logging (LLM_LOG_IO) — one place, both real providers
# --------------------------------------------------------------------------- #

_IO_CAP = 8000  # chars of prompt / response logged verbatim before truncation


def _cap(text: str | None) -> str:
    text = text or ""
    if len(text) <= _IO_CAP:
        return text
    return text[:_IO_CAP] + f"\n... (+{len(text) - _IO_CAP} more chars)"


def _emit_block(gutter: str, text: str) -> None:
    """Log `text` one line per record so EVERY line carries the [LLM] tag."""
    for line in (_cap(text).splitlines() or [""]):
        logger.info("%s%s", gutter, line)


def _log_llm_request(kind: str, model: str, system: str, user: str, *, enabled: bool) -> None:
    if not enabled:
        return
    logger.info("+---- LLM REQUEST  ->  %s  |  %s", model, kind)
    logger.info("| SYSTEM PROMPT (%d chars)", len(system or ""))
    _emit_block("| ", system)
    logger.info("| USER PROMPT (%d chars)", len(user or ""))
    _emit_block("| ", user)
    logger.info("+---- sent; awaiting model ...")


def _log_llm_response(kind: str, text: str, elapsed: float, *, enabled: bool) -> None:
    if not enabled:
        return
    logger.info("+---- LLM RESPONSE  <-  %s  |  %.1fs  |  %d chars", kind, elapsed, len(text or ""))
    _emit_block("| ", text)
    logger.info("+---- end response")


# --------------------------------------------------------------------------- #
# JSON -> domain mapping, shared by every real (non-stub) provider
# --------------------------------------------------------------------------- #


def _json_to_extraction(data: dict) -> Extraction:
    scenario = data.get("scenario")
    if scenario not in SCENARIOS:
        scenario = "other"
    qty = data.get("quantity")
    try:
        qty = int(qty) if qty is not None else None
    except (TypeError, ValueError):
        qty = None
    urgency = data.get("urgency")
    return Extraction(
        scenario=scenario,
        product_description=str(data.get("product_description") or "")[:300],
        quantity=qty,
        unit_of_measure=data.get("unit_of_measure"),
        delivery_terms=data.get("delivery_terms"),
        destination=data.get("destination"),
        specifications=data.get("specifications"),
        client_name=data.get("client_name"),
        client_company=data.get("client_company"),
        urgency=urgency if urgency in {"low", "normal", "high"} else "normal",
        confidence=float(data.get("confidence", 0.7) or 0.7),
    )


def _json_to_draft(
    data: dict, enquiry: Extraction, candidate: dict, settings: Settings
) -> dict:
    fields = _sanitize_fields(data.get("fields"))
    fixed = {
        "supplier_name": candidate.get("supplier_name") or "team",
        "product_name": candidate.get("product_name") or enquiry.product_description,
        "client_company": enquiry.client_company or "our client",
        "client_name": enquiry.client_name or "our client",
        "broker_name": settings.broker_name,
        "broker_email": settings.broker_email,
    }
    body_template = substitute(str(data.get("body_template") or ""), fixed)
    # Models sometimes drop {{token}}s into the subject too; render it the same
    # way as the body so a stored subject is never a raw template.
    subject = str(data.get("subject") or f"RFQ - {fixed['product_name']}")
    subject = render_body(substitute(subject, fixed), fields)
    return {
        "to": candidate.get("supplier_email") or "",
        "subject": subject,
        "body_template": body_template,
        "fields": fields,
        "body": render_body(body_template, fields),
    }


# Anything price- or client-ish: we never quote a reference price to the
# suppliers we're asking to price the job, and we never let the client's
# identity reach a supplier — this is the backstop for both, independent of
# the system prompt. Catches unit_price, target_price, price_ref, "Ref price",
# client_name, client_company, and any model-invented variant containing
# either word (e.g. "end_client", "client_ref").
_LEAK_KEY_RE = re.compile(r"price|client", re.I)


def _drop_leak_lines(template: str) -> str:
    """Remove every WHOLE LINE that carries a price-/client-ish {{token}},
    rather than just blanking the token in place. A tidy local model puts one
    token per "- Label: {{token}}" bullet, where blanking would do; a weak one
    can bury {{client_company}} mid-sentence ("This quote is for {{client_name}}
    at ...") where blanking leaves a grammatically-broken but still-legible
    half-sentence. Dropping the line is the only way that's safe either way —
    and costs nothing, since every field this could remove (quantity, etc.) is
    still shown to the operator in the structured fields list regardless of
    whether it's also named inline in the body text.
    """
    return "\n".join(
        line for line in template.splitlines()
        if not any(_LEAK_KEY_RE.search(t) for t in _TOKEN_RE.findall(line))
    )


def _json_to_shared_draft(data: dict, enquiry: Extraction, settings: Settings) -> dict:
    """Map the shared-draft JSON to the stored shape.

    The model's `fields` list is NOT trusted (small local models echo the
    template tokens back as values, or expose system tokens like
    {{broker_name}} as editable rows). We build the canonical field set from the
    extraction ourselves; a value we can't fill is left empty + NEEDS-REVIEW so
    the UI prompts the operator. {{supplier_name}} / {{company}} stay live
    (filled per recipient at send); every other {{token}} is either substituted
    from the extraction here or surfaced as a NEEDS-REVIEW field — a raw
    {{token}} must never reach an input box. client_name / client_company are
    deliberately NOT in `fixed`: even if a model outputs {{client_name}} despite
    the system prompt forbidding it, _drop_leak_lines()/_LEAK_KEY_RE scrub it
    below rather than ever filling it with the real client's identity.
    """
    fields = _canonical_shared_fields(enquiry)

    fixed = {
        "product_name": enquiry.product_description or "the requirement",
        "broker_name": settings.broker_name,
        "broker_email": settings.broker_email,
    }
    if enquiry.destination:
        fixed["destination"] = enquiry.destination
    if enquiry.urgency and enquiry.urgency != "normal":
        fixed["urgency"] = enquiry.urgency

    raw_template = str(data.get("body_template") or "").strip() or _SHARED_BODY_TEMPLATE
    raw_subject = str(data.get("subject") or f"RFQ - {fixed['product_name']}")

    # Blank a leak token wherever it shows up (subject included) before anything
    # else happens, so it can never be filled with a real value.
    leak_tokens = {
        t: "" for t in _TOKEN_RE.findall(raw_template) + _TOKEN_RE.findall(raw_subject)
        if _LEAK_KEY_RE.search(t)
    }

    body_template = substitute(_drop_leak_lines(raw_template), fixed)

    subject = substitute(raw_subject, {**fixed, **leak_tokens, "supplier_name": "", "company": ""})
    subject = re.sub(r"[ \t]{2,}", " ", subject).strip(" -")  # tidy up a now-dangling "for"/"at"
    subject = render_body(subject, fields)

    # Any {{token}} still in the body that isn't a per-recipient placeholder and
    # isn't one of our fields -> expose it as a field to fill, never a bare token.
    # (leak tokens can't reach here — their whole line was already dropped above;
    # the _LEAK_KEY_RE check stays as a defensive no-op in case a corner case slips by.)
    known = {f["key"] for f in fields} | {"supplier_name", "company"}
    for tok in dict.fromkeys(_TOKEN_RE.findall(body_template)):
        if tok in known or _LEAK_KEY_RE.search(tok):
            continue
        fields.append({
            "key": tok,
            "label": tok.replace("_", " ").strip().title() or tok,
            "value": "",
            "tag": "NEEDS-REVIEW",
            "confidence": None,
        })

    return {
        "to": "{{supplier_name}}",
        "subject": subject,
        "body_template": body_template,
        "fields": fields,
        "body": render_body(body_template, fields),
    }


def _shared_draft_user_prompt(enquiry: Extraction, context: dict, settings: Settings) -> str:
    # client_name / client_company are deliberately withheld from the prompt
    # itself, not just forbidden by instruction — the model can't leak what it
    # was never shown.
    sourcing_request = {
        k: v for k, v in enquiry.as_dict().items()
        if k not in ("client_name", "client_company")
    }
    return (
        "SOURCING REQUEST (client identity withheld on purpose - do not ask about "
        "or refer to who this is for):\n"
        f"{json.dumps(sourcing_request, indent=2)}\n\n"
        "REPRESENTATIVE CATALOGUE PRODUCT (context only - the email goes to several suppliers):\n"
        f"{json.dumps(context, indent=2, default=str)}\n\n"
        f"Broker: {settings.broker_name} <{settings.broker_email}>"
    )


def _classify_user_prompt(email_data: dict) -> str:
    return (
        f"From: {email_data.get('from')}\n"
        f"Subject: {email_data.get('subject')}\n\n"
        f"{email_data.get('body') or ''}"
    )


def _draft_user_prompt(enquiry: Extraction, candidate: dict, settings: Settings) -> str:
    row = {
        k: candidate.get(k)
        for k in (
            "supplier_name", "supplier_email", "product_name", "product_specifications",
            "unit_price", "moq", "lead_time_days", "supplier_certifications",
            "unit_of_measure",
        )
    }
    return (
        "CLIENT ENQUIRY (extracted):\n"
        f"{json.dumps(enquiry.as_dict(), indent=2)}\n\n"
        "SUPPLIER CATALOGUE ROW:\n"
        f"{json.dumps(row, indent=2, default=str)}\n\n"
        f"Broker: {settings.broker_name} <{settings.broker_email}>"
    )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover - dependency is declared
            raise LLMError("anthropic SDK not installed") from e
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self._model = settings.llm_model
        self._max_tokens = settings.llm_max_tokens
        self._log_io = settings.llm_log_io

    def _call(self, system: str, user: str, *, kind: str = "llm") -> dict:
        # classify/extract/draft are short structured tasks -> effort "low".
        # Adaptive thinking stays on (default on Opus 5) and its tokens count
        # against max_tokens, so keep max_tokens roomy (LLM_MAX_TOKENS).
        _log_llm_request(kind, self._model, system, user, enabled=self._log_io)
        started = time.perf_counter()
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                output_config={"effort": "low"},
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except TypeError:
            # older SDK without output_config support
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except self._anthropic.APIError as e:
            raise LLMError(f"Claude API call failed: {e}") from e

        stop = getattr(resp, "stop_reason", None)
        if stop == "refusal":
            detail = getattr(getattr(resp, "stop_details", None), "category", None)
            raise LLMError(f"Claude refused the request (category={detail})")
        if stop == "max_tokens":
            raise LLMError("Claude response hit max_tokens — raise LLM_MAX_TOKENS")

        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        _log_llm_response(kind, text, time.perf_counter() - started, enabled=self._log_io)
        try:
            return _extract_json(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"Claude returned unparseable JSON: {text[:200]!r}") from e

    def classify_and_extract(self, email_data: dict) -> Extraction:
        return _json_to_extraction(
            self._call(_EXTRACT_SYSTEM, _classify_user_prompt(email_data), kind="classify+extract")
        )

    def draft_supplier_outreach(
        self, enquiry: Extraction, candidate: dict, settings: Settings
    ) -> dict:
        data = self._call(
            _DRAFT_SYSTEM, _draft_user_prompt(enquiry, candidate, settings), kind="draft outreach"
        )
        return _json_to_draft(data, enquiry, candidate, settings)

    def draft_shared_outreach(
        self, enquiry: Extraction, context: dict, settings: Settings
    ) -> dict:
        data = self._call(
            _DRAFT_SHARED_SYSTEM, _shared_draft_user_prompt(enquiry, context, settings),
            kind="draft shared outreach",
        )
        return _json_to_shared_draft(data, enquiry, settings)


def _sanitize_fields(raw: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        tag = item.get("tag") if item.get("tag") in VALID_TAGS else "AI-GENERATED"
        conf = item.get("confidence")
        try:
            conf = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf = None
        out.append({
            "key": str(item["key"]),
            "label": str(item.get("label") or item["key"]),
            "value": "" if item.get("value") is None else str(item["value"]),
            "tag": tag,
            "confidence": conf,
        })
    return out


# --------------------------------------------------------------------------- #
# Ollama provider (local models via the Ollama HTTP server)
# --------------------------------------------------------------------------- #


class OllamaProvider:
    """Local LLM via an Ollama server. Same prompts as AnthropicProvider.

    Uses Ollama's native /api/chat with `format: "json"` so the model is
    constrained to emit a single JSON object. The model named by OLLAMA_MODEL
    must already be pulled (`ollama pull <model>`).
    """

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        try:
            import httpx
        except ImportError as e:  # pragma: no cover - httpx ships with fastapi
            raise LLMError("httpx not installed") from e
        self._httpx = httpx
        self._host = settings.ollama_host
        self._model = settings.ollama_model
        self._timeout = settings.ollama_timeout
        self._log_io = settings.llm_log_io

    def _call(self, system: str, user: str, *, kind: str = "llm") -> dict:
        url = f"{self._host}/api/chat"
        payload = {
            "model": self._model,
            "stream": False,
            "format": "json",
            # num_predict caps generation: small models sometimes run away emitting
            # whitespace under format:"json" until they hit the wall (looks like a hang).
            "options": {"temperature": 0, "num_predict": 1536},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        _log_llm_request(kind, self._model, system, user, enabled=self._log_io)
        started = time.perf_counter()
        try:
            resp = self._httpx.post(url, json=payload, timeout=self._timeout)
        except self._httpx.HTTPError as e:
            raise LLMError(
                f"Ollama request to {url} failed: {e} "
                f"(is `ollama serve` running and is OLLAMA_HOST correct?)"
            ) from e
        if resp.status_code == 404:
            raise LLMError(
                f"Ollama has no model '{self._model}' - run `ollama pull {self._model}`"
            )
        if resp.status_code >= 400:
            raise LLMError(f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            content = resp.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as e:
            raise LLMError(f"Ollama response was not in the expected shape: {resp.text[:200]!r}") from e
        _log_llm_response(kind, content, time.perf_counter() - started, enabled=self._log_io)
        try:
            return _extract_json(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"Ollama returned unparseable JSON: {content[:200]!r}") from e

    def classify_and_extract(self, email_data: dict) -> Extraction:
        return _json_to_extraction(
            self._call(_EXTRACT_SYSTEM, _classify_user_prompt(email_data), kind="classify+extract")
        )

    def draft_supplier_outreach(
        self, enquiry: Extraction, candidate: dict, settings: Settings
    ) -> dict:
        data = self._call(
            _DRAFT_SYSTEM, _draft_user_prompt(enquiry, candidate, settings), kind="draft outreach"
        )
        return _json_to_draft(data, enquiry, candidate, settings)

    def draft_shared_outreach(
        self, enquiry: Extraction, context: dict, settings: Settings
    ) -> dict:
        data = self._call(
            _DRAFT_SHARED_SYSTEM, _shared_draft_user_prompt(enquiry, context, settings),
            kind="draft shared outreach",
        )
        return _json_to_shared_draft(data, enquiry, settings)


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def _anthropic_credentials_available() -> bool:
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return True
    cfg = Path.home() / ".config" / "anthropic"
    return cfg.is_dir() and any(cfg.iterdir())


def get_provider(settings: Settings | None = None) -> Provider:
    settings = settings or get_settings()
    choice = settings.llm_provider or "auto"

    if choice == "stub":
        logger.info("LLM provider: stub (deterministic, no API calls)")
        return StubProvider()

    if choice == "anthropic":
        provider = AnthropicProvider(settings)
        logger.info("LLM provider: anthropic (model=%s)", settings.llm_model)
        return provider

    if choice == "ollama":
        provider = OllamaProvider(settings)
        logger.info(
            "LLM provider: ollama (model=%s, host=%s)",
            settings.ollama_model, settings.ollama_host,
        )
        return provider

    # auto
    try:
        import anthropic  # noqa: F401
    except ImportError:
        logger.info("LLM provider: stub (anthropic SDK not importable)")
        return StubProvider()
    if not _anthropic_credentials_available():
        logger.info("LLM provider: stub (no ANTHROPIC_API_KEY / ant auth profile found)")
        return StubProvider()
    try:
        provider = AnthropicProvider(settings)
    except LLMError:
        logger.warning("LLM provider: stub (AnthropicProvider init failed)", exc_info=True)
        return StubProvider()
    logger.info("LLM provider: anthropic (model=%s, auto-selected)", settings.llm_model)
    return provider
