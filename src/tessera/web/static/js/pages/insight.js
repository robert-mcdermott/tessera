// Insight: portfolio figures over a cohort and a period, each with what it counts (INT-01);
// reports, their snapshots, and exports (INT-03, INT-04). Loaded on first use. Every page shares
// the cohort bar and the figure card.

import { get, post, request } from "../core/api.js";
import { h, icon, replace } from "../core/dom.js";
import { date, enumLabel, money, number, plural, relative } from "../core/format.js";
import { navigate, query, setQuery } from "../core/router.js";
import { can } from "../core/session.js";
import { cohortBar, insightParams } from "../components/cohort-bar.js";
import { closeDrawer, openDrawer } from "../components/drawer.js";
import { categoryBars, exportLinks, figureCard, trendList, trendMark, yearChart } from "../components/figure.js";
import { toast } from "../components/toast.js";
import { callout, card, emptyState, pageHeader, segmented } from "../components/ui.js";
import { show } from "./page.js";

export const INSIGHT_PAGES = [
  { href: "/insight", label: "Overview" },
  { href: "/insight/funding", label: "Funding" },
  { href: "/insight/topics", label: "Topics" },
  { href: "/insight/studies", label: "Studies" },
  { href: "/insight/reports", label: "Reports" },
  { href: "/insight/definitions", label: "Definitions" },
];
const KEEPS_COHORT = new Set(["/insight", "/insight/funding", "/insight/topics", "/insight/studies"]);

const LEVEL = { key: "level", label: "Topics as", default: "heading",
  choices: [{ value: "heading", label: "MeSH headings" }, { value: "area", label: "Research areas" }] };
const AMOUNT = { key: "amount", label: "Amount", default: "total",
  choices: [{ value: "total", label: "Total" }, { value: "direct", label: "Direct" }, { value: "indirect", label: "Indirect" }] };
const AWARDEE = { key: "awardee", label: "Awarded to", default: "organization",
  choices: [{ value: "organization", label: "The organization" }, { value: "anywhere", label: "Anywhere" }] };
const MONTHS = [{ id: "6", label: "6 months" }, { id: "12", label: "12 months" }, { id: "24", label: "24 months" }];
const CONDITION_LEVEL = [{ id: "area", label: "Disease areas" }, { id: "condition", label: "As registered" }];

const dollars = (v) => money(v, { compactForm: true });

// --- how each metric is drawn -------------------------------------------------------------------

function publicationsChart(f) {
  return yearChart(f, f.options.basis?.value === "confirmed"
    ? [{ key: "confirmed", label: "Confirmed", tone: "solid" }]
    : [{ key: "confirmed", label: "Confirmed", tone: "solid" }, { key: "likely", label: "Likely", tone: "soft" }],
  { label: "Publications by year" });
}

function awardsChart(f) {
  return yearChart({ ...f, rows: f.rows.map((r) => ({ ...r, year: r.fiscal_year })) }, [
    { key: "new", label: "New awards", tone: "solid" },
    { key: "renewal", label: "Renewals", tone: "deep" },
    { key: "continuing", label: "Continuing years", tone: "soft" },
    { key: "supplement", label: "Supplements", tone: "pale" },
  ].filter((s) => f.rows.some((r) => r[s.key])), { format: dollars, label: "Awards by fiscal year" });
}

// Settled ratios are charted; recent papers (provisional) and papers iCite lacks are said.
function influenceChart(f) {
  const settled = f.rows.filter((r) => r.share != null); // the first rows, so small marks line up
  const aside = f.rows.filter((r) => r.share == null && r.papers);
  return h("div", { class: "stack-sm" },
    categoryBars({ ...f, rows: settled }, { valueKey: "papers", shareKey: "share" }),
    aside.length ? h("p", { class: "muted figure-aside" }, "Not charted: ",
      aside.map((r) => `${number(r.papers)} ${r.label.charAt(0).toLowerCase()}${r.label.slice(1)}`).join("; "), ".") : null);
}

const money_ = (f) => categoryBars(f, { valueKey: "amount", shareKey: "share", format: dollars });
const studies_ = (f) => categoryBars(f, { valueKey: "studies", shareKey: "share" });

