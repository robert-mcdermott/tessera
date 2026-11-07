// Tessera web app bootstrap: shell, routes, and global shortcuts.

import { get } from "./core/api.js";
import { h } from "./core/dom.js";
import { applyTheme, currentTheme, THEMES, write } from "./core/prefs.js";
import { fallback, route, start } from "./core/router.js";
import { closeDrawer } from "./components/drawer.js";
import { openPalette } from "./components/palette.js";
import { mountShell } from "./components/shell.js";
import { emptyState } from "./components/ui.js";
import { browse } from "./pages/browse.js";
import { home } from "./pages/home.js";
import { setProduct, setTitle } from "./pages/page.js";
import { person } from "./pages/person.js";
import { grant, publication, study } from "./pages/records.js";
import { searchPage } from "./pages/search.js";
import { sources } from "./pages/sources.js";

applyTheme(currentTheme());

async function boot() {
  const root = document.getElementById("app");
  let meta;
  try {
    meta = (await get("/api/v1/meta")).data;
  } catch {
    root.replaceChildren(h("div", { class: "page" }, emptyState({ iconName: "warning", title: "Tessera is not responding",
      text: "The server could not be reached. Check that `tessera serve` is running, then reload." })));
    return;
  }
  setProduct(meta.product.name);
  const palette = () => openPalette({ actions: [{
    group: "Actions", title: "Toggle theme", iconName: "moon", run: () => {
      const next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
      write("theme", next);
      applyTheme(next);
    },
  }] });
  const { main } = mountShell(root, meta, { onSearch: palette });

  route("/", () => home(main, meta));
  route("/search", (_, ctx) => searchPage(main, ctx));
  route("/people", (_, ctx) => browse(main, "person", ctx));
  route("/people/:id", (params, ctx) => person(main, params, ctx));
  route("/publications", (_, ctx) => browse(main, "publication", ctx));
  route("/publications/:pmid", (params) => publication(main, params));
  route("/grants", (_, ctx) => browse(main, "grant", ctx));
  route("/grants/:core", (params) => grant(main, params));
  route("/studies", (_, ctx) => browse(main, "study", ctx));
  route("/studies/:nct", (params) => study(main, params));
  route("/admin/sources", () => sources(main));
  fallback(() => {
    setTitle("Not found");
    main.replaceChildren(h("div", { class: "page" }, emptyState({ iconName: "search", title: "Page not found",
      text: "That address does not match any page.", action: h("a", { class: "btn", href: "/" }, "Go home") })));
  });

  document.addEventListener("keydown", (event) => {
    const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      closeDrawer();
      palette();
    } else if (event.key === "/" && !typing) {
      event.preventDefault();
      palette();
    }
  });

  await start();
}

boot();
