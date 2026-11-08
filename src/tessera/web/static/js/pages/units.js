// Units: how alike each pair of units' interests are, and how much their members work together,
// as a heatmap (shade: interest; inner square: collaboration) and as a sortable list. Every
// cell opens the pairs of people behind it, in one request.

import { get } from "../core/api.js";
import { h, icon, svg } from "../core/dom.js";
import { number } from "../core/format.js";
import { setQuery } from "../core/router.js";
import { sortableTable } from "../components/sortable-table.js";
import { card, emptyState } from "../components/ui.js";

const key = (a, b) => (a < b ? `${a}\u0000${b}` : `${b}\u0000${a}`);

/** Axis labels without the words every unit name starts with ("Department of"). */
function shortNames(names) {
  const words = names.map((n) => n.split(" "));
  let common = 0;
  while (names.length > 1 && words.every((w) => w.length > common + 1 && w[common] === words[0][common])) common++;
  return new Map(names.map((n, i) => [n, words[i].slice(common).join(" ")]));
}

export function unitsContent(state, body) {
  const { units, cells } = body.data;
  if (units.length < 2) {
    return [card({ body: emptyState({ iconName: "unit", title: "Units appear with memberships",
      text: "This compares units once at least two of them have two or more members. Import a roster with a unit column to add memberships." }) })];
  }
  const p = state.params;
  const byKey = new Map(cells.map((c) => [key(c.a, c.b), c]));
  const short = shortNames(units.map((u) => u.name));
  const side = h("aside", { class: "graph-side", "aria-live": "polite" });
  const maxSim = Math.max(...cells.map((c) => c.similarity), 1e-9);
  const maxCol = Math.max(...cells.map((c) => c.collaboration), 1e-9);

  const describe = (c) => `${c.a === c.b ? `Within ${c.a}` : `${c.a} and ${c.b}`}: ${number(c.similar_pairs)} of ${number(c.possible)} possible pairs alike (interest ${c.similarity.toFixed(2)}), ${number(c.collaborating_pairs)} working together (${c.collaboration.toFixed(2)}), ${number(c.missed)} missed connections`;

  async function open(c) {
    side.replaceChildren(h("h2", {}, c.a === c.b ? short.get(c.a) : `${short.get(c.a)} and ${short.get(c.b)}`),
      h("p", { class: "muted" }, describe(c)), h("p", { class: "subtle" }, "Loading the pairs…"));
    let pairs;
    try {
      pairs = (await get("/api/v1/graph/units/pairs", { a: c.a, b: c.b })).data;
    } catch {
      side.lastChild.textContent = "The pairs could not be loaded.";
      return;
    }
    side.lastChild.replaceWith(pairs.length
      ? h("ul", { class: "plain-list side-list pair-list" }, pairs.slice(0, 60).map(pairItem))
      : h("p", { class: "subtle" }, "No one here is alike or works together yet."));
  }

  const legend = () => side.replaceChildren(
    h("h2", {}, "Reading this matrix"),
    h("ul", { class: "legend" },
      h("li", {}, h("span", { class: "legend-swatch swatch-interest", "aria-hidden": "true" }), h("span", {}, "Shade: how alike their members' interests are, per possible pair")),
      h("li", {}, h("span", { class: "legend-swatch swatch-collab", "aria-hidden": "true" }), h("span", {}, "Inner square: how much they work together, per possible pair"))),
    h("p", { class: "muted" }, "A strong shade with a small square marks units that share interests but rarely work together. Units are ordered by how alike their members are to everyone else's."),
    h("p", { class: "subtle" }, "Select a cell, or use the arrow keys and Enter, to see the people behind it."));
  legend();

  if (state.list) {
    const pairsCard = h("div");
    const table = sortableTable([
      { key: "units", label: "Units", sort: (c) => `${c.a} ${c.b}`, cell: (c) => (c.a === c.b ? `Within ${short.get(c.a)}` : `${short.get(c.a)} and ${short.get(c.b)}`) },
      { key: "possible", label: "Possible pairs", num: true, sort: (c) => c.possible, cell: (c) => number(c.possible) },
      { key: "alike", label: "Alike", num: true, sort: (c) => c.similarity, cell: (c) => `${number(c.similar_pairs)} (${c.similarity.toFixed(2)})` },
      { key: "together", label: "Working together", num: true, sort: (c) => c.collaboration, cell: (c) => `${number(c.collaborating_pairs)} (${c.collaboration.toFixed(2)})` },
      { key: "missed", label: "Missed", num: true, sort: (c) => c.missed, cell: (c) => number(c.missed) },
      { key: "actions", label: "", cell: (c) => h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: async () => {
        await open(c);
        pairsCard.replaceChildren(card({ pad: true, body: h("div", { class: "stack" }, ...side.children) }));
        pairsCard.scrollIntoView({ block: "nearest" });
      } }, "Show pairs") },
    ], cells, { caption: "Unit pairs", initial: "alike" });
    return [controls(p, body), card({ title: `Unit pairs (${number(cells.length)})`, body: table.element }), pairsCard];
  }

  const n = units.length;
  const size = Math.max(18, Math.min(40, Math.floor(560 / n)));
  const left = 8 + Math.min(220, 7 * Math.max(...[...short.values()].map((s) => s.length)));
  const top = Math.round(left * 0.75);
  const width = left + n * size + Math.round(top * 0.9); // room for the last rotated label
  const height = top + n * size + 12;
  const cursor = svg("rect", { class: "cell-selected", width: size, height: size, visibility: "hidden" });
  const grid = svg("g", {});
  let at = null;
  const select = (i, j, openIt = true) => {
    at = [i, j];
    cursor.setAttribute("x", left + j * size);
    cursor.setAttribute("y", top + i * size);
    cursor.setAttribute("visibility", "visible");
    if (openIt) open(byKey.get(key(units[i].name, units[j].name)));
  };
  units.forEach((row, i) => {
    grid.append(svg("text", { class: "matrix-label", x: left - 8, y: top + i * size + size / 2, "text-anchor": "end", "dominant-baseline": "middle" },
      svg("title", {}, `${row.name} (${row.members} members)`), short.get(row.name)));
    grid.append(svg("text", { class: "matrix-label", transform: `translate(${left + i * size + size / 2} ${top - 8}) rotate(-45)` },
      svg("title", {}, `${row.name} (${row.members} members)`), short.get(row.name)));
    units.forEach((col, j) => {
      const c = byKey.get(key(row.name, col.name));
      const x = left + j * size;
      const y = top + i * size;
      const inner = c.collaboration > 0 ? Math.max(3, Math.sqrt(c.collaboration / maxCol) * (size - 8)) : 0;
      grid.append(svg("g", { class: "cell", "data-i": i, "data-j": j },
        svg("title", {}, describe(c)),
        svg("rect", { class: "cell-bg", x, y, width: size, height: size }),
        c.similarity > 0 ? svg("rect", { class: "cell-interest", x: x + 1, y: y + 1, width: size - 2, height: size - 2,
          "fill-opacity": (0.12 + 0.88 * (c.similarity / maxSim)).toFixed(3) }) : null,
        inner ? svg("rect", { class: "cell-collab", x: x + (size - inner) / 2, y: y + (size - inner) / 2, width: inner, height: inner }) : null));
    });
  });
  const matrix = svg("svg", { class: "unit-matrix", viewBox: `0 0 ${width} ${height}`, width, height, tabindex: "0", role: "img",
    "aria-label": `Units by units: ${n} units. Arrow keys move between cells, Enter shows the pairs. The List view has the same information.` },
  grid, cursor);
  matrix.addEventListener("click", (event) => {
    const cell = event.target.closest(".cell");
    if (cell) select(Number(cell.dataset.i), Number(cell.dataset.j));
  });
  matrix.addEventListener("keydown", (event) => {
    const moves = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    if (moves[event.key]) {
      const [i, j] = at ?? [0, -1];
      select(Math.min(n - 1, Math.max(0, i + moves[event.key][0])), Math.min(n - 1, Math.max(0, j + moves[event.key][1])), false);
    } else if (event.key === "Enter" && at) {
      select(...at);
    } else if (event.key === "Escape") {
      at = null;
      cursor.setAttribute("visibility", "hidden");
      legend();
    } else {
      return;
    }
    event.preventDefault();
  });
  return [controls(p, body),
    h("div", { class: "graph-layout" }, h("div", { class: "matrix-wrap" }, matrix), side)];
}

