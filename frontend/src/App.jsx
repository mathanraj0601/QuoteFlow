import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, LayoutGroup, motion } from "framer-motion";
import { api, openProgressSocket } from "./api.js";
import { fadeUp, pressable, spring, staggerContainer, viewSwap } from "./motion.js";
import EnquiryDetail from "./EnquiryDetail.jsx";
import Suppliers from "./Suppliers.jsx";
import Dashboard from "./Dashboard.jsx";
import SimulateDialog from "./SimulateDialog.jsx";
import Login from "./Login.jsx";
import { deskBucket, rowBadge } from "./status.js";

const AUTH_KEY = "qf_user";
const readUser = () => {
  try {
    return localStorage.getItem(AUTH_KEY);
  } catch {
    return null;
  }
};

function initials(name) {
  const s = (name || "").trim();
  if (!s) return "QF";
  if (s.includes("@")) return s.slice(0, 2).toUpperCase();
  const parts = s.split(/\s+/);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || parts[0]?.[1] || "")).toUpperCase();
}

// ---------- theme (light / dark) ----------
// No stored choice -> follows the OS (styles.css has the matching
// `@media (prefers-color-scheme: light)` block). An explicit toggle click
// stamps data-theme on <html> and wins over the OS from then on.
const THEME_KEY = "qf_theme";

