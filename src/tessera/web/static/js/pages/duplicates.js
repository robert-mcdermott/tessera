// Possible duplicates: two records that may be the same person, side by side with the evidence.
// A curator merges them (either record can be the one kept), says they are different people,
// or leaves the pair for later. Every merge can be undone.

import { get, post } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { enumLabel, number, percent, plural, relative } from "../core/format.js";
import { query, setQuery } from "../core/router.js";
import { toast } from "../components/toast.js";
import { avatar, card, emptyState, pageHeader, pager, pill, segmented } from "../components/ui.js";
import { show } from "./page.js";

const PAGE = 20;
const STATUSES = [
  { id: "suggested", label: "Awaiting a decision" },
  { id: "deferred", label: "Later" },
  { id: "rejected", label: "Different people" },
  { id: "accepted", label: "Merged" },
];
const SCHEMES = { orcid: "ORCID", email: "Email", nih_profile: "NIH profile", local: "Local ID" };

export function duplicates(main, ctx = {}) {
  const params = query();
  const status = STATUSES.some((s) => s.id === params.get("status")) ? params.get("status") : "suggested";
  const offset = Number(params.get("offset")) || 0;
  const reload = () => duplicates(main, { samePage: true });

  return show(main, {
    title: "Possible duplicates",
    quiet: ctx.samePage,
    load: async () => get("/api/v1/duplicates", { status, limit: PAGE, offset }, { fresh: true }),
    view: (body) => {
      const total = body.meta.total;
      const pairs = body.data.map((item) => pairCard(item, reload));
      return h("div", { class: "stack-lg" },
        pageHeader({
          eyebrow: "Curate",
          title: "Possible duplicates",
          meta: h("span", {}, `${plural(total, "pair")} · two records that may be one person. A merge moves everything to the record you keep, and can be undone.`),
        }),
        h("div", { class: "toolbar" },
          segmented(STATUSES, status, (id) => setQuery({ status: id === "suggested" ? null : id, offset: null }), "Decision")),
        pairs.length ? h("div", { class: "stack" }, pairs)
          : card({ body: emptyState({ iconName: "people", title: status === "suggested" ? "No duplicates waiting" : "Nothing here",
            text: status === "suggested" ? "Tessera looks for duplicates at every sync: records that share an identifier, or claim the same author positions." : null }) }),
        pager({ total, offset, limit: PAGE, onPage: (next) => setQuery({ offset: next || null }) }));
    },
  });
}

function pairCard(item, reload) {
  const { merge: fold, into: keep } = item;
  if (!fold || !keep) return null;
  const open = item.status === "suggested" || item.status === "deferred";
  const likelihood = item.score == null ? null
    : h("span", { class: `band-score`, "data-band": item.band }, percent(item.score));

  const sameName = fold.name === keep.name; // then say which side, not which name
  const actions = open ? [
    h("button", { type: "button", class: "btn btn-sm btn-accept", onclick: (e) => mergeInto(fold, keep, e.currentTarget, reload) },
      icon("check", "icon icon-sm"), sameName ? "Merge into the record on the right" : `Merge into ${keep.name}`),
    h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: (e) => mergeInto(keep, fold, e.currentTarget, reload) },
      sameName ? "Keep the left record instead" : `Keep ${fold.name} instead`),
    h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: (e) => decide(item, "reject", e.currentTarget, reload) }, "Different people"),
    item.status === "suggested"
      ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: (e) => decide(item, "defer", e.currentTarget, reload) }, "Later")
      : null,
  ] : item.status === "rejected" ? [
    h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: (e) => decide(item, "undo", e.currentTarget, reload) },
      icon("undo", "icon icon-sm"), "Decide again"),
  ] : [h("a", { class: "btn btn-sm btn-ghost", href: `/people/${keep.id}` }, `Open ${keep.name}`)];

  const reasons = item.evidence.filter((f) => f.feature !== "prior");
  return h("section", { class: "card duplicate", "aria-label": `${fold.name} and ${keep.name}` },
    h("div", { class: "card-header" },
      h("h2", {}, item.status === "accepted" ? "Merged" : "Same person?"), likelihood,
      item.decided_by ? h("span", { class: "subtle" }, `${item.status === "rejected" ? "Different people" : "Decided"} by ${item.decided_by} ${relative(item.decided_at)}`) : null),
    h("div", { class: "card-body stack" },
      h("div", { class: "duplicate-pair" },
        record(fold, item.status === "accepted" ? "Folded in" : "Merge this record"),
        h("span", { class: "duplicate-arrow", "aria-hidden": "true" }, icon("arrowRight")),
        record(keep, item.status === "accepted" ? "Kept" : "Into this one")),
      reasons.length ? h("ul", { class: "reasons", "aria-label": "Evidence" }, reasons.map((f) =>
        h("li", {},
          h("span", { class: `reason-weight ${f.weight > 0 ? "pos" : "neg"}` },
            `${f.weight > 0 ? "+" : "−"}${Math.abs(f.weight).toFixed(1)}`),
          h("span", {}, f.explanation)))) : null,
      item.note ? h("p", { class: "subtle" }, `“${item.note}”`) : null,
      h("div", { class: "row wrap" }, actions)));
}

