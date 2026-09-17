/* JobBot dashboard — données servies par gui.py (SQLite) */
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const icon = (id, cls = "") => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;
const fmtInt = (n) => Number(n || 0).toLocaleString("fr-FR");
const km = (v) => v == null ? "—" : `${String(v).replace(".", ",")} km`;
const dateFr = (iso, opts = { day: "2-digit", month: "2-digit" }) => { const d = new Date(iso); return isNaN(d) ? "—" : d.toLocaleDateString("fr-FR", opts); };

/* ======================= État ======================= */
const S = {
  data: { running: false, stats: {}, logs: [] },
  offers: [], companies: [], runs: [], config: null, defaults: null, allSources: [],
  version: null, view: "apercu", page: 0, sortKey: "score", sortDir: "desc",
  expanded: new Set(), selected: new Set(), charts: {}, tableMode: {}, logKey: "", lastFinished: undefined,
};
const PAGE_SIZE = 50;
const STATUS = [
  { id: "", label: "Nouvelle" }, { id: "vu", label: "Vue" }, { id: "favori", label: "Favori" },
  { id: "postule", label: "Postulé" }, { id: "relance", label: "Relancé" }, { id: "entretien", label: "Entretien" },
  { id: "offre", label: "Offre reçue" }, { id: "refuse", label: "Refusé" }, { id: "masque", label: "Masquée" },
];
const statusLabel = (id) => (STATUS.find(s => s.id === (id || "")) || STATUS[0]).label;
const APPLIED = new Set(["postule", "relance", "entretien", "offre", "refuse"]);
const PIPE = ["favori", "postule", "relance", "entretien", "offre", "refuse"];
const CONTRACTS = ["CDI", "CDD", "Intérim", "Stage / alternance", "Vacation / libéral"];

/* ======================= Dérivés ======================= */
function daysAgo(o) {
  const m = String(o.posted_date || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  return Math.max(0, Math.round((Date.now() - new Date(+m[1], +m[2] - 1, +m[3])) / 864e5));
}
const ageLabel = (n) => n === null ? "—" : n === 0 ? "Aujourd'hui" : n === 1 ? "Hier" : `${n} j`;
const contractOf = (o) => o.contract || "Non précisé";
const RAW_CONTRACT = { fulltime: "Temps plein", parttime: "Temps partiel", contract: "CDD", temporary: "Intérim", internship: "Stage" };
const contractText = (o) => RAW_CONTRACT[fold(o.contract_raw)] || o.contract_raw || o.contract || "—";
const lastRunId = () => { const r = S.runs.find(r => r.finished_at && r.trigger !== "ancien" && r.trigger !== "import"); return r ? r.id : null; };
const isNew = (o) => { const id = lastRunId(); return id !== null && o.first_run_id === id && S.runs.filter(r => r.trigger !== "ancien").length > 1; };
const visible = () => S.offers.filter(o => o.is_active && o.status !== "masque");
const companyKey = (name) => fold(name).replace(/[^a-z0-9 ]/g, " ").replace(/\b(sas|sasu|sarl|sa|eurl|groupe|france|sante|care|medical)\b/g, " ").replace(/\s+/g, " ").trim();

/* ======================= API ======================= */
async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const json = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(json.error || `HTTP ${r.status}`);
  return json;
}
function toast(msg) {
  const t = $("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.hidden = true, 3500);
}
function replaceOffer(updated) {
  const i = S.offers.findIndex(o => o.id === updated.id);
  if (i >= 0) S.offers[i] = { ...S.offers[i], ...updated, listings: S.offers[i].listings.map(l => ({ ...l, ...(updated.listings || []).find(u => u.id === l.id), description: l.description })) };
}
async function setStatus(id, status) {
  const o = await api(`/api/offers/${id}`, { status });
  replaceOffer(o);
  const same = S.offers.filter(x => x.id !== id && APPLIED.has(x.status) && companyKey(x.company) && companyKey(x.company) === companyKey(o.company));
  if (APPLIED.has(status) && same.length) toast(`Attention : tu as déjà ${same.length} candidature(s) chez ${o.company}.`);
  else toast(`Statut : ${statusLabel(status)}`);
  renderView();
}
async function setNote(id, note) {
  const o = await api(`/api/offers/${id}`, { note });
  replaceOffer(o);
  toast("Note enregistrée");
}
async function loadData() {
  const [offers, runs, companies] = await Promise.all([api("/api/offers"), api("/api/runs"), api("/api/companies")]);
  S.offers = offers; S.runs = runs; S.companies = companies;
}

/* ======================= Routage ======================= */
const VIEWS = ["apercu", "offres", "candidatures", "employeurs", "doublons", "recherche", "historique"];
function parseRoute() {
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  return { view: VIEWS.includes(path) ? path : "apercu", params: new URLSearchParams(query || "") };
}
function go(view, params) {
  const q = params ? new URLSearchParams(params).toString() : "";
  location.hash = `#/${view}${q ? "?" + q : ""}`;
}
function onRoute() {
  const { view, params } = parseRoute();
  S.view = view;
  document.querySelectorAll("nav.tabs a").forEach(a => a.dataset.view === view ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
  document.querySelectorAll("section.view").forEach(s => s.hidden = s.id !== `view-${view}`);
  if (view === "offres") { readOfferFilters(params); S.page = 0; }
  renderView();
  window.scrollTo({ top: 0 });
}

/* ======================= Graphiques ======================= */
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const HAS_CHART = typeof window.Chart !== "undefined";

function baseOptions(horizontal) {
  const grid = css("--grid"), muted = css("--muted"), text = css("--text"), surface = css("--surface"), border = css("--border");
  const font = { family: "Fira Sans", size: 12 };
  const valueAxis = { beginAtZero: true, grid: { color: grid, drawTicks: false }, border: { display: false }, ticks: { color: muted, precision: 0, padding: 6, font } };
  const catAxis = { grid: { display: false }, border: { color: border }, ticks: { color: text, padding: 4, font, autoSkip: !horizontal } };
  return {
    responsive: true, maintainAspectRatio: false, animation: false, indexAxis: horizontal ? "y" : "x",
    plugins: {
      legend: { display: false },
      tooltip: { backgroundColor: surface, titleColor: text, bodyColor: text, borderColor: border, borderWidth: 1, padding: 10,
        titleFont: { family: "Fira Sans", weight: "600" }, bodyFont: { family: "Fira Sans" }, boxPadding: 4 },
    },
    scales: horizontal ? { x: valueAxis, y: catAxis } : { x: catAxis, y: valueAxis },
  };
}
const bar = (label, data, color, extra = {}) => ({ label, data, backgroundColor: color, hoverBackgroundColor: color, borderWidth: 0,
  borderRadius: 4, borderSkipped: "start", maxBarThickness: 22, categoryPercentage: .8, barPercentage: .9, ...extra });

function singleBar(labels, values, click, head, fullNames) {
  const opts = baseOptions(true);
  if (fullNames) opts.plugins.tooltip.callbacks = { title: (items) => fullNames[items[0].dataIndex] };
  return {
    height: Math.max(160, labels.length * 32 + 40),
    config: { type: "bar", data: { labels, datasets: [bar("Offres", values, css("--series-1"))] }, options: opts },
    table: { head, rows: labels.map((l, i) => [fullNames ? fullNames[i] : l, values[i]]) },
    click, empty: values.some(v => v > 0) ? null : "Pas encore de données.",
  };
}

function chartSpecs() {
  const offers = visible();
  const specs = {};
  const count = (fn) => { const m = new Map(); for (const o of offers) for (const k of [].concat(fn(o))) m.set(k, (m.get(k) || 0) + 1); return m; };

  { // Apport des sites : exclusif (slot 1) vs partagé (slot 2)
    const names = [...new Set(offers.flatMap(o => o.sources))];
    const only = names.map(n => offers.filter(o => o.sources.length === 1 && o.sources[0] === n).length);
    const shared = names.map(n => offers.filter(o => o.sources.length > 1 && o.sources.includes(n)).length);
    const order = names.map((n, i) => i).sort((a, b) => (only[b] + shared[b]) - (only[a] + shared[a]));
    const opts = baseOptions(true);
    opts.scales.x.stacked = true; opts.scales.y.stacked = true;
    opts.plugins.tooltip.mode = "index"; opts.plugins.tooltip.intersect = false;
    opts.plugins.tooltip.callbacks = { footer: (items) => `Total : ${items.reduce((a, b) => a + b.parsed.x, 0)}` };
    const L = order.map(i => names[i]);
    specs.sources = {
      legend: [["Uniquement sur ce site", "--series-1"], ["Aussi sur d'autres sites", "--series-2"]],
      height: Math.max(180, L.length * 40 + 40),
      config: { type: "bar", data: { labels: L, datasets: [
        bar("Uniquement sur ce site", order.map(i => only[i]), css("--series-1"), { borderRadius: 0, borderSkipped: false, borderWidth: { right: 2 }, borderColor: css("--surface") }),
        bar("Aussi sur d'autres sites", order.map(i => shared[i]), css("--series-2")),
      ] }, options: opts },
      table: { head: ["Site", "Uniquement ici", "Aussi ailleurs"], rows: order.map(i => [names[i], only[i], shared[i]]) },
      click: (i) => go("offres", { source: L[i] }),
      empty: names.length ? null : "Pas encore de données.",
    };
  }
  { const m = count(o => o.hub || "?"); const n = [...m.keys()].sort((a, b) => m.get(b) - m.get(a));
    specs.cities = singleBar(n, n.map(k => m.get(k)), (i) => go("offres", { city: n[i] }), ["Zone", "Offres"]); }
  { const m = count(contractOf); const n = [...CONTRACTS, "Non précisé"].filter(k => m.has(k));
    specs.contracts = singleBar(n, n.map(k => m.get(k)), (i) => go("offres", { contract: n[i] }), ["Contrat", "Offres"]); }
  { const top = S.companies.filter(c => c.active).slice(0, 10);
    specs.employers = singleBar(top.map(c => c.name.length > 28 ? c.name.slice(0, 27) + "…" : c.name), top.map(c => c.active),
      (i) => go("offres", { q: top[i].name }), ["Employeur", "Offres actives"], top.map(c => c.name)); }
  { const labels = [], values = [], dates = []; const now = new Date();
    for (let d = 29; d >= 0; d--) {
      const dt = new Date(now.getFullYear(), now.getMonth(), now.getDate() - d);
      const iso = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
      dates.push(iso); labels.push(dt.toLocaleDateString("fr-FR", { day: "numeric", month: "short" }));
      values.push(offers.filter(o => String(o.posted_date || "").startsWith(iso)).length);
    }
    const s = singleBar(labels, values, (i) => go("offres", { day: dates[i] }), ["Date", "Offres"]);
    const opts = baseOptions(false); opts.scales.x.ticks.maxRotation = 0; opts.scales.x.ticks.autoSkipPadding = 12;
    s.config.options = opts; s.config.data.datasets[0].maxBarThickness = 14; s.height = 260;
    s.table.rows = s.table.rows.filter(r => r[1] > 0);
    specs.days = s; }
  { const reached = (set) => S.offers.filter(o => set.includes(o.status)).length;
    const stages = [["Favoris et +", reached(PIPE)], ["Postulées", reached([...APPLIED])], ["Entretiens", reached(["entretien", "offre"])], ["Offres reçues", reached(["offre"])]];
    specs.funnel = { height: 200,
      config: { type: "bar", data: { labels: stages.map(s => s[0]), datasets: [bar("Candidatures", stages.map(s => s[1]), ["--ord-1", "--ord-2", "--ord-3", "--ord-4"].map(css))] }, options: baseOptions(true) },
      table: { head: ["Étape", "Candidatures"], rows: stages }, click: () => go("candidatures"),
      empty: stages[0][1] ? null : "Aucune candidature suivie. Utilise la colonne Statut dans Offres." }; }
  { const h = S.runs.filter(r => r.finished_at && r.trigger !== "import").slice(0, 30).reverse();
    const labels = h.map(r => new Date(r.started_at).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }));
    const opts = baseOptions(false); opts.interaction = { mode: "index", intersect: false }; opts.plugins.tooltip.mode = "index"; opts.plugins.tooltip.intersect = false;
    const line = (label, data, v) => ({ label, data, borderColor: css(v), backgroundColor: css(v), borderWidth: 2, pointRadius: 4, pointHoverRadius: 6, pointBorderColor: css("--surface"), pointBorderWidth: 2, tension: .25 });
    specs.history = { height: 280,
      legend: [["Offres actives", "--series-1"], ["Faisant fonction", "--series-2"], ["Nouvelles", "--series-3"]],
      config: { type: "line", data: { labels, datasets: [line("Offres actives", h.map(r => r.offers_active), "--series-1"), line("Faisant fonction", h.map(r => r.ff), "--series-2"), line("Nouvelles", h.map(r => r.new_offers), "--series-3")] }, options: opts },
      table: { head: ["Recherche", "Actives", "Faisant fonction", "Nouvelles"], rows: h.map((r, i) => [labels[i], r.offers_active, r.ff, r.new_offers]) },
      empty: h.length ? null : "Pas encore d'historique." }; }
  return specs;
}

