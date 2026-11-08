// The app shell: sidebar navigation (what the signed-in person may use), top bar, theme
// toggle, user menu, and mobile menu.

import { get, request } from "../core/api.js";
import { h, icon, iconButton } from "../core/dom.js";
import { compact } from "../core/format.js";
import { onNavigate } from "../core/router.js";
import { can, session, setCsrfToken } from "../core/session.js";
import { openAppearance } from "./appearance.js";
import { openDrawer } from "./drawer.js";
import { toast } from "./toast.js";
import { avatar } from "./ui.js";

const NAV = [
  { section: null, items: [
    { href: "/", label: "Home", iconName: "home", match: (p) => p === "/" },
    { href: "/search", label: "Search", iconName: "search", match: (p) => p.startsWith("/search") },
  ] },
  { section: "Explore", items: [
    { href: "/people", label: "People", iconName: "people", match: (p) => p.startsWith("/people") },
    { href: "/groups", label: () => groupsLabel, iconName: "unit", match: (p) => p.startsWith("/groups") },
    { href: "/experts", label: "Find experts", iconName: "compass", match: (p) => p.startsWith("/experts") },
    { href: "/graph", label: "Connections", iconName: "network", match: (p) => p.startsWith("/graph") },
    { href: "/publications", label: "Publications", iconName: "publication", match: (p) => p.startsWith("/publications") },
    { href: "/grants", label: "Grants", iconName: "grant", match: (p) => p.startsWith("/grants") },
    { href: "/studies", label: "Studies", iconName: "study", match: (p) => p.startsWith("/studies") },
  ] },
  { section: "Insight", items: [
    { href: "/insight", label: "Portfolio", iconName: "chart", match: (p) => p.startsWith("/insight") },
  ] },
  { section: "Curate", items: [
    { href: "/inbox", label: "Inbox", iconName: "list", badge: "inbox", when: "edit_groups", match: (p) => p.startsWith("/inbox") },
    { href: "/review", label: () => (can("curate_any") ? "Review queue" : "My suggestions"), iconName: "review", badge: "review", when: "review", match: (p) => p === "/review" },
    { href: "/review/people", label: "Discovered people", iconName: "userPlus", badge: "candidates", when: "curate_all", match: (p) => p.startsWith("/review/people") },
    { href: "/review/duplicates", label: "Possible duplicates", iconName: "layers", badge: "duplicates", when: "curate_all", match: (p) => p.startsWith("/review/duplicates") },
    { href: "/review/log", label: "Decision log", iconName: "history", when: "curate_any", match: (p) => p.startsWith("/review/log") },
  ] },
  { section: "Admin", items: [
    { href: "/admin/sources", label: "Sources", iconName: "sources", match: (p) => p.startsWith("/admin/sources") },
    { href: "/admin/evidence", label: "Evidence", iconName: "sparkle", when: "curate_any", match: (p) => p.startsWith("/admin/evidence") },
    { href: "/admin/groups", label: () => groupsLabel, iconName: "unit", when: "admin", match: (p) => p.startsWith("/admin/groups") },
    { href: "/admin/accounts", label: "Accounts", iconName: "people", when: "admin", match: (p) => p.startsWith("/admin/accounts") },
    { href: "/admin/operations", label: "Operations", iconName: "clock", when: "admin", match: (p) => p.startsWith("/admin/operations") },
    { href: "/api/docs", label: "API reference", iconName: "api", match: () => false, external: true },
  ] },
];

let groupsLabel = "Groups"; // what the organization calls groups ([vocabulary])