/** How a metric is drawn; null shows its table. */
export const DRAW = {
  "publications.count": publicationsChart,
  "publications.influence": influenceChart,
  "funding.awarded": awardsChart,
  "funding.by_institute": money_,
  "funding.by_activity": money_,
  "funding.by_mechanism": money_,
  "funding.by_group": (f) => categoryBars(f, { labelKey: "group", valueKey: "amount", format: dollars, hrefOf: (r) => `/groups/${r.slug}`, limit: 40 }),
  "funding.ending": null,
  "topics.landscape": (f) => categoryBars(f, { valueKey: "papers", shareKey: "share", limit: 30, extra: (r) => trendMark(r.trend) }),
  "topics.by_year": (f) => trendList(f),
  "studies.by_status": studies_,
  "studies.by_phase": studies_,
  "studies.by_sponsor": studies_,
  "studies.by_intervention": studies_,
  "studies.by_condition": studies_,
  "studies.by_start_year": (f) => yearChart(f, [{ key: "interventional", label: "Interventional", tone: "solid" },
    { key: "other", label: "Observational and other", tone: "soft" }], { label: "Studies by start year" }),
};
const WIDE = new Set(["funding.awarded", "funding.ending", "topics.landscape", "topics.by_year"]);
const EMPTY = {
  "funding.ending": "No grant's project ends in the window.",
  "studies.by_status": "No study of this cohort was under way in the period.",
};

/** A figure card for a metric, drawn the usual way. */
function figure(metric, params, extra = {}) {
  return figureCard({ metric, params, draw: DRAW[metric] ?? null, wide: WIDE.has(metric),
    empty: EMPTY[metric] ?? (metric.startsWith("studies.") ? EMPTY["studies.by_status"] : undefined), ...extra });
}

// --- page frame ---------------------------------------------------------------------------------

/** The row of Insight's pages, under the header. Cohort pages keep the cohort and years. */
export function insightNav(current) {
  const keep = window.location.search;
  return h("nav", { class: "tabs subnav", "aria-label": "Insight" }, INSIGHT_PAGES.map((p) =>
    h("a", { class: "tab", href: KEEPS_COHORT.has(p.href) && KEEPS_COHORT.has(current) ? p.href + keep : p.href,
      "aria-current": p.href === current || (p.href === "/insight/reports" && current.startsWith("/insight/reports")) ? "page" : null }, p.label)));
}

const mayShare = () => can("manage") || can("review") || can("admin");

/** "Save as report": the figures on this page, with the cohort, years, and options chosen. */
function saveReportButton(figures) {
  if (!mayShare()) return null;
  return h("button", { type: "button", class: "btn", onclick: () => openDrawer({
    title: "Save as a report",
    subtitle: "These figures, for this cohort and these years, under a name everyone can open, snapshot, and export.",
    render: (body) => {
      const params = insightParams();
      const name = h("input", { class: "input", required: true, maxlength: 200, id: "report-name" });
      const description = h("textarea", { class: "input textarea", rows: 3, maxlength: 4000, id: "report-description" });
      body.append(h("form", { class: "stack", onsubmit: async (event) => {
        event.preventDefault();
        try {
          const saved = (await post("/api/v1/insight/reports", {
            name: name.value, description: description.value || null, cohort: params.cohort || "organization",
            start: params.start ? Number(params.start) : null, end: params.end ? Number(params.end) : null,
            figures: figures(params),
          })).data;
          closeDrawer();
          toast(`Saved “${saved.name}”.`);
          navigate(`/insight/reports/${saved.id}`);
        } catch (error) {
          toast(error.detail || error.message, "error");
        }
      } },
      h("label", { class: "field", for: "report-name" }, h("span", {}, "Name"), name),
      h("label", { class: "field", for: "report-description" }, h("span", {}, "What it is for (optional)"), description),
      h("p", { class: "muted" }, `${plural(figures(params).length, "figure")}. Opening the report computes them from the data as it is then; a snapshot freezes them.`),
      h("div", {}, h("button", { class: "btn btn-primary", type: "submit" }, icon("plus", "icon icon-sm"), "Save report"))));
      queueMicrotask(() => name.focus());
    },
  }) }, icon("bookmark", "icon icon-sm"), "Save as report");
}

