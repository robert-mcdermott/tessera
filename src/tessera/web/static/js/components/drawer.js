// Side drawer (an accessible modal dialog) and the evidence drawer built on it.

import { get, post } from "../core/api.js";
import { h, icon, iconButton } from "../core/dom.js";
import { date, relative, scoreLabel } from "../core/format.js";
import { toast } from "./toast.js";
import { entityHref, refKind, statusMark } from "./ui.js";

let open = null;

function focusables(root) {
  return [...root.querySelectorAll('a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter((el) => !el.hidden && el.offsetParent !== null);
}

export function closeDrawer() {
  if (!open) return;
  const { backdrop, panel, returnFocus, onKey } = open;
  document.removeEventListener("keydown", onKey, true);
  backdrop.remove();
  panel.remove();
  open = null;
  returnFocus?.focus?.();
}

/** Open a drawer. render(body, footer, close) fills it; it may be async. */
export function openDrawer({ title, subtitle = null, render, label }) {
  closeDrawer();
  const returnFocus = document.activeElement;
  const heading = h("h2", { id: "drawer-title" }, title);
  const body = h("div", { class: "drawer-body" });
  const footer = h("div", { class: "drawer-footer", hidden: true });
  const panel = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true",
    "aria-labelledby": "drawer-title", "aria-label": label ?? null },
  h("div", { class: "drawer-header" },
    h("div", { class: "grow stack-sm" }, heading, subtitle ? h("div", { class: "muted" }, subtitle) : null),
    iconButton("close", "Close", { onclick: closeDrawer })),
  body, footer);
  const backdrop = h("div", { class: "drawer-backdrop", onclick: closeDrawer });
  const onKey = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeDrawer();
    } else if (event.key === "Tab") {
      const items = focusables(panel);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  };
  document.addEventListener("keydown", onKey, true);
  document.body.append(backdrop, panel);
  open = { backdrop, panel, returnFocus, onKey };
  Promise.resolve(render(body, footer, closeDrawer)).then(() => {
    (focusables(panel)[1] ?? focusables(panel)[0])?.focus();
  });
  return { panel, body, footer };
}

function weightBar(weight) {
  if (weight == null) return h("div");
  // Scale so a typical strong signal (about 4 points) fills the half-track; decisive
  // signals such as an ORCID match simply run to the end.
  const width = `${Math.max(6, Math.min(100, (Math.abs(weight) / 4) * 100))}%`;
  const bar = h("span", { class: `weight-bar ${weight >= 0 ? "pos" : "neg"}` });
  bar.style.setProperty("width", width); // CSSOM, which the content security policy allows
  return h("div", { class: "weight", title: `${weight > 0 ? "+" : ""}${weight}` },
    h("span", { class: "weight-value" }, `${weight > 0 ? "+" : ""}${weight}`), bar);
}

export const ACTION_LABELS = { accept: "Accepted", reject: "Rejected", defer: "Deferred", undo: "Undone" };

/**
 * Show why Tessera believes an assertion, with its history and decision buttons.
 * onChange is called after a decision so the page can refresh.
 */
export function openEvidence(assertionId, { onChange = () => {} } = {}) {
  openDrawer({
    title: "Why Tessera thinks this",
    label: "Evidence",
    render: async (body, footer) => {
      body.append(h("div", { class: "skeleton skeleton-title" }));
      let data;
      try {
        data = (await get(`/api/v1/assertions/${assertionId}`, null, { fresh: true })).data;
      } catch (error) {
        body.replaceChildren(h("p", {}, error.detail || "The evidence could not be loaded."));
        return;
      }
      draw(body, footer, data, onChange);
    },
  });
}

function entityLink(side) {
  const kind = refKind(side.id);
  const href = kind ? entityHref(kind, side.id) : null;
  const label = side.label ?? side.id;
  return href ? h("a", { href, "data-entity": kind }, label) : h("span", {}, label);
}

const PREDICATES = {
  authored: "is an author of", investigator_on: "is an investigator on", official_of: "is an official of",
  acknowledges: "acknowledges", references: "cites", funded_by: "is funded by", member_of: "is a member of",
};