function renderCharts(scope) {
  const specs = chartSpecs();
  scope.querySelectorAll("[data-chart]").forEach(card => {
    const key = card.dataset.chart, spec = specs[key], slot = card.querySelector(".chart-slot");
    if (!spec || !slot) return;
    const head = card.querySelector(".card-head");
    if (!head.querySelector(".view-toggle")) head.insertAdjacentHTML("beforeend", `<button type="button" class="btn-ghost btn-sm view-toggle" data-key="${key}"></button>`);
    const tableMode = S.tableMode[key] || !HAS_CHART;
    const toggle = head.querySelector(".view-toggle");
    toggle.hidden = !HAS_CHART;
    toggle.innerHTML = tableMode ? `${icon("chart")}Graphique` : `${icon("table")}Tableau`;
    if (S.charts[key]) { S.charts[key].destroy(); delete S.charts[key]; }
    if (spec.empty) { slot.innerHTML = `<div class="empty">${esc(spec.empty)}</div>`; return; }
    if (tableMode) {
      slot.innerHTML = `<table class="chart-table"><thead><tr>${spec.table.head.map((h, i) => `<th scope="col" class="${i ? "n" : ""}">${esc(h)}</th>`).join("")}</tr></thead>
        <tbody>${spec.table.rows.map(r => `<tr>${r.map((c, i) => i ? `<td class="n">${fmtInt(c)}</td>` : `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      return;
    }
    const legend = spec.legend ? `<div class="legend">${spec.legend.map(([l, v]) => `<span><i style="background:${css(v)}"></i>${esc(l)}</span>`).join("")}</div>` : "";
    const summary = spec.table.rows.slice(0, 5).map(r => `${r[0]} : ${r[1]}`).join(", ");
    slot.innerHTML = `${legend}<div class="chart-box" style="height:${spec.height}px"><canvas role="img" aria-label="${esc(card.querySelector("h3").textContent)}. ${esc(summary)}"></canvas></div>`;
    const canvas = slot.querySelector("canvas");
    const chart = new Chart(canvas, spec.config);
    if (spec.click) {
      canvas.style.cursor = "pointer";
      canvas.onclick = (evt) => { const p = chart.getElementsAtEventForMode(evt, "nearest", { intersect: true }, true); if (p.length) spec.click(p[0].index, p[0].datasetIndex); };
    }
    S.charts[key] = chart;
  });
}
document.addEventListener("click", (e) => {
  const t = e.target.closest(".view-toggle");
  if (t) { S.tableMode[t.dataset.key] = !S.tableMode[t.dataset.key]; renderView(); }
  const k = e.target.closest("[data-go]");
  if (k) go(k.dataset.go, JSON.parse(k.dataset.params || "{}"));
});

/* ======================= Vue d'ensemble ======================= */
function renderOverview() {
  const offers = visible();
  const merged = offers.filter(o => o.sources.length > 1).length;
  const listings = offers.reduce((a, o) => a + o.listings.length, 0);
  const closed = S.offers.filter(o => !o.is_active).length;
  const reposted = offers.filter(o => o.reposted).length;
  const applied = S.offers.filter(o => APPLIED.has(o.status)).length;
  const kpi = (label, value, hint, params, view = "offres") =>
    `<button type="button" class="kpi" data-go="${view}" data-params='${esc(JSON.stringify(params || {}))}'><span class="label">${label}</span><span class="value">${fmtInt(value)}</span><span class="hint">${hint}</span></button>`;
  $("kpis").innerHTML = [
    kpi("Offres actives", offers.length, `${fmtInt(listings)} annonces · ${merged} sur plusieurs sites`, {}),
    kpi("Faisant fonction", offers.filter(o => o.ff).length, `${offers.filter(o => o.ff && !o.status).length} pas encore traitées`, { ff: "1" }),
    kpi("Sans diplôme exigé", offers.filter(o => !o.diploma_required).length, `${offers.filter(o => o.diploma_required).length} exigent le DEAS`, { nodip: "1" }),
    kpi("Nouvelles", offers.filter(isNew).length, "depuis la dernière recherche", { new: "1" }),
    kpi("Candidatures", applied, `${S.offers.filter(o => ["entretien", "offre"].includes(o.status)).length} entretiens`, {}, "candidatures"),
    kpi("Disparues", closed, `${reposted} republiées`, { closed: "1" }),
  ].join("");
  const last = S.runs.find(r => r.finished_at);
  $("apercu-sub").textContent = (last ? `Dernière recherche : ${new Date(last.finished_at).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" })}. ` : "")
    + (S.data.next_auto_run ? `Prochaine automatique : ${new Date(S.data.next_auto_run).toLocaleString("fr-FR", { weekday: "short", hour: "2-digit", minute: "2-digit" })}. ` : "")
    + "Clique sur un indicateur ou une barre pour ouvrir les offres.";

  const items = [];
  for (const o of S.offers) {
    const since = o.status_at ? Math.floor((Date.now() - new Date(o.status_at)) / 864e5) : 0;
    if (o.status === "entretien") items.push({ p: 0, o, tag: "Entretien", cls: "b-new", sub: `${o.company || ""}` });
    else if (o.status === "postule" && since >= 7) items.push({ p: 1, o, tag: "Relancer", cls: "b-repost", sub: `Postulé il y a ${since} j · ${o.company || ""}` });
    else if (o.status === "favori" && o.is_active) items.push({ p: 2, o, tag: "Postuler", cls: "b-new", sub: `Favori · ${o.company || ""}` });
  }
  offers.filter(o => !o.status && !o.diploma_required).sort((a, b) => b.score - a.score).slice(0, 6)
    .forEach(o => items.push({ p: 3, o, tag: `Score ${o.score}`, cls: o.ff ? "b-ff" : "b-ok", sub: `${o.company || "?"} · ${o.hub} · ${km(o.distance_km)} · ${ageLabel(daysAgo(o))}` }));
  items.sort((a, b) => a.p - b.p);
  $("todo").innerHTML = items.slice(0, 8).map(it => `
    <li><div class="t"><strong title="${esc(it.o.title)}">${esc(it.o.title)}</strong><span>${esc(it.sub)}</span></div>
      <span class="badge ${it.cls}">${esc(it.tag)}</span>
      <a class="btn btn-ghost btn-sm icon-btn" href="${esc(it.o.url)}" target="_blank" rel="noopener" aria-label="Ouvrir l'offre ${esc(it.o.title)}">${icon("ext")}</a></li>`).join("")
    || `<li class="empty" style="display:block"><strong>Rien d'urgent</strong>Lance une recherche pour trouver de nouvelles offres.</li>`;
  renderCharts($("view-apercu"));
  $("health-mini").innerHTML = healthTable(true);
}

/* ======================= Santé des sources ======================= */
function sourceState(st, running) {
  if (!st) return ["idle", "Désactivée"];
  if (st.errors && st.errors >= Math.max(1, st.done || 0)) return ["bad", "En échec"];
  if (st.errors) return ["warn", `${st.errors} erreur${st.errors > 1 ? "s" : ""}`];
  if (running && st.passes && st.done < st.passes) return ["idle", "En cours"];
  if ((st.done || 0) && !st.raw) return ["warn", "Aucun résultat"];
  if (st.done || st.raw || st.added) return ["good", "OK"];
  return ["idle", "En attente"];
}
function healthTable(compact) {
  const stats = S.data.stats || {};
  const names = S.allSources.length ? S.allSources : Object.keys(stats);
  const rows = names.map(n => {
    const st = stats[n]; const [cls, label] = sourceState(st, S.data.running);
    const ic = cls === "good" ? "check" : cls === "idle" ? "" : "alert";
    return `<tr><td>${esc(n)}</td><td><span class="st ${cls}"><span class="sd" aria-hidden="true"></span>${ic ? icon(ic) : ""}${esc(label)}</span></td>
      ${compact ? "" : `<td class="n">${st && st.passes ? `${st.done}/${st.passes}` : "—"}</td><td class="n">${st ? fmtInt(st.raw) : "—"}</td>`}
      <td class="n">${st ? fmtInt(st.added) : "—"}</td>
      ${compact ? "" : `<td class="n">${st && st.seconds ? Math.round(st.seconds) + " s" : "—"}</td><td class="muted" style="max-width:220px;overflow-wrap:anywhere">${st && st.last_error ? esc(st.last_error) : ""}</td>`}</tr>`;
  }).join("");
  return `<table class="chart-table"><thead><tr><th scope="col">Source</th><th scope="col">État</th>
    ${compact ? "" : `<th class="n" scope="col">Passes</th><th class="n" scope="col">Brutes</th>`}<th class="n" scope="col">Annonces</th>
    ${compact ? "" : `<th class="n" scope="col">Durée</th><th scope="col">Dernière erreur</th>`}</tr></thead><tbody>${rows}</tbody></table>`;
}

/* ======================= Offres ======================= */
const OF = { q: "", city: "", source: "", contract: "", status: "", age: "", dist: "", day: "", ff: false, nodip: false, new: false, closed: false, hidden: false };
const OF_TEXT = ["q", "city", "source", "contract", "status", "age", "dist", "day"];
const OF_BOOL = ["ff", "nodip", "new", "closed", "hidden"];
function readOfferFilters(p) {
  OF_TEXT.forEach(k => OF[k] = p.get(k) || "");
  OF_BOOL.forEach(k => OF[k] = p.get(k) === "1");
  if (p.get("sort")) { const [k, d] = p.get("sort").split(":"); S.sortKey = k; S.sortDir = d || "desc"; }
  fillOfferSelects();
  OF_TEXT.filter(k => k !== "day").forEach(k => $(`o-${k}`).value = OF[k]);
  OF_BOOL.forEach(k => $(`o-${k}`).checked = OF[k]);
}
function writeOfferFilters() {
  const p = {};
  OF_TEXT.forEach(k => { if (OF[k]) p[k] = OF[k]; });
  OF_BOOL.forEach(k => { if (OF[k]) p[k] = "1"; });
  if (!(S.sortKey === "score" && S.sortDir === "desc")) p.sort = `${S.sortKey}:${S.sortDir}`;
  const q = new URLSearchParams(p).toString();
  history.replaceState(null, "", `#/offres${q ? "?" + q : ""}`);
}
function fillOfferSelects() {
  const opt = (id, label, values, cur) => { $(id).innerHTML = `<option value="">${label}</option>` + values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join(""); $(id).value = cur; };
  opt("o-city", "Toutes les zones", [...new Set(S.offers.map(o => o.hub).filter(Boolean))].sort(), OF.city);
  opt("o-source", "Tous les sites", [...new Set(S.offers.flatMap(o => o.sources))].sort(), OF.source);
  opt("o-contract", "Tous contrats", [...CONTRACTS, "Non précisé"], OF.contract);
  $("o-status").innerHTML = `<option value="">Tous statuts</option><option value="_none">Nouvelle (sans statut)</option>` + STATUS.filter(s => s.id).map(s => `<option value="${s.id}">${s.label}</option>`).join("");
  $("o-status").value = OF.status;
  $("bulk-status").innerHTML = STATUS.map(s => `<option value="${s.id}">${s.id ? s.label : "Retirer le statut"}</option>`).join("");
}
function filteredOffers() {
  const q = fold(OF.q).trim();
  const list = S.offers.filter(o => {
    if (OF.closed ? o.is_active : !o.is_active) return false;
    if (!OF.hidden && o.status === "masque" && OF.status !== "masque") return false;
    if (OF.city && o.hub !== OF.city) return false;
    if (OF.source && !o.sources.includes(OF.source)) return false;
    if (OF.contract && contractOf(o) !== OF.contract) return false;
    if (OF.status === "_none" ? o.status : OF.status && o.status !== OF.status) return false;
    if (OF.ff && !o.ff) return false;
    if (OF.nodip && o.diploma_required) return false;
    if (OF.new && !isNew(o)) return false;
    if (OF.day && !String(o.posted_date || "").startsWith(OF.day)) return false;
    if (OF.age) { const d = daysAgo(o); if (d === null || d > +OF.age) return false; }
    if (OF.dist && (o.distance_km == null || o.distance_km > +OF.dist)) return false;
    if (q && !fold(`${o.title} ${o.company} ${o.location} ${o.description} ${o.note}`).includes(q)) return false;
    return true;
  });
  const dir = S.sortDir === "asc" ? 1 : -1;
  const val = { title: o => fold(o.title), city: o => fold(o.hub), dist: o => o.distance_km ?? 999, contract: contractOf, sites: o => o.sources.length,
    age: o => -(daysAgo(o) ?? -999), score: o => o.score, status: o => STATUS.findIndex(s => s.id === o.status) }[S.sortKey] || (o => o.score);
  return list.sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : 0) * dir || (daysAgo(a) ?? 99) - (daysAgo(b) ?? 99); });
}
function badges(o) {
  return [
    o.ff ? `<span class="badge b-ff">${icon("star")}Faisant fonction</span>` : "",
    o.diploma_required ? `<span class="badge b-de">DEAS exigé</span>` : "",
    o.beginner_ok ? `<span class="badge b-ok">Débutant accepté</span>` : "",
    isNew(o) ? `<span class="badge b-new">Nouvelle</span>` : "",
    o.reposted ? `<span class="badge b-repost">Republiée</span>` : "",
    !o.is_active ? `<span class="badge b-closed">Disparue</span>` : "",
    o.shift ? `<span class="badge b-src">${esc(o.shift)}</span>` : "",
    o.salary_max ? `<span class="badge b-src">${o.salary_min === o.salary_max ? "" : fmtInt(o.salary_min) + "–"}${fmtInt(o.salary_max)} €/mois</span>` : "",
    o.note ? `<span class="badge b-src">Note</span>` : "",
  ].join("");
}
const scoreCls = (s) => s >= 70 ? "hi" : s >= 50 ? "mid" : "";
function renderOffers() {
  fillOfferSelects();
  const list = filteredOffers();
  S.filtered = list;
  const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
  S.page = Math.min(S.page, pages - 1);
  const slice = list.slice(S.page * PAGE_SIZE, (S.page + 1) * PAGE_SIZE);
  $("o-count").textContent = `${fmtInt(list.length)} offre${list.length > 1 ? "s" : ""}` + (OF.day ? ` publiées le ${dateFr(OF.day, { dateStyle: "long" })}` : "");
  document.querySelectorAll("#o-table th[data-sort]").forEach(th => {
    const active = th.dataset.sort === S.sortKey;
    th.setAttribute("aria-sort", active ? (S.sortDir === "asc" ? "ascending" : "descending") : "none");
    th.querySelector("use").setAttribute("href", active ? (S.sortDir === "asc" ? "#i-up" : "#i-down") : "#i-sort");
  });
  if (!slice.length) {
    $("o-body").innerHTML = `<tr><td colspan="10"><div class="empty"><strong>${S.offers.length ? "Aucune offre ne correspond aux filtres" : "Aucune offre pour l'instant"}</strong>
      ${S.offers.length ? `<button type="button" class="btn-ghost btn-sm" data-go="offres" style="margin-top:8px">Réinitialiser les filtres</button>` : "Lance une recherche avec « Lancer »."}</div></td></tr>`;
  } else {
    $("o-body").innerHTML = slice.map(o => {
      const open = S.expanded.has(o.id), d = daysAgo(o);
      return `<tr class="row ${o.ff ? "is-ff" : ""} ${["masque", "refuse"].includes(o.status) || !o.is_active ? "dim" : ""}">
        <td><label class="sr-only" for="sel-${o.id}">Sélectionner</label><input type="checkbox" class="sel" id="sel-${o.id}" data-id="${o.id}" ${S.selected.has(o.id) ? "checked" : ""}/></td>
        <td><span class="score ${scoreCls(o.score)}" title="${esc(o.score_reasons.join("\n"))}">${o.score}</span></td>
        <td><div class="cell-title">
          <button type="button" class="ttl" data-expand="${o.id}" aria-expanded="${open}" aria-controls="det-${o.id}">${esc(o.title)}</button>
          <span class="co">${esc(o.company || "Employeur non indiqué")} · ${esc(o.location)}</span>
          <span class="cell-badges">${badges(o)}</span></div></td>
        <td>${esc(o.hub)}</td>
        <td class="num">${km(o.distance_km)}</td>
        <td>${esc(contractOf(o))}</td>
        <td><span class="badge ${o.sources.length > 1 ? "b-ok" : "b-src"}" title="${esc(o.sources.join(", "))}">${o.sources.length} site${o.sources.length > 1 ? "s" : ""}</span></td>
        <td class="num">${ageLabel(d)}</td>
        <td><label class="sr-only" for="st-${o.id}">Statut</label><select class="status" id="st-${o.id}" data-id="${o.id}">${STATUS.map(s => `<option value="${s.id}" ${s.id === o.status ? "selected" : ""}>${s.label}</option>`).join("")}</select></td>
        <td><a class="btn btn-ghost btn-sm icon-btn" href="${esc(o.url)}" target="_blank" rel="noopener" data-open="${o.id}" aria-label="Ouvrir l'offre (nouvel onglet)">${icon("ext")}</a></td>
      </tr>${open ? detailRow(o) : ""}`;
    }).join("");
  }
  $("o-pager").innerHTML = pages > 1 ? `<button type="button" class="btn-ghost btn-sm" data-page="${S.page - 1}" ${S.page === 0 ? "disabled" : ""}>Précédent</button>
    <span>Page ${S.page + 1} / ${pages}</span><button type="button" class="btn-ghost btn-sm" data-page="${S.page + 1}" ${S.page >= pages - 1 ? "disabled" : ""}>Suivant</button>` : "";
  const ids = slice.map(o => o.id);
  $("sel-all").checked = ids.length > 0 && ids.every(i => S.selected.has(i));
  updateSelection();
}
function highlight(text, match) {
  const t = esc(text || "Pas de description.");
  if (!match) return t;
  const words = fold(match).split(/\s+/).filter(w => w.length > 2).slice(0, 3);
  return words.length ? t.replace(new RegExp(`(${words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi"), `<mark class="hl">$1</mark>`) : t;
}
function detailRow(o) {
  const sig = o.signals || {};
  return `<tr class="detail" id="det-${o.id}"><td colspan="10"><div class="detail-grid">
    <div>
      <p class="desc">${highlight(o.description, sig.ff || sig.diploma || sig.beginner)}</p>
      <h4 class="section-title" style="margin-top:8px">Annonces (${o.listings.length})</h4>
      <div class="listings">${o.listings.map(l => `<div class="listing">
        <span class="badge b-src">${esc(l.source)}</span>
        <span class="lt">${esc(l.title)}<span class="why">${l.match_reason ? `Rattachée : ${esc(l.match_reason)}${l.match_confidence ? ` · confiance ${l.match_confidence} %` : ""}` : "Annonce d'origine"} · vue du ${dateFr(l.first_seen)} au ${dateFr(l.last_seen)}</span></span>
        <a class="btn btn-ghost btn-sm" href="${esc(l.url)}" target="_blank" rel="noopener">${icon("ext")}Ouvrir</a>
        ${o.listings.length > 1 ? `<button type="button" class="btn-ghost btn-sm" data-split="${l.id}">${icon("split")}Séparer</button>` : ""}
      </div>`).join("")}</div>
    </div>
    <div style="display:grid;gap:10px;align-content:start">
      <div><strong style="font-size:.875rem">Pourquoi ce score (${o.score}/100)</strong>
        <ul class="reasons">${o.score_reasons.map(r => `<li class="${r.startsWith("-") ? "neg" : ""}">${esc(r)}</li>`).join("") || "<li>Score de base</li>"}</ul></div>
      <dl>
        <dt>Employeur</dt><dd>${esc(o.company || "—")}</dd>
        <dt>Lieu</dt><dd>${esc(o.location || "—")} · ${km(o.distance_km)} de ${esc(o.hub)}</dd>
        <dt>Contrat</dt><dd>${esc(contractText(o))}${o.part_time ? " · temps partiel" : ""}</dd>
        <dt>Salaire</dt><dd>${esc(o.salary_text || "—")}</dd>
        <dt>Publiée</dt><dd>${esc(o.posted_date || "—")}</dd>
        <dt>Suivie depuis</dt><dd>${dateFr(o.first_seen, { dateStyle: "medium" })}${o.closed_at ? ` · disparue le ${dateFr(o.closed_at, { dateStyle: "medium" })}` : ""}</dd>
        ${o.status_history.length ? `<dt>Suivi</dt><dd>${o.status_history.map(h => `${esc(statusLabel(h.status))} (${dateFr(h.at)})`).join(" → ")}</dd>` : ""}
      </dl>
      <label for="note-${o.id}" style="font-weight:600;font-size:.875rem">Note personnelle</label>
      <textarea id="note-${o.id}" class="note" data-id="${o.id}" placeholder="Contact, date d'appel, questions…">${esc(o.note)}</textarea>
    </div></div></td></tr>`;
}
function updateSelection() {
  $("sel-box").hidden = S.selected.size === 0;
  $("sel-count").textContent = `${S.selected.size} sélectionnée${S.selected.size > 1 ? "s" : ""}`;
  $("bulk-merge").hidden = S.selected.size !== 2;
}
function bindOffers() {
  let t;
  $("o-q").addEventListener("input", e => { clearTimeout(t); t = setTimeout(() => { OF.q = e.target.value; S.page = 0; writeOfferFilters(); renderOffers(); }, 200); });
  ["city", "source", "contract", "status", "age", "dist"].forEach(k => $(`o-${k}`).addEventListener("change", e => { OF[k] = e.target.value; OF.day = ""; S.page = 0; writeOfferFilters(); renderOffers(); }));
  OF_BOOL.forEach(k => $(`o-${k}`).addEventListener("change", e => { OF[k] = e.target.checked; S.page = 0; writeOfferFilters(); renderOffers(); }));
  $("o-reset").addEventListener("click", () => go("offres"));
  $("sel-all").addEventListener("change", e => {
    (S.filtered || []).slice(S.page * PAGE_SIZE, (S.page + 1) * PAGE_SIZE).forEach(o => e.target.checked ? S.selected.add(o.id) : S.selected.delete(o.id));
    renderOffers();
  });
  $("bulk-apply").addEventListener("click", async () => {
    const ids = [...S.selected], status = $("bulk-status").value;
    await api("/api/offers/bulk", { ids, status });
    S.offers.forEach(o => { if (S.selected.has(o.id)) o.status = status; });
    S.selected.clear(); toast(`${ids.length} offre(s) : ${statusLabel(status)}`); renderView();
  });
  $("bulk-merge").addEventListener("click", async () => {
    const [a, b] = [...S.selected].map(id => S.offers.find(o => o.id === id)).sort((x, y) => y.listings.length - x.listings.length);
    if (!confirm(`Fusionner « ${b.title} » dans « ${a.title} » ? Elles seront considérées comme le même poste.`)) return;
    await api("/api/offers/merge", { keep: a.id, other: b.id });
    S.selected.clear(); await refreshData(); toast("Offres fusionnées");
  });
  $("o-export").addEventListener("click", () => {
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const head = ["score", "titre", "employeur", "lieu", "zone", "distance_km", "contrat", "salaire", "faisant_fonction", "deas_exige", "sites", "publiee", "statut", "note", "lien"];
    const rows = (S.filtered || []).map(o => [o.score, o.title, o.company, o.location, o.hub, o.distance_km, contractOf(o), o.salary_text, o.ff, o.diploma_required, o.sources.join(" + "), o.posted_date, statusLabel(o.status), o.note, o.url].map(q).join(";"));
    const blob = new Blob(["﻿" + head.join(";") + "\n" + rows.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `jobbot_offres_${new Date().toISOString().slice(0, 10)}.csv` });
    a.click(); URL.revokeObjectURL(a.href);
  });
  const table = $("o-table");
  table.addEventListener("click", async e => {
    const th = e.target.closest("th[data-sort] button");
    if (th) {
      const key = th.parentElement.dataset.sort;
      if (S.sortKey === key) S.sortDir = S.sortDir === "asc" ? "desc" : "asc";
      else { S.sortKey = key; S.sortDir = ["title", "city", "contract", "dist"].includes(key) ? "asc" : "desc"; }
      writeOfferFilters(); renderOffers(); return;
    }
    const ex = e.target.closest("[data-expand]");
    if (ex) { const id = +ex.dataset.expand; S.expanded.has(id) ? S.expanded.delete(id) : S.expanded.add(id); renderOffers(); return; }
    const sp = e.target.closest("[data-split]");
    if (sp) { await api(`/api/listings/${sp.dataset.split}/split`, {}); await refreshData(); toast("Annonce séparée : elle ne sera plus rattachée à cette offre"); return; }
    const op = e.target.closest("[data-open]");
    if (op) { const o = S.offers.find(x => x.id === +op.dataset.open); if (o && !o.status) setStatus(o.id, "vu"); }
  });
  table.addEventListener("change", e => {
    if (e.target.matches("select.status")) setStatus(+e.target.dataset.id, e.target.value);
    if (e.target.matches("input.sel")) { e.target.checked ? S.selected.add(+e.target.dataset.id) : S.selected.delete(+e.target.dataset.id); updateSelection(); }
  });
  table.addEventListener("focusout", e => {
    if (!e.target.matches("textarea.note")) return;
    const o = S.offers.find(x => x.id === +e.target.dataset.id);
    if (o && o.note !== e.target.value) setNote(o.id, e.target.value);
  });
  $("o-pager").addEventListener("click", e => { const b = e.target.closest("[data-page]"); if (b) { S.page = +b.dataset.page; renderOffers(); table.scrollIntoView({ block: "start" }); } });
}

/* ======================= Candidatures ======================= */
function renderPipeline() {
  const labels = { favori: "À postuler (favoris)", postule: "Postulé", relance: "Relancé", entretien: "Entretien", offre: "Offre reçue", refuse: "Refusé" };
  const appliedBy = new Map();
  S.offers.filter(o => APPLIED.has(o.status)).forEach(o => { const k = companyKey(o.company); if (k) appliedBy.set(k, (appliedBy.get(k) || 0) + 1); });
  $("pipeline").innerHTML = PIPE.map(st => {
    const items = S.offers.filter(o => o.status === st).sort((a, b) => String(b.status_at).localeCompare(String(a.status_at)));
    return `<div class="col" role="region" aria-label="${labels[st]}"><h3>${labels[st]} <span class="badge b-src">${items.length}</span></h3>
      ${items.map(o => {
        const since = o.status_at ? Math.floor((Date.now() - new Date(o.status_at)) / 864e5) : 0;
        const dupEmployer = st === "favori" && appliedBy.get(companyKey(o.company));
        return `<div class="pcard">
          <strong>${esc(o.title)}</strong>
          <span class="muted">${esc(o.company || "")} · ${esc(o.hub)} · ${km(o.distance_km)}</span>
          <span class="row2">
            ${st === "postule" && since >= 7 ? `<span class="st warn"><span class="sd" aria-hidden="true"></span>${icon("alert")}Relance conseillée</span>` : `<span class="muted">depuis ${since} j</span>`}
            ${!o.is_active ? `<span class="badge b-closed">Disparue</span>` : ""}
            ${dupEmployer ? `<span class="st warn"><span class="sd" aria-hidden="true"></span>Déjà postulé chez cet employeur</span>` : ""}
          </span>
          <span class="row2">
            <label class="sr-only" for="p-${o.id}">Étape</label>
            <select id="p-${o.id}" class="pstatus" data-id="${o.id}">${STATUS.filter(s => s.id !== "vu").map(s => `<option value="${s.id}" ${s.id === st ? "selected" : ""}>${s.id ? s.label : "Retirer du suivi"}</option>`).join("")}</select>
            <a class="btn btn-ghost btn-sm icon-btn" href="${esc(o.url)}" target="_blank" rel="noopener" aria-label="Ouvrir l'offre">${icon("ext")}</a>
          </span>
          <label class="sr-only" for="pn-${o.id}">Note</label>
          <textarea id="pn-${o.id}" class="pnote" data-id="${o.id}" placeholder="Note…">${esc(o.note)}</textarea>
        </div>`;
      }).join("") || `<p class="muted" style="margin:0;font-size:.8125rem">Vide</p>`}</div>`;
  }).join("");
}
function bindPipeline() {
  $("pipeline").addEventListener("change", e => { if (e.target.matches("select.pstatus")) setStatus(+e.target.dataset.id, e.target.value); });
  $("pipeline").addEventListener("focusout", e => {
    if (!e.target.matches("textarea.pnote")) return;
    const o = S.offers.find(x => x.id === +e.target.dataset.id);
    if (o && o.note !== e.target.value) setNote(o.id, e.target.value);
  });
}

/* ======================= Employeurs ======================= */
function renderEmployers() {
  const q = fold($("e-q").value);
  const list = S.companies.filter(c => !q || fold(c.name).includes(q));
  $("e-table").innerHTML = `<thead><tr><th class="static">Employeur</th><th class="static">Actives</th><th class="static">Total vues</th><th class="static">Faisant fonction</th>
    <th class="static">Republiées</th><th class="static">Tes candidatures</th><th class="static">Zones</th><th class="static">Sites</th><th class="static">Vu la 1re fois</th><th class="static"></th></tr></thead>
    <tbody>${list.map(c => `<tr class="row">
      <td><strong>${esc(c.name)}</strong></td><td class="num">${c.active}</td><td class="num">${c.offers}</td><td class="num">${c.ff}</td>
      <td class="num">${c.reposted ? `<span class="badge b-repost">${c.reposted}</span>` : "0"}</td>
      <td>${c.applied ? `<span class="st warn"><span class="sd" aria-hidden="true"></span>${c.applied} envoyée${c.applied > 1 ? "s" : ""}</span>` : `<span class="muted">—</span>`}</td>
      <td>${esc(c.cities.join(", "))}</td><td style="font-size:.8125rem">${esc(c.sources.join(", "))}</td>
      <td class="num">${dateFr(c.first_seen)}</td>
      <td><button type="button" class="btn-ghost btn-sm" data-go="offres" data-params='${esc(JSON.stringify({ q: c.name }))}'>Voir les offres</button></td>
    </tr>`).join("") || `<tr><td colspan="10"><div class="empty">Aucun employeur.</div></td></tr>`}</tbody>`;
}

/* ======================= Doublons ======================= */
function renderDuplicates() {
  const groups = S.offers.filter(o => o.listings.length > 1)
    .map(o => ({ o, conf: Math.min(...o.listings.filter(l => l.match_confidence).map(l => l.match_confidence), 100) }))
    .sort((a, b) => a.conf - b.conf);
  const listings = S.offers.reduce((a, o) => a + o.listings.length, 0);
  $("dup-kpis").innerHTML = [
    ["Annonces collectées", listings, "toutes sources"],
    ["Offres uniques", S.offers.length, `${fmtInt(listings - S.offers.length)} doublons évités`],
    ["Offres sur plusieurs sites", groups.length, `${groups.filter(g => g.conf < 90).length} à vérifier (confiance < 90 %)`],
  ].map(([l, v, h]) => `<div class="kpi"><span class="label">${l}</span><span class="value">${fmtInt(v)}</span><span class="hint">${h}</span></div>`).join("");
  $("dup-list").innerHTML = groups.map(({ o, conf }) => `<div class="card dup-card">
    <div class="card-head"><div><h3>${esc(o.title)}</h3><p class="card-sub">${esc(o.company || "?")} · ${esc(o.hub)} · ${o.listings.length} annonces</p></div>
      <span class="st ${conf >= 90 ? "good" : "warn"}"><span class="sd" aria-hidden="true"></span>${icon(conf >= 90 ? "check" : "alert")}Confiance ${conf} %</span></div>
    <div class="listings">${o.listings.map(l => `<div class="listing">
      <span class="badge b-src">${esc(l.source)}</span>
      <span class="lt">${esc(l.title)} — ${esc(l.company || "employeur non indiqué")}<span class="why">${l.match_reason ? esc(l.match_reason) : "Annonce d'origine"}</span></span>
      <a class="btn btn-ghost btn-sm" href="${esc(l.url)}" target="_blank" rel="noopener">${icon("ext")}Ouvrir</a>
      ${l.match_reason && !l.match_reason.includes("manuellement") ? `<button type="button" class="btn-ghost btn-sm" data-split="${l.id}">${icon("split")}Séparer</button>` : ""}
    </div>`).join("")}</div></div>`).join("") || `<div class="empty"><strong>Aucun doublon détecté</strong>Les offres publiées sur plusieurs sites apparaîtront ici.</div>`;
}
function bindDuplicates() {
  $("dup-list").addEventListener("click", async e => {
    const sp = e.target.closest("[data-split]");
    if (!sp) return;
    await api(`/api/listings/${sp.dataset.split}/split`, {});
    await refreshData(); toast("Annonce séparée");
  });
}

/* ======================= Recherche (config) ======================= */
const lines = (id) => $(id).value.split("\n").map(s => s.trim()).filter(Boolean);
let draftCities = [], draftRadius = {};
function formConfig() {
  return {
    sources: [...document.querySelectorAll("#cfg-sources input:checked")].map(i => i.value),
    cities: draftCities.slice(),
    city_radius: Object.fromEntries(draftCities.filter(c => draftRadius[c]).map(c => [c, +draftRadius[c]])),
    strict_radius: $("cfg-strict").checked,
    source_terms: lines("cfg-source-terms"), indeed_terms: lines("cfg-indeed-terms"),
    radius_km: +$("cfg-radius").value, max_pages: +$("cfg-pages").value,
    enrich: $("cfg-enrich").checked, max_enrich: +$("cfg-max-enrich").value,
    schedule: { auto_run: $("cfg-auto").checked, interval_h: +$("cfg-interval").value, quiet_start: +$("cfg-quiet-start").value, quiet_end: +$("cfg-quiet-end").value },
  };
}
function fillForm(cfg) {
  $("cfg-sources").innerHTML = S.allSources.map(s => `<label class="check"><input type="checkbox" value="${esc(s)}" ${cfg.sources.includes(s) ? "checked" : ""}/>${esc(s)}</label>`).join("");
  $("cfg-radius").value = cfg.radius_km;
  draftCities = cfg.cities.slice(); draftRadius = { ...(cfg.city_radius || {}) };
  renderCities();
  $("cfg-strict").checked = cfg.strict_radius !== false;
  $("cfg-source-terms").value = cfg.source_terms.join("\n");
  $("cfg-indeed-terms").value = cfg.indeed_terms.join("\n");
  $("cfg-pages").value = cfg.max_pages; $("cfg-enrich").checked = cfg.enrich; $("cfg-max-enrich").value = cfg.max_enrich;
  const sc = cfg.schedule || {};
  $("cfg-auto").checked = !!sc.auto_run; $("cfg-interval").value = sc.interval_h ?? 6;
  $("cfg-quiet-start").value = sc.quiet_start ?? 22; $("cfg-quiet-end").value = sc.quiet_end ?? 7;
  updateEstimate();
}
function renderCities() {
  $("cfg-cities").innerHTML = draftCities.map((c, i) => `<span class="chip">${esc(c)}
    <label class="sr-only" for="rad-${i}">Rayon pour ${esc(c)} en km</label>
    <input type="number" id="rad-${i}" class="city-radius" data-city="${esc(c)}" min="1" max="100" placeholder="${esc($("cfg-radius").value || 10)}" value="${draftRadius[c] || ""}"/><span class="km">km</span>
    <button type="button" data-rm="${i}" aria-label="Retirer ${esc(c)}">${icon("x")}</button></span>`).join("") || `<span class="muted" style="font-size:.875rem">Aucune ville</span>`;
}
function updateEstimate() {
  const c = formConfig();
  const other = c.sources.filter(s => s !== "Indeed").length, indeed = c.sources.includes("Indeed") ? c.indeed_terms.length : 0;
  const passes = c.cities.length * (indeed + other * c.source_terms.length);
  const requests = c.cities.length * (indeed + other * c.source_terms.length * c.max_pages);
  const perim = c.cities.map(x => `${esc(x)} ${c.city_radius[x] || c.radius_km} km`).join(" · ");
  $("cfg-estimate").innerHTML = `Périmètre : ${perim || "aucune ville"}${c.strict_radius ? " (strict)" : ""}<br/><strong>${fmtInt(passes)}</strong> recherches · ~${fmtInt(requests)} pages · durée estimée ~${Math.max(1, Math.round(requests * 0.9 / 120))} min (+ descriptions des nouvelles offres)`;
  const saved = S.config ? JSON.stringify(S.config) : "";
  $("cfg-state").textContent = S.config && JSON.stringify({ ...S.config, ...c }) !== saved ? "Modifications non enregistrées" : "Enregistré";
  $("next-run").textContent = S.data.next_auto_run ? `Prochaine recherche automatique : ${new Date(S.data.next_auto_run).toLocaleString("fr-FR", { weekday: "long", hour: "2-digit", minute: "2-digit" })}` : "Recherche automatique désactivée.";
}
async function saveConfig() {
  const res = await api("/api/config", { config: formConfig() });
  S.config = res.config; fillForm(S.config); $("cfg-state").textContent = "Enregistré";
  return S.config;
}
function bindConfig() {
  $("cfg-form").addEventListener("input", updateEstimate);
  const addCity = () => {
    const v = $("city-input").value.trim();
    if (v && !draftCities.some(c => fold(c) === fold(v))) draftCities.push(v.charAt(0).toUpperCase() + v.slice(1));
    $("city-input").value = ""; renderCities(); updateEstimate(); $("city-input").focus();
  };
  $("city-add").addEventListener("click", addCity);
  $("city-input").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); addCity(); } });
  $("cfg-cities").addEventListener("click", e => { const b = e.target.closest("[data-rm]"); if (b) { delete draftRadius[draftCities[+b.dataset.rm]]; draftCities.splice(+b.dataset.rm, 1); renderCities(); updateEstimate(); } });
  $("cfg-cities").addEventListener("input", e => { if (e.target.matches(".city-radius")) { const v = +e.target.value; if (v > 0) draftRadius[e.target.dataset.city] = Math.min(100, v); else delete draftRadius[e.target.dataset.city]; } });
  $("cfg-save").addEventListener("click", () => saveConfig().then(() => toast("Paramètres enregistrés")).catch(err => $("cfg-state").textContent = err.message));
  $("cfg-defaults").addEventListener("click", () => { fillForm(S.defaults); $("cfg-state").textContent = "Valeurs par défaut (non enregistrées)"; });
  $("cfg-run").addEventListener("click", async () => { try { await saveConfig(); await startRun(); } catch (err) { $("cfg-state").textContent = err.message; } });
  $("log-errors").addEventListener("change", () => { S.logKey = ""; renderLog(); });
}
function renderLog() {
  const logs = S.data.logs || [], onlyErr = $("log-errors").checked;
  const key = `${logs.length}|${onlyErr}|${logs.length ? logs[logs.length - 1].msg : ""}`;
  if (key === S.logKey) return;
  S.logKey = key;
  const isErr = (m) => /erreur|echec|échec|http \d|introuvable|inconnue/i.test(m);
  const shown = onlyErr ? logs.filter(l => isErr(l.msg)) : logs;
  $("log").innerHTML = shown.map(l => `<span class="t">${esc(l.t)}</span><span class="${isErr(l.msg) ? "err" : /Termine|Base:|Perimetre/.test(l.msg) ? "ok" : ""}">${esc(l.msg)}</span>`).join("\n") || (onlyErr ? "Aucune erreur." : "Journal vide (il se remplit pendant une recherche).");
  $("log-sub").textContent = `${logs.length} lignes · ${logs.filter(l => isErr(l.msg)).length} erreurs`;
  if ($("log-follow").checked) $("log").scrollTop = $("log").scrollHeight;
}
function renderSearch() {
  if (S.config && !$("cfg-sources").children.length) fillForm(S.config);
  $("health-full").innerHTML = healthTable(false);
  const p = S.data.progress || {};
  $("health-sub").textContent = S.data.running ? `En cours : ${p.label || ""}` : "Dernière recherche";
  renderLog();
}

/* ======================= Historique ======================= */
function renderHistory() {
  renderCharts($("view-historique"));
  const cell = (s) => s === "ok" ? `<span class="st good"><span class="sd" aria-hidden="true"></span>${icon("check")}Terminée</span>`
    : s === "arrete" ? `<span class="st warn"><span class="sd" aria-hidden="true"></span>${icon("alert")}Arrêtée</span>`
    : s === "running" ? `<span class="st idle"><span class="sd" aria-hidden="true"></span>En cours</span>`
    : `<span class="st bad"><span class="sd" aria-hidden="true"></span>${icon("alert")}${esc(s)}</span>`;
  $("h-table").innerHTML = `<thead><tr><th class="static">Date</th><th class="static">Type</th><th class="static">Durée</th><th class="static">État</th>
    <th class="static">Annonces</th><th class="static">Nouvelles</th><th class="static">Doublons</th><th class="static">Disparues</th><th class="static">Republiées</th>
    <th class="static">Actives</th><th class="static">Périmètre</th><th class="static"></th></tr></thead>
    <tbody>${S.runs.map(r => `<tr class="row">
      <td class="num">${new Date(r.started_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}</td>
      <td>${esc(r.trigger)}</td><td class="num">${Math.floor((r.duration_s || 0) / 60)} min ${(r.duration_s || 0) % 60} s</td><td>${cell(r.status)}</td>
      <td class="num">${fmtInt(r.listings)}</td><td class="num">${fmtInt(r.new_offers)}</td><td class="num">${fmtInt(r.merged)}</td>
      <td class="num">${fmtInt(r.closed)}</td><td class="num">${fmtInt(r.reposted)}</td><td class="num">${fmtInt(r.offers_active)}</td>
      <td class="muted" style="font-size:.8125rem">${r.config && r.config.cities ? esc(r.config.cities.map(c => `${c} ${(r.config.city_radius || {})[c] || r.config.radius_km} km`).join(", ")) : ""}</td>
      <td>${r.config && r.config.cities ? `<button type="button" class="btn-ghost btn-sm" data-rerun="${r.id}">Réutiliser</button>` : ""}</td>
    </tr>`).join("") || `<tr><td colspan="12"><div class="empty">Aucune recherche.</div></td></tr>`}</tbody>`;
}
function bindHistory() {
  $("h-table").addEventListener("click", e => {
    const b = e.target.closest("[data-rerun]");
    if (!b) return;
    const r = S.runs.find(x => x.id === +b.dataset.rerun);
    go("recherche");
    setTimeout(() => { fillForm({ ...S.defaults, ...r.config, schedule: S.config.schedule }); $("cfg-state").textContent = "Paramètres repris de l'historique (non enregistrés)"; }, 0);
  });
}

/* ======================= En-tête, alertes, thème ======================= */
async function startRun() {
  $("btn-start").disabled = true;
  try { await api("/api/start", {}); if (S.view !== "recherche") go("recherche"); }
  catch (err) { toast("Impossible de lancer : " + err.message); $("btn-start").disabled = false; }
}
function renderHeader() {
  const d = S.data, running = d.running;
  $("btn-start").disabled = running; $("btn-stop").disabled = !running;
  $("start-label").textContent = running ? "En cours…" : "Lancer";
  $("start-icon").innerHTML = `<use href="#i-${running ? "loader" : "play"}"/>`;
  $("start-icon").classList.toggle("spin", running);
  const fin = d.finished_at ? new Date(d.finished_at) : null;
  $("pill").className = "pill " + (running ? "running" : d.error ? "error" : fin ? "done" : "");
  $("pill-text").textContent = running ? `Recherche ${d.trigger || ""}` : d.error ? "Erreur" : fin && !isNaN(fin) ? `À jour · ${fin.toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}` : "Prêt";
  const p = d.progress || {};
  const pct = running ? (p.total ? Math.round(100 * p.current / p.total) : 2) : 0;
  $("bar").style.transform = `scaleX(${pct / 100})`;
  $("bar-wrap").setAttribute("aria-valuenow", pct);
  $("prog-label").textContent = d.error ? `Erreur : ${d.error}` : running ? `${p.current || 0}/${p.total || 0} · ${p.label || ""}` : "";
  $("nav-offres").textContent = fmtInt(visible().length);
  $("nav-cand").textContent = fmtInt(S.offers.filter(o => PIPE.includes(o.status)).length);
  $("nav-dup").textContent = fmtInt(S.offers.filter(o => o.listings.length > 1).length);
  $("btn-notif").classList.toggle("btn-primary", "Notification" in window && Notification.permission === "granted");
}
function notifyNewOffers() {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const fresh = visible().filter(o => isNew(o) && (o.ff || (!o.diploma_required && o.score >= 60)));
  if (!fresh.length) return;
  const n = new Notification(`JobBot : ${fresh.length} nouvelle(s) offre(s) intéressante(s)`, {
    body: fresh.slice(0, 3).map(o => `• ${o.title} (${o.hub}, score ${o.score})`).join("\n"), tag: "jobbot-new",
  });
  n.onclick = () => { window.focus(); go("offres", { new: "1" }); };
}
$("btn-notif").addEventListener("click", async () => {
  if (!("Notification" in window)) return toast("Ton navigateur ne gère pas les notifications.");
  const p = await Notification.requestPermission();
  toast(p === "granted" ? "Alertes activées : tu seras prévenu des nouvelles offres intéressantes (onglet ouvert)." : "Alertes refusées par le navigateur.");
  renderHeader();
});
const THEMES = ["auto", "light", "dark"];
let theme = "auto";
try { theme = localStorage.getItem("jobbot-theme") || "auto"; } catch {}
function applyTheme(t) {
  if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", t);
  $("theme-icon").innerHTML = `<use href="#i-${t === "light" ? "sun" : t === "dark" ? "moon" : "auto"}"/>`;
  $("btn-theme").setAttribute("aria-label", `Thème : ${t === "auto" ? "automatique" : t === "light" ? "clair" : "sombre"}`);
}
$("btn-theme").addEventListener("click", () => {
  theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
  try { localStorage.setItem("jobbot-theme", theme); } catch {}
  applyTheme(theme); renderView();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => renderView());

/* ======================= Boucle ======================= */
function renderView() {
  renderHeader();
  ({ apercu: renderOverview, offres: renderOffers, candidatures: renderPipeline, employeurs: renderEmployers,
     doublons: renderDuplicates, recherche: renderSearch, historique: renderHistory })[S.view]();
}
async function refreshData() { await loadData(); S.version = S.data.data_version; renderView(); }
const typing = () => document.activeElement && document.activeElement.matches("textarea, input[type=search], input[type=text], input[type=number]");

async function poll() {
  try {
    S.data = await api("/api/state");
    const finished = S.lastFinished !== undefined && S.data.finished_at !== S.lastFinished && !S.data.running;
    S.lastFinished = S.data.finished_at;
    if (S.data.data_version !== S.version && !S.data.running && !typing()) {
      await refreshData();
      if (finished) { notifyNewOffers(); toast("Recherche terminée : données mises à jour"); }
    } else if (S.view === "recherche") { renderHeader(); renderSearch(); }
    else renderHeader();
  } catch (e) {
    $("pill").className = "pill error"; $("pill-text").textContent = "Serveur injoignable";
  }
  setTimeout(poll, S.data.running ? 1000 : 4000);
}

(async function init() {
  applyTheme(theme);
  bindOffers(); bindPipeline(); bindDuplicates(); bindConfig(); bindHistory();
  $("e-q").addEventListener("input", renderEmployers);
  $("btn-start").addEventListener("click", startRun);
  $("btn-stop").addEventListener("click", async () => { $("btn-stop").disabled = true; await api("/api/stop", {}); });
  window.addEventListener("hashchange", onRoute);
  try {
    const cfg = await api("/api/config");
    S.config = cfg.config; S.defaults = cfg.defaults; S.allSources = cfg.all_sources;
    S.data = await api("/api/state");
    S.lastFinished = S.data.finished_at;
    await loadData();
    S.version = S.data.data_version;
  } catch (err) { $("pill-text").textContent = "Erreur : " + err.message; }
  onRoute();
  setTimeout(poll, 1500);
})();
