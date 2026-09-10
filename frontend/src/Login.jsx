import { useState } from "react";

// Static, cosmetic sign-in — matches plans/v2SQ-quoteflow-ui.html. There is no
// backend auth (POC scope doc: "Auth, multi-user — out of scope"). Any non-empty
// email + password gets you in; "Continue as demo user" skips even that. The
// chosen email is kept in localStorage so a refresh doesn't bounce you out.
const DEMO_EMAIL = "operator@quoteflow.io";

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
        <div className="brandrow">
          <div className="mark">Q</div>
          <div className="wordmark">QuoteFlow</div>
        </div>
        <h2>The desk you&rsquo;ll actually enjoy using</h2>
        <p>
          Enquiries, quotes and suppliers matched and drafted automatically &mdash;
          you review, edit, and send.
        </p>
      </div>

      <div className="login-form-wrap">
        <form className="login-form-inner" onSubmit={submit}>
          <div className="brandrow center">
            <div className="mark">Q</div>
            <div className="wordmark">QuoteFlow</div>
          </div>
          <div className="login-title">
            <h1>Sign in</h1>
            <p>Welcome back to your desk</p>
          </div>

          <div className="field-group">
            <label htmlFor="login-email">Email</label>
            <input
              id="login-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
            />
          </div>
          <div className="field-group">
            <label htmlFor="login-password">Password</label>
            <input
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>
          <div className="forgot">
            <a onClick={(e) => e.preventDefault()} href="#">
              Forgot password?
            </a>
          </div>

          {err && <div className="login-err">{err}</div>}

          <button type="submit" className="btn-primary">
            Sign in
          </button>
          <div className="demo-link">
            <a onClick={() => onSignIn("demo user")}>Continue as demo user</a>
          </div>
          <div className="login-foot">Local proof of concept &middot; QuoteFlow 2026</div>
        </form>
      </div>
    </div>
  );
}
