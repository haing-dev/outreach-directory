// Shared helpers for Pages Functions (kept outside /functions so it is not a route).

export function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

// Cloudflare Access puts the signed-in email in this header. If ALLOWED_EMAILS is set
// (comma-separated), only those emails may use the API. Leave it unset for local dev.
export function authorize(request, env) {
  const allowed = (env.ALLOWED_EMAILS || "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (!allowed.length) return null;
  const email = (request.headers.get("cf-access-authenticated-user-email") || "").toLowerCase();
  if (!allowed.includes(email)) return json({ error: "forbidden" }, 403);
  return null;
}
