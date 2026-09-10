import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import EnquiryDetail from "./EnquiryDetail.jsx";
import Suppliers from "./Suppliers.jsx";
import SimulateDialog from "./SimulateDialog.jsx";

function useToast() {
  const [msg, setMsg] = useState(null);
  const t = useRef();
  const toast = useCallback((m) => {
    setMsg(m);
    clearTimeout(t.current);
    t.current = setTimeout(() => setMsg(null), 2800);
  }, []);
  return [msg, toast];
}

function HealthPill({ health }) {
  if (!health) return <span className="pill err">backend down</span>;
  const poller = health.poller_alive ? "live" : health.poller_enabled ? "starting" : "off";
  return (
    <span className="pill ok">
      poller {poller} · llm:{health.llm_provider} · emb:{health.embedding_provider}
    </span>
  );
}

function Desk({ selectedId, onSelect, toast, refreshKey }) {
  const [enquiries, setEnquiries] = useState(null);
  const [includeAll, setIncludeAll] = useState(false);
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    try {
      setEnquiries((await api.enquiries(includeAll)).enquiries);
      setErr(null);
    } catch (e) {
      setErr(e.message);
    }
  }, [includeAll]);

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load, refreshKey]);

  return (
    <div className="grid">
      <div className="panel">
        <div className="phead">
          <h2>The Desk</h2>
          <label className="toggle">
            <input
              type="checkbox"
              checked={includeAll}
              onChange={(e) => setIncludeAll(e.target.checked)}
            />
            show seed backfill
          </label>
        </div>
        {err && <div className="empty">{err}</div>}
        {!err && enquiries?.length === 0 && <div className="empty">No enquiries yet.</div>}
        {!err && !enquiries && <div className="empty">loading…</div>}
        {enquiries?.map((e) => {
          const allSent = e.status === "drafted" && e.drafts > 0 && e.sent === e.drafts;
          return (
            <div
              key={e.email_thread_id}
              className={"deal" + (selectedId === e.email_thread_id ? " sel" : "")}
              onClick={() => onSelect(e.email_thread_id)}
            >
              <div className="row1">
                <div className="subj">{e.subject || "(no subject)"}</div>
                {e.status === "drafted" ? (
                  <span className={"badge " + (allSent ? "sent" : "drafted")}>
                    {allSent ? "all sent" : "drafted"}
                  </span>
                ) : (
                  <span className="badge logged">{e.scenario || "logged"}</span>
                )}
              </div>
              <div className="from">{e.from_email}</div>
              <div className="meta">
                <span>#{e.email_thread_id}</span>
                <span>{e.drafts} draft(s)</span>
                {e.drafts > 0 && <span>{e.sent}/{e.drafts} sent</span>}
              </div>
            </div>
          );
        })}
      </div>

      <div className="panel">
        {selectedId ? (
          <EnquiryDetail
            threadId={selectedId}
            onClose={() => onSelect(null)}
            onActivity={load}
            toast={toast}
          />
        ) : (
          <div className="empty">Select an enquiry, or use “Simulate email”.</div>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState("desk");
  const [health, setHealth] = useState(undefined);
  const [selectedId, setSelectedId] = useState(null);
  const [simOpen, setSimOpen] = useState(false);
  const [deskKey, setDeskKey] = useState(0);
  const refreshDesk = useCallback(() => setDeskKey((k) => k + 1), []);
  const [toastMsg, toast] = useToast();

  useEffect(() => {
    const load = () => api.health().then(setHealth).catch(() => setHealth(null));
    load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, []);

  return (
    <>
      <header>
        <div className="mark">Q</div>
        <div className="title">QuoteFlow</div>
        <nav>
          <button className={view === "desk" ? "active" : ""} onClick={() => setView("desk")}>
            Desk
          </button>
          <button
            className={view === "suppliers" ? "active" : ""}
            onClick={() => setView("suppliers")}
          >
            Suppliers
          </button>
        </nav>
        <div className="spacer" />
        <HealthPill health={health} />
        <button className="hbtn" onClick={() => setSimOpen(true)}>
          Simulate email
        </button>
      </header>

      <main>
        {view === "desk" && (
          <Desk
            selectedId={selectedId}
            onSelect={setSelectedId}
            toast={toast}
            refreshKey={deskKey}
          />
        )}
        {view === "suppliers" && <Suppliers />}
      </main>

      {simOpen && (
        <SimulateDialog
          onClose={() => setSimOpen(false)}
          onDelivered={(res) => {
            setSimOpen(false);
            refreshDesk();
            toast(
              `${res.status}` +
                (res.supplier_quote_request_ids
                  ? ` · ${res.supplier_quote_request_ids.length} draft(s)`
                  : ""),
            );
            setView("desk");
            if (res.email_thread_id) setSelectedId(res.email_thread_id);
          }}
          toast={toast}
        />
      )}

      {toastMsg && <div className="toast">{toastMsg}</div>}
    </>
  );
}
