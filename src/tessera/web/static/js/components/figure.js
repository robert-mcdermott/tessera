// Insight figures: a card that loads a figure (a metric over a cohort and a period) and shows
// it as a chart or a table, with what it counted one click away (INT-01). Small numbers are
// hatched in charts and marked in tables; years before a source's records are complete are
// dimmed.

import { get } from "../core/api.js";
import { h, icon, replace, svg } from "../core/dom.js";
import { date, money, number, percent, plural, relative } from "../core/format.js";
import { openDrawer } from "./drawer.js";
import { callout, emptyState, entityHref, segmented, sparkline } from "./ui.js";

let hatchSeq = 0;

// Bar shades for stacked series, from strongest to palest.
const TONES = { solid: "bar", deep: "bar-deep", soft: "bar-soft", pale: "bar-pale" };

/** Format one cell by its column's kind. */
export function cell(column, value) {
  if (value == null || value === "") return "–";
  switch (column.kind) {
    case "money": return money(value);
    case "share": return percent(value, 1);
    case "count": return number(value);
    case "date": return date(value);
    case "list": return Array.isArray(value) ? value.join("; ") : String(value);
    default: return String(value);
  }
}

function smallSet(figure) {
  const out = new Set();
  for (const s of figure.small ?? []) for (const key of s.columns) out.add(`${s.row}:${key}`);
  return out;
}

/** Years before a source's records are complete, for the figure's period basis. */
function incompleteBefore(figure) {
  const firsts = (figure.sources ?? []).map((s) => s.complete_from).filter((y) => y != null);
  return firsts.length ? Math.max(...firsts) : null;
}

/**
 * Stacked bars by year. series: [{key, label, tone}] drawn bottom-up; tone "solid" or "soft".
 */
