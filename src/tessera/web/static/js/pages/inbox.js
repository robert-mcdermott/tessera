// The inbox (/inbox): each group's weekly review in one place, for its managers and curators:
// papers to attribute, new grants, roster changes, and openings closing soon. Loaded on first
// use.

import { get, post } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { number, plural, relative } from "../core/format.js";
import { setQuery, query } from "../core/router.js";
import { openEvidence } from "../components/drawer.js";
import { grantItem } from "../components/items.js";
import { toast } from "../components/toast.js";
import { callout, card, emptyState, pageHeader, pill } from "../components/ui.js";
import { show } from "./page.js";

const LABELS = {
  papers: ["paper to attribute", "papers to attribute"], grants: ["new grant", "new grants"],
  roster: ["roster change", "roster changes"], openings: ["opening closing soon", "openings closing soon"],
};
const ROSTER = { member_add: "joined", member_end: "left", member_edit: "changed role or dates", member_remove: "was removed" };

export function inboxPage(main, ctx = {}) {
  const wanted = query().get("group");
  const reload = () => inboxPage(main, { samePage: true });
  return show(main, {
    title: "Inbox",
    quiet: ctx.samePage,
    load: async () => {
      const digest = (await get("/api/v1/inbox", null, { fresh: true })).data;
      const chosen = digest.groups.find((g) => g.slug === wanted) ?? digest.groups.find((g) => g.total) ?? digest.groups[0];
      const items = chosen ? (await get(`/api/v1/groups/${chosen.id}/inbox`, null, { fresh: true })).data : null;
      return { digest, chosen, items };
    },
    view: ({ digest, chosen, items }) => {
      const review = digest.review ?? {};
      const queue = (review.strong ?? 0) + (review.possible ?? 0);
      const groupList = card({ title: "Your groups", attrs: { "data-entity": "unit" }, body: digest.groups.length
        ? h("ul", { class: "plain-list inbox-groups" }, digest.groups.map((g) => h("li", {},
          h("a", { class: `inbox-group${g.id === chosen?.id ? " is-current" : ""}`, href: `/inbox?group=${g.slug}`,
            "aria-current": g.id === chosen?.id ? "page" : null },
          h("span", { class: "grow" }, g.name, h("span", { class: "subtle" }, ` · ${g.kind_label}`)),
          g.total ? h("span", { class: "badge" }, number(g.total)) : icon("check", "icon icon-sm")))))
        : h("div", { class: "card-body" }, h("p", { class: "subtle" }, "Nothing is waiting in any group.")) });
      const others = [
        queue ? h("li", {}, h("a", { href: "/review" }, `Review queue: ${number(queue)} suggestions`)) : null,
        review.candidates ? h("li", {}, h("a", { href: "/review/people" }, `Discovered people: ${number(review.candidates)}`)) : null,
        review.duplicates ? h("li", {}, h("a", { href: "/review/duplicates" }, `Possible duplicates: ${number(review.duplicates)}`)) : null,
      ].filter(Boolean);
      return h("div", { class: "stack-lg" },
        pageHeader({
          eyebrow: "Curate",
          title: "Inbox",
          meta: h("span", {}, "Each group's weekly review: new papers to attribute, new grants, roster changes, and openings closing soon, decided in one place."),
        }),
        h("div", { class: "grid-main grid-main-left" },
          h("div", { class: "stack-lg" }, groupList,
            others.length ? card({ title: "Also waiting", pad: true, body: h("ul", { class: "plain-list" }, others) }) : null),
          items ? groupInbox(items, reload) : card({ pad: true, body: emptyState({ iconName: "check", title: "Nothing to review",
            text: "When your groups have papers to attribute, new grants, roster changes, or openings closing soon, they appear here." }) })));
    },
  });
}

