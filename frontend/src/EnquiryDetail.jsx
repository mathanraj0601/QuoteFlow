import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import SupplierCard from "./SupplierCard.jsx";

const STEPS_DONE = (trace) => trace.filter((n) => n.done).length;

// The processing trace from GET /enquiry/{id}/supplier-drafts — shown while the
// enquiry is still New / Processing (mirrors the prototype's "procView").
function ProcessingTrace({ status, stage, trace, draftProgress }) {
  const done = STEPS_DONE(trace);
  const pct = Math.round((done / trace.length) * 100);
  const dp = draftProgress || { done: 0, total: 0 };
  const drafting = (stage === "drafting" || stage === "matched") && dp.total > 0;
  return (
    <div className="proc">
      <div className="proc-hero">
        <div
          className="ring"
          style={{
            background: `conic-gradient(var(--brass) ${pct * 3.6}deg, var(--line) 0)`,
          }}
        >
          <div className="hole">
            <b>{pct}%</b>
            <span>{done} of {trace.length}</span>
          </div>
        </div>
        <div>
          <h3>{status === "new" ? "Queued" : "Processing"}</h3>
          <p>
            {drafting
              ? `Drafting supplier ${Math.min(dp.done + 1, dp.total)} of ${dp.total}…`
              : stage
                ? `Stage: ${stage}`
                : "Waiting to start"}
          </p>
        </div>
      </div>

      {drafting && (
        <div className="draftbar">
          <div className="draftbar-track">
            <div
              className="draftbar-fill"
              style={{ width: `${Math.round((dp.done / dp.total) * 100)}%` }}
            />
          </div>
          <span>
            {dp.done} / {dp.total} supplier draft(s) done
          </span>
        </div>
      )}

      <div className="trace">
        {trace.map((n) => (
          <div className={"trow" + (n.done ? "" : " pending")} key={n.key}>
            <div className={"node" + (n.done ? " done" : "")} />
            <div>
              <h4>{n.label}</h4>
              <p>{n.detail || "Pending"}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function EnquiryDetail({ threadId, live, onClose, onActivity, toast }) {
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

  // Hard reset when the selected enquiry changes...
  useEffect(() => {
    setData(null);
    load();
  }, [threadId, load]);

  // ...soft refresh (no "loading…" flash) on every live progress tick.
  useEffect(() => {
    if (live) load();
  }, [live, load]);

  if (err) return <div className="empty">{err}</div>;
  if (!data) return <div className="empty">loading…</div>;

  const oe = data.original_email || {};
  const ex = oe.extracted || {};
  const inProgress = data.status === "new" || data.status === "processing";

  return (
    <>
      <div className="phead">
        <h2>
          Enquiry #{data.email_thread_id}
          {data.status && <span className={"badge " + data.status}>{data.status}</span>}
        </h2>
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

      {inProgress && data.trace ? (
        <ProcessingTrace
          status={data.status}
          stage={data.pipeline_stage}
          trace={data.trace}
          draftProgress={data.draft_progress}
        />
      ) : (
        <>
          <div className="progress">
            <span>Supplier outreach</span>
            <b>
              {data.progress.sent} / {data.progress.total} sent
            </b>
          </div>

          {data.supplier_drafts.length === 0 && (
            <div className="empty">
              No supplier cleared the match threshold for this enquiry.
            </div>
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
      )}
    </>
  );
}
