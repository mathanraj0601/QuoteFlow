import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import SupplierCard from "./SupplierCard.jsx";

export default function EnquiryDetail({ threadId, onClose, onActivity, toast }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    try {
      setData(await api.supplierDrafts(threadId));
      setErr(null);
    } catch (e) {
      setErr(e.message);
    }
  }, [threadId]);

  useEffect(() => {
    setData(null);
    load();
  }, [load]);

  if (err) return <div className="empty">{err}</div>;
  if (!data) return <div className="empty">loading…</div>;

  const oe = data.original_email || {};
  const ex = oe.extracted || {};

  return (
    <>
      <div className="phead">
        <h2>Enquiry #{data.email_thread_id}</h2>
        <button className="btn ghost" onClick={onClose}>
          close
        </button>
      </div>

      <div className="email-card">
        <div className="lbl">Original client email</div>
        <div className="kv" style={{ marginTop: 6 }}>
          <b>From</b> {oe.from}
        </div>
        <div className="kv">
          <b>Subject</b> {oe.subject}
        </div>
        {ex.scenario && (
          <div className="kv">
            <b>Extracted</b> {ex.product_description} · qty {String(ex.quantity)}{" "}
            {ex.unit_of_measure || ""} · {ex.delivery_terms || "—"} · urgency {ex.urgency}
          </div>
        )}
        <div className="txt">{oe.body}</div>
      </div>

      <div className="progress">
        <span>Supplier outreach</span>
        <b>
          {data.progress.sent} / {data.progress.total} sent
        </b>
      </div>

      {data.supplier_drafts.length === 0 && (
        <div className="empty">No supplier cleared the match threshold for this enquiry.</div>
      )}
      {data.supplier_drafts.map((d) => (
        <SupplierCard
          key={d.id}
          draft={d}
          onChanged={() => {
            load();
            onActivity?.();
          }}
          toast={toast}
        />
      ))}
    </>
  );
}
