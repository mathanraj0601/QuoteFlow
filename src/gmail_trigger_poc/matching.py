"""Supplier matching: Chroma product search -> SQL join -> rerank to a 0..1 score.

Follows Plan B §5 (chroma.query -> sql_join_suppliers_for_products -> rerank), with
one addition the seed data forces: the seed catalog has exactly one supplier per
product, so an exact-catalog join alone can't "fan out" to N suppliers. The
schema calls `suppliers.specialization` the field that "feeds ranking step", so
after the vector hit we also pull same-category suppliers (scored a notch lower,
with no priced row) to give the enquiry its N candidates. The pipeline then keeps
every candidate with match_score >= threshold (default 0.65).
"""

from __future__ import annotations

import logging
import sqlite3

from gmail_trigger_poc.config import Settings, get_settings
from gmail_trigger_poc.vectorstore import Embedder, get_embedder, get_products_collection

logger = logging.getLogger(__name__)

# match_score = SEMANTIC_WEIGHT*similarity + RATING_WEIGHT*(rating/5) + CATEGORY_WEIGHT*category_alignment
SEMANTIC_WEIGHT = 0.60
RATING_WEIGHT = 0.15
CATEGORY_WEIGHT = 0.25
# same category but no priced supplier_products row -> small penalty on the final score.
NO_CATALOG_PENALTY = 0.90
# a product hit only defines a "matched category" if its similarity is within this
# fraction of the best hit's similarity — keeps a broad top-N query from marking
# half the catalog's categories as relevant.
CATEGORY_HIT_FRAC = 0.6


def match_products(
    query_text: str,
    settings: Settings | None = None,
    *,
    collection=None,
    embedder: Embedder | None = None,
    n: int | None = None,
) -> list[tuple[int, float]]:
    """Return [(product_id, cosine_distance)] for the top-N products by vector similarity."""
    settings = settings or get_settings()
    collection = collection if collection is not None else get_products_collection(settings)
    embedder = embedder or get_embedder(settings)
    n = n or settings.match_top_n

    res = collection.query(query_embeddings=embedder.embed([query_text or ""]), n_results=n)
    ids = [int(i) for i in res["ids"][0]]
    dists = res.get("distances") or [[0.0] * len(ids)]
    pairs = list(zip(ids, [float(d) for d in dists[0]]))
    logger.debug("match_products(%r) -> %s", query_text, pairs)
    return pairs


def sql_join_suppliers_for_products(
    product_ids: list[int], conn: sqlite3.Connection
) -> list[dict]:
    """Every supplier with a priced `supplier_products` row for any of `product_ids`."""
    if not product_ids:
        return []
    marks = ", ".join("?" for _ in product_ids)
    rows = conn.execute(
        f"""
        SELECT
            sp.product_id           AS product_id,
            p.name                  AS product_name,
            p.category              AS product_category,
            p.specifications        AS product_specifications,
            p.unit_of_measure       AS unit_of_measure,
            s.id                    AS supplier_id,
            s.name                  AS supplier_name,
            s.contact_email         AS supplier_email,
            s.rating                AS supplier_rating,
            s.specialization        AS supplier_specialization,
            s.certifications        AS supplier_certifications,
            s.payment_terms         AS supplier_payment_terms,
            sp.unit_price           AS unit_price,
            sp.moq                  AS moq,
            sp.bulk_pricing_json    AS bulk_pricing_json,
            sp.lead_time_days       AS lead_time_days
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        JOIN products  p ON p.id = sp.product_id
        WHERE sp.product_id IN ({marks})
        """,
        product_ids,
    ).fetchall()
    return [dict(r) | {"match_source": "catalog"} for r in rows]


