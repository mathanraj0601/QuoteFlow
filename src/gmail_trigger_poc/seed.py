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
from gmail_trigger_poc.vectorstore import Embedder, get_embedder, get_products_collection

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep", action="store_true",
        help="load into the existing DB/chroma instead of resetting them first",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    counts = run(settings, reset=not args.keep)
    print("Seeded and embedded - ready for /process-email")
    for k, v in counts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
