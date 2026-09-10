# QuoteFlow — Plan B: Database Loaded Directly (No Extraction for Backfill) — v2SQ

**v2SQ note:** everything in §1–4 (backfill loading, embeddings) is unchanged — the seed data (clients/suppliers/products/etc.) doesn't describe the old client-quotation output, so it needed no content changes. What changed is §5's `/process-email` endpoint and §6's UI, both updated below to draft/send supplier outreach instead of a client quotation.

**Approach:** Skip the LLM entirely for getting the 100+ historical records
into the database. You already generated this data (`quoteflow_data_without_extraction.json`)
with known, clean, correctly-shaped fields — just insert it directly and
embed it directly. No classification, no extraction, no prompt debugging.

**Important — read this first:** "no extraction" applies **only** to how the
104 historical emails get into the database. The live trigger — a real new
client email arriving — still needs the extraction module, always, in both
plans. That's the actual point of the demo: showing the system handle a real
unstructured email it's never seen before. Plan B just means you're not
*also* debugging extraction accuracy on 104 emails before you get to see the
live trigger work.

**Use this plan when:** you want the live "new client email → quotation"
flow working and demoable as fast as possible, and you're fine validating
extraction prompts separately/later (or via Plan A, side by side).

---

## 0. What already exists (inputs to this plan)

| Artifact | What it is |
|---|---|
| `quoteflow_schema_without_extraction.sql` | Empty table definitions — SQLite, 9 tables |
| `quoteflow_data_without_extraction.json` | Already-clean, already-correct flat rows: 15 clients, 18 suppliers, 26 products, 26 supplier_products, 26 quotations, 104 email_threads, 26 packing_slips |
| `generate_without_extraction.py` | The original generator that produced `quoteflow_data_without_extraction.json` — reusable if you want a bigger/different dataset later |

---

## 1. Initialize storage

```bash
sqlite3 quoteflow.db < quoteflow_schema_without_extraction.sql
```
Same empty-schema step as Plan A. Then a single empty Chroma collection,
`products` — same as Plan A. (`email_threads`, `supplier_profiles`,
`chat_messages` collections aren't created — none of them feed the
quotation flow; see note below.)

---

## 2. Direct SQL load — no LLM call, straight inserts

```python
def seed_direct(db_conn):
    with open("quoteflow_data_without_extraction.json") as f:
        data = json.load(f)

    for row in data["clients"]:
        db_conn.execute(
            "INSERT INTO clients (id, name, company, contact_email, region, order_history_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (row["id"], row["name"], row["company"], row["contact_email"], row["region"], row["order_history_count"]),
        )

    for row in data["suppliers"]:
        db_conn.execute(
            "INSERT INTO suppliers (id, name, contact_email, phone, region, rating, specialization, certifications, payment_terms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["id"], row["name"], row["contact_email"], row["phone"], row["region"],
             row["rating"], row["specialization"], row["certifications"], row["payment_terms"]),
        )

    for row in data["products"]:
        db_conn.execute(
            "INSERT INTO products (id, name, category, description, specifications, hs_code, unit_of_measure) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row["id"], row["name"], row["category"], row.get("description"), row.get("specifications"),
             row.get("hs_code"), row.get("unit_of_measure")),
        )

    for row in data["supplier_products"]:
        db_conn.execute(
            "INSERT INTO supplier_products (id, supplier_id, product_id, unit_price, moq, bulk_pricing_json, lead_time_days, last_quoted_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (row["id"], row["supplier_id"], row["product_id"], row["unit_price"], row["moq"],
             row.get("bulk_pricing_json"), row["lead_time_days"], row.get("last_quoted_date")),
        )

    for row in data["quotations"]:
        db_conn.execute(
            "INSERT INTO quotations (id, client_id, supplier_id, product_id, quoted_price, quantity, shipping_cost, "
            "margin_pct, status, match_score, created_at, sent_at, valid_until) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["id"], row["client_id"], row["supplier_id"], row["product_id"],
             row["quoted_price"], row["quantity"], row["shipping_cost"], row.get("margin_pct"),
             row["status"], row.get("match_score"), row.get("created_at"), row.get("sent_at"),
             row.get("valid_until")),
        )

    for row in data["email_threads"]:
        db_conn.execute(
            "INSERT INTO email_threads (id, gmail_message_id, thread_id, from_email, to_email, subject, body_text, direction, extracted_data_json, processed_at, linked_quotation_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["id"], row["gmail_message_id"], row["thread_id"], row["from_email"], row["to_email"],
             row["subject"], row["body_text"], row["direction"], row.get("extracted_data_json"),
             row["processed_at"], row.get("linked_quotation_id")),
        )

    for row in data["packing_slips"]:
        db_conn.execute(
            "INSERT INTO packing_slips (id, supplier_id, product_id, packaging_details, weight, dimensions, documentation_urls) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row["id"], row["supplier_id"], row["product_id"], row.get("packaging_details"),
             row.get("weight"), row.get("dimensions"), row.get("documentation_urls")),
        )

    db_conn.commit()
```

