// Find experts: "who works on X?", with the concepts behind every answer. A query in words is
// read through the papers it finds, and the concepts it became are shown, so the reader can
// drop one, add one, or start from a concept directly.

import { get } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { number } from "../core/format.js";
import { query, queryList, setQuery } from "../core/router.js";
import { avatar, card, debounce, emptyState, pageHeader } from "../components/ui.js";
import { show } from "./page.js";

const EXAMPLES = ["pancreatic cancer", "antibiotic resistance", "single-cell sequencing", "sleep"];

export function experts(main, ctx = {}) {
  const q = query().get("q") ?? "";
  const chosen = queryList("concept");
  const asked = Boolean(q || chosen.length);
  return show(main, {
    title: q ? `Experts: ${q}` : "Find experts",
    quiet: ctx.samePage,
    load: async () => (asked ? get("/api/v1/experts", { q: q || null, concept: chosen, limit: 25 }) : null),
    view: (body) => {
      const input = h("input", { class: "input", type: "search", value: q, autocomplete: "off",
        placeholder: "A topic, a method, a disease…", "aria-label": "Who works on" });
      const form = h("form", { class: "search-field toolbar experts-search", role: "search", onsubmit: (event) => {
        event.preventDefault();
        const text = input.value.trim();
        setQuery({ q: text || null, concept: null }, { replace: false });
      } }, icon("search"), input, h("button", { type: "submit", class: "btn btn-primary" }, "Find"));
      const header = pageHeader({ eyebrow: "Explore", title: "Find experts",
        meta: h("span", {}, "Who works on what, ranked by the topics of their papers, with the reasons shown.") });

      if (!asked) {
        return h("div", { class: "stack-lg" }, header, form,
          card({ body: emptyState({ iconName: "compass", title: "Who works on…?",
            text: "Describe a topic in your own words. Tessera reads it through the papers it finds and ranks people by the concepts they share.",
            action: h("div", { class: "chips" }, EXAMPLES.map((e) => h("a", { class: "chip chip-plain", href: `/experts?q=${encodeURIComponent(e)}` }, e))) }) }));
      }
      if (!body.meta.ready) {
        return h("div", { class: "stack-lg" }, header, form, card({ body: emptyState({ iconName: "info", title: "Not ready yet", text: body.meta.note }) }));
      }
      const concepts = body.meta.concepts;
      const ids = concepts.map((c) => c.descriptor_ui);
      const refine = conceptBar(concepts, ids);
      const top = body.data[0]?.score || 1;
      const results = body.data.length
        ? h("div", { class: "items" }, body.data.map((p) => h("div", { class: "item expert" },
          avatar(p.name),
          h("div", { class: "item-main" },
            h("a", { class: "item-title", href: `/people/${p.id}` }, p.name),
            p.units.length ? h("div", { class: "item-meta" }, p.units.join(", ")) : null,
            h("div", { class: "chips" }, p.matches.map((m) => h("a", {
              class: "chip", "data-entity": "concept", href: `/experts?concept=${m.descriptor_ui}`,
              title: `${number(m.papers)} of their papers` }, m.descriptor)))),
          h("div", { class: "item-side" },
            h("div", { class: "expert-meter", role: "img", "aria-label": `match ${Math.round((p.score / top) * 100)} of 100` },
              h("span", { "data-w": String(Math.max(6, Math.round((p.score / top) * 100))) })),
            h("span", { class: "subtle tabular" }, `match ${p.score.toFixed(2)}`)))))
        : emptyState({ iconName: "people", title: "No one matches", text: "Try broader words, or remove a concept." });
      const view = h("div", { class: "stack-lg" }, header, form, refine,
        card({ title: `People (${number(body.data.length)})`, body: results }),
        h("p", { class: "subtle" }, "Similarity reflects the topics of people's papers, weighted toward recent and confirmed work. It says nothing about quality or availability."),
        body.meta.mesh_year ? h("p", { class: "subtle" }, `Headings, synonyms, and hierarchy: MeSH ${body.meta.mesh_year}, from the U.S. National Library of Medicine. A broad heading includes the narrower ones beneath it.`) : null);
      // Meter widths through the CSSOM (the content security policy allows no inline styles).
      for (const bar of view.querySelectorAll(".expert-meter > span")) bar.style.setProperty("width", `${bar.dataset.w}%`);
      return view;
    },
  });
}

/** The concepts a query became: remove one, or add one by name. */
function conceptBar(concepts, ids) {
  const remove = (ui) => setQuery({ q: null, concept: ids.filter((x) => x !== ui) }, { replace: false });
  const options = h("datalist", { id: "concept-options" });
  const adder = h("input", { class: "input", list: "concept-options", placeholder: "Add a concept…", "aria-label": "Add a concept",
    autocomplete: "off",
    oninput: debounce(async () => {
      const text = adder.value.trim();
      if (text.length < 2) return;
      const found = (await get("/api/v1/concepts", { q: text })).data;
      options.replaceChildren(...found.map((c) => h("option", { value: c.descriptor }, `${c.descriptor} (${number(c.people)} people)`)));
      adder.dataset.found = JSON.stringify(found);
    }, 200),
    onchange: () => {
      const found = JSON.parse(adder.dataset.found || "[]");
      const pick = found.find((c) => c.descriptor.toLowerCase() === adder.value.trim().toLowerCase());
      if (pick) setQuery({ q: null, concept: [...new Set([...ids, pick.descriptor_ui])] }, { replace: false });
    } });
  return h("div", { class: "concept-bar" },
    h("span", { class: "muted" }, concepts.length ? "Understood as" : "No concepts matched"),
    h("div", { class: "chips" }, concepts.map((c) => h("span", { class: "chip", "data-entity": "concept" },
      h("span", { title: c.via ? `From “${c.via}”, a synonym` : null }, c.descriptor, c.via ? h("span", { class: "chip-via" }, ` · ${c.via}`) : null),
      h("button", { type: "button", class: "chip-remove", "aria-label": `Remove ${c.descriptor}`, onclick: () => remove(c.descriptor_ui) }, "×")))),
    h("div", { class: "concept-add" }, adder, options));
}
