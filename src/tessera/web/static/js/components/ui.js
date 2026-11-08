// Shared UI building blocks.

import { h, icon, svg } from "../core/dom.js";
import { ENTITY_ICONS } from "../core/icons.js";
import { hue, initials, number, relative, scoreLabel } from "../core/format.js";

export const ENTITY_LABELS = { person: "Person", publication: "Publication", grant: "Grant",
  study: "Study", concept: "Topic", unit: "Unit" };

export const ENTITY_PATHS = { person: "/people", publication: "/publications", grant: "/grants",
  study: "/studies" };

/** URL of an entity from its kind and natural id. */
export function entityHref(kind, id) {
  if (!id) return null;
  const clean = String(id).replace(/^(pub|grt|stu):/, "");
  return `${ENTITY_PATHS[kind]}/${encodeURIComponent(clean)}`;
}

export function refKind(ref) {
  if (!ref) return null;
  if (ref.startsWith("per_")) return "person";
  return { "pub:": "publication", "grt:": "grant", "stu:": "study" }[ref.slice(0, 4)] ?? null;
}

export function entityIcon(kind, size = "") {
  return h("span", { class: `entity-icon ${size}`, "data-entity": kind, "aria-hidden": "true" },
    icon(ENTITY_ICONS[kind] ?? "info"));
}

export function avatar(name, size = "") {
  return h("span", { class: `avatar ${size ? `avatar-${size}` : ""}`, "data-hue": hue(name),
    "aria-hidden": "true" }, initials(name));
}

export function chip(kind, label, href = null, attrs = {}) {
  const children = [kind && kind !== "plain" ? icon(ENTITY_ICONS[kind] ?? "info", "icon icon-sm") : null,
    h("span", {}, label)];
  const cls = kind === "plain" ? "chip chip-plain" : "chip";
  return href
    ? h("a", { class: cls, href, "data-entity": kind, title: label, ...attrs }, children)
    : h("span", { class: cls, "data-entity": kind, title: label, ...attrs }, children);
}

export function topicChips(topics, limit = 6) {
  if (!topics?.length) return null;
  return h("div", { class: "chips" }, topics.slice(0, limit).map((t) => chip("concept", t)));
}

export function pill(text, tone = "") {
  return h("span", { class: `pill ${tone ? `pill-${tone}` : ""}` }, text);
}

const MARK_TEXT = { accepted: "Confirmed", suggested: "Suggested", deferred: "Deferred",
  rejected: "Rejected" };

/** The status mark shown on every linked item. Clicking opens the evidence drawer. */
export function statusMark(assertion, onOpen) {
  const status = assertion.status;
  const label = assertion.auto ? "Auto-confirmed" : MARK_TEXT[status] ?? status;
  const unreported = assertion.reported === false;
  const score = status === "suggested" && assertion.score != null ? scoreLabel(assertion.score) : null;
  const title = [
    `${label}${score ? ` (${score} likely)` : ""}`,
    assertion.origin === "source" ? "stated by the source record" : null,
    assertion.origin === "human" ? "entered by a person" : null,
    assertion.auto ? "accepted automatically; undo from Admin → Evidence" : null,
    unreported ? "no longer reported by the source" : null,
    onOpen ? "Show the evidence" : null,
  ].filter(Boolean).join(" · ");
  const children = [h("span", { class: "mark-dot" }), label,
    score ? h("span", { class: "mark-score" }, score) : null];
  const cls = `mark mark-${status}${unreported ? " mark-unreported" : ""}`;
  return onOpen
    ? h("button", { type: "button", class: cls, title, "aria-label": title, onclick: onOpen }, children)
    : h("span", { class: cls, title }, children);
}

export function pageHeader({ eyebrow, title, meta, actions, lead }) {
  return h("header", { class: "page-header" },
    lead ?? null,
    h("div", { class: "grow" },
      eyebrow ? h("div", { class: "eyebrow" }, eyebrow) : null,
      h("h1", { class: "page-title", tabindex: "-1" }, title),
      meta ? h("div", { class: "page-meta" }, meta) : null),
    actions ? h("div", { class: "page-actions" }, actions) : null);
}

