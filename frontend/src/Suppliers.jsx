import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { api } from "./api.js";
import { fadeUp } from "./motion.js";

const COLS = [
  { key: "name", label: "Name" },
  { key: "specialization", label: "Specialisation" },
  { key: "region", label: "Region" },
  { key: "rating", label: "Rating" },
  { key: "contact_email", label: "Contact" },
  { key: "payment_terms", label: "Payment terms" },
];

function Stars({ rating }) {
  const r = Math.round(rating || 0);
  return (
    <span className="stars" title={`${rating} / 5`}>
      {"★".repeat(Math.max(0, Math.min(5, r)))}
      {"☆".repeat(Math.max(0, 5 - r))} {rating}
    </span>
  );
}

function SuppliersSkeleton() {
  return (
    <div className="skel-list">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div className="skel-row" key={i}>
          <div className="skel w-45" />
          <div className="skel w-70" />
        </div>
      ))}
    </div>
  );
}

// Module-level, not state: survives Suppliers unmounting when you switch tabs
// (the Desk/Suppliers views are conditionally mounted, so this component is
// torn down and rebuilt every time). Without it, every single visit after the
// first re-shows the skeleton and re-fetches from scratch, which is most of
// what "taking a while to load" actually was — the real request only takes a
// few ms. A background refetch still runs on every mount to stay current.
let cachedSuppliers = null;

export default function Suppliers() {
  const [data, setData] = useState(cachedSuppliers);
  const [err, setErr] = useState(null);
  // Click a column header to sort by it; click again to flip direction.
  const [sort, setSort] = useState({ key: "name", dir: "asc" });

  useEffect(() => {
    api
      .suppliers()
      .then((d) => {
        cachedSuppliers = d;
        setData(d);
        setErr(null);
      })
      .catch((e) => setErr(e.message));
  }, []);

  const sorted = useMemo(() => {
    if (!data) return [];
    const { key, dir } = sort;
    const mul = dir === "asc" ? 1 : -1;
    return [...data.suppliers].sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * mul;
      return String(av ?? "").localeCompare(String(bv ?? "")) * mul;
    });
  }, [data, sort]);

  const toggleSort = (key) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));

  // Only a fatal, panel-replacing error if we have nothing cached to fall
  // back on — a background refresh failing shouldn't blank out a table the
  // operator was already looking at.
  if (err && !data)
    return (
      <div className="panel">
        <div className="empty">{err}</div>
      </div>
    );

  return (
    <div className="panel">
      <div className="phead">
        <h2>Supplier directory</h2>
        {data && <span className="chip">{data.count} suppliers</span>}
      </div>
      {!data ? (
        <SuppliersSkeleton />
      ) : (
        <div className="tbl-wrap">
          <table>
            <tbody>
              <tr>
                {COLS.map((c) => {
                  const active = sort.key === c.key;
                  return (
                    <th
                      key={c.key}
                      className={"sortable" + (active ? " sort-active" : "")}
                      onClick={() => toggleSort(c.key)}
                      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                    >
                      {c.label}
                      <span className="sort-arrow">{active ? (sort.dir === "asc" ? "▲" : "▼") : ""}</span>
                    </th>
                  );
                })}
              </tr>
              {sorted.map((s, i) => (
                <motion.tr
                  key={s.id}
                  variants={fadeUp}
                  initial="hidden"
                  animate="show"
                  custom={i}
                  transition={{ delay: Math.min(i * 0.03, 0.3) }}
                >
                  <td data-label="Name">
                    <b>{s.name}</b>
                  </td>
                  <td data-label="Specialisation">{s.specialization}</td>
                  <td data-label="Region">{s.region}</td>
                  <td data-label="Rating">
                    <Stars rating={s.rating} />
                  </td>
                  <td data-label="Contact">{s.contact_email}</td>
                  <td data-label="Payment terms">{s.payment_terms}</td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
