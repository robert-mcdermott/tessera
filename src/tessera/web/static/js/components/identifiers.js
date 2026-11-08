// A person's identifiers on their profile: ORCID iDs with what a curator needs to judge them
// (how many of the person's papers carry the iD, what the public ORCID record says, who else
// holds it), and buttons to confirm, reject, restore, remove, or add one.

import { post } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { number, plural } from "../core/format.js";
import { openDrawer } from "./drawer.js";
import { toast } from "./toast.js";
import { card, pill } from "./ui.js";

const SCHEMES = { orcid: "ORCID iD", nih_profile: "NIH profile", email: "Email", local: "Local ID" };
const SOURCES = {
  roster: "from the roster", reporter: "from NIH RePORTER", pubmed: "from PubMed author records",
  papers: "found on their papers", curator: "added by a curator",
};
const RECORD_STATUS = {
  not_found: "ORCID has no record for this iD", deprecated: "ORCID says this iD was replaced by another",
  locked: "The ORCID record is locked", deactivated: "The ORCID record was deactivated",
  unavailable: "The ORCID record could not be read",
};
const ORDER = { suggested: 0, accepted: 1, rejected: 2 };

/** The Identifiers card. */
export function identifiersCard(profile, refresh) {
  const items = [...profile.identifiers].sort((a, b) =>
    (ORDER[a.status] ?? 3) - (ORDER[b.status] ?? 3) || (b.papers ?? 0) - (a.papers ?? 0)
    || a.scheme.localeCompare(b.scheme));
  const add = h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => addIdentifier(profile, refresh) },
    "Add");
  const confirmed = items.some((i) => i.scheme === "orcid" && i.status === "accepted");
  return card({
    title: "Identifiers",
    action: add,
    pad: true,
    body: h("div", { class: "stack-sm" },
      items.length
        ? h("ul", { class: "identifiers" }, items.map((item) => identifierRow(profile, item, refresh)))
        : h("p", { class: "muted" }, "No identifiers yet."),
      items.some((i) => i.scheme === "orcid") ? h("p", { class: "subtle" }, confirmed
        ? "Confirmed ORCID iDs count as evidence, and their public records are read, from the next sync."
        : "Confirm an ORCID iD to use it as evidence from the next sync.") : null),
  });
}

function identifierRow(profile, item, refresh) {
  const value = item.scheme === "orcid"
    ? h("a", { class: "row-xs mono", href: item.url, target: "_blank", rel: "noopener noreferrer" },
      icon("orcid", "icon icon-sm"), item.value)
    : h("span", { class: item.scheme === "email" ? "" : "mono" }, item.value);
  const status = item.status === "suggested" ? pill("Suggested", "warn")
    : item.status === "rejected" ? pill("Not theirs", "") : null;

  const facts = [];
  if (item.scheme === "orcid" && item.papers) {
    facts.push(`On ${number(item.papers)} of their papers`);
  }
  if (item.record) {
    facts.push(item.record.status === "public"
      ? `ORCID record: ${item.record.name ?? "no public name"}, ${plural(item.record.works, "work")}`
      : RECORD_STATUS[item.record.status] ?? item.record.status);
  }
  if (!(item.source === "papers" && item.papers)) facts.push(SOURCES[item.source] ?? item.source);

  const act = (action, label, cls = "btn btn-sm") =>
    h("button", { type: "button", class: cls, onclick: (event) => decide(profile, item, action, refresh, event.currentTarget) }, label);
  // Identifiers that come from the sources themselves (an NIH profile from RePORTER) would
  // only come back at the next sync, so they offer no action.
  const fromSource = item.scheme !== "orcid" && (item.source === "reporter" || item.source === "pubmed");
  const actions = item.status === "suggested" ? [act("accept", "Confirm", "btn btn-sm btn-accept"), act("reject", "Not theirs", "btn btn-sm btn-ghost")]
    : item.status === "rejected" ? [act("accept", "Restore", "btn btn-sm btn-ghost")]
    : fromSource ? []
    : [act(item.scheme === "orcid" ? "reject" : "remove", item.scheme === "orcid" ? "Not theirs" : "Remove", "btn btn-sm btn-ghost")];

  return h("li", { class: "identifier", "data-status": item.status },
    h("div", { class: "identifier-head" },
      h("span", { class: "identifier-scheme" }, SCHEMES[item.scheme] ?? item.scheme), status),
    h("div", { class: "identifier-value" }, value),
    h("div", { class: "item-meta" }, facts.map((fact, i) => [i ? h("span", { class: "sep" }) : null, h("span", {}, fact)])),
    item.also.length ? h("div", { class: "callout callout-warn", role: "note" }, icon("warning", "icon icon-sm"),
      h("div", {}, "Also recorded for ", item.also.map((o, i) => [i ? ", " : null, h("a", { href: `/people/${o.id}` }, o.name)]),
        ". One of the records may be a duplicate.")) : null,
    actions.length ? h("div", { class: "row-sm" }, actions) : null);
}

