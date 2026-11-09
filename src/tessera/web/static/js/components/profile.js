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
  const visibility = async () => {
    try {
      await post(`/api/v1/people/${encodeURIComponent(personId)}/profile/entries/${entry.id}/public`, { hidden: !entry.hidden });
      done();
    } catch (failure) {
      toast(failure.detail || failure.message);
    }
  };
  return h("div", { class: "profile-actions" },
    iconButton(entry.hidden ? "eyeOff" : "eye", entry.hidden ? `Show ${name} on the public page` : `Leave ${name} off the public page`,
      { onclick: visibility, "aria-pressed": String(Boolean(entry.hidden)) }),
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
  const offPublic = keeps && entry.hidden ? pill("Not on the public page", "") : null;
  return h("li", { class: "profile-entry" }, h("div", { class: "grow stack-xs" }, main, offPublic), keeps ? entryActions({ ...ctx, entry, index }) : null);
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
  const NOTES = {
    self: "This is your profile. Changes show at once; the History tab lists every change.",
    delegate: "You keep this profile for them. Changes show at once, recorded as made on their behalf.",
  };
  const helpers = data.delegates.map((d) => d.name);
  const note = keeps ? h("p", { class: "subtle profile-keeper" }, icon("edit", "icon icon-sm"),
    NOTES[data.capacity] ?? "You may keep this profile. Changes show at once; the History tab lists every change.")
    : helpers.length ? h("p", { class: "subtle profile-keeper" }, icon("person", "icon icon-sm"), `Kept with help from ${helpers.join(", ")}.`) : null;
  const keepers = data.may_name && data.accounts ? keepersCard(data, personId, done) : null;
  const publicCard = keeps ? publicChoices(data, personId, done) : null;
  const documents = keeps ? h("div", {}) : null;
  if (documents) import("../pages/cv.js").then((m) => documents.replaceChildren(m.documentsCard(personId))).catch(() => {});
  return h("div", { class: "stack-lg profile-about" }, note, publicCard, documents, cards, addMore, keepers);
}

/** Whether the profile is public, which sections the public page shows, and a preview. */
function publicChoices(data, personId, done) {
  const base = `/api/v1/people/${encodeURIComponent(personId)}/profile/public`;
  const save = async (body) => {
    try {
      await post(base, body);
      done();
    } catch (failure) {
      toast(failure.detail || failure.message);
    }
  };
  const hidden = new Set(data.hidden_sections);
  const boxes = data.public_sections.map((s) => h("label", { class: "check" },
    h("input", { type: "checkbox", checked: !hidden.has(s.id), onchange: (event) => {
      if (event.currentTarget.checked) hidden.delete(s.id); else hidden.add(s.id);
      save({ hidden_sections: [...hidden] });
    } }), s.label));
  const preview = h("a", { class: "btn btn-sm", href: `${base}/preview`, target: "_blank", rel: "noopener" },
    icon("external", "icon icon-sm"), "Preview the public page");
  const toggle = h("button", { type: "button", class: data.public ? "btn btn-sm" : "btn btn-sm btn-primary",
    onclick: () => save({ public: !data.public }) }, data.public ? "Make it internal only" : "Make it public");
  return h("details", { class: "card profile-public" },
    h("summary", { class: "card-header" }, h("h2", {}, "Public page"),
      h("span", { class: "spacer" }, data.public ? h("span", { class: "pill pill-ok" }, "Public") : h("span", { class: "pill" }, "Inside only"))),
    h("div", { class: "card-body stack" },
      h("p", {}, data.public
        ? "This profile has a page on the organization's public site (from the next publish). Everyone inside always sees everything here; the public page shows only what is ticked below, and leaves off any entry marked with the closed eye."
        : "Only people inside the organization see this profile. Make it public to give it a page on the public site, showing only what is ticked below."),
      h("div", { class: "row-sm wrap" }, toggle, preview),
      h("fieldset", { class: "profile-public-sections" }, h("legend", {}, "Sections on the public page"), boxes)));
}

/** Who keeps the profile besides its person: for the person, their curators, and admins. */
function keepersCard(data, personId, done) {
  const base = `/api/v1/people/${encodeURIComponent(personId)}/profile/delegates`;
  const problem = h("p", { class: "field-error", role: "alert", hidden: true });
  const username = h("input", { class: "input", id: "pf-delegate", autocomplete: "off", maxlength: 64,
    placeholder: "Their username", "aria-describedby": "pf-delegate-hint" });
  const form = h("form", { class: "row-sm wrap", onsubmit: async (event) => {
    event.preventDefault();
    try {
      await post(base, { username: username.value.trim() });
    } catch (failure) {
      problem.textContent = failure.detail || failure.message;
      problem.hidden = false;
      return;
    }
    toast("They can now keep this profile.");
    done();
  } }, h("label", { for: "pf-delegate", class: "sr-only" }, "Username of the account to name"), username,
  h("button", { type: "submit", class: "btn btn-sm" }, icon("plus", "icon icon-sm"), "Name them"));
  return card({ title: "Who keeps this profile", pad: true, attrs: { "data-profile-section": "keepers" },
    body: h("div", { class: "stack" },
      h("p", { class: "subtle", id: "pf-delegate-hint" },
        "Besides its person, their curators, and admins, anyone named here may keep this profile, and later their CVs and biosketches, on the person's behalf. They cannot decide the person's papers or identifiers."),
      data.delegates.length ? h("ul", { class: "plain-list profile-entries" }, data.delegates.map((d) => h("li", { class: "profile-entry" },
        h("div", { class: "stack-xs grow" }, h("span", { class: "strong" }, d.name), h("span", { class: "subtle" }, d.username)),
        h("div", { class: "profile-actions" }, iconButton("trash", `Stop ${d.name} keeping this profile`, { onclick: () => confirmAction({
          title: `Stop ${d.name} keeping this profile?`, text: h("p", {}, "They will no longer be able to change it."),
          confirmLabel: "Stop", tone: "reject", run: async () => {
            try {
              await request("DELETE", `${base}/${d.account_id}`);
              invalidate();
              toast("Done.");
              done();
            } catch (failure) {
              toast(failure.detail || failure.message);
            }
          } }) }))))) : h("p", {}, "Nobody else keeps it."),
      problem, form) });
}

const ACTIONS = { added: "added", changed: "changed", removed: "removed", reordered: "reordered",
  published: "made public", unpublished: "made internal only", sections: "sections on it changed",
  hidden: "left off the public page", shown: "put back on the public page", created: "started", renamed: "renamed",
  deleted: "deleted" };

/** Profile changes, for the History tab. */
export function profileHistory(events) {
  if (!events.length) return null;
  return card({ title: "Profile changes", body: h("div", { class: "items" }, events.map((e) => {
    const title = e.after?.title ?? e.before?.title ?? e.after?.role ?? e.before?.role ?? e.after?.label ?? e.before?.label
      ?? e.after?.institution ?? e.before?.institution ?? e.after?.name ?? e.before?.name ?? null;
    return h("div", { class: "item" },
      h("span", { class: "entity-icon", "data-entity": "person" }, icon(e.action === "removed" ? "trash" : e.action === "added" ? "plus" : "edit")),
      h("div", { class: "item-main" },
        h("div", {}, `${e.section_label}: ${ACTIONS[e.action] ?? e.action}`, title ? h("span", { class: "muted" }, ` — ${title}`) : null),
        h("div", { class: "item-meta" }, h("span", {}, `${e.actor} (${e.capacity_label})`), h("span", { class: "sep" }), h("span", { title: e.at }, relative(e.at)))));
  })) });
}
