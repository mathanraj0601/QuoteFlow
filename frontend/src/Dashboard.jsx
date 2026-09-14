import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { api } from "./api.js";
import { fadeUp, staggerContainer } from "./motion.js";
import { BUCKET_COLOR_VAR, BUCKET_LABEL, BUCKET_ORDER, deskBucket, rowBadge } from "./status.js";

const TREND_DAYS = 14;

function DashboardSkeleton() {
  return (
    <div className="dash-grid">
      <div className="panel dash-stats-panel">
        <div className="skel-list"><div className="skel-row"><div className="skel w-70" /></div></div>
      </div>
      <div className="panel"><div className="skel-list"><div className="skel-row"><div className="skel w-45" /></div></div></div>
      <div className="panel"><div className="skel-list"><div className="skel-row"><div className="skel w-45" /></div></div></div>
      <div className="panel"><div className="skel-list">
        {[0, 1, 2].map((i) => (
          <div className="skel-row" key={i}><div className="skel w-70" /><div className="skel w-30" /></div>
        ))}
      </div></div>
    </div>
  );
}

// Donut/pie chart built from plain SVG (stroke-dasharray segments on a ring) —
// no charting library needed for five slices, and it inherits the theme's CSS
// custom properties directly instead of a second hardcoded palette.
function Donut({ counts, size = 152, thickness = 24 }) {
  const total = BUCKET_ORDER.reduce((s, k) => s + (counts[k] || 0), 0);
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  let acc = 0;
  const segments = BUCKET_ORDER.filter((k) => counts[k] > 0).map((k) => {
    const value = counts[k];
    const frac = total ? value / total : 0;
    const dash = frac * c;
    const seg = { key: k, value, dash, offset: acc, color: `var(${BUCKET_COLOR_VAR[k]})` };
    acc += dash;
    return seg;
  });

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="donut" role="img"
         aria-label={`${total} enquiries tracked, by status`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={thickness} />
      {segments.map((s) => (
        <motion.circle
          key={s.key}
          cx={size / 2} cy={size / 2} r={r} fill="none"
          stroke={s.color} strokeWidth={thickness} strokeLinecap="butt"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          initial={{ strokeDasharray: `0 ${c}`, strokeDashoffset: -s.offset }}
          animate={{ strokeDasharray: `${s.dash} ${c - s.dash}`, strokeDashoffset: -s.offset }}
          transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
        />
      ))}
      <text x="50%" y="47%" textAnchor="middle" className="donut-total">{total}</text>
      <text x="50%" y="64%" textAnchor="middle" className="donut-total-lbl">tracked</text>
    </svg>
  );
}

function Legend({ counts, active, onPick }) {
  return (
    <div className="legend">
      {BUCKET_ORDER.map((k) => (
        <button
          key={k}
          className={"legend-item" + (active === k ? " active" : "")}
          onClick={() => onPick(active === k ? null : k)}
        >
          <span className="legend-swatch" style={{ background: `var(${BUCKET_COLOR_VAR[k]})` }} />
          <span className="legend-lbl">{BUCKET_LABEL[k]}</span>
          <span className="legend-val">{counts[k] || 0}</span>
        </button>
      ))}
    </div>
  );
}

// Vertical bar chart, plain CSS — enquiries received per day for the last
// TREND_DAYS days, so a spike or a dead patch in the pipeline is visible at a
// glance instead of only living in the raw list below.
function TrendBars({ series }) {
  const max = Math.max(1, ...series.map((d) => d.count));
  return (
    <div className="trend-bars">
      {series.map((d, i) => (
        <div className="trend-col" key={d.date} title={`${d.date}: ${d.count} enquiry(ies)`}>
          <motion.div
            className="trend-bar"
            initial={{ height: 0 }}
            animate={{ height: `${(d.count / max) * 100}%` }}
            transition={{ duration: 0.5, delay: i * 0.02, ease: [0.22, 1, 0.36, 1] }}
          />
          <span className="trend-lbl">{d.dayLabel}</span>
        </div>
      ))}
    </div>
  );
}

function dayKey(iso) {
  return (iso || "").slice(0, 10); // "YYYY-MM-DD"
}

