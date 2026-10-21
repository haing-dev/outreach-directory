// Cloudflare Pages Function: notes API backed by D1 (binding name: DB)
//   GET    /api/notes        -> { notes: { [entry_id]: note } }
//   PUT    /api/notes/:id    -> upsert one note (partial fields allowed)
//   DELETE /api/notes/:id    -> delete one note
import { authorize, json } from "../../../lib/api.js";

const STATUSES = new Set(["none", "emailed", "follow_up", "replied", "meeting", "applied", "interview", "offer", "closed"]);

function rowToNote(r) {
  return {
    entry_id: r.entry_id, status: r.status, priority: r.priority, follow_up: r.follow_up,
    contact: r.contact, tags: safeParse(r.tags, []), body: r.body, log: safeParse(r.log, []),
    updated_at: r.updated_at,
  };
}
function safeParse(s, d) { try { return JSON.parse(s); } catch { return d; } }

export async function onRequest(ctx) {
  const { request, env, params } = ctx;
  const denied = authorize(request, env);
  if (denied) return denied;
  if (!env.DB) return json({ error: "D1 binding 'DB' is not configured" }, 500);

  const id = (params.path || [])[0];

  if (request.method === "GET" && !id) {
    const { results } = await env.DB.prepare("SELECT * FROM notes").all();
    const notes = {};
    for (const r of results) notes[r.entry_id] = rowToNote(r);
    return json({ notes });
  }

  if (!id || !/^[a-z0-9][a-z0-9-]{0,80}$/.test(id)) return json({ error: "bad id" }, 400);

  if (request.method === "DELETE") {
    await env.DB.prepare("DELETE FROM notes WHERE entry_id = ?").bind(id).run();
    return json({ ok: true });
  }

  if (request.method === "PUT") {
    let body;
    try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
    const cur = await env.DB.prepare("SELECT * FROM notes WHERE entry_id = ?").bind(id).first();
    const prev = cur ? rowToNote(cur) : { status: "none", priority: 0, follow_up: null, contact: null, tags: [], body: "", log: [] };
    const n = { ...prev, ...body };
    if (!STATUSES.has(n.status)) n.status = "none";
    n.priority = Math.max(0, Math.min(3, parseInt(n.priority, 10) || 0));
    if (n.follow_up && !/^\d{4}-\d{2}-\d{2}$/.test(n.follow_up)) n.follow_up = null;
    n.tags = Array.isArray(n.tags) ? n.tags.map(String).slice(0, 20) : [];
    n.log = Array.isArray(n.log) ? n.log.slice(0, 200) : [];
    n.body = String(n.body || "").slice(0, 20000);
    n.contact = n.contact ? String(n.contact).slice(0, 200) : null;
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO notes (entry_id, status, priority, follow_up, contact, tags, body, log, updated_at)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
       ON CONFLICT(entry_id) DO UPDATE SET status=?2, priority=?3, follow_up=?4, contact=?5, tags=?6, body=?7, log=?8, updated_at=?9`
    ).bind(id, n.status, n.priority, n.follow_up, n.contact, JSON.stringify(n.tags), n.body, JSON.stringify(n.log), now).run();
    return json({ note: { entry_id: id, ...n, updated_at: now } });
  }

  return json({ error: "method not allowed" }, 405);
}
