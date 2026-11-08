// Admin → Groups: the organization's divisions, programs, labs, and other groups; who belongs to
// each, in which role and since when; and the tools to add, nest, archive, and merge them.
// Loaded on first use.

import { get, invalidate, post, request } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { number, plural, relative } from "../core/format.js";
import { navigate, query, setQuery } from "../core/router.js";
import { closeDrawer, confirmAction, openDrawer } from "../components/drawer.js";
import { toast } from "../components/toast.js";
import { avatar, callout, card, debounce, emptyState, kv, pageHeader, pager, pill, tabs } from "../components/ui.js";
import { show } from "./page.js";

const PAGE = 50;

function sourceText(g) {
  if (g.source === "manual") return "Made by hand";
  if (g.source === "roster") return g.external_id ? `Roster · ${g.external_id}` : "Roster";
  return g.external_id ? `${g.source} · ${g.external_id}` : g.source;
}

function dates(m) {
  if (!m.start && !m.end) return h("span", { class: "subtle" }, m.current ? "Current" : "–");
  return h("span", { class: "nowrap tabular" }, `${m.start ?? "?"} – ${m.end ?? (m.current ? "now" : "?")}`);
}

/** The directory of groups, filtered by kind and words. */
export function groupsAdmin(main, ctx = {}) {
  const params = query();
  const kind = params.get("kind") || "";
  const q = params.get("q") || "";
  const archived = params.get("archived") === "1";
  const offset = Number(params.get("offset") || 0);
  return show(main, {
    title: "Groups",
    quiet: ctx.samePage,
    load: () => get("/api/v1/groups", { q: q || null, kind: kind || null, status: archived ? "all" : "active", limit: PAGE, offset }, { fresh: true }),
    view: (result) => {
      const vocab = result.meta.vocabulary;
      const search = h("input", { class: "input", type: "search", value: q, placeholder: `Find a ${vocab.group.toLowerCase()} by name or code`,
        "aria-label": `Find a ${vocab.group.toLowerCase()}`,
        oninput: debounce((event) => setQuery({ q: event.target.value.trim() || null, offset: null }), 250) });
      const kinds = [{ id: "", label: "All" }, ...result.meta.kinds.filter((k) => k.count || k.id === kind)];
      const kindTabs = h("div", { class: "segmented", role: "group", "aria-label": "Kind" }, kinds.map((k) =>
        h("button", { type: "button", "aria-pressed": String(k.id === kind), onclick: () => setQuery({ kind: k.id || null, offset: null }) },
          k.label, k.count != null ? h("span", { class: "subtle tabular" }, ` ${number(k.count)}`) : null)));
      const showArchived = h("label", { class: "row-sm subtle" },
        h("input", { type: "checkbox", checked: archived, onchange: (event) => setQuery({ archived: event.target.checked ? "1" : null, offset: null }) }),
        "Show archived");
      const rows = result.data.map((g) => h("tr", { class: g.status === "archived" ? "muted" : "" },
        h("td", {}, h("div", { class: "stack-xs" },
          h("a", { href: `/admin/groups/${g.slug}` }, h("strong", {}, g.name)),
          g.short_name || g.code ? h("span", { class: "subtle" }, [g.short_name, g.code].filter(Boolean).join(" · ")) : null)),
        h("td", {}, g.kind_label, g.status === "archived" ? h("span", {}, " ", pill("Archived")) : null),
        h("td", { class: "subtle" }, g.parents.map((p) => p.name).join("; ") || "–"),
        h("td", { class: "tabular" }, number(g.members)),
        h("td", { class: "tabular subtle" }, number(g.alumni)),
        h("td", { class: "subtle" }, sourceText(g))));
      const empty = !result.data.length && !q && !kind;
      return h("div", { class: "stack-lg" },
        pageHeader({
          eyebrow: "Admin",
          title: vocab.groups,
          meta: h("span", {}, "Divisions, programs, labs, and every other kind, with who belongs to each, in which role, and since when."),
          actions: [h("button", { type: "button", class: "btn btn-primary", onclick: () => editGroup(null, vocab) },
            icon("plus", "icon icon-sm"), `Add ${vocab.group.toLowerCase()}`)],
        }),
        empty ? card({ pad: true, body: emptyState({ iconName: "unit", title: `No ${vocab.groups.toLowerCase()} yet`,
          text: "Add them here one at a time, or import a file from your organization's records with `tessera groups import`.",
          action: h("button", { type: "button", class: "btn btn-primary", onclick: () => editGroup(null, vocab) }, `Add a ${vocab.group.toLowerCase()}`) }) })
          : h("div", { class: "stack" },
            h("div", { class: "toolbar" }, h("div", { class: "search-field" }, icon("search"), search), showArchived),
            kindTabs,
            card({ body: rows.length
              ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
                h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Kind"), h("th", {}, "Within"),
                  h("th", {}, "Members"), h("th", {}, "Alumni"), h("th", {}, "Source"))),
                h("tbody", {}, rows)))
              : emptyState({ iconName: "search", title: "No matches", text: "Try other words, another kind, or include archived groups." }) }),
            pager({ total: result.meta.total, offset, limit: PAGE, onPage: (next) => setQuery({ offset: next || null }) })),
        callout(h("span", {}, "Groups can also come from your organization's own records: ",
          h("code", {}, "tessera groups import groups.csv --source hr"),
          ". Each row carries the source's identifier, so importing again updates what it made. See the guide on groups.")));
    },
  });
}