const pick = (params, ...keys) => Object.fromEntries(keys.filter((k) => params[k]).map((k) => [k, params[k]]));

function header(title, current, figures, lede = null) {
  return [pageHeader({ eyebrow: "Insight", title, actions: figures ? saveReportButton(figures) : null }),
    insightNav(current), lede];
}

// --- the pages ----------------------------------------------------------------------------------

const OVERVIEW = ["publications.count", "funding.awarded", "topics.landscape", "publications.influence", "studies.by_status", "studies.by_phase"];

export function insightOverview(main) {
  return show(main, {
    title: "Insight",
    load: () => get("/api/v1/insight/years"),
    view: ({ data: years }) => {
      const params = insightParams();
      const figures = (p) => OVERVIEW.map((metric) => ({ metric, options: pick(p, "basis") }));
      return h("div", { class: "stack-lg" },
        header("Portfolio overview", "/insight", figures,
          h("p", { class: "lede" }, "Every figure says what it counts, for whom, over which years, and from which sources. ",
            "Open ", h("strong", {}, "About"), " on any figure for its definition and the records behind it.")),
        cohortBar({ years }),
        h("div", { class: "figure-grid" },
          figure("publications.count", params, { wide: false }),
          figure("funding.awarded", params, { wide: false }),
          figureCard({ metric: "topics.landscape", params,
            draw: (f) => categoryBars(f, { valueKey: "papers", shareKey: "share", limit: 10, extra: (r) => trendMark(r.trend) }) }),
          figure("publications.influence", params),
          figure("studies.by_status", params),
          figure("studies.by_phase", params)));
    },
  });
}

export function funding(main) {
  return show(main, {
    title: "Funding",
    load: () => Promise.all([get("/api/v1/insight/years"), get("/api/v1/meta")]),
    view: ([{ data: years }, { data: meta }]) => {
      const params = insightParams();
      const kinds = (meta.vocabulary?.kinds ?? []).map((k) => ({ key: k.id, label: k.label }));
      const kind = query().get("kind") || (kinds.find((k) => k.key === "program") ?? kinds[0])?.key || "program";
      const months = query().get("months") || "12";
      const money = (p) => pick(p, "amount", "awardee");
      const figures = (p) => [
        ...["funding.awarded", "funding.by_institute", "funding.by_activity", "funding.by_mechanism"].map((metric) => ({ metric, options: money(p) })),
        { metric: "funding.by_group", options: { ...money(p), kind } },
        { metric: "funding.ending", options: { ...pick(p, "awardee"), months } },
      ];
      const groups = figure("funding.by_group", { ...params, kind });
      groups.querySelector(".card-header").append(h("select", { class: "input input-sm", "aria-label": "Kind of group",
        onchange: (e) => setQuery({ kind: e.target.value }) },
      kinds.map((k) => h("option", { value: k.key, selected: k.key === kind || null }, k.label))));
      const ending = figure("funding.ending", { ...params, months }, { empty: `No grant's project ends in the next ${months} months.` });
      ending.querySelector(".card-header").append(segmented(MONTHS, months, (id) => setQuery({ months: id === "12" ? null : id }), "Ending within"));
      return h("div", { class: "stack-lg" },
        header("Funding", "/insight/funding", figures,
          h("p", { class: "lede" }, "Awards from NIH RePORTER, counted per grant and per fiscal-year record, so nothing is counted twice. ",
            "Amounts are what was awarded, not what was spent, and public data has awards only, never applications.")),
        cohortBar({ years, fiscal: true, withBasis: false, options: [AMOUNT, AWARDEE] }),
        h("div", { class: "figure-grid" },
          figure("funding.awarded", params),
          figure("funding.by_institute", params),
          figure("funding.by_activity", params),
          figure("funding.by_mechanism", params),
          groups,
          ending));
    },
  });
}