function record(p, label) {
  const facts = [];
  if (p.papers.accepted || p.papers.strong) {
    facts.push(`${number(p.papers.accepted)} confirmed, ${number(p.papers.strong)} strongly suggested ${p.papers.accepted + p.papers.strong === 1 ? "paper" : "papers"}`);
  }
  if (p.grants) facts.push(plural(p.grants, "grant"));
  if (p.units.length) facts.push(p.units.join(", "));
  return h("div", { class: "duplicate-record" },
    h("div", { class: "eyebrow" }, label),
    h("div", { class: "row-sm" }, avatar(p.name, "sm"),
      h("div", { class: "stack-xs" },
        h("a", { class: "strong", href: `/people/${p.id}` }, p.name),
        h("span", { class: "row-xs" }, pill(enumLabel(p.status), p.status === "active" ? "ok" : ""),
          h("span", { class: "subtle" }, `added ${relative(p.created_at)}`)))),
    facts.length ? h("ul", { class: "plain-list compact" }, facts.map((f) => h("li", {}, f))) : h("p", { class: "muted" }, "No papers or grants yet."),
    p.identifiers.length ? h("div", { class: "chips" }, p.identifiers.map((i) =>
      h("span", { class: "chip chip-plain", title: i.status === "suggested" ? "Suggested, not yet confirmed" : null },
        `${SCHEMES[i.scheme] ?? i.scheme} ${i.value}${i.status === "suggested" ? " ?" : ""}`))) : null);
}

async function mergeInto(fold, keep, button, reload) {
  button.disabled = true;
  let result;
  try {
    result = (await post("/api/v1/merges", { merge: fold.id, into: keep.id })).data;
  } catch (error) {
    button.disabled = false;
    toast(error.detail || "The records could not be merged.");
    return;
  }
  reload();
  const conflicts = result.conflicts.length
    ? ` ${plural(result.conflicts.length, "link was", "links were")} decided differently on the two records; the more recent decision holds.` : "";
  const what = fold.name === keep.name ? `the two records of ${keep.name}` : `${fold.name} into ${keep.name}`;
  toast(`Merged ${what}.${conflicts}`, {
    action: { label: "Undo", run: async () => {
      await post(`/api/v1/merges/${result.merge_id}/undo`, {});
      reload();
    } },
  });
}

async function decide(item, action, button, reload) {
  button.disabled = true;
  try {
    await post(`/api/v1/assertions/${item.id}/decision`, { action });
  } catch (error) {
    button.disabled = false;
    toast(error.detail || "That did not work.");
    return;
  }
  reload();
  const words = { reject: "Marked as different people.", defer: "Left for later.", undo: "Back to awaiting a decision." };
  toast(words[action], action === "undo" ? {} : {
    action: { label: "Undo", run: async () => {
      await post(`/api/v1/assertions/${item.id}/decision`, { action: "undo" });
      reload();
    } },
  });
}
