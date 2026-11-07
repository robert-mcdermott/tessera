// Renderers for records: publications, grants, studies, people.

import { h, highlighted, icon } from "../core/dom.js";
import { compact, enumLabel, money, number } from "../core/format.js";
import { avatar, chip, entityHref, pill, statusMark } from "./ui.js";

function hl(item, key, fallback) {
  return highlighted(item?.highlights?.[key] ?? fallback ?? "");
}

function members(list, limit = 4) {
  if (!list?.length) return null;
  const shown = list.slice(0, limit);
  return h("div", { class: "chips" },
    shown.map((m) => chip("person", m.name, `/people/${m.id}`)),
    list.length > limit ? h("span", { class: "chip chip-plain" }, `+${list.length - limit}`) : null);
}

export function publicationItem(summary, { assertion = null, onEvidence = null, highlights = null } = {}) {
  const s = summary ?? {};
  const ctx = { highlights };
  const metrics = [];
  if (s.citation_count != null) metrics.push(h("span", { class: "tabular", title: "Citations (NIH iCite)" }, `${number(s.citation_count)} citations`));
  if (s.relative_citation_ratio != null) metrics.push(h("span", { class: "tabular", title: "Relative Citation Ratio: field-normalized citations, 1.0 is the median NIH-funded paper. Not a quality score." }, `RCR ${s.relative_citation_ratio}`));
  return h("article", { class: "item", "data-dim": assertion?.status === "rejected" ? "" : null },
    h("div", { class: "item-main" },
      h("a", { class: "item-title", href: entityHref("publication", s.pmid) }, hl(ctx, "title", s.title ?? `PMID ${s.pmid}`)),
      s.authors ? h("div", { class: "item-snippet clamp-2" }, s.authors) : null,
      highlights?.body ? h("div", { class: "item-snippet clamp-2" }, highlighted(highlights.body)) : null,
      h("div", { class: "item-meta" },
        s.journal ? h("span", {}, s.journal) : null,
        s.year ? [h("span", { class: "sep" }), h("span", { class: "tabular" }, s.year)] : null,
        metrics.length ? [h("span", { class: "sep" }), metrics.map((m, i) => [i ? h("span", { class: "sep" }) : null, m])] : null,
        s.is_retracted ? pill("Retracted", "danger") : null),
      assertion ? null : members(s.members)),
    assertion ? h("div", { class: "item-side" }, statusMark(assertion, onEvidence),
      assertion.qualifiers?.author_position ? h("span", { class: "subtle tabular", title: "Author position" },
        `Author ${assertion.qualifiers.author_position}${s.author_count ? ` of ${s.author_count}` : ""}`) : null) : null);
}

export function grantItem(summary, { assertion = null, onEvidence = null, highlights = null } = {}) {
  const s = summary ?? {};
  const years = s.fiscal_years ? (s.fiscal_years[0] === s.fiscal_years[1] ? `FY${s.fiscal_years[0]}` : `FY${s.fiscal_years[0]}–${s.fiscal_years[1]}`) : null;
  return h("article", { class: "item" },
    h("div", { class: "item-main" },
      h("a", { class: "item-title", href: entityHref("grant", s.core_project_num) }, hl({ highlights }, "title", s.title)),
      h("div", { class: "item-meta" },
        h("span", { class: "tabular" }, s.core_project_num),
        s.institute ? [h("span", { class: "sep" }), h("span", {}, s.institute)] : null,
        years ? [h("span", { class: "sep" }), h("span", { class: "tabular" }, years)] : null,
        s.total_awarded ? [h("span", { class: "sep" }), h("span", { class: "tabular", title: s.amount_note }, `${money(s.total_awarded, { compactForm: true })} awarded`)] : null,
        s.publication_count ? [h("span", { class: "sep" }), h("span", {}, `${number(s.publication_count)} papers`)] : null),
      assertion ? null : members(s.investigators)),
    h("div", { class: "item-side" },
      assertion ? statusMark(assertion, onEvidence) : null,
      pill(s.is_active ? "Active" : "Ended", s.is_active ? "ok" : "")));
}

