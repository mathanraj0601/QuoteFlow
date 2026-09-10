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


# --------------------------------------------------------------------------- #
# Provider interface
# --------------------------------------------------------------------------- #


class Provider(Protocol):
    name: str

    def classify_and_extract(self, email_data: dict) -> Extraction: ...

    def draft_supplier_outreach(
        self, enquiry: Extraction, candidate: dict, settings: Settings
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
