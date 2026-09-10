-- ============================================================================
-- QuoteFlow — RDBMS Schema — v2SQ (Supplier Quote outreach flow)
-- Dialect: SQLite (POC). Portable to Postgres — see notes at bottom.
--
-- Shipped verbatim from plans/v2SQ-quoteflow_schema_without_extraction.sql with
-- exactly ONE additive change, marked "[gmail-trigger-poc]" below:
--   email_threads.gmail_thread_id TEXT — the raw Gmail thread id (hex). The
--   canonical schema's thread_id is INTEGER NOT NULL; Gmail thread ids are hex
--   strings, so the live pipeline derives the integer thread_id as
--   (int(gmail_thread_id, 16) & 0x7fffffff) and keeps the original hex here.
--   Seed rows keep their integer thread_id (1..26) and leave this NULL.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- CORE ENTITIES
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS clients (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    company                 TEXT NOT NULL,
    contact_email           TEXT NOT NULL UNIQUE,
    region                  TEXT,
    preferred_suppliers     TEXT,                       -- JSON array of supplier_id
    order_history_count     INTEGER DEFAULT 0,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS suppliers (
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

CREATE TABLE IF NOT EXISTS products (
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

CREATE TABLE IF NOT EXISTS supplier_products (
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

CREATE TABLE IF NOT EXISTS packing_slips (
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

CREATE TABLE IF NOT EXISTS quotations (
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
CREATE TABLE IF NOT EXISTS quotation_field_status (
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

CREATE TABLE IF NOT EXISTS email_threads (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_message_id        TEXT UNIQUE,
    thread_id               INTEGER NOT NULL,            -- groups the 4-step cycle
    gmail_thread_id         TEXT,                        -- [gmail-trigger-poc] raw Gmail thread id (hex); NULL for seed rows
    from_email              TEXT NOT NULL,
    to_email                TEXT NOT NULL,
    subject                 TEXT,
    body_text               TEXT,
    direction               TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
    extracted_data_json     TEXT,                        -- AI-parsed fields cache
    embedding_status        TEXT DEFAULT 'pending'      -- body_text vector lives in Chroma collection
                              CHECK (embedding_status IN ('pending','embedded','failed')),  -- "email_threads", id = email_threads.id
    processed_at            TIMESTAMP,
    linked_quotation_id     INTEGER REFERENCES quotations(id),

    -- [gmail-trigger-poc] v2SQ enquiry lifecycle — mirrors the prototype UI's
    -- Desk badges + processing trace (plans/v2SQ-quoteflow-ui.html). Only ever
    -- set on inbound client-enquiry rows; NULL on seed-backfill / outbound rows.
    --   new        -- row exists, live pipeline hasn't finished a stage yet
    --   processing -- pipeline is mid-flight (see pipeline_stage for how far)
    --   drafted    -- supplier_quote_requests rows created (0 or more)
    --   logged     -- classified as not a client enquiry; nothing drafted
    status                  TEXT
                              CHECK (status IS NULL OR status IN ('new','processing','drafted','logged')),
    -- Coarse progress inside 'processing', shown as the trace in the UI:
    --   received -> extracted -> matched -> drafting -> drafted
    -- 'drafting' = suppliers matched, per-supplier outreach in progress
    -- (drafts_done / drafts_total track how far).
    pipeline_stage          TEXT
                              CHECK (pipeline_stage IS NULL OR
                                     pipeline_stage IN ('received','extracted','matched','drafting','drafted')),
    pipeline_updated_at     TIMESTAMP,
    -- How many matched suppliers this enquiry will draft to (set once at match
    -- time); drafts_done is COUNT(supplier_quote_requests) as they commit one by
    -- one, so the UI can show "Drafting 2 of 3 ...".
    drafts_total            INTEGER
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
CREATE TABLE IF NOT EXISTS supplier_quote_requests (
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
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat_messages (
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

CREATE INDEX IF NOT EXISTS idx_email_threads_thread_id       ON email_threads(thread_id);
CREATE INDEX IF NOT EXISTS idx_email_threads_gmail_thread    ON email_threads(gmail_thread_id);
CREATE INDEX IF NOT EXISTS idx_email_threads_status          ON email_threads(status);
CREATE INDEX IF NOT EXISTS idx_email_threads_quotation       ON email_threads(linked_quotation_id);
CREATE INDEX IF NOT EXISTS idx_email_threads_embed_status    ON email_threads(embedding_status);
CREATE INDEX IF NOT EXISTS idx_products_embed_status         ON products(embedding_status);
CREATE INDEX IF NOT EXISTS idx_suppliers_embed_status        ON suppliers(embedding_status);
CREATE INDEX IF NOT EXISTS idx_quotations_client             ON quotations(client_id);
CREATE INDEX IF NOT EXISTS idx_quotations_supplier           ON quotations(supplier_id);
CREATE INDEX IF NOT EXISTS idx_quotations_status             ON quotations(status);
CREATE INDEX IF NOT EXISTS idx_supplier_products_product     ON supplier_products(product_id);
CREATE INDEX IF NOT EXISTS idx_supplier_products_supplier    ON supplier_products(supplier_id);
CREATE INDEX IF NOT EXISTS idx_quotation_field_status_quote  ON quotation_field_status(quotation_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_quotation       ON chat_messages(quotation_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_embed_status    ON chat_messages(embedding_status);
CREATE INDEX IF NOT EXISTS idx_sqr_email_thread              ON supplier_quote_requests(email_thread_id);
CREATE INDEX IF NOT EXISTS idx_sqr_supplier                  ON supplier_quote_requests(supplier_id);
CREATE INDEX IF NOT EXISTS idx_sqr_status                    ON supplier_quote_requests(status);
