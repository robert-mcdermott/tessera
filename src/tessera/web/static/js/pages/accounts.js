// Accounts (admins): who can sign in, with which roles, and the tools to add, change, disable,
// and reset them.

import { get, post } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { relative } from "../core/format.js";
import { session } from "../core/session.js";
import { openDrawer } from "../components/drawer.js";
import { toast } from "../components/toast.js";
import { avatar, card, emptyState, pageHeader, pill } from "../components/ui.js";
import { show } from "./page.js";

const ROLES = [
  { id: "viewer", label: "Viewer", text: "Reads everything; decides nothing." },
  { id: "researcher", label: "Researcher", text: "Also reviews the suggestions about their own profile." },
  { id: "curator", label: "Curator", text: "Decides for everyone, or only for one group or person." },
  { id: "manager", label: "Manager", text: "Keeps one group's page and decides which papers are the group's." },
  { id: "admin", label: "Admin", text: "Everything, including accounts." },
];
const ROLE_LABEL = Object.fromEntries(ROLES.map((r) => [r.id, r.label]));

export function accounts(main, ctx = {}) {
  const reload = () => accounts(main, { samePage: true });
  return show(main, {
    title: "Accounts",
    quiet: ctx.samePage,
    load: async () => {
      const [list, groups] = await Promise.all([
        get("/api/v1/accounts", null, { fresh: true }),
        get("/api/v1/groups", { limit: 200 }),
      ]);
      return { list: list.data, units: groups.data.map((g) => ({ id: g.id, slug: g.slug, name: g.name, kind: g.kind_label })) };
    },
    view: ({ list, units }) => {
      const rows = list.map((a) => h("tr", { class: a.status === "disabled" ? "muted" : "" },
        h("td", {}, h("div", { class: "row-sm" }, avatar(a.name, "sm"),
          h("div", { class: "stack-xs" }, h("strong", {}, a.name), h("span", { class: "subtle mono" }, a.username)))),
        h("td", {}, h("div", { class: "chips" }, a.roles.map((r) => h("span", { class: "chip chip-plain",
          title: r.source === "provider" ? "From single sign-on: changed at the provider" : null },
          roleText(r), r.source === "provider" ? h("span", { class: "subtle" }, " · SSO") : null)))),
        h("td", { class: "nowrap muted" }, signsInWith(a.sign_in)),
        h("td", {}, a.status === "active" ? pill("Active", "ok") : pill("Disabled", "")),
        h("td", { class: "nowrap muted" }, a.last_sign_in_at ? relative(a.last_sign_in_at) : "never"),
        h("td", { class: "actions" }, h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => editAccount(a, units, reload) }, "Edit"))));
      return h("div", { class: "stack-lg" },
        pageHeader({
          eyebrow: "Admin",
          title: "Accounts",
          meta: h("span", {}, "Who can sign in, and what each person may do. Roles add up."),
          actions: [h("button", { type: "button", class: "btn btn-primary", onclick: () => addAccount(units, reload) }, icon("userPlus", "icon icon-sm"), "Add account")],
        }),
        card({ body: rows.length
          ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
            h("thead", {}, h("tr", {}, h("th", {}, "Person"), h("th", {}, "Roles"), h("th", {}, "Signs in with"), h("th", {}, "Status"), h("th", {}, "Last sign-in"),
              h("th", { class: "actions" }, h("span", { class: "sr-only" }, "Actions")))),
            h("tbody", {}, rows)))
          : emptyState({ iconName: "people", title: "No accounts yet" }) }),
        card({ title: "Roles", pad: true, body: h("dl", { class: "kv" }, ROLES.flatMap((r) => [h("dt", {}, r.label), h("dd", {}, r.text)])) }));
    },
  });
}

function signsInWith(how) {
  if (!how) return "";
  const ways = [how.single_sign_on ? "Single sign-on" : null, how.password ? "Password" : null].filter(Boolean);
  return ways.join(" or ") || "Nothing yet";
}

function roleText(r) {
  const label = ROLE_LABEL[r.role] ?? r.role;
  const below = r.include_below ? " and below" : "";
  return r.scope_type ? `${label} · ${r.scope_label ?? (r.scope_type === "unit" ? "one group" : "one person")}${below}` : label;
}

