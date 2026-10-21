// Password-protects the whole site (pages, data and API) when SITE_PASSWORD is set.
// Your browser asks once; any username works, the password is SITE_PASSWORD.
export async function onRequest({ request, env, next }) {
  const pw = env.SITE_PASSWORD;
  if (!pw) return next();
  const auth = request.headers.get("authorization") || "";
  if (auth.startsWith("Basic ")) {
    let decoded = "";
    try { decoded = atob(auth.slice(6)); } catch { /* bad header */ }
    const given = decoded.slice(decoded.indexOf(":") + 1);
    if (await same(given, pw)) return next();
  }
  return new Response("Password required", {
    status: 401,
    headers: { "www-authenticate": 'Basic realm="Outreach Directory", charset="UTF-8"', "cache-control": "no-store" },
  });
}

// Constant-time comparison via hashing
async function same(a, b) {
  const enc = new TextEncoder();
  const [x, y] = await Promise.all([a, b].map((s) => crypto.subtle.digest("SHA-256", enc.encode(s))));
  const u = new Uint8Array(x), v = new Uint8Array(y);
  let diff = 0;
  for (let i = 0; i < u.length; i++) diff |= u[i] ^ v[i];
  return diff === 0;
}
