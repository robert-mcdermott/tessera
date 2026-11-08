// Insight: portfolio figures over a cohort and a period, each with what it counts (INT-01).
// Loaded on first use. Every page shares the cohort bar and the figure card.

import { get } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { enumLabel, number } from "../core/format.js";
import { cohortBar, insightParams } from "../components/cohort-bar.js";
import { figureCard, yearChart } from "../components/figure.js";
import { pageHeader } from "../components/ui.js";
import { show } from "./page.js";

export const INSIGHT_PAGES = [
  { href: "/insight", label: "Overview" },
  { href: "/insight/definitions", label: "Definitions" },
];

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
            metric: "publications.count", params, wide: true,
            draw: (f) => yearChart(f, f.options.basis?.value === "confirmed"
              ? [{ key: "confirmed", label: "Confirmed", tone: "solid" }]
              : [{ key: "confirmed", label: "Confirmed", tone: "solid" }, { key: "likely", label: "Likely", tone: "soft" }],
            { label: "Publications by year" }),
          })));
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