/** The role picker shared by the add and edit forms. units: [{id, slug, name, kind}]. */
function roleFields(initial, units) {
  // Roles from single sign-on are the provider's to change; these are the ones given here.
  const given = initial.roles.filter((r) => r.source !== "provider");
  const fromProvider = initial.roles.filter((r) => r.source === "provider");
  const roles = new Set(given.map((r) => r.role));
  const scoped = given.find((r) => r.role === "curator" && r.scope_type);
  const managed = given.find((r) => r.role === "manager");
  const boxes = ROLES.map((r) => h("input", { type: "checkbox", id: `role-${r.id}`, value: r.id, checked: roles.has(r.id) }));
  const groupOptions = (prefix) => units.map((u) => h("option", { value: `group:${u.slug}` }, `${prefix}${u.name} (${u.kind})`));
  const slugOf = (id) => units.find((u) => u.id === id)?.slug;
  const scope = h("select", { class: "input", id: "role-scope", "aria-label": "Curator scope" },
    h("option", { value: "" }, "Everyone"), groupOptions("People of "));
  if (scoped?.scope_type === "unit" && slugOf(scoped.scope_id)) {
    scope.value = `group:${slugOf(scoped.scope_id)}`;
  } else if (scoped?.scope_type === "person") {
    scope.append(h("option", { value: `person:${scoped.scope_id}` }, `Only ${scoped.scope_label ?? scoped.scope_id}`));
    scope.value = `person:${scoped.scope_id}`;
  }
  const manages = h("select", { class: "input", id: "role-manages", "aria-label": "Group managed" },
    h("option", { value: "" }, "Choose a group"), groupOptions(""));
  if (managed && slugOf(managed.scope_id)) manages.value = `group:${slugOf(managed.scope_id)}`;
  const below = h("input", { type: "checkbox", id: "role-below", checked: Boolean(managed?.include_below) });
  const person = h("input", { class: "input mono", id: "role-person", value: initial.person_id ?? "",
    placeholder: "per_… (from the person's profile address)", autocomplete: "off" });
  const fieldset = h("fieldset", { class: "field" }, h("legend", {}, fromProvider.length ? "Roles given here" : "Roles"),
    fromProvider.length ? h("p", { class: "subtle" }, `From single sign-on, changed at the provider at each sign-in: ${fromProvider.map(roleText).join(", ")}.`) : null,
    ROLES.map((r, i) => h("div", { class: "row-sm" }, boxes[i], h("label", { for: `role-${r.id}` }, h("strong", {}, r.label), h("span", { class: "subtle" }, ` · ${r.text}`)))),
    h("div", { class: "field" }, h("label", { for: "role-scope" }, "A curator decides for"), scope),
    h("div", { class: "field" }, h("label", { for: "role-manages" }, "A manager keeps the page of"), manages,
      h("label", { class: "row-sm subtle" }, below, "and the groups below it")),
    h("div", { class: "field" }, h("label", { for: "role-person" }, "Their own profile (needed for researchers)"), person));
  const value = () => {
    const chosen = boxes.filter((b) => b.checked).map((b) => b.value);
    if (chosen.includes("manager") && !manages.value) throw new Error("Choose the group the manager keeps.");
    return {
      roles: chosen.map((role) => role === "curator" ? { role, scope: scope.value || null }
        : role === "manager" ? { role, scope: manages.value, include_below: below.checked }
          : { role, scope: null }),
      person_id: person.value.trim() || null,
    };
  };
  return { fieldset, value };
}

function formDrawer({ title, subtitle, fields, submitLabel, onSubmit, extra = null }) {
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

function input(id, label, attrs = {}) {
  const node = h("input", { class: "input", id, autocomplete: "off", ...attrs });
  return [node, h("div", { class: "field" }, h("label", { for: id }, label), node)];
}

function addAccount(units, reload) {
  const [username, usernameField] = input("acct-username", "Username", { autocapitalize: "none", spellcheck: "false" });
  const [name, nameField] = input("acct-name", "Name (shown, and recorded on decisions)");
  const [email, emailField] = input("acct-email", "Email (optional)", { type: "email" });
  const [password, passwordField] = input("acct-password", "Temporary password (at least 12 characters)", { type: "password", autocomplete: "new-password" });
  const roles = roleFields({ roles: [{ role: "viewer" }] }, units);
  formDrawer({
    title: "Add an account",
    fields: [usernameField, nameField, emailField, passwordField, roles.fieldset,
      h("p", { class: "subtle" }, "Give the person their temporary password in person or by phone, and ask them to change it from their menu.")],
    submitLabel: "Add account",
    onSubmit: async () => {
      const chosen = roles.value();
      await post("/api/v1/accounts", { username: username.value.trim(), name: name.value.trim(), email: email.value.trim() || null,
        password: password.value, roles: chosen.roles, person_id: chosen.person_id });
      reload();
      toast(`Added ${username.value.trim().toLowerCase()}.`);
    },
  });
}

function editAccount(a, units, reload) {
  const me = session();
  const [name, nameField] = input("acct-name", "Name", { value: a.name });
  const [email, emailField] = input("acct-email", "Email", { type: "email", value: a.email ?? "" });
  const [password, passwordField] = input("acct-password", "New password (leave empty to keep)", { type: "password", autocomplete: "new-password" });
  const roles = roleFields(a, units);
  const toggle = h("button", { type: "button", class: `btn ${a.status === "active" ? "btn-reject" : "btn-ghost"}`, disabled: a.id === me?.id,
    title: a.id === me?.id ? "You cannot disable your own account" : null,
    onclick: async () => {
      try {
        await post(`/api/v1/accounts/${a.id}`, { status: a.status === "active" ? "disabled" : "active" });
      } catch (failure) {
        toast(failure.detail || "That did not work.");
        return;
      }
      document.querySelector(".drawer .btn-ghost")?.click();
      reload();
      toast(a.status === "active" ? `Disabled ${a.username} and signed them out.` : `Enabled ${a.username}.`);
    } }, a.status === "active" ? "Disable account" : "Enable account");
  formDrawer({
    title: `Edit ${a.username}`,
    subtitle: a.name,
    fields: [nameField, emailField, roles.fieldset, passwordField],
    submitLabel: "Save",
    extra: h("span", { class: "spacer" }, toggle),
    onSubmit: async () => {
      const chosen = roles.value();
      const body = { name: name.value.trim(), email: email.value.trim(), roles: chosen.roles, person_id: chosen.person_id ?? "" };
      if (password.value) body.password = password.value;
      await post(`/api/v1/accounts/${a.id}`, body);
      reload();
      toast(`Saved ${a.username}.`);
    },
  });
}
