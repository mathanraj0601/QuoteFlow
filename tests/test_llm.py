import dataclasses
import json
import re

import httpx
import pytest

from gmail_trigger_poc.config import get_settings
from gmail_trigger_poc.llm import (
    VALID_TAGS,
    Extraction,
    LLMError,
    OllamaProvider,
    StubProvider,
    get_provider,
    render_body,
    substitute,
)

FORMAL_ENQUIRY = {
    "from": '"Lisa Bergstrom" <lisa.b@nordicchemsolutions.se>',
    "subject": "Enquiry: Industrial Grade Citric Acid - 8,000 kg needed",
    "body": (
        "Dear Team,\n\nMy name is Lisa Bergstrom and I represent Nordic Chem Solutions AB. "
        "We are currently sourcing Industrial Grade Citric Acid and would like to request a "
        "quotation.\n\n"
        "- Product: Industrial Grade Citric Acid\n"
        "- Quantity required: 8,000 kg(s)\n"
        "- Preferred delivery terms: CIF Stockholm\n"
        "- Specifications: 99.5% purity, USP/food grade\n"
        "- Delivery location: Stockholm, Sweden\n\n"
        "Please advise on unit pricing and lead time. We need this quickly.\n\nBest regards,\nLisa"
    ),
}


def test_substitute_and_render_body():
    assert substitute("a {{x}} b {{y}}", {"x": "1", "y": "2"}) == "a 1 b 2"
    assert substitute("keep {{missing}}", {}) == "keep {{missing}}"
    fields = [{"key": "q", "value": "50 kg"}, {"key": "p", "value": "$1"}]
    assert render_body("Qty {{q}} at {{p}}", fields) == "Qty 50 kg at $1"


def test_stub_classifies_client_enquiry_and_extracts_fields():
    ex = StubProvider().classify_and_extract(FORMAL_ENQUIRY)
    assert ex.scenario == "client_enquiry"
    assert ex.quantity == 8000
    assert ex.unit_of_measure == "kg"
    assert ex.delivery_terms == "CIF Stockholm"
    assert ex.urgency == "high"
    assert "Citric Acid" in ex.product_description


@pytest.mark.parametrize(
    "subject,body",
    [
        ("Automatic reply: Out of office", "I am away until Monday."),
        ("Team lunch Friday", "Anyone up for lunch at the usual place?"),
        ("Undeliverable: your message", "Delivery has failed to these recipients."),
    ],
)
def test_stub_rejects_non_enquiries(subject, body):
    ex = StubProvider().classify_and_extract({"from": "x@y.com", "subject": subject, "body": body})
    assert ex.scenario != "client_enquiry"


def test_stub_draft_shape_and_body_consistency():
    settings = get_settings()
    enquiry = Extraction(
        scenario="client_enquiry", product_description="Stainless Steel Bolts M10x50",
        quantity=5000, unit_of_measure="piece", delivery_terms="FOB Shanghai",
        destination="Ohio, USA", specifications="Grade A2-70, DIN 933",
    )
    candidate = {
        "supplier_name": "Jiangsu Metal Works Co.", "supplier_email": "wang.li@jmw.com",
        "product_name": "Stainless Steel Bolts M10x50",
        "product_specifications": "Grade A2-70", "unit_price": 0.1166, "moq": 2000,
        "lead_time_days": 17, "unit_of_measure": "piece",
    }
    draft = StubProvider().draft_supplier_outreach(enquiry, candidate, settings)

    assert set(draft) == {"to", "subject", "body_template", "fields", "body"}
    assert draft["to"] == "wang.li@jmw.com"
    assert draft["body"] == render_body(draft["body_template"], draft["fields"])

    # every leftover {{token}} in the template is backed by a field
    field_keys = {f["key"] for f in draft["fields"]}
    leftover = set(re.findall(r"\{\{(\w+)\}\}", draft["body_template"]))
    assert leftover <= field_keys
    # fixed tokens were already substituted
    assert "{{supplier_name}}" not in draft["body_template"]
    assert "Jiangsu Metal Works Co." in draft["body"]

    for f in draft["fields"]:
        assert f["tag"] in VALID_TAGS
        assert set(f) == {"key", "label", "value", "tag", "confidence"}


def test_get_provider_stub_selected(seeded_settings):
    # conftest pins LLM_PROVIDER=stub
    assert isinstance(get_provider(seeded_settings), StubProvider)


# --------------------------------------------------------------------------- #
# Shared outreach draft — one template for every matched supplier
# --------------------------------------------------------------------------- #

_SHARED_ORDER = ["quantity", "shipping_location"]
_SHARED_KEYS = set(_SHARED_ORDER)


