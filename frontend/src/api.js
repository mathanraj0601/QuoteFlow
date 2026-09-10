// Thin fetch wrapper. Paths are same-origin in the built app; proxied in dev.
async function req(path, opts) {
  const r = await fetch(path, opts);
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((body && body.detail) || `${r.status} ${r.statusText}`);
  return body;
}

const json = (method, path, payload) =>
  req(path, {
    method,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });

export const api = {
  health: () => req("/health"),
  enquiries: (includeAll) => req("/enquiries" + (includeAll ? "?include_all=true" : "")),
  supplierDrafts: (threadId) => req(`/enquiry/${threadId}/supplier-drafts`),
  processEmail: (msg) => json("POST", "/process-email", msg),
  patchDraft: (id, patch) => json("PATCH", `/supplier-quote-requests/${id}`, patch),
  sendDraft: (id, payload) => json("POST", `/supplier-quote-requests/${id}/send`, payload || {}),
  suppliers: () => req("/suppliers"),
};

// Live pipeline progress over WebSocket. `onEvent` gets each {event, ...} message.
// Auto-reconnects with backoff. Returns a stop() function.
export function openProgressSocket(onEvent) {
  let ws = null;
  let stopped = false;
  let retry = 0;
  const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";

  const connect = () => {
    if (stopped) return;
    try {
      ws = new WebSocket(url);
    } catch {
      schedule();
      return;
    }
    ws.onopen = () => {
      retry = 0;
      onEvent({ event: "_connected" });
    };
    ws.onmessage = (e) => {
      try {
        onEvent(JSON.parse(e.data));
      } catch {
        /* ignore non-JSON */
      }
    };
    ws.onclose = () => {
      onEvent({ event: "_disconnected" });
      schedule();
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* noop */
      }
    };
  };
  const schedule = () => {
    if (stopped) return;
    retry = Math.min(retry + 1, 6);
    setTimeout(connect, 500 * 2 ** (retry - 1)); // 0.5s .. 16s
  };

  connect();
  return () => {
    stopped = true;
    try {
      ws && ws.close();
    } catch {
      /* noop */
    }
  };
}

// {{token}} -> field value, for the live body preview (mirrors llm.render_body)
export function composeBody(template, fields) {
  return (template || "").replace(/\{\{\s*(\w+)\s*\}\}/g, (m, k) => {
    const f = fields.find((x) => x.key === k);
    return f ? f.value : m;
  });
}
