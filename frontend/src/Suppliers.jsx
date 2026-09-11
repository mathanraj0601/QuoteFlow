import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api } from "./api.js";
import { fadeUp } from "./motion.js";

const COLS = ["Name", "Specialisation", "Region", "Rating", "Contact", "Payment terms"];

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

export default function Suppliers() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.suppliers().then(setData).catch((e) => setErr(e.message));
  }, []);

  if (err)
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
                {COLS.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
              {data.suppliers.map((s, i) => (
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
