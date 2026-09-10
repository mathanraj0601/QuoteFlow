# QuoteFlow — System Flow & Architecture Document (v2SQ — Supplier Quote outreach)

**Project:** [POC][CPS] — QuoteFlow
**Version:** 2.0 (v2SQ)
**Date:** September 2026
**Supersedes (for the live pipeline's output):** `quoteflow-flow-document.md` v1.0 — see change summary below.

---

## 0. v2SQ Change Summary

**What changed:** the live pipeline no longer drafts a quotation *to the client*. It now drafts a **quote-request email to each matched supplier**. Sending a final quotation back to the client is **out of scope** for this version — the demo ends once supplier outreach is drafted and sent.

| | v1 (superseded) | v2SQ (this document) |
|---|---|---|
| Pipeline output | One client-facing quotation draft | N supplier outreach drafts (one per matched supplier above a match-score threshold) |
| UI action | Single "Send to Client" button | One "Send" button per supplier card, sent independently |
| Matched suppliers panel | Read-only ranked list | The actionable content itself — each row is an editable, sendable draft |
| Enquiry status on completion | N/A (single send) | Stays "Drafted"; a per-card progress indicator (e.g. "2/3 sent") tracks partial sends |
| Client quotation | Step 4 of the cycle | Deferred — not built in this version |

---

## 1. Executive Summary

QuoteFlow is a procurement automation platform where the company acts as an **intermediary (broker)** between **clients** (buyers requesting products) and **suppliers** (vendors providing products). The system uses an AI agent to automate the matching of product enquiry emails with appropriate suppliers, and drafts an outreach/quote-request email to each matched supplier, presented in an editable UI before sending. Drafting and sending a final quotation back to the client is deferred to a future version.

---

## 2. Actors & Roles

| Actor | Role | Interaction |
|-------|------|-------------|
| **Client** | End customer who sends product enquiry emails | Sends enquiry → (final quotation back to client is deferred, not built in v2SQ) |
| **Intermediary (Us)** | Broker who matches clients with suppliers | Operates the QuoteFlow UI, reviews/edits/sends each supplier outreach draft |
| **Supplier** | Vendor who provides products, pricing, shipping | Receives an AI-drafted quote request per matching enquiry; replies out of band (not yet ingested in v2SQ) |
| **AI Agent** | System intelligence | Extracts enquiry data, matches suppliers, drafts one outreach email per matched supplier |

---

## 3. System Phases & Detailed Flow

### Phase 1: Data Seeding — Dummy Email Generation (One-time Setup)

**Objective:** Create a realistic corpus of 100+ procurement email threads to seed the database.

**Email Thread Structure (per transaction):**

Each dummy thread simulates a complete procurement cycle:

1. **Client → Intermediary (Enquiry)**
   - Product name / description
   - Required quantity
   - Delivery timeline / location
   - Quality specifications
   - Budget range (sometimes)

2. **Intermediary → Supplier (Quote Request)**
   - Forwarded product requirements
   - Request for unit pricing
   - Request for shipping details
   - Minimum order quantity (MOQ) inquiry

3. **Supplier → Intermediary (Quote Response)**
   - Unit price / bulk pricing tiers
   - Shipping cost & delivery timeline
   - Packing slip / documentation
   - Payment terms
   - Certifications / compliance docs

4. **Intermediary → Client (Final Quotation)**
   - Final pricing (with intermediary margin)
   - Delivery estimates
   - Terms & conditions
   - Documentation summary

**Email Characteristics (human-like):**
- Varied writing styles (formal, semi-formal, casual)
- Typos and natural language quirks
- Follow-up emails, negotiation threads
- Multiple product categories (electronics, textiles, chemicals, machinery, food products, etc.)
- Different suppliers per category
- Varied quantities, currencies, and regions

**Generation approach:** Use an LLM to generate these emails with realistic diversity across product categories, supplier profiles, pricing structures, and communication styles.

---

### Phase 2: Data Extraction & Storage

**Objective:** Extract structured data from emails and store in an optimized database.

#### 2a. Extraction Pipeline

The AI agent parses each email and extracts:

| Field | Source | Example |
|-------|--------|---------|
| Product name | Client enquiry | "Stainless Steel Bolts M10x50" |
| Product category | AI classification | "Industrial Fasteners" |
| Quantity | Client enquiry | 5,000 units |
| Unit price | Supplier quote | $0.12/unit |
| Bulk pricing tiers | Supplier quote | 1K=$0.15, 5K=$0.12, 10K=$0.10 |
| Shipping cost | Supplier quote | $450 (FOB Shenzhen) |
| Delivery timeline | Supplier quote | 14-21 business days |
| Payment terms | Supplier quote | Net 30, 50% advance |
| Supplier name | Supplier email | "Jiangsu Metal Works Co." |
| Supplier contact | Supplier email | "wang.li@jmw.com" |
| Supplier rating | Historical data | 4.2/5 |
| Packing details | Supplier packing slip | "Carton: 500pcs, Pallet: 5000pcs" |
| Certifications | Supplier docs | ISO 9001, CE Marking |
| Client name | Client email | "Apex Manufacturing Ltd." |
| Client contact | Client email | "john@apex-mfg.com" |

#### 2b. Database Design — Hybrid Approach (Recommended)

**Why hybrid?** Each database type solves a different problem:

**Relational Database (PostgreSQL) — for structured, queryable data:**

```
Tables:
├── suppliers
│   ├── id, name, contact_email, phone, region, rating
│   └── specialization, certifications, payment_terms
├── products
│   ├── id, name, category, description, specifications
│   └── hs_code, unit_of_measure
├── supplier_products (junction)
│   ├── supplier_id, product_id
│   ├── unit_price, moq, bulk_pricing_json
│   └── lead_time_days, last_quoted_date
├── clients
│   ├── id, name, contact_email, company, region
│   └── preferred_suppliers, order_history_count
├── quotations
│   ├── id, client_id, supplier_id, product_id
│   ├── quoted_price, quantity, shipping_cost
│   ├── status (draft/sent/accepted/rejected)
│   └── created_at, sent_at, valid_until
├── email_threads
│   ├── id, gmail_message_id, thread_id
│   ├── from_email, to_email, subject, body_text
│   ├── direction (inbound/outbound)
│   ├── extracted_data_json
│   └── processed_at, linked_quotation_id
└── packing_slips
    ├── id, supplier_id, product_id
    ├── packaging_details, weight, dimensions
    └── documentation_urls
```

**Vector Database (Pinecone / Pgvector / ChromaDB) — for semantic search:**

```
Collections:
├── email_embeddings
│   ├── vector (embedding of email body)
│   ├── metadata: {email_id, thread_id, direction, category}
│   └── Used for: finding similar past enquiries
├── product_embeddings
│   ├── vector (embedding of product name + description + specs)
│   ├── metadata: {product_id, category, supplier_ids}
│   └── Used for: fuzzy product matching from client descriptions
└── supplier_profile_embeddings
    ├── vector (embedding of supplier capabilities + history)
    ├── metadata: {supplier_id, specializations, region}
    └── Used for: matching suppliers to new requirements
```

**Why this split:**
- **Relational** — when you need exact lookups: "Give me the last 3 quotes from Supplier X for Product Y" or "What's the cheapest price for M10 bolts?"
- **Vector** — when you need fuzzy/semantic matching: "A client asks for 'heavy-duty metal fasteners for marine use' — which suppliers have handled similar requests?"

---

### Phase 3: Live Email Processing Pipeline

**Objective:** When a new client email arrives in Gmail, automatically process it and prepare a quotation.

**Trigger:** Gmail webhook (via Google Pub/Sub) or periodic polling (Gmail API).

**Step-by-step flow:**

```
Step 1: Email Received
  └── Gmail webhook fires → Email Listener service activated

Step 2: Email Classification
  └── AI classifies: Is this a product enquiry? (vs. spam, internal, etc.)
  └── If not an enquiry → archive/skip

Step 3: Content Extraction
  └── AI agent extracts structured fields:
      - Product description (natural language)
      - Quantity needed
      - Delivery requirements
      - Special specs / certifications needed
      - Client identity (name, company, email)
      - Urgency level

Step 4: Supplier Search & Matching
  └── Vector search: Find similar past enquiries + matched suppliers
  └── SQL query: Get exact product matches + pricing history
  └── Rank suppliers by:
      - Product match relevance (semantic similarity)
      - Historical pricing competitiveness
      - Delivery capability (lead time vs. client needs)
      - Supplier rating
      - Past relationship with this client

Step 5: Supplier Outreach Draft Generation (v2SQ)
  └── Suppliers above the match-score threshold (default 0.65, all of them —
      not a fixed top-N) each get their own draft
  └── For EACH matched supplier, AI generates a draft outreach email:
      - Product / specs / quantity, summarized from the client's enquiry
      - Request for unit pricing, MOQ, shipping cost, lead time
      - Requested delivery timeline (from client's enquiry)
  └── Each field on each draft is tagged:
      - [AI-GENERATED] — auto-filled by AI, editable
      - [FROM-DATABASE] — pulled from existing supplier/product records
      - [NEEDS-REVIEW] — low confidence, requires human input
  └── One `supplier_quote_requests` row is created per supplier, status='draft'

Step 6: Present in UI
  └── Dashboard shows the enquiry as "Drafted" once all supplier drafts exist
  └── Opening it shows a stack of supplier cards, each independently editable
  └── Side panel/card context per supplier:
      - Original client email (shared, shown once)
      - This supplier's match confidence
      - This supplier's draft outreach email, editable
      - Draft/Sent state for this specific card

Step 7: Review & Send (per supplier)
  └── Operator reviews/edits each supplier's draft independently
  └── Clicks "Send" on that supplier's card
  └── System sends that one outreach email via Gmail API
  └── That supplier_quote_requests row is marked sent, sent_at set
  └── Enquiry stays "Drafted" — a small progress indicator (e.g. "2/3 sent")
      reflects how many of the fanned-out drafts have gone out
  └── Sending a final quotation back to the client is deferred — not part
      of this flow
```

---

### Phase 4: UI — Supplier Outreach Review Dashboard

**Objective:** A modern, user-friendly interface for reviewing and sending AI-drafted supplier outreach emails, one per matched supplier.

#### UI Views:

**View 1: Inbox / Email Feed ("The Desk")**
- List of incoming client emails
- Status badges: New | Processing | Drafted | Sent (statuses unchanged from v1 — only what "Drafted" and "Sent" contain has changed; see below)
- "Drafted" = all supplier drafts generated for this enquiry, none sent yet
- "Sent" badge on the card itself doesn't change automatically — see the per-card progress note under View 2
- Click to open the full processing view

**View 2: Supplier Outreach Builder (Main workspace, formerly "Quotation Builder")**

```
┌──────────────────────────────────────────────────────┐
│  ┌─────────────────────┐  ┌────────────────────────┐ │
│  │  ORIGINAL EMAIL      │  │  Progress: 1/3 sent    │ │
│  │  (shown once,        │  ├────────────────────────┤ │
│  │   shared context     │  │  Jiangsu Metal Works ★4.5│ │
│  │   for all drafts)     │  │  Match: 0.91  ✓ Sent    │ │
│  │                      │  │ ┌────────────────────┐  │ │
│  │  From: john@apex..   │  │ │ THE ACTUAL EMAIL     │  │ │
│  │  Subject: Need bolts │  │ │ To: wang.li@jmw.com  │  │ │
│  │                      │  │ │ Subject: RFQ — SS    │  │ │
│  │  "Hi, we need about  │  │ │  Bolts M10x50        │  │ │
│  │   5000 stainless     │  │ │ ────────────────────│  │ │
│  │   steel bolts M10..."│  │ │ Hi team, we're       │  │ │
│  │                      │  │ │ sourcing 5,000 SS    │  │ │
│  │                      │  │ │ bolts M10x50. Could  │  │ │
│  │                      │  │ │ you confirm pricing  │  │ │
│  │                      │  │ │ around $0.12/unit,   │  │ │
│  │                      │  │ │ MOQ, and 14-day lead │  │ │
│  │                      │  │ │ time?                │  │ │
│  │                      │  │ │ (this box = live      │  │ │
│  │                      │  │ │  preview, read-only   │  │ │
│  │                      │  │ │  once sent)           │  │ │
│  │                      │  │ └────────────────────┘  │ │
│  │                      │  │  Draft details ▾         │ │
│  │                      │  │  Quantity  [5,000]  ✎ AI│ │
│  │                      │  │  Target price [$0.12] DB│ │
│  │                      │  │  Lead time [14 days] ✎  │ │
│  │                      │  ├────────────────────────┤ │
│  │                      │  │  Dongguan Fastener ★4.1 │ │
│  │                      │  │  Match: 0.84             │ │
│  │                      │  │  ...same shape...         │ │
│  │                      │  │  [Send]                   │ │
│  │                      │  ├────────────────────────┤ │
│  │                      │  │  Thai Steel Supply ★3.8  │ │
│  │                      │  │  Match: 0.71             │ │
│  │                      │  │  ...same shape...         │ │
│  │                      │  │  [Send]                   │ │
│  └─────────────────────┘  └────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

Every supplier above the match-score threshold (default 0.65) gets a card — this is dynamic per enquiry, not a fixed count. Each card shows the **actual email** (To / Subject / Body) as the primary content, with the structured fields below it — editing a field (quantity, target price, lead time, etc.) re-substitutes that value into the body live, so what's in the box above the fields is always exactly what "Send" will fire, not a separate summary sitting next to it. The old "matched suppliers ranked list" panel is gone because the cards themselves are now the actionable content.

**View 3: Supplier Directory**
- Searchable list of all suppliers
- Product catalog per supplier
- Pricing history & rating
- Contact information

**View 4: Outreach History**
- All sent supplier outreach drafts with status tracking
- Filter by client, supplier, product, date
- Analytics: response rate, average time-to-send
- (Client quotation history/analytics deferred along with the client-quotation feature itself)

#### Field Tagging System:

| Tag | Meaning | UI Behavior |
|-----|---------|-------------|
| `AI-GENERATED` | Value inferred by AI from email | Yellow highlight, editable, shows confidence % |
| `FROM-DATABASE` | Pulled from existing supplier/product data | Blue highlight, editable, shows source record |
| `NEEDS-REVIEW` | Low confidence or missing data | Red highlight, mandatory edit before sending |
| `MANUAL` | Entered or modified by operator | Green highlight, no source tag |

**v2SQ addition:** tags live on the structured fields, not the email body — the body is a generated view of the fields, substituted into the AI-drafted template on every edit. This is what makes the "email preview" above the fields always trustworthy: there is exactly one source of truth (the fields), and the body is never a stale second copy of them.

---

## 4. Technology Stack (Recommended)

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Frontend** | React / Next.js + Tailwind CSS | Modern, component-driven UI |
| **Backend** | Python (FastAPI) or Node.js (Express) | API server, email processing |
| **AI / LLM** | Claude API (Anthropic) | Email parsing, classification, quotation drafting |
| **Relational DB** | PostgreSQL | Structured data, transactions, reporting |
| **Vector DB** | Pgvector (PostgreSQL ext.) or ChromaDB | Semantic search, embedding storage |
| **Email Integration** | Gmail API + Google Pub/Sub | Real-time email ingestion & sending |
| **Embeddings** | Voyage / OpenAI Embeddings | Convert text to vectors for similarity search |
| **Task Queue** | Celery / Bull | Async email processing pipeline |

---

## 5. Workflow Summary (End-to-End)

```
CLIENT sends enquiry email
       │
       ▼
GMAIL receives email → Webhook fires
       │
       ▼
EMAIL LISTENER picks up new message
       │
       ▼
AI AGENT classifies (enquiry? yes/no)
       │
       ▼
AI AGENT extracts: product, qty, specs, client info
       │
       ▼
MATCHING ENGINE queries:
  ├── Vector DB → semantic similarity (similar past deals)
  └── SQL DB → exact matches (supplier + product + pricing)
       │
       ▼
AI AGENT drafts ONE outreach email PER matched supplier
(all suppliers above match-score threshold, default 0.65)
       │
       ▼
supplier_quote_requests rows created, status='draft' (one per supplier)
       │
       ▼
UI DASHBOARD presents:
  ├── Original email (left panel, shared)
  ├── Stack of supplier draft cards (right panel, each editable)
  └── Confidence/tag indicators per field, per card
       │
       ▼
OPERATOR reviews & edits each supplier's draft independently
       │
       ▼
OPERATOR clicks "Send" on a given supplier's card
       │
       ▼
SYSTEM sends that outreach email to THAT SUPPLIER via Gmail
       │
       ▼
That supplier_quote_requests row → status='sent', sent_at set
Enquiry stays "Drafted"; UI shows progress (e.g. "2/3 sent")
       │
       ▼
(Client quotation drafting/sending is deferred — not part of this flow)
```

---

## 6. Data Flow Diagram

```
┌──────────┐     ┌──────────────┐     ┌──────────────┐
│  Gmail   │────▶│ Email Parser │────▶│  AI Agent    │
│  Inbox   │     │  (Listener)  │     │  (LLM)       │
└──────────┘     └──────────────┘     └──────┬───────┘
                                             │
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                     ┌──────────┐    ┌──────────┐   ┌────────────────┐
                     │ Vector   │    │ Relational│   │ Supplier Outreach│
                     │ DB       │    │ DB (SQL)  │   │ Drafter (AI)    │
                     │(Semantic)│    │(Structured)│  │ — 1 draft per    │
                     └──────────┘    └──────────┘   │ matched supplier │
                                                     └────────┬────────┘
                                                              │
                                                              ▼
                                                     ┌──────────────────┐
                                                     │  Review UI        │
                                                     │  (stacked cards,  │
                                                     │  one per supplier)│
                                                     └────────┬─────────┘
                                                              │
                                                              ▼
                                                     ┌──────────────────┐
                                                     │ Gmail Send        │
                                                     │ (per-supplier,    │
                                                     │  sent independently)│
                                                     └──────────────────┘
```
*(Sending a quotation back to the client is deferred — no longer part of this diagram.)*

---

## 7. Key Decisions Required

| # | Decision | Options | Recommendation |
|---|----------|---------|----------------|
| 1 | Vector DB choice | Pgvector (in PostgreSQL) vs. Pinecone vs. ChromaDB | **Pgvector** — keeps it in one DB, simpler for POC |
| 2 | Email trigger | Gmail Webhook (Pub/Sub) vs. Polling | **Pub/Sub webhook** — real-time, scalable |
| 3 | LLM provider | Claude vs. GPT vs. local model | **Claude** — strong at structured extraction |
| 4 | Frontend framework | React vs. Vue vs. Angular | **React + Next.js** — ecosystem, speed |
| 5 | Dummy email count | 100 vs. 200+ | **Start with 100**, expand if accuracy needs more data |
| 6 | Margin calculation | Fixed % vs. per-product vs. per-client | **Deferred (v2SQ)** — margin only applies to a client-facing quotation, which isn't drafted in this version. Supplier outreach asks for raw pricing, no margin applied. |

---

## 8. Implementation Phases

### Sprint 1 (Week 1-2): Foundation
- Generate 100+ dummy email threads using LLM
- Set up PostgreSQL + Pgvector
- Design and create database schema
- Build email parsing pipeline

### Sprint 2 (Week 3-4): Intelligence
- Implement AI extraction pipeline (LLM-based)
- Build embedding generation for emails and products
- Implement supplier matching engine (vector + SQL hybrid)
- Build quotation generation logic with field tagging

### Sprint 3 (Week 5-6): Integration & UI
- Gmail API integration (receive + send)
- Build React dashboard (inbox, quotation builder, supplier directory)
- Implement editable field system with AI/DB tags
- End-to-end testing with dummy data

### Sprint 4 (Week 7-8): Polish & Deploy
- Quotation history and analytics
- Error handling and edge cases
- Performance optimization
- UAT and deployment

---

## 9. Open Questions

1. What product categories should the dummy emails cover?
2. ~~What is the intermediary margin model~~ — deferred, no margin applied in v2SQ (see §7 item 6).
3. ~~Should suppliers be notified automatically, or only after operator approval?~~ — resolved: **operator approval required**, per-supplier "Send" button, no auto-send.
4. Are there specific document formats required for packing slips / certifications?
5. Multi-currency support needed from day one?
6. Should the system ingest supplier replies (negotiation, quote responses) in a future version, and if so does that reopen the client-quotation flow? — out of scope for v2SQ, worth deciding before the next iteration.
7. **New (v2SQ):** is the match-score threshold (default 0.65) the right cutoff, or should it be tunable per product category / from the UI?

---

*This document (v2SQ) supersedes the live-pipeline sections of v1.0 for the supplier-outreach flow. Client-facing quotation sections are preserved for reference but are not implemented in this version. Please review and confirm before implementation.*
