// Connections: the collaboration network, a person focus, and the concept landscape. Each is
// drawn as a graph and also available as sortable tables with the same nodes and
// relationships. The view, its focus, and its filters are in the URL.

import { get } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { number } from "../core/format.js";
import { navigate, onNavigate, query, setQuery } from "../core/router.js";
import { session } from "../core/session.js";
import { graphView } from "../components/graph-view.js";
import { sortableTable } from "../components/sortable-table.js";
import { card, debounce, emptyState, pageHeader, segmented, tabs } from "../components/ui.js";
import { missedContent } from "./missed.js";
import { show } from "./page.js";
import { unitsContent } from "./units.js";

const VIEWS = [
  { id: "network", label: "Collaboration" },
  { id: "focus", label: "Person focus" },
  { id: "landscape", label: "Concept landscape" },
  { id: "missed", label: "Missed connections" },
  { id: "units", label: "Units" },
];
const TRAIL = 5;
const names = new Map(); // person id -> name, from every graph loaded (for breadcrumbs)
let active = null; // the graph on screen; stopped when replaced or when the page is left

onNavigate((current) => {
  if (current.pathname !== "/graph" && active) { active.destroy(); active = null; }
});

export function graph(main, ctx = {}) {
  const params = query();
  const view = VIEWS.some((v) => v.id === params.get("view")) ? params.get("view") : "network";
  const person = params.get("person") || (view === "focus" ? session()?.person_id : null) || null;
  const trail = (params.get("trail") ?? "").split(",").filter(Boolean).slice(-TRAIL);
  const state = { view, person, trail, list: params.get("list") === "1", params };
  return show(main, {
    title: "Connections",
    quiet: ctx.samePage,
    load: () => load(state),
    view: (data) => page(state, data),
  });
}

async function load({ view, person, trail, params }) {
  if (view === "network") {
    return get("/api/v1/graph/network", {
      unit: params.get("unit"), max_people: params.get("max"), min_similarity: params.get("min"),
      similar: params.get("similar") === "0" ? "false" : null,
    });
  }
  if (view === "landscape") {
    return get("/api/v1/graph/landscape", { max_concepts: params.get("max"), min_people: params.get("people") });
  }
  if (view === "missed") {
    return get("/api/v1/graph/missed", { unit: params.get("unit"), across: params.get("across") === "1" ? "true" : null,
      min_similarity: params.get("min"), offset: params.get("offset"), limit: 25 });
  }
  if (view === "units") return get("/api/v1/graph/units", { max_units: params.get("max") });
  if (!person) return { pick: true };
  const id = encodeURIComponent(person);
  const [body, similar] = await Promise.all([
    get(`/api/v1/graph/focus/${id}`),
    get(`/api/v1/people/${id}/similar`).then((r) => r.data).catch(() => []),
  ]);
  await Promise.all(trail.filter((t) => !names.has(t)).map((t) =>
    get(`/api/v1/people/${encodeURIComponent(t)}`).then((r) => names.set(t, r.data.name)).catch(() => names.set(t, "Someone"))));
  return { ...body, similar };
}

