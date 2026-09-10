import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { fadeUp, staggerContainer } from "./motion.js";

// Static, cosmetic sign-in — matches plans/v2SQ-quoteflow-ui.html. There is no
// backend auth (POC scope doc: "Auth, multi-user — out of scope"). Any non-empty
// email + password gets you in; "Continue as demo user" skips even that. The
// chosen email is kept in localStorage so a refresh doesn't bounce you out.
const DEMO_EMAIL = "operator@quoteflow.io";

const orbFloat = (dur) => ({
  animate: { x: [0, 24, -12, 0], y: [0, -18, 14, 0], scale: [1, 1.08, 0.96, 1] },
  transition: { duration: dur, repeat: Infinity, ease: "easeInOut" },
});

export default function Login({ onSignIn }) {
  const [email, setEmail] = useState(DEMO_EMAIL);
  const [password, setPassword] = useState("demo1234");
  const [err, setErr] = useState(null);

  const submit = (e) => {
    e.preventDefault();
    if (!email.trim() || !password.trim()) {
      setErr("Enter an email and password (any value — this is a local POC).");
      return;
    }
    onSignIn(email.trim());
  };

  return (
    <div className="login-shell">
      <div className="login-brand">
        <motion.div className="login-orb a" {...orbFloat(16)} />
        <motion.div className="login-orb b" {...orbFloat(21)} />
        <div className="brandrow">
          <div className="mark">Q</div>
          <div className="wordmark">QuoteFlow</div>
        </div>
        <motion.h2
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        >
          The desk you&rsquo;ll actually <span className="gradient-text">enjoy using</span>
        </motion.h2>
        <motion.p
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.08, ease: [0.22, 1, 0.36, 1] }}
        >
          Enquiries, quotes and suppliers matched and drafted automatically &mdash;
          you review, edit, and send.
        </motion.p>
      </div>

      <div className="login-form-wrap">
        <motion.form
          className="login-form-inner"
          onSubmit={submit}
          variants={staggerContainer(0.06)}
          initial="hidden"
          animate="show"
        >
          <motion.div className="brandrow center" variants={fadeUp}>
            <div className="mark">Q</div>
            <div className="wordmark">QuoteFlow</div>
          </motion.div>
          <motion.div className="login-title" variants={fadeUp}>
            <h1>Sign in</h1>
            <p>Welcome back to your desk</p>
          </motion.div>

          <motion.div className="field-group" variants={fadeUp}>
            <label htmlFor="login-email">Email</label>
            <input
              id="login-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
            />
          </motion.div>
          <motion.div className="field-group" variants={fadeUp}>
            <label htmlFor="login-password">Password</label>
            <input
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </motion.div>
          <motion.div className="forgot" variants={fadeUp}>
            <a onClick={(e) => e.preventDefault()} href="#">
              Forgot password?
            </a>
          </motion.div>

          <AnimatePresence>
            {err && (
              <motion.div
                className="login-err"
                initial={{ opacity: 0, y: -6, height: 0 }}
                animate={{ opacity: 1, y: 0, height: "auto" }}
                exit={{ opacity: 0, y: -6, height: 0 }}
              >
                {err}
              </motion.div>
            )}
          </AnimatePresence>

          <motion.button
            type="submit"
            className="btn-primary"
            variants={fadeUp}
            whileHover={{ y: -1 }}
            whileTap={{ scale: 0.98 }}
          >
            Sign in
          </motion.button>
          <motion.div className="demo-link" variants={fadeUp}>
            <a onClick={() => onSignIn("demo user")}>Continue as demo user</a>
          </motion.div>
          <motion.div className="login-foot" variants={fadeUp}>
            Local proof of concept &middot; QuoteFlow 2026
          </motion.div>
        </motion.form>
      </div>
    </div>
  );
}
