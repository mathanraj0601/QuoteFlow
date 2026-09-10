import { useEffect, useState } from "react";
import { api } from "./api.js";

export default function Suppliers() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.suppliers().then(setData).catch((e) => setErr(e.message));
  }, []);

  if (err) return <div className="panel"><div className="empty">{err}</div></div>;
  if (!data) return <div className="panel"><div className="empty">loading…</div></div>;

  return (
    <div className="panel">
      <div className="phead">
        <h2>Supplier directory</h2>
        <span className="kv">{data.count} suppliers</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table>
          <tbody>
            <tr>
              <th>Name</th>
              <th>Specialisation</th>
              <th>Region</th>
              <th>Rating</th>
              <th>Contact</th>
              <th>Payment terms</th>
            </tr>
            {data.suppliers.map((s) => (
              <tr key={s.id}>
                <td>
                  <b>{s.name}</b>
                </td>
                <td>{s.specialization}</td>
                <td>{s.region}</td>
                <td className="stars">★ {s.rating}</td>
                <td>{s.contact_email}</td>
                <td>{s.payment_terms}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