function page(state, body) {
  const header = pageHeader({ eyebrow: "Explore", title: "Connections",
    meta: h("span", {}, "Who works together, who shares interests, and how research areas connect.") });
  const viewTabs = tabs(VIEWS, state.view, (id) => setQuery({ view: id, list: state.list ? "1" : null }, { replace: false }));
  if (body.pick) {
    return h("div", { class: "stack-lg" }, header, viewTabs,
      card({ pad: true, body: h("div", { class: "stack" },
        h("p", { class: "muted" }, "Choose someone to see their top topics and the people who share them."),
        personPicker(state)) }));
  }
  if (!body.meta.ready) {
    return h("div", { class: "stack-lg" }, header, viewTabs, card({ body: emptyState({ iconName: "network", title: "Not ready yet", text: body.meta.note }) }));
  }
  if (state.view === "missed" || state.view === "units") {
    active?.destroy();
    active = null;
    const content = state.view === "missed" ? missedContent(state, body) : unitsContent(state, body);
    const mode = state.view === "units" && body.data.units.length >= 2
      ? h("div", { class: "toolbar graph-toolbar" }, h("span", { class: "spacer" }),
        segmented([{ id: "graph", label: "Matrix", icon: "grid" }, { id: "list", label: "List", icon: "list" }],
          state.list ? "list" : "graph", (id) => setQuery({ list: id === "list" ? "1" : null }), "Show as"))
      : null;
    return h("div", { class: "stack-lg" }, header, viewTabs, mode, ...content);
  }
  const { nodes, edges } = body.data;
  for (const node of nodes) if (node.kind === "person" && node.label) names.set(node.id, node.label);
  const look = LOOKS[state.view](nodes, edges);

  const side = h("aside", { class: "graph-side", "aria-live": "polite" });
  const detail = (node) => side.replaceChildren(...(node && !node.focus ? nodeDetail(state, node, nodes, edges) : overview(state, body, look)).filter(Boolean));
  detail(null);

  let stage;
  let search = () => [];
  if (!nodes.length || (state.view !== "focus" && !edges.length)) {
    stage = card({ body: emptyState({ iconName: "network", title: "Nothing to show",
      text: state.view === "network" ? "No one here has shared papers, grants, studies, or close interests yet. Try another unit or a lower similarity." : "Too few people share topics yet." }) });
  } else if (state.list) {
    const list = graphList(state, nodes, edges);
    stage = list.element;
    search = list.filter;
  } else {
    active?.destroy();
    active = graphView({
      nodes, edges, look, label: `${VIEWS.find((v) => v.id === state.view).label}: ${number(nodes.length)} nodes and ${number(edges.length)} links`,
      on: {
        select: (node) => detail(node),
        open: (node) => openNode(state, node),
      },
    });
    stage = active.element;
    search = (text) => active.search(text);
  }

  const found = h("span", { class: "subtle tabular", "aria-live": "polite" });
  const finder = h("input", { class: "input", type: "search", placeholder: state.list ? "Filter the list…" : "Find in this graph…",
    "aria-label": state.list ? "Filter the list" : "Find in this graph", autocomplete: "off" });
  const runSearch = () => {
    const hits = search(finder.value);
    found.textContent = finder.value.trim() ? `${number(hits.length)} ${hits.length === 1 ? "match" : "matches"}` : "";
    return hits;
  };
  finder.addEventListener("input", debounce(runSearch, 120));
  finder.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || state.list || !active) return;
    event.preventDefault();
    const [first] = runSearch();
    if (first) { active.flyTo(first.id); active.select(first.id); detail(first); }
  });

  const mode = segmented([{ id: "graph", label: "Graph", icon: "network" }, { id: "list", label: "List", icon: "list" }],
    state.list ? "list" : "graph", (id) => setQuery({ list: id === "list" ? "1" : null }), "Show as");
  const tools = h("div", { class: "toolbar graph-toolbar" },
    h("div", { class: "search-field" }, icon("search"), finder), found, h("span", { class: "spacer" }), mode);

  return h("div", { class: "stack-lg" }, header, viewTabs,
    state.view === "focus" ? breadcrumbs(state, nodes) : null,
    controls(state, body),
    tools,
    state.list ? stage : h("div", { class: "graph-layout" }, h("div", { class: "graph-main" }, stage), side),
    h("p", { class: "subtle" }, "Similarity reflects the topics of people's papers. It says nothing about quality or availability."));
}

function openNode(state, node) {
  if (node.kind === "person") focusOn(state, node.id);
  else if (node.kind === "concept") navigate(`/experts?concept=${node.id.replace("msh:", "")}`);
}

