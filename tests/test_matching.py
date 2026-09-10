from gmail_trigger_poc.matching import (
    find_matches,
    match_products,
    rerank,
    sql_join_suppliers_for_products,
)


def test_match_products_ranks_exact_product_first(seeded_settings):
    pairs = match_products("Stainless Steel Bolts M10x50 grade A2 DIN 933", seeded_settings)
    assert pairs, "no vector hits"
    assert pairs[0][0] == 1  # product 1 = Stainless Steel Bolts M10x50
    # distances are ascending (closest first)
    assert pairs == sorted(pairs, key=lambda p: p[1])


def test_sql_join_returns_priced_supplier_rows(conn):
    rows = sql_join_suppliers_for_products([1], conn)
    assert rows and all(r["product_id"] == 1 for r in rows)
    r = rows[0]
    assert r["supplier_id"] == 1 and r["unit_price"] is not None
    assert r["match_source"] == "catalog"


def test_rerank_scores_bounded_and_sorted():
    candidates = [
        {"supplier_id": 1, "product_id": 1, "supplier_rating": 4.5,
         "supplier_specialization": "Industrial Fasteners", "product_category": "Industrial Fasteners"},
        {"supplier_id": 2, "product_id": 2, "supplier_rating": 3.0,
         "supplier_specialization": "Textiles", "product_category": "Textiles"},
    ]
    ranked = rerank(candidates, {1: 0.2, 2: 0.9}, hit_categories={"Industrial Fasteners"})
    assert [c["supplier_id"] for c in ranked] == [1, 2]
    for c in ranked:
        assert 0.0 <= c["match_score"] <= 1.0
        assert 0.0 <= c["semantic_similarity"] <= 1.0


def test_rerank_dedupes_to_best_row_per_supplier():
    candidates = [
        {"supplier_id": 5, "product_id": 5, "supplier_rating": 4.7,
         "supplier_specialization": "Electronics Components", "product_category": "Electronics Components"},
        {"supplier_id": 5, "product_id": 6, "supplier_rating": 4.7,
         "supplier_specialization": "Electronics Components", "product_category": "Electronics Components"},
    ]
    ranked = rerank(candidates, {5: 0.1, 6: 0.8}, hit_categories={"Electronics Components"})
    assert len(ranked) == 1 and ranked[0]["product_id"] == 5


def test_find_matches_fans_out_to_same_category_suppliers(conn, seeded_settings):
    ranked = find_matches(
        "Need 5000 stainless steel hex bolts M10x50, DIN 933", conn, seeded_settings
    )
    kept = [c for c in ranked if c["match_score"] >= seeded_settings.match_score_threshold]
    assert len(kept) >= 2
    # every kept supplier specialises in fasteners
    assert all(c["supplier_specialization"] == "Industrial Fasteners" for c in kept)


def test_find_matches_rejects_off_catalog_text(conn, seeded_settings):
    ranked = find_matches("Out of office until Monday, contact my colleague", conn, seeded_settings)
    kept = [c for c in ranked if c["match_score"] >= seeded_settings.match_score_threshold]
    assert kept == []
