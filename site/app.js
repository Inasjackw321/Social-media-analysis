// Renders scan reports from data/index.json and data/reports/<id>.json.
// All post text comes from social media, so it is only ever inserted as text.

const PLATFORM_NAMES = { youtube: "YouTube", twitter: "X / Twitter", facebook: "Facebook", instagram: "Instagram", file: "File" };
const app = document.getElementById("app");
const select = document.getElementById("report-select");

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "href") { if (/^https?:\/\//i.test(v)) node.href = v; }
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c != null && c !== false) node.append(c instanceof Node ? c : String(c));
  }
  return node;
}

const fmt = (n) => Intl.NumberFormat(undefined, { notation: "compact" }).format(n || 0);
const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "");
const platName = (p) => PLATFORM_NAMES[p] || p;

async function getJSON(path) {
  const res = await fetch(path, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

function reportIdFromHash() {
  return new URLSearchParams(location.hash.slice(1)).get("report");
}

async function init() {
  let index;
  try {
    index = await getJSON("data/index.json");
  } catch {
    index = { reports: [] };
  }
  if (!index.reports.length) {
    select.disabled = true;
    select.append(el("option", {}, "No scans yet"));
    app.replaceChildren(el("div", { class: "empty" },
      el("h2", {}, "No scans yet"),
      el("p", {}, "Run the “Scan social media” workflow in GitHub Actions (or ask Claude to run a scan) and the results will show up here.")));
    return;
  }
  for (const r of index.reports) {
    const q = r.query && r.query.length ? r.query.join(", ") : "everything";
    select.append(el("option", { value: r.id }, `${when(r.generated_at)} — ${q}`));
  }
  select.addEventListener("change", () => { location.hash = `report=${encodeURIComponent(select.value)}`; });
  window.addEventListener("hashchange", () => show(reportIdFromHash() || index.reports[0].id));
  show(reportIdFromHash() || index.reports[0].id);
}

async function show(id) {
  select.value = id;
  try {
    render(await getJSON(`data/reports/${encodeURIComponent(id)}.json`));
  } catch (e) {
    app.replaceChildren(el("div", { class: "empty" }, el("h2", {}, "Couldn’t load that scan"), el("p", { class: "muted" }, e.message)));
  }
}

function render(r) {
  const q = r.query && r.query.length ? r.query.join(", ") : "general (no search terms)";
  const maxScore = Math.max(1, ...r.topics.map((t) => t.score));
  const byPlat = r.totals.by_platform || {};

  app.replaceChildren(...[
    el("p", { class: "meta" },
      "Search: ", el("strong", {}, q), ` · last ${r.lookback_hours}h · scanned ${when(r.generated_at)}`),

    el("div", { class: "stats" },
      stat(fmt(r.totals.posts), "posts scanned"),
      stat(r.topics.length, "topics found"),
      stat(fmt(r.totals.engagement), "total engagement"),
      ...Object.entries(byPlat).map(([p, n]) => stat(fmt(n), `${platName(p)} posts`))),

    el("div", { class: "sources" },
      Object.entries(r.sources).map(([name, s]) =>
        el("span", { class: `src ${s.status}`, title: s.message || "" },
          el("span", { class: "dot" }),
          `${platName(name)}: ${s.status === "ok" ? `${s.count} posts` : s.status}`))),

    el("h2", {}, "Topics"),
    r.topics.length
      ? r.topics.map((t, i) => topicCard(t, i, maxScore))
      : el("p", { class: "muted" }, "No topic was mentioned by enough different accounts. Try a longer lookback or broader search terms."),

    r.hashtags && r.hashtags.length
      ? [el("h2", {}, "Top hashtags"),
         el("div", { class: "tags" }, r.hashtags.map((h) => el("span", { class: "kw" }, `${h.tag} · ${h.count}`)))]
      : [],

    skippedNotes(r.sources),
  ].flat(2));
}

function stat(n, label) {
  return el("div", { class: "stat" }, el("div", { class: "n" }, n), el("div", { class: "l" }, label));
}

function topicCard(t, i, maxScore) {
  const pct = Math.max(3, Math.round((t.score / maxScore) * 100));
  return el("article", { class: "topic" },
    el("div", { class: "topic-head" },
      el("span", { class: "rank" }, i + 1),
      el("span", { class: "label" }, t.label),
      t.new ? el("span", { class: "badge-new" }, "new") : null,
      el("span", { class: "counts" }, `${t.post_count} posts · ${fmt(t.engagement)} engagement`)),
    el("div", { class: "bar", "aria-hidden": "true" }, el("span", { style: `width:${pct}%` })),
    el("div", { class: "row" },
      Object.entries(t.platforms).map(([p, n]) => el("span", { class: `plat ${p}` }, `${platName(p)} ${n}`)),
      t.keywords.length > 1 ? [el("span", { class: "muted" }, "·"), t.keywords.slice(1).map((k) => el("span", { class: "kw" }, k))] : []),
    el("details", {},
      el("summary", {}, `Top posts (${t.samples.length})`),
      el("ul", { class: "samples" }, t.samples.map((s) =>
        el("li", {},
          el("div", { class: "who" },
            el("span", { class: `plat ${s.platform}` }, platName(s.platform)),
            ` · ${[s.author || "unknown", when(s.created_at), `${fmt(s.engagement)} engagement`].filter(Boolean).join(" · ")} · `,
            el("a", { href: s.url, target: "_blank", rel: "noopener" }, "open")),
          el("div", { class: "txt" }, s.text))))));
}

function skippedNotes(sources) {
  const notes = Object.entries(sources).filter(([, s]) => s.status !== "ok");
  if (!notes.length) return [];
  return [el("h2", {}, "Platforms not included"),
    el("ul", { class: "muted" }, notes.map(([p, s]) => el("li", {}, el("strong", {}, platName(p)), `: ${s.message}`)))];
}

init();
