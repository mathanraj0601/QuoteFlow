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
