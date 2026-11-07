// Command palette: search everything, jump anywhere, run actions. Opened with ⌘K / Ctrl-K.

import { get } from "../core/api.js";
import { h, highlighted, icon, plain } from "../core/dom.js";
import { navigate } from "../core/router.js";
import { debounce, entityHref, entityIcon } from "./ui.js";

const KIND_TITLES = { person: "People", publication: "Publications", grant: "Grants", study: "Studies" };
let state = null;

function pages(actions) {
  return [
    { group: "Go to", title: "Home", iconName: "home", run: () => navigate("/") },
    { group: "Go to", title: "People", iconName: "people", run: () => navigate("/people") },
    { group: "Go to", title: "Publications", iconName: "publication", run: () => navigate("/publications") },
    { group: "Go to", title: "Grants", iconName: "grant", run: () => navigate("/grants") },
    { group: "Go to", title: "Studies", iconName: "study", run: () => navigate("/studies") },
    { group: "Go to", title: "Sources and data health", iconName: "sources", run: () => navigate("/admin/sources") },
    { group: "Go to", title: "API reference", iconName: "api", run: () => window.location.assign("/api/docs") },
    ...actions,
  ];
}

export function closePalette() {
  if (!state) return;
  state.backdrop.remove();
  state.dialog.remove();
  document.removeEventListener("keydown", state.onKey, true);
  state.returnFocus?.focus?.();
  state = null;
}

export function openPalette({ actions = [] } = {}) {
  if (state) return;
  const input = h("input", { class: "palette-input", type: "text", placeholder: "Search people, papers, grants, studies…",
    "aria-label": "Search or jump to", autocomplete: "off", spellcheck: "false", role: "combobox",
    "aria-expanded": "true", "aria-controls": "palette-results" });
  const results = h("div", { class: "palette-results", id: "palette-results", role: "listbox" });
  const dialog = h("div", { class: "palette", role: "dialog", "aria-modal": "true", "aria-label": "Command palette" },
    h("div", { class: "palette-input-row" }, icon("search", "icon icon-lg"), input, h("kbd", {}, "esc")),
    results,
    h("div", { class: "palette-footer" },
      h("span", {}, h("kbd", {}, "↑"), " ", h("kbd", {}, "↓"), " to move"),
      h("span", {}, h("kbd", {}, "↵"), " to open"),
      h("span", { class: "spacer" }, "Type to search everything")));
  const backdrop = h("div", { class: "palette-backdrop", onclick: closePalette });
  state = { dialog, backdrop, input, results, items: [], active: 0, returnFocus: document.activeElement,
    staticItems: pages(actions), controller: null };

  state.onKey = (event) => {
    if (event.key === "Escape") { event.preventDefault(); closePalette(); }
    else if (event.key === "ArrowDown") { event.preventDefault(); move(1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); move(-1); }
    else if (event.key === "Enter") { event.preventDefault(); choose(state.items[state.active]); }
  };
  document.addEventListener("keydown", state.onKey, true);
  input.addEventListener("input", debounce(() => update(input.value), 120));
  document.body.append(backdrop, dialog);
  input.focus();
  update("");
}

function move(delta) {
  if (!state.items.length) return;
  state.active = (state.active + delta + state.items.length) % state.items.length;
  paintActive();
}

function paintActive() {
  state.results.querySelectorAll(".palette-item").forEach((node, index) => {
    node.setAttribute("aria-selected", String(index === state.active));
    if (index === state.active) {
      node.scrollIntoView({ block: "nearest" });
      state.input.setAttribute("aria-activedescendant", node.id);
    }
  });
}

function choose(item) {
  if (!item) return;
  closePalette();
  item.run();
}

async function update(text) {
  const q = text.trim();
  const needle = q.toLowerCase();
  const local = state.staticItems.filter((item) => !needle || item.title.toLowerCase().includes(needle));
  let remote = [];
  if (q.length >= 2) {
    state.controller?.abort();
    state.controller = new AbortController();
    try {
      const data = (await get("/api/v1/search", { q, per_type: 4 }, { signal: state.controller.signal })).data;
      for (const kind of ["person", "publication", "grant", "study"]) {
        for (const item of data[kind]?.items ?? []) {
          remote.push({
            group: `${KIND_TITLES[kind]} (${data[kind].total})`,
            kind,
            title: item.title,
            titleNode: highlighted(item.highlights?.title ?? item.title),
            sub: item.subtitle ?? (item.year ? String(item.year) : ""),
            run: () => navigate(entityHref(kind, kind === "person" ? item.ref : item.ref)),
          });
        }
        if ((data[kind]?.total ?? 0) > 4) {
          remote.push({ group: `${KIND_TITLES[kind]} (${data[kind].total})`, title: `See all ${data[kind].total} ${KIND_TITLES[kind].toLowerCase()}`,
            iconName: "arrowRight", run: () => navigate(`/search?q=${encodeURIComponent(q)}&type=${kind}`) });
        }
      }
    } catch (error) {
      if (error.name === "AbortError" || !state) return;
      remote = [{ group: "Search", title: error.detail || "Search is unavailable", iconName: "warning", run: () => {} }];
    }
  }
  if (!state) return;
  const items = [...remote, ...(q ? [{ group: "Search", title: `Search for “${q}”`, iconName: "search",
    run: () => navigate(`/search?q=${encodeURIComponent(q)}`) }] : []), ...local];
  state.items = items;
  state.active = 0;
  render(items);
}

function render(items) {
  if (!items.length) {
    state.results.replaceChildren(h("div", { class: "palette-empty" }, "No matches."));
    return;
  }
  const out = [];
  let group = null;
  items.forEach((item, index) => {
    if (item.group !== group) {
      group = item.group;
      out.push(h("div", { class: "palette-group-title", role: "presentation" }, group));
    }
    out.push(h("div", {
      class: "palette-item", role: "option", id: `palette-item-${index}`, "aria-selected": String(index === 0),
      onclick: () => choose(item), onmousemove: () => { if (state.active !== index) { state.active = index; paintActive(); } },
    },
    item.kind ? entityIcon(item.kind) : h("span", { class: "entity-icon", "data-entity": "unit" }, icon(item.iconName ?? "arrowRight")),
    h("div", { class: "palette-item-text" },
      h("div", { class: "palette-item-title", title: plain(item.title) }, item.titleNode ?? item.title),
      item.sub ? h("div", { class: "palette-item-sub" }, item.sub) : null)));
  });
  state.results.replaceChildren(...out);
}
