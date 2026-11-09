// A profile's About tab: the sections a researcher keeps, beside the positions the group
// directory holds. Everyone reads it; those who keep the profile edit each section in place.
// Loaded when the tab is first opened.

import { get, invalidate, post, request } from "../core/api.js";
import { h, icon, iconButton } from "../core/dom.js";
import { paragraphs, relative } from "../core/format.js";
import { closeDrawer, confirmAction, openDrawer } from "./drawer.js";
import { toast } from "./toast.js";
import { card, emptyState, pill } from "./ui.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ONGOING = new Set(["position", "membership", "teaching"]); // no end date means "present"

/** "2019", "Sep 2019", or "12 Sep 2019". */
export function when(value) {
  if (!value) return "";
  const [y, m, d] = value.split("-");
  if (!m) return y;
  return `${d ? `${Number(d)} ` : ""}${MONTHS[Number(m) - 1]} ${y}`;
}

function span(f, section) {
  const start = when(f.start);
  const end = f.end ? when(f.end) : ONGOING.has(section) ? "present" : "";
  if (!start) return end;
  return end ? `${start} – ${end}` : `${start} –`;
}

const join = (...parts) => parts.filter(Boolean).join(" · ");

/** What one entry says, as its title line and meta line. */
function describe(section, f) {
  switch (section) {
    case "position": return [f.title, join(f.department, f.organization, f.location), span(f, section)];
    case "education": return [join(f.degree, f.field), join(f.institution, f.location), span(f, section)];
    case "honor": return [f.title, f.organization, when(f.year)];
    case "membership": return [f.role, f.organization, span(f, section)];
    case "teaching": return [f.title, join(f.role, f.organization), span(f, section)];
    case "talk": return [f.title, join(f.event, f.location), when(f.date)];
    default: return [f.title ?? f.label ?? "", "", ""];
  }
}

// --- the editor --------------------------------------------------------------------------

function input(field, value) {
  const attrs = { id: `pf-${field.key}`, name: field.key, class: "input", maxlength: field.limit,
    required: field.required, "aria-describedby": field.hint ? `pf-${field.key}-hint` : null };
  if (field.kind === "long") return h("textarea", { ...attrs, rows: field.limit > 2000 ? 10 : 5 }, value ?? "");
  if (field.kind === "choice") {
    return h("select", { ...attrs, class: "input" }, h("option", { value: "" }, "—"),
      field.choices.map((c) => h("option", { value: c.value, selected: c.value === value }, c.label)));
  }
  return h("input", { ...attrs, type: field.kind === "url" ? "url" : "text", value: value ?? "",
    inputmode: field.kind === "date" ? "numeric" : null, placeholder: field.kind === "date" ? "YYYY or YYYY-MM" : null });
}

async function paperChoice(personId, value) {
  const select = h("select", { id: "pf-pmid", name: "pmid", class: "input", required: true },
    h("option", { value: "" }, "Loading their confirmed papers…"));
  get(`/api/v1/people/${encodeURIComponent(personId)}/publications`, { status: ["accepted"], sort: "year", limit: 200 })
    .then(({ data }) => {
      select.replaceChildren(h("option", { value: "" }, "Choose one of their confirmed papers"),
        data.map((p) => {
          const s = p.summary ?? p;
          const pmid = String(s.pmid ?? p.pmid);
          return h("option", { value: pmid, selected: pmid === value }, `${s.year ?? ""} · ${s.title ?? pmid}`);
        }));
    }).catch(() => select.replaceChildren(h("option", { value: "" }, "Could not load the papers")));
  return select;
}

