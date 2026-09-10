-- ============================================================================
-- QuoteFlow — RDBMS Schema — v2SQ (Supplier Quote outreach flow)
-- Dialect: SQLite (POC). Portable to Postgres — see notes at bottom.
--
-- v2SQ CHANGE SUMMARY (vs. original quoteflow_schema_without_extraction.sql):
--   - Client enquiry processing no longer produces a client-facing quotation.
--     Instead it produces one draft outreach email PER matched supplier
--     (new table: supplier_quote_requests). Sending a final quotation back
--     to the client is out of scope for this version.
--   - `quotations` and `quotation_field_status` are kept in this schema
--     (harmless, not dropped) but are DEFERRED — the live pipeline no longer
--     writes to them. They're the natural home for a future "client
--     quotation" phase if that comes back into scope.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- CORE ENTITIES
-- ----------------------------------------------------------------------------

CREATE TABLE clients (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    company                 TEXT NOT NULL,
    contact_email           TEXT NOT NULL UNIQUE,
    region                  TEXT,
    preferred_suppliers     TEXT,                       -- JSON array of supplier_id
    order_history_count     INTEGER DEFAULT 0,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE suppliers (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    contact_email           TEXT NOT NULL UNIQUE,
    phone                   TEXT,
    region                  TEXT,
    rating                  REAL DEFAULT 0,
    specialization          TEXT,                       -- primary category, feeds ranking step
    certifications          TEXT,
    payment_terms           TEXT,
    embedding_status        TEXT DEFAULT 'pending'      -- profile vector lives in Chroma collection
                              CHECK (embedding_status IN ('pending','embedded','failed')),  -- "supplier_profiles", id = suppliers.id
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE products (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    category                TEXT NOT NULL,
    description             TEXT,
    specifications          TEXT,
    hs_code                 TEXT,
    unit_of_measure         TEXT,
    embedding_status        TEXT DEFAULT 'pending'      -- vector itself lives in Chroma collection "products",
                              CHECK (embedding_status IN ('pending','embedded','failed')),  -- id = products.id (as string)
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- SUPPLIER <-> PRODUCT OFFERS (the sourceable catalog)
-- ----------------------------------------------------------------------------

CREATE TABLE supplier_products (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id             INTEGER NOT NULL REFERENCES suppliers(id),
    product_id              INTEGER NOT NULL REFERENCES products(id),
    unit_price              REAL NOT NULL,
    moq                     INTEGER,
    bulk_pricing_json       TEXT,                       -- {"qty": price, ...}
    lead_time_days          INTEGER,
    last_quoted_date        DATE,
    UNIQUE (supplier_id, product_id)
);

CREATE TABLE packing_slips (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id             INTEGER NOT NULL REFERENCES suppliers(id),
    product_id              INTEGER NOT NULL REFERENCES products(id),
    packaging_details       TEXT,
    weight                  TEXT,
    dimensions              TEXT,
    documentation_urls      TEXT
);

-- ----------------------------------------------------------------------------
-- CLIENT QUOTATIONS — DEFERRED IN v2SQ
-- Not written to by the live pipeline in this version (see header note).
-- Left in place, unmodified, for a future client-facing-quotation phase.
-- ----------------------------------------------------------------------------

CREATE TABLE quotations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id               INTEGER NOT NULL REFERENCES clients(id),
    supplier_id             INTEGER NOT NULL REFERENCES suppliers(id),
    product_id              INTEGER NOT NULL REFERENCES products(id),
    quoted_price            REAL,
    quantity                INTEGER,
    shipping_cost           REAL,
    margin_pct              REAL,                       -- explicit now — was implicit before
    status                  TEXT DEFAULT 'draft'
                              CHECK (status IN ('draft','sent','accepted','rejected')),
    match_score             REAL,                       -- overall supplier-match confidence (0.0-1.0)
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at                 TIMESTAMP,
    valid_until             DATE
);

-- DEFERRED IN v2SQ (see header note). Field-level provenance/confidence
-- tagging for supplier drafts is stored inline as JSON on
-- supplier_quote_requests.draft_fields_json instead of a separate table —
-- keeps the new flow POC-light. This table is unused but left in place.
CREATE TABLE quotation_field_status (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    quotation_id            INTEGER NOT NULL REFERENCES quotations(id),
    field_name              TEXT NOT NULL,               -- 'unit_price','quantity','shipping_cost', etc.
    value                   TEXT,
    source_tag              TEXT NOT NULL
                              CHECK (source_tag IN ('AI-GENERATED','FROM-DATABASE','NEEDS-REVIEW','MANUAL')),
    confidence              REAL,                        -- 0.0-1.0, NULL for FROM-DATABASE/MANUAL
    edited_by_operator      BOOLEAN DEFAULT 0,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (quotation_id, field_name)
);

-- ----------------------------------------------------------------------------
-- EMAIL LOG (raw in/out + semantic search source)
-- ----------------------------------------------------------------------------

CREATE TABLE email_threads (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_message_id        TEXT UNIQUE,
    thread_id               INTEGER NOT NULL,            -- groups the 4-step cycle
    from_email              TEXT NOT NULL,
    to_email                TEXT NOT NULL,
    subject                 TEXT,
    body_text               TEXT,
    direction               TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
    extracted_data_json     TEXT,                        -- AI-parsed fields cache
    embedding_status        TEXT DEFAULT 'pending'      -- body_text vector lives in Chroma collection
                              CHECK (embedding_status IN ('pending','embedded','failed')),  -- "email_threads", id = email_threads.id
    processed_at            TIMESTAMP,
    linked_quotation_id     INTEGER REFERENCES quotations(id)
);

-- ----------------------------------------------------------------------------
-- SUPPLIER QUOTE REQUESTS (v2SQ core table — the outbound leg to suppliers)
-- One row per (client enquiry × matched supplier). A single client enquiry
-- fans out into N rows here — one per supplier above the match-score
-- threshold — each independently editable and independently sendable from
-- the UI. This is what "Drafted" status now means: N of these exist for
-- the enquiry, each starting as 'draft'.
-- ----------------------------------------------------------------------------

-- IMPORTANT: this row IS the email that gets sent — not a set of decorative
-- fields alongside one. `draft_body` is always the exact text that would go
-- out on send; `draft_body_template` + `draft_fields_json` exist so the UI
-- can offer per-field editing (quantity, target price, delivery, etc.) that
-- regenerates `draft_body` live, instead of one big free-text box. Whichever
-- side the operator edits — a structured field, or the body directly — the
-- other must be reconciled before send. See draft_edit_mode below.
CREATE TABLE supplier_quote_requests (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    email_thread_id         INTEGER NOT NULL REFERENCES email_threads(id),  -- the client enquiry this fans out from
    client_id               INTEGER NOT NULL REFERENCES clients(id),
    supplier_id             INTEGER NOT NULL REFERENCES suppliers(id),
    product_id              INTEGER NOT NULL REFERENCES products(id),
    quantity                INTEGER,                    -- from the client enquiry, carried through for supplier's context
    specs_summary           TEXT,                        -- client's specs/delivery/quality asks, summarized for the supplier
    match_score              REAL,                        -- this supplier's match confidence (0.0-1.0) against the enquiry

    -- The actual email, editable at any time before send:
    draft_to                 TEXT NOT NULL,               -- defaults to suppliers.contact_email, operator can override
    draft_subject             TEXT NOT NULL,
    draft_body                 TEXT NOT NULL,               -- CANONICAL — this exact text is what gets sent on click

    -- Structured editing support (drives draft_body, doesn't replace it):
    draft_body_template        TEXT NOT NULL,               -- draft_body's source, with {{field_key}} tokens, e.g.
                                                             -- "...Quantity requested: {{quantity}}..."
    draft_fields_json          TEXT NOT NULL,               -- [{"key":"quantity","label":"Quantity requested",
                                                             --   "value":"800 m","tag":"AI-GENERATED","confidence":0.9}, ...]
                                                             -- draft_body = draft_body_template with each {{key}} replaced
                                                             -- by its current field value. Superset of the old
                                                             -- field_tags_json — now carries the value too, not just the tag.
    draft_edit_mode             TEXT DEFAULT 'fields'
                                CHECK (draft_edit_mode IN ('fields','free_text')),
                                                             -- 'fields' (default): editing a structured field
                                                             -- regenerates draft_body from the template.
                                                             -- 'free_text': operator typed directly into the body;
                                                             -- draft_body is now authoritative and no longer kept in
                                                             -- sync with draft_fields_json for this row.

    status                    TEXT DEFAULT 'draft'
                              CHECK (status IN ('draft','sent')),
    gmail_message_id          TEXT,                        -- set once actually sent
    created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at                    TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- OPERATOR <-> AI CHAT (scoped chat feature, Phase 2 per minimal-version doc)
-- quotation_id NULL = general/unscoped chat; non-NULL = scoped to one deal.
-- Kept as one table so both "search this deal's chat" and "search all my past
-- chats" run off the same embedding index.
-- v2SQ note: quotation_id still points at the deferred `quotations` table.
-- Out of scope for now (chat is Phase 2, per minimal-version doc); if scoped
-- chat is built before client quotations come back, point this at
-- supplier_quote_requests(id) instead.
-- ----------------------------------------------------------------------------

CREATE TABLE chat_messages (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    quotation_id            INTEGER REFERENCES quotations(id),
    role                    TEXT NOT NULL CHECK (role IN ('user','assistant')),
    message_text            TEXT NOT NULL,
    embedding_status        TEXT DEFAULT 'pending'      -- message_text vector lives in Chroma collection
                              CHECK (embedding_status IN ('pending','embedded','failed')),  -- "chat_messages", id = chat_messages.id
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- INDEXES
-- ----------------------------------------------------------------------------

CREATE INDEX idx_email_threads_thread_id       ON email_threads(thread_id);
CREATE INDEX idx_email_threads_quotation       ON email_threads(linked_quotation_id);
CREATE INDEX idx_email_threads_embed_status    ON email_threads(embedding_status);
CREATE INDEX idx_products_embed_status         ON products(embedding_status);
CREATE INDEX idx_suppliers_embed_status        ON suppliers(embedding_status);
CREATE INDEX idx_quotations_client             ON quotations(client_id);
CREATE INDEX idx_quotations_supplier           ON quotations(supplier_id);
CREATE INDEX idx_quotations_status             ON quotations(status);
CREATE INDEX idx_supplier_products_product     ON supplier_products(product_id);
CREATE INDEX idx_supplier_products_supplier    ON supplier_products(supplier_id);
CREATE INDEX idx_quotation_field_status_quote  ON quotation_field_status(quotation_id);
CREATE INDEX idx_chat_messages_quotation       ON chat_messages(quotation_id);
CREATE INDEX idx_chat_messages_embed_status    ON chat_messages(embedding_status);
CREATE INDEX idx_sqr_email_thread              ON supplier_quote_requests(email_thread_id);
CREATE INDEX idx_sqr_supplier                  ON supplier_quote_requests(supplier_id);
CREATE INDEX idx_sqr_status                    ON supplier_quote_requests(status);

-- ============================================================================
-- NOTES — vector storage lives in ChromaDB, not SQLite
-- ============================================================================
-- Two-store architecture:
--   - SQLite (this file)  -> all relational data, source of truth for IDs
--   - ChromaDB (./chroma_data/, PersistentClient, local/embedded, no server)
--        -> 4 collections: "products", "email_threads", "supplier_profiles",
--           "chat_messages". Each Chroma record's id = the matching SQL row's
--           primary key (as a string), so a Chroma query returns IDs you use
--           directly in a SQL `WHERE id IN (...)` to pull the full row back.
--
-- 1. `embedding_status` stays on the SQL side (products, suppliers,
--    email_threads, chat_messages) purely as pipeline bookkeeping — so a
--    background worker can query `WHERE embedding_status = 'pending'`
--    without touching Chroma. The vector itself only exists in Chroma.
-- 2. `chat_messages.quotation_id` is nullable by design — NULL = general/
--    unscoped chat, non-NULL = scoped to one deal. If scoped and general
--    chat should never mix (different retention/privacy rules), split into
--    two tables instead; Chroma-side, just add a `scope` metadata field.
-- 3. `quotation_field_status` is DEFERRED in v2SQ — see header note.
-- 4. Postgres port: swap `INTEGER PRIMARY KEY AUTOINCREMENT` -> `SERIAL
--    PRIMARY KEY`. ChromaDB itself doesn't need to change — it already runs
--    the same way in both local dev and a real deployment (or swap to its
--    hosted offering later without touching the SQL side at all).
-- 5. v2SQ: `supplier_quote_requests` is the new core output table. One
--    client enquiry (`email_threads` row, direction='inbound') fans out into
--    N rows here — one per matched supplier scoring above the match-score
--    threshold (default 0.65, configurable). Each row is independently
--    'draft' -> 'sent'. The enquiry's own status stays 'Drafted' in the UI
--    throughout — there's no separate "all sent" status; the UI instead
--    shows a per-enquiry progress indicator (e.g. "2/3 sent") computed by
--    counting this table's rows per email_thread_id, not a new SQL status.
-- 6. v2SQ: `draft_body` is always what actually gets sent — it is never a
--    second, out-of-sync copy of the structured fields. The POC UI edits it
--    the easy way (regenerate from draft_body_template + draft_fields_json
--    on every field keystroke); if a future version adds direct free-text
--    editing of the body itself, `draft_edit_mode='free_text'` is the flag
--    that tells the UI to stop regenerating it from the fields.
-- ============================================================================