export function yearChart(figure, series, { format = number, label = "" } = {}) {
  const rows = figure.rows;
  if (!rows.length) return null;
  const small = smallSet(figure);
  const before = incompleteBefore(figure);
  const width = Math.max(rows.length * 52, 320);
  const height = 190;
  const plot = height - 26;
  const totals = rows.map((r) => series.reduce((sum, s) => sum + (r[s.key] ?? 0), 0));
  const max = Math.max(...totals, 1);
  const slot = width / rows.length;
  const barWidth = Math.min(30, slot * 0.62);
  const hatch = `hatch-${++hatchSeq}`;
  const bars = rows.map((row, i) => {
    const x = (i + 0.5) * slot - barWidth / 2;
    let y = plot;
    const parts = series.map((s) => {
      const value = row[s.key] ?? 0;
      if (!value) return null;
      const tall = Math.max((value / max) * (plot - 18), 1.5);
      y -= tall;
      const isSmall = small.has(`${i}:${s.key}`);
      return svg("g", {},
        svg("rect", { class: TONES[s.tone] ?? "bar", x, y, width: barWidth, height: tall, rx: 2 }),
        isSmall ? svg("rect", { class: "bar-small", x, y, width: barWidth, height: tall, rx: 2, fill: `url(#${hatch})` }) : null);
    });
    const year = row.year ?? row.label;
    const dim = before != null && typeof year === "number" && year < before;
    const tip = `${year}: ${series.map((s) => `${s.label} ${format(row[s.key] ?? 0)}`).join(", ")}${dim ? " (incomplete year)" : ""}`;
    return svg("g", { class: dim ? "is-incomplete" : null },
      svg("title", {}, tip),
      parts,
      svg("text", { x: x + barWidth / 2, y: height - 8, "text-anchor": "middle" }, String(year)),
      totals[i] ? svg("text", { class: "value", x: x + barWidth / 2, y: y - 5, "text-anchor": "middle" }, format(totals[i])) : null);
  });
  const drawing = svg("svg", { class: "chart figure-chart", viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": label || figure.metric.name },
  svg("defs", {}, svg("pattern", { id: hatch, width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
    svg("line", { class: "hatch-line", x1: 0, y1: 0, x2: 0, y2: 6 }))),
  svg("line", { class: "axis", x1: 0, x2: width, y1: plot + 0.5, y2: plot + 0.5 }),
  bars);
  drawing.style.setProperty("max-width", `${Math.round(width * 1.4)}px`);
  return h("figure", { class: "chart-figure" }, drawing,
    h("div", { class: "figure-legend" }, series.map((s) => h("span", {},
      h("span", { class: `figure-swatch swatch-${s.tone ?? "solid"}` }), s.label)),
    small.size ? h("span", {}, h("span", { class: "figure-swatch swatch-small" }), `Fewer than ${figure.small_number}`) : null));
}

/** Horizontal bars for categories: one row per item, with the value and an optional share. */
export function categoryBars(figure, { labelKey = "label", valueKey, shareKey = null, format = number, hrefOf = null, limit = 12, extra = null } = {}) {
  const rows = figure.rows.slice(0, limit);
  if (!rows.length) return null;
  const small = smallSet(figure);
  const max = Math.max(...rows.map((r) => r[valueKey] ?? 0), 1);
  return h("ol", { class: "category-bars" }, rows.map((row, i) => {
    const value = row[valueKey] ?? 0;
    const isSmall = small.has(`${i}:${valueKey}`);
    const text = hrefOf?.(row) ? h("a", { href: hrefOf(row) }, row[labelKey] ?? "–") : (row[labelKey] ?? "–");
    const detail = [row.detail, extra?.(row)].filter(Boolean);
    const name = detail.length ? [text, h("span", { class: "category-detail" }, detail)] : text;
    const meter = h("span", { class: "category-meter", "data-small": isSmall ? "" : null });
    meter.style.setProperty("--w", `${Math.max((value / max) * 100, 0.8).toFixed(1)}%`);
    return h("li", {},
      h("span", { class: "category-name" }, name),
      h("span", { class: "category-track", "aria-hidden": "true" }, meter),
      h("span", { class: "category-value tabular" }, format(value), isSmall ? h("span", { class: "small-mark", title: `Fewer than ${figure.small_number}` }, "†") : null),
      h("span", { class: "category-share tabular muted" }, shareKey && row[shareKey] != null ? percent(row[shareKey], 0) : ""));
  }));
}

const TREND_ICONS = { Rising: "↑", Falling: "↓", Steady: "→" };

/** A topic's trend as a small labeled mark; "too few" stays plain text. */
export function trendMark(label) {
  const arrow = TREND_ICONS[label];
  return h("span", { class: "trend", "data-trend": arrow ? label.toLowerCase() : "unknown" }, arrow ? `${arrow} ${label}` : label);
}

/** Rows of a by-year table (keys y2022, y2023, …) as a list with a sparkline for each. */
export function trendList(figure, { labelKey = "label", valueKey = "papers" } = {}) {
  const years = figure.columns.filter((c) => /^y\d{4}$/.test(c.key));
  const small = smallSet(figure);
  return h("ol", { class: "trend-list" }, figure.rows.map((row, i) => {
    const values = years.map((c) => row[c.key] ?? 0);
    const smallYears = years.filter((c) => small.has(`${i}:${c.key}`)).length;
    return h("li", {},
      h("span", { class: "trend-name" }, row[labelKey]),
      h("span", { class: "trend-spark", title: years.map((c, j) => `${c.label}: ${values[j]}`).join(", ") }, sparkline(values, { width: 120, height: 26 })),
      h("span", { class: "category-value tabular" }, number(row[valueKey])),
      h("span", { class: "trend-cell" }, trendMark(row.trend),
        smallYears ? h("span", { class: "small-mark", title: `${smallYears} years with fewer than ${figure.small_number}` }, "†") : null));
  }));
}

/** The figure's rows as an accessible table, with small numbers marked. */
export function figureTable(figure, { limit = 500 } = {}) {
  const small = smallSet(figure);
  return h("div", { class: "table-wrap" }, h("table", { class: "table", "data-dense": "" },
    h("caption", { class: "sr-only" }, figure.metric.name),
    h("thead", {}, h("tr", {}, figure.columns.map((c) => h("th", { scope: "col", class: ["count", "money", "share"].includes(c.kind) ? "num" : null }, c.label)))),
    h("tbody", {}, figure.rows.slice(0, limit).map((row, i) => h("tr", {}, figure.columns.map((c) => {
      const isSmall = small.has(`${i}:${c.key}`);
      return h("td", { class: ["count", "money", "share"].includes(c.kind) ? "num" : null, "data-small": isSmall ? "" : null },
        cell(c, row[c.key]), isSmall ? h("span", { class: "small-mark", title: `Fewer than ${figure.small_number}` }, "†") : null,
        isSmall ? h("span", { class: "sr-only" }, " (a small number)") : null);
    }))))));
}

function sourceLine(figure) {
  return (figure.sources ?? []).map((s) => s.last_success_at
    ? `${s.label} as of ${date(s.last_success_at)}`
    : `${s.label}: not fetched yet`).join(" · ");
}

/** One line under every figure: whose, when, from what. */
export function figureMeta(figure) {
  return h("p", { class: "figure-meta" },
    h("span", {}, figure.cohort.label, figure.cohort.size != null ? ` (${plural(figure.cohort.size, "person", "people")})` : ""),
    h("span", {}, figure.period.label),
    figure.sources?.length ? h("span", {}, sourceLine(figure)) : null,
    h("code", { class: "metric-ref" }, figure.metric.ref));
}

const RECORD_HREF = { pmid: "publication", core_project_num: "grant", nct_id: "study" };

// The drawer is narrow: identifiers, links, and long lists stay in exports.
const DRAWER_SKIP = new Set(["doi", "links", "journal", "assertions"]);

function recordsTable(figure) {
  const columns = (figure.record_columns ?? []).filter((c) => !DRAWER_SKIP.has(c.key));
  if (!figure.records?.length) return h("p", { class: "muted" }, "Nothing was counted.");
  return h("div", { class: "table-wrap" }, h("table", { class: "table", "data-dense": "" },
    h("caption", { class: "sr-only" }, "Records counted"),
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { scope: "col" }, c.label)))),
    h("tbody", {}, figure.records.slice(0, 300).map((r) => h("tr", {}, columns.map((c) => {
      const kind = RECORD_HREF[c.key];
      const value = cell(c, r[c.key]);
      return h("td", {}, kind && r[c.key] ? h("a", { href: entityHref(kind, r[c.key]) }, value) : value);
    }))))),
  figure.records.length > 300 ? h("p", { class: "muted" }, `The first 300 of ${number(figure.records.length)}; export the figure for all of them.`) : null);
}

