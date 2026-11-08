// Browse pages for people, publications, grants, and studies: search, facets, sort, pages.
// Everything lives in the URL so views can be shared and Back works.

import { get } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { enumLabel, number } from "../core/format.js";
import { query, queryList, setQuery } from "../core/router.js";
import { read, write } from "../core/prefs.js";
import { itemFor, personCard, personTable } from "../components/items.js";
import { saveSearchButton, savedSearchPanel } from "../components/saved-search.js";
import { card, debounce, emptyState, facetPanel, FACET_LABELS, pageHeader, pager, segmented } from "../components/ui.js";
import { show } from "./page.js";

export const KINDS = {
  person: { title: "People", path: "/people", endpoint: "/api/v1/people", facets: ["status", "unit"],
    sorts: [["name", "Name"], ["activity", "Most papers"]], placeholder: "Search people by name, unit, or topic",
    blurb: "Researchers found in grants, publications, and the roster." },
  publication: { title: "Publications", path: "/publications", endpoint: "/api/v1/publications",
    facets: ["year", "journal", "type", "flag"], sorts: [["year", "Newest"], ["citations", "Most cited"]],
    placeholder: "Search titles, abstracts, authors, topics", blurb: "Papers by members, with the organization's affiliation, or tied to its grants and studies." },
  grant: { title: "Grants", path: "/grants", endpoint: "/api/v1/grants", facets: ["status", "activity", "institute", "year"],
    sorts: [["year", "Newest"], ["amount", "Largest"]], placeholder: "Search titles, abstracts, investigators, numbers",
    blurb: "NIH awards held by members or made to the organization." },
  study: { title: "Studies", path: "/studies", endpoint: "/api/v1/studies", facets: ["status", "phase", "type", "year"],
    sorts: [["year", "Newest"]], placeholder: "Search titles, conditions, interventions, officials",
    blurb: "Registry records for studies the organization sponsors, hosts, or leads. Statuses are registry statuses." },
  opportunity: { title: "Opportunities", path: "/opportunities", endpoint: "/api/v1/opportunities",
    facets: ["status", "closing", "source", "kind", "agency", "activity", "category"], sorts: [["closes", "Closing soonest"], ["posted", "Newest"]],
    placeholder: "Search titles, sponsors, numbers, and descriptions",
    blurb: "Funding opportunities: forecast and posted federal ones from Grants.gov, and those the organization tracks itself." },
};

const LIMIT = 24;

const OPPORTUNITY_FACETS = { posted: "Open", forecasted: "Forecast", grants_gov: "Grants.gov", local: "Tracked here",
  30: "30 days", 90: "90 days" };

function facetFormat(name, value) {
  if (name === "closing" || name === "source" || (name === "status" && OPPORTUNITY_FACETS[value])) return OPPORTUNITY_FACETS[value] ?? value;
  if (name === "status" || name === "phase" || name === "type" || name === "kind") return enumLabel(value);
  return value;
}

export function browse(main, kind, { samePage = false } = {}) {
  const spec = KINDS[kind];
  const q = query().get("q") ?? "";
  const sort = query().get("sort") ?? "";
  const pageNo = Math.max(1, Number(query().get("page") ?? 1) || 1);
  const selected = Object.fromEntries(spec.facets.map((f) => [f, queryList(f)]));
  const view = kind === "person" ? (query().get("view") ?? read("peopleView", "cards")) : "list";
  const savedId = query().get("saved");

  return show(main, {
    title: spec.title,
    quiet: samePage,
    load: () => get(spec.endpoint, { q, sort, limit: LIMIT, offset: (pageNo - 1) * LIMIT, ...selected }),
    view: (result) => {
      const { data, meta } = result;
      const searchInput = h("input", { class: "input", type: "search", value: q, placeholder: spec.placeholder,
        "aria-label": `Search ${spec.title.toLowerCase()}` });
      const go = debounce(() => setQuery({ q: searchInput.value.trim(), page: null }), 300);
      searchInput.addEventListener("input", go);
      searchInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); setQuery({ q: searchInput.value.trim(), page: null }); }
      });
      queueMicrotask(() => {
        if (samePage && q) { searchInput.focus(); searchInput.setSelectionRange(q.length, q.length); }
      });

      const sortSelect = h("select", { class: "input", "aria-label": "Sort", onchange: (event) => setQuery({ sort: event.target.value, page: null }) },
        q ? h("option", { value: "", selected: !sort }, "Best match") : null,
        spec.sorts.map(([value, label]) => h("option", { value, selected: sort === value || (!sort && !q && value === spec.sorts[0][0]) }, label)));

      const active = Object.entries(selected).flatMap(([name, values]) => values.map((value) =>
        h("span", { class: "filter-chip" }, `${FACET_LABELS[name] ?? name}: ${facetFormat(name, value)}`,
          h("button", { type: "button", "aria-label": `Remove filter ${value}`, onclick: () => setQuery({ [name]: values.filter((v) => v !== value), page: null }) },
            icon("close", "icon icon-sm"))))) ;

      let results;
      if (!data.length) {
        results = card({ body: emptyState({ kind, title: q || active.length ? "No matches" : `No ${spec.title.toLowerCase()} yet`,
          text: q || active.length ? "Try fewer words or remove a filter." : "Run `tessera sync` to fetch data." }) });
      } else if (kind === "person" && view === "cards") {
        results = h("div", { class: "grid-cards" }, data.map((p) => personCard(p, { highlights: p.highlights })));
      } else if (kind === "person") {
        results = personTable(data);
      } else {
        results = card({ body: h("div", { class: "items" }, data.map((item) => itemFor(kind, item, { highlights: item.highlights }))) });
      }
      const pages = pager({ total: meta.total, offset: (pageNo - 1) * LIMIT, limit: LIMIT,
        onPage: (offset) => setQuery({ page: offset / LIMIT + 1 }, { replace: false }) });

      return h("div", {},
        pageHeader({ eyebrow: spec.blurb, title: spec.title,
          meta: h("span", { class: "result-count tabular" }, `${number(meta.total)} ${meta.total === 1 ? "result" : "results"}`) }),
        h("div", { class: "browse" },
          facetPanel(Object.fromEntries(spec.facets.map((f) => [f, meta.facets?.[f] ?? []])), selected,
            (name, values) => setQuery({ [name]: values, page: null }), { format: facetFormat }),
          h("div", { class: "stack" },
            h("div", { class: "toolbar" },
              h("div", { class: "search-field" }, icon("search"), searchInput),
              sortSelect,
              kind === "person" ? segmented([{ id: "cards", icon: "grid", title: "Cards" }, { id: "table", icon: "list", title: "Table" }],
                view, (id) => { write("peopleView", id); setQuery({ view: id }); }, "View") : null,
              savedId ? null : saveSearchButton({ kind, q, filters: Object.fromEntries(Object.entries(selected).filter(([, v]) => v.length)),
                suggested: q || (active.length ? `${spec.title}: ${Object.values(selected).flat().join(", ")}` : spec.title) })),
            savedId ? savedSearchPanel(savedId) : null,
            active.length ? h("div", { class: "active-filters" }, active) : null,
            results,
            pages ? card({ body: pages }) : null)));
    },
  });
}
