// The app shell: sidebar navigation, top bar, theme toggle, and mobile menu.

import { get } from "../core/api.js";
import { h, icon, iconButton } from "../core/dom.js";
import { compact } from "../core/format.js";
import { applyTheme, currentTheme, THEMES, write } from "../core/prefs.js";
import { onNavigate } from "../core/router.js";
import { avatar } from "./ui.js";

const NAV = [
  { section: null, items: [
    { href: "/", label: "Home", iconName: "home", match: (p) => p === "/" },
    { href: "/search", label: "Search", iconName: "search", match: (p) => p.startsWith("/search") },
  ] },
  { section: "Explore", items: [
    { href: "/people", label: "People", iconName: "people", match: (p) => p.startsWith("/people") },
    { href: "/publications", label: "Publications", iconName: "publication", match: (p) => p.startsWith("/publications") },
    { href: "/grants", label: "Grants", iconName: "grant", match: (p) => p.startsWith("/grants") },
    { href: "/studies", label: "Studies", iconName: "study", match: (p) => p.startsWith("/studies") },
  ] },
  { section: "Curate", items: [
    { href: "/review", label: "Review queue", iconName: "review", badge: "review", match: (p) => p === "/review" },
    { href: "/review/people", label: "Discovered people", iconName: "userPlus", badge: "candidates", match: (p) => p.startsWith("/review/people") },
    { href: "/review/log", label: "Decision log", iconName: "history", match: (p) => p.startsWith("/review/log") },
  ] },
  { section: "Admin", items: [
    { href: "/admin/sources", label: "Sources", iconName: "sources", match: (p) => p.startsWith("/admin/sources") },
    { href: "/api/docs", label: "API reference", iconName: "api", match: () => false, external: true },
  ] },
];

const THEME_ICONS = { system: "system", light: "sun", dark: "moon" };

export function mountShell(root, meta, { onSearch }) {
  const product = meta.product;
  const org = meta.organization;
  const navItems = [];
  const sidebar = h("nav", { class: "sidebar", "aria-label": "Main" },
    h("a", { class: "brand", href: "/" },
      h("img", { class: "brand-mark", src: "/img/tessera-mark.svg", alt: "", width: 26, height: 26 }),
      h("span", { class: "brand-text" }, h("span", {}, product.name),
        h("span", { class: "brand-org" }, org.short_name || org.name))),
    NAV.map((group) => h("div", { class: "nav" },
      group.section ? h("div", { class: "nav-section" }, group.section) : null,
      group.items.map((item) => {
        const badge = item.badge ? h("span", { class: "badge", hidden: true }) : null;
        const node = h("a", { class: "nav-item", href: item.href, title: item.label },
          icon(item.iconName), h("span", { class: "nav-label" }, item.label), badge);
        navItems.push({ node, item, badge });
        return node;
      }))),
    h("div", { class: "sidebar-footer" },
      h("span", {}, org.name),
      product.show_powered_by
        ? h("span", {}, "Powered by ", h("a", { href: "/api/docs" }, `Tessera ${product.version}`))
        : null));

  const themeButton = iconButton(THEME_ICONS[currentTheme()], `Theme: ${currentTheme()}`, {
    onclick: () => {
      const next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
      write("theme", next);
      applyTheme(next);
      themeButton.replaceChildren(icon(THEME_ICONS[next]));
      themeButton.setAttribute("aria-label", `Theme: ${next}`);
      themeButton.title = `Theme: ${next}`;
    },
  });

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
      themeButton,
      h("span", { class: "operator", title: "Single-operator mode: decisions are recorded under this name" },
        avatar(meta.mode.operator, "sm"), h("span", { class: "operator-name" }, meta.mode.operator))));

  const main = h("main", { class: "shell-main", id: "main" });
  const shell = h("div", { class: "shell" }, sidebar, topbar, main);
  root.replaceChildren(shell);

  // Work waiting for a curator, shown beside Review queue and Discovered people. Refreshed
  // after every change anywhere in the app.
  let pending = null;
  const refreshCounts = () => {
    clearTimeout(pending);
    pending = setTimeout(async () => {
      const [queue, found] = await Promise.all([
        get("/api/v1/review", { limit: 1 }, { fresh: true }).catch(() => null),
        get("/api/v1/candidates", { limit: 1 }, { fresh: true }).catch(() => null),
      ]);
      const counts = {
        review: queue ? (queue.meta.bands?.strong ?? 0) + (queue.meta.bands?.possible ?? 0) : null,
        candidates: found ? found.meta.total : null,
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
