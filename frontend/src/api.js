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

// {{token}} -> field value, for the live body preview (mirrors llm.render_body)
export function composeBody(template, fields) {
  return (template || "").replace(/\{\{\s*(\w+)\s*\}\}/g, (m, k) => {
    const f = fields.find((x) => x.key === k);
    return f ? f.value : m;
  });
}