export function card({ title, action, body, pad = false, attrs = {} }) {
  return h("section", { class: "card", ...attrs },
    title ? h("div", { class: "card-header" }, h("h2", {}, title),
      action ? h("div", { class: "spacer" }, action) : null) : null,
    pad ? h("div", { class: "card-body" }, body) : body);
}

export function emptyState({ kind = null, iconName = "info", title, text, action = null }) {
  return h("div", { class: "empty" },
    kind ? entityIcon(kind) : h("span", { class: "entity-icon", "data-entity": "unit" }, icon(iconName)),
    h("h2", {}, title), text ? h("p", {}, text) : null, action);
}

export function skeletonPage() {
  return h("div", { class: "stack-lg", "aria-busy": "true", "aria-label": "Loading" },
    h("div", { class: "skeleton skeleton-title" }),
    h("div", { class: "card card-pad" },
      [90, 70, 82, 60, 76].map((w) => h("div", { class: "skeleton skeleton-line", "data-w": w }))));
}

export function errorState(error, retry) {
  const notFound = error?.status === 404;
  return emptyState({
    iconName: notFound ? "search" : "warning",
    title: notFound ? "Not found" : "Something went wrong",
    text: error?.detail || error?.message || "Please try again.",
    action: retry && !notFound ? h("button", { class: "btn", type: "button", onclick: retry }, "Try again") : null,
  });
}

export function statTile({ kind, label, value, note, series }) {
  return h("div", { class: "card stat", "data-entity": kind },
    h("div", { class: "stat-label" }, icon(ENTITY_ICONS[kind] ?? "info", "icon icon-sm"), label),
    h("div", { class: "stat-value" }, value == null ? "–" : number(value)),
    note ? h("div", { class: "stat-note" }, note) : null,
    series && series.length > 1 ? sparkline(series) : null);
}