function systemTheme() {
  try {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function storedTheme() {
  try {
    return localStorage.getItem(THEME_KEY);
  } catch {
    return null;
  }
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme) root.setAttribute("data-theme", theme);
  else root.removeAttribute("data-theme");
}

function useTheme() {
  const [theme, setThemeState] = useState(() => storedTheme() || systemTheme());

  // Land the explicit choice (if any) on <html> before first paint's worth of
  // caring; CSS itself already renders correctly either way via the media query.
  useEffect(() => {
    applyTheme(storedTheme());
  }, []);

  // No stored preference yet -> keep tracking the OS if it changes underneath us.
  useEffect(() => {
    if (storedTheme()) return undefined;
    let mq;
    try {
      mq = window.matchMedia("(prefers-color-scheme: light)");
    } catch {
      return undefined;
    }
    const onChange = () => setThemeState(systemTheme());
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);

  const setTheme = useCallback((next) => {
    setThemeState(next);
    applyTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* private mode — the toggle still works for this tab */
    }
  }, []);

  const toggle = useCallback(
    () => setTheme(theme === "light" ? "dark" : "light"),
    [theme, setTheme],
  );

  return [theme, toggle];
}

function SunIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="2" />
      <path
        d="M12 2v2.4M12 19.6V22M4.9 4.9l1.7 1.7M17.4 17.4l1.7 1.7M2 12h2.4M19.6 12H22M4.9 19.1l1.7-1.7M17.4 6.6l1.7-1.7"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M20.5 14.5A8.5 8.5 0 1 1 9.5 3.5a6.7 6.7 0 0 0 11 11Z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ThemeToggle({ theme, onToggle }) {
  return (
    <motion.button
      className="btn ghost icon"
      {...pressable}
      onClick={onToggle}
      aria-label={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
      title={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
    >
      {theme === "light" ? <SunIcon /> : <MoonIcon />}
    </motion.button>
  );
}

function SparkIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function HamburgerIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M3 6h18M3 12h18M3 18h18"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

// Small-screen nav: the Desk/Suppliers pills move behind a hamburger button
// (styles.css swaps them in under the mobile breakpoint) instead of wrapping.
function MobileNav({ items, view, onSelect }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="hamburger-wrap">
      <motion.button
        className="btn ghost icon hamburger"
        {...pressable}
        onClick={() => setOpen((v) => !v)}
        aria-label="Menu"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <HamburgerIcon />
      </motion.button>
      <AnimatePresence>
        {open && (
          <>
            <div className="avatar-scrim" onClick={() => setOpen(false)} />
            <motion.div
              className="mobile-nav-menu"
              role="menu"
              initial={{ opacity: 0, scale: 0.94, y: -6 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: -4 }}
              transition={spring}
            >
              {items.map(([key, label]) => (
                <button
                  key={key}
                  role="menuitem"
                  className={"mobile-nav-item" + (view === key ? " active" : "")}
                  onClick={() => {
                    onSelect(key);
                    setOpen(false);
                  }}
                >
                  {label}
                </button>
              ))}
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}

function AvatarMenu({ user, onSignOut }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="avatar-wrap">
      <motion.button
        className="avatar"
        {...pressable}
        onClick={() => setOpen((v) => !v)}
        title={user}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {initials(user)}
      </motion.button>
      <AnimatePresence>
        {open && (
          <>
            <div className="avatar-scrim" onClick={() => setOpen(false)} />
            <motion.div
              className="avatar-menu"
              role="menu"
              initial={{ opacity: 0, scale: 0.94, y: -6 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: -4 }}
              transition={spring}
            >
              <div className="avatar-menu-head">
                <b>Signed in</b>
                <span>{user}</span>
              </div>
              <button
                className="avatar-menu-item"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  onSignOut();
                }}
              >
                Sign out
              </button>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}

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

function HealthPill({ health, live }) {
  if (!health)
    return (
      <span className="pill err">
        <span className="dot" />
        backend down
      </span>
    );
  const poller = health.poller_alive ? "live" : health.poller_enabled ? "starting" : "off";
  return (
    <span className={"pill " + (live ? "ok" : "warn")}>
      <span className="dot" />
      {live ? "live" : "polling"} · poller {poller} · llm:{health.llm_provider}
    </span>
  );
}

const FILTERS = [
  ["all", "All"],
  ["new", "New"],
  ["processing", "Processing"],
  ["drafted", "Drafted"],
  ["sent", "Fully sent"],
];

function DealRow({ e, selected, onSelect }) {
  const bucket = deskBucket(e);
  return (
    <motion.div
      layout
      variants={fadeUp}
      exit={{ opacity: 0, height: 0, transition: { duration: 0.18 } }}
      className={"deal" + (selected ? " sel" : "")}
      onClick={() => onSelect(e.email_thread_id)}
    >
      <div className="row1">
        <div className="subj">{e.subject || "(no subject)"}</div>
        <span className={"badge " + bucket}>{rowBadge(e)}</span>
      </div>
      <div className="from">{e.from_email}</div>
      <div className="meta">
        <span>#{e.email_thread_id}</span>
        {bucket === "new" || bucket === "processing" ? (
          <span className="live-stage">
            {e.pipeline_stage === "drafting" || e.pipeline_stage === "matched"
              ? `drafting ${e.drafts}/${e.drafts_total || "?"}`
              : e.pipeline_stage || "queued"}
          </span>
        ) : (
          <span>{e.drafts} draft(s)</span>
        )}
        {e.drafts > 0 && (
          <span>
            {e.sent}/{e.drafts} sent
          </span>
        )}
      </div>
    </motion.div>
  );
}

function DeskSkeleton() {
  return (
    <div className="skel-list">
      {[0, 1, 2, 3].map((i) => (
        <div className="skel-row" key={i}>
          <div className="skel w-70" />
          <div className="skel w-45" />
          <div className="skel w-30" />
        </div>
      ))}
    </div>
  );
}

function Desk({ selectedId, onSelect, toast, refreshKey, live }) {
  const [enquiries, setEnquiries] = useState(null);
  const [includeAll, setIncludeAll] = useState(false);
  const [filter, setFilter] = useState("all");
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    try {
      setEnquiries((await api.enquiries(includeAll)).enquiries);
      setErr(null);
    } catch (e) {
      setErr(e.message);
    }
  }, [includeAll]);

  // WebSocket pushes drive the refresh; the interval is just a slow safety net.
  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load, refreshKey, live]);

  const counts = (enquiries || []).reduce(
    (acc, e) => {
      acc.all += 1;
      acc[deskBucket(e)] += 1;
      return acc;
    },
    { all: 0, new: 0, processing: 0, drafted: 0, sent: 0, logged: 0 },
  );
  const shown = (enquiries || []).filter(
    (e) => filter === "all" || deskBucket(e) === filter,
  );

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
        <div className="chips">
          {FILTERS.map(([key, label]) => (
            <button
              key={key}
              className={"chip" + (filter === key ? " active" : "")}
              onClick={() => setFilter(key)}
            >
              {label} <b>{counts[key] ?? 0}</b>
            </button>
          ))}
        </div>
        {err && <div className="empty">{err}</div>}
        {!err && !enquiries && <DeskSkeleton />}
        {!err && enquiries?.length === 0 && <div className="empty">No enquiries yet.</div>}
        {!err && enquiries?.length > 0 && shown.length === 0 && (
          <div className="empty">Nothing in “{filter}”.</div>
        )}
        {!err && enquiries && (
          <LayoutGroup>
            <motion.div variants={staggerContainer(0.035)} initial="hidden" animate="show">
              <AnimatePresence initial={false}>
                {shown.map((e) => (
                  <DealRow
                    key={e.email_thread_id}
                    e={e}
                    selected={selectedId === e.email_thread_id}
                    onSelect={onSelect}
                  />
                ))}
              </AnimatePresence>
            </motion.div>
          </LayoutGroup>
        )}
      </div>

      <div className="panel">
        {selectedId ? (
          <EnquiryDetail
            key={selectedId}
            threadId={selectedId}
            live={live}
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

const NAV = [
  ["dashboard", "Dashboard"],
  ["desk", "Desk"],
  ["suppliers", "Suppliers"],
];

export default function App() {
  const [theme, toggleTheme] = useTheme();
  const [user, setUser] = useState(readUser);
  const [view, setView] = useState("dashboard");
  const [health, setHealth] = useState(undefined);
  const [selectedId, setSelectedId] = useState(null);
  const [simOpen, setSimOpen] = useState(false);
  const [deskKey, setDeskKey] = useState(0);
  const refreshDesk = useCallback(() => setDeskKey((k) => k + 1), []);
  const [toastMsg, toast] = useToast();
  // Bumped on every WebSocket progress event; used as a render/refresh trigger.
  const [liveTick, setLiveTick] = useState(0);
  const [wsConnected, setWsConnected] = useState(false);

  const signIn = useCallback((email) => {
    try {
      localStorage.setItem(AUTH_KEY, email);
    } catch {
      /* private mode — session-only sign-in still works */
    }
    setUser(email);
  }, []);

  const signOut = useCallback(() => {
    try {
      localStorage.removeItem(AUTH_KEY);
    } catch {
      /* ignore */
    }
    setUser(null);
    setSelectedId(null);
    setView("dashboard");
  }, []);

  useEffect(() => {
    if (!user) return undefined;
    const load = () => api.health().then(setHealth).catch(() => setHealth(null));
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [user]);

  // One WebSocket for the whole app: each pipeline event bumps liveTick, which
  // Desk + EnquiryDetail take as a refresh trigger. Falls back to polling if the
  // socket can't connect.
  useEffect(() => {
    if (!user) return undefined;
    const stop = openProgressSocket((msg) => {
      if (msg.event === "_connected") setWsConnected(true);
      else if (msg.event === "_disconnected") setWsConnected(false);
      else {
        setLiveTick((t) => t + 1);
        if (msg.event === "draft" && msg.supplier)
          toast(`draft ${msg.done}/${msg.total}: ${msg.supplier}`);
        if (msg.event === "done" && msg.status === "drafted")
          toast(`enquiry #${msg.email_thread_id}: ${msg.drafts} draft(s) ready`);
      }
    });
    return stop;
  }, [user, toast]);

  if (!user) return <Login onSignIn={signIn} />;

  return (
    <>
      <header>
        <div className="mark">Q</div>
        <div className="title">QuoteFlow</div>

        {/* Mobile: Desk/Suppliers collapse behind a hamburger (styles.css swaps
            these two in/out at the mobile breakpoint). */}
        <MobileNav items={NAV} view={view} onSelect={setView} />

        <LayoutGroup>
          <nav className="nav-desktop">
            {NAV.map(([key, label]) => (
              <button
                key={key}
                className={view === key ? "active" : ""}
                onClick={() => setView(key)}
              >
                {view === key && (
                  <motion.span
                    layoutId="nav-pill"
                    className="nav-pill"
                    transition={spring}
                  />
                )}
                {label}
              </button>
            ))}
          </nav>
        </LayoutGroup>
        <div className="spacer" />
        {/* Health pill — hidden for now, health polling + wsConnected tracking still run.
        <HealthPill health={health} live={wsConnected} />
        */}
        {/* Simulate email — hidden for now, keeping the dialog + wiring in place.
        <motion.button className="hbtn" {...pressable} onClick={() => setSimOpen(true)}>
          <SparkIcon />
          <span className="lbl-text">Simulate email</span>
        </motion.button>
        */}
        <ThemeToggle theme={theme} onToggle={toggleTheme} />
        <AvatarMenu user={user} onSignOut={signOut} />
      </header>

      <main>
        {/* popLayout (not "wait"): the incoming view mounts and starts fading in
            immediately instead of waiting out the outgoing view's exit first —
            the visible content (and Suppliers' now-cached data) shows up right
            on click instead of after a sequential exit-then-enter delay. */}
        <AnimatePresence mode="popLayout">
          <motion.div
            key={view}
            variants={viewSwap}
            initial="hidden"
            animate="show"
            exit="exit"
          >
            {view === "dashboard" && <Dashboard live={liveTick} />}
            {view === "desk" && (
              <Desk
                selectedId={selectedId}
                onSelect={setSelectedId}
                toast={toast}
                refreshKey={deskKey}
                live={liveTick}
              />
            )}
            {view === "suppliers" && <Suppliers />}
          </motion.div>
        </AnimatePresence>
      </main>

      <AnimatePresence>
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
      </AnimatePresence>

      <AnimatePresence>
        {toastMsg && (
          <motion.div
            className="toast"
            initial={{ opacity: 0, y: 24, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 12, scale: 0.98 }}
            transition={spring}
          >
            {toastMsg}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
