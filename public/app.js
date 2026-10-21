/* Outreach Directory - front end (no build step). */
(() => {
  "use strict";

  const GROUPS = [
    { id: "academic", label: "Academic labs", lab: true },
    { id: "institute", label: "Independent institutes", lab: true },
    { id: "bigtech", label: "Big tech", lab: false },
    { id: "biotech", label: "Biotech & pharma", lab: false },
    { id: "startup", label: "Startups", lab: false },
  ];
  const PSTATUS = { open: "Open", opens_soon: "Opens soon", rolling: "Rolling", closed: "Closed", unknown: "Check page" };
  const TODAY = new Date().toISOString().slice(0, 10);
  const LS = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
               set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } } };

  const S = Object.assign({ view: "directory", group: "academic", q: "", decision: "all", inst: "", sortKey: "score", sortDir: -1, hideClosed: true },
    LS.get("od.ui2", {}));
  let DATA = { entries: [] }, CHANGES = { weeks: [] }, NOTES = {}, REMOTE = true;
  const saveUI = () => LS.set("od.ui2", { view: S.view, group: S.group, sortKey: S.sortKey, sortDir: S.sortDir, hideClosed: S.hideClosed });

  // ------------------------------------------------------------------ utils
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const okUrl = (u) => typeof u === "string" && /^https?:\/\//i.test(u);
  const link = (u, label, cls = "") => (okUrl(u) ? `<a class="${cls}" href="${esc(u)}" target="_blank" rel="noopener">${esc(label)}</a>` : `<span class="${cls}">${esc(label)}</span>`);
  const fmtDate = (d) => (d ? new Date(d.length === 10 ? d + "T00:00:00" : d).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "");
  const $ = (s, r = document) => r.querySelector(s);
  const toast = (m) => { const t = $("#toast"); t.textContent = m; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 2200); };
  const note = (id) => NOTES[id] || { decision: null, body: "" };
  const group = (id) => GROUPS.find((g) => g.id === id);
  const byId = (id) => DATA.entries.find((e) => e.id === id);

  // ------------------------------------------------------------------ notes
  async function loadNotes() {
    try {
      const r = await fetch("api/notes", { headers: { accept: "application/json" } });
      if (!r.ok || !(r.headers.get("content-type") || "").includes("json")) throw new Error(r.status);
      NOTES = (await r.json()).notes || {};
    } catch {
      REMOTE = false;
      NOTES = LS.get("od.notes", {});
      const b = $("#banner"); b.hidden = false;
      b.textContent = "Notes are saved in this browser only (the notes database isn't connected).";
    }
  }
  const timers = {};
  function saveNote(id, patch, quiet = true) {
    NOTES[id] = { ...note(id), ...patch, entry_id: id };
    if (!REMOTE) { LS.set("od.notes", NOTES); return; }
    clearTimeout(timers[id]);
    timers[id] = setTimeout(async () => {
      try {
        const r = await fetch(`api/notes/${encodeURIComponent(id)}`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(NOTES[id]) });
        if (!r.ok) throw new Error(r.status);
        if (!quiet) toast("Saved");
      } catch (e) { toast("Couldn't save (" + e.message + ")"); }
    }, 350);
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    const [d, c] = await Promise.all([
      fetch("data/directory.json", { cache: "no-store" }).then((r) => r.json()).catch(() => ({ entries: [] })),
      fetch("data/changes.json", { cache: "no-store" }).then((r) => r.json()).catch(() => ({ weeks: [] })),
    ]);
    DATA = d; CHANGES = c;
    await loadNotes();
    const last = DATA.entries.map((e) => e.refreshed_at).filter(Boolean).sort().pop();
    $("#refreshInfo").textContent = last ? `Updated ${fmtDate(last.slice(0, 10))}` : "Not refreshed yet";
    const n = CHANGES.weeks?.[0]?.items?.length || 0;
    if (n) { $("#newsCount").hidden = false; $("#newsCount").textContent = n; }
    $("#nav").onclick = (e) => { const a = e.target.closest("[data-view]"); if (a) { S.view = a.dataset.view; saveUI(); render(); } };
    $("#scrim").onclick = closeDrawer;
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
    render();
    const h = location.hash.slice(1);
    if (h && byId(h)) openDrawer(h);
  }

  function render() {
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.view === S.view));
    const app = $("#app");
    app.innerHTML = S.view === "programs" ? viewPrograms() : S.view === "news" ? viewNews() : viewDirectory();
    bind(app);
  }

  // ------------------------------------------------------------------ shared cells
  const scoreCell = (e) => e.fit?.score
    ? `<div class="score" title="${esc(e.fit.reason || "")}"><span class="dots">${[1, 2, 3, 4, 5].map((i) => `<i class="${i <= e.fit.score ? "f" : ""}"></i>`).join("")}</span><b>${e.fit.score}</b></div>`
    : `<span class="pending">–</span>`;
  const fitToggle = (id) => {
    const d = note(id).decision;
    return `<div class="fit" data-fit="${id}"><button class="yes ${d === "fit" ? "on" : ""}" data-v="fit">Fit</button><button class="no ${d === "not_fit" ? "on" : ""}" data-v="not_fit">Not fit</button></div>`;
  };
  const focusCell = (e) => e.focus
    ? `<div class="focus">${esc(e.focus)}</div>${(e.topics || []).length ? `<div class="tags">${e.topics.slice(0, 4).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}`
    : `<span class="pending">Not researched yet</span>`;
  const noteCell = (id) => `<textarea class="note" data-note="${id}" placeholder="Add a note…">${esc(note(id).body)}</textarea>`;
  function peopleSummary(e) {
    const m = e.members || [];
    if (!e.refreshed_at) return `<span class="pending">–</span>`;
    const pd = m.filter((x) => /post/i.test(x.role)).length, phd = m.length - pd;
    return m.length ? `<div class="people">${pd} postdoc${pd === 1 ? "" : "s"}<br>${phd} PhD student${phd === 1 ? "" : "s"}</div>` : `<span class="pending">None listed</span>`;
  }
  function programsCell(e) {
    const ps = (e.programs || []).filter((p) => p.status !== "closed");
    if (!e.refreshed_at) return `<span class="pending">–</span>`;
    if (!ps.length) return `<span class="pending">${(e.programs || []).length ? "All closed" : "None listed"}</span>`;
    return ps.slice(0, 2).map((p) => `<div class="prog"><div>${link(p.url, p.name, "name")} <span class="st ${p.status}">· ${p.deadline ? "Due " + fmtDate(p.deadline) : esc(p.deadline_text || PSTATUS[p.status])}</span></div>${p.requirements ? `<div class="req">${esc(p.requirements)}</div>` : ""}</div>`).join("")
      + (ps.length > 2 ? `<div class="sub">+${ps.length - 2} more</div>` : "");
  }

  // ------------------------------------------------------------------ directory
  function rows() {
    const q = S.q.trim().toLowerCase();
    let list = DATA.entries.filter((e) => e.category === S.group);
    if (S.inst) list = list.filter((e) => (e.group || e.institution) === S.inst);
    if (S.decision !== "all") list = list.filter((e) => (note(e.id).decision || "undecided") === S.decision);
    if (q) list = list.filter((e) => [e.name, e.pi, e.institution, e.department, e.focus, ...(e.topics || []),
      ...(e.members || []).map((m) => `${m.name} ${m.focus}`), ...(e.leads || []).map((l) => l.name),
      ...(e.programs || []).map((p) => p.name), note(e.id).body].join(" ").toLowerCase().includes(q));
    const key = {
      score: (e) => e.fit?.score ?? 0, name: (e) => e.name.toLowerCase(), inst: (e) => (e.institution || "").toLowerCase(),
      rank: (e) => -(e.rank ?? 99), people: (e) => (e.members || []).length,
    }[S.sortKey] || ((e) => e.fit?.score ?? 0);
    return list.sort((a, b) => { const x = key(a), y = key(b); const c = typeof x === "string" ? x.localeCompare(y) : x - y; return S.sortDir * c || a.name.localeCompare(b.name); });
  }

  function viewDirectory() {
    const g = group(S.group);
    const counts = Object.fromEntries(GROUPS.map((x) => [x.id, DATA.entries.filter((e) => e.category === x.id).length]));
    const insts = g.lab ? [...new Set(DATA.entries.filter((e) => e.category === S.group).map((e) => e.group || e.institution))].sort() : [];
    const list = rows();
    const th = (k, l) => k ? `<th class="sort" data-sort="${k}">${l}${S.sortKey === k ? (S.sortDir < 0 ? " ↓" : " ↑") : ""}</th>` : `<th>${l}</th>`;
    const head = g.lab
      ? [th(null, "Fit"), th("score", "Score"), th("name", "Lab"), th("inst", "Institution"), th(null, "Focus"), th("people", "People"), th(null, "Notes")]
      : [th(null, "Fit"), th("score", "Score"), th("name", "Company"), th(null, "Leads"), th(null, "Focus"), th(null, "Programs"), th(null, "Notes")];
    const body = list.map((e) => {
      const nf = note(e.id).decision === "not_fit" ? "not-fit" : "";
      if (g.lab) return `<tr class="${nf}" data-open="${e.id}"><td>${fitToggle(e.id)}</td><td>${scoreCell(e)}</td>
        <td style="min-width:170px">${link(e.url, e.name, "name")}<div class="sub">${esc(e.pi || e.pi_profile?.name || "")}</div></td>
        <td style="min-width:140px">${esc(e.current_institution || e.institution)}${e.department ? `<div class="sub">${esc(e.department)}</div>` : ""}</td>
        <td>${focusCell(e)}</td><td>${peopleSummary(e)}</td><td style="width:220px">${noteCell(e.id)}</td></tr>`;
      return `<tr class="${nf}" data-open="${e.id}"><td>${fitToggle(e.id)}</td><td>${scoreCell(e)}</td>
        <td style="min-width:150px">${link(e.url, e.name, "name")}<div class="sub">${esc(e.location || "")}</div></td>
        <td style="min-width:160px">${(e.leads || []).slice(0, 3).map((l) => `<div>${esc(l.name)}<div class="sub">${esc(l.role || "")}</div></div>`).join("") || '<span class="pending">–</span>'}</td>
        <td>${focusCell(e)}</td><td style="min-width:220px">${programsCell(e)}</td><td style="width:220px">${noteCell(e.id)}</td></tr>`;
    }).join("");
    return `
      <div class="groups">${GROUPS.map((x) => `<button class="group ${x.id === S.group ? "on" : ""}" data-group="${x.id}">${x.label}<span class="n">${counts[x.id]}</span></button>`).join("")}</div>
      <div class="toolbar">
        <input class="search" id="q" placeholder="Search names, topics, people, notes…" value="${esc(S.q)}">
        <div class="seg">${[["all", "All"], ["fit", "Fit"], ["not_fit", "Not fit"], ["undecided", "Undecided"]].map(([v, l]) => `<button class="${S.decision === v ? "on" : ""}" data-decision="${v}">${l}</button>`).join("")}</div>
        <span class="count">${list.length} ${g.lab ? "labs" : "companies"}</span>
      </div>
      ${insts.length > 1 ? `<div class="chips"><button class="chipbtn ${!S.inst ? "on" : ""}" data-inst="">All</button>${insts.map((i) => `<button class="chipbtn ${S.inst === i ? "on" : ""}" data-inst="${esc(i)}">${esc(i)}</button>`).join("")}</div>` : ""}
      <div class="card tablewrap">${list.length ? `<table><thead><tr>${head.join("")}</tr></thead><tbody>${body}</tbody></table>` : `<div class="empty">Nothing here yet.</div>`}</div>`;
  }

  // ------------------------------------------------------------------ programs
  function viewPrograms() {
    let ps = DATA.entries.flatMap((e) => (e.programs || []).map((p) => ({ ...p, e })));
    const total = ps.length;
    if (S.hideClosed) ps = ps.filter((p) => p.status !== "closed");
    const k = (p) => (p.deadline && p.deadline >= TODAY ? "0" + p.deadline : p.status === "opens_soon" ? "1" : p.status === "rolling" ? "2" : "3");
    ps.sort((a, b) => k(a).localeCompare(k(b)) || a.e.name.localeCompare(b.e.name));
    return `<div class="toolbar"><div class="seg"><button class="${S.hideClosed ? "on" : ""}" data-closed="1">Open &amp; upcoming</button><button class="${!S.hideClosed ? "on" : ""}" data-closed="0">All (${total})</button></div><span class="count">${ps.length} programs · by deadline</span></div>
      <div class="card tablewrap">${ps.length ? `<table><thead><tr><th>Deadline</th><th>Program</th><th>Company</th><th>Type</th><th>Requirements</th><th>Eligibility</th><th>Status</th></tr></thead><tbody>
      ${ps.map((p) => `<tr data-open="${p.e.id}"><td class="name" style="white-space:nowrap">${p.deadline ? fmtDate(p.deadline) : `<span class="${p.status}">${esc(p.deadline_text || PSTATUS[p.status])}</span>`}</td>
        <td>${link(p.url, p.name, "name")}</td><td>${esc(p.e.name)}</td><td>${esc(p.type)}</td>
        <td style="max-width:360px">${esc(p.requirements || "")}</td><td style="max-width:260px" class="sub">${esc(p.eligibility || "")}</td><td class="st ${p.status}">${PSTATUS[p.status]}</td></tr>`).join("")}
      </tbody></table>` : `<div class="empty">No programs yet. They appear once companies are researched.</div>`}</div>`;
  }

  // ------------------------------------------------------------------ news
  function viewNews() {
    const weeks = CHANGES.weeks || [];
    if (!weeks.length) return `<div class="card empty">The change log starts after the first refresh.</div>`;
    return weeks.map((w) => `<div class="card week"><h2>Week of ${fmtDate(w.week)}<span class="sub">${w.items.length} changes</span></h2>
      ${w.items.length ? w.items.map((it) => `<div class="item"><span class="kind ${it.type === "needs_review" ? "warn" : ""}">${esc(it.type.replace(/_/g, " "))}</span>
        <div><a data-open="${esc(it.entry_id)}" class="name" style="cursor:pointer">${esc(it.entry_name)}</a><div class="sub">${esc(it.text)} ${okUrl(it.url) ? link(it.url, "source") : ""}</div></div></div>`).join("") : `<div class="empty">No changes.</div>`}</div>`).join("");
  }

  // ------------------------------------------------------------------ drawer
  function openDrawer(id) {
    const e = byId(id);
    if (!e) return;
    history.replaceState(null, "", "#" + id);
    const lab = group(e.category).lab;
    const pi = e.pi_profile || {};
    const d = $("#drawer");
    const people = lab ? `<section><h3>Postdocs &amp; PhD students (${(e.members || []).length})</h3>
      ${(e.members || []).length ? `<div class="card"><table class="mini"><thead><tr><th>Name</th><th>Role</th><th>Works on</th><th>Contact</th></tr></thead><tbody>
      ${e.members.map((m) => `<tr><td>${link(m.profile_url, m.name, "name")}</td><td class="sub" style="white-space:nowrap">${esc(m.role)}${m.year ? " · " + esc(m.year) : ""}</td><td>${esc(m.focus || "")}</td>
        <td>${m.email ? `<a href="mailto:${esc(m.email)}">${esc(m.email)}</a>` : okUrl(m.profile_url) ? link(m.profile_url, "Profile") : '<span class="pending">–</span>'}</td></tr>`).join("")}
      </tbody></table></div>${okUrl(e.members_page) ? `<div class="links">${link(e.members_page, "Lab members page ↗")}</div>` : ""}` : `<div class="pending">${e.refreshed_at ? "No current postdocs or PhD students listed." : "Not researched yet."}</div>`}</section>` : "";
    const programs = !lab ? `<section><h3>Programs (${(e.programs || []).length})</h3>${(e.programs || []).map((p) => `<div class="pcard">
        <div class="row"><span class="name">${link(p.url, p.name, "name")}</span><span class="st ${p.status}">${PSTATUS[p.status]}</span></div>
        <dl><dt>Type</dt><dd>${esc(p.type)}</dd>${p.requirements ? `<dt>Requirements</dt><dd>${esc(p.requirements)}</dd>` : ""}${p.eligibility ? `<dt>Eligibility</dt><dd>${esc(p.eligibility)}</dd>` : ""}
        ${p.location ? `<dt>Location</dt><dd>${esc(p.location)}</dd>` : ""}${p.duration ? `<dt>Duration</dt><dd>${esc(p.duration)}</dd>` : ""}
        <dt>Deadline</dt><dd>${p.deadline ? fmtDate(p.deadline) : esc(p.deadline_text || "Not posted")}</dd></dl></div>`).join("") || `<div class="pending">${e.refreshed_at ? "No programs found." : "Not researched yet."}</div>`}</section>` : "";
    const leads = !lab && (e.leads || []).length ? `<section><h3>Leads &amp; founders</h3>${e.leads.map((l) => `<div style="margin-bottom:10px">${link(l.profile_url, l.name, "name")}<div class="sub">${esc([l.role, l.background].filter(Boolean).join(" · "))}</div></div>`).join("")}</section>` : "";
    d.innerHTML = `<button class="close" data-close>Close</button>
      <div class="eyebrow">${esc(group(e.category).label)}${e.group ? " · " + esc(e.group) : ""}</div>
      <h1>${link(e.url, e.name)}</h1>
      <div class="sub">${lab ? `${link(pi.profile_url, pi.name || e.pi || "")}${pi.title ? " · " + esc(pi.title) : ""}<br>` : ""}${esc([e.current_institution || e.institution, e.department, e.location].filter(Boolean).join(" · "))}</div>
      <div class="links">${okUrl(e.url) ? link(e.url, "Website ↗") : ""}${okUrl(pi.scholar_url) ? link(pi.scholar_url, "Google Scholar ↗") : ""}</div>
      ${(e.flags || []).map((f) => `<div class="flag">${esc(f)}</div>`).join("")}
      <section><div class="row">${fitToggle(e.id)}${scoreCell(e)}</div>${e.fit?.reason ? `<div class="reason">${esc(e.fit.reason)}</div>` : ""}</section>
      <section><h3>Focus</h3><div class="lead">${esc(e.focus || "Not researched yet.")}</div><div class="tags">${(e.topics || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div></section>
      ${leads}${people}${programs}
      <section><h3>My notes</h3><textarea class="note" data-note="${e.id}" placeholder="Add a note…">${esc(note(e.id).body)}</textarea></section>
      ${(e.sources || []).length ? `<section><h3>Sources</h3><div class="links" style="flex-direction:column;gap:4px">${e.sources.map((s) => link(s.url, s.title || s.url)).join("")}</div></section>` : ""}
      <div class="sub" style="margin-top:24px">${e.refreshed_at ? "Researched " + fmtDate(e.refreshed_at.slice(0, 10)) : ""}</div>`;
    d.hidden = false; $("#scrim").hidden = false; d.scrollTop = 0;
    bind(d, true);
  }
  function closeDrawer() { $("#drawer").hidden = true; $("#scrim").hidden = true; history.replaceState(null, "", location.pathname); }

  // ------------------------------------------------------------------ events
  function bind(root, inDrawer = false) {
    root.onclick = (ev) => {
      const t = ev.target;
      if (t.closest("[data-close]")) return closeDrawer();
      const fb = t.closest("[data-fit] button");
      if (fb) {
        const id = fb.closest("[data-fit]").dataset.fit;
        const v = note(id).decision === fb.dataset.v ? null : fb.dataset.v;
        saveNote(id, { decision: v });
        render(); if (inDrawer) openDrawer(id);
        return;
      }
      if (t.closest("textarea, input, a[href]")) return;
      const g = t.closest("[data-group]"); if (g) { S.group = g.dataset.group; S.inst = ""; S.sortKey = group(S.group).lab ? "score" : "rank"; S.sortDir = -1; saveUI(); return render(); }
      const dcs = t.closest("[data-decision]"); if (dcs) { S.decision = dcs.dataset.decision; return render(); }
      const ins = t.closest("[data-inst]"); if (ins) { S.inst = ins.dataset.inst; return render(); }
      const cl = t.closest("[data-closed]"); if (cl) { S.hideClosed = cl.dataset.closed === "1"; saveUI(); return render(); }
      const so = t.closest("th[data-sort]"); if (so) { const k = so.dataset.sort; if (S.sortKey === k) S.sortDir *= -1; else { S.sortKey = k; S.sortDir = ["name", "inst"].includes(k) ? 1 : -1; } saveUI(); return render(); }
      const op = !inDrawer && t.closest("[data-open]"); if (op) openDrawer(op.dataset.open);
    };
    root.oninput = (ev) => { const t = ev.target; if (t.dataset.note) saveNote(t.dataset.note, { body: t.value }); };
    root.onchange = (ev) => { const t = ev.target; if (t.dataset.note) { saveNote(t.dataset.note, { body: t.value }, false); if (inDrawer) render(); } };
    const q = $("#q", root);
    if (q) q.oninput = () => { S.q = q.value; const p = q.selectionStart; render(); const q2 = $("#q"); q2.focus(); q2.setSelectionRange(p, p); };
  }

  boot();
})();