def _sql_suppliers_for_categories(
    categories: list[str],
    conn: sqlite3.Connection,
    *,
    exclude_supplier_ids: set[int],
    product_by_category: dict[str, dict],
) -> list[dict]:
    """Same-specialization suppliers with no priced row for the matched product."""
    if not categories:
        return []
    marks = ", ".join("?" for _ in categories)
    rows = conn.execute(
        f"""
        SELECT id AS supplier_id, name AS supplier_name, contact_email AS supplier_email,
               rating AS supplier_rating, specialization AS supplier_specialization,
               certifications AS supplier_certifications, payment_terms AS supplier_payment_terms
        FROM suppliers
        WHERE specialization IN ({marks})
        """,
        categories,
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        if r["supplier_id"] in exclude_supplier_ids:
            continue
        prod = product_by_category.get(r["supplier_specialization"], {})
        out.append(
            {
                **dict(r),
                "product_id": prod.get("product_id"),
                "product_name": prod.get("product_name"),
                "product_category": r["supplier_specialization"],
                "product_specifications": prod.get("product_specifications"),
                "unit_of_measure": prod.get("unit_of_measure"),
                "unit_price": None,
                "moq": None,
                "bulk_pricing_json": None,
                "lead_time_days": None,
                "match_source": "specialization",
            }
        )
    return out


def rerank(
    candidates: list[dict],
    distance_by_product: dict[int, float],
    *,
    hit_categories: set[str] | None = None,
) -> list[dict]:
    """Attach `semantic_similarity` + `match_score` (0..1); dedupe to best row per supplier.

    semantic_similarity = clamp(1 - cosine_distance, 0, 1)   (per matched product)
    category_alignment  = 1.0 if supplier's category is among the vector hits, else similarity
    match_score = 0.60*similarity + 0.15*(rating/5) + 0.25*category_alignment
                  (x0.93 when the supplier has no priced row for the product)
    """
    hit_categories = hit_categories or set()
    best_sim = max(
        (max(0.0, min(1.0, 1.0 - d)) for d in distance_by_product.values()), default=0.0
    )
    scored: list[dict] = []
    for c in candidates:
        dist = distance_by_product.get(c.get("product_id"), None)
        sim = max(0.0, min(1.0, 1.0 - dist)) if dist is not None else best_sim
        rating = (c.get("supplier_rating") or 0.0) / 5.0
        category_aligned = (c.get("supplier_specialization") in hit_categories) or (
            c.get("product_category") in hit_categories
        )
        cat_score = 1.0 if category_aligned else sim
        score = (
            SEMANTIC_WEIGHT * sim
            + RATING_WEIGHT * rating
            + CATEGORY_WEIGHT * cat_score
        )
        if c.get("match_source") == "specialization":
            score *= NO_CATALOG_PENALTY
        scored.append(
            {
                **c,
                "semantic_similarity": round(sim, 4),
                "match_score": round(min(1.0, score), 4),
            }
        )

    best_per_supplier: dict[int, dict] = {}
    for c in scored:
        cur = best_per_supplier.get(c["supplier_id"])
        if cur is None or c["match_score"] > cur["match_score"]:
            best_per_supplier[c["supplier_id"]] = c
    ranked = sorted(
        best_per_supplier.values(), key=lambda c: c["match_score"], reverse=True
    )
    return ranked


def find_matches(
    query_text: str,
    conn: sqlite3.Connection,
    settings: Settings | None = None,
    *,
    collection=None,
    embedder: Embedder | None = None,
) -> list[dict]:
    """Full match path: vector search -> catalog join (+ same-category suppliers) -> rerank."""
    settings = settings or get_settings()
    pairs = match_products(query_text, settings, collection=collection, embedder=embedder)
    distance_by_product = dict(pairs)
    product_ids = [pid for pid, _ in pairs]

    catalog = sql_join_suppliers_for_products(product_ids, conn)

    # A vector hit only defines a "matched category" if it's close to the best hit
    # (CATEGORY_HIT_FRAC) — otherwise a broad top-N query tags nearly every category.
    best_sim = max(
        (max(0.0, min(1.0, 1.0 - d)) for _, d in pairs), default=0.0
    )
    hit_categories: set[str] = set()
    product_by_category: dict[str, dict] = {}
    if product_ids:
        marks = ", ".join("?" for _ in product_ids)
        for r in conn.execute(
            f"SELECT id AS product_id, name AS product_name, category, specifications, "
            f"unit_of_measure FROM products WHERE id IN ({marks})",
            product_ids,
        ):
            sim = max(0.0, min(1.0, 1.0 - distance_by_product.get(r["product_id"], 1.0)))
            if best_sim and sim < CATEGORY_HIT_FRAC * best_sim:
                continue
            hit_categories.add(r["category"])
            product_by_category.setdefault(
                r["category"],
                {
                    "product_id": r["product_id"],
                    "product_name": r["product_name"],
                    "product_specifications": r["specifications"],
                    "unit_of_measure": r["unit_of_measure"],
                },
            )

    extra = _sql_suppliers_for_categories(
        sorted(hit_categories),
        conn,
        exclude_supplier_ids={c["supplier_id"] for c in catalog},
        product_by_category=product_by_category,
    )
    ranked = rerank(catalog + extra, distance_by_product, hit_categories=hit_categories)
    logger.info(
        "find_matches(%r): %d product hit(s) in %d categor(y/ies) -> %d supplier candidate(s); "
        "top score=%.3f",
        query_text, len(pairs), len(hit_categories), len(ranked),
        ranked[0]["match_score"] if ranked else 0.0,
    )
    return ranked
