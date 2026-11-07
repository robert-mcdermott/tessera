// Discovered people: authors and investigators found in the data who are not in the
// directory yet. A curator promotes them (they become members) or dismisses them (they are
// not proposed again). Both can be undone.

import { get, post } from "../core/api.js";
import { h, highlighted, icon } from "../core/dom.js";
import { number, plural } from "../core/format.js";
import { query, queryList, setQuery } from "../core/router.js";
import { confirmAction } from "../components/drawer.js";
import { toast } from "../components/toast.js";
import { avatar, callout, card, debounce, emptyState, pageHeader, pager, segmented } from "../components/ui.js";
import { show } from "./page.js";

const PAGE = 25;
const STATUSES = [
  { id: "candidate", label: "Awaiting a decision" },
  { id: "active", label: "Promoted" },
  { id: "dismissed", label: "Dismissed" },
];
const SOURCES = [
  { id: "", label: "Any source" },
  { id: "publications", label: "Affiliated papers" },
  { id: "grants", label: "NIH grants" },
];
const VERBS = { promote: "Promoted", dismiss: "Dismissed", undo: "Restored" };
const SUBTITLES = {
  candidate: "found in public data, not yet in the directory",
  active: "promoted from this list; undo to send them back for a decision",
  dismissed: "judged not to be members; restore to decide again",
};

export function candidates(main, ctx = {}) {
  const params = query();
  const status = STATUSES.some((s) => s.id === params.get("status")) ? params.get("status") : "candidate";
  const source = queryList("source")[0] ?? "";
  const q = params.get("q") ?? "";
  const sort = params.get("sort") === "name" ? "name" : "evidence";
  const offset = Number(params.get("offset")) || 0;

  return show(main, {
    title: "Discovered people",
    quiet: ctx.samePage,
    load: async () => (await get("/api/v1/candidates", { status, source: source || null, q: q || null,
      sort: q ? null : sort, limit: PAGE, offset }, { fresh: true })),
    view: (body) => {
      const items = body.data;
      const total = body.meta.total;
      const selected = new Set();
      const bulk = status !== "active"; // promoted people are undone one at a time
      const bulkBar = h("div", { class: "bulk-bar", role: "region", "aria-label": "Act on selected people" });

      const search = h("input", { class: "input", type: "search", value: q, placeholder: "Search names and affiliations",
        "aria-label": "Search discovered people",
        oninput: debounce((event) => setQuery({ q: event.target.value || null, offset: null }), 250) });

      async function decide(ids, action) {
        let result;
        try {
          result = (await post("/api/v1/candidates/decisions", { ids, action })).data;
        } catch (error) {
          toast(error.detail || "That did not work.");
          return;
        }
        const done = result.results.map((r) => r.id);
        const failed = result.failed.length ? ` ${number(result.failed.length)} could not be changed.` : "";
        candidates(main, { samePage: true });
        if (!done.length) {
          toast(result.failed[0]?.detail ?? "Nothing changed.");
          return;
        }
        toast(`${VERBS[action]} ${plural(done.length, "person", "people")}.${failed}`, action === "undo" ? {} : {
          action: { label: "Undo", run: () => decide(done, "undo") },
        });
      }

      function confirmMany(action) {
        const ids = [...selected];
        const verb = action === "promote" ? "Promote" : "Dismiss";
        confirmAction({
          title: `${verb} ${plural(ids.length, "person", "people")}`,
          text: h("p", {}, action === "promote"
            ? "They join the directory as active members. Their suggested papers appear after the next resolve or sync."
            : "They leave this list and are not proposed again. You can restore them from the Dismissed view."),
          confirmLabel: `${verb} ${number(ids.length)}`,
          tone: action === "promote" ? "accept" : "reject",
          run: () => decide(ids, action),
        });
      }

      // Always present (never pushes the list down mid-click); buttons wake up on selection.
      function updateBulk() {
        const none = selected.size === 0;
        bulkBar.toggleAttribute("data-active", !none);
        bulkBar.replaceChildren(
          none ? h("span", { class: "muted" }, "Select people to promote or dismiss several at once.") : h("strong", {}, `${number(selected.size)} selected`),
          h("span", { class: "spacer" }),
          status !== "active" ? h("button", { type: "button", class: "btn btn-sm btn-accept", disabled: none, onclick: () => confirmMany("promote") }, icon("check", "icon icon-sm"), "Promote") : null,
          status === "candidate" ? h("button", { type: "button", class: "btn btn-sm btn-reject", disabled: none, onclick: () => confirmMany("dismiss") }, icon("close", "icon icon-sm"), "Dismiss") : null,
          h("button", { type: "button", class: "btn btn-sm btn-ghost", disabled: none, onclick: () => {
            selected.clear();
            table.querySelectorAll("input[type=checkbox]").forEach((c) => { c.checked = false; });
            updateBulk();
          } }, "Clear"));
      }

      const selectAll = h("input", { type: "checkbox", "aria-label": "Select every person on this page", onchange: (event) => {
        for (const box of table.querySelectorAll("tbody input[type=checkbox]")) {
          box.checked = event.target.checked;
          if (box.checked) selected.add(box.value); else selected.delete(box.value);
        }
        updateBulk();
      } });

      const table = h("table", { class: "table candidates-table" },
        h("thead", {}, h("tr", {}, bulk ? h("th", { class: "check" }, selectAll) : null, h("th", {}, "Person"), h("th", {}, "Evidence"), h("th", { class: "actions" }, h("span", { class: "sr-only" }, "Actions")))),
        h("tbody", {}, items.map((c) => row(c))));

      function row(c) {
        const box = h("input", { type: "checkbox", value: c.id, "aria-label": `Select ${c.name}`, onchange: (event) => {
          if (event.target.checked) selected.add(c.id); else selected.delete(c.id);
          updateBulk();
        } });
        const actions = c.status === "candidate"
          ? [h("button", { type: "button", class: "btn btn-sm btn-accept", onclick: () => decide([c.id], "promote") }, "Promote"),
            h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => decide([c.id], "dismiss") }, "Dismiss")]
          : [h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => decide([c.id], "undo") }, icon("undo", "icon icon-sm"),
            c.status === "dismissed" ? "Restore" : "Undo promotion")];
        return h("tr", {},
          bulk ? h("td", { class: "check" }, box) : null,
          h("td", {}, h("div", { class: "row-sm" }, avatar(c.name, "sm"),
            h("div", { class: "stack-xs" },
              h("a", { href: `/people/${c.id}`, class: "strong" }, highlighted(c.highlights?.title ?? c.name)),
              c.names?.length ? h("span", { class: "subtle" }, `Also ${c.names.slice(0, 2).join(", ")}`) : null,
              c.orcid ? h("a", { class: "subtle row-xs", href: `https://orcid.org/${c.orcid}`, target: "_blank", rel: "noopener noreferrer" }, icon("orcid", "icon icon-sm"), c.orcid) : null))),
          h("td", {}, evidence(c)),
          h("td", { class: "actions" }, h("div", { class: "row-sm" }, actions)));
      }

      updateBulk();
      const content = items.length
        ? h("div", { class: "table-wrap" }, table)
        : emptyState({ iconName: "people", title: q ? "No matches" : status === "candidate" ? "No one is waiting" : "Nobody here yet",
          text: status === "candidate" ? "Discovery runs with every sync and proposes affiliated authors who match nobody in the directory." : null });

      return h("div", { class: "stack-lg" },
        pageHeader({
          eyebrow: "Curate",
          title: "Discovered people",
          meta: h("span", {}, `${plural(total, "person", "people")} · ${SUBTITLES[status]}`),
        }),
        status === "candidate" && total ? callout("Promote people who belong to the organization; dismiss the rest. Promoted people get suggested papers at the next resolve or sync.") : null,
        h("div", { class: "toolbar" },
          segmented(STATUSES, status, (id) => setQuery({ status: id === "candidate" ? null : id, offset: null }), "Decision"),
          search,
          h("select", { class: "input", "aria-label": "Found through", onchange: (event) => setQuery({ source: event.target.value || null, offset: null }) },
            SOURCES.map((s) => h("option", { value: s.id, selected: s.id === source }, s.label))),
          h("select", { class: "input", "aria-label": "Sort", disabled: Boolean(q), onchange: (event) => setQuery({ sort: event.target.value === "name" ? "name" : null }) },
            h("option", { value: "evidence", selected: sort === "evidence" }, "Most evidence"),
            h("option", { value: "name", selected: sort === "name" }, "Name"))),
        items.length && bulk ? bulkBar : null,
        card({ body: content }),
        pager({ total, offset, limit: PAGE, onPage: (next) => setQuery({ offset: next || null }) }));
    },
  });
}

