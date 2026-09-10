# QuoteFlow — Minimal Version 1 (v2SQ — Supplier Quote outreach)

**Description:** This is the minimal, stripped-down version of QuoteFlow built for a 3-day proof-of-concept demo, run entirely on a local machine. It trims the full architecture down to the smallest working slice — real email trigger, mocked backfill data, local database, and a simple UI — while keeping the core AI-driven extraction and matching intact. **v2SQ change:** the output of processing a client enquiry is no longer a client-facing quotation — it's one AI-drafted outreach email per matched supplier, each reviewed and sent independently from the UI. Sending a final quotation back to the client is deferred, not built in this version. No cloud deployment or production hardening is included at this stage.

---

## 1. What changes from the full design

| Area | Full version | POC (3-day, local) |
|---|---|---|
| Backfill data (100+ emails) | Sent as real emails, ingested through Gmail | **Mocked**: generate via LLM script → parse → insert directly into DB. No real sending. |
| Live client email | Simulated in UI | **Real**: one real Gmail inbox, live trigger |
| Email trigger mechanism | Google Pub/Sub webhook | **Polling** — check inbox every 15–30s. Webhooks need a public HTTPS endpoint; polling works entirely on localhost, zero setup. |
| Relational DB | PostgreSQL | **SQLite** — zero install, single file, fine for demo data volumes |
| Vector DB | Pgvector / Pinecone / ChromaDB | **In-process**: store embeddings as JSON blobs in SQLite, do cosine similarity in Python at query time. No extra service to run. |
| AI / LLM | Claude API | **Claude API** — kept real, it's the core value prop of the demo. Calls are few (per email), so cost/latency is a non-issue. |
| Frontend | React + Next.js, full dashboard | **React (Vite)**, single-page app, 3 views only (see below) |
| Backend | FastAPI / Express, task queue | **FastAPI**, no queue — synchronous processing is fine at demo scale |
| Auth, multi-user, deployment | — | **Out of scope entirely** for POC |

**Why these swaps:** every cut removes infrastructure you'd otherwise spend a day standing up (Postgres server, pgvector extension, Pub/Sub project, Celery/Redis) with zero demo-visible payoff. SQLite + in-process vectors + polling behave identically from the user's perspective on a 100–200 row dataset.

---

## 2. Real email trigger — how polling works locally

```
Local script (runs every 20s)
   │
   ▼
Gmail API: users.messages.list (query: is:unread, label: INBOX)
   │
   ▼
New message found? ──No──▶ sleep 20s, repeat
   │ Yes
   ▼
Fetch full message body → mark as read
   │
   ▼
Push into the SAME processing pipeline used for backfill
(classify → extract → match → draft quotation)
   │
   ▼
UI polls backend every few seconds → new quotation draft appears
```

**Setup needed:** one Gmail account (can be a test account), OAuth2 credentials (`credentials.json` from Google Cloud Console, one-time setup, ~10 min), `google-api-python-client`. No domain, no webhook, no public URL required.

---

## 3. Revised architecture (local)

```
┌─────────────────┐
│  Gmail (test     │◀── Client sends real enquiry email here for the demo
│  account)        │
└────────┬─────────┘
         │ polling (Gmail API)
         ▼
┌─────────────────────────┐        ┌──────────────────────────┐
│  FastAPI backend         │───────▶│  Claude API               │
│  (single process)        │        │  - classify enquiry       │
│                           │        │  - extract fields         │
│  ┌─────────────────────┐ │        │  - draft outreach (1/supplier)│
│  │ SQLite               │ │◀───────┘
│  │ - suppliers          │ │
│  │ - products            │ │
│  │ - supplier_quote_requests (v2SQ — replaces quotations for live output) │
│  │ - email_threads       │ │
│  │ - embeddings (JSON)   │ │  (cosine similarity computed in Python)
│  └─────────────────────┘ │
└────────┬──────────────────┘
         │ REST API
         ▼
┌─────────────────────────┐
│  React (Vite) frontend   │
│  - Inbox view ("The Desk")│
│  - Supplier outreach builder│
│  - Supplier directory     │
└──────────────────────────┘
```

One backend process, one SQLite file, one frontend dev server. Everything runs with `uvicorn` + `npm run dev` on localhost.

---

## 4. Day-by-day plan

