// Home: the organization's research footprint at a glance.

import { get } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { number } from "../core/format.js";
import { navigate } from "../core/router.js";
import { grantItem, opportunityList, publicationItem } from "../components/items.js";
import { savedSearchesCard } from "../components/saved-search.js";
import { barChart, card, emptyState, freshness, statTile } from "../components/ui.js";
import { show } from "./page.js";

export function home(main, meta) {
  return show(main, {
    title: "Home",
    load: async () => {
      const [stats, pubs, grants, mine] = await Promise.all([
        get("/api/v1/stats"),
        get("/api/v1/publications", { sort: "year", limit: 6 }).catch(() => null),
        get("/api/v1/grants", { status: "active", sort: "amount", limit: 5 }).catch(() => null),
        get("/api/v1/me/opportunities", null, { fresh: true }).catch(() => null),
      ]);
      return { stats: stats.data, pubs: pubs?.data ?? [], grants: grants?.data ?? [], mine };
    },
    view: ({ stats, pubs, grants, mine }) => {
      const org = meta.organization;
      const years = stats.publications_by_year ?? [];
      const recentYears = years.slice(-8);
      if (stats.publications == null) {
        return h("div", { class: "stack-lg" }, hero(org, meta),
          emptyState({ iconName: "layers", title: "No data yet",
            text: "Run `tessera sync` to fetch your organization's public footprint and build the search index. Then reload this page." }));
      }
      return h("div", { class: "stack-lg" },
        hero(org, meta),
        h("div", { class: "stats" },
          statTile({ kind: "person", label: "People", value: stats.people.active,
            note: stats.people.candidates ? `${number(stats.people.candidates)} more awaiting review` : null }),
          statTile({ kind: "publication", label: "Publications", value: stats.publications,
            series: recentYears.map((y) => y.count), note: recentYears.length ? `${recentYears[0].year}–${recentYears.at(-1).year}` : null }),
          statTile({ kind: "grant", label: "Active grants", value: stats.grants.active,
            note: `${number(stats.grants.total)} in the data` }),
          statTile({ kind: "study", label: "Clinical studies", value: stats.studies })),
        h("div", { class: "grid-main" },
          h("div", { class: "stack-lg" },
            card({ title: "Recent publications", action: h("a", { href: "/publications" }, "All publications"),
              body: pubs.length ? h("div", { class: "items" }, pubs.map((p) => publicationItem(p)))
                : emptyState({ kind: "publication", title: "No publications yet" }) }),
            recentYears.length > 1 ? card({ title: "Publications by year", pad: true,
              attrs: { "data-entity": "publication" },
              body: barChart(recentYears.map((y) => ({ label: String(y.year), value: y.count })),
                { caption: "Publications by year" }) }) : null),
          h("div", { class: "stack-lg" },
            savedSearchesCard(),
            mine?.data.length ? card({ title: "Opportunities for you", pad: true, attrs: { "data-entity": "opportunity" },
              action: mine.meta.linked ? h("a", { href: "/opportunities?fits=me&status=posted&status=forecasted" }, "All that fit") : null,
              body: h("div", { class: "stack-sm" }, opportunityList(mine.data),
                h("p", { class: "subtle" }, "Open opportunities that fit your confirmed work, and those you watch. Fit is not eligibility; each says whether the organization may apply.")) }) : null,
            card({ title: "Largest active grants", action: h("a", { href: "/grants?status=active" }, "All grants"),
              body: grants.length ? h("div", { class: "items" }, grants.map((g) => grantItem(g)))
                : h("div", { class: "card-body muted" }, "No active grants in the data.") }),
            card({ title: "Data sources", action: h("a", { href: "/admin/sources" }, "Details"), pad: true,
              body: h("div", { class: "stack-sm" }, (meta.sources ?? []).map(freshness),
                stats.index_built_at ? h("span", { class: "subtle" }, "Search index updated with the last sync.") : null) }))));
    },
  });
}

function hero(org, meta) {
  const input = h("input", { class: "input", type: "search", name: "q", placeholder: "Search people, topics, grants, studies…",
    "aria-label": "Search everything", autocomplete: "off" });
  return h("section", { class: "hero" },
    h("div", { class: "hero-text" },
      h("div", { class: "eyebrow" }, meta.product.tagline),
      h("h1", { class: "page-title", tabindex: "-1" }, org.name),
      h("p", { class: "hero-lede" },
        "People, publications, grants, and clinical studies from public sources, connected with the evidence behind every link.")),
    h("form", { class: "hero-search search-field", role: "search", onsubmit: (event) => {
      event.preventDefault();
      const q = input.value.trim();
      navigate(q ? `/search?q=${encodeURIComponent(q)}` : "/search");
    } }, icon("search"), input));
}
