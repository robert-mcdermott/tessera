// The decision log: every curation decision about links and people, newest first.

import { get } from "../core/api.js";
import { h } from "../core/dom.js";
import { date, relative } from "../core/format.js";
import { query, setQuery } from "../core/router.js";
import { ACTION_LABELS, openEvidence } from "../components/drawer.js";
import { card, emptyState, entityHref, pageHeader, pager, refKind } from "../components/ui.js";
import { show } from "./page.js";

const PAGE = 50;
const PERSON_ACTIONS = { promote: "Promoted", dismiss: "Dismissed", undo: "Undone", merge: "Merged", unmerge: "Unmerged" };
const IDENTIFIER_ACTIONS = { add: "Added an identifier", accept: "Confirmed an identifier",
  reject: "Marked an identifier as not theirs", remove: "Removed an identifier" };
const SCHEMES = { orcid: "ORCID iD", email: "email", nih_profile: "NIH profile", local: "local ID" };
const STATUS_WORDS = { accepted: "confirmed", rejected: "rejected", deferred: "deferred", suggested: "suggested",
  active: "active member", candidate: "awaiting a decision", dismissed: "dismissed", former: "former member",
  merged: "merged" };
const PREDICATES = { authored: "authored", investigator_on: "investigator on", official_of: "official of" };

export function decisions(main, ctx = {}) {
  const offset = Number(query().get("offset")) || 0;
  const reload = () => decisions(main, { samePage: true });
  return show(main, {
    title: "Decision log",
    quiet: ctx.samePage,
    load: async () => get("/api/v1/decisions", { limit: PAGE, offset }, { fresh: true }),
    view: (body) => {
      const rows = body.data.map((e) => {
        const kind = refKind(e.subject.id);
        const subject = h("a", { href: e.kind === "person" ? `/people/${e.subject.id}` : entityHref(kind, e.subject.id) }, e.subject.label ?? e.subject.id);
        const objectKind = e.object ? refKind(e.object.id) : null;
        const what = e.kind === "link"
          ? [subject, ` ${PREDICATES[e.predicate] ?? e.predicate} `,
            objectKind ? h("a", { href: entityHref(objectKind, e.object.id) }, e.object.label ?? e.object.id) : (e.object.label ?? e.object.id)]
          : e.identifier ? [subject, ` · ${SCHEMES[e.identifier.scheme] ?? e.identifier.scheme} `,
            h("span", { class: "mono" }, e.identifier.value)]
          : e.merge ? [subject, e.action === "merge" ? " into " : " from ",
            h("a", { href: `/people/${e.merge.survivor.id}` }, e.merge.survivor.label ?? e.merge.survivor.id)]
          : [subject];
        const action = e.identifier ? IDENTIFIER_ACTIONS[e.identifier.action] ?? e.identifier.action
          : (e.kind === "person" ? PERSON_ACTIONS : ACTION_LABELS)[e.action] ?? e.action;
        const word = (value) => STATUS_WORDS[value] ?? value ?? (e.identifier ? "not recorded" : "–");
        return h("tr", {},
          h("td", { class: "nowrap muted", title: date(e.at) }, relative(e.at)),
          h("td", {}, h("strong", {}, action),
            h("div", { class: "subtle" }, `${word(e.from_status)} → ${word(e.to_status)}`)),
          h("td", {}, h("div", {}, what), e.note ? h("div", { class: "subtle" }, `“${e.note}”`) : null),
          h("td", { class: "muted" }, e.actor),
          h("td", { class: "actions" }, e.kind === "link"
            ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => openEvidence(e.assertion_id, { onChange: reload }) }, "Evidence")
            : null));
      });
      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: "Curate", title: "Decision log",
          meta: h("span", {}, "Every decision is kept. Open a link's evidence to see its full history or undo it.") }),
        card({ body: rows.length
          ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
            h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Decision"), h("th", {}, "About"), h("th", {}, "By"), h("th", { class: "actions" }, h("span", { class: "sr-only" }, "Actions")))),
            h("tbody", {}, rows)))
          : emptyState({ iconName: "history", title: "No decisions yet", text: "Decisions from the review queue, profiles, and discovered people appear here." }) }),
        pager({ total: body.meta.total, offset, limit: PAGE, onPage: (next) => setQuery({ offset: next || null }) }));
    },
  });
}
