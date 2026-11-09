// An NIH biosketch: the Biographical Sketch Common Form and the NIH Supplement, written and
// chosen here, checked against NIH's limits as it is written, and given section by section to
// paste into SciENcv, where the investigator completes and certifies it.

import { post } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { number } from "../core/format.js";
import { slot } from "../core/slots.js";
import { toast } from "../components/toast.js";
import { card } from "../components/ui.js";

// --- the preview (what prints), in NIH's order ------------------------------------------------------

function citation(p) {
  if (p.manual) return h("li", {}, p.text);
  return h("li", {}, p.authors.flatMap((a, n) => [n ? ", " : "", a.self ? h("strong", {}, a.name) : a.name]),
    p.authors.length ? ". " : "", p.rest);
}

function paper(data) {
  const part = Object.fromEntries(data.sections.map((s) => [s.id, s]));
  const rows = (s, cells) => s.rows.length ? h("table", { class: "bio-table" }, h("tbody", {}, s.rows.map((r) => h("tr", {}, cells(r).map((c) => h("td", {}, c))))))
    : h("p", { class: "subtle" }, "None included.");
  return h("article", { class: "cv-paper bio-paper", "aria-label": "The biosketch as it prints" },
    h("header", { class: "cv-head" }, h("h1", {}, data.header.name), h("p", {}, `NIH biosketch · ${data.document.name}`)),
    h("h2", { class: "bio-form" }, "Biographical Sketch Common Form"),
    h("section", { class: "cv-section" }, h("h3", {}, part.identity.title),
      h("dl", { class: "bio-identity" }, part.identity.rows.flatMap((r) => [h("dt", {}, r.label), h("dd", {}, r.value || "—")]))),
    h("section", { class: "cv-section" }, h("h3", {}, part.preparation.title),
      rows(part.preparation, (r) => [[r.institution, r.location].filter(Boolean).join(", "), r.degree, [r.start, r.end].filter(Boolean).join(" – "), r.field])),
    h("section", { class: "cv-section" }, h("h3", {}, part.positions.title),
      rows(part.positions, (r) => [[r.start, r.end].filter(Boolean).join(" – "), r.title, [r.organization, r.location].filter(Boolean).join(", ")])),
    h("section", { class: "cv-section" }, h("h3", {}, part.products.title),
      h("h4", {}, "Products closely related to the proposed project"),
      part.products.related.length ? h("ol", { class: "cv-citations" }, part.products.related.map(citation)) : h("p", { class: "subtle" }, "None chosen."),
      h("h4", {}, "Other significant products"),
      part.products.other.length ? h("ol", { class: "cv-citations" }, part.products.other.map(citation)) : h("p", { class: "subtle" }, "None chosen.")),
    h("section", { class: "cv-section" }, h("h3", {}, part.certification.title), h("p", { class: "subtle" }, part.certification.text)),
    h("h2", { class: "bio-form" }, "NIH Biographical Sketch Supplement"),
    h("section", { class: "cv-section" }, h("h3", {}, `A. ${part.statement.title}`),
      part.statement.text ? h("div", { class: "bio-text" }, part.statement.text) : h("p", { class: "subtle" }, "Not written yet.")),
    h("section", { class: "cv-section" }, h("h3", {}, `B. ${part.honors.title}`),
      rows(part.honors, (r) => [r.year, r.title, r.organization])),
    h("section", { class: "cv-section" }, h("h3", {}, `C. ${part.contributions.title}`),
      part.contributions.items.length ? h("ol", { class: "bio-contributions" }, part.contributions.items.map((c) => h("li", { class: "bio-text" }, c)))
        : h("p", { class: "subtle" }, "None written yet.")));
}

function checksBox(data) {
  if (!data.checks.length) {
    return h("div", { class: "callout no-print", role: "status" }, icon("check", "icon icon-sm"),
      h("div", {}, "Nothing NIH would refuse here. Complete and certify it in SciENcv."));
  }
  return h("div", { class: "callout callout-warn no-print", role: "status" }, icon("warning", "icon icon-sm"),
    h("div", { class: "stack-xs" }, h("strong", {}, `${data.checks.length === 1 ? "One thing" : `${number(data.checks.length)} things`} to fix before SciENcv`),
      h("ul", { class: "bio-checks" }, data.checks.map((c) => h("li", {}, c.message)))));
}