### Day 1 — Data + backend core
- Set up SQLite schema (suppliers, products, supplier_products, clients, quotations, email_threads)
- Write LLM prompt script to generate 100+ dummy email threads (varied categories, styles)
- Write extraction script: run each dummy email through Claude → parse structured fields → insert into SQLite directly (this is the "mock backfill" path — no real email involved)
- Generate embeddings for products/suppliers, store as JSON in SQLite
- **End of day 1 checkpoint:** DB populated with 100+ realistic supplier/product/quotation records

### Day 2 — Live pipeline + matching
- Gmail OAuth setup + polling script (real trigger)
- Build the shared processing pipeline: classify → extract → match (vector + SQL, all suppliers above match-score threshold) → draft ONE outreach email PER matched supplier, each with field tags (`AI-GENERATED`, `FROM-DATABASE`, `NEEDS-REVIEW`)
- Wire polling script to call this pipeline on new mail
- FastAPI endpoints: list emails, get supplier outreach drafts for an enquiry, list suppliers, update/send an individual supplier draft
- **End of day 2 checkpoint:** send a real test email → see it processed → N supplier outreach drafts appear via API, one per matched supplier (test with curl/Postman before UI exists)

### Day 3 — UI + demo polish

**UI flow (build in this order):**

1. **Task board** — new emails appear as cards the moment they're picked up (client name + subject + status: `New` / `Processing` / `Ready` / `Sent`). This is the landing screen.
2. **Click a card → processing steps view** — a live status timeline for that email, stepping through: `Extracting content` → `Matching supplier` → `Getting quotation` → `Shipping and docs`. Each step flips to "done" as the backend pipeline completes it (poll the email's status endpoint every couple seconds, or just replay the steps with a short delay if the pipeline finishes faster than the animation — either is fine for a demo).
3. **Supplier outreach view (v2SQ)** — appears once processing finishes, i.e. once a draft exists for every matched supplier above the match-score threshold (default 0.65). Original client email shown once on one side; the other side is a **stack of supplier cards**, one per matched supplier, each with its own tagged/editable fields (`AI-GENERATED` / `FROM-DATABASE` / `NEEDS-REVIEW`) and its own **"Send"** button. Sending is per-card, independent of the others — the enquiry stays "Drafted" throughout, with a small progress readout (e.g. "2/3 sent"). Real send via Gmail API is a nice-to-have if time allows on this day, otherwise simulate and just show the final email text per card. Sending a quotation back to the client is out of scope — this view's job ends at supplier outreach.
4. **Supplier directory** — simple table, lower priority than the above three.

**Scoped chat (phase 2, only after 1–3 above work):** a chat box on the quotation view where the user can ask questions about that specific email/quotation. Backend-side this just means: on each chat message, pull the original email text + the quotation record + top-matched supplier records as context, pass them to Claude along with the question. No new infrastructure needed — it reuses the same DB and embeddings already built. Build this last, and only if Day 3 time allows; it's not required for the core demo to land.

- Run full rehearsal: send a real email → watch it land on the task board → click through processing → review/edit the final quotation
- **End of day 3:** working local demo, rehearsed once

---

## 5. What to explicitly call out as "not in scope" during the demo

Being upfront about this avoids awkward questions mid-demo:
- No authentication / multi-user support
- No production database (SQLite, will migrate to Postgres+pgvector later)
- No cloud hosting — local only
- **Sending a final quotation back to the client is out of scope entirely in v2SQ** — the demo's endpoint is sending outreach to suppliers, not closing the loop back to the client
- Sending supplier outreach itself is either simulated or best-effort real, depending on time left on Day 3
- No handling of attachments (PDFs, images) in emails yet
- No multi-currency, no negotiation-thread handling, no ingestion of supplier replies
- Scoped chat (ask questions about a specific email/quotation) is a stretch goal — core demo does not depend on it

---

## 6. Minimal folder structure

```
quoteflow-poc/
├── backend/
│   ├── main.py              # FastAPI app
│   ├── db.py                 # SQLite setup + queries
│   ├── gmail_client.py       # OAuth + polling
│   ├── pipeline.py           # classify → extract → match → draft
│   ├── embeddings.py         # generate + cosine similarity
│   └── seed_dummy_data.py    # generates 100+ emails, extracts, inserts
├── frontend/
│   └── (React + Vite app: Inbox, SupplierOutreachBuilder [v2SQ], SupplierDirectory)
└── data/
    └── quoteflow.db          # SQLite file
```

---

*This plan trades scalability for speed — every swap above is reversible when moving from POC to production (SQLite → Postgres, polling → webhook, in-process vectors → pgvector).*