/** One group: its members (current and alumni), sub-groups, details, and history. */
export function groupAdmin(main, params, ctx = {}) {
  const ref = params.ref;
  const tab = query().get("show") === "alumni" ? "alumni" : "current";
  const reload = () => groupAdmin(main, params, { samePage: true });
  return show(main, {
    title: (d) => d.group.name,
    quiet: ctx.samePage,
    load: async () => {
      const [detail, members, history] = await Promise.all([
        get(`/api/v1/groups/${encodeURIComponent(ref)}`, null, { fresh: true }),
        get(`/api/v1/groups/${encodeURIComponent(ref)}/members`, { include: "all" }, { fresh: true }),
        get(`/api/v1/groups/${encodeURIComponent(ref)}/history`, { limit: 40 }, { fresh: true }),
      ]);
      return { group: detail.data, vocab: detail.meta.vocabulary, members: members.data, history: history.data };
    },
    view: ({ group, vocab, members, history }) => {
      if (group.slug !== ref && group.id !== ref) {
        // An old address (a merged group's, or the code): show the group at its own.
        window.history.replaceState(null, "", `/admin/groups/${group.slug}`);
      }
      const current = members.filter((m) => m.current);
      const here = new Set(current.map((m) => m.person_id));
      const alumni = members.filter((m) => !m.current && !here.has(m.person_id));
      const shown = tab === "alumni" ? alumni : current;
      const merged = group.status === "merged";
      const actions = [
        h("button", { type: "button", class: "btn", onclick: () => editGroup(group, vocab, reload) }, "Edit"),
        h("button", { type: "button", class: "btn btn-primary", onclick: () => editMembership(group, null, vocab, reload) },
          icon("userPlus", "icon icon-sm"), "Add member"),
      ];
      const memberRows = shown.map((m) => h("tr", {},
        h("td", {}, h("a", { class: "row-sm", href: `/people/${m.person_id}` }, avatar(m.name, "sm"), h("strong", {}, m.name))),
        h("td", {}, m.leader ? pill(m.role_label, "accent") : m.role_label),
        h("td", { class: "muted" }, m.title ?? ""),
        h("td", {}, dates(m)),
        h("td", { class: "subtle" }, m.source === "manual" ? "By hand" : m.source),
        h("td", { class: "actions" }, h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => editMembership(group, m, vocab, reload) }, "Edit"))));
      const membersCard = card({
        title: "Members",
        action: tabs([{ id: "current", label: "Current", count: current.length }, { id: "alumni", label: "Alumni", count: alumni.length }],
          tab, (id) => setQuery({ show: id === "alumni" ? "alumni" : null })),
        body: memberRows.length
          ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
            h("thead", {}, h("tr", {}, h("th", {}, "Person"), h("th", {}, "Role"), h("th", {}, "Title"), h("th", {}, "Dates"),
              h("th", {}, "Source"), h("th", { class: "actions" }, h("span", { class: "sr-only" }, "Actions")))),
            h("tbody", {}, memberRows)))
          : emptyState({ iconName: "people", title: tab === "alumni" ? "No alumni" : "No current members",
            text: tab === "alumni" ? "People whose memberships have ended appear here." : "Add the people who belong, with their roles and dates." }),
      });
      const children = card({
        title: "Within this group",
        action: merged ? null : h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => editGroup(null, vocab, null, group) },
          icon("plus", "icon icon-sm"), "Add"),
        pad: true,
        body: group.children.length
          ? h("ul", { class: "plain-list" }, group.children.map((c) => h("li", { class: "row-sm" },
            h("a", { href: `/admin/groups/${c.slug}` }, c.name), h("span", { class: "subtle" }, `${c.kind_label} · ${plural(c.members, "member")}`),
            c.status === "archived" ? pill("Archived") : null)))
          : h("p", { class: "subtle" }, `No ${vocab.groups.toLowerCase()} sit under this one.`),
      });
      const details = card({ title: "Details", pad: true, body: h("div", { class: "stack" },
        group.description ? h("p", {}, group.description) : null,
        kv([
          ["Address", h("span", { class: "mono" }, `/groups/${group.slug}`)],
          ["Short name", group.short_name],
          ["Code", group.code ? h("span", { class: "mono" }, group.code) : null],
          ["Source", sourceText(group)],
          ["Merged here", group.merged_from.length ? group.merged_from.map((g) => g.name).join("; ") : null],
          ["Created", relative(group.created_at)],
        ]),
        !["manual", "roster"].includes(group.source)
          ? callout(`This group comes from ${group.source}. Its next import from ${group.source} overwrites the fields the import supplies.`)
          : null) });
      const more = h("div", { class: "row wrap" },
        group.status === "archived"
          ? h("button", { type: "button", class: "btn btn-sm", onclick: () => setStatus(group, "active", reload) }, "Restore")
          : h("button", { type: "button", class: "btn btn-sm", onclick: () => setStatus(group, "archived", reload) }, "Archive"),
        h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => mergeGroup(group, vocab) }, "Merge into another…"));
      return h("div", { class: "stack-lg" },
        pageHeader({
          eyebrow: h("a", { href: "/admin/groups" }, vocab.groups),
          title: group.name,
          meta: h("div", { class: "row-sm wrap" },
            pill(group.kind_label, "info"),
            group.status === "archived" ? pill("Archived", "warn") : null,
            group.parents.length ? h("span", { class: "subtle" }, "within ",
              group.parents.map((p, i) => [i ? ", " : "", h("a", { href: `/admin/groups/${p.id}` }, p.name)])) : null,
            h("span", { class: "subtle" }, `· ${plural(current.length, "current member")}, ${plural(alumni.length, "alumnus", "alumni")}`)),
          actions: merged ? null : actions,
        }),
        h("div", { class: "grid-main" },
          h("div", { class: "stack-lg" }, membersCard, children),
          h("div", { class: "stack-lg" }, details,
            card({ title: "History", pad: true, body: history.length ? historyList(history, vocab) : h("p", { class: "subtle" }, "No changes yet.") }),
            merged ? null : more)));
    },
  });
}