function forSciencv(data) {
  return card({ title: "Copy for SciENcv", pad: true, attrs: { class: "card no-print" }, body: h("div", { class: "stack" },
    h("p", { class: "subtle" }, `${data.form_label}. NIH requires the investigator to complete and certify both forms in SciENcv; paste each section there.`),
    data.sciencv.map((s) => h("div", { class: "bio-copy" },
      h("div", { class: "row-sm" }, h("strong", { class: "grow" }, s.title),
        h("button", { type: "button", class: "btn btn-sm", onclick: async () => {
          try {
            await navigator.clipboard.writeText(s.text);
            toast(`${s.title}: copied.`);
          } catch {
            toast("Copying is not allowed here; select the text instead.");
          }
        } }, "Copy")),
      h("pre", { class: "bio-pre" }, s.text || "(empty)")))) });
}

// --- the editor ------------------------------------------------------------------------------------

function counter(textarea, limit) {
  const out = h("span", { class: "bio-count", "aria-live": "polite" });
  const update = () => {
    const n = textarea.value.trim().length;
    out.textContent = `${number(n)} of ${number(limit)} characters${n > limit ? `, ${number(n - limit)} over` : ""}`;
    out.classList.toggle("is-over", n > limit);
  };
  textarea.addEventListener("input", update);
  update();
  return out;
}

function checklist({ options, chosen, most, onChange, filter = false }) {
  const picked = chosen === null ? options.map((o) => o.id) : [...chosen];
  const count = h("span", { class: "bio-count" });
  const show = () => {
    count.textContent = most ? `${picked.length} of ${most}` : `${picked.length} of ${options.length}`;
    count.classList.toggle("is-over", Boolean(most) && picked.length > most);
  };
  const items = options.map((o) => h("li", {}, h("label", { class: "check" }, h("input", { type: "checkbox", checked: picked.includes(o.id), onchange: (event) => {
    if (event.currentTarget.checked) picked.push(o.id); else picked.splice(picked.indexOf(o.id), 1);
    show();
    onChange(picked.length === options.length && !most ? null : [...picked]);
  } }), o.label)));
  const list = h("ul", { class: "cv-ed-entries" }, items);
  const search = filter ? h("input", { class: "input", type: "search", placeholder: "Filter by words or year", "aria-label": "Filter papers", oninput: (event) => {
    const q = event.currentTarget.value.toLowerCase();
    options.forEach((o, n) => { items[n].hidden = Boolean(q) && !o.label.toLowerCase().includes(q); });
  } }) : null;
  show();
  return h("div", { class: "stack-xs" }, search, list, count);
}

function block(title, hint, ...children) {
  return h("li", { class: "cv-ed-section is-on" }, h("div", { class: "stack-xs" }, h("strong", {}, title), hint ? h("span", { class: "subtle" }, hint) : null), h("div", { class: "cv-ed-body" }, children));
}