/** Add an entry to a section, or change one, in a drawer built from the section's fields. */
export function editEntry({ personId, section, entry = null, done }) {
  openDrawer({
    title: `${entry ? "Change" : "Add"} ${section.noun}`,
    subtitle: section.label,
    render: async (body, footer, close) => {
      const problem = h("p", { class: "field-error", role: "alert", hidden: true });
      const controls = await Promise.all(section.fields.map(async (field) => {
        const value = entry?.fields?.[field.key];
        const control = field.kind === "pmid" ? await paperChoice(personId, value) : input(field, value);
        return h("div", { class: "field" },
          h("label", { for: `pf-${field.key}` }, field.label, field.required ? h("span", { class: "required", "aria-hidden": "true" }, " *") : null),
          control,
          field.hint ? h("span", { class: "subtle", id: `pf-${field.key}-hint` }, field.hint) : null);
      }));
      const form = h("form", { class: "stack", id: "profile-entry-form", onsubmit: async (event) => {
        event.preventDefault();
        const fields = {};
        for (const field of section.fields) {
          const value = form.elements[field.key].value.trim();
          if (value) fields[field.key] = value;
        }
        try {
          if (entry) await post(`/api/v1/people/${encodeURIComponent(personId)}/profile/entries/${entry.id}`, { fields });
          else await post(`/api/v1/people/${encodeURIComponent(personId)}/profile/entries`, { section: section.id, fields });
        } catch (failure) {
          problem.textContent = failure.detail || failure.message;
          problem.hidden = false;
          return;
        }
        close();
        toast(entry ? "Changed." : "Added.");
        done();
      } }, problem, controls);
      body.append(form);
      footer.replaceChildren(h("div", { class: "row wrap" },
        h("button", { type: "submit", class: "btn btn-primary", form: "profile-entry-form" }, entry ? "Save" : "Add"),
        h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel")));
      footer.hidden = false;
      form.querySelector("input, textarea, select")?.focus();
    },
  });
}

// --- the sections ---------------------------------------------------------------------------

function entryActions({ personId, section, entries, entry, index, done }) {
  const move = (step) => async () => {
    const ids = entries.map((e) => e.id);
    [ids[index], ids[index + step]] = [ids[index + step], ids[index]];
    try {
      await post(`/api/v1/people/${encodeURIComponent(personId)}/profile/order`, { section: section.id, ids });
      done();
    } catch (failure) {
      toast(failure.detail || failure.message);
    }
  };
  const [title] = describe(section.id, entry.fields);
  const name = title || section.noun;
  return h("div", { class: "profile-actions" },
    iconButton("edit", `Change ${name}`, { onclick: () => editEntry({ personId, section, entry, done }) }),
    entries.length > 1 ? iconButton("chevronUp", `Move ${name} up`, { disabled: index === 0, onclick: move(-1) }) : null,
    entries.length > 1 ? iconButton("chevronDown", `Move ${name} down`, { disabled: index === entries.length - 1, onclick: move(1) }) : null,
    iconButton("trash", `Remove ${name}`, { onclick: () => confirmAction({
      title: `Remove ${section.noun}?`, text: h("p", {}, `“${name}” will be removed from this profile. The history keeps what it said.`),
      confirmLabel: "Remove", tone: "reject", run: async () => {
        try {
          await request("DELETE", `/api/v1/people/${encodeURIComponent(personId)}/profile/entries/${entry.id}`);
          invalidate();
          toast("Removed.");
          done();
        } catch (failure) {
          toast(failure.detail || failure.message);
        }
      } }) }));
}

function entryRow(ctx, entry, index) {
  const { section, keeps } = ctx;
  const f = entry.fields;
  let main;
  if (section.id === "link") {
    main = h("a", { href: f.url, target: "_blank", rel: "noopener noreferrer", class: "row-sm" }, icon("external", "icon icon-sm"), f.label);
  } else if (section.id === "featured") {
    const paper = entry.paper ?? {};
    main = h("div", { class: "stack-xs" },
      h("a", { class: "strong", href: `/publications/${encodeURIComponent(f.pmid)}` }, paper.title ?? `PubMed ${f.pmid}`),
      h("span", { class: "subtle" }, join(paper.journal, paper.year, f.note)),
      entry.confirmed === false ? pill("No longer confirmed: hidden from readers", "warn") : null);
  } else {
    const [title, meta, dates] = describe(section.id, f);
    main = h("div", { class: "stack-xs" },
      h("div", { class: "row-sm wrap" }, h("span", { class: "strong" }, title), f.kind && section.id !== "teaching" ? pill(f.kind, "") : null),
      meta ? h("span", { class: "subtle" }, meta) : null,
      dates ? h("span", { class: "subtle profile-dates" }, dates) : null,
      f.description ? h("p", { class: "profile-note" }, f.description) : null);
  }
  return h("li", { class: "profile-entry" }, h("div", { class: "grow" }, main), keeps ? entryActions({ ...ctx, entry, index }) : null);
}

function prose(text) {
  return h("div", { class: "prose" }, paragraphs(text).map((p) => h("p", {}, p)));
}

function sectionCard(ctx) {
  const { section, entries, keeps, personId, done, extra = [] } = ctx;
  const add = keeps && !(section.single && entries.length) && entries.length < section.most
    ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => editEntry({ personId, section, done }) },
      icon("plus", "icon icon-sm"), "Add") : null;
  if (section.single) {
    const entry = entries[0];
    return card({ title: section.label, pad: true, attrs: { "data-profile-section": section.id },
      action: keeps && entry ? entryActions({ ...ctx, entry, index: 0 }) : add,
      body: prose(entry.fields.text) });
  }
  return card({ title: section.label, pad: true, attrs: { "data-profile-section": section.id }, action: add,
    body: h("ul", { class: "plain-list profile-entries" }, extra, entries.map((e, i) => entryRow(ctx, e, i))) });
}