export function topics(main) {
  return show(main, {
    title: "Topics",
    load: () => get("/api/v1/insight/years"),
    view: ({ data: years }) => {
      const params = insightParams();
      const figures = (p) => [
        { metric: "topics.landscape", options: pick(p, "basis", "level") },
        { metric: "topics.by_year", options: pick(p, "basis", "level") },
        { metric: "publications.influence", options: pick(p, "basis") },
      ];
      return h("div", { class: "stack-lg" },
        header("Topics", "/insight/topics", figures,
          h("p", { class: "lede" }, "What the cohort's papers are about, from the major MeSH headings that NLM's indexers give each paper. ",
            "A trend is claimed only when there are enough papers in both halves of the period to support it.")),
        cohortBar({ years, options: [LEVEL] }),
        h("div", { class: "figure-grid" },
          figure("topics.landscape", params),
          figure("topics.by_year", params),
          figure("publications.influence", params)));
    },
  });
}

const STUDY_METRICS = ["studies.by_status", "studies.by_phase", "studies.by_sponsor", "studies.by_intervention", "studies.by_condition", "studies.by_start_year"];

export function studies(main) {
  return show(main, {
    title: "Studies",
    load: () => get("/api/v1/insight/years"),
    view: ({ data: years }) => {
      const params = insightParams();
      const level = query().get("level") === "condition" ? "condition" : "area";
      const figures = (p) => STUDY_METRICS.map((metric) => ({ metric,
        options: { ...pick(p, "basis"), ...(metric === "studies.by_condition" ? { level } : {}) } }));
      const conditions = figure("studies.by_condition", { ...params, level });
      conditions.querySelector(".card-header").append(segmented(CONDITION_LEVEL, level,
        (id) => setQuery({ level: id === "area" ? null : id }), "Conditions as"));
      return h("div", { class: "stack-lg" },
        header("Studies", "/insight/studies", figures,
          h("p", { class: "lede" }, "Clinical studies registered at ClinicalTrials.gov that were under way in the period. ",
            "Status is always the registry's overall status as the sponsor reported it, never recruitment here.")),
        cohortBar({ years }),
        h("div", { class: "figure-grid" }, STUDY_METRICS.map((m) => (m === "studies.by_condition" ? conditions : figure(m, params)))));
    },
  });
}

// --- reports and snapshots ---------------------------------------------------------------------

function periodText(r) {
  if (r.start && r.end) return r.start === r.end ? String(r.start) : `${r.start}–${r.end}`;
  if (r.start) return `${r.start} on`;
  if (r.end) return `to ${r.end}`;
  return "The default years";
}

export function reports(main) {
  return show(main, {
    title: "Reports",
    load: () => Promise.all([get("/api/v1/insight/reports", null, { fresh: true }), get("/api/v1/insight/snapshots", null, { fresh: true })]),
    view: ([{ data: list }, { data: snapshots }]) => h("div", { class: "stack-lg" },
      header("Reports", "/insight/reports", null,
        h("p", { class: "lede" }, "A report is a named set of figures over one cohort and one period. Opening it computes them from the data as it is now; ",
          "a snapshot freezes them with the exact records they were computed from, so they can be shown, checked, and exported later, whatever has changed since.")),
      list.length ? h("div", { class: "grid-cards" }, list.map((r) => h("a", { class: "card card-pad report-card", href: `/insight/reports/${r.id}` },
        h("h2", {}, r.name),
        r.description ? h("p", { class: "muted clamp-2" }, r.description) : null,
        h("div", { class: "report-meta" },
          h("span", {}, plural(r.figures.length, "figure")), h("span", {}, periodText(r)),
          h("span", {}, plural(r.snapshots ?? 0, "snapshot")))))) : card({ body: emptyState({ iconName: "chart", title: "No reports yet",
        text: "Choose a cohort and years on any Insight page, then Save as report." }) }),
      snapshots.length ? card({ title: "Recent snapshots", pad: true, body: snapshotList(snapshots.slice(0, 12)) }) : null),
  });
}

function snapshotList(snapshots) {
  return h("ul", { class: "plain-list snapshot-list" }, snapshots.map((s) => h("li", {},
    h("a", { href: `/insight/snapshots/${s.id}` }, s.name),
    h("span", { class: "muted" }, `${date(s.taken_at)} · ${s.taken_by}${s.note ? ` · ${s.note}` : ""}`),
    h("code", { class: "metric-ref" }, s.digest.slice(0, 12)))));
}