function focusOn(state, id) {
  const trail = state.view === "focus" && state.person && state.person !== id ? [...state.trail, state.person].slice(-TRAIL) : [];
  setQuery({ view: "focus", person: id, trail: trail.join(",") || null, unit: null, max: null, min: null, similar: null, people: null }, { replace: false });
}

// --- how each view looks -------------------------------------------------------------------

const personFill = (node) => (node.status === "former" ? "var(--text-subtle)" : "var(--ent-person)");

/** Colour people by group where the view spans several, otherwise by collaboration cluster;
 * hues go to the largest groups first, so the main groups never share a colour. */
function colouring(nodes) {
  const byUnit = new Set(nodes.map((n) => n.group).filter(Boolean)).size > 1;
  const keyOf = (n) => (byUnit ? n.group : n.cluster);
  const counts = new Map();
  for (const n of nodes) if (keyOf(n)) counts.set(keyOf(n), (counts.get(keyOf(n)) ?? 0) + 1);
  // Six distinct series colours; any further, smaller groups share a neutral one.
  const legend = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([label, people], i) => ({ label, people, colour: i < 6 ? `var(--series-${i})` : "var(--ent-unit)" }));
  const colourOf = new Map(legend.map((g) => [g.label, g.colour]));
  return {
    byUnit,
    legend: legend.length > 1 ? legend : [],
    color: (n) => (n.status === "former" ? "var(--text-subtle)"
      : legend.length > 1 && colourOf.has(keyOf(n)) ? colourOf.get(keyOf(n)) : "var(--ent-person)"),
  };
}

const LOOKS = {
  network(nodes) {
    const colours = colouring(nodes);
    return {
      colours,
      radius: (n) => 4 + Math.min(9, 1.7 * Math.sqrt(n.degree || 0)),
      color: colours.color,
      ring: (n) => (n.bridge ? "var(--accent)" : null),
      edge: (e) => e.kind === "collaboration"
        ? { color: "var(--text-muted)", width: 0.8 + 1.1 * Math.log2(1 + e.weight), alpha: 0.45 }
        : { color: "var(--ent-concept)", width: 1, dash: true, alpha: 0.55 },
      force: (e) => e.kind === "collaboration"
        ? { distance: 48 + 36 / (1 + e.weight), strength: Math.min(0.7, 0.2 + e.weight / 8) }
        : { distance: 110, strength: 0.06 },
      describe: (n) => [n.units?.length ? n.units.join(", ") : null, n.cluster ? `Cluster: ${n.cluster}` : null,
        n.bridge ? "Connects clusters" : null, n.status === "former" ? "Former member" : null],
    };
  },
  focus(nodes) {
    return {
      // Topics are the hubs, so they stay larger than the people around them.
      radius: (n) => (n.focus ? 14 : n.kind === "concept" ? 8 + Math.min(8, Math.sqrt(n.papers || 1)) : 4 + Math.min(6, 9 * Math.sqrt(n.overlap || 0))),
      color: (n) => (n.kind === "concept" ? "var(--ent-concept)" : n.focus ? "var(--accent)" : personFill(n)),
      edge: (e) => ({ color: "var(--text-subtle)", width: 0.6 + 2.5 * e.weight, alpha: 0.45 }),
      force: (e) => ({ distance: 46 + 50 * (1 - Math.min(1, e.weight * 2)), strength: 0.35 }),
      always: (n) => n.kind === "concept" || n.focus,
      describe: (n) => (n.kind === "concept" ? [`${number(n.papers)} of their papers`] : n.focus ? ["In focus"] : [n.units?.join(", ") || null]),
      count: nodes.length,
    };
  },
  landscape(nodes) {
    const top = new Set([...nodes].sort((a, b) => b.people - a.people).slice(0, 18).map((n) => n.id));
    // Topics are colored by MeSH category (diseases, anatomy, processes, …) when MeSH is loaded.
    const counts = new Map();
    for (const n of nodes) if (n.category) counts.set(n.category, (counts.get(n.category) ?? 0) + 1);
    const legend = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([label, people], i) => ({ label, people, colour: i < 6 ? `var(--series-${i})` : "var(--ent-unit)" }));
    const colourOf = new Map(legend.map((g) => [g.label, g.colour]));
    return {
      categories: legend,
      radius: (n) => Math.min(18, 3 + 2.6 * (n.size || 1)),
      color: (n) => colourOf.get(n.category) ?? "var(--ent-concept)",
      edge: (e) => ({ color: "var(--ent-concept)", width: 0.5 + 0.8 * Math.log2(1 + e.people), alpha: 0.3 }),
      force: (e) => ({ distance: 60, strength: 0.08 + Math.min(0.5, e.people / 20) }),
      always: (n) => top.has(n.id),
      describe: (n) => [`Among the top topics of ${number(n.people)} ${n.people === 1 ? "person" : "people"}`, n.category],
    };
  },
};