const FIELD_NAMES = { name: "name", kind: "kind", slug: "address", short_name: "short name", code: "code", description: "description", status: "status" };

function eventText(e, vocab) {
  const d = e.detail ?? {};
  const who = e.person ?? "someone";
  const role = (key) => vocab.roles.find((r) => r.id === key)?.label ?? key;
  switch (e.action) {
    case "create": return "Created";
    case "edit": case "import": {
      const fields = Object.keys(d.changes ?? {}).map((k) => FIELD_NAMES[k] ?? k);
      return `${e.action === "import" ? "Updated by import" : "Changed"}: ${fields.join(", ")}`;
    }
    case "archive": return "Archived";
    case "restore": return "Restored";
    case "nest": return "Changed where it sits";
    case "merge": return `Merged into ${d.into_name}`;
    case "absorb": return `Took in ${d.merged_name}`;
    case "member_add": return `Added ${who} as ${role(d.after?.role).toLowerCase()}`;
    case "member_end": return `Ended ${who}'s membership`;
    case "member_remove": return `Removed ${who}`;
    case "member_edit": return `Changed ${who}'s membership`;
    default: return e.action;
  }
}

function historyList(events, vocab) {
  return h("ol", { class: "history" }, events.map((e) => h("li", { class: "history-item" },
    icon(e.action.startsWith("member") ? "person" : "history", "icon icon-sm"),
    h("span", { class: "grow" }, eventText(e, vocab), h("span", { class: "subtle" }, ` · ${e.actor}, ${relative(e.at)}`)))));
}

