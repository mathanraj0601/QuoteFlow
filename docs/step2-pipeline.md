# Step 2 — the `process_email()` pipeline (v2SQ)

Implements Plan B §5 against the canonical v2SQ schema: a real inbound client
email is classified, extracted, matched to suppliers, and turned into one
draft outreach email per matched supplier (`supplier_quote_requests` rows).
Sending anything out (`gmail.send`) and client-facing quotations remain out of
scope (integration guide §9.4, minimal-version §5).

## Flow

```
process_email(email_data)                       # single-arg; poller + POST /process-email
  │
  ├─ already seen this gmail_message_id?  → replay the prior result (idempotent)
  │
  ├─ llm.classify_and_extract(email)            # Extraction{scenario, product_description,
  │                                             #   quantity, uom, delivery_terms, ...}
  │     scenario != "client_enquiry"  → insert inbound email_threads row, return {"status":"logged"}
  │
  ├─ repository.get_or_create_client(from_email)
  ├─ repository.insert_inbound_thread(...)       # email_threads, direction='inbound'
  │
  ├─ matching.find_matches(product_description)  # Chroma products.query(n=MATCH_TOP_N)
  │     → sql_join_suppliers_for_products(...)   #   priced supplier_products rows
  │     + same-specialisation suppliers          #   (seed has 1 supplier/product — see below)
  │     → rerank → match_score in 0..1
  │
  ├─ keep every candidate with match_score >= MATCH_SCORE_THRESHOLD
  ├─ for each: llm.draft_supplier_outreach(...)  # {to, subject, body_template, fields[], body}
  │            repository.create_supplier_quote_request(...)   # status='draft'
  │
  └─ return {"status":"drafted", "email_thread_id", "supplier_quote_request_ids":[...],
             "matched_suppliers":[...]}
```

## REST API (`app.py`)

| Method + path | Purpose |
|---|---|
| `POST /process-email` | thin wrapper over `process_email()` (Plan B §7) |
| `GET /enquiries` | "The Desk": every inbound email_threads row + draft/sent counts |
| `GET /enquiry/{email_thread_id}/supplier-drafts` | original email + `supplier_drafts[]` + `progress` (Plan B §6) |
| `PATCH /supplier-quote-requests/{id}` | persist operator edits (`draft_to/subject/body`, `fields`) |
| `POST /supplier-quote-requests/{id}/send` | **mark-only**: status→`sent`, `sent_at` set, logs the MIME it *would* send; `transmitted:false` |
| `GET /suppliers` | supplier directory (UI View 3) |

## Providers (no key required to run)

| Concern | `auto` default | Offline fallback | Force |
|---|---|---|---|
| Embeddings (`EMBEDDING_PROVIDER`) | Chroma ONNX `all-MiniLM-L6-v2` (downloaded once, ~80 MB) | deterministic hashed bag-of-words (`vectorstore.HashEmbedder`) | `chroma_default` / `hash` |
| LLM (`LLM_PROVIDER`) | Claude `claude-opus-5` when `ANTHROPIC_API_KEY` / `ant auth` resolves | `llm.StubProvider` (regex classify + field extraction + templated draft) | `anthropic` / `stub` |

Add `ANTHROPIC_API_KEY` (or `ant auth login`) and set `EMBEDDING_PROVIDER=chroma_default`,
then re-run `python -m gmail_trigger_poc.seed` to go fully live.

## Deviations from the plans (with reasons)

1. **`MATCH_SCORE_THRESHOLD` defaults to 0.50, not 0.65.** MiniLM cosine
   similarity on short enquiry text lands lower than the plans assumed, the seed
   catalogue has exactly one supplier per product (so a high bar collapses the
   fan-out), and the flow doc's open question #7 flags this cutoff as tunable.
   Env-overridable.
2. **Same-specialisation suppliers are pulled in alongside the priced-catalogue
   join.** With one supplier per product, `sql_join_suppliers_for_products`
   alone can't "fan out" to N suppliers. `suppliers.specialization` is the field
   the schema says "feeds ranking step", so `matching.find_matches` also brings
   in same-category suppliers (scored a notch lower, `match_source:
   "specialization"`, no priced row). Only categories whose product hit is
   within `CATEGORY_HIT_FRAC` (0.6) of the best hit count.
3. **`email_threads.gmail_thread_id TEXT` added** (one additive column). The
   canonical `thread_id` is `INTEGER NOT NULL`; Gmail thread ids are hex. The
   live pipeline stores the hex here and derives `thread_id =
   int(hex,16) & 0x7fffffff` (stable per Gmail thread). Seed rows keep 1..26 and
   leave the new column NULL.
4. **Vector store is ChromaDB** (Plan B §1/§4 + schema notes), not the
   minimal-version doc's "in-process JSON embeddings in SQLite". The Chroma
   collection's dimension is pinned by whichever embedder seeds it first —
   re-seed after changing `EMBEDDING_PROVIDER`.
5. **`quotations` / `quotation_field_status` are left unused** (deferred in
   v2SQ). Field-level tags live inline in `supplier_quote_requests.draft_fields_json`.

## Validation performed

- `python -m gmail_trigger_poc.seed` → row counts 15 / 18 / 26 / 26 / 26 / 104 / 26, Chroma `products` = 26.
- Matching (MiniLM): precise enquiries (bolts, citric acid, CNC housings, PCB
  connectors, cotton canvas) each fan out to 2–3 same-category suppliers;
  "out of office" / off-catalogue text → 0 kept.
- End-to-end HTTP smoke (MiniLM + stub LLM): `POST /process-email` → drafted,
  `GET …/supplier-drafts` → cards + progress, `POST …/send` → sent / `transmitted:false`,
  re-send → 409, replayed `POST /process-email` → `idempotent_replay:true`, no duplicate rows.
- `uv run pytest` — full suite green (`tests/test_seed.py`, `test_matching.py`,
  `test_llm.py`, rewritten `test_pipeline.py`, extended `test_app.py`).
