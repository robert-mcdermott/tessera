// Missed connections: people whose interests are alike but who have never published, held a
// grant, or run a study together, with what they share and who could introduce them.

import { h, icon } from "../core/dom.js";
import { number } from "../core/format.js";
import { setQuery } from "../core/router.js";
import { avatar, card, emptyState, pager } from "../components/ui.js";

export function missedContent(state, body) {
  const p = state.params;
  const units = body.meta.units ?? [];
  const select = (label, name, options, value) => h("label", { class: "graph-control" }, h("span", {}, label),
    h("select", { class: "input", onchange: (event) => setQuery({ [name]: event.target.value || null, offset: null }, { replace: false }) },
      options.map(([v, text]) => h("option", { value: v, selected: String(v) === String(value) }, text))));
  const controls = h("div", { class: "graph-controls" },
    units.length ? select("Unit", "unit", [["", "Everyone"], ...units.map((u) => [u.value, u.value])], p.get("unit") ?? "") : null,
    units.length ? h("label", { class: "graph-control graph-check" },
      h("input", { type: "checkbox", checked: p.get("across") === "1",
        onchange: (event) => setQuery({ across: event.target.checked ? "1" : null, offset: null }, { replace: false }) }),
      h("span", {}, "Only between different units")) : null,
    select("How alike", "min", [[0.15, "Similarity 0.15 or more"], [0.25, "0.25 or more"], [0.35, "0.35 or more"], [0.5, "0.5 or more"]], p.get("min") ?? 0.25));

  const intro = h("p", { class: "muted measure" },
    "People whose interests are alike, from the topics of their papers, but who have not published, held a grant, or run a study together. The pairs most alike come first; where someone has worked with both, they are named as a possible introduction.");
  if (!body.data.length) {
    return [controls, intro, card({ body: emptyState({ iconName: "people", title: "No missed connections here",
      text: "Everyone this alike already works together. Try a lower similarity, or another unit." }) })];
  }
  const offset = Number(body.meta.offset) || 0;
  const items = h("div", { class: "items" }, body.data.map((item) => {
    const meter = h("span", { "data-w": String(Math.max(6, Math.round(item.score * 100))) });
    return h("div", { class: "item missed" },
      h("div", { class: "missed-pair" }, person(item.a), h("span", { class: "missed-link", "aria-hidden": "true" }, icon("link", "icon icon-sm")), person(item.b)),
      h("div", { class: "item-main" },
        h("span", { class: "subtle" }, "They share"),
        h("div", { class: "chips" }, item.shared.map((s) => h("a", { class: "chip", "data-entity": "concept",
          href: `/experts?concept=${s.descriptor_ui}`, title: `Find others who work on ${s.descriptor}` }, s.descriptor))),
        item.introducers.length ? h("p", { class: "subtle" }, "Worked with both: ",
          ...item.introducers.flatMap((x, i) => [i ? ", " : null, h("a", { href: `/people/${x.id}` }, x.name)])) : null),
      h("div", { class: "item-side" },
        h("div", { class: "expert-meter", role: "img", "aria-label": `similarity ${item.score.toFixed(2)}` }, meter),
        h("span", { class: "subtle tabular" }, `alike ${item.score.toFixed(2)}`)));
  }));
  for (const bar of items.querySelectorAll(".expert-meter > span")) bar.style.setProperty("width", `${bar.dataset.w}%`);
  return [controls, intro,
    card({ title: `Pairs (${number(body.meta.total)})`, body: h("div", {}, items,
      pager({ total: body.meta.total, offset, limit: body.meta.limit, onPage: (o) => setQuery({ offset: o || null }, { replace: false }) })) })];
}

function person(p) {
  return h("div", { class: "missed-person" }, avatar(p.name, "sm"),
    h("div", { class: "stack-xs" }, h("a", { class: "item-title", href: `/people/${p.id}` }, p.name),
      p.units.length ? h("span", { class: "subtle" }, p.units.join(", ")) : null));
}
