// A table whose column headers sort it, for the list views that stand beside graphs and
// heatmaps. Headers are buttons (keyboard operable) and carry aria-sort.

import { h, icon } from "../core/dom.js";

/** A table whose column headers sort it (keyboard operable, with aria-sort). */
export function sortableTable(columns, rows, { caption, initial }) {
  let key = initial;
  let descending = true;
  let keep = () => true;
  const head = h("tr");
  const tbody = h("tbody");
  const draw = () => {
    const column = columns.find((c) => c.key === key);
    const sorted = rows.filter(keep).sort((a, b) => {
      const x = column.sort(a); const y = column.sort(b);
      const order = x < y ? -1 : x > y ? 1 : 0;
      return descending ? -order : order;
    });
    head.replaceChildren(...columns.map((c) => h("th", { scope: "col", class: c.num ? "num" : null,
      "aria-sort": c.key === key ? (descending ? "descending" : "ascending") : c.sort ? "none" : null },
      c.sort ? h("button", { type: "button", class: "th-sort", onclick: () => {
        descending = c.key === key ? !descending : Boolean(c.num);
        key = c.key;
        draw();
      } }, c.label, c.key === key ? icon(descending ? "chevronDown" : "chevronRight", "icon icon-sm") : null) : h("span", { class: "sr-only" }, "Actions"))));
    tbody.replaceChildren(...sorted.slice(0, 500).map((row) => h("tr", {}, columns.map((c) => h("td", { class: c.num ? "num" : null }, c.cell(row))))));
    return sorted;
  };
  draw();
  return {
    element: h("div", { class: "table-wrap" }, h("table", { class: "table", "data-dense": "" }, h("caption", { class: "sr-only" }, caption), h("thead", {}, head), tbody)),
    filter(fn) { keep = fn; return draw(); },
  };
}