function evidence(c) {
  const parts = [];
  if (c.papers) {
    parts.push(h("div", {}, h("strong", {}, plural(c.papers, "affiliated paper")),
      c.years ? h("span", { class: "muted" }, ` · ${c.years[0] === c.years[1] ? c.years[0] : `${c.years[0]}–${c.years[1]}`}`) : null));
    parts.push(h("ul", { class: "plain-list compact" }, c.recent.map((p) =>
      h("li", {}, h("a", { href: `/publications/${encodeURIComponent(p.pmid)}` }, p.title), p.year ? h("span", { class: "subtle" }, ` · ${p.year}`) : null))));
  }
  if (c.grant_count) {
    parts.push(h("div", {}, h("strong", {}, plural(c.grant_count, "NIH grant")), h("span", { class: "muted" }, " at the organization")));
    parts.push(h("ul", { class: "plain-list compact" }, c.grants.map((g) =>
      h("li", {}, h("a", { href: `/grants/${encodeURIComponent(g.core_project_num)}` }, g.title ?? g.core_project_num),
        h("span", { class: "subtle" }, g.contact_pi ? " · contact PI" : " · co-investigator")))));
  }
  if (c.affiliations?.length) parts.push(h("div", { class: "subtle clamp-2" }, c.affiliations[0]));
  if (!parts.length) parts.push(h("span", { class: "muted" }, "No affiliated papers found in the current data."));
  return h("div", { class: "stack-xs" }, parts);
}
