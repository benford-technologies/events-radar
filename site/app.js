(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  // Everything we render comes from scraped web pages: always escape.
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");

  const CATEGORY = {
    cfo: "CFO & finance", audit_accounting: "Audit & accounting", finance_tech: "Fintech & AI",
    founders_investors: "Founders & investors", hackathon: "Hackathon", other: "Other",
  };
  const today = new Date().toISOString().slice(0, 10);
  const weekAgo = new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 10);

  const state = { events: [], hackathons: [], universities: [], runs: [], meta: {}, region: "london", uni: null, recordView: "went" };

  // ---------------------------------------------------------------- load
  async function load() {
    const get = (p) => fetch(p, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    const [events, hackathons, universities, runs, meta] = await Promise.all([
      get("api/events.json"), get("api/hackathons.json"), get("api/universities.json"),
      get("api/runs.json"), get("api/meta.json"),
    ]);
    Object.assign(state, {
      events: events || [], hackathons: hackathons || [], universities: universities || [],
      runs: runs || [], meta: meta || {},
    });

    const last = state.runs[0];
    $("#last-scan").textContent = last
      ? `CFO events, audit and accounting conferences, speaker calls and university hackathons across London and Norway. Last scanned ${fmtDateTime(last.finished_at)}, ${last.new} new.`
      : "CFO events, audit and accounting conferences, speaker calls and university hackathons across London and Norway. Refreshed every morning.";
    $("#demo-banner").hidden = !state.meta.demo;
    if (state.meta.manual_edit_url) {
      const a = $("#add-event");
      a.href = state.meta.manual_edit_url; a.target = "_blank"; a.hidden = false;
    }
    applyPending();
    restoreFilters();
    renderEvents();
    renderRuns();
  }

  // ---------------------------------------------------------------- helpers
  const isPast = (e) => (e.end_date || e.start_date || "9999") < today;
  const isNew = (e) => (e.first_seen || "") >= weekAgo;
  const countryKey = (c) => {
    const s = (c || "").toLowerCase();
    if (/(uk|united kingdom|england|scotland|wales|britain)/.test(s)) return "uk";
    if (/(norway|norge)/.test(s)) return "norway";
    return "other";
  };
  function fmtDateTime(iso) {
    try { return new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); }
    catch { return iso; }
  }
  function dateRange(e) {
    if (!e.start_date) return "Date TBC";
    const o = { day: "numeric", month: "short", year: "numeric" };
    const s = new Date(e.start_date).toLocaleDateString("en-GB", o);
    if (!e.end_date || e.end_date === e.start_date) return s;
    return `${s} – ${new Date(e.end_date).toLocaleDateString("en-GB", o)}`;
  }
  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2600);
  }

  function eventCard(e, compact = false, extraChips = "") {
    const d = e.start_date ? new Date(e.start_date) : null;
    const dateBlock = d
      ? `<div class="date"><b>${d.getDate()}</b><span>${d.toLocaleDateString("en-GB", { month: "short" })}</span></div>`
      : `<div class="date tbc"><b>TBC</b></div>`;
    const where = [e.venue, e.city, e.country].filter(Boolean).join(", ");
    const fit = Number(e.relevance) || 0;
    const fitCls = fit >= 75 ? "hi" : fit >= 55 ? "mid" : "";
    const url = safeUrl(e.url);
    const chips = [
      extraChips,
      isNew(e) && !isPast(e) ? `<span class="chip new">New</span>` : "",
      e.status && e.status !== "new" && !e._hideStatus ? `<span class="chip ${esc(e.status)}">${esc(STATUS_LABEL[e.status] || e.status)}${e.owner ? " · " + esc(e.owner) : ""}${e._pending ? " · saving" : ""}</span>` : "",
      e.reason ? `<span class="chip">${esc(e.reason)}</span>` : "",
      `<span class="chip">${esc(CATEGORY[e.category] || e.category || "")}</span>`,
      e.speaker_opportunity ? `<span class="chip speaker" title="${esc(e.speaker_info)}">Speaker / sponsor slot</span>` : "",
      e.format && e.format !== "unknown" && e.format !== "in_person" ? `<span class="chip">${esc(e.format.replace("_", " "))}</span>` : "",
      e.cost ? `<span class="chip">${esc(e.cost)}</span>` : "",
      e.date_confirmed === false ? `<span class="chip" title="Date not confirmed on the official page">date unconfirmed</span>` : "",
      e.source === "manual" ? `<span class="chip">added by team</span>` : "",
    ].join("");
    const sources = (e.source_urls || []).map(safeUrl).filter(Boolean);
    return `
      <article class="ev${isPast(e) ? " past" : ""}">
        ${dateBlock}
        <div>
          <h3>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(e.name)}</a>` : esc(e.name)}</h3>
          <div class="meta">${esc(dateRange(e))}${where ? " · " + esc(where) : ""}${e.organiser ? " · " + esc(e.organiser) : ""}</div>
          ${e.why_relevant && !compact ? `<div class="why">${esc(e.why_relevant)}</div>` : ""}
          ${e.speaker_info && !compact ? `<div class="note"><span class="mono">Speak</span>${esc(e.speaker_info)}</div>` : ""}
          ${e.team_note ? `<div class="note"><span class="mono">Note</span>${esc(e.team_note)}</div>` : ""}
          <div class="chips">${chips}</div>
          ${compact ? "" : reasonsRow(e)}
          ${sources.length && !compact ? `<details class="sources"><summary>${sources.length} source${sources.length > 1 ? "s" : ""}</summary>${sources.map((s) => `<div><a href="${esc(s)}" target="_blank" rel="noopener">${esc(s)}</a></div>`).join("")}</details>` : ""}
        </div>
        ${compact ? "" : `<div class="side">
          <span class="fit ${fitCls}" title="Fit for Benford (0–100)"><b>${fit}</b><span>fit</span></span>
          ${flagButtons(e)}
        </div>`}
      </article>`;
  }

  // ---------------------------------------------------------------- flags (interested / pass / went)
  const STATUS_LABEL = {
    interested: "Interested", shortlisted: "Interested", attending: "Attending", maybe: "Maybe",
    skip: "Passed", attended: "We went", missed: "Missed",
  };
  const PASS_REASONS = ["Wrong audience", "Too small", "Too expensive", "Wrong location", "Not our topic", "Date clash", "Other"];
  const isInterested = (e) => ["interested", "shortlisted", "attending"].includes(e.status);

  function flagButtons(e) {
    const id = esc(e.id);
    if (isPast(e)) {
      return `<div class="acts">
        <button class="act yes" data-flag="attended" data-id="${id}" aria-pressed="${e.status === "attended"}">We went</button>
        <button class="act no" data-flag="missed" data-id="${id}" aria-pressed="${e.status === "missed"}">Didn't go</button>
      </div>`;
    }
    return `<div class="acts">
      <button class="act yes" data-flag="interested" data-id="${id}" aria-pressed="${isInterested(e)}" title="Flag interest: keeps it on the list and teaches the scanner to find more like it">Interested</button>
      <button class="act no" data-pass="${id}" aria-pressed="${e.status === "skip"}" aria-expanded="false" title="Hide it for everyone and teach the scanner to avoid similar ones">Pass</button>
    </div>`;
  }
  function reasonsRow(e) {
    if (isPast(e)) return "";
    return `<div class="reasons" data-reasons="${esc(e.id)}" hidden>
      <span class="mono">Why pass?</span>
      ${PASS_REASONS.map((r) => `<button class="chip-btn" data-flag="skip" data-reason="${esc(r)}" data-id="${esc(e.id)}">${esc(r)}</button>`).join("")}
    </div>`;
  }

  // Flags save through a GitHub issue (the triage bot writes triage.yaml and republishes).
  // Until that lands, the viewer's own flags are applied locally so the list updates at once.
  const PENDING_KEY = "radar-pending";
  function readPending() { try { return JSON.parse(localStorage.getItem(PENDING_KEY) || "{}"); } catch { return {}; } }
  function writePending(p) { try { localStorage.setItem(PENDING_KEY, JSON.stringify(p)); } catch {} }
  function applyPending() {
    const p = readPending(), cutoff = Date.now() - 2 * 864e5;
    for (const e of [...state.events, ...state.hackathons]) {
      const f = p[e.id];
      if (!f) continue;
      if (f.at < cutoff || e.status === f.status) { delete p[e.id]; continue; }
      e.status = f.status; e.reason = f.reason || e.reason; e._pending = true;
    }
    writePending(p);
  }

  function flag(id, status, reason = "") {
    const e = [...state.events, ...state.hackathons].find((x) => x.id === id);
    if (!e) return;
    // clicking an active flag again clears it
    if ((status === "interested" && isInterested(e)) || (status === e.status && !reason)) status = "new";
    const p = readPending(); p[id] = { status, reason, at: Date.now() }; writePending(p);
    Object.assign(e, { status, reason, _pending: true });
    rerender();

    const repo = state.meta.repo;
    if (!repo) { toast("Saved in this browser only (no GitHub repo configured)"); return; }
    const title = `[triage] ${status} · ${e.name}`.slice(0, 180);
    const body = [`id: ${e.id}`, `status: ${status}`, `reason: ${reason}`, `note: `, "",
      "<!-- Click 'Create' to save this for the whole team. The triage bot updates data/triage.yaml",
      "     and republishes the dashboard in about a minute. You can add a note above. -->"].join("\n");
    const url = `https://github.com/${repo}/issues/new?` + new URLSearchParams({ title, body, labels: "triage" });
    window.open(url, "_blank", "noopener");
    toast(status === "skip" ? "Hidden. Click Create on GitHub to save it for the team" : "Flagged. Click Create on GitHub to save it for the team");
  }

  function rerender() {
    renderEvents();
    if (!$("#tab-hackathons").hidden) renderHackathons();
    if (!$("#tab-record").hidden) renderRecord();
  }

  // ---------------------------------------------------------------- events tab
  const FILTERS = ["q", "f-category", "f-country", "f-status", "f-min", "f-speaker", "f-new", "f-past"];
  function saveFilters() {
    try {
      const v = Object.fromEntries(FILTERS.map((id) => { const el = $("#" + id); return [id, el.type === "checkbox" ? el.checked : el.value]; }));
      localStorage.setItem("radar-filters", JSON.stringify(v));
    } catch {}
  }
  function restoreFilters() {
    try {
      const v = JSON.parse(localStorage.getItem("radar-filters") || "{}");
      for (const [id, val] of Object.entries(v)) {
        const el = $("#" + id); if (!el) continue;
        if (el.type === "checkbox") el.checked = !!val; else el.value = val;
      }
    } catch {}
    $("#f-min-out").textContent = $("#f-min").value;
  }

  function renderEvents() {
    const q = $("#q").value.trim().toLowerCase();
    const cat = $("#f-category").value, country = $("#f-country").value, status = $("#f-status").value;
    const min = +$("#f-min").value, speaker = $("#f-speaker").checked, onlyNew = $("#f-new").checked, past = $("#f-past").checked;
    $("#f-min-out").textContent = min;

    const upcoming = state.events.filter((e) => !isPast(e) && e.status !== "skip");
    $("#stats").innerHTML = [
      [upcoming.length, "upcoming events"],
      [upcoming.filter(isNew).length, "new this week"],
      [upcoming.filter((e) => e.speaker_opportunity).length, "speaker / sponsor slots"],
      [upcoming.filter(isInterested).length, "flagged interested"],
    ].map(([n, l], i) => `<div class="stat${i === 3 ? " accent" : ""}"><b>${n}</b><span class="mono">${l}</span></div>`).join("");

    const list = state.events.filter((e) => {
      if (!past && isPast(e)) return false;
      if (cat && e.category !== cat) return false;
      if (country && countryKey(e.country) !== country) return false;
      if (status && (e.status || "new") !== status) return false;
      if (!status && e.status === "skip") return false;
      if ((Number(e.relevance) || 0) < min && !isInterested(e)) return false;
      if (speaker && !e.speaker_opportunity) return false;
      if (onlyNew && !isNew(e)) return false;
      if (q && ![e.name, e.organiser, e.city, e.country, e.audience, e.why_relevant].join(" ").toLowerCase().includes(q)) return false;
      return true;
    });

    if (!list.length) {
      $("#event-list").innerHTML = `<div class="empty">No events match these filters.</div>`;
      return;
    }
    $("#event-list").innerHTML = groupedByMonth(list);
  }

  // ---------------------------------------------------------------- hackathons tab
  const regionOf = (country) => (countryKey(country) === "norway" ? "norway" : countryKey(country) === "uk" ? "london" : "other");

  function setRegion(r) {
    state.region = r; state.uni = null;
    $$("#hack-region button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.region === r)));
    renderHackathons();
  }

  function groupedByMonth(list, card = eventCard) {
    let html = "", month = null;
    for (const e of list) {
      const m = e.start_date ? new Date(e.start_date).toLocaleDateString("en-GB", { month: "long", year: "numeric" }) : "Date to be confirmed";
      if (m !== month) { html += `<h2 class="month">${esc(m)}</h2>`; month = m; }
      html += card(e);
    }
    return html;
  }

  function renderHackathons() {
    const inRegion = (country) => state.region === "all" || regionOf(country) === state.region;
    const upcoming = state.hackathons.filter((h) => !isPast(h) && h.status !== "skip" && inRegion(h.country));
    const count = {};
    for (const h of upcoming) count[h.university] = (count[h.university] || 0) + 1;

    // University directory: every tracked university in the region, busiest first.
    const unis = state.universities
      .filter((u) => inRegion(u.country))
      .map((u) => ({ ...u, n: count[u.name] || 0 }))
      .sort((a, b) => b.n - a.n || a.name.localeCompare(b.name));
    const covered = unis.filter((u) => u.n).length;
    $("#uni-summary").innerHTML =
      `<b>${upcoming.length}</b> upcoming hackathon${upcoming.length === 1 ? "" : "s"} across <b>${covered}</b> of ${unis.length} universities`;
    $("#uni-chips").innerHTML = unis.map((u) => `
      <button class="uni${u.n ? "" : " zero"}" data-uni="${esc(u.name)}" aria-pressed="${state.uni === u.name}">
        <span>${esc(u.name)}</span><b>${u.n}</b>
      </button>`).join("");

    const list = state.uni ? upcoming.filter((h) => h.university === state.uni) : upcoming;
    const withUni = (h) => eventCard({
      ...h,
      organiser: [h.organiser, !["None", "Other", ""].includes(h.university || "") ? h.university : ""].filter(Boolean).join(" · "),
    });
    $("#hack-list").innerHTML = list.length
      ? groupedByMonth(list, withUni)
      : `<div class="empty">${state.uni ? `No upcoming hackathons found at ${esc(state.uni)} yet.` : "No upcoming hackathons found here yet."}</div>`;
  }

  // ---------------------------------------------------------------- track record tab
  // Past events sorted into what we did about them.
  function outcomeOf(e) {
    if (e.status === "attended") return "went";
    if (isInterested(e)) return "confirm";                                            // flagged, not updated yet
    if (e.status === "missed") return "missed";                                      // planned, didn't make it
    if (e.status === "skip") return "skipped";                                       // decided not to go
    if ((Number(e.relevance) || 0) >= 60) return "passed";                          // relevant, nobody picked it up
    return null;
  }
  const OUTCOME_LABEL = {
    went: "We went", confirm: "Did we go?", missed: "Planned, missed", skipped: "Decided to skip", passed: "Not pursued",
  };

  function recordCard(e) {
    const o = outcomeOf(e);
    const extra = [
      `<span class="chip outcome ${o}">${OUTCOME_LABEL[o]}</span>`,
      e.leads ? `<span class="chip">${esc(e.leads)} lead${Number(e.leads) === 1 ? "" : "s"}</span>` : "",
      e.rating ? `<span class="chip" title="Worth it, 1–5">Worth it ${esc(e.rating)}/5</span>` : "",
      e.owner ? `<span class="chip">${esc(e.owner)}</span>` : "",
      e._pending ? `<span class="chip">saving</span>` : "",
    ].join("");
    return eventCard({ ...e, _hideStatus: true }, false, extra);
  }

  function setRecordView(v) {
    state.recordView = v;
    $$("#record-view button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === v)));
    renderRecord();
  }

  function renderRecord() {
    const thisYear = today.slice(0, 4);
    const past = [...state.events, ...state.hackathons].filter(isPast).filter((e) => outcomeOf(e));
    const by = (o) => past.filter((e) => outcomeOf(e) === o);
    const went = by("went"), confirm = by("confirm");
    const didnt = past.filter((e) => ["missed", "skipped", "passed"].includes(outcomeOf(e)));
    const leads = went.reduce((s, e) => s + (Number(e.leads) || 0), 0);

    $("#record-stats").innerHTML = [
      [went.filter((e) => (e.start_date || "").startsWith(thisYear)).length, `events attended in ${thisYear}`, true],
      [leads, "leads from events"],
      [by("missed").length, "planned but missed"],
      [by("passed").length + by("skipped").length, "relevant ones we passed on"],
    ].map(([n, l, accent]) => `<div class="stat${accent ? " accent" : ""}"><b>${n}</b><span class="mono">${l}</span></div>`).join("");

    const views = { went, didnt, confirm };
    $$("#record-view button").forEach((b) => { $("b", b).textContent = views[b.dataset.view].length; });
    $("#record-confirm-hint").hidden = state.recordView !== "confirm" || !confirm.length;

    const newestFirst = (a, b) => (b.start_date || "").localeCompare(a.start_date || "");
    const list = views[state.recordView].slice().sort(newestFirst);
    const empty = {
      went: "Nothing logged yet. After an event, mark it <code>attended</code> in triage.yaml.",
      didnt: "No missed or skipped events yet.",
      confirm: "All past events are up to date.",
    };
    $("#record-list").innerHTML = list.length ? groupedByMonth(list, recordCard) : `<div class="empty">${empty[state.recordView]}</div>`;
  }

  // ---------------------------------------------------------------- runs tab
  function renderRuns() {
    if (!state.runs.length) { $("#runs").innerHTML = `<div class="empty">No scans yet.</div>`; return; }
    $("#runs").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Finished</th><th>New</th><th>Learned from</th><th>Source</th><th>Groups</th><th>Errors</th></tr></thead><tbody>${
      state.runs.map((r) => `<tr>
        <td>${esc(fmtDateTime(r.finished_at))}</td>
        <td class="num">${esc(r.new)}</td>
        <td>${r.feedback ? `${esc(r.feedback.liked)} liked · ${esc(r.feedback.passed)} passed` : "—"}</td>
        <td>${esc(r.source)}<div class="meta">${esc(r.model || "")}</div></td>
        <td>${esc((r.groups || []).join(", "))}</td>
        <td>${(r.errors || []).map((e) => `<div class="err">${esc(e.group)}: ${esc(e.error)}</div>`).join("") || "—"}</td>
      </tr>`).join("")
    }</tbody></table></div>`;
  }

  // ---------------------------------------------------------------- wiring
  function showTab(name) {
    $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    $$(".tab").forEach((s) => (s.hidden = s.id !== "tab-" + name));
    if (name === "hackathons") renderHackathons();
    if (name === "record") renderRecord();
    try { localStorage.setItem("radar-tab", name); } catch {}
  }

  $$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $$("#hack-region button").forEach((b) => b.addEventListener("click", () => setRegion(b.dataset.region)));
  $$("#record-view button").forEach((b) => b.addEventListener("click", () => setRecordView(b.dataset.view)));
  FILTERS.forEach((id) => $("#" + id).addEventListener("input", () => { renderEvents(); saveFilters(); }));
  document.addEventListener("click", (ev) => {
    const f = ev.target.closest("[data-flag]");
    if (f) { flag(f.dataset.id, f.dataset.flag, f.dataset.reason || ""); return; }
    const pass = ev.target.closest("[data-pass]");
    if (pass) {
      const row = document.querySelector(`[data-reasons="${CSS.escape(pass.dataset.pass)}"]`);
      const e = [...state.events, ...state.hackathons].find((x) => x.id === pass.dataset.pass);
      if (e && e.status === "skip") { flag(e.id, "skip"); return; }    // already passed: undo
      if (row) { row.hidden = !row.hidden; pass.setAttribute("aria-expanded", String(!row.hidden)); }
      return;
    }
    const u = ev.target.closest("[data-uni]");
    if (u) { state.uni = state.uni === u.dataset.uni ? null : u.dataset.uni; renderHackathons(); }
  });

  load().then(() => {
    let tab = "events";
    try { tab = localStorage.getItem("radar-tab") || "events"; } catch {}
    showTab(tab);
  });
})();
