// Tessera web app bootstrap: shell, routes, and global shortcuts.

import { get, request } from "./core/api.js";
import { h } from "./core/dom.js";
import { applyAppearance, applyTheme, currentTheme, setOrganizationPalette, THEMES, write } from "./core/prefs.js";
import { fallback, route, start } from "./core/router.js";
import { can, setSession } from "./core/session.js";
import { openAppearance } from "./components/appearance.js";
import { closeDrawer } from "./components/drawer.js";
import { openPalette } from "./components/palette.js";
import { mountShell } from "./components/shell.js";
import { emptyState } from "./components/ui.js";
import { accounts } from "./pages/accounts.js";
import { browse } from "./pages/browse.js";
import { candidates } from "./pages/candidates.js";
import { duplicates } from "./pages/duplicates.js";
import { evidence } from "./pages/evidence.js";
import { experts } from "./pages/experts.js";
import { decisions } from "./pages/decisions.js";
import { home } from "./pages/home.js";
import { setProduct, setTitle } from "./pages/page.js";
import { person } from "./pages/person.js";
import { grant, publication, study } from "./pages/records.js";
import { review } from "./pages/review.js";
import { searchPage } from "./pages/search.js";
import { nextPath, signIn } from "./pages/signin.js";
import { sources } from "./pages/sources.js";

applyAppearance();

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
  setOrganizationPalette(meta.product.theme ?? "tessera");

  // Who is signed in. With accounts on and nobody signed in, the app is the sign-in page.
  try {
    setSession((await request("GET", "/api/v1/session")).data);
  } catch (error) {
    if (error.status === 401) {
      if (window.location.pathname !== "/sign-in") {
        const next = window.location.pathname + window.location.search;
        window.history.replaceState(null, "", `/sign-in?next=${encodeURIComponent(next)}`);
      }
      signIn(root, meta);
      return;
    }
    throw error;
  }
  if (window.location.pathname === "/sign-in") window.history.replaceState(null, "", nextPath());
  // A session that ends while the app is open (expired, or signed out elsewhere).
  window.addEventListener("tessera:signed-out", () => {
    const next = window.location.pathname + window.location.search;
    window.location.assign(`/sign-in?next=${encodeURIComponent(next)}`);
  }, { once: true });
  const palette = () => openPalette({ actions: [
    { group: "Actions", title: "Appearance…", iconName: "palette", run: () => openAppearance() },
    { group: "Actions", title: "Switch light or dark", iconName: "moon", run: () => {
      const next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
      write("theme", next);
      applyTheme(next);
    } },
  ] });
  const { main } = mountShell(root, meta, { onSearch: palette });

  route("/", () => home(main, meta));
  route("/search", (_, ctx) => searchPage(main, ctx));
  route("/experts", (_, ctx) => experts(main, ctx));
  // Loaded on first use: the graph views are larger than the rest and not needed at start.
  route("/graph", async (_, ctx) => (await import("./pages/graph.js")).graph(main, ctx));
  const groupPages = () => import("./pages/groups.js");
  route("/groups", async (_, ctx) => (await groupPages()).groupsDirectory(main, ctx));
  route("/groups/:slug", async (params, ctx) => (await groupPages()).groupPage(main, params, ctx));
  route("/people", (_, ctx) => browse(main, "person", ctx));
  route("/people/:id", (params, ctx) => person(main, params, ctx));
  route("/publications", (_, ctx) => browse(main, "publication", ctx));
  route("/publications/:pmid", (params) => publication(main, params));
  route("/grants", (_, ctx) => browse(main, "grant", ctx));
  route("/grants/:core", (params) => grant(main, params));
  route("/studies", (_, ctx) => browse(main, "study", ctx));
  route("/studies/:nct", (params) => study(main, params));
  route("/review", () => review(main));
  route("/review/people", (_, ctx) => candidates(main, ctx));
  route("/review/duplicates", (_, ctx) => duplicates(main, ctx));
  route("/review/log", (_, ctx) => decisions(main, ctx));
  route("/admin/sources", () => sources(main));
  if (can("admin")) {
    route("/admin/accounts", (_, ctx) => accounts(main, ctx));
    // Loaded on first use, like the graph views.
    const groupsPage = () => import("./pages/groups-admin.js");
    route("/admin/groups", async (_, ctx) => (await groupsPage()).groupsAdmin(main, ctx));
    route("/admin/groups/:ref", async (params, ctx) => (await groupsPage()).groupAdmin(main, params, ctx));
  }
  if (can("curate_any")) route("/admin/evidence", (_, ctx) => evidence(main, ctx));
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