export function mountShell(root, meta, { onSearch }) {
  groupsLabel = meta.vocabulary?.groups ?? groupsLabel;
  const product = meta.product;
  const org = meta.organization;
  const navItems = [];
  const sidebar = h("nav", { class: "sidebar", "aria-label": "Main" },
    // An organization's own logo replaces the mark and name (light and dark versions).
    product.logo
      ? h("a", { class: "brand brand-has-logo", href: "/", "aria-label": `${product.name} home` },
        h("img", { class: "brand-logo brand-logo-light", src: product.logo.light, alt: product.name }),
        h("img", { class: "brand-logo brand-logo-dark", src: product.logo.dark, alt: "" }))
      : h("a", { class: "brand", href: "/" },
        h("img", { class: "brand-mark", src: "/img/tessera-mark.svg", alt: "", width: 26, height: 26 }),
        h("span", { class: "brand-text" }, h("span", {}, product.name),
          h("span", { class: "brand-org" }, org.short_name || org.name))),
    NAV.map((group) => {
      const items = group.items.filter((item) => !item.when || can(item.when));
      if (!items.length) return null;
      return h("div", { class: "nav" },
        group.section ? h("div", { class: "nav-section" }, group.section) : null,
        items.map((item) => {
          const label = typeof item.label === "function" ? item.label() : item.label;
          const badge = item.badge ? h("span", { class: "badge", hidden: true }) : null;
          const node = h("a", { class: "nav-item", href: item.href, title: label },
            icon(item.iconName), h("span", { class: "nav-label" }, label), badge);
          navItems.push({ node, item, badge });
          return node;
        }));
    }),
    h("div", { class: "sidebar-footer" },
      h("span", {}, org.name),
      (product.footer_links ?? []).length ? h("span", { class: "footer-links" }, product.footer_links.map((l, i) =>
        [i ? " · " : "", h("a", { href: l.url, rel: "noopener", target: l.url.startsWith("mailto:") ? null : "_blank" }, l.label)])) : null,
      product.show_powered_by
        ? h("span", {}, "Powered by ", h("a", { href: "/api/docs" }, `Tessera ${product.version}`))
        : null));

  const appearanceButton = iconButton("palette", "Appearance", { onclick: () => openAppearance() });

  let backdrop = null;
  let menuButton = null;
  const onMenuKey = (event) => {
    if (event.key === "Escape") closeMenu({ restoreFocus: true });
  };
  // On navigation the page moves focus to its heading, so focus returns to the menu
  // button only when the person dismisses the menu.
  const closeMenu = ({ restoreFocus = false } = {}) => {
    if (!backdrop) return;
    sidebar.removeAttribute("data-open");
    menuButton.setAttribute("aria-expanded", "false");
    document.removeEventListener("keydown", onMenuKey);
    backdrop.remove();
    backdrop = null;
    if (restoreFocus) menuButton.focus();
  };
  menuButton = iconButton("menu", "Open navigation", {
    class: "icon-button menu-button",
    "aria-expanded": "false",
    onclick: () => {
      sidebar.setAttribute("data-open", "");
      menuButton.setAttribute("aria-expanded", "true");
      backdrop = h("div", { class: "sidebar-backdrop", onclick: () => closeMenu({ restoreFocus: true }) });
      document.body.append(backdrop);
      document.addEventListener("keydown", onMenuKey);
      sidebar.querySelector("a")?.focus();
    },
  });

  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  const topbar = h("header", { class: "topbar" },
    menuButton,
    h("button", { type: "button", class: "search-trigger", onclick: onSearch, "aria-label": "Search (opens command palette)" },
      icon("search", "icon icon-sm"), h("span", { class: "grow" }, "Search people, papers, grants, studies…"),
      h("kbd", {}, isMac ? "⌘K" : "Ctrl K")),
    h("div", { class: "topbar-actions" },
      appearanceButton,
      meta.mode.accounts ? userMenu() : h("span", { class: "operator", title: "Single-operator mode: decisions are recorded under this name" },
        avatar(meta.mode.operator, "sm"), h("span", { class: "operator-name" }, meta.mode.operator))));

  const main = h("main", { class: "shell-main", id: "main" });
  const shell = h("div", { class: "shell" }, sidebar, topbar, main);
  root.replaceChildren(shell);

  // Work waiting for a curator, shown beside the review queue, discovered people, and possible
  // duplicates. Refreshed after every change anywhere in the app.
  let pending = null;
  const refreshCounts = () => {
    clearTimeout(pending);
    pending = setTimeout(async () => {
      const ask = (allowed, path) => (allowed ? get(path, { limit: 1 }, { fresh: true }).catch(() => null) : null);
      const [queue, found, pairs, inbox] = await Promise.all([
        ask(can("review"), "/api/v1/review"),
        ask(can("curate_all"), "/api/v1/candidates"),
        ask(can("curate_all"), "/api/v1/duplicates"),
        ask(can("edit_groups"), "/api/v1/inbox"),
      ]);
      const counts = {
        inbox: inbox ? inbox.data.groups.reduce((n, g) => n + g.total, 0) : null,
        review: queue ? (queue.meta.bands?.strong ?? 0) + (queue.meta.bands?.possible ?? 0) : null,
        candidates: found ? found.meta.total : null,
        duplicates: pairs ? pairs.meta.total : null,
      };
      for (const { item, badge } of navItems) {
        if (!badge) continue;
        const value = counts[item.badge];
        badge.hidden = !value;
        badge.textContent = value ? compact(value) : "";
        badge.title = value ? `${value.toLocaleString()} waiting` : "";
      }
    }, 400);
  };
  window.addEventListener("tessera:changed", refreshCounts);
  refreshCounts();

  onNavigate((current) => {
    for (const { node, item } of navItems) {
      if (item.match(current.pathname)) node.setAttribute("aria-current", "page");
      else node.removeAttribute("aria-current");
    }
    closeMenu();
  });
  return { main };
}

