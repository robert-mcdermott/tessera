// Saved searches (DIS-10): save the search on screen, see what is new since you last looked,
// and find your saved searches on the home page. Each person's saved searches are their own.

import { get, post, request } from "../core/api.js";
import { h, icon, replace } from "../core/dom.js";
import { date, number, plural } from "../core/format.js";
import { navigate } from "../core/router.js";
import { closeDrawer, openDrawer } from "./drawer.js";
import { itemFor, personRow } from "./items.js";
import { toast } from "./toast.js";
import { callout, card, refKind } from "./ui.js";

const PATHS = { person: "/people", publication: "/publications", grant: "/grants", study: "/studies" };

/** Where a saved search shows its results: the browse page with its filters, or Search. */
export function savedHref(s) {
  const params = new URLSearchParams();
  if (s.q) params.set("q", s.q);
  for (const [name, values] of Object.entries(s.filters ?? {})) for (const v of values) params.append(name, v);
  params.set("saved", s.id);
  return `${PATHS[s.kind] ?? "/search"}?${params}`;
}

export function saveSearchButton({ kind, q, filters, suggested }) {
  return h("button", { type: "button", class: "btn", title: "Save this search and see what is new later",
    onclick: () => openDrawer({
      title: "Save this search",
      subtitle: "Come back to it and see the matches that are new since you last looked.",
      render: (body) => {
        const name = h("input", { class: "input", required: true, maxlength: 200, value: suggested, id: "saved-name" });
        body.append(h("form", { class: "stack", onsubmit: async (event) => {
          event.preventDefault();
          try {
            const saved = (await post("/api/v1/saved-searches", { name: name.value, kind, q, filters })).data;
            closeDrawer();
            toast(`Saved “${saved.name}”. ${plural(saved.total, "match", "matches")} now.`);
            navigate(savedHref(saved), { replace: true });
          } catch (error) {
            toast(error.detail || error.message, "error");
          }
        } },
        h("label", { class: "field", for: "saved-name" }, h("span", {}, "Name"), name),
        h("p", { class: "muted" }, "Saved searches are yours alone. What the search finds now counts as seen."),
        h("div", {}, h("button", { class: "btn btn-primary", type: "submit" }, icon("bookmark", "icon icon-sm"), "Save"))));
        queueMicrotask(() => name.select());
      },
    }) }, icon("bookmark", "icon icon-sm"), "Save search");
}

/** Shown above results when the page was opened from a saved search: what is new. */
export function savedSearchPanel(id) {
  const body = h("div", { class: "card-body" }, h("p", { class: "muted" }, "Checking for new matches…"));
  const panel = card({ title: "Saved search", body, attrs: { class: "card saved-panel" } });
  const title = panel.querySelector(".card-header h2");
  get(`/api/v1/saved-searches/${id}`, null, { fresh: true }).then(({ data }) => {
    title.textContent = data.name;
    const actions = h("div", { class: "row-sm" },
      data.new ? h("button", { type: "button", class: "btn btn-sm", onclick: async () => {
        await post(`/api/v1/saved-searches/${id}/seen`);
        toast("Marked as seen.");
        navigate(window.location.pathname + window.location.search, { replace: true });
      } }, icon("check", "icon icon-sm"), "Mark as seen") : null,
      h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: async () => {
        await request("DELETE", `/api/v1/saved-searches/${id}`);
        toast(`Deleted “${data.name}”.`);
        const params = new URLSearchParams(window.location.search);
        params.delete("saved");
        navigate(`${window.location.pathname}?${params}`, { replace: true });
      } }, icon("trash", "icon icon-sm"), "Delete"));
    const lead = data.new
      ? `${plural(data.new, "new match", "new matches")} since you last looked on ${date(data.seen_at)}, of ${number(data.total)} in all.`
      : `Nothing new since you last looked on ${date(data.seen_at)}; ${plural(data.total, "match", "matches")} in all.`;
    replace(body, h("div", { class: "row-sm saved-lead" }, h("p", { class: "grow" }, lead), actions),
      data.new_items.length ? h("div", { class: "items" }, data.new_items.map(({ ref, summary }) => {
        const kind = refKind(ref);
        if (!summary) return null;
        return kind === "person" ? personRow(summary) : itemFor(kind, { summary });
      })) : null,
      data.new > data.new_items.length ? h("p", { class: "muted" }, `The first ${data.new_items.length} are shown.`) : null);
  }).catch((error) => replace(body, callout(error.detail || error.message, "warn")));
  return panel;
}

/** The home page's list of the person's saved searches, with how many matches are new. */
export function savedSearchesCard() {
  const body = h("div", { class: "card-body" });
  const panel = card({ title: "Your saved searches", body });
  get("/api/v1/saved-searches", null, { fresh: true }).then(({ data }) => {
    if (!data.length) {
      panel.hidden = true;
      return;
    }
    replace(body, h("ul", { class: "plain-list saved-list" }, data.map((s) => h("li", {},
      h("a", { href: savedHref(s) }, s.name),
      s.new ? h("span", { class: "badge badge-accent" }, `${number(s.new)} new`) : h("span", { class: "subtle" }, `${number(s.total)}`)))));
  }).catch(() => { panel.hidden = true; });
  return panel;
}