export default function Dashboard({ live }) {
  const [enquiries, setEnquiries] = useState(null);
  const [err, setErr] = useState(null);
  const [filter, setFilter] = useState(null); // null = all buckets

  useEffect(() => {
    api.enquiries(true).then((d) => { setEnquiries(d.enquiries); setErr(null); })
      .catch((e) => setErr(e.message));
  }, [live]);

  const counts = useMemo(() => {
    const c = { new: 0, processing: 0, drafted: 0, sent: 0, logged: 0 };
    for (const e of enquiries || []) c[deskBucket(e)] += 1;
    return c;
  }, [enquiries]);

  const totalDrafts = useMemo(
    () => (enquiries || []).reduce((s, e) => s + (e.drafts || 0), 0),
    [enquiries],
  );
  const totalSent = useMemo(
    () => (enquiries || []).reduce((s, e) => s + (e.sent || 0), 0),
    [enquiries],
  );

  const trend = useMemo(() => {
    const byDay = new Map();
    for (const e of enquiries || []) {
      const k = dayKey(e.processed_at);
      if (!k) continue;
      byDay.set(k, (byDay.get(k) || 0) + 1);
    }
    const days = [];
    const today = new Date();
    for (let i = TREND_DAYS - 1; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const iso = d.toISOString().slice(0, 10);
      days.push({
        date: iso,
        dayLabel: String(d.getDate()).padStart(2, "0"),
        count: byDay.get(iso) || 0,
      });
    }
    return days;
  }, [enquiries]);

  const shown = useMemo(
    () => (filter ? (enquiries || []).filter((e) => deskBucket(e) === filter) : enquiries || []),
    [enquiries, filter],
  );

  if (err) return <div className="panel"><div className="empty">{err}</div></div>;
  if (!enquiries) return <DashboardSkeleton />;

  const total = enquiries.length;

  return (
    <motion.div variants={staggerContainer(0.05)} initial="hidden" animate="show" className="dash-grid">
      <motion.div className="panel dash-stats-panel" variants={fadeUp}>
        <div className="phead"><h2>All enquiries tracked</h2></div>
        <div className="dash-stats">
          <button
            className={"stat-card" + (filter === null ? " active" : "")}
            onClick={() => setFilter(null)}
          >
            <span className="stat-value">{total}</span>
            <span className="stat-label">Total</span>
          </button>
          {BUCKET_ORDER.map((k) => (
            <button
              key={k}
              className={"stat-card" + (filter === k ? " active" : "")}
              onClick={() => setFilter(filter === k ? null : k)}
            >
              <span className="stat-value" style={{ color: `var(${BUCKET_COLOR_VAR[k]})` }}>
                {counts[k]}
              </span>
              <span className="stat-label">{BUCKET_LABEL[k]}</span>
            </button>
          ))}
          <div className="stat-card readonly">
            <span className="stat-value">{totalSent}/{totalDrafts}</span>
            <span className="stat-label">Supplier drafts sent</span>
          </div>
        </div>
      </motion.div>

      <motion.div className="panel dash-chart-panel" variants={fadeUp}>
        <div className="phead"><h2>By status</h2></div>
        <div className="dash-donut-row">
          <Donut counts={counts} />
          <Legend counts={counts} active={filter} onPick={setFilter} />
        </div>
      </motion.div>

      <motion.div className="panel dash-chart-panel" variants={fadeUp}>
        <div className="phead"><h2>Received, last {TREND_DAYS} days</h2></div>
        <TrendBars series={trend} />
      </motion.div>

      <motion.div className="panel dash-table-panel" variants={fadeUp}>
        <div className="phead">
          <h2>
            {filter ? `${BUCKET_LABEL[filter]} enquiries` : "Every tracked enquiry"}
          </h2>
          <span className="chip">{shown.length} shown</span>
        </div>
        {shown.length === 0 ? (
          <div className="empty">Nothing in this view.</div>
        ) : (
          <div className="tbl-wrap dash-tbl-wrap">
            <table>
              <tbody>
                <tr>
                  <th>Subject</th>
                  <th>From</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th>Received</th>
                </tr>
                {shown.map((e) => {
                  const bucket = deskBucket(e);
                  return (
                    <tr key={e.email_thread_id}>
                      <td data-label="Subject">{e.subject || "(no subject)"}</td>
                      <td data-label="From">{e.from_email}</td>
                      <td data-label="Status">
                        <span className={"badge " + bucket}>{rowBadge(e)}</span>
                      </td>
                      <td data-label="Progress">{e.drafts ? `${e.sent}/${e.drafts} sent` : "—"}</td>
                      <td data-label="Received">{(e.processed_at || "").replace("T", " ").slice(0, 16)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </motion.div>
    </motion.div>
  );
}