/** The claim, likelihood, evidence, and history of an assertion (drawer and review panel). */
export function assertionDetail(a) {
  const band = a.band;
  const meter = a.score != null ? h("div", { class: "stack-sm" },
    h("div", { class: "row" }, h("strong", {}, `${scoreLabel(a.score)} likely`),
      h("span", { class: "muted" }, band ? `${band} band` : ""), h("span", { class: "spacer subtle" }, a.method)),
    (() => {
      const fill = h("span");
      fill.style.setProperty("width", `${Math.round(a.score * 100)}%`);
      return h("div", { class: "meter", "data-band": band }, fill);
    })()) : h("div", { class: "muted" }, `${a.origin === "source" ? "Stated by the source record" : "Entered by a person"} · ${a.method}`);

  return [
    h("section", { class: "stack-sm" },
      h("div", { class: "row wrap" }, statusMark(a), a.reported === false ? h("span", { class: "pill pill-warn" }, "No longer reported") : null),
      h("p", {}, entityLink(a.subject), ` ${PREDICATES[a.predicate] ?? a.predicate} `, entityLink(a.object),
        a.qualifiers?.author_position ? ` (author ${a.qualifiers.author_position}${a.qualifiers.author_count ? ` of ${a.qualifiers.author_count}` : ""}${a.qualifiers.author_name ? `, listed as “${a.qualifiers.author_name}”` : ""})` : ""),
      meter),
    h("section", { class: "drawer-section" },
      h("h3", {}, "Evidence"),
      h("div", { class: "evidence" }, (a.evidence ?? []).map((item) =>
        h("div", { class: "evidence-item" },
          h("div", {}, h("div", { class: "evidence-text" }, item.explanation),
            h("div", { class: "evidence-feature" }, item.feature)),
          weightBar(item.weight))))),
    h("section", { class: "drawer-section" },
      h("h3", {}, "History"),
      a.history?.length
        ? h("div", { class: "history" }, a.history.map((event) => h("div", { class: "history-item" },
          icon(event.action === "undo" ? "undo" : event.action === "reject" ? "close" : "check", "icon icon-sm"),
          h("span", {}, `${ACTION_LABELS[event.action] ?? event.action} by ${event.actor} · ${relative(event.at)}`,
            event.note ? ` — “${event.note}”` : ""))))
        : h("p", { class: "muted" }, `First seen ${date(a.first_seen_at)}. No decisions yet.`)),
  ];
}

/** Whether "undo" has a decision to reverse (the server's rule: undos pop a stack). */
export function canUndo(a) {
  return (a.history ?? []).reduce((depth, e) => (e.action === "undo" ? Math.max(0, depth - 1) : depth + 1), 0) > 0;
}

function draw(body, footer, a, onChange) {
  body.replaceChildren(...assertionDetail(a));

  if ((a.predicate === "authored" || a.predicate === "official_of") && a.can_decide === false) {
    footer.replaceChildren(h("p", { class: "subtle" }, "Only a curator for this person, or the person themselves, can decide this link."));
    footer.hidden = false;
  } else if (a.predicate === "authored" || a.predicate === "official_of") {
    const note = h("textarea", { class: "input", rows: 2, placeholder: "Optional note", "aria-label": "Note" });
    const act = async (action) => {
      try {
        const updated = (await post(`/api/v1/assertions/${a.id}/decision`, { action, note: note.value || null })).data;
        draw(body, footer, updated, onChange);
        onChange(updated);
        toast(`${ACTION_LABELS[action]}.`, action === "undo" ? {} : {
          action: { label: "Undo", run: async () => {
            const back = (await post(`/api/v1/assertions/${a.id}/decision`, { action: "undo" })).data;
            draw(body, footer, back, onChange);
            onChange(back);
          } },
        });
      } catch (error) {
        toast(error.detail || "That did not work.");
      }
    };
    const buttons = [];
    if (a.status !== "accepted") buttons.push(h("button", { type: "button", class: "btn btn-accept", onclick: () => act("accept") }, icon("check", "icon icon-sm"), "Accept"));
    if (a.status !== "rejected") buttons.push(h("button", { type: "button", class: "btn btn-reject", onclick: () => act("reject") }, icon("close", "icon icon-sm"), "Reject"));
    if (a.status === "suggested") buttons.push(h("button", { type: "button", class: "btn btn-ghost", onclick: () => act("defer") }, "Defer"));
    if (canUndo(a)) buttons.push(h("button", { type: "button", class: "btn btn-ghost", onclick: () => act("undo") }, icon("undo", "icon icon-sm"), "Undo last"));
    footer.replaceChildren(note, h("div", { class: "row wrap" }, buttons));
    footer.hidden = false;
  } else {
    footer.hidden = true;
  }
}

/**
 * Ask before a change that touches many records. run() is awaited after the drawer closes;
 * text may be a string or nodes.
 */
export function confirmAction({ title, text, confirmLabel, tone = "primary", run }) {
  openDrawer({
    title,
    label: title,
    render: (body, footer, close) => {
      body.append(h("div", { class: "stack" }, text));
      footer.replaceChildren(h("div", { class: "row wrap" },
        h("button", { type: "button", class: `btn btn-${tone}`, onclick: async () => {
          close();
          await run();
        } }, confirmLabel),
        h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel")));
      footer.hidden = false;
    },
  });
}

/** A list of keyboard shortcuts, as a drawer. keys: [[keys, what]]. */
export function showShortcuts(title, keys) {
  openDrawer({
    title,
    label: title,
    render: (body) => {
      body.append(h("table", { class: "table shortcuts" }, h("tbody", {}, keys.map(([combo, what]) =>
        h("tr", {}, h("td", {}, combo.split(" / ").map((k, i) => [i ? " or " : null, h("kbd", {}, k)])), h("td", {}, what))))));
    },
  });
}
