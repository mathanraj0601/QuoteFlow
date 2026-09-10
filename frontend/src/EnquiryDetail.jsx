import { useCallback, useEffect, useState } from "react";
import { motion, useMotionValue, useSpring, useTransform } from "framer-motion";
import { api } from "./api.js";
import { fadeUp, pressable, staggerContainer, viewSwap } from "./motion.js";
import SupplierCard from "./SupplierCard.jsx";

const STEPS_DONE = (trace) => trace.filter((n) => n.done).length;

// Smoothly eased integer that chases `value` — used for the ring % readout.
function useCountUp(value) {
  const mv = useMotionValue(value);
  const s = useSpring(mv, { stiffness: 120, damping: 22, mass: 0.7 });
  useEffect(() => {
    mv.set(value);
  }, [value, mv]);
  return useTransform(s, (v) => Math.round(v));
}

// The processing trace from GET /enquiry/{id}/supplier-drafts — shown while the
// enquiry is still New / Processing (mirrors the prototype's "procView").
function ProcessingTrace({ status, stage, trace, draftProgress }) {
  const done = STEPS_DONE(trace);
  const pct = Math.round((done / trace.length) * 100);
  const pctText = useCountUp(pct);
  const dp = draftProgress || { done: 0, total: 0 };
  const drafting = (stage === "drafting" || stage === "matched") && dp.total > 0;
  return (
    <div className="proc">
      <div className="proc-hero">
        <div
          className="ring"
          style={{
            background: `conic-gradient(var(--accent) ${pct * 3.6}deg, var(--surface-3) 0)`,
          }}
        >
          <div className="hole">
            <b>
              <motion.span>{pctText}</motion.span>%
            </b>
            <span>
              {done} of {trace.length}
            </span>
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

      <motion.div
        className="trace"
        variants={staggerContainer(0.05)}
        initial="hidden"
        animate="show"
      >
        {trace.map((n) => (
          <motion.div
            className={"trow" + (n.done ? " done" : " pending")}
            key={n.key}
            variants={fadeUp}
          >
            <div className={"node" + (n.done ? " done" : "")} />
            <div>
              <h4>{n.label}</h4>
              <p>{n.detail || "Pending"}</p>
            </div>
          </motion.div>
        ))}
      </motion.div>
    </div>
  );
}

function CloseIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
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
    <motion.div variants={viewSwap} initial="hidden" animate="show">
      <div className="phead">
        <h2>
          Enquiry #{data.email_thread_id}
          {data.status && <span className={"badge " + data.status}>{data.status}</span>}
        </h2>
        <motion.button
          className="btn ghost icon"
          {...pressable}
          onClick={onClose}
          aria-label="Close enquiry"
        >
          <CloseIcon />
        </motion.button>
      </div>

      <div className="email-card">
        <div className="lbl">Original client email</div>
        <div className="kv" style={{ marginTop: 8 }}>
          <b>From</b> {oe.from}
        </div>
        <div className="kv">
          <b>Subject</b> {oe.subject}
        </div>
        {ex.scenario && (
          <div className="chipset">
            <span className="mini">
              <b>{ex.product_description}</b>
            </span>
            <span className="mini">
              qty <b>{String(ex.quantity)}</b> {ex.unit_of_measure || ""}
            </span>
            <span className="mini">{ex.delivery_terms || "—"}</span>
            <span className="mini">
              urgency <b>{ex.urgency}</b>
            </span>
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
          <motion.div variants={staggerContainer(0.06)} initial="hidden" animate="show">
            {data.supplier_drafts.map((d) => (
              <motion.div key={d.id} variants={fadeUp}>
                <SupplierCard
                  draft={d}
                  onChanged={() => {
                    load();
                    onActivity?.();
                  }}
                  toast={toast}
                />
              </motion.div>
            ))}
          </motion.div>
        </>
      )}
    </motion.div>
  );
}