// --- controls, breadcrumbs, and the side panel ---------------------------------------------

function controls(state, body) {
  const p = state.params;
  const select = (label, name, options, value) => h("label", { class: "graph-control" }, h("span", {}, label),
    h("select", { class: "input", onchange: (event) => setQuery({ [name]: event.target.value || null }, { replace: false }) },
      options.map(([v, text]) => h("option", { value: v, selected: String(v) === String(value) }, text))));
  if (state.view === "network") {
    const units = body.meta.units ?? [];
    return h("div", { class: "graph-controls" },
      units.length ? select("Unit", "unit", [["", "Everyone"], ...units.map((u) => [u.value, `${u.value} (${number(u.count)})`])], p.get("unit") ?? "") : null,
      select("People", "max", [[50, "Up to 50"], [100, "Up to 100"], [150, "Up to 150"], [250, "Up to 250"]], p.get("max") ?? 150),
      select("Similar, not yet working together", "min", [[0.15, "Similarity 0.15 or more"], [0.25, "0.25 or more"], [0.35, "0.35 or more"], [0.5, "0.5 or more"]], p.get("min") ?? 0.25),
      h("label", { class: "graph-control graph-check" },
        h("input", { type: "checkbox", checked: p.get("similar") !== "0", onchange: (event) => setQuery({ similar: event.target.checked ? null : "0" }, { replace: false }) }),
        h("span", {}, "Show similar interests")),
      h("span", { class: "subtle" }, `${number(body.meta.shown)} of ${number(body.meta.people)} connected people shown`));
  }
  if (state.view === "landscape") {
    return h("div", { class: "graph-controls" },
      select("Concepts", "max", [[50, "Up to 50"], [100, "Up to 100"], [150, "Up to 150"], [250, "Up to 250"]], p.get("max") ?? 150),
      select("Held by", "people", [[2, "At least 2 people"], [3, "At least 3 people"], [5, "At least 5 people"]], p.get("people") ?? 2),
      h("span", { class: "subtle" }, `${number(body.meta.shown)} of ${number(body.meta.concepts)} concepts shown`));
  }
  return h("div", { class: "graph-controls" }, personPicker(state));
}

function personPicker(state) {
  const options = h("datalist", { id: "graph-people" });
  let found = [];
  const input = h("input", { class: "input", list: "graph-people", placeholder: "Focus on someone…", "aria-label": "Focus on someone", autocomplete: "off",
    oninput: debounce(async () => {
      const text = input.value.trim();
      if (text.length < 2) return;
      found = (await get("/api/v1/people", { q: text, limit: 8, fields: "id,name" })).data;
      options.replaceChildren(...found.map((p) => h("option", { value: p.name })));
    }, 200),
    onchange: () => {
      const pick = found.find((p) => p.name.toLowerCase() === input.value.trim().toLowerCase());
      if (pick) focusOn(state, pick.id);
    } });
  return h("div", { class: "search-field graph-picker" }, icon("person"), input, options);
}

