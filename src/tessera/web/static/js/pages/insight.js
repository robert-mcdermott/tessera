// Insight: portfolio figures over a cohort and a period, each with what it counts (INT-01).
// Loaded on first use. Every page shares the cohort bar and the figure card.

import { get } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { enumLabel, money, number } from "../core/format.js";
import { query, setQuery } from "../core/router.js";
import { cohortBar, insightParams } from "../components/cohort-bar.js";
import { categoryBars, figureCard, trendList, trendMark, yearChart } from "../components/figure.js";
import { pageHeader, segmented } from "../components/ui.js";
import { show } from "./page.js";

export const INSIGHT_PAGES = [
  { href: "/insight", label: "Overview" },
  { href: "/insight/funding", label: "Funding" },
  { href: "/insight/topics", label: "Topics" },
  { href: "/insight/studies", label: "Studies" },
  { href: "/insight/definitions", label: "Definitions" },
];

const LEVEL = { key: "level", label: "Topics as", default: "heading",
  choices: [{ value: "heading", label: "MeSH headings" }, { value: "area", label: "Research areas" }] };
const CONDITION_LEVEL = [{ id: "area", label: "Disease areas" }, { id: "condition", label: "As registered" }];

function topicsCard(params, { limit = 30, wide = true } = {}) {
  return figureCard({ metric: "topics.landscape", params, wide,
    draw: (f) => categoryBars(f, { valueKey: "papers", shareKey: "share", limit, extra: (r) => trendMark(r.trend) }) });
}

function studyCard(metric, params, { wide = false } = {}) {
  return figureCard({ metric, params, wide, empty: "No study of this cohort was under way in the period.",
    draw: (f) => categoryBars(f, { valueKey: "studies", shareKey: "share" }) });
}

function influenceCard(params) {
  // Settled ratios are charted; recent papers (provisional) and papers iCite lacks are said.
  const draw = (f) => {
    const settled = f.rows.filter((r) => r.share != null);
    const aside = f.rows.filter((r) => r.share == null && r.papers);
    return h("div", { class: "stack-sm" },
      categoryBars({ ...f, rows: settled }, { valueKey: "papers", shareKey: "share" }),  // the first rows: small marks still line up
      aside.length ? h("p", { class: "muted figure-aside" }, "Not charted: ",
        aside.map((r) => `${number(r.papers)} ${r.label.charAt(0).toLowerCase()}${r.label.slice(1)}`).join("; "), ".") : null);
  };
  return figureCard({ metric: "publications.influence", params, draw });
}

const AMOUNT = { key: "amount", label: "Amount", default: "total",
  choices: [{ value: "total", label: "Total" }, { value: "direct", label: "Direct" }, { value: "indirect", label: "Indirect" }] };
const AWARDEE = { key: "awardee", label: "Awarded to", default: "organization",
  choices: [{ value: "organization", label: "The organization" }, { value: "anywhere", label: "Anywhere" }] };
const MONTHS = [{ id: "6", label: "6 months" }, { id: "12", label: "12 months" }, { id: "24", label: "24 months" }];

const dollars = (v) => money(v, { compactForm: true });

/** Awards by fiscal year, stacked by award type. */
function awardsChart(f) {
  return yearChart({ ...f, rows: f.rows.map((r) => ({ ...r, year: r.fiscal_year })) }, [
    { key: "new", label: "New awards", tone: "solid" },
    { key: "renewal", label: "Renewals", tone: "deep" },
    { key: "continuing", label: "Continuing years", tone: "soft" },
    { key: "supplement", label: "Supplements", tone: "pale" },
  ].filter((s) => f.rows.some((r) => r[s.key])), { format: dollars, label: "Awards by fiscal year" });
}

function categoryCard(metric, params, labelKey = "label") {
  return figureCard({ metric, params,
    draw: (f) => categoryBars(f, { labelKey, valueKey: "amount", shareKey: "share", format: dollars }) });
}

/** The row of Insight's pages, under the header. */
export function insightNav(current) {
  const keep = window.location.search;
  return h("nav", { class: "tabs subnav", "aria-label": "Insight" }, INSIGHT_PAGES.map((p) =>
    h("a", { class: "tab", href: p.href === "/insight/definitions" ? p.href : p.href + keep,
      "aria-current": p.href === current ? "page" : null }, p.label)));
}

function intro() {
  return h("p", { class: "lede" }, "Every figure says what it counts, for whom, over which years, and from which sources. ",
    "Open ", h("strong", {}, "About"), " on any figure for its definition and the records behind it.");
}

export function insightOverview(main) {
  return show(main, {
    title: "Insight",
    load: () => get("/api/v1/insight/years"),
    view: ({ data: years }) => {
      const params = insightParams();
      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: "Insight", title: "Portfolio overview", lead: null }),
        insightNav("/insight"),
        intro(),
        cohortBar({ years }),
        h("div", { class: "figure-grid" },
          figureCard({
            metric: "publications.count", params,
            draw: (f) => yearChart(f, f.options.basis?.value === "confirmed"
              ? [{ key: "confirmed", label: "Confirmed", tone: "solid" }]
              : [{ key: "confirmed", label: "Confirmed", tone: "solid" }, { key: "likely", label: "Likely", tone: "soft" }],
            { label: "Publications by year" }),
          }),
          figureCard({ metric: "funding.awarded", params, draw: awardsChart }),
          topicsCard(params, { limit: 10, wide: false }),
          influenceCard(params),
          studyCard("studies.by_status", params),
          studyCard("studies.by_phase", params)));
    },
  });
}