function groupInbox(items, reload) {
  const group = items.group;
  const counts = items.counts;
  const since = items.reviewed ? `since ${items.reviewed.reviewed_by} reviewed it ${relative(items.reviewed.reviewed_at)}` : "in the last week";
  const decide = (ids, action) => async () => {
    try {
      await post(`/api/v1/groups/${group.id}/publications/decisions`, { ids, action });
    } catch (failure) {
      toast(failure.detail || "That did not work.");
      return;
    }
    toast(`${action === "accept" ? "Attributed" : "Declined"} ${plural(ids.length, "paper")}.`);
    reload();
  };
  const strongIds = items.papers.filter((p) => p.assertion.band === "strong").map((p) => p.assertion.id);
  const papers = card({
    title: `Papers to attribute (${number(counts.papers)})`,
    attrs: { "data-entity": "publication" },
    action: strongIds.length > 1 ? h("button", { type: "button", class: "btn btn-sm btn-accept", onclick: decide(strongIds, "accept") },
      icon("check", "icon icon-sm"), `Accept ${strongIds.length} strong`) : null,
    body: items.papers.length ? h("div", { class: "items" }, items.papers.map((p) => h("article", { class: "item" },
      h("div", { class: "item-main" },
        h("a", { class: "item-title", href: `/publications/${p.summary.pmid}` }, p.summary.title ?? "(untitled)"),
        h("div", { class: "item-meta" }, p.summary.journal ? h("span", {}, p.summary.journal) : null,
          p.summary.year ? [h("span", { class: "sep" }), h("span", { class: "tabular" }, p.summary.year)] : null,
          p.assertion.band ? [h("span", { class: "sep" }), pill(p.assertion.band, p.assertion.band === "strong" ? "ok" : "")] : null),
        h("ul", { class: "plain-list compact reasons" }, (p.assertion.evidence ?? []).map((e) => h("li", { class: "subtle" }, e.explanation)))),
      h("div", { class: "item-side" },
        h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => openEvidence(p.assertion.id, { onChange: reload }) }, "Evidence"),
        h("div", { class: "row-sm" },
          h("button", { type: "button", class: "btn btn-sm btn-accept", onclick: decide([p.assertion.id], "accept") }, "The group's"),
          h("button", { type: "button", class: "btn btn-sm btn-reject", onclick: decide([p.assertion.id], "reject") }, "Not"))))))
      : h("div", { class: "card-body" }, h("p", { class: "subtle" }, "Every suggested paper has been decided.")),
  });
  const grants = card({ title: `New grants (${number(counts.grants)})`, attrs: { "data-entity": "grant" },
    body: items.grants.length ? h("div", { class: "items" }, items.grants.map((g) => grantItem(g.summary)))
      : h("div", { class: "card-body" }, h("p", { class: "subtle" }, `No new grants ${since}.`)) });
  const roster = card({ title: `Roster changes (${number(counts.roster)})`, attrs: { "data-entity": "person" }, pad: true,
    body: items.roster.length ? h("ul", { class: "plain-list" }, items.roster.map((r) => h("li", {},
      r.person_id ? h("a", { href: `/people/${r.person_id}` }, r.person ?? r.person_id) : "Someone",
      ` ${ROSTER[r.action] ?? r.action}`, r.role ? h("span", { class: "subtle" }, ` · ${r.role.replace("_", " ")}`) : null,
      h("span", { class: "subtle" }, ` · ${r.actor}, ${relative(r.at)}`))))
      : h("p", { class: "subtle" }, `No roster changes ${since}.`) });
  const extend = (o) => async () => {
    const closes = new Date(`${o.closes_on}T00:00:00`);
    closes.setDate(closes.getDate() + 30);
    try {
      await post(`/api/v1/groups/${group.id}/openings/${o.id}`, { closes_on: closes.toISOString().slice(0, 10) });
    } catch (failure) {
      toast(failure.detail || "That did not work.");
      return;
    }
    toast(`${o.title} stays open 30 more days.`);
    reload();
  };
  const close = (o) => async () => {
    try {
      await post(`/api/v1/groups/${group.id}/openings/${o.id}`, { closed: true });
    } catch (failure) {
      toast(failure.detail || "That did not work.");
      return;
    }
    toast(`Closed ${o.title}.`);
    reload();
  };
  const openings = card({ title: `Openings closing soon (${number(counts.openings)})`, pad: true,
    body: items.openings.length ? h("ul", { class: "plain-list" }, items.openings.map((o) => h("li", { class: "row-sm wrap" },
      h("span", { class: "grow" }, h("strong", {}, o.title), h("span", { class: "subtle" }, ` · closes ${o.closes_on} (${plural(o.days_left, "day")})`)),
      h("button", { type: "button", class: "btn btn-sm", onclick: extend(o) }, "Extend 30 days"),
      h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: close(o) }, "Close now"))))
      : h("p", { class: "subtle" }, "No openings close in the next two weeks.") });
  const done = h("button", { type: "button", class: "btn btn-primary", onclick: async () => {
    try {
      await post(`/api/v1/groups/${group.id}/inbox/reviewed`, {});
    } catch (failure) {
      toast(failure.detail || "That did not work.");
      return;
    }
    toast(`Marked ${group.name} as reviewed.`);
    reload();
  } }, icon("check", "icon icon-sm"), "Mark as reviewed");
  return h("div", { class: "stack-lg" },
    h("div", { class: "row wrap" },
      h("div", { class: "stack-xs grow" }, h("h2", { class: "section-title" }, h("a", { href: `/groups/${group.slug}` }, group.name)),
        h("span", { class: "subtle" }, `New grants and roster changes ${since}. ${Object.entries(counts).filter(([, n]) => n).map(([k, n]) => `${number(n)} ${LABELS[k][n === 1 ? 0 : 1]}`).join(", ") || "Nothing waiting."}`)),
      done),
    counts.papers || counts.grants || counts.roster || counts.openings ? null
      : callout("Nothing is waiting. Mark it as reviewed to start a new week."),
    papers, grants, roster, openings);
}