function breadcrumbs(state, nodes) {
  const focus = nodes.find((n) => n.focus);
  if (!focus) return null;
  const crumbs = state.trail.map((id, i) => h("li", {}, h("a", {
    href: `/graph?view=focus&person=${encodeURIComponent(id)}${i ? `&trail=${state.trail.slice(0, i).join(",")}` : ""}` }, names.get(id) ?? "Someone")));
  return h("nav", { class: "crumbs", "aria-label": "Focus trail" },
    h("ol", {}, ...crumbs, h("li", { "aria-current": "page" }, focus.label)));
}

function legendRow(swatch, text) {
  return h("li", {}, h("span", { class: `legend-swatch ${swatch}`, "aria-hidden": "true" }), h("span", {}, text));
}

function overview(state, body, look) {
  if (state.view === "network") {
    const { legend, byUnit } = look.colours;
    const bridges = body.meta.bridges ?? [];
    return [
      h("h2", {}, "Reading this graph"),
      h("ul", { class: "legend" },
        legendRow("swatch-line", "Published, funded, or ran a study together (thicker: more, and with fewer others)"),
        legendRow("swatch-dashed", "Similar interests, not yet working together"),
        bridges.length ? legendRow("swatch-ring", "Connects clusters") : null,
        body.data.nodes.some((n) => n.status === "former") ? legendRow("swatch-former", "Former member") : null),
      legend.length ? h("h3", {}, byUnit ? "Groups" : "Clusters that work together") : null,
      legend.length ? h("ul", { class: "legend" }, legend.slice(0, 12).map((g) => {
        const swatch = h("span", { class: "legend-swatch swatch-dot", "aria-hidden": "true" });
        swatch.style.setProperty("background", g.colour);
        return h("li", {}, swatch, h("span", {}, `${g.label} (${number(g.people)})`));
      })) : null,
      bridges.length ? h("h3", {}, "People who connect clusters") : null,
      bridges.length ? h("ul", { class: "plain-list side-list" }, bridges.map((b) =>
        sideItem(b.id, b.name, `works with ${b.groups.slice(0, 3).map((g) => `${number(g.people)} in ${g.label}`).join(", ")}`, state))) : null,
      bridges.length ? h("p", { class: "subtle" }, "Listed by name: connecting clusters is a role in this network, not a ranking.") : null,
      h("p", { class: "subtle" }, "Hover to see someone's connections, select for details, double-click to focus on them. Drag a person to pin them in place."),
    ];
  }
  if (state.view === "landscape") {
    const categories = look.categories ?? [];
    return [
      h("h2", {}, "Reading this graph"),
      h("p", { class: "muted" }, "Each circle is a topic among people's top fifteen; larger circles are shared by more people. Topics are linked when the same people hold both, so groups of circles are research areas."),
      categories.length > 1 ? h("h3", {}, "MeSH categories") : null,
      categories.length > 1 ? h("ul", { class: "legend" }, categories.slice(0, 12).map((g) => {
        const swatch = h("span", { class: "legend-swatch swatch-dot", "aria-hidden": "true" });
        swatch.style.setProperty("background", g.colour);
        return h("li", {}, swatch, h("span", {}, `${g.label} (${number(g.people)})`));
      })) : null,
      h("p", { class: "subtle" }, "Double-click a topic to find who works on it."),
    ];
  }
  const focus = body.data.nodes.find((n) => n.focus);
  const collaborators = body.meta.collaborators ?? [];
  return [
    h("h2", {}, h("a", { href: `/people/${focus.id}` }, focus.label)),
    focus.units?.length ? h("p", { class: "subtle" }, focus.units.join(", ")) : null,
    h("p", { class: "muted" }, "Their top topics, and the people who share them. People gather around the topics they share."),
    collaborators.length ? h("h3", {}, "Worked with") : null,
    collaborators.length ? h("ul", { class: "plain-list side-list" }, collaborators.map((c) => sideItem(c.target, c.name, together(c), state))) : null,
    body.similar?.length ? h("h3", {}, "Similar interests") : null,
    body.similar?.length ? h("ul", { class: "plain-list side-list" }, body.similar.slice(0, 8).map((p) =>
      sideItem(p.id, p.name, p.shared.slice(0, 3).map((s) => s.descriptor).join(", "), state))) : null,
  ];
}

