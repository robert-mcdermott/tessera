// Grant-to-impact threads (INT-08): what followed a grant or a paper, each step linked as
// shown. Choosing a paper, a study, or a clinical article picks out what it is connected to.

import { h, icon } from "../core/dom.js";
import { enumLabel, number, plural, relative } from "../core/format.js";
import { pill } from "./ui.js";

const KIND_TONE = { guideline: "accent", trial: "ok" };

function joinList(items) {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

/** One sentence that says what followed. */
export function threadSentence(s, { grant = true } = {}) {
  const parts = [];
  if (grant) parts.push(`${plural(s.papers, "paper")} ${s.papers === 1 ? "acknowledges" : "acknowledge"} this grant`);
  if (s.studies) parts.push(`${plural(s.studies, "registered study", "registered studies")} ${grant ? "reference them or name the grant" : "reference it"}`);
  if (s.clinical) {
    const of = grant ? ` ${number(s.papers_cited_clinically)} of the papers` : " it";
    const kinds = [s.guidelines ? plural(s.guidelines, "practice guideline") : null, s.trials ? plural(s.trials, "clinical trial") : null].filter(Boolean);
    parts.push(`${plural(s.clinical, "clinical article")} ${s.clinical === 1 ? "cites" : "cite"}${of}${kinds.length ? `, including ${joinList(kinds)}` : ""}`);
  }
  if (!parts.length) return grant ? "No paper acknowledges this grant yet." : "No registered study or clinical article cites this paper yet.";
  let text = `${joinList(parts)}.`;
  text = text[0].toUpperCase() + text.slice(1);
  if (s.years_to_first_clinical != null && s.years_to_first_clinical >= 0) {
    text += ` The first clinical citation came ${s.years_to_first_clinical === 0 ? "in the year the grant began" : `${plural(s.years_to_first_clinical, "year")} after the grant began`}.`;
  }
  return text;
}

export function sourceNote(sources) {
  const fetched = (sources ?? []).filter((s) => s.last_success_at);
  if (!fetched.length) return null;
  return h("p", { class: "subtle" }, "Data: ", fetched.map((s) => `${s.label}, fetched ${relative(s.last_success_at)}`).join(" · "), ".");
}

function linkedBy(sources) {
  return sources?.length ? `Linked by ${joinList(sources)}` : null;
}

function clinicalLine(c) {
  return [c.journal, c.first_author ? `${c.first_author} et al.` : null].filter(Boolean).join(" · ");
}

/** A clinical article as a list item (for papers and people). */
export function clinicalItem(c) {
  return h("li", { class: "thread-row" },
    h("span", { class: "tabular subtle" }, c.year ?? "–"),
    h("div", { class: "grow stack-xs" },
      h("a", { href: c.url, target: "_blank", rel: "noopener noreferrer" }, c.title ?? `PubMed ${c.pmid}`, icon("external", "icon icon-sm")),
      h("span", { class: "row-sm wrap" }, pill(c.kind_label, KIND_TONE[c.kind] ?? ""), h("span", { class: "subtle" }, clinicalLine(c)))));
}

export function clinicalList(clinical) {
  return h("ul", { class: "plain-list thread-list" }, clinical.map(clinicalItem));
}

/** The interactive thread: papers, studies, and clinical articles in columns. */
export function threadView(t) {
  const papers = new Map(t.papers.map((p) => [p.pmid, p]));
  const studies = new Map(t.studies.map((s) => [s.nct_id, s]));
  const clinical = new Map(t.clinical.map((c) => [c.pmid, c]));
  const nodes = new Map(); // key -> element
  let selected = null;

  function related(key) {
    const [kind, id] = key.split(":");
    const out = new Set([key]);
    const addPaper = (pmid) => {
      const p = papers.get(pmid);
      if (!p) return;
      out.add(`paper:${pmid}`);
      p.studies.forEach((n) => out.add(`study:${n}`));
      p.clinical.forEach((c) => out.add(`clinical:${c}`));
    };
    if (kind === "paper") addPaper(id);
    if (kind === "study") {
      for (const v of studies.get(id)?.via ?? []) if (v.pmid) { out.add(`paper:${v.pmid}`); papers.get(v.pmid)?.clinical.forEach((c) => out.add(`clinical:${c}`)); }
    }
    if (kind === "clinical") {
      for (const pmid of clinical.get(id)?.cites ?? []) { out.add(`paper:${pmid}`); papers.get(pmid)?.studies.forEach((n) => out.add(`study:${n}`)); }
    }
    return out;
  }

  // With something chosen, each column shows only what is linked to it.
  function paint(keys, message = "") {
    const shown = { paper: 0, study: 0, clinical: 0 };
    for (const [key, node] of nodes) {
      node.querySelector(".thread-select").setAttribute("aria-pressed", key === selected ? "true" : "false");
      node.toggleAttribute("data-related", !!keys && keys.has(key) && key !== selected);
      node.hidden = !!keys && !keys.has(key);
      if (!node.hidden) shown[key.split(":")[0]] += 1;
    }
    for (const [kind, span] of Object.entries(counts)) {
      span.textContent = keys ? ` ${number(shown[kind])} of ${number(totals[kind])}` : ` ${number(totals[kind])}`;
      nones[kind].hidden = !keys || shown[kind] > 0 || totals[kind] === 0;
    }
    clear.hidden = !keys;
    status.textContent = message;
  }

  function choose(key) {
    selected = selected === key ? null : key;
    paint(selected ? related(selected) : null,
      selected ? "Showing what is linked to your choice. Choose it again, or Show everything, to see the whole thread." : "Showing the whole thread.");
  }

  function guidelinePaths() {
    const keys = new Set();
    for (const c of t.clinical) if (c.kind === "guideline") related(`clinical:${c.pmid}`).forEach((k) => keys.add(k));
    selected = null;
    paint(keys.size ? keys : null, "Showing the papers and studies on paths to practice guidelines.");
  }

  function item(key, { title, meta, href, external = false, label, extra = null, tooltip = null }) {
    const node = h("li", { class: "thread-item", "data-key": key },
      h("button", { type: "button", class: "thread-select", "aria-pressed": "false", title: tooltip, onclick: () => choose(key) },
        h("span", { class: "thread-title" }, title), meta ? h("span", { class: "thread-meta" }, meta) : null, extra),
      h("a", { class: "thread-open", href, "aria-label": label, title: label, ...(external ? { target: "_blank", rel: "noopener noreferrer" } : {}) },
        icon(external ? "external" : "chevronRight", "icon icon-sm")));
    nodes.set(key, node);
    return node;
  }

  const marks = (p) => [p.studies.length ? plural(p.studies.length, "study", "studies") : null,
    p.clinical.length ? plural(p.clinical.length, "clinical citation") : null].filter(Boolean).join(" · ");

  const totals = { paper: t.papers.length, study: t.studies.length, clinical: t.clinical.length };
  const counts = {};
  const nones = {};
  const column = (title, kind, entity, items, empty) => {
    counts[kind] = h("span", { class: "subtle tabular" }, ` ${number(totals[kind])}`);
    nones[kind] = h("p", { class: "subtle thread-empty", hidden: true }, "Nothing here is linked to your choice.");
    return h("section", { class: "thread-column", "data-entity": entity, "aria-label": title },
      h("h3", { class: "thread-heading" }, title, counts[kind]),
      items.length ? h("ol", { class: "thread-items" }, items) : h("p", { class: "subtle thread-empty" }, empty),
      nones[kind]);
  };

  const status = h("p", { class: "sr-only", role: "status", "aria-live": "polite" });
  const clear = h("button", { type: "button", class: "btn btn-sm btn-ghost", hidden: true, onclick: () => { selected = null; paint(null, "Showing the whole thread."); } }, "Show everything");
  const guidelines = t.clinical.filter((c) => c.kind === "guideline").length;

  return h("div", { class: "stack" },
    h("div", { class: "row-sm wrap" },
      guidelines ? h("button", { type: "button", class: "btn btn-sm", onclick: guidelinePaths }, "Paths to practice guidelines") : null,
      clear,
      h("span", { class: "subtle" }, "Choose a paper, study, or clinical article to see only what it is linked to.")),
    status,
    h("div", { class: "thread" },
      column("Papers", "paper", "publication", t.papers.map((p) => item(`paper:${p.pmid}`, {
        title: p.title ?? `PubMed ${p.pmid}`, meta: [p.year, p.journal, marks(p)].filter(Boolean).join(" · "),
        href: `/publications/${p.pmid}`, label: "Open the paper", tooltip: linkedBy(p.sources) })),
        "No paper acknowledges this grant yet."),
      column("Registered studies", "study", "study", t.studies.map((s) => item(`study:${s.nct_id}`, {
        title: s.title ?? s.nct_id,
        meta: [s.nct_id, s.status ? `${enumLabel(s.status)} (registry status)` : null,
          s.via.some((v) => v.type === "funded_by") ? "names the grant" : null,
          s.via.filter((v) => v.pmid).length ? `references ${plural(s.via.filter((v) => v.pmid).length, "paper")}` : null].filter(Boolean).join(" · "),
        href: `/studies/${s.nct_id}`, label: "Open the study", tooltip: "Linked by ClinicalTrials.gov" })),
        "No registered study references these papers or names the grant."),
      column("Clinical articles", "clinical", "clinical", t.clinical.map((c) => item(`clinical:${c.pmid}`, {
        title: c.title ?? `PubMed ${c.pmid}`, meta: [c.year, clinicalLine(c), `cites ${plural(c.cites.length, "paper")}`].filter(Boolean).join(" · "),
        extra: h("span", { class: "thread-kind" }, pill(c.kind_label, KIND_TONE[c.kind] ?? "")),
        href: c.url, external: true, label: "Open in PubMed", tooltip: "Listed by NIH iCite" })),
        "No clinical article cites these papers yet.")));
}