def test_stub_shared_draft_is_canonical_and_priceless():
    enquiry = Extraction(
        scenario="client_enquiry", product_description="Nitrile Gloves",
        quantity=200000, unit_of_measure="unit", delivery_terms="CIF Felixstowe",
        destination="Birmingham, UK", specifications="4 mil, blue, AQL 1.5",
        client_name="Lisa Bergstrom", client_company="Nordic Chem Solutions AB",
    )
    draft = StubProvider().draft_shared_outreach(enquiry, {}, get_settings())

    assert set(draft) == {"to", "subject", "body_template", "fields", "body"}
    assert draft["to"] == "{{supplier_name}}"                     # per-recipient placeholder
    assert "{{supplier_name}}" in draft["body_template"]          # stays live
    assert [f["key"] for f in draft["fields"]] == _SHARED_ORDER
    assert draft["body"] == render_body(draft["body_template"], draft["fields"])
    for f in draft["fields"]:
        assert f["tag"] in VALID_TAGS
        assert "{{" not in f["value"]
        assert "price" not in f["key"].lower()
        assert "client" not in f["key"].lower()
    assert "price" not in draft["body_template"].lower() or "pricing" in draft["body_template"].lower()
    # the client's identity must never appear anywhere in what a supplier sees
    for text in (draft["subject"], draft["body_template"], draft["body"]):
        assert "Lisa Bergstrom" not in text
        assert "Nordic Chem Solutions" not in text
    assert "stock" in draft["body"].lower()  # asks about availability, per spec


def test_stub_shared_draft_marks_missing_values_needs_review():
    """quantity / shipping location we can't fill are EMPTY + NEEDS-REVIEW so the
    UI prompts the operator before a send is allowed."""
    thin = Extraction(scenario="client_enquiry", product_description="Widgets")
    fields = {f["key"]: f for f in StubProvider().draft_shared_outreach(thin, {}, get_settings())["fields"]}

    for key in ("quantity", "shipping_location"):
        assert fields[key]["value"] == ""
        assert fields[key]["tag"] == "NEEDS-REVIEW"


def test_ollama_shared_draft_discards_model_fields_and_tokens(monkeypatch):
    """A weak local model echoes template tokens back as field values, exposes
    system tokens as editable rows, and — despite the system prompt — tries to
    name the client and quote a reference price. None of that must reach the
    stored draft; the client's real name/company must never appear anywhere."""
    reply = {
        "subject": "RFQ - {{product_name}} for {{client_company}}",
        "body_template": (
            "Hi {{supplier_name}} team,\n\n"
            "This is for {{client_name}} at {{client_company}}.\n"
            "- Quantity: {{quantity}}\n- Ship to: {{shipping_location}}\n"
            "- Deliver to: {{destination}}\n- Urgency: {{urgency}}\n"
            "- Ref price: {{price_ref}}\n\nRegards,\n{{broker_name}}\n{{broker_email}}"
        ),
        "fields": [
            {"key": "product_name", "label": "Product Name", "value": "{{product_name}}",
             "tag": "AI-GENERATED", "confidence": None},
            {"key": "supplier_name", "label": "Supplier Name", "value": "{{supplier_name}}",
             "tag": "FROM-DATABASE", "confidence": None},
            {"key": "broker_name", "label": "Broker Name", "value": "{{broker_name}}",
             "tag": "FROM-DATABASE", "confidence": None},
            {"key": "quantity", "label": "Quantity", "value": "", "tag": "AI-GENERATED",
             "confidence": None},
            {"key": "client_company", "label": "Client Company", "value": "Nordic Chem",
             "tag": "AI-GENERATED", "confidence": None},
            {"key": "price_ref", "label": "Ref price", "value": "$1.20 / unit",
             "tag": "FROM-DATABASE", "confidence": None},
        ],
    }
    monkeypatch.setattr(httpx, "post", _fake_post(reply))
    enquiry = Extraction(
        scenario="client_enquiry", product_description="Citric Acid", quantity=8000,
        unit_of_measure="kg", specifications="99.5%", delivery_terms="FOB Rotterdam",
        destination="Hamburg", client_name="Lisa Bergstrom", client_company="Nordic Chem",
    )
    draft = OllamaProvider(_ollama_settings()).draft_shared_outreach(
        enquiry, {}, get_settings()
    )

    keys = [f["key"] for f in draft["fields"]]
    # system tokens are NEVER editable fields
    assert "supplier_name" not in keys and "broker_name" not in keys and "product_name" not in keys
    # no price, no client anywhere in the field set
    assert not any("price" in k.lower() for k in keys)
    assert not any("client" in k.lower() for k in keys)
    assert "price_ref" not in draft["body_template"]  # substituted away (empty) — not a bare token
    # the canonical fields are always present, valued from the extraction
    assert _SHARED_KEYS <= set(keys)
    qty = next(f for f in draft["fields"] if f["key"] == "quantity")
    assert qty["value"] == "8,000 kg"
    # {{destination}} was fillable from the extraction; {{urgency}} (normal) was not
    assert "Hamburg" in draft["body_template"]
    # the client's actual identity must NEVER reach the stored draft, in any form
    for text in (draft["subject"], draft["body_template"], draft["body"]):
        assert "Lisa Bergstrom" not in text
        assert "Nordic Chem" not in text
        assert "{{client_name}}" not in text and "{{client_company}}" not in text
    for f in draft["fields"]:
        assert "Nordic Chem" not in f["value"] and "Lisa Bergstrom" not in f["value"]
        # NOTHING is left as a raw token in a field value
        assert "{{" not in f["value"]
    # per-recipient placeholder stays; system tokens resolved
    assert "{{supplier_name}}" in draft["body_template"]
    assert "{{broker_name}}" not in draft["body_template"]
    assert draft["body"] == render_body(draft["body_template"], draft["fields"])