function sideItem(id, name, note, state) {
  return h("li", {},
    h("div", { class: "stack-xs grow" }, h("a", { href: `/people/${id}` }, name), note ? h("span", { class: "subtle" }, note) : null),
    state ? h("button", { type: "button", class: "btn btn-sm btn-ghost", "aria-label": `Focus on ${name}`, onclick: () => focusOn(state, id) }, "Focus") : null);
}

function together(e) {
  const parts = [];
  if (e.papers) parts.push(`${number(e.papers)} ${e.papers === 1 ? "paper" : "papers"}${e.first_year ? ` (${e.first_year === e.last_year ? e.first_year : `${e.first_year}–${e.last_year}`})` : ""}`);
  if (e.grants) parts.push(`${number(e.grants)} ${e.grants === 1 ? "grant" : "grants"}`);
  if (e.studies) parts.push(`${number(e.studies)} ${e.studies === 1 ? "study" : "studies"}`);
  return parts.join(", ");
}

function relation(e, byId) {
  if (e.kind === "collaboration") return together(e);
  if (e.kind === "similarity") return `Similar interests: ${e.shared.map((s) => s.descriptor).join(", ")}`;
  if (e.kind === "cooccurrence") return `Held together by ${number(e.people)} ${e.people === 1 ? "person" : "people"}`;
  return `${byId.get(e.target)?.label ?? "Topic"} (weight ${e.weight.toFixed(2)})`;
}

function nodeDetail(state, node, nodes, edges) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const mine = edges.filter((e) => e.source === node.id || e.target === node.id);
  const other = (e) => byId.get(e.source === node.id ? e.target : e.source);
  if (node.kind === "concept") {
    const holders = mine.map(other).filter((n) => n?.kind === "person");
    const ui = node.id.replace("msh:", "");
    return [
      h("h2", {}, node.label),
      h("p", { class: "muted" }, state.view === "landscape" ? `Among the top topics of ${number(node.people)} people.` : `${number(holders.length)} people here hold it.`),
      h("a", { class: "btn btn-sm", href: `/experts?concept=${ui}` }, icon("compass", "icon icon-sm"), "Find experts on this"),
      holders.length ? h("ul", { class: "plain-list side-list" }, holders.map((p) => h("li", {}, h("a", { href: `/people/${p.id}` }, p.label)))) : null,
      state.view === "landscape" && mine.length ? h("h3", {}, "Linked topics") : null,
      state.view === "landscape" && mine.length ? h("ul", { class: "plain-list side-list" }, mine.sort((a, b) => b.people - a.people).map((e) =>
        h("li", {}, h("span", {}, other(e)?.label), h("span", { class: "subtle" }, `${number(e.people)} people`)))) : null,
    ];
  }
  const collaborations = mine.filter((e) => e.kind === "collaboration").sort((a, b) => b.weight - a.weight);
  const similar = mine.filter((e) => e.kind === "similarity").sort((a, b) => b.weight - a.weight);
  const topics = mine.filter((e) => e.kind === "concept").map((e) => byId.get(e.target)).filter(Boolean);
  return [
    h("h2", {}, h("a", { href: `/people/${node.id}` }, node.label)),
    node.units?.length ? h("p", { class: "subtle" }, node.units.join(", ")) : null,
    h("button", { type: "button", class: "btn btn-sm", onclick: () => focusOn(state, node.id) }, icon("network", "icon icon-sm"), "Focus on them"),
    topics.length ? h("h3", {}, "Shares") : null,
    topics.length ? h("div", { class: "chips" }, topics.map((t) => h("a", { class: "chip", "data-entity": "concept", href: `/experts?concept=${t.id.replace("msh:", "")}` }, t.label))) : null,
    collaborations.length ? h("h3", {}, "Worked with") : null,
    collaborations.length ? h("ul", { class: "plain-list side-list" }, collaborations.map((e) => sideItem(other(e).id, other(e).label, together(e)))) : null,
    similar.length ? h("h3", {}, "Similar, not yet working together") : null,
    similar.length ? h("ul", { class: "plain-list side-list" }, similar.map((e) => sideItem(other(e).id, other(e).label, e.shared.map((s) => s.descriptor).join(", ")))) : null,
  ];
}

