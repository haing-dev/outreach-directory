// POST /api/refresh -> starts the GitHub Actions refresh workflow ("Refresh now" button).
// Needs env vars: GITHUB_TOKEN (fine-grained token, Actions: read & write on this repo)
//                 GITHUB_REPO  (e.g. "yourname/outreach-directory")
import { authorize, json } from "../../lib/api.js";

export async function onRequestPost({ request, env }) {
  const denied = authorize(request, env);
  if (denied) return denied;
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) {
    return json({ error: "Set GITHUB_TOKEN and GITHUB_REPO in Cloudflare Pages settings to enable this button." }, 501);
  }
  let body = {};
  try { body = await request.json(); } catch { /* empty body ok */ }
  const inputs = {};
  if (body.only) inputs.only = String(body.only).slice(0, 500);
  if (body.discover) inputs.discover = "true";

  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/refresh.yml/dispatches`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "user-agent": "outreach-directory",
    },
    body: JSON.stringify({ ref: env.GITHUB_BRANCH || "main", inputs }),
  });
  if (r.status === 204) return json({ ok: true, message: "Refresh started. The site updates in ~20-40 minutes." });
  return json({ error: `GitHub returned ${r.status}: ${(await r.text()).slice(0, 300)}` }, 502);
}
