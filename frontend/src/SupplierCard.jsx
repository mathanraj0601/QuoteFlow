import { useState } from "react";
import { api, composeBody } from "./api.js";

export default function SupplierCard({ draft, onChanged, toast }) {
  const sent = draft.status === "sent";
  const [to, setTo] = useState(draft.draft_to);
  const [subject, setSubject] = useState(draft.draft_subject);
  const [fields, setFields] = useState(draft.draft_fields || []);
  const [busy, setBusy] = useState(false);

  const body = composeBody(draft.draft_body_template, fields);

  const setFieldValue = (i, value) =>
    setFields((fs) => fs.map((f, j) => (j === i ? { ...f, value } : f)));

  async function save() {
    setBusy(true);
    try {
      await api.patchDraft(draft.id, { draft_to: to, draft_subject: subject, fields });
      toast("Draft saved");
      onChanged();
    } catch (e) {
      toast("Error: " + e.message);
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    setBusy(true);
    try {
      const res = await api.sendDraft(draft.id, { to, subject, body, fields });
      toast(`Sent (mark-only) · ${res.progress.sent}/${res.progress.total}`);
      onChanged();
    } catch (e) {
      toast("Error: " + e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={"scard" + (sent ? " sent" : "")}>
      <div className="sc1">
        <div className="sname">{draft.supplier_name}</div>
        {sent ? (
          <span className="badge sent">sent</span>
        ) : (
          <span className="badge drafted">
            match {Math.round((draft.match_score || 0) * 100)}%
          </span>
        )}
      </div>
      <div className="smeta">
        ★ {draft.supplier_rating ?? "—"} · supplier #{draft.supplier_id}
        {draft.sent_at ? ` · ${draft.sent_at}` : ""}
      </div>
      {sent && (
        <div className="sent-note">
          Marked sent — not transmitted (gmail.send is out of scope this version).
        </div>
      )}

      <div className="mail">
        <div className="mrow">
          <span>To</span>
          <input value={to} disabled={sent} onChange={(e) => setTo(e.target.value)} />
        </div>
        <div className="mrow">
          <span>Subject</span>
          <input value={subject} disabled={sent} onChange={(e) => setSubject(e.target.value)} />
        </div>
        <div className="mbody">{sent ? draft.draft_body : body}</div>
      </div>

      <div className="fields">
        {fields.map((f, i) => (
          <div className="f" key={f.key}>
            <div className="flabel">
              <span>{f.label}</span>
              <span className={"tag " + f.tag}>{f.tag}</span>
            </div>
            <input
              className={f.tag === "NEEDS-REVIEW" ? "rev" : ""}
              value={f.value}
              disabled={sent}
              onChange={(e) => setFieldValue(i, e.target.value)}
            />
          </div>
        ))}
      </div>

      {!sent && (
        <div className="actions">
          <button className="btn primary" disabled={busy} onClick={send}>
            Send to supplier
          </button>
          <button className="btn ghost" disabled={busy} onClick={save}>
            Save draft
          </button>
        </div>
      )}
    </div>
  );
}