// --- the list view ---------------------------------------------------------------------------

function graphList(state, nodes, edges) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const degree = new Map();
  for (const e of edges) for (const id of [e.source, e.target]) degree.set(id, (degree.get(id) ?? 0) + 1);
  const nameCell = (n) => (n.kind === "concept"
    ? h("a", { href: `/experts?concept=${n.id.replace("msh:", "")}` }, n.label)
    : h("a", { href: `/people/${n.id}` }, n.label));
  const nodeColumns = [
    { key: "name", label: state.view === "landscape" ? "Topic" : "Name", sort: (n) => n.label?.toLowerCase(), cell: nameCell },
    state.view === "focus" ? { key: "kind", label: "Kind", sort: (n) => n.kind, cell: (n) => (n.focus ? "In focus" : n.kind === "concept" ? "Topic" : "Person") } : null,
    state.view === "network" ? { key: "units", label: "Units", sort: (n) => n.units?.join(", ") ?? "", cell: (n) => n.units?.join(", ") ?? "" } : null,
    state.view === "landscape" ? { key: "people", label: "People", num: true, sort: (n) => n.people, cell: (n) => number(n.people) } : null,
    { key: "links", label: "Links", num: true, sort: (n) => degree.get(n.id) ?? 0, cell: (n) => number(degree.get(n.id) ?? 0) },
    state.view !== "landscape" ? { key: "actions", label: "", cell: (n) => (n.kind === "person" && !n.focus
      ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => focusOn(state, n.id) }, "Focus") : null) } : null,
  ].filter(Boolean);
  const edgeRows = edges.map((e) => ({ ...e, from: byId.get(e.source), to: byId.get(e.target) })).filter((e) => e.from && e.to);
  const edgeColumns = [
    { key: "from", label: "From", sort: (e) => e.from.label?.toLowerCase(), cell: (e) => nameCell(e.from) },
    { key: "to", label: "To", sort: (e) => e.to.label?.toLowerCase(), cell: (e) => nameCell(e.to) },
    { key: "relation", label: "Relationship", sort: (e) => e.kind, cell: (e) => relation(e, byId) },
    { key: "strength", label: "Strength", num: true, sort: (e) => e.weight, cell: (e) => e.weight.toFixed(2) },
  ];
  const nodeTable = sortableTable(nodeColumns, nodes, { caption: state.view === "landscape" ? "Topics" : "People and topics", initial: "links" });
  const edgeTable = sortableTable(edgeColumns, edgeRows, { caption: "Relationships", initial: "strength" });
  return {
    element: h("div", { class: "stack" }, card({ title: `${state.view === "landscape" ? "Topics" : state.view === "focus" ? "People and topics" : "People"} (${number(nodes.length)})`, body: nodeTable.element }),
      card({ title: `Relationships (${number(edgeRows.length)})`, body: edgeTable.element })),
    filter(text) {
      const q = text.trim().toLowerCase();
      const keep = (n) => !q || (n.label ?? "").toLowerCase().includes(q);
      const shown = nodeTable.filter(keep);
      edgeTable.filter((e) => keep(e.from) || keep(e.to));
      return shown;
    },
  };
}
