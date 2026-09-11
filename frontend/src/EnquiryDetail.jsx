import { useCallback, useEffect, useMemo, useState } from "react";
import { motion, useMotionValue, useSpring, useTransform } from "framer-motion";
import { api, composeBody } from "./api.js";
import { fadeUp, pressable, staggerContainer, viewSwap } from "./motion.js";

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
function ProcessingTrace({ status, stage, trace, draftProgress, matched }) {
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
          <h3>
            {status === "new" && stage === "received"
              ? "Enquiry received"
              : status === "new"
                ? "Queued"
                : "Processing"}
          </h3>
          <p>
            {drafting
              ? `Drafting supplier ${Math.min(dp.done + 1, dp.total)} of ${dp.total}…`
              : stage === "received"
                ? "Logged in — reading the email next"
                : stage
                  ? `Stage: ${stage}`
                  : "Waiting to start"}
          </p>
        </div>
      </div>

      {matched && matched.length > 0 && (
        <div className="matched-chips">
          <span className="matched-lbl">Matched so far</span>
          {matched.map((m) => (
            <span className="mchip" key={m.supplier_id}>
              {m.name}
              {m.match_score != null && <b> {Math.round(m.match_score * 100)}%</b>}
            </span>
          ))}
        </div>
      )}

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

// v2SQ: ONE shared outreach email per enquiry. The operator edits it once, ticks
// the suppliers to contact, and hits a single Send — every selected supplier gets
// the same text with {{supplier_name}} filled in for them.
function SharedOutreach({ threadId, draft, matched, progress, onActivity, toast }) {
  const [subject, setSubject] = useState(draft.subject);
  const [fields, setFields] = useState(draft.fields || []);
  const [busy, setBusy] = useState(false);

  const pending = useMemo(
    () => matched.filter((m) => m.status !== "sent"),
    [matched],
  );
  const [selected, setSelected] = useState(() =>
    new Set(pending.map((m) => m.supplier_id)),
  );

  // Keep the tick-set in step when the matched list changes under us (a live
  // refresh after a send): drop ids that are gone or now sent.
  useEffect(() => {
    setSelected((cur) => {
      const next = new Set();
      for (const m of matched)
        if (m.status !== "sent" && cur.has(m.supplier_id)) next.add(m.supplier_id);
      return next;
    });
  }, [matched]);

  const body = composeBody(draft.body_template, fields);
  const setFieldValue = (i, value) =>
    setFields((fs) => fs.map((f, j) => (j === i ? { ...f, value } : f)));
  const toggle = (id) =>
    setSelected((cur) => {
      const next = new Set(cur);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  const allPendingTicked =
    pending.length > 0 && pending.every((m) => selected.has(m.supplier_id));
  const toggleAll = () =>
    setSelected(allPendingTicked ? new Set() : new Set(pending.map((m) => m.supplier_id)));

  async function save() {
    setBusy(true);
    try {
      await api.patchEnquiryDraft(threadId, { draft_subject: subject, fields });
      toast("Draft saved");
      onActivity?.();
    } catch (e) {
      toast("Error: " + e.message);
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    const ids = [...selected];
    if (!ids.length) return;
    setBusy(true);
    try {
      // persist the current text first, then send to the ticked suppliers
      await api.patchEnquiryDraft(threadId, { draft_subject: subject, fields });
      const res = await api.sendEnquiry(threadId, ids);
      toast(
        `Sent to ${res.newly_sent.length} supplier(s) · ${res.progress.sent}/${res.progress.total}`,
      );
      onActivity?.();
    } catch (e) {
      toast("Error: " + e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="progress">
        <span>Supplier outreach</span>
        <b>
          {progress.sent} / {progress.total} sent
        </b>
      </div>

      {matched.length === 0 ? (
        <div className="empty">
          No supplier cleared the match threshold for this enquiry.
        </div>
      ) : (
        <div className="shared-outreach">
          <div className="so-mail">
            <div className="mrow">
              <span>To</span>
              <input
                value={`{{supplier_name}}  ·  ${selected.size} recipient${
                  selected.size === 1 ? "" : "s"
                } selected`}
                disabled
                title="Filled in per supplier on send"
              />
            </div>
            <div className="mrow">
              <span>Subject</span>
              <input value={subject} onChange={(e) => setSubject(e.target.value)} />
            </div>
            <div className="mbody">{body}</div>
            <div className="so-hint">
              <code>{"{{supplier_name}}"}</code> and <code>{"{{company}}"}</code>-style
              tokens above are placeholders — each supplier gets their own name filled in.
            </div>
          </div>

          <motion.div
            className="fields"
            variants={staggerContainer(0.035)}
            initial="hidden"
            animate="show"
          >
            {fields.map((f, i) => (
              <motion.div className="f" key={f.key} variants={fadeUp}>
                <div className="flabel">
                  <span>{f.label}</span>
                  <span className={"tag " + f.tag}>{f.tag}</span>
                </div>
                <input
                  className={f.tag === "NEEDS-REVIEW" ? "rev" : ""}
                  value={f.value}
                  onChange={(e) => setFieldValue(i, e.target.value)}
                />
              </motion.div>
            ))}
          </motion.div>

          <div className="so-suppliers">
            <div className="so-suppliers-head">
              <label className="so-check">
                <input
                  type="checkbox"
                  checked={allPendingTicked}
                  onChange={toggleAll}
                  disabled={pending.length === 0}
                />
                <span>Suppliers ({matched.length})</span>
              </label>
              <span className="so-count">{selected.size} selected</span>
            </div>
            {matched.map((m) => {
              const sent = m.status === "sent";
              return (
                <motion.label
                  layout
                  className={"so-row" + (sent ? " is-sent" : "")}
                  key={m.supplier_id}
                >
                  <input
                    type="checkbox"
                    checked={sent || selected.has(m.supplier_id)}
                    disabled={sent}
                    onChange={() => toggle(m.supplier_id)}
                  />
                  <span className="so-name">{m.name}</span>
                  {m.match_score != null && (
                    <span className="so-score">match {Math.round(m.match_score * 100)}%</span>
                  )}
                  <span className="so-email">{m.email}</span>
                  <span className={"badge " + (sent ? "sent" : "drafted")}>
                    {sent ? "sent" : "ready"}
                  </span>
                </motion.label>
              );
            })}
          </div>

          <div className="actions">
            <motion.button
              className="btn primary"
              {...pressable}
              disabled={busy || selected.size === 0}
              onClick={send}
            >
              {busy && <span className="spinner" />}
              Send to {selected.size} supplier{selected.size === 1 ? "" : "s"}
            </motion.button>
            <motion.button className="btn ghost" {...pressable} disabled={busy} onClick={save}>
              Save draft
            </motion.button>
          </div>
        </div>
      )}
    </>
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
          matched={data.matched_suppliers}
        />
      ) : data.enquiry_draft ? (
        <SharedOutreach
          threadId={data.email_thread_id}
          draft={data.enquiry_draft}
          matched={data.matched_suppliers || []}
          progress={data.progress}
          onActivity={() => {
            load();
            onActivity?.();
          }}
          toast={toast}
        />
      ) : (
        <div className="empty">
          No supplier cleared the match threshold for this enquiry.
        </div>
      )}
    </motion.div>
  );
}
