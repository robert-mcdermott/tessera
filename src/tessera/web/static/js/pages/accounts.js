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
  { id: "curator", label: "Curator", text: "Decides for everyone, or only for one unit or person." },
  { id: "admin", label: "Admin", text: "Everything, including accounts." },
];
const ROLE_LABEL = Object.fromEntries(ROLES.map((r) => [r.id, r.label]));

export function accounts(main, ctx = {}) {
  const reload = () => accounts(main, { samePage: true });
  return show(main, {
    title: "Accounts",
    quiet: ctx.samePage,
    load: async () => {
      const [list, people] = await Promise.all([
        get("/api/v1/accounts", null, { fresh: true }),
        get("/api/v1/people", { limit: 1 }), // for the unit names in the directory's facets
      ]);
      return { list: list.data, units: (people.meta.facets?.unit ?? []).map((f) => f.value) };
    },
    view: ({ list, units }) => {
      const rows = list.map((a) => h("tr", { class: a.status === "disabled" ? "muted" : "" },
        h("td", {}, h("div", { class: "row-sm" }, avatar(a.name, "sm"),
          h("div", { class: "stack-xs" }, h("strong", {}, a.name), h("span", { class: "subtle mono" }, a.username)))),
        h("td", {}, h("div", { class: "chips" }, a.roles.map((r) => h("span", { class: "chip chip-plain" }, roleText(r))))),
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
            h("thead", {}, h("tr", {}, h("th", {}, "Person"), h("th", {}, "Roles"), h("th", {}, "Status"), h("th", {}, "Last sign-in"),
              h("th", { class: "actions" }, h("span", { class: "sr-only" }, "Actions")))),
            h("tbody", {}, rows)))
          : emptyState({ iconName: "people", title: "No accounts yet" }) }),
        card({ title: "Roles", pad: true, body: h("dl", { class: "kv" }, ROLES.flatMap((r) => [h("dt", {}, r.label), h("dd", {}, r.text)])) }));
    },
  });
}

function roleText(r) {
  const label = ROLE_LABEL[r.role] ?? r.role;
  return r.scope_type ? `${label} · ${r.scope_label ?? (r.scope_type === "unit" ? "one unit" : "one person")}` : label;
}

/** The role picker shared by the add and edit forms. */
function roleFields(initial, units) {
  const roles = new Set(initial.roles.map((r) => r.role));
  const scoped = initial.roles.find((r) => r.role === "curator" && r.scope_type);
  const boxes = ROLES.map((r) => h("input", { type: "checkbox", id: `role-${r.id}`, value: r.id, checked: roles.has(r.id) }));
  const scope = h("select", { class: "input", id: "role-scope", "aria-label": "Curator scope" },
    h("option", { value: "" }, "Everyone"),
    units.map((u) => h("option", { value: `unit:${u}` }, `People of ${u}`)));
  if (scoped?.scope_type === "unit" && scoped.scope_label) {
    if (!units.includes(scoped.scope_label)) scope.append(h("option", { value: `unit:${scoped.scope_label}` }, `People of ${scoped.scope_label}`));
    scope.value = `unit:${scoped.scope_label}`;
  } else if (scoped?.scope_type === "person") {
    scope.append(h("option", { value: `person:${scoped.scope_id}` }, `Only ${scoped.scope_label ?? scoped.scope_id}`));
    scope.value = `person:${scoped.scope_id}`;
  }
  const person = h("input", { class: "input mono", id: "role-person", value: initial.person_id ?? "",
    placeholder: "per_… (from the person's profile address)", autocomplete: "off" });
  const fieldset = h("fieldset", { class: "field" }, h("legend", {}, "Roles"),
    ROLES.map((r, i) => h("div", { class: "row-sm" }, boxes[i], h("label", { for: `role-${r.id}` }, h("strong", {}, r.label), h("span", { class: "subtle" }, ` · ${r.text}`)))),
    h("div", { class: "field" }, h("label", { for: "role-scope" }, "A curator decides for"), scope),
    h("div", { class: "field" }, h("label", { for: "role-person" }, "Their own profile (needed for researchers)"), person));
  const value = () => ({
    roles: boxes.filter((b) => b.checked).map((b) => ({ role: b.value, scope: b.value === "curator" ? scope.value || null : null })),
    person_id: person.value.trim() || null,
  });
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