// --- forms ---------------------------------------------------------------------------------

function field(id, label, control, hint = null) {
  return h("div", { class: "field" }, h("label", { for: id }, label), control, hint ? h("p", { class: "subtle" }, hint) : null);
}

function formDrawer({ title, subtitle = null, fields, submitLabel, onSubmit, extra = null }) {
  openDrawer({
    title,
    subtitle,
    label: title,
    render: (body, footer, close) => {
      const error = h("p", { class: "field-error", role: "alert", hidden: true });
      const submit = h("button", { type: "submit", class: "btn btn-primary" }, submitLabel);
      body.append(h("form", { class: "stack", novalidate: true, onsubmit: async (event) => {
        event.preventDefault();
        submit.disabled = true;
        try {
          await onSubmit();
        } catch (failure) {
          error.textContent = failure.detail || failure.message || "That did not work.";
          error.hidden = false;
          submit.disabled = false;
          return;
        }
        close();
      } }, fields, error, h("div", { class: "row wrap" }, submit, h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel"), extra)));
    },
  });
}

/**
 * Search-as-you-type for a person or a group. choose(item) is called with a pick; the picks
 * stay visible as chips (several when multiple).
 */
function picker({ id, placeholder, search, describe, initial = [], multiple = false, exclude = () => false }) {
  let chosen = [...initial];
  const chips = h("div", { class: "chips" });
  const results = h("div", { class: "plain-list compact picker-results", role: "listbox", "aria-label": "Matches" });
  const input = h("input", { class: "input", id, type: "search", placeholder, autocomplete: "off" });
  const draw = () => {
    chips.replaceChildren(...chosen.map((item) => h("span", { class: "filter-chip" }, describe(item).label,
      h("button", { type: "button", "aria-label": `Remove ${describe(item).label}`, onclick: () => {
        chosen = chosen.filter((c) => c.id !== item.id);
        draw();
      } }, icon("close", "icon icon-sm")))));
    input.hidden = !multiple && chosen.length > 0;
  };
  const run = debounce(async () => {
    const text = input.value.trim();
    if (text.length < 2) {
      results.replaceChildren();
      return;
    }
    let items = [];
    try {
      items = (await search(text)).filter((item) => !exclude(item) && !chosen.some((c) => c.id === item.id));
    } catch {
      items = [];
    }
    results.replaceChildren(...(items.length ? items.slice(0, 8).map((item) => {
      const d = describe(item);
      return h("button", { type: "button", class: "btn btn-ghost btn-sm picker-option", role: "option", onclick: () => {
        chosen = multiple ? [...chosen, item] : [item];
        input.value = "";
        results.replaceChildren();
        draw();
      } }, h("span", {}, d.label), d.note ? h("span", { class: "subtle" }, ` · ${d.note}`) : null);
    }) : [h("span", { class: "subtle" }, "No matches")]));
  }, 200);
  input.addEventListener("input", run);
  draw();
  return { node: h("div", { class: "stack-sm" }, chips, input, results), value: () => chosen };
}

function groupPicker(id, vocab, { initial = [], multiple = true, exclude = () => false } = {}) {
  return picker({
    id,
    placeholder: `Find a ${vocab.group.toLowerCase()}`,
    initial,
    multiple,
    exclude,
    search: async (text) => (await get("/api/v1/groups", { q: text, limit: 8 })).data,
    describe: (g) => ({ label: g.name, note: g.kind_label }),
  });
}

/** Add a group (group null), or edit one. parent presets the new group's place. */
function editGroup(group, vocab, reload = null, parent = null) {
  const value = (v) => v ?? "";
  const name = h("input", { class: "input", id: "group-name", value: value(group?.name), autocomplete: "off" });
  const kind = h("select", { class: "input", id: "group-kind" }, vocab.kinds.map((k) => h("option", { value: k.id }, k.label)));
  kind.value = group?.kind ?? (parent ? "lab" : vocab.kinds[0].id);
  if (!vocab.kinds.some((k) => k.id === kind.value)) kind.value = vocab.kinds[0].id;
  const shortName = h("input", { class: "input", id: "group-short", value: value(group?.short_name), autocomplete: "off" });
  const code = h("input", { class: "input mono", id: "group-code", value: value(group?.code), autocomplete: "off" });
  const slug = h("input", { class: "input mono", id: "group-slug", value: value(group?.slug), autocomplete: "off",
    placeholder: group ? "" : "made from the name", spellcheck: "false" });
  const description = h("textarea", { class: "input", id: "group-description", rows: 4 }, value(group?.description));
  const below = new Set([group?.id, ...(group?.children ?? []).map((c) => c.id)]);
  const parents = groupPicker("group-parents", vocab, {
    initial: group ? group.parents : parent ? [parent] : [],
    exclude: (g) => below.has(g.id),
  });
  formDrawer({
    title: group ? `Edit ${group.name}` : `Add a ${vocab.group.toLowerCase()}`,
    subtitle: group ? group.kind_label : parent ? `Within ${parent.name}` : null,
    fields: [
      field("group-name", "Name", name),
      h("div", { class: "grid-2" }, field("group-kind", "Kind", kind), field("group-short", "Short name (optional)", shortName)),
      field("group-parents", "Sits within", parents.node, "Any number: a program can span several divisions."),
      field("group-description", "What it does (optional)", description),
      h("div", { class: "grid-2" }, field("group-code", "Code (optional)", code), field("group-slug", "Address", slug,
        group ? "Changing it breaks links to the old address." : null)),
    ],
    submitLabel: group ? "Save" : `Add ${vocab.group.toLowerCase()}`,
    onSubmit: async () => {
      const body = { name: name.value.trim(), kind: kind.value, short_name: shortName.value.trim(), code: code.value.trim(),
        description: description.value.trim(), parents: parents.value().map((p) => p.id) };
      if (slug.value.trim()) body.slug = slug.value.trim();
      if (group) {
        const saved = await post(`/api/v1/groups/${group.id}`, body);
        toast(`Saved ${saved.data.name}.`);
        if (saved.data.slug !== group.slug) navigate(`/admin/groups/${saved.data.slug}`, { replace: true });
        else reload?.();
      } else {
        for (const key of ["short_name", "code", "description"]) if (!body[key]) delete body[key];
        const made = await post("/api/v1/groups", body);
        toast(`Added ${made.data.name}.`);
        navigate(`/admin/groups/${made.data.slug}`);
      }
    },
  });
}

function setStatus(group, status, reload) {
  const archiving = status === "archived";
  confirmAction({
    title: archiving ? `Archive ${group.name}?` : `Restore ${group.name}?`,
    text: archiving
      ? h("p", {}, "It leaves lists, pickers, and the directory's filters. Its memberships and history stay, and you can restore it at any time.")
      : h("p", {}, "It returns to lists, pickers, and the directory's filters."),
    confirmLabel: archiving ? "Archive" : "Restore",
    run: async () => {
      try {
        await post(`/api/v1/groups/${group.id}`, { status });
      } catch (failure) {
        toast(failure.detail || "That did not work.");
        return;
      }
      toast(archiving ? `Archived ${group.name}.` : `Restored ${group.name}.`);
      reload();
    },
  });
}

function mergeGroup(group, vocab) {
  const below = new Set([group.id, ...group.children.map((c) => c.id)]);
  const target = groupPicker("merge-into", vocab, { multiple: false, exclude: (g) => below.has(g.id) });
  formDrawer({
    title: `Merge ${group.name}`,
    subtitle: "into another group",
    fields: [
      field("merge-into", "The group that remains", target.node),
      callout(h("span", {}, `Its ${plural(group.members, "current member")} and alumni, the groups within it, its place under others, and curator roles limited to it all move to the group you choose. Its address will lead there. A merge cannot be undone from the app.`), "warn"),
    ],
    submitLabel: "Merge",
    onSubmit: async () => {
      const [into] = target.value();
      if (!into) throw new Error("Choose the group that remains.");
      await post(`/api/v1/groups/${group.id}/merge`, { into: into.id });
      toast(`Merged ${group.name} into ${into.name}.`);
      navigate(`/admin/groups/${into.slug}`);
    },
  });
}

function datesHint() {
  return "As precise as you know: 2015, 2015-07, or 2015-07-01.";
}

/** Add a member (membership null), or change, end, or remove a membership. */
function editMembership(group, membership, vocab, reload) {
  const person = membership ? null : picker({
    id: "member-person",
    placeholder: "Find a person by name",
    search: async (text) => (await get("/api/v1/people", { q: text, limit: 8, status: "active,former" })).data,
    describe: (p) => ({ label: p.name, note: (p.units ?? []).join(", ") || p.status }),
  });
  const role = h("select", { class: "input", id: "member-role" }, vocab.roles.map((r) => h("option", { value: r.id }, r.label)));
  role.value = membership?.role ?? vocab.default_role;
  const title = h("input", { class: "input", id: "member-title", value: membership?.title ?? "", autocomplete: "off",
    placeholder: "e.g. Postdoctoral fellow" });
  const start = h("input", { class: "input mono", id: "member-start", value: membership?.start ?? "", autocomplete: "off", placeholder: "2015" });
  const end = h("input", { class: "input mono", id: "member-end", value: membership?.end ?? "", autocomplete: "off", placeholder: "Empty while current" });
  const today = new Date();
  const thisMonth = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
  const endNow = membership?.current
    ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => { end.value = thisMonth; end.focus(); } }, "Ended this month")
    : null;
  const remove = membership ? h("button", { type: "button", class: "btn btn-reject", onclick: () => {
    closeDrawer();
    confirmAction({
      title: `Remove ${membership.name}?`,
      text: h("div", { class: "stack-sm" },
        h("p", {}, `Only if this membership was a mistake: ${membership.name} will not appear in ${group.name}, now or as an alumnus, and imports will not bring it back.`),
        h("p", { class: "subtle" }, "To record that someone left, set an end date instead.")),
      confirmLabel: "Remove",
      tone: "reject",
      run: async () => {
        try {
          await request("DELETE", `/api/v1/groups/${group.id}/members/${membership.id}`);
          invalidate();
        } catch (failure) {
          toast(failure.detail || "That did not work.");
          return;
        }
        toast(`Removed ${membership.name} from ${group.name}.`);
        reload();
      },
    });
  } }, "Remove…") : null;
  formDrawer({
    title: membership ? membership.name : `Add to ${group.name}`,
    subtitle: membership ? `${membership.role_label} in ${group.name}` : null,
    fields: [
      person ? field("member-person", "Person", person.node, "People come from the directory; add new people with the roster.") : null,
      h("div", { class: "grid-2" }, field("member-role", "Role", role), field("member-title", "Title (optional)", title)),
      h("div", { class: "grid-2" }, field("member-start", "Since", start), field("member-end", "Until", end, endNow)),
      h("p", { class: "subtle" }, datesHint()),
      membership && membership.source !== "manual"
        ? callout(`This membership comes from ${membership.source}; its next import from ${membership.source} may change it again.`)
        : null,
    ],
    submitLabel: membership ? "Save" : "Add member",
    extra: remove ? h("span", { class: "spacer" }, remove) : null,
    onSubmit: async () => {
      const body = { role: role.value, title: title.value.trim(), start: start.value.trim(), end: end.value.trim() };
      if (membership) {
        await post(`/api/v1/groups/${group.id}/members/${membership.id}`, body);
        toast(`Saved ${membership.name}'s membership.`);
      } else {
        const [who] = person.value();
        if (!who) throw new Error("Choose a person.");
        for (const key of ["title", "start", "end"]) if (!body[key]) delete body[key];
        await post(`/api/v1/groups/${group.id}/members`, { ...body, person_id: who.id });
        toast(`Added ${who.name} to ${group.name}.`);
      }
      reload();
    },
  });
}

