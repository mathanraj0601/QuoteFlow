"""One-time backfill: load the seed dataset straight into SQLite + embed products.

This is Plan B's "no extraction for backfill" path — the rows in
`data/quoteflow_seed.json` are already clean and correctly shaped, so they are
inserted directly (no LLM). Only the live `/process-email` pipeline runs real
classification/extraction.

    python -m gmail_trigger_poc.seed            # reset DB + chroma, load, embed
    python -m gmail_trigger_poc.seed --keep     # load into existing store (INSERT OR IGNORE)
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path

from gmail_trigger_poc.config import Settings, get_settings
from gmail_trigger_poc.db import connect, init_db, table_counts
from gmail_trigger_poc.logging_setup import configure_logging
from gmail_trigger_poc.vectorstore import (
    Embedder,
    get_embedder,
    get_products_collection,
)

logger = logging.getLogger(__name__)

# (table, columns) — column order matches the INSERT tuple built in _load_table.
# Verified against schema.sql; every JSON field is carried to its column.
_TABLES: list[tuple[str, list[str]]] = [
    ("clients", ["id", "name", "company", "contact_email", "region", "order_history_count"]),
    ("suppliers", ["id", "name", "contact_email", "phone", "region", "rating",
                   "specialization", "certifications", "payment_terms"]),
    ("products", ["id", "name", "category", "description", "specifications",
                  "hs_code", "unit_of_measure"]),
    ("supplier_products", ["id", "supplier_id", "product_id", "unit_price", "moq",
                           "bulk_pricing_json", "lead_time_days", "last_quoted_date"]),
    ("quotations", ["id", "client_id", "supplier_id", "product_id", "quoted_price",
                    "quantity", "shipping_cost", "margin_pct", "status", "match_score",
                    "created_at", "sent_at", "valid_until"]),
    ("email_threads", ["id", "gmail_message_id", "thread_id", "from_email", "to_email",
                       "subject", "body_text", "direction", "extracted_data_json",
                       "processed_at", "linked_quotation_id"]),
    ("packing_slips", ["id", "supplier_id", "product_id", "packaging_details", "weight",
                       "dimensions", "documentation_urls"]),
]


def _load_table(conn, table: str, columns: list[str], rows: list[dict]) -> int:
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(r.get(c) for c in columns) for r in rows])
    return len(rows)


def seed_direct(conn, data: dict) -> dict[str, int]:
    """Straight inserts for every table in the seed JSON. Returns rows seen per table."""
    seen: dict[str, int] = {}
    for table, columns in _TABLES:
        seen[table] = _load_table(conn, table, columns, data.get(table, []))
    conn.commit()
    logger.info("seed_direct: %s", ", ".join(f"{k}={v}" for k, v in seen.items()))
    return seen


def embed_products(conn, collection, embedder: Embedder) -> int:
    """Embed `name + specifications` for every product into the Chroma collection."""
    products = conn.execute(
        "SELECT id, name, category, specifications FROM products ORDER BY id"
    ).fetchall()
    if not products:
        logger.warning("embed_products: no products to embed")
        return 0
    ids = [str(p["id"]) for p in products]
    documents = [f"{p['name']} {p['specifications'] or ''}".strip() for p in products]
    metadatas = [{"category": p["category"]} for p in products]
    collection.upsert(
        ids=ids,
        embeddings=embedder.embed(documents),
        documents=documents,
        metadatas=metadatas,
    )
    conn.execute("UPDATE products SET embedding_status = 'embedded'")
    conn.commit()
    logger.info(
        "embed_products: %d product(s) -> collection %r (embedder=%s)",
        len(ids), collection.name, embedder.name,
    )
    return len(ids)


def run(settings: Settings | None = None, *, reset: bool = True) -> dict[str, int]:
    settings = settings or get_settings()
    seed_path = Path(settings.seed_file)
    if not seed_path.exists():
        raise FileNotFoundError(f"seed file not found: {seed_path} (set QUOTEFLOW_SEED_FILE)")

    if reset:
        db_path = Path(settings.db_file)
        if db_path.exists():
            db_path.unlink()
            logger.info("reset: removed %s", db_path)
        chroma_dir = Path(settings.chroma_path)
        if chroma_dir.exists():
            shutil.rmtree(chroma_dir)
            logger.info("reset: removed %s/", chroma_dir)

    init_db(settings)
    data = json.loads(seed_path.read_text(encoding="utf-8"))

    conn = connect(settings)
    try:
        seed_direct(conn, data)
        embedder = get_embedder(settings)
        collection = get_products_collection(settings)
        embed_products(conn, collection, embedder)
        counts = table_counts(conn)
        counts["_chroma_products"] = collection.count()
    finally:
        conn.close()

    logger.info("seed complete: %s", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return counts


def load_demo(settings: Settings | None = None, conn=None) -> dict[str, int]:
    """Overlay the demo enquiries (`settings.demo_file`) onto an already-seeded DB.

    Six client enquiries frozen at different v2SQ lifecycle stages (new /
    processing / drafted / all-sent) so a walkthrough can show every Desk badge
    at once. IDs are in a 9000+ block; `INSERT OR IGNORE` makes this re-runnable.
    Needs the main seed loaded first (it references seed client/supplier/product ids).
    """
    from gmail_trigger_poc.llm import render_body

    settings = settings or get_settings()
    demo_path = Path(settings.demo_file)
    if not demo_path.exists():
        raise FileNotFoundError(f"demo file not found: {demo_path} (set QUOTEFLOW_DEMO_FILE)")
    data = json.loads(demo_path.read_text(encoding="utf-8"))
    template = data["body_template"]

    own = conn is None
    conn = conn or connect(settings)
    seen = {"email_threads": 0, "supplier_quote_requests": 0}
    try:
        for enq in data["enquiries"]:
            extracted = enq.get("extracted")
            conn.execute(
                """
                INSERT OR IGNORE INTO email_threads
                    (id, gmail_message_id, thread_id, from_email, to_email, subject,
                     body_text, direction, extracted_data_json, processed_at,
                     status, pipeline_stage, pipeline_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'inbound', ?, ?, ?, ?, ?)
                """,
                (
                    enq["id"], enq["gmail_message_id"], enq["id"],
                    enq["from_email"], settings.broker_email, enq["subject"],
                    enq["body_text"],
                    json.dumps(extracted) if extracted is not None else None,
                    enq["processed_at"], enq["status"], enq["pipeline_stage"],
                    enq["processed_at"],
                ),
            )
            seen["email_threads"] += 1
            for d in enq.get("supplier_drafts", []):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO supplier_quote_requests
                        (id, email_thread_id, client_id, supplier_id, product_id, quantity,
                         specs_summary, match_score, draft_to, draft_subject, draft_body,
                         draft_body_template, draft_fields_json, draft_edit_mode,
                         status, gmail_message_id, created_at, sent_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'fields', ?, NULL, ?, ?)
                    """,
                    (
                        d["id"], enq["id"], enq["client_id"], d["supplier_id"],
                        d["product_id"], d.get("quantity"), d.get("specs_summary"),
                        d.get("match_score"), d["draft_to"], d["draft_subject"],
                        render_body(template, d["fields"]),
                        template, json.dumps(d["fields"]),
                        d["status"], enq["processed_at"], d.get("sent_at"),
                    ),
                )
                seen["supplier_quote_requests"] += 1
        conn.commit()
    finally:
        if own:
            conn.close()
    logger.info("load_demo: %s", ", ".join(f"{k}={v}" for k, v in seen.items()))
    return seen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep", action="store_true",
        help="load into the existing DB/chroma instead of resetting them first",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="also overlay data/demo_enquiries.json (enquiries at mixed lifecycle stages)",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    counts = run(settings, reset=not args.keep)
    if args.demo:
        demo_counts = load_demo(settings)
        counts.update({f"_demo_{k}": v for k, v in demo_counts.items()})
    print("Seeded and embedded - ready for /process-email")
    for k, v in counts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
