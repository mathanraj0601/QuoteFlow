import { useState } from "react";
import { api } from "./api.js";

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
    <div className="backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="dlg" onSubmit={submit}>
        <h3>Simulate an incoming client email</h3>
        <p>
          POSTs to <code>/process-email</code> — the same call the Gmail poller makes.
        </p>
        <label>From</label>
        <input value={from} onChange={(e) => setFrom(e.target.value)} />
        <label>Subject</label>
        <input value={subject} onChange={(e) => setSubject(e.target.value)} />
        <label>Body</label>
        <textarea value={body} onChange={(e) => setBody(e.target.value)} />
        <div className="actions">
          <button className="btn primary" type="submit" disabled={busy}>
            Deliver to pipeline
          </button>
          <button className="btn ghost" type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