No LLM, no classification, no prompt — this is a straight loop over data you
already trust, because you generated it yourself with known-correct shape.

> **Fix note:** an earlier version of this function omitted several columns
> that exist in both the schema and the JSON (`products.hs_code` /
> `unit_of_measure`, `supplier_products.id` / `last_quoted_date`,
> `quotations.margin_pct` / `match_score` / `created_at` / `sent_at` /
> `valid_until`, `packing_slips.id` / `documentation_urls`). Those inserts
> ran without error but silently dropped the data — e.g. every seeded
> quotation got `created_at` = load time instead of its real historical
> date, and `sent_at`/`valid_until` came back `NULL`. The version above
> carries every JSON field through to its matching column, verified against
> `quoteflow_schema_without_extraction.sql`.

---

## 3. Direct embedding — still calls an embedding model, but not an LLM extraction call

This is worth being precise about: embedding text still requires calling
*something* (Chroma's local model, or Voyage/OpenAI) — what's skipped is the
LLM *extraction* call (the one that reads messy raw email text and figures
out structured fields). Here, the fields are already known and clean, so
there's nothing to extract — you're just turning already-correct text into
vectors.

```python
def embed_direct(db_conn, chroma_client):
    products_col = chroma_client.get_collection("products")
    products = db_conn.execute("SELECT id, name, category, specifications FROM products").fetchall()

    products_col.upsert(
        ids=[str(p["id"]) for p in products],
        documents=[f"{p['name']} {p['specifications'] or ''}" for p in products],
        metadatas=[{"category": p["category"]} for p in products],
    )
    db_conn.execute("UPDATE products SET embedding_status='embedded'")
    db_conn.commit()
```

That's it — nothing else gets embedded. `email_threads` stays a SQL-only
table (all 104 rows already sit there with their `extracted_data_json`
intact, just never turned into vectors). `supplier_profiles` and
`chat_messages` collections aren't created at all — none of the three feed
the quotation flow; they only matter for "similar past enquiries," scoped
chat, and capability-based supplier search, none of which are in scope
right now.

---

## 4. Run it — one script, no server needed yet

```python
# seed_direct_run.py
conn = sqlite3.connect("quoteflow.db")
chroma_client = chromadb.PersistentClient(path="./chroma_data")
chroma_client.get_or_create_collection("products")

seed_direct(conn)
embed_direct(conn, chroma_client)
print("Seeded and embedded — ready for /process-email")
```

```bash
python seed_direct_run.py
```

**End state:** identical database shape to what Plan A produces, but
populated in seconds, guaranteed-correct, zero prompt-accuracy risk — the
tradeoff being you haven't proven the extraction prompts work on messy real
email text, only that the live endpoint's matching/quotation logic works
against good data.

---

## 5. 🚧 *Yet to improve* — The one live endpoint (v2SQ: fans out to suppliers)

This is the important part: **`/process-email` is exactly the same
code in both plans.** The only thing that differed was how the historical
data got in. The live endpoint has always needed real extraction — that
requirement doesn't go away just because backfill was direct-inserted.

**v2SQ change:** instead of picking the single top-ranked supplier and
creating one client-facing `quotations` row, this now keeps **every**
candidate above the match-score threshold and creates one
`supplier_quote_requests` draft per supplier — each independently editable
and sendable from the UI.

```python
MATCH_SCORE_THRESHOLD = 0.65  # configurable; suppliers below this aren't drafted

@app.post("/process-email")
def live_email(gmail_message: dict):
    result = process_email(gmail_message, db_conn)   # extraction module — same as Plan A, Step 2

    if result["scenario"] != "client_enquiry":
        return {"status": "logged", "scenario": result["scenario"]}

    client_id = get_or_create_client(result, db_conn)
    thread_row_id = insert_email_thread_row(gmail_message, result, db_conn)

    query_text = result["product_description"]
    matches = chroma_client.get_collection("products").query(query_texts=[query_text], n_results=10)
    matched_product_ids = [int(i) for i in matches["ids"][0]]

    candidates = sql_join_suppliers_for_products(matched_product_ids, db_conn)
    ranked = rerank(candidates, matches["distances"][0])

    # v2SQ: keep every supplier above threshold, not just the top one
    above_threshold = [c for c in ranked if c["match_score"] >= MATCH_SCORE_THRESHOLD]

    draft_ids = []
    for candidate in above_threshold:
        draft = draft_supplier_outreach(result, candidate, db_conn)
        # draft = {
        #   "to": "...", "subject": "...",
        #   "body_template": "...Quantity requested: {{quantity}}...",
        #   "fields": [{"key": "quantity", "label": "Quantity requested",
        #               "value": "800 m", "tag": "AI-GENERATED", "confidence": 0.9}, ...],
        #   "body": <body_template with every {{key}} already substituted — this is
        #            what's actually sent if the operator changes nothing>
        # }
        request_id = create_supplier_quote_request(
            email_thread_id=thread_row_id,
            client_id=client_id,
            candidate=candidate,
            draft=draft,
            db_conn=db_conn,
        )   # status='draft'; writes draft_to/draft_subject/draft_body/draft_body_template/draft_fields_json
        draft_ids.append(request_id)

    return {
        "thread_id": thread_row_id,
        "status": "drafted",
        "supplier_quote_request_ids": draft_ids,
        "matched_suppliers": above_threshold,
    }
```

You still need to build `process_email()` (the `detect_scenario` +
LLM-extraction glue from Plan A, Step 2) — Plan B just means you're not
*also* running it 104 times against seed data first. You could build it
minimally at first (just enough to parse a `client_enquiry` correctly) and
harden it later.

**New in v2SQ: `draft_supplier_outreach()`.** One Claude call per matched
supplier, given the extracted enquiry fields + that supplier's product/pricing
record. It must **not** just return a finished paragraph of prose — it returns
a `body_template` with `{{field_key}}` tokens plus the structured `fields`
list that fill those tokens in, each carrying a confidence/source tag. This
is what makes the "editable fields update the actual email" behavior in the
UI possible: the operator edits a field, the client re-substitutes it into
`body_template`, and that's the literal text that gets sent — never a
second, out-of-sync summary sitting next to the real email.

---

## 6. 🚧 *Yet to improve* — UI (v2SQ: stacked supplier cards, per-card send)

Same task board, same expand-to-two-panel layout. The UI layer doesn't know
or care which plan populated the database underneath it — that's the whole
point of keeping `/process-email` shape-identical between the two plans.

**v2SQ change:** the right panel is no longer "one quotation draft + one
Send to Client button." It's a list rendered from
`supplier_quote_request_ids` (or a `GET /enquiry/{thread_id}/supplier-drafts`
fetch) — one card per row. Each card shows the actual email (To / Subject /
Body) plus, below it, the structured fields that generated that body
(quantity, target price, delivery, etc., each tagged AI-GENERATED /
FROM-DATABASE / NEEDS-REVIEW). Editing a field re-substitutes it into
`body_template` client-side and re-renders the body preview immediately —
the operator is always looking at the literal text `POST
/supplier-quote-requests/{id}/send` will send, not a decorative summary next
to it. On send, the client posts the current `to`/`subject`/`body` (and,
optionally, the edited `fields`, so the backend can persist them too) to
that endpoint. The enquiry's own status stays
`Drafted`; the UI computes a simple `sent_count / total_count` readout per
enquiry by counting `supplier_quote_requests` rows for that
`email_thread_id`, rather than reading a new SQL status.

---

## 7. 🚧 *Yet to improve* — Order of work, start to finish

1. `sqlite3 quoteflow.db < quoteflow_schema_without_extraction.sql` (empty tables)
2. Create the `products` Chroma collection (empty)
3. Write and run `seed_direct_run.py` (Steps 2-4 above) — DB + embeddings ready in one shot
4. Build a minimal `process_email()` — just enough to handle a `client_enquiry`
   correctly (full 4-mode classification can come later; live demo only
   ever needs this one mode to work, per your note that real triggers are
   only ever client enquiries)
5. Build `/process-email` (Chroma query → SQL join → rerank → filter by
   match-score threshold → draft one outreach email per remaining supplier,
   v2SQ)
6. Build the two-panel expand view in the UI — right panel renders a stack
   of supplier cards, each with its own Send button (v2SQ)
7. Manually POST a new Gmail-shaped client email at `/process-email` and
   watch the whole thing resolve into N supplier drafts — this is demoable
   almost immediately after step 3, since steps 4-5 are comparatively small