export function studyItem(summary, { assertion = null, onEvidence = null, highlights = null } = {}) {
  const s = summary ?? {};
  const status = s.registry_status;
  const tone = { RECRUITING: "ok", ACTIVE_NOT_RECRUITING: "info", COMPLETED: "", NOT_YET_RECRUITING: "accent",
    TERMINATED: "danger", WITHDRAWN: "danger", SUSPENDED: "warn" }[status] ?? "";
  return h("article", { class: "item" },
    h("div", { class: "item-main" },
      h("a", { class: "item-title", href: entityHref("study", s.nct_id) }, hl({ highlights }, "title", s.title)),
      h("div", { class: "item-meta" },
        h("span", { class: "tabular" }, s.nct_id),
        s.lead_sponsor ? [h("span", { class: "sep" }), h("span", {}, s.lead_sponsor)] : null,
        (() => {
          // "Not applicable" is the registry's word for studies that have no phase; say nothing.
          const phases = (s.phases ?? []).filter((p) => p !== "NA");
          return phases.length ? [h("span", { class: "sep" }), h("span", {}, phases.map(enumLabel).join(", "))] : null;
        })()),
      s.conditions?.length ? h("div", { class: "chips" }, s.conditions.slice(0, 4).map((c) => chip("plain", c))) : null,
      assertion ? null : members(s.officials)),
    h("div", { class: "item-side" },
      assertion ? statusMark(assertion, onEvidence) : null,
      status ? h("span", { class: `pill ${tone ? `pill-${tone}` : ""}`, title: "Registry status (not local recruitment status)" }, enumLabel(status)) : null));
}

export function personCard(p, { highlights = null } = {}) {
  const counts = p.counts ?? {};
  return h("a", { class: "card person-card", href: `/people/${p.id}` },
    h("div", { class: "person-card-head" }, avatar(p.name),
      h("div", { class: "grow" },
        h("div", { class: "person-card-name" }, highlighted(highlights?.title ?? p.name)),
        p.units?.length ? h("div", { class: "muted truncate" }, p.units.join(", ")) : null)),
    p.concepts?.length ? h("div", { class: "chips" }, p.concepts.slice(0, 3).map((c) => chip("concept", c))) : null,
    h("div", { class: "person-card-counts" },
      h("span", {}, h("strong", {}, number((counts.accepted ?? 0) + (counts.suggested ?? 0))), " papers"),
      h("span", {}, h("strong", {}, number(counts.grants ?? 0)), " grants"),
      counts.studies ? h("span", {}, h("strong", {}, number(counts.studies)), " studies") : null,
      p.status === "former" ? pill("Former", "") : null));
}

export function personTable(people) {
  return h("div", { class: "card table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {},
      h("th", {}, "Name"), h("th", {}, "Unit"), h("th", {}, "Topics"),
      h("th", { class: "num" }, "Papers"), h("th", { class: "num" }, "Grants"), h("th", { class: "num" }, "Studies"))),
    h("tbody", {}, people.map((p) => h("tr", {},
      h("td", {}, h("a", { class: "row-sm", href: `/people/${p.id}` }, avatar(p.name, "sm"), p.name)),
      h("td", { class: "muted" }, p.units?.join(", ") || "–"),
      h("td", { class: "muted" }, h("span", { class: "clamp-2" }, p.concepts?.slice(0, 3).join(", ") || "–")),
      h("td", { class: "num" }, number((p.counts?.accepted ?? 0) + (p.counts?.suggested ?? 0))),
      h("td", { class: "num" }, number(p.counts?.grants ?? 0)),
      h("td", { class: "num" }, number(p.counts?.studies ?? 0)))))));
}

export function itemFor(kind, item, options = {}) {
  const summary = item.summary ?? item;
  const highlights = item.highlights ?? options.highlights ?? null;
  if (kind === "publication") return publicationItem(summary, { ...options, highlights });
  if (kind === "grant") return grantItem(summary, { ...options, highlights });
  if (kind === "study") return studyItem(summary, { ...options, highlights });
  return personRow(summary, highlights);
}

export function personRow(p, highlights = null) {
  return h("article", { class: "item" },
    avatar(p.name),
    h("div", { class: "item-main" },
      h("a", { class: "item-title", href: `/people/${p.id}` }, highlighted(highlights?.title ?? p.name)),
      h("div", { class: "item-meta" }, p.units?.length ? h("span", {}, p.units.join(", ")) : null,
        p.concepts?.length ? [p.units?.length ? h("span", { class: "sep" }) : null, h("span", {}, p.concepts.slice(0, 3).join(", "))] : null)),
    h("div", { class: "item-side" }, h("span", { class: "subtle tabular" },
      `${compact((p.counts?.accepted ?? 0) + (p.counts?.suggested ?? 0))} papers`)));
}

export { icon };
