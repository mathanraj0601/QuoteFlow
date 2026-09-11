import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { api } from "./api.js";
import { overlay, pressable, sheet } from "./motion.js";

const DEFAULT_BODY = `We are sourcing rechargeable lithium-ion 18650 battery cells (3.7V ~3000mAh, UN38.3 tested).
- Product: Lithium-ion Battery Cells 18650
- Quantity required: 25,000 pieces
- Preferred delivery terms: FOB Shenzhen
- Delivery location: Bangalore, India
Somewhat urgent - please share pricing, MOQ and lead time.`;

export default function SimulateDialog({ onClose, onDelivered, toast }) {
  const [from, setFrom] = useState('"Priya Nair" <priya.nair@nairelectronics.in>');
  const [subject, setSubject] = useState("Enquiry: Lithium-ion 18650 cells - 25,000 pcs");
  const [body, setBody] = useState(DEFAULT_BODY);
  const [busy, setBusy] = useState(false);
  const formRef = useRef(null);
  const firstFieldRef = useRef(null);

  // Escape to close + focus the first field on open.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    firstFieldRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Lightweight focus trap: wrap Tab within the dialog's focusables.
  const onKeyDown = (e) => {
    if (e.key !== "Tab" || !formRef.current) return;
    const f = formRef.current.querySelectorAll(
      'input, textarea, button, a[href], [tabindex]:not([tabindex="-1"])',
    );
    if (!f.length) return;
    const first = f[0];
    const last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await api.processEmail({
        id: "ui-" + Date.now(),
        from,
        to: "procurement@meridiantrade.com",
        subject,
        body,
      });
      onDelivered(res);
    } catch (err) {
      toast("Error: " + err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.div
      className="backdrop"
      variants={overlay}
      initial="hidden"
      animate="show"
      exit="exit"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <motion.form
        ref={formRef}
        className="dlg"
        role="dialog"
        aria-modal="true"
        aria-label="Simulate an incoming client email"
        variants={sheet}
        onSubmit={submit}
        onKeyDown={onKeyDown}
      >
        <h3>Simulate an incoming client email</h3>
        <p>
          POSTs to <code>/process-email</code> — the same call the Gmail poller makes.
        </p>
        <label htmlFor="sim-from">From</label>
        <input
          id="sim-from"
          ref={firstFieldRef}
          value={from}
          onChange={(e) => setFrom(e.target.value)}
        />
        <label htmlFor="sim-subject">Subject</label>
        <input id="sim-subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
        <label htmlFor="sim-body">Body</label>
        <textarea id="sim-body" value={body} onChange={(e) => setBody(e.target.value)} />
        <div className="actions">
          <motion.button className="btn primary" {...pressable} type="submit" disabled={busy}>
            {busy && <span className="spinner" />}
            Deliver to pipeline
          </motion.button>
          <motion.button
            className="btn ghost"
            {...pressable}
            type="button"
            onClick={onClose}
          >
            Cancel
          </motion.button>
        </div>
      </motion.form>
    </motion.div>
  );
}