export function reportPage(main, { id }) {
  return show(main, {
    title: "Report",
    load: () => get(`/api/v1/insight/reports/${id}`, null, { fresh: true }),
    view: ({ data: r }) => {
      const base = { cohort: r.cohort, ...(r.start ? { start: r.start } : {}), ...(r.end ? { end: r.end } : {}) };
      const cohortLabel = h("span", {}, r.cohort);
      get("/api/v1/insight/cohort", { cohort: r.cohort, limit: 0 }).then(({ data }) => {
        cohortLabel.textContent = `${data.label} (${plural(data.size, "person", "people")})`;
      }).catch(() => {});
      const snapshotButton = mayShare() ? h("button", { type: "button", class: "btn btn-primary", onclick: () => openDrawer({
        title: "Take a snapshot",
        subtitle: "Freeze every figure with the exact records it is computed from.",
        render: (body) => {
          const note = h("input", { class: "input", maxlength: 2000, id: "snapshot-note", placeholder: "For the annual review" });
          body.append(h("form", { class: "stack", onsubmit: async (event) => {
            event.preventDefault();
            try {
              const snap = (await post(`/api/v1/insight/reports/${id}/snapshots`, { note: note.value || null })).data;
              closeDrawer();
              toast("Snapshot taken.");
              navigate(`/insight/snapshots/${snap.id}`);
            } catch (error) {
              toast(error.detail || error.message, "error");
            }
          } }, h("label", { class: "field", for: "snapshot-note" }, h("span", {}, "Note (optional)"), note),
          h("div", {}, h("button", { class: "btn btn-primary", type: "submit" }, icon("history", "icon icon-sm"), "Take snapshot"))));
        },
      }) }, icon("history", "icon icon-sm"), "Take snapshot") : null;
      const remove = mayShare() ? h("button", { type: "button", class: "btn btn-ghost", onclick: async () => {
        if (!window.confirm(`Delete the report “${r.name}”? Its snapshots stay.`)) return;
        try {
          await request("DELETE", `/api/v1/insight/reports/${id}`);
          toast(`Deleted “${r.name}”.`);
          navigate("/insight/reports");
        } catch (error) {
          toast(error.detail || error.message, "error");
        }
      } }, icon("trash", "icon icon-sm"), "Delete") : null;
      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: "Insight · Report", title: r.name, actions: [snapshotButton, remove],
          meta: [h("span", {}, "Cohort: ", cohortLabel), h("span", {}, `Years: ${periodText(r)}`), h("span", {}, `Saved by ${r.created_by}`)] }),
        insightNav(`/insight/reports/${id}`),
        r.description ? h("p", { class: "lede" }, r.description) : null,
        callout("These figures are computed from the data as it is now. Take a snapshot to freeze them."),
        r.snapshot_list.length ? card({ title: "Snapshots", body: h("div", { class: "card-body" }, snapshotList(r.snapshot_list)) }) : null,
        h("div", { class: "figure-grid" }, r.figures.map((f) => figure(f.metric, { ...base, ...f.options }))));
    },
  });
}