function editor(data, { save, saveSoon, personId, docId }) {
  const spec = structuredClone(data.document.spec);
  const limits = data.limits;
  const now = () => save(spec);
  const later = () => saveSoon(spec);
  const field = (key, label) => {
    const input = h("input", { class: "input", id: `bio-${key}`, value: spec[key] ?? "", maxlength: 300,
      oninput: (event) => { spec[key] = event.currentTarget.value; later(); } });
    return h("div", { class: "field" }, h("label", { for: `bio-${key}` }, label), input);
  };
  const products = (key, label) => {
    const papers = spec[key].filter((p) => p.pmid).map((p) => p.pmid);
    const typed = spec[key].filter((p) => p.text);
    const other = h("input", { class: "input", placeholder: "A product that is not a paper: its citation", "aria-label": `${label}: add one that is not a paper` });
    return block(label, `Up to ${limits[key]}: confirmed papers, or another product's citation.`,
      checklist({ options: data.options.publications, chosen: papers, most: limits[key], filter: true, onChange: (ids) => {
        spec[key] = [...ids.map((pmid) => ({ pmid })), ...typed];
        now();
      } }),
      typed.length ? h("ul", { class: "plain-list" }, typed.map((t) => h("li", { class: "row-sm" }, h("span", { class: "grow" }, t.text),
        h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => { spec[key] = spec[key].filter((x) => x !== t); now(); } }, "Remove")))) : null,
      h("form", { class: "row-sm", onsubmit: (event) => {
        event.preventDefault();
        if (!other.value.trim()) return;
        spec[key] = [...spec[key], { text: other.value.trim() }];
        now();
      } }, other, h("button", { type: "submit", class: "btn btn-sm", "aria-label": `Add to ${label.toLowerCase()}` }, "Add")));
  };
  const statement = h("textarea", { class: "input bio-area", id: "bio-statement", rows: 10, "aria-label": "Personal statement", "aria-describedby": "bio-statement-hint",
    oninput: (event) => { spec.statement = event.currentTarget.value; later(); } }, spec.statement);
  const insertInto = (textarea, assign) => (text) => {
    textarea.value = text;
    textarea.dispatchEvent(new Event("input"));
    assign(text);
    toast("A first draft is in: read it and edit it; it is saved as you type.");
  };
  const contributions = spec.contributions.map((text, n) => {
    const area = h("textarea", { class: "input bio-area", rows: 6, "aria-label": `Contribution ${n + 1}`,
      oninput: (event) => { spec.contributions[n] = event.currentTarget.value; later(); } }, text);
    return h("div", { class: "stack-xs bio-contribution" },
      h("div", { class: "row-sm" }, h("strong", { class: "grow" }, `Contribution ${n + 1}`),
        h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => { spec.contributions.splice(n, 1); now(); } }, "Remove")),
      area, h("div", { class: "row-sm wrap" }, counter(area, limits.contribution),
        ...slot("biosketch:draft", { part: "contribution", personId, docId, papers: data.options.publications,
          insert: insertInto(area, (t) => { spec.contributions[n] = t; }) })));
  });
  return card({ title: "What it says", pad: true, attrs: { class: "card cv-editor no-print" }, body: h("ul", { class: "plain-list cv-ed-sections" },
    block("Identifying information", data.header.orcid ? `ORCID iD ${data.header.orcid}` : "No ORCID iD on the profile; NIH requires one.",
      field("position_title", "Position title"), field("organization", "Organization and location")),
    block("Professional preparation", "Education and training, from the profile.",
      checklist({ options: data.options.education, chosen: spec.preparation, onChange: (ids) => { spec.preparation = ids; now(); } })),
    block("Appointments and positions", "From the group directory and the profile.",
      checklist({ options: data.options.position, chosen: spec.positions, onChange: (ids) => { spec.positions = ids; now(); } })),
    products("products_related", "Products closely related to the project"),
    products("products_other", "Other significant products"),
    block("A. Personal statement", null, h("p", { class: "subtle", id: "bio-statement-hint" },
      `Why you are suited to this project. At most ${number(limits.statement)} characters, and no citations: refer to the products above instead.`),
      statement, h("div", { class: "row-sm wrap" }, counter(statement, limits.statement),
        ...slot("biosketch:draft", { part: "statement", personId, docId, papers: data.options.publications,
          insert: insertInto(statement, (t) => { spec.statement = t; }) }))),
    block("B. Honors", `At most ${limits.honors}, from the profile.`,
      checklist({ options: data.options.honor, chosen: spec.honors, most: limits.honors, onChange: (ids) => { spec.honors = ids; now(); } })),
    block("C. Contributions to science", `Up to ${limits.contributions}, each at most ${number(limits.contribution)} characters, with no citations.`,
      contributions, spec.contributions.length < limits.contributions + 2
        ? h("button", { type: "button", class: "btn btn-sm", onclick: () => { spec.contributions.push(""); now(); } }, icon("plus", "icon icon-sm"), "Add a contribution") : null)) });
}

export function biosketchView(data, { url, personId, docId, header }) {
  const checks = h("div", {}, checksBox(data));
  const preview = h("div", { class: "cv-preview stack-lg" }, paper(data));
  const copy = h("div", {}, forSciencv(data));
  let timer = 0;
  const refresh = (next) => {
    Object.assign(data, next);
    checks.replaceChildren(checksBox(next));
    preview.replaceChildren(paper(next));
    copy.replaceChildren(forSciencv(next));
  };
  const save = async (spec, { redraw = true } = {}) => {
    clearTimeout(timer);
    try {
      const next = (await post(url, { spec })).data;
      refresh(next);
      if (redraw) editorSlot.replaceChildren(editor(next, handlers));
    } catch (failure) {
      toast(failure.detail || failure.message);
    }
  };
  // Typing saves after a pause and leaves the editor as it is, so the cursor stays put.
  const saveSoon = (spec) => {
    clearTimeout(timer);
    timer = setTimeout(() => save(spec, { redraw: false }), 900);
  };
  const handlers = { save, saveSoon, personId, docId };
  const editorSlot = h("div", {}, editor(data, handlers));
  return h("div", { class: "stack-lg" }, header,
    h("div", { class: "cv-layout" }, editorSlot, h("div", { class: "stack-lg" }, checks, preview, copy)));
}
