from gmail_trigger_poc.db import table_counts
from gmail_trigger_poc.vectorstore import get_products_collection

EXPECTED = {
    "clients": 15,
    "suppliers": 18,
    "products": 26,
    "supplier_products": 26,
    "quotations": 26,
    "email_threads": 104,
    "packing_slips": 26,
}


def test_seed_row_counts(fresh_store, fresh_conn):
    counts = table_counts(fresh_conn)
    for table, n in EXPECTED.items():
        assert counts[table] == n, f"{table}: got {counts[table]}, want {n}"


def test_seed_products_all_embedded(fresh_store, fresh_conn):
    pending = fresh_conn.execute(
        "SELECT COUNT(*) AS c FROM products WHERE embedding_status != 'embedded'"
    ).fetchone()["c"]
    assert pending == 0
    assert get_products_collection(fresh_store).count() == 26


def test_seed_is_rerunnable_without_duplicates(fresh_store):
    from gmail_trigger_poc import seed

    seed.run(fresh_store, reset=False)  # INSERT OR IGNORE path
    from gmail_trigger_poc.db import connect

    c = connect(fresh_store)
    try:
        assert c.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"] == 26
        assert c.execute("SELECT COUNT(*) AS c FROM email_threads").fetchone()["c"] == 104
    finally:
        c.close()


def test_load_demo_overlays_mixed_lifecycle_rows(fresh_store):
    from gmail_trigger_poc import seed
    from gmail_trigger_poc.db import connect

    first = seed.load_demo(fresh_store)
    seed.load_demo(fresh_store)  # re-runnable (INSERT OR IGNORE)

    c = connect(fresh_store)
    try:
        statuses = dict(
            c.execute(
                "SELECT status, COUNT(*) FROM email_threads WHERE id >= 9000 GROUP BY status"
            ).fetchall()
        )
        assert statuses == {"new": 2, "processing": 2, "drafted": 2}
        assert first["email_threads"] == 6

        # Solaris (#9006) — every supplier draft sent -> the Desk's "fully sent" bucket
        drafts = c.execute(
            "SELECT status FROM supplier_quote_requests WHERE email_thread_id = 9006"
        ).fetchall()
        assert [d["status"] for d in drafts] == ["sent", "sent"]

        body = c.execute(
            "SELECT draft_body FROM supplier_quote_requests WHERE id = 9001"
        ).fetchone()["draft_body"]
        assert "{{" not in body and "800 m" in body  # template rendered on load
    finally:
        c.close()