export function snapshotPage(main, { id }) {
  return show(main, {
    title: "Snapshot",
    load: () => get(`/api/v1/insight/snapshots/${id}`),
    view: ({ data: s }) => {
      const m = s.manifest;
      const status = h("div", { class: "row-sm" });
      const verify = h("button", { type: "button", class: "btn", onclick: async () => {
        verify.disabled = true;
        replace(status, h("span", { class: "muted" }, "Computing every figure again from the frozen inputs…"));
        try {
          const result = (await get(`/api/v1/insight/snapshots/${id}/verify`, null, { fresh: true })).data;
          replace(status, result.same
            ? callout(`Verified: all ${result.figures.length} figures compute again exactly from the frozen inputs.`)
            : callout(["The snapshot does not compute again as it was.", ...result.problems,
              ...result.figures.filter((f) => !f.same).map((f) => `${f.metric}: ${f.problem ?? "different"}`)].join(" "), "warn"));
        } catch (error) {
          replace(status, callout(error.detail || error.message, "warn"));
        } finally {
          verify.disabled = false;
        }
      } }, icon("check", "icon icon-sm"), "Verify");
      const all = h("details", { class: "export-menu" }, h("summary", { class: "btn" }, icon("download", "icon icon-sm"), "Export all"),
        exportLinks((fmt) => `/api/v1/insight/snapshots/${id}/export?format=${fmt}`));
      let full = null;
      const loadRecords = async () => {
        full = full ?? (await get(`/api/v1/insight/snapshots/${id}`, { records: "true" })).data.figures;
        return full;
      };
      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: "Insight · Snapshot", title: s.name, actions: [verify, all],
          meta: [h("span", {}, `Taken ${date(s.taken_at)} (${relative(s.taken_at)}) by ${s.taken_by}`),
            h("span", {}, `Cohort: ${m.cohort.label} (${plural(m.cohort.size, "person", "people")})`),
            h("code", { class: "metric-ref", title: "The snapshot's digest" }, s.digest.slice(0, 16))] }),
        insightNav("/insight/reports"),
        s.note ? h("p", { class: "lede" }, s.note) : null,
        callout(`These figures are frozen as they were on ${date(s.taken_at)}, with the records they were computed from. Later syncs do not change them.`),
        status,
        s.report_id ? h("p", {}, h("a", { href: `/insight/reports/${s.report_id}` }, "Open the report with today's data")) : null,
        h("div", { class: "figure-grid" }, s.figures.map((f, i) => figureCard({ metric: f.metric.id, params: {}, data: f,
          draw: DRAW[f.metric.id] ?? null, wide: WIDE.has(f.metric.id),
          exportHref: (fmt) => `/api/v1/insight/snapshots/${id}/export?format=${fmt}&figure=${i + 1}`,
          recordsLoader: async () => (await loadRecords())[i] }))));
    },
  });
}

// --- definitions ---------------------------------------------------------------------------------

function metricCard(m) {
  return h("article", { class: "card card-pad definition", id: m.id },
    h("header", { class: "row-sm definition-head" },
      h("h3", { class: "grow" }, m.name), h("code", { class: "metric-ref" }, m.ref)),
    h("p", {}, m.definition),
    h("dl", { class: "kv" },
      h("dt", {}, "Counting"), h("dd", {}, m.counting),
      h("dt", {}, "Unit"), h("dd", {}, enumLabel(m.unit)),
      h("dt", {}, "Years"), h("dd", {}, m.period === "fiscal" ? "The funder's fiscal years" : m.period === "upcoming" ? "From today" : "Calendar years"),
      h("dt", {}, "Sources"), h("dd", {}, m.sources.map(enumLabel).join(", ")),
      m.options.length ? [h("dt", {}, "Options"), h("dd", {}, m.options.map((o) =>
        h("div", {}, o.free ? `${o.label}: any kind (default ${o.default})`
          : `${o.label}: ${o.choices.map((c) => c.label + (c.value === o.default ? " (default)" : "")).join(", ")}`)))] : null,
      m.caveats.length ? [h("dt", {}, "Keep in mind"), h("dd", {}, m.caveats.map((c) => h("div", {}, c)))] : null));
}

export function definitions(main) {
  return show(main, {
    title: "Definitions",
    load: () => get("/api/v1/insight/metrics"),
    view: ({ data: metrics }) => {
      const areas = [...new Set(metrics.map((m) => m.area))];
      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: "Insight", title: "Definitions",
          meta: `${number(metrics.length)} metrics, each with a version. A change to what a metric counts is a new version, so a saved report always says what it counted.` }),
        insightNav("/insight/definitions"),
        h("div", { class: "row-sm muted" }, icon("info", "icon icon-sm"),
          "No figure ranks people: figures break down by year, funder, activity, topic, phase, or group."),
        areas.map((area) => h("section", { class: "stack" }, h("h2", { class: "section-title" }, area),
          h("div", { class: "definition-grid" }, metrics.filter((m) => m.area === area).map(metricCard)))));
    },
  });
}