const FORMATS = [["csv", "CSV"], ["parquet", "Parquet"], ["json", "JSON"]];

/** Download links for a figure: CSV and Parquet come as zips with the records and definitions. */
export function exportLinks(hrefFor) {
  return h("ul", { class: "export-list" }, FORMATS.map(([fmt, label]) => h("li", {},
    h("a", { href: hrefFor(fmt), download: "" }, icon("download", "icon icon-sm"), label))));
}

/** The address that exports a live figure. */
export function liveExport(metric, params) {
  return (fmt) => {
    const search = new URLSearchParams({ ...params, format: fmt });
    return `/api/v1/insight/figures/${metric}/export?${search}`;
  };
}

/** The About panel: definition, counting rule, cohort, period, options, sources, notes. */
export function openAbout(figure, params, { exportHref = null, recordsLoader = null } = {}) {
  const m = figure.metric;
  openDrawer({
    title: m.name,
    subtitle: h("code", {}, m.ref),
    render: (body) => {
      const records = h("div", { class: "stack-sm" },
        h("button", { type: "button", class: "btn btn-sm", onclick: async () => {
          replace(records, h("p", { class: "muted" }, "Loading the records…"));
          try {
            const full = recordsLoader ? await recordsLoader()
              : (await get(`/api/v1/insight/figures/${m.id}`, { ...params, records: "true" })).data;
            replace(records, recordsTable(full));
          } catch (error) {
            replace(records, callout(error.detail || error.message, "warn"));
          }
        } }, icon("list", "icon icon-sm"), `Show the ${plural(figure.record_count, "record")} counted`));
      body.append(
        h("section", { class: "about-section" }, h("h3", {}, "What it shows"), h("p", {}, m.definition)),
        h("section", { class: "about-section" }, h("h3", {}, "What is counted"), h("p", {}, m.counting)),
        h("section", { class: "about-section" }, h("h3", {}, "For whom, and when"),
          h("dl", { class: "kv" },
            h("dt", {}, "Cohort"), h("dd", {}, figure.cohort.label, ` · ${plural(figure.cohort.size, "person", "people")}`,
              figure.cohort.note ? h("div", { class: "muted" }, figure.cohort.note) : null),
            h("dt", {}, "Period"), h("dd", {}, figure.period.label, figure.period.basis === "fiscal" ? " (the funder's fiscal years)" : ""),
            Object.values(figure.options ?? {}).length ? [h("dt", {}, "Options"), h("dd", {}, Object.entries(figure.options).map(([key, o]) =>
              h("div", {}, `${m.options.find((x) => x.key === key)?.label ?? key}: ${o.label}`)))] : null,
            h("dt", {}, "Small numbers"), h("dd", {}, `Counts from 1 to ${figure.small_number - 1} are marked †.`))),
        h("section", { class: "about-section" }, h("h3", {}, "Sources"),
          h("ul", { class: "plain-list" }, (figure.sources ?? []).map((s) => h("li", {},
            h("strong", {}, s.label), " · ",
            s.last_success_at ? `last fetched ${relative(s.last_success_at)} (${date(s.last_success_at)})` : "not fetched yet",
            s.complete_from ? ` · complete from ${s.period === "fiscal" ? "FY" : ""}${s.complete_from}` : ""))),
          h("p", { class: "muted" }, `Computed ${relative(figure.generated_at)}.`)),
        figure.notes?.length ? h("section", { class: "about-section" }, h("h3", {}, "Notes"),
          h("ul", {}, figure.notes.map((n) => h("li", {}, n)))) : null,
        m.caveats?.length ? h("section", { class: "about-section" }, h("h3", {}, "Keep in mind"),
          h("ul", {}, m.caveats.map((n) => h("li", {}, n)))) : null,
        h("section", { class: "about-section" }, h("h3", {}, "Behind the number"), records),
        exportHref ? h("section", { class: "about-section" }, h("h3", {}, "Export"),
          h("p", { class: "muted" }, "The figure, every record counted with its links, and this account of what it is. CSV and Parquet come as a zip."),
          exportLinks(exportHref)) : null);
    },
  });
}

