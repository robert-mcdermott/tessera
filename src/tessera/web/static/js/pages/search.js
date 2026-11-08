// Search everything, grouped by kind; a kind tab switches to the full browse view.

import { get } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { number } from "../core/format.js";
import { query, setQuery } from "../core/router.js";
import { itemFor, personRow } from "../components/items.js";
import { saveSearchButton, savedSearchPanel } from "../components/saved-search.js";
import { card, emptyState, pageHeader, tabs } from "../components/ui.js";
import { browse, KINDS } from "./browse.js";
import { show } from "./page.js";

const ORDER = ["person", "publication", "grant", "study"];

export function searchPage(main, { samePage = false } = {}) {
  const q = query().get("q") ?? "";
  const type = query().get("type");
  if (type && KINDS[type]) return browseWithTabs(main, q, type, samePage);
  return show(main, {
    title: q ? `Search: ${q}` : "Search",
    quiet: samePage,
    load: () => (q ? get("/api/v1/search", { q, per_type: 5 }) : Promise.resolve({ data: null })),
    view: ({ data }) => {
      const header = searchHeader(q, data, null);
      if (!q) {
        return h("div", {}, header, card({ body: emptyState({ iconName: "search", title: "Search everything",
          text: "People, publications, grants, and studies, in one place. Tip: press ⌘K (Ctrl K) from anywhere." }) }));
      }
      const total = ORDER.reduce((sum, kind) => sum + (data[kind]?.total ?? 0), 0);
      if (!total) {
        return h("div", {}, header, card({ body: emptyState({ iconName: "search", title: `Nothing found for “${q}”`,
          text: "Check the spelling, or try a broader term." }) }));
      }
      const savedId = query().get("saved");
      return h("div", { class: "stack-lg" }, header,
        savedId ? savedSearchPanel(savedId) : h("div", { class: "row-sm" }, saveSearchButton({ kind: "everything", q, filters: {}, suggested: q })),
        ORDER.filter((kind) => data[kind]?.total).map((kind) =>
        card({
          title: `${KINDS[kind].title} (${number(data[kind].total)})`,
          action: data[kind].total > data[kind].items.length
            ? h("a", { href: `/search?q=${encodeURIComponent(q)}&type=${kind}` }, `See all ${number(data[kind].total)}`) : null,
          body: h("div", { class: "items" }, data[kind].items.map((item) =>
            kind === "person" ? personRow(item.summary, item.highlights) : itemFor(kind, item))),
        })));
    },
  });
}

function searchHeader(q, data, type) {
  const input = h("input", { class: "input", type: "search", value: q, placeholder: "Search everything",
    "aria-label": "Search everything", autocomplete: "off" });
  const counts = Object.fromEntries(ORDER.map((k) => [k, data?.[k]?.total]));
  return h("div", {},
    pageHeader({ title: q ? `Results for “${q}”` : "Search" }),
    h("form", { class: "search-field toolbar", role: "search", onsubmit: (event) => {
      event.preventDefault();
      setQuery({ q: input.value.trim() }, { replace: false });
    } }, icon("search"), input),
    q ? tabs([{ id: "all", label: "All" }, ...ORDER.map((k) => ({ id: k, label: KINDS[k].title, count: counts[k] }))],
      type ?? "all", (id) => setQuery({ type: id === "all" ? null : id, page: null }, { replace: false })) : null);
}

function browseWithTabs(main, q, type, samePage) {
  // The typed view is the browse page for that kind, carrying the query.
  return browse(main, type, { samePage });
}