function endingCard(params) {
  const months = query().get("months") || "12";
  const card = figureCard({ metric: "funding.ending", params: { ...params, months }, wide: true,
    empty: `No grant's project ends in the next ${months} months.` });
  card.querySelector(".card-header").append(segmented(MONTHS, months, (id) => setQuery({ months: id === "12" ? null : id }), "Ending within"));
  return card;
}

function groupCard(params, kinds) {
  const kind = query().get("kind") || (kinds.find((k) => k.key === "program") ?? kinds[0])?.key || "program";
  const select = h("select", { class: "input input-sm", "aria-label": "Kind of group", onchange: (e) => setQuery({ kind: e.target.value }) },
    kinds.map((k) => h("option", { value: k.key, selected: k.key === kind || null }, k.label)));
  const card = figureCard({ metric: "funding.by_group", params: { ...params, kind },
    draw: (f) => categoryBars(f, { labelKey: "group", valueKey: "amount", format: dollars, hrefOf: (r) => `/groups/${r.slug}`, limit: 40 }) });
  card.querySelector(".card-header").append(select);
  return card;
}

export function funding(main) {
  return show(main, {
    title: "Funding",
    load: () => Promise.all([get("/api/v1/insight/years"), get("/api/v1/meta")]),
    view: ([{ data: years }, { data: meta }]) => {
      const params = insightParams();
      const kinds = (meta.vocabulary?.kinds ?? []).map((k) => ({ key: k.id, label: k.label }));
      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: "Insight", title: "Funding" }),
        insightNav("/insight/funding"),
        h("p", { class: "lede" }, "Awards from NIH RePORTER, counted per grant and per fiscal-year record, so nothing is counted twice. ",
          "Amounts are what was awarded, not what was spent, and public data has awards only, never applications."),
        cohortBar({ years, fiscal: true, withBasis: false, options: [AMOUNT, AWARDEE] }),
        h("div", { class: "figure-grid" },
          figureCard({ metric: "funding.awarded", params, wide: true, draw: awardsChart }),
          categoryCard("funding.by_institute", params),
          categoryCard("funding.by_activity", params),
          categoryCard("funding.by_mechanism", params),
          groupCard(params, kinds),
          endingCard(params)));
    },
  });
}

export function topics(main) {
  return show(main, {
    title: "Topics",
    load: () => get("/api/v1/insight/years"),
    view: ({ data: years }) => {
      const params = insightParams();
      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: "Insight", title: "Topics" }),
        insightNav("/insight/topics"),
        h("p", { class: "lede" }, "What the cohort's papers are about, from the major MeSH headings that NLM's indexers give each paper. ",
          "A trend is claimed only when there are enough papers in both halves of the period to support it."),
        cohortBar({ years, options: [LEVEL] }),
        h("div", { class: "figure-grid" },
          topicsCard(params),
          figureCard({ metric: "topics.by_year", params, wide: true, draw: (f) => trendList(f) }),
          influenceCard(params)));
    },
  });
}

export function studies(main) {
  return show(main, {
    title: "Studies",
    load: () => get("/api/v1/insight/years"),
    view: ({ data: years }) => {
      const params = insightParams();
      const level = query().get("level") === "condition" ? "condition" : "area";
      const conditions = studyCard("studies.by_condition", { ...params, level });
      conditions.querySelector(".card-header").append(segmented(CONDITION_LEVEL, level,
        (id) => setQuery({ level: id === "area" ? null : id }), "Conditions as"));
      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: "Insight", title: "Studies" }),
        insightNav("/insight/studies"),
        h("p", { class: "lede" }, "Clinical studies registered at ClinicalTrials.gov that were under way in the period. ",
          "Status is always the registry's overall status as the sponsor reported it, never recruitment here."),
        cohortBar({ years }),
        h("div", { class: "figure-grid" },
          studyCard("studies.by_status", params),
          studyCard("studies.by_phase", params),
          studyCard("studies.by_sponsor", params),
          studyCard("studies.by_intervention", params),
          conditions,
          figureCard({ metric: "studies.by_start_year", params,
            draw: (f) => yearChart(f, [{ key: "interventional", label: "Interventional", tone: "solid" },
              { key: "other", label: "Observational and other", tone: "soft" }], { label: "Studies by start year" }) })));
    },
  });
}

function metricCard(m) {
  return h("article", { class: "card card-pad definition", id: m.id },
    h("header", { class: "row-sm definition-head" },
      h("h3", { class: "grow" }, m.name), h("code", { class: "metric-ref" }, m.ref)),
    h("p", {}, m.definition),
    h("dl", { class: "kv" },
      h("dt", {}, "Counting"), h("dd", {}, m.counting),
      h("dt", {}, "Unit"), h("dd", {}, enumLabel(m.unit)),
      h("dt", {}, "Years"), h("dd", {}, m.period === "fiscal" ? "The funder's fiscal years" : "Calendar years"),
      h("dt", {}, "Sources"), h("dd", {}, m.sources.map(enumLabel).join(", ")),
      m.options.length ? [h("dt", {}, "Options"), h("dd", {}, m.options.map((o) =>
        h("div", {}, `${o.label}: ${o.choices.map((c) => c.label + (c.value === o.default ? " (default)" : "")).join(", ")}`)))] : null,
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