const ROLE_NAMES = { admin: "Admin", curator: "Curator", researcher: "Researcher", viewer: "Viewer" };

/** The signed-in person's name, roles, and actions (change password, sign out). */
function userMenu() {
  const me = session();
  const roles = [...new Set(me.roles.map((r) => (r.scope_type ? `${ROLE_NAMES[r.role]} (limited)` : ROLE_NAMES[r.role])))].join(", ");
  const menu = h("div", { class: "user-menu", role: "menu", hidden: true },
    h("div", { class: "user-menu-who" }, h("strong", {}, me.name), h("span", { class: "subtle" }, `${me.username} · ${roles}`)),
    h("button", { type: "button", role: "menuitem", class: "user-menu-item", onclick: () => { close(); changePassword(); } }, icon("keyboard", "icon icon-sm"), "Change password…"),
    h("button", { type: "button", role: "menuitem", class: "user-menu-item", onclick: () => signOut() }, icon("arrowRight", "icon icon-sm"), "Sign out"));
  const button = h("button", { type: "button", class: "operator user-button", "aria-haspopup": "menu", "aria-expanded": "false",
    onclick: () => (menu.hidden ? open() : close()) },
    avatar(me.name, "sm"), h("span", { class: "operator-name" }, me.name));
  const onKey = (event) => { if (event.key === "Escape") { close(); button.focus(); } };
  const onClick = (event) => { if (!wrap.contains(event.target)) close(); };
  function open() {
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    document.addEventListener("keydown", onKey);
    document.addEventListener("click", onClick, true);
    menu.querySelector("[role=menuitem]")?.focus();
  }
  function close() {
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
    document.removeEventListener("keydown", onKey);
    document.removeEventListener("click", onClick, true);
  }
  const wrap = h("div", { class: "user-wrap" }, button, menu);
  return wrap;
}

async function signOut() {
  try {
    await request("DELETE", "/api/v1/session");
  } catch {
    /* signed out either way */
  }
  window.location.assign("/sign-in");
}

function changePassword() {
  openDrawer({
    title: "Change password",
    label: "Change password",
    render: (body, footer, close) => {
      const field = (id, label, autocomplete) => {
        const input = h("input", { class: "input", id, type: "password", autocomplete, required: true });
        return [input, h("div", { class: "field" }, h("label", { for: id }, label), input)];
      };
      const [current, currentField] = field("pw-current", "Current password", "current-password");
      const [next, nextField] = field("pw-new", "New password (at least 12 characters)", "new-password");
      const [again, againField] = field("pw-again", "New password again", "new-password");
      const error = h("p", { class: "field-error", role: "alert", hidden: true });
      const submit = h("button", { type: "submit", class: "btn btn-primary" }, "Change password");
      const fail = (message, focus) => {
        error.textContent = message;
        error.hidden = false;
        focus.focus();
      };
      body.append(h("form", { class: "stack", novalidate: true, onsubmit: async (event) => {
        event.preventDefault();
        if (next.value !== again.value) return fail("The new passwords differ.", again);
        submit.disabled = true;
        try {
          const result = (await request("POST", "/api/v1/session/password",
            { body: { current_password: current.value, new_password: next.value } })).data;
          setCsrfToken(result.csrf_token);
        } catch (failure) {
          submit.disabled = false;
          return fail(failure.detail || "The password could not be changed.", next);
        }
        close();
        toast("Password changed. Other sessions were signed out.");
      } },
      h("p", { class: "muted" }, "Use a long passphrase you do not use anywhere else. Your other sessions will be signed out."),
      currentField, nextField, againField, error,
      h("div", { class: "row-sm" }, submit, h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel"))));
      current.focus();
    },
  });
}