export function sparkline(values, { width = 240, height = 32 } = {}) {
  const max = Math.max(...values, 1);
  const step = width / Math.max(values.length - 1, 1);
  const points = values.map((v, i) => [i * step, height - 2 - (v / max) * (height - 4)]);
  const line = points.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${width},${height} L0,${height} Z`;
  return svg("svg", { class: "sparkline", viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none",
    role: "img", "aria-label": `Trend: ${values.join(", ")}` },
  svg("path", { class: "area", d: area }), svg("path", { class: "line", d: line }));
}

/**
 * Vertical bar chart. data: [{label, value, secondary?}] where secondary is an inner
 * (darker) portion, e.g. confirmed vs all. Includes an accessible table.
 */
export function barChart(data, { height = 140, format = number, caption = "" } = {}) {
  if (!data.length) return null;
  const width = Math.max(data.length * 34, 240);
  const max = Math.max(...data.map((d) => d.value), 1);
  const barWidth = Math.min(26, (width / data.length) * 0.66);
  const plotHeight = height - 22;
  const bars = data.map((d, i) => {
    const x = (i + 0.5) * (width / data.length) - barWidth / 2;
    const full = (d.value / max) * (plotHeight - 14);
    const inner = d.secondary != null ? (d.secondary / max) * (plotHeight - 14) : null;
    return svg("g", {},
      svg("rect", { class: inner != null ? "bar-soft" : "bar", x, y: plotHeight - full, width: barWidth, height: Math.max(full, 1), rx: 3 },
        svg("title", {}, `${d.label}: ${format(d.value)}${d.secondary != null ? ` (${format(d.secondary)} confirmed)` : ""}`)),
      inner ? svg("rect", { class: "bar", x, y: plotHeight - inner, width: barWidth, height: inner, rx: 3 }) : null,
      svg("text", { x: x + barWidth / 2, y: height - 4, "text-anchor": "middle" }, d.label),
      d.value ? svg("text", { x: x + barWidth / 2, y: plotHeight - full - 4, "text-anchor": "middle" }, format(d.value)) : null);
  });
  // The data table is for screen readers. Tables size to their content, so it sits inside
  // a visually hidden block rather than carrying the class itself.
  const drawing = svg("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": caption || "Bar chart" }, bars);
  // A chart with few bars would otherwise stretch to the card and blow its labels up.
  drawing.style.setProperty("max-width", `${Math.round(width * 1.25)}px`);
  return h("figure", { class: "chart-figure" }, drawing,
    h("div", { class: "sr-only" }, h("table", {}, h("caption", {}, caption),
      h("tbody", {}, data.map((d) => h("tr", {}, h("th", {}, d.label), h("td", {}, format(d.value))))))));
}

export function freshness(source) {
  const run = source.last_run;
  let state = "fresh";
  if (!source.enabled) state = "never";
  else if (run?.status === "failed") state = "failed";
  else if (!source.last_success_at) state = "never";
  else if (source.stale) state = "stale";
  const text = !source.enabled ? `${source.label} · disabled`
    : source.last_success_at ? `${source.label} · ${relative(source.last_success_at)}`
      : `${source.label} · not fetched yet`;
  return h("span", { class: "fresh", "data-state": state, title: run?.error ?? "" }, text);
}

export function callout(text, tone = "info") {
  return h("div", { class: `callout ${tone === "warn" ? "callout-warn" : ""}`, role: "note" },
    icon(tone === "warn" ? "warning" : "info", "icon icon-sm"), h("div", {}, text));
}

export function kv(pairs) {
  return h("dl", { class: "kv" }, pairs.filter(([, v]) => v != null && v !== "" && v !== false)
    .map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

export function tabs(items, selected, onSelect) {
  return h("div", { class: "tabs", role: "tablist" }, items.map((item) =>
    h("button", {
      type: "button", class: "tab", role: "tab", "aria-selected": String(item.id === selected),
      onclick: () => onSelect(item.id),
    }, item.label, item.count != null ? h("span", { class: "badge" }, number(item.count)) : null)));
}

export function segmented(items, selected, onSelect, label) {
  return h("div", { class: "segmented", role: "group", "aria-label": label }, items.map((item) =>
    h("button", { type: "button", "aria-pressed": String(item.id === selected), onclick: () => onSelect(item.id),
      title: item.title ?? null }, item.icon ? icon(item.icon, "icon icon-sm") : null, item.label ?? null)));
}

export function pager({ total, offset, limit, onPage }) {
  if (total <= limit) return null;
  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  return h("nav", { class: "pager", "aria-label": "Pages" },
    h("span", { class: "tabular" }, `${number(from)}–${number(to)} of ${number(total)}`),
    h("div", { class: "row-sm" },
      h("button", { class: "btn btn-sm", type: "button", disabled: offset === 0,
        onclick: () => onPage(Math.max(0, offset - limit)) }, icon("chevronLeft", "icon icon-sm"), "Previous"),
      h("button", { class: "btn btn-sm", type: "button", disabled: to >= total,
        onclick: () => onPage(offset + limit) }, "Next", icon("chevronRight", "icon icon-sm"))));
}

export const FACET_LABELS = {
  status: "Status", unit: "Unit", year: "Year", journal: "Journal", type: "Type", flag: "Flags",
  activity: "Activity code", institute: "Institute", phase: "Phase", person: "Person",
};

/** Facet checkboxes. selected: {name: [values]}; onChange(name, values). */
export function facetPanel(facets, selected, onChange, { labels = FACET_LABELS, format = (_, v) => v } = {}) {
  const groups = Object.entries(facets ?? {}).filter(([, values]) => values.length);
  if (!groups.length) return h("div");
  return h("aside", { class: "facets", "aria-label": "Filters" }, groups.map(([name, values]) => {
    const chosen = new Set(selected[name] ?? []);
    const options = values.slice(0, 50);
    const list = h("div", { class: "facet-options" });
    let expanded = false;
    const draw = () => {
      list.replaceChildren(...options.slice(0, expanded ? options.length : 8).map((option) =>
        h("label", { class: "facet-option" },
          h("input", { type: "checkbox", checked: chosen.has(option.value), onchange: (event) => {
            if (event.target.checked) chosen.add(option.value); else chosen.delete(option.value);
            onChange(name, [...chosen]);
          } }),
          h("span", { class: "grow", title: format(name, option.value) }, format(name, option.value)),
          h("span", { class: "count" }, number(option.count)))));
      if (options.length > 8) {
        list.append(h("button", { type: "button", class: "facet-more", onclick: () => { expanded = !expanded; draw(); } },
          expanded ? "Show fewer" : `Show ${options.length - 8} more`));
      }
    };
    draw();
    return h("div", { class: "facet-group" }, h("div", { class: "facet-title" }, labels[name] ?? name), list);
  }));
}

export function debounce(fn, wait = 200) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