/**
 * A card for one figure. draw(figure) returns the chart; without it the table is shown.
 * params: cohort, start, end, and the metric's options.
 */
export function figureCard({ metric, params, title = null, draw = null, empty = "Nothing to count for this cohort and period.", wide = false,
  data = null, exportHref = null, recordsLoader = null, onLoad = null }) {
  exportHref = exportHref ?? liveExport(metric, params);
  const heading = h("h2", {}, title ?? "…");
  const actions = h("div", { class: "figure-actions" });
  const body = h("div", { class: "figure-body" }, h("div", { class: "skeleton skeleton-chart" }));
  const footer = h("div", { class: "figure-footer" });
  const card = h("section", { class: `card figure-card${wide ? " figure-wide" : ""}`, "data-metric": metric, "aria-busy": "true" },
    h("div", { class: "card-header" }, heading, h("div", { class: "spacer" }, actions)), body, footer);
  let mode = "chart";
  const render = (figure) => {
    const total = Object.values(figure.totals ?? {}).find((v) => typeof v === "number");
    const nothing = !figure.rows.length || total === 0;
    const chart = draw && !nothing ? draw(figure) : null;
    const view = nothing ? emptyState({ iconName: "info", title: "Nothing counted", text: empty })
      : mode === "table" || !chart ? figureTable(figure) : chart;
    replace(body, view);
    replace(actions, 
      draw && !nothing ? segmented([{ id: "chart", label: "Chart" }, { id: "table", label: "Table" }], mode, (id) => {
        mode = id;
        render(figure);
      }, "Show as") : null,
      h("details", { class: "export-menu" }, h("summary", { class: "btn btn-sm btn-ghost", "aria-label": `Export ${figure.metric.name}` },
        icon("download", "icon icon-sm"), "Export"), exportLinks(exportHref)),
      h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => openAbout(figure, params, { exportHref, recordsLoader }),
        "aria-label": `About this figure: ${figure.metric.name}` }, icon("info", "icon icon-sm"), "About"));
    replace(footer, 
      figure.notes?.length ? h("ul", { class: "figure-notes" }, figure.notes.map((n) => h("li", {}, n))) : null,
      figureMeta(figure));
  };
  const loaded = data ? Promise.resolve({ data }) : get(`/api/v1/insight/figures/${metric}`, params);
  loaded.then(({ data: figure }) => {
    card.removeAttribute("aria-busy");
    heading.textContent = title ?? figure.metric.name;
    render(figure);
    onLoad?.(figure);
  }).catch((error) => {
    card.removeAttribute("aria-busy");
    replace(body, callout(error.detail || error.message || "This figure could not be computed.", "warn"));
  });
  return card;
}