function controls(p, body) {
  return h("div", { class: "graph-controls" },
    h("label", { class: "graph-control" }, h("span", {}, "Units"),
      h("select", { class: "input", onchange: (event) => setQuery({ max: event.target.value }, { replace: false }) },
        [[10, "Largest 10"], [20, "Largest 20"], [30, "Largest 30"], [60, "Largest 60"]].map(([v, text]) =>
          h("option", { value: v, selected: String(v) === String(p.get("max") ?? 30) }, text)))),
    h("span", { class: "subtle" }, `${number(body.data.units.length)} of ${number(body.meta.available)} units with two or more members`));
}

function pairItem(x) {
  const note = [
    x.score != null ? `alike ${x.score.toFixed(2)}${x.shared.length ? `: ${x.shared.slice(0, 3).map((s) => s.descriptor).join(", ")}` : ""}` : null,
    x.together ? `worked together (${number(x.together.papers)} ${x.together.papers === 1 ? "paper" : "papers"}${x.together.grants ? `, ${number(x.together.grants)} grants` : ""})` : null,
  ].filter(Boolean).join("; ");
  return h("li", { class: x.together ? null : "is-missed" },
    h("div", { class: "stack-xs grow" },
      h("span", {}, h("a", { href: `/people/${x.a.id}` }, x.a.name), " and ", h("a", { href: `/people/${x.b.id}` }, x.b.name)),
      h("span", { class: "subtle" }, note)),
    x.together ? null : h("span", { class: "pill", title: "Alike, but no shared papers, grants, or studies" }, icon("link", "icon icon-sm"), "Not yet"));
}