const DONE = {
  accept: (v) => `Confirmed ${v}.`, reject: (v) => `Marked ${v} as not theirs.`,
  remove: (v) => `Removed ${v}.`, add: (v) => `Added ${v}.`,
};

async function decide(profile, item, action, refresh, button) {
  button.disabled = true;
  try {
    await post(`/api/v1/people/${encodeURIComponent(profile.id)}/identifiers`,
      { scheme: item.scheme, value: item.value, action });
  } catch (error) {
    button.disabled = false;
    toast(error.detail || "That did not work.");
    return;
  }
  refresh();
  toast(DONE[action](item.value));
}

/** A form to add an identifier by hand. */
function addIdentifier(profile, refresh) {
  openDrawer({
    title: "Add an identifier",
    subtitle: profile.name,
    label: "Add an identifier",
    render: (body, footer, close) => {
      const scheme = h("select", { class: "input", id: "id-scheme" },
        Object.entries(SCHEMES).filter(([k]) => k !== "nih_profile").map(([k, label]) => h("option", { value: k }, label)));
      const value = h("input", { class: "input mono", id: "id-value", autocomplete: "off", required: true, placeholder: "0000-0002-1825-0097" });
      scheme.addEventListener("change", () => {
        value.placeholder = { orcid: "0000-0002-1825-0097", email: "name@example.edu", local: "Employee or roster ID" }[scheme.value];
      });
      const note = h("textarea", { class: "input", id: "id-note", rows: 2, placeholder: "How you know (optional)" });
      const error = h("p", { class: "field-error", role: "alert", hidden: true });
      const submit = h("button", { type: "submit", class: "btn btn-primary" }, "Add identifier");
      const form = h("form", { class: "stack", novalidate: true, onsubmit: async (event) => {
        event.preventDefault();
        const text = value.value.trim().replace(/^https?:\/\/orcid\.org\//i, "");
        if (!text) {
          error.textContent = "Enter a value.";
          error.hidden = false;
          value.focus();
          return;
        }
        submit.disabled = true;
        try {
          await post(`/api/v1/people/${encodeURIComponent(profile.id)}/identifiers`,
            { scheme: scheme.value, value: text, action: "add", note: note.value.trim() || null });
        } catch (failure) {
          error.textContent = failure.detail || "The identifier could not be added.";
          error.hidden = false;
          submit.disabled = false;
          value.focus();
          return;
        }
        close();
        refresh();
        toast(DONE.add(text));
      } },
      h("p", { class: "muted" }, "An identifier you add is confirmed. A confirmed ORCID iD is decisive evidence, so add only iDs you have checked."),
      h("div", { class: "field" }, h("label", { for: "id-scheme" }, "Type"), scheme),
      h("div", { class: "field" }, h("label", { for: "id-value" }, "Value"), value),
      h("div", { class: "field" }, h("label", { for: "id-note" }, "Note"), note),
      error,
      h("div", { class: "row-sm" }, submit, h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel")));
      body.append(form);
    },
  });
}