/** The group directory's positions for this person, shown first among positions. */
function directoryPositions(person, organization) {
  return person.units.filter((u) => u.title || u.role_label).map((u) => h("li", { class: "profile-entry" },
    h("div", { class: "stack-xs grow" },
      h("div", { class: "row-sm wrap" }, h("span", { class: "strong" }, u.title || u.role_label),
        u.current ? null : pill("Past", "")),
      h("span", { class: "subtle" }, join(u.title && u.role_label ? u.role_label : null,
        h("a", { href: `/groups/${u.group_slug}` }, u.unit).textContent, organization)),
      h("span", { class: "subtle profile-dates" }, span({ start: u.start, end: u.end }, u.current ? "position" : "")),
      h("span", { class: "subtle profile-source" }, "From the group directory"))));
}

/** The About tab. data: GET /people/{id}/profile; person: GET /people/{id}. */
export function aboutPanel({ person, data, organization, refresh }) {
  const keeps = data.may_edit;
  const personId = data.person_id;
  const done = () => refresh();
  const cards = [];
  const empty = [];
  for (const section of data.sections) {
    const entries = section.entries;
    const extra = section.id === "position" ? directoryPositions(person, organization) : [];
    if (!entries.length && !extra.length) {
      if (keeps) empty.push(section);
      continue;
    }
    cards.push(sectionCard({ section, entries, keeps, personId, done, extra }));
  }
  if (!cards.length && !keeps) {
    return card({ body: emptyState({ iconName: "person", title: "Nothing here yet",
      text: "Positions, education, honors, and the rest of this profile appear here once they are added." }) });
  }
  const addMore = keeps && empty.length ? card({ title: "Add to this profile", pad: true, attrs: { "data-profile-section": "add" },
    body: h("div", { class: "stack-sm" },
      h("p", { class: "subtle" }, "Sections without entries are hidden from readers until something is added."),
      h("div", { class: "chips" }, empty.map((section) => h("button", { type: "button", class: "btn btn-sm",
        onclick: () => editEntry({ personId, section, done }) }, icon("plus", "icon icon-sm"), section.label)))) }) : null;
  const note = keeps ? h("p", { class: "subtle profile-keeper" }, icon("edit", "icon icon-sm"),
    data.capacity === "self" ? "This is your profile. Changes show at once; the History tab lists every change."
      : "You may keep this profile. Changes show at once; the History tab lists every change.") : null;
  return h("div", { class: "stack-lg profile-about" }, note, cards, addMore);
}

const ACTIONS = { added: "added", changed: "changed", removed: "removed", reordered: "reordered" };

/** Profile changes, for the History tab. */
export function profileHistory(events) {
  if (!events.length) return null;
  return card({ title: "Profile changes", body: h("div", { class: "items" }, events.map((e) => {
    const title = e.after?.title ?? e.before?.title ?? e.after?.role ?? e.before?.role ?? e.after?.label ?? e.before?.label
      ?? e.after?.institution ?? e.before?.institution ?? null;
    return h("div", { class: "item" },
      h("span", { class: "entity-icon", "data-entity": "person" }, icon(e.action === "removed" ? "trash" : e.action === "added" ? "plus" : "edit")),
      h("div", { class: "item-main" },
        h("div", {}, `${e.section_label}: ${ACTIONS[e.action] ?? e.action}`, title ? h("span", { class: "muted" }, ` — ${title}`) : null),
        h("div", { class: "item-meta" }, h("span", {}, `${e.actor} (${e.capacity_label})`), h("span", { class: "sep" }), h("span", { title: e.at }, relative(e.at)))));
  })) });
}