# --------------------------------------------------------------------------- #
# Ollama provider — HTTP mocked, no server required
# --------------------------------------------------------------------------- #


def _ollama_settings():
    return dataclasses.replace(get_settings(), llm_provider="ollama", ollama_model="test-model")


def _fake_post(content_obj, *, status_code=200):
    """Return a stand-in for httpx.post that yields one Ollama /api/chat reply."""
    captured = {}

    def post(url, json=None, timeout=None):  # noqa: A002 - mirror httpx signature
        captured["url"] = url
        captured["payload"] = json
        body = {"message": {"role": "assistant", "content": json_dumps(content_obj)}}
        return httpx.Response(status_code, json=body, request=httpx.Request("POST", url))

    post.captured = captured
    return post


def json_dumps(obj):
    return obj if isinstance(obj, str) else json.dumps(obj)


def test_ollama_classify_parses_json(monkeypatch):
    reply = {
        "scenario": "client_enquiry",
        "product_description": "Industrial Grade Citric Acid",
        "quantity": 8000,
        "unit_of_measure": "kg",
        "delivery_terms": "CIF Stockholm",
        "urgency": "high",
        "confidence": 0.82,
    }
    post = _fake_post(reply)
    monkeypatch.setattr(httpx, "post", post)

    ex = OllamaProvider(_ollama_settings()).classify_and_extract(FORMAL_ENQUIRY)

    assert ex.scenario == "client_enquiry"
    assert ex.quantity == 8000
    assert ex.unit_of_measure == "kg"
    assert ex.urgency == "high"
    assert post.captured["url"].endswith("/api/chat")
    assert post.captured["payload"]["model"] == "test-model"
    assert post.captured["payload"]["format"] == "json"
    assert post.captured["payload"]["stream"] is False


def test_ollama_draft_shape_and_body_consistency(monkeypatch):
    reply = {
        # model dropped tokens into the subject — must come out fully rendered
        "subject": "Quote Request for {{product_name}} - {{client_company}} ({{quantity}})",
        "body_template": (
            "Hi {{supplier_name}} team,\n\nQuantity: {{quantity}}\nPrice ref: {{price_ref}}\n\n"
            "Regards,\n{{broker_name}}"
        ),
        "fields": [
            {"key": "quantity", "label": "Qty", "value": "8,000 kg", "tag": "AI-GENERATED",
             "confidence": 0.9},
            {"key": "price_ref", "label": "Ref price", "value": "$1.20 / kg",
             "tag": "FROM-DATABASE", "confidence": None},
        ],
    }
    monkeypatch.setattr(httpx, "post", _fake_post(reply))

    enquiry = Extraction(scenario="client_enquiry", product_description="Citric Acid",
                         quantity=8000, client_company="Nordic Chem")
    candidate = {"supplier_name": "Anhui Chem", "supplier_email": "z@anhuichem.cn",
                 "product_name": "Industrial Grade Citric Acid", "unit_price": 1.2}
    draft = OllamaProvider(_ollama_settings()).draft_supplier_outreach(
        enquiry, candidate, get_settings()
    )

    assert set(draft) == {"to", "subject", "body_template", "fields", "body"}
    assert "{{" not in draft["subject"]  # subject is rendered, not a raw template
    assert draft["subject"] == "Quote Request for Industrial Grade Citric Acid - Nordic Chem (8,000 kg)"
    assert draft["to"] == "z@anhuichem.cn"
    assert draft["body"] == render_body(draft["body_template"], draft["fields"])
    assert "{{supplier_name}}" not in draft["body_template"]  # fixed token substituted
    assert "Anhui Chem" in draft["body"]
    for f in draft["fields"]:
        assert f["tag"] in VALID_TAGS


def test_ollama_missing_model_raises(monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post({}, status_code=404))
    with pytest.raises(LLMError, match="ollama pull"):
        OllamaProvider(_ollama_settings()).classify_and_extract(FORMAL_ENQUIRY)


def test_ollama_connection_error_raises(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(LLMError, match="ollama serve"):
        OllamaProvider(_ollama_settings()).classify_and_extract(FORMAL_ENQUIRY)


def test_get_provider_ollama_selected():
    assert isinstance(get_provider(_ollama_settings()), OllamaProvider)
