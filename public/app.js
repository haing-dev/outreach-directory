/* Outreach Directory - front end (no build step). */
(() => {
  "use strict";

  // ------------------------------------------------------------------ constants
  const CATS = [
    { id: "academic", label: "Academic labs", regions: [["all", "All"], ["US", "US"], ["UK", "UK"], ["Other", "Notable PIs elsewhere"]] },
    { id: "institute", label: "Independent institutes", regions: [["all", "All"], ["US", "US"], ["UK", "UK"]] },
    { id: "bigtech", label: "Big tech AI-bio", regions: null },
    { id: "biotech", label: "Biotech & pharma", regions: null },
    { id: "startup", label: "AI-bio startups", regions: null },
  ];
  const IS_LAB = (e) => e.category === "academic" || e.category === "institute";
  const LAB_STATUSES = [["none", "Not contacted"], ["emailed", "Emailed"], ["follow_up", "Follow-up"], ["replied", "Replied"], ["meeting", "Meeting set"], ["applied", "Applied"], ["closed", "Closed"]];
  const CO_STATUSES = [["none", "To apply"], ["applied", "Applied"], ["interview", "Interview"], ["offer", "Offer"], ["closed", "Closed"]];
  const STATUS_ORDER = { none: 0, emailed: 1, follow_up: 2, replied: 3, meeting: 4, applied: 5, interview: 6, offer: 7, closed: 8 };
  const PSTATUS = { open: "Open", opens_soon: "Opens soon", rolling: "Rolling", closed: "Closed", unknown: "Check page" };
  const TODAY = new Date().toISOString().slice(0, 10);
  const LS = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
               set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } } };

  // ------------------------------------------------------------------ state
  const S = Object.assign({
    view: "directory", cat: "academic", region: "all", q: "", sortKey: "fit", sortDir: -1,
    topic: "", statusFilter: "", openOnly: false, ptype: "", hideClosed: true,
  }, LS.get("od.ui", {}));
  let DATA = { entries: [] }, CHANGES = { weeks: [], suggestions: [] }, NOTES = {}, NOTES_REMOTE = true;
  const byId = (id) => DATA.entries.find((e) => e.id === id);
  const saveUI = () => LS.set("od.ui", { view: S.view, cat: S.cat, region: S.region, sortKey: S.sortKey, sortDir: S.sortDir, hideClosed: S.hideClosed });

  // ------------------------------------------------------------------ utils
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);
  const link = (u, label) => (safeUrl(u) ? `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(label)} ↗</a>` : "");
  const fmtDate = (d) => (d ? new Date(d + (d.length === 10 ? "T00:00:00" : "")).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "");
  const initials = (n) => (n || "?").replace(/^(Dr|Prof)\.?\s+/i, "").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  const $ = (sel, root = document) => root.querySelector(sel);
  const toast = (msg) => { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2600); };
  const note = (id) => NOTES[id] || { status: "none", priority: 0, follow_up: null, contact: null, tags: [], body: "", log: [] };
  const statusesFor = (e) => (IS_LAB(e) ? LAB_STATUSES : CO_STATUSES);
  const isDue = (n) => (n.status === "emailed" || n.status === "follow_up") && n.follow_up && n.follow_up <= TODAY;

  function nextProgram(e) {
    const ps = (e.programs || []).filter((p) => p.status !== "closed");
    const dated = ps.filter((p) => p.deadline && p.deadline >= TODAY).sort((a, b) => a.deadline.localeCompare(b.deadline));
    return dated[0] || ps.find((p) => p.status === "rolling") || ps[0] || null;
  }
  const changedThisWeek = (() => {
    let cache = null;
    return (id) => {
      if (!cache) {
        cache = {};
        for (const it of (CHANGES.weeks?.[0]?.items || [])) (cache[it.entry_id] ||= []).push(it);
      }
      return cache[id] || [];
    };
  })();

  // ------------------------------------------------------------------ notes storage
  async function loadNotes() {
    try {
      const r = await fetch("api/notes", { headers: { accept: "application/json" } });
      if (!r.ok || !(r.headers.get("content-type") || "").includes("json")) throw new Error(r.status);
      NOTES = (await r.json()).notes || {};
      NOTES_REMOTE = true;
    } catch {
      NOTES = LS.get("od.notes", {});
      NOTES_REMOTE = false;
      const b = $("#banner");
      b.hidden = false;
      b.textContent = "Notes API not connected: notes are saved in this browser only. (Set up the D1 database to sync them; see README.)";
    }
  }
  const pending = {};
  function saveNote(id, patch, { quiet = false } = {}) {
    NOTES[id] = { ...note(id), ...patch, entry_id: id };
    if (!NOTES_REMOTE) { LS.set("od.notes", NOTES); if (!quiet) toast("Saved"); return; }
    clearTimeout(pending[id]);
    pending[id] = setTimeout(async () => {
      try {
        const r = await fetch(`api/notes/${encodeURIComponent(id)}`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(NOTES[id]) });
        if (!r.ok) throw new Error(r.status);
        if (!quiet) toast("Saved");
      } catch (err) { toast("Could not save note (" + err.message + ")"); }
    }, 400);
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    const [d, c] = await Promise.all([
      fetch("data/directory.json", { cache: "no-store" }).then((r) => r.json()).catch(() => ({ entries: [] })),
      fetch("data/changes.json", { cache: "no-store" }).then((r) => r.json()).catch(() => ({ weeks: [] })),
    ]);
    DATA = d; CHANGES = c;
    await loadNotes();
    const gen = DATA.generated_at ? new Date(DATA.generated_at) : null;
    const next = gen ? new Date(gen.getTime() + 7 * 864e5) : null;
    $("#refreshInfo").innerHTML = gen
      ? `<i></i>Refreshed ${gen.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} · next ~${next.toLocaleDateString(undefined, { day: "numeric", month: "short" })}`
      : "<i style='background:#c90'></i>Not refreshed yet";
    const n = CHANGES.weeks?.[0]?.items?.length || 0;
    if (n) { $("#newsCount").hidden = false; $("#newsCount").textContent = n; }
    $("#nav").addEventListener("click", (ev) => { const a = ev.target.closest("a[data-view]"); if (a) { S.view = a.dataset.view; saveUI(); render(); } });
    $("#refreshNow").addEventListener("click", refreshNow);
    $("#scrim").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeDrawer(); });
    const hash = location.hash.slice(1);
    render();
    if (hash && byId(hash)) openDrawer(hash);
  }

  async function refreshNow() {
    if (!confirm("Start a refresh now? It takes about 20-40 minutes and uses your Claude token.")) return;
    try {
      const r = await fetch("api/refresh", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
      const j = await r.json().catch(() => ({}));
      toast(j.message || j.error || `Status ${r.status}`);
    } catch (e) { toast("Refresh endpoint not available here"); }
  }

  // ------------------------------------------------------------------ render root
  function render() {
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.view === S.view));
    const app = $("#app");
    if (S.view === "programs") app.innerHTML = renderPrograms();
    else if (S.view === "outreach") app.innerHTML = renderOutreach();
    else if (S.view === "news") app.innerHTML = renderNews();
    else app.innerHTML = renderDirectory();
    bind(app);
  }

  // ------------------------------------------------------------------ directory
  function filtered() {
    const q = S.q.trim().toLowerCase();
    return DATA.entries.filter((e) => {
      if (e.category !== S.cat) return false;
      if (S.region !== "all" && CATS.find((c) => c.id === S.cat).regions && e.region !== S.region) return false;
      if (S.topic && !(e.topics || []).includes(S.topic)) return false;
      const n = note(e.id);
      if (S.statusFilter === "due" ? !isDue(n) : S.statusFilter && n.status !== S.statusFilter) return false;
      if (S.openOnly && !(e.programs || []).some((p) => p.status === "open" || p.status === "rolling")) return false;
      if (q) {
        const hay = [e.name, e.pi, e.institution, e.summary, e.department, ...(e.topics || []),
          ...(e.people || []).map((p) => p.name), ...(e.contacts || []).map((c) => c.name + " " + c.focus),
          ...(e.programs || []).map((p) => p.name), n.body, (n.tags || []).join(" ")].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  const SORTS = {
    fit: ["Fit with my research", (e) => e.fit?.score ?? 0],
    rank: ["Rank", (e) => -(e.rank ?? 99)],
    deadline: ["Next deadline", (e) => { const p = nextProgram(e); return p?.deadline ? -Date.parse(p.deadline) : p?.status === "rolling" ? -9e15 : -1e16; }],
    priority: ["My priority", (e) => note(e.id).priority || 0],
    status: ["Outreach status", (e) => -(STATUS_ORDER[note(e.id).status] ?? 0)],
    updated: ["Recently updated", (e) => (e.refreshed_at ? Date.parse(e.refreshed_at) : 0)],
    name: ["Name A→Z", (e) => e.name.toLowerCase()],
    institution: ["Institution A→Z", (e) => (e.institution || "").toLowerCase()],
  };
  function sorted(list) {
    const [, fn] = SORTS[S.sortKey] || SORTS.fit;
    const dir = S.sortDir;
    return [...list].sort((a, b) => {
      const x = fn(a), y = fn(b);
      if (typeof x === "string") return dir * x.localeCompare(y);
      return dir * (x - y) || a.name.localeCompare(b.name);
    });
  }

  function renderDirectory() {
    const cat = CATS.find((c) => c.id === S.cat);
    const counts = Object.fromEntries(CATS.map((c) => [c.id, DATA.entries.filter((e) => e.category === c.id).length]));
    const topics = [...new Set(DATA.entries.filter((e) => e.category === S.cat).flatMap((e) => e.topics || []))].sort();
    const rows = sorted(filtered());
    const statuses = S.cat === "academic" || S.cat === "institute" ? LAB_STATUSES : CO_STATUSES;
    const lab = S.cat === "academic" || S.cat === "institute";

    const th = (key, label) => key
      ? `<th class="sortable" data-sort="${key}">${label}${S.sortKey === key ? `<span class="arrow">${S.sortDir === -1 ? "↓" : "↑"}</span>` : ""}</th>`
      : `<th>${label}</th>`;
    const head = lab
      ? [th("priority", "Priority"), th("name", S.cat === "academic" ? "Lab · PI" : "Institute"), th("institution", S.cat === "academic" ? "University" : "Region"),
         th("fit", "Fit · focus"), th("deadline", "Programs"), th(null, "Contact"), th("status", "Status"), th(null, "My notes"), th("updated", "Updated")]
      : [th("rank", "#"), th("priority", "Priority"), th("name", "Company"), th(null, "Leads"), th("fit", "What they do"),
         th("deadline", "Programs"), th("status", "Status"), th(null, "My notes"), th("updated", "Updated")];

    const body = rows.map((e) => {
      const n = note(e.id);
      const p = nextProgram(e);
      const ch = changedThisWeek(e.id);
      const prog = p ? `<div>${esc(p.name)}</div><div class="t2 ${p.status}">${p.deadline ? "Due " + fmtDate(p.deadline) : esc(p.deadline_text || PSTATUS[p.status])}</div>${(e.programs.length > 1) ? `<div class="t2">+${e.programs.length - 1} more</div>` : ""}`
        : `<span class="faint">${e.refreshed_at ? "None listed" : "-"}</span>`;
      const best = (e.contacts || []).find((c) => c.best) || (e.contacts || [])[0];
      const statusSel = `<select class="status s-${isDue(n) ? "due" : n.status}" data-status="${e.id}">${statusesFor(e).map(([v, l]) => `<option value="${v}" ${n.status === v ? "selected" : ""}>${isDue(n) && n.status === v ? "Follow-up due" : l}</option>`).join("")}</select>`;
      const notesCell = `<textarea class="inline-note" data-note="${e.id}" placeholder="+ Add note">${esc(n.body)}</textarea>`;
      const upd = `${ch.length ? `<span class="new">${esc(ch[0].type.replace(/_/g, " "))}</span>` : ""}${e.error ? `<span class="new warn">refresh failed</span>` : ""}<div class="t2">${e.refreshed_at ? fmtDate(e.refreshed_at.slice(0, 10)) : "pending"}</div>`;
      const stars = `<span class="stars" data-stars="${e.id}">${"★".repeat(n.priority || 0)}<span class="o">${"★".repeat(3 - (n.priority || 0))}</span></span>`;
      const fit = e.fit?.score ? `<span class="chip" style="border-color:transparent;background:var(--accent-soft);color:var(--accent)">Fit: ${["", "Low", "Med", "High"][e.fit.score]}</span>` : "";
      const tags = (e.topics || []).slice(0, 3).map((t) => `<span class="chip">${esc(t)}</span>`).join("");
      if (lab) {
        const name = S.cat === "academic"
          ? `<div class="t1">${esc(e.name)}</div><div class="t2">${esc(e.pi || (e.people?.[0]?.name) || "")}${e.department ? " · " + esc(e.department) : ""}</div>`
          : `<div class="t1">${esc(e.name)}</div><div class="t2">${esc(e.location || "")}</div>`;
        return `<tr data-open="${e.id}"><td>${stars}</td><td>${name}</td><td>${esc(S.cat === "academic" ? e.institution : e.region)}</td>
          <td>${fit}${tags}</td><td>${prog}</td>
          <td>${best ? `<div class="t1">${esc(best.name)}</div><div class="t2">${esc(best.role || "")}</div>` : '<span class="faint">-</span>'}</td>
          <td>${statusSel}</td><td>${notesCell}</td><td>${upd}</td></tr>`;
      }
      return `<tr data-open="${e.id}"><td class="t1">${e.rank ?? ""}</td><td>${stars}</td><td><div class="t1">${esc(e.name)}</div><div class="t2">${esc(e.location || "")}</div></td>
        <td style="max-width:220px">${(e.people || []).slice(0, 3).map((p) => `<div>${esc(p.name)} <span class="t2">${esc(p.role || "")}</span></div>`).join("") || '<span class="faint">-</span>'}</td>
        <td style="max-width:300px">${fit}<div class="t2">${esc(e.summary || "")}</div></td><td>${prog}</td><td>${statusSel}</td><td>${notesCell}</td><td>${upd}</td></tr>`;
    }).join("");

    return `<div class="panel">
      <div class="subtabs">
        ${CATS.map((c) => `<button class="tab ${c.id === S.cat ? "on" : ""}" data-cat="${c.id}">${c.label} <span class="ct">${counts[c.id]}</span></button>`).join("")}
        ${cat.regions ? `<div class="seg">${cat.regions.map(([v, l]) => `<button class="${S.region === v ? "on" : ""}" data-region="${v}">${l}</button>`).join("")}</div>` : ""}
      </div>
      <div class="toolbar">
        <input class="search" id="q" placeholder="Search names, people, topics, programs, your notes…" value="${esc(S.q)}">
        <select class="btn" id="sortSel">${Object.entries(SORTS).filter(([k]) => lab ? k !== "rank" : true).map(([k, [l]]) => `<option value="${k}" ${S.sortKey === k ? "selected" : ""}>Sort: ${l}</option>`).join("")}</select>
        <select class="btn" id="topicSel"><option value="">All topics</option>${topics.map((t) => `<option ${S.topic === t ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
        <select class="btn" id="statusSel"><option value="">Any status</option><option value="due" ${S.statusFilter === "due" ? "selected" : ""}>Follow-up due</option>${statuses.map(([v, l]) => `<option value="${v}" ${S.statusFilter === v ? "selected" : ""}>${l}</option>`).join("")}</select>
        <label class="check"><input type="checkbox" id="openOnly" ${S.openOnly ? "checked" : ""}> Has open program</label>
        <div class="grow"></div>
        <span class="faint">${rows.length} shown</span>
        <button class="btn" id="csv">Export CSV</button>
      </div>
      <div class="tablewrap">${rows.length ? `<table><thead><tr>${head.join("")}</tr></thead><tbody>${body}</tbody></table>` : `<div class="empty">Nothing matches these filters.</div>`}</div>
    </div>`;
  }

  // ------------------------------------------------------------------ programs view
  function allPrograms() {
    return DATA.entries.flatMap((e) => (e.programs || []).map((p) => ({ ...p, e })));
  }
  function renderPrograms() {
    let ps = allPrograms();
    const types = [...new Set(ps.map((p) => p.type))].sort();
    if (S.ptype) ps = ps.filter((p) => p.type === S.ptype);
    if (S.hideClosed) ps = ps.filter((p) => p.status !== "closed");
    const key = (p) => (p.deadline && p.deadline >= TODAY ? "0" + p.deadline : p.status === "opens_soon" ? "1" : p.status === "rolling" ? "2" : "3");
    ps.sort((a, b) => key(a).localeCompare(key(b)) || a.e.name.localeCompare(b.e.name));
    const catLabel = (c) => CATS.find((x) => x.id === c)?.label || c;
    return `<div class="panel">
      <div class="toolbar">
        <select class="btn" id="ptype"><option value="">All types (${allPrograms().length})</option>${types.map((t) => `<option ${S.ptype === t ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
        <label class="check"><input type="checkbox" id="hideClosed" ${S.hideClosed ? "checked" : ""}> Hide closed</label>
        <div class="grow"></div>
        <span class="faint">${ps.length} programs · sorted by deadline</span>
        <button class="btn" id="ics">Export deadlines (.ics)</button>
      </div>
      <div class="tablewrap">${ps.length ? `<table><thead><tr><th>Deadline</th><th>Program</th><th>Organization</th><th>Category</th><th>Type</th><th>Eligibility</th><th>Status</th><th>Link</th></tr></thead><tbody>
        ${ps.map((p) => `<tr data-open="${p.e.id}"><td class="t1">${p.deadline ? fmtDate(p.deadline) : `<span class="${p.status}">${esc(p.deadline_text || PSTATUS[p.status])}</span>`}</td>
          <td>${esc(p.name)}</td><td>${esc(p.e.name)}<div class="t2">${esc(p.e.institution !== p.e.name ? p.e.institution : "")}</div></td>
          <td>${esc(catLabel(p.e.category))}${p.e.region ? " · " + esc(p.e.region) : ""}</td><td>${esc(p.type)}</td><td style="max-width:280px">${esc(p.eligibility || "")}</td>
          <td class="${p.status}">${PSTATUS[p.status] || ""}</td><td>${link(p.url, "Open")}</td></tr>`).join("")}
      </tbody></table>` : `<div class="empty">No programs yet. They appear after the first refresh.</div>`}</div></div>`;
  }

  function exportIcs() {
    const ps = allPrograms().filter((p) => p.deadline && p.deadline >= TODAY);
    const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//outreach-directory//EN"];
    for (const p of ps) {
      const d = p.deadline.replace(/-/g, "");
      lines.push("BEGIN:VEVENT", `UID:${p.e.id}-${d}-${p.name.replace(/\W+/g, "").slice(0, 20)}@outreach`, `DTSTAMP:${TODAY.replace(/-/g, "")}T000000Z`,
        `DTSTART;VALUE=DATE:${d}`, `SUMMARY:Deadline: ${p.name} (${p.e.name})`.replace(/[,;]/g, " "), `DESCRIPTION:${(p.url || "").replace(/[,;]/g, " ")}`,
        "BEGIN:VALARM", "TRIGGER:-P7D", "ACTION:DISPLAY", "DESCRIPTION:Deadline in 7 days", "END:VALARM", "END:VEVENT");
    }
    lines.push("END:VCALENDAR");
    download("deadlines.ics", lines.join("\r\n"), "text/calendar");
  }

  // ------------------------------------------------------------------ outreach board
  function renderOutreach() {
    const tracked = DATA.entries.filter((e) => { const n = note(e.id); return n.status !== "none" || n.priority > 0 || n.body; });
    const cols = [
      ["To contact", (e, n) => n.status === "none"],
      ["Emailed", (e, n) => n.status === "emailed" && !isDue(n)],
      ["Follow-up due", (e, n) => isDue(n) || n.status === "follow_up"],
      ["Replied", (e, n) => n.status === "replied"],
      ["Meeting / interview", (e, n) => n.status === "meeting" || n.status === "interview"],
      ["Applied / offer", (e, n) => n.status === "applied" || n.status === "offer"],
    ];
    const contacted = tracked.filter((e) => !["none"].includes(note(e.id).status)).length;
    const replied = tracked.filter((e) => ["replied", "meeting", "interview", "offer"].includes(note(e.id).status)).length;
    const upcoming = DATA.entries.map((e) => ({ e, n: note(e.id) })).filter(({ n }) => n.follow_up && n.follow_up >= TODAY)
      .sort((a, b) => a.n.follow_up.localeCompare(b.n.follow_up)).slice(0, 8);
    return `
      <div class="stats">
        <div class="stat"><span>Tracked</span><b>${tracked.length}</b></div>
        <div class="stat"><span>Contacted</span><b>${contacted}</b></div>
        <div class="stat"><span>Reply rate</span><b>${contacted ? Math.round((100 * replied) / contacted) + "%" : "-"}</b></div>
        <div class="stat"><span>Applications</span><b>${tracked.filter((e) => ["applied", "interview", "offer"].includes(note(e.id).status)).length}</b></div>
        <div class="stat" style="background:var(--amber-soft)"><span style="color:var(--amber)">Follow-ups due</span><b style="color:var(--amber)">${tracked.filter((e) => isDue(note(e.id))).length}</b></div>
      </div>
      ${tracked.length ? "" : `<div class="panel empty">Nothing tracked yet. Star an entry, set a status or write a note in the Directory and it shows up here.</div>`}
      <div class="board">${cols.map(([title, fn]) => {
        const items = tracked.filter((e) => fn(e, note(e.id)));
        return `<div class="col"><h3>${title}<span class="ct">${items.length}</span></h3>${items.map((e) => { const n = note(e.id); return `
          <div class="bcard" data-open="${e.id}"><div class="t1">${esc(e.name)}</div><div class="t2">${esc(e.institution !== e.name ? e.institution : CATS.find((c) => c.id === e.category).label)}${n.contact ? " · " + esc(n.contact) : ""}</div>
          ${n.follow_up ? `<div class="t2">Follow up ${fmtDate(n.follow_up)}</div>` : ""}${n.body ? `<div class="note">${esc(n.body.slice(0, 90))}${n.body.length > 90 ? "…" : ""}</div>` : ""}</div>`; }).join("")}</div>`;
      }).join("")}</div>
      ${upcoming.length ? `<div class="panel" style="margin-top:14px"><div class="toolbar"><b>Upcoming follow-ups</b></div>${upcoming.map(({ e, n }) => `<div class="newsitem" data-open="${e.id}" style="cursor:pointer"><b style="width:110px">${fmtDate(n.follow_up)}</b><span>${esc(e.name)}${n.contact ? " · " + esc(n.contact) : ""}</span></div>`).join("")}</div>` : ""}`;
  }

  // ------------------------------------------------------------------ what's new
  function renderNews() {
    const weeks = CHANGES.weeks || [];
    const sug = CHANGES.suggestions || [];
    const label = (t) => t.replace(/_/g, " ");
    return `${weeks.length ? weeks.map((w, i) => `<div class="panel week" style="margin-bottom:14px">
        <h2>Week of ${fmtDate(w.week)}<span class="faint" style="font-size:12px;font-weight:400">${w.checked ?? ""} checked · ${w.items.length} changes</span></h2>
        ${w.items.length ? w.items.map((it) => `<div class="newsitem"><span class="new ${it.type === "needs_review" ? "warn" : ""}">${esc(label(it.type))}</span>
          <div><a data-open="${esc(it.entry_id)}" style="cursor:pointer" class="t1">${esc(it.entry_name)}</a><div class="t2">${esc(it.text)} ${link(it.url, "source")}</div></div></div>`).join("")
        : `<div class="empty">No changes this week.</div>`}</div>`).join("")
      : `<div class="panel empty">The change log starts after the first weekly refresh.</div>`}
      ${sug.length ? `<div class="panel week"><h2>Suggested additions<span class="faint" style="font-size:12px;font-weight:400">copy into config/seeds.yaml to add</span></h2>
        ${sug.map((s) => `<div class="newsitem"><span class="chip">${esc(s.institution)}</span><div><span class="t1">${esc(s.pi)}</span> ${esc(s.lab_name ? "· " + s.lab_name : "")} ${link(s.url, "site")}<div class="t2">${esc(s.department || "")} ${esc(s.why || "")}</div></div></div>`).join("")}</div>` : ""}`;
  }

  // ------------------------------------------------------------------ drawer (detail)
  function openDrawer(id) {
    const e = byId(id);
    if (!e) return;
    history.replaceState(null, "", "#" + id);
    const n = note(id);
    const lab = IS_LAB(e);
    const crumb = [CATS.find((c) => c.id === e.category).label, e.region, e.category === "academic" ? e.institution : null].filter(Boolean).join(" › ");
    const people = (e.people || []).map((p) => `<div class="person"><div class="ini">${esc(initials(p.name))}</div><div>
      <div class="t1">${esc(p.name)}</div><div class="t2">${esc(p.role || "")}</div>${p.background ? `<div class="t2">${esc(p.background)}</div>` : ""}
      <div class="links">${(p.links || []).map((l) => link(l.url, l.label)).join("")}</div></div></div>`).join("") || '<span class="faint">Filled in by the next refresh.</span>';
    const papers = (e.recent_papers || []).length ? `<div class="sect" style="margin-top:10px">Recent work (talking points)</div>${e.recent_papers.map((p) => `<div style="font-size:12px;margin-bottom:3px">• ${safeUrl(p.url) ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)} <span class="faint">${esc([p.venue, p.year].filter(Boolean).join(", "))}</span></div>`).join("")}` : "";
    const programs = (e.programs || []).length ? `<table><thead><tr><th>Program</th><th>Type</th><th>Who it's for</th><th>Deadline</th><th>Status</th><th>Link</th></tr></thead><tbody>
      ${e.programs.map((p) => `<tr style="cursor:default"><td class="t1">${esc(p.name)}</td><td>${esc(p.type)}</td><td>${esc(p.eligibility || "")}</td><td>${p.deadline ? fmtDate(p.deadline) : esc(p.deadline_text || "")}</td><td class="${p.status}">${PSTATUS[p.status]}</td><td>${link(p.url, "Open")}</td></tr>`).join("")}</tbody></table>`
      : `<div class="faint" style="padding:0 13px 10px">No programs found${e.refreshed_at ? "" : " yet"}.</div>`;
    const contacts = lab ? `<div class="sect" style="margin-top:16px">Who to reach out to · PhD students &amp; postdocs <span style="text-transform:none;letter-spacing:0;font-weight:400"> (public members page; profile links only)</span></div>
      <div class="contacts">${(e.contacts || []).map((c) => `<div class="card contact ${c.best ? "best" : ""}"><div class="t1">${esc(c.name)} ${c.best ? '<span class="new" style="background:var(--accent-soft);color:var(--accent)">best first contact</span>' : ""}</div>
        <div class="t2">${esc(c.role || "")}</div><div style="font-size:12px;margin-top:6px"><b>Works on:</b> ${esc(c.focus || "")}</div>
        <div style="font-size:12px"><b>Opener:</b> ${esc(c.opener || "")}</div><div class="links">${link(c.profile_url, "Profile")}<a href="#" data-contact="${esc(c.name)}">Set as my contact</a></div></div>`).join("") || '<span class="faint">None found yet.</span>'}</div>` : "";
    const statusOpts = statusesFor(e).map(([v, l]) => `<option value="${v}" ${n.status === v ? "selected" : ""}>${l}</option>`).join("");

    const d = $("#drawer");
    d.innerHTML = `<div class="main" style="position:relative">
        <button class="btn close" data-close>Close ✕</button>
        <div class="crumb">${esc(crumb)}</div>
        <h1>${esc(e.name)}</h1>
        <div class="muted">${esc([e.pi && e.category === "academic" ? "PI: " + e.pi : null, e.current_institution && e.current_institution !== e.institution ? e.current_institution : null, e.department, e.location].filter(Boolean).join(" · "))}</div>
        <div class="links">${link(e.website, "Website")}${(e.sources || []).slice(0, 1).map((s) => link(s.url, "Main source")).join("")}</div>
        ${(e.flags || []).map((f) => `<div class="flag">${esc(f)}</div>`).join("")}
        ${e.error ? `<div class="flag" style="background:var(--red-soft);color:var(--red)">Last refresh failed; showing older data. (${esc(e.error)})</div>` : ""}
        <div class="grid2">
          <div class="card"><div class="sect">What they do</div><div>${esc(e.summary || "Filled in by the next refresh.")}</div>
            <div style="margin-top:8px">${(e.topics || []).map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</div>
            ${e.fit?.score ? `<div class="fitbox"><b>Fit: ${["", "Low", "Medium", "High"][e.fit.score]}.</b> ${esc(e.fit.reason || "")}</div>` : ""}</div>
          <div class="card"><div class="sect">${lab ? "Key people" : "Founders & team leads"}</div>${people}${papers}</div>
        </div>
        <div class="card" style="margin-top:12px;padding:12px 0 0"><div class="sect" style="padding:0 13px">Programs &amp; opportunities</div><div class="tablewrap">${programs}</div></div>
        ${contacts}
        ${(e.sources || []).length ? `<div class="sect" style="margin-top:16px">Sources</div><div style="font-size:12px;display:flex;flex-direction:column;gap:2px">${e.sources.map((s) => link(s.url, s.title || s.url)).join("")}</div>` : ""}
        <div class="t2" style="margin-top:12px">${e.refreshed_at ? "Refreshed " + new Date(e.refreshed_at).toLocaleString() : "Not refreshed yet"}</div>
      </div>
      <div class="side">
        <div class="sect" style="margin:0">My notes &amp; tracking</div>
        <div><label>Status</label><select id="dStatus">${statusOpts}</select></div>
        <div><label>Priority</label><span class="stars" data-stars="${e.id}" style="font-size:18px">${"★".repeat(n.priority || 0)}<span class="o">${"★".repeat(3 - (n.priority || 0))}</span></span></div>
        <div><label>Follow up on</label><input type="date" id="dFollow" value="${esc(n.follow_up || "")}"></div>
        <div><label>${lab ? "Who I'm contacting" : "Recruiter / referral"}</label><input id="dContact" value="${esc(n.contact || "")}"></div>
        <div><label>Tags (comma separated)</label><input id="dTags" value="${esc((n.tags || []).join(", "))}"></div>
        <div><label>Notes (auto-saves; never touched by the refresh)</label><textarea id="dBody">${esc(n.body)}</textarea></div>
        <div><label>Contact log</label><div style="display:flex;gap:6px"><input id="dLog" placeholder="e.g. Emailed R. Sample"><button class="btn primary" id="dLogAdd">Add</button></div></div>
        <div style="display:flex;flex-direction:column;gap:6px">${(n.log || []).map((l, i) => `<div class="logitem"><b>${fmtDate(l.date)}</b><span>${esc(l.text)}</span><button data-dellog="${i}" title="Delete">✕</button></div>`).join("")}</div>
      </div>`;
    d.hidden = false; $("#scrim").hidden = false;
    d.scrollTop = 0;

    const upd = (patch, quiet = true) => { saveNote(id, patch, { quiet }); render(); };
    $("#dStatus", d).onchange = (ev) => upd({ status: ev.target.value }, false);
    $("#dFollow", d).onchange = (ev) => upd({ follow_up: ev.target.value || null }, false);
    $("#dContact", d).onchange = (ev) => upd({ contact: ev.target.value.trim() || null }, false);
    $("#dTags", d).onchange = (ev) => upd({ tags: ev.target.value.split(",").map((t) => t.trim()).filter(Boolean) }, false);
    $("#dBody", d).oninput = (ev) => saveNote(id, { body: ev.target.value }, { quiet: true });
    $("#dBody", d).onchange = () => { render(); toast("Saved"); };
    const addLog = () => {
      const v = $("#dLog", d).value.trim(); if (!v) return;
      saveNote(id, { log: [{ date: TODAY, text: v }, ...(note(id).log || [])] }); render(); openDrawer(id);
    };
    $("#dLogAdd", d).onclick = addLog;
    $("#dLog", d).onkeydown = (ev) => { if (ev.key === "Enter") addLog(); };
    d.onclick = (ev) => {
      if (ev.target.closest("[data-close]")) return closeDrawer();
      const del = ev.target.closest("[data-dellog]");
      if (del) { const log = [...(note(id).log || [])]; log.splice(+del.dataset.dellog, 1); saveNote(id, { log }); render(); return openDrawer(id); }
      const st = ev.target.closest("[data-stars]");
      if (st) { const p = (note(id).priority || 0) % 3 + 1; saveNote(id, { priority: note(id).priority === 3 ? 0 : p }); render(); return openDrawer(id); }
      const ct = ev.target.closest("[data-contact]");
      if (ct) { ev.preventDefault(); saveNote(id, { contact: ct.dataset.contact }); $("#dContact", d).value = ct.dataset.contact; render(); }
    };
  }
  function closeDrawer() {
    $("#drawer").hidden = true; $("#scrim").hidden = true;
    history.replaceState(null, "", location.pathname + location.search);
  }

  // ------------------------------------------------------------------ events
  function bind(root) {
    root.onclick = (ev) => {
      const t = ev.target;
      if (t.closest("select, textarea, input, a[href], label")) return;
      const cat = t.closest("[data-cat]");
      if (cat) { S.cat = cat.dataset.cat; S.region = "all"; S.topic = ""; S.statusFilter = ""; if (!["academic", "institute"].includes(S.cat) && S.sortKey === "fit") { S.sortKey = "rank"; S.sortDir = -1; } saveUI(); return render(); }
      const reg = t.closest("[data-region]");
      if (reg) { S.region = reg.dataset.region; saveUI(); return render(); }
      const sort = t.closest("th[data-sort]");
      if (sort) { const k = sort.dataset.sort; if (S.sortKey === k) S.sortDir *= -1; else { S.sortKey = k; S.sortDir = ["name", "institution"].includes(k) ? 1 : -1; } saveUI(); return render(); }
      const st = t.closest("[data-stars]");
      if (st) { const id = st.dataset.stars; const cur = note(id).priority || 0; saveNote(id, { priority: cur === 3 ? 0 : cur + 1 }, { quiet: true }); return render(); }
      if (t.id === "csv") return exportCsv();
      if (t.id === "ics") return exportIcs();
      const open = t.closest("[data-open]");
      if (open) openDrawer(open.dataset.open);
    };
    root.onchange = (ev) => {
      const t = ev.target;
      if (t.dataset.status) { saveNote(t.dataset.status, { status: t.value }); return render(); }
      if (t.dataset.note) { saveNote(t.dataset.note, { body: t.value }); return; }
      if (t.id === "sortSel") { S.sortKey = t.value; S.sortDir = ["name", "institution"].includes(t.value) ? 1 : -1; saveUI(); return render(); }
      if (t.id === "topicSel") { S.topic = t.value; return render(); }
      if (t.id === "statusSel") { S.statusFilter = t.value; return render(); }
      if (t.id === "openOnly") { S.openOnly = t.checked; return render(); }
      if (t.id === "ptype") { S.ptype = t.value; return render(); }
      if (t.id === "hideClosed") { S.hideClosed = t.checked; saveUI(); return render(); }
    };
    const q = $("#q", root);
    if (q) q.oninput = () => { S.q = q.value; const pos = q.selectionStart; render(); const q2 = $("#q"); q2.focus(); q2.setSelectionRange(pos, pos); };
  }

  // ------------------------------------------------------------------ export
  function download(name, text, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type }));
    a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  function exportCsv() {
    const rows = sorted(filtered());
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const head = ["Name", "Category", "Region", "Institution", "PI / leads", "Summary", "Topics", "Fit", "Next program", "Deadline", "Program link", "Suggested contact", "Status", "Priority", "Follow up", "My notes", "Website"];
    const lines = [head.map(q).join(",")];
    for (const e of rows) {
      const n = note(e.id), p = nextProgram(e), c = (e.contacts || []).find((x) => x.best) || (e.contacts || [])[0];
      lines.push([e.name, e.category, e.region, e.institution, e.pi || (e.people || []).map((x) => x.name).join("; "), e.summary, (e.topics || []).join("; "),
        e.fit?.score, p?.name, p?.deadline || p?.deadline_text, p?.url, c ? `${c.name} (${c.role})` : "", n.status, n.priority, n.follow_up, n.body, e.website].map(q).join(","));
    }
    download(`outreach-${S.cat}-${TODAY}.csv`, lines.join("\n"), "text/csv");
  }

  boot();
})();
