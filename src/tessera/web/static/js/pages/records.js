// Detail pages: a publication, a grant, a study. Each shows its connections and provenance.

import { get } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { date, enumLabel, money, number, paragraphs, personName, relative } from "../core/format.js";
import { openEvidence } from "../components/drawer.js";
import { grantItem, publicationItem, studyItem } from "../components/items.js";
import { barChart, callout, card, chip, emptyState, entityIcon, kv, pageHeader, pill, statusMark } from "../components/ui.js";
import { show } from "./page.js";

function prose(text) {
  return h("div", { class: "abstract" }, paragraphs(text).map((part) => h("p", {}, part)));
}

function externalLink(href, label) {
  return h("a", { class: "btn btn-sm", href, target: "_blank", rel: "noopener noreferrer" }, label, icon("external", "icon icon-sm"));
}

function provenance(p) {
  if (!p) return null;
  const names = { pubmed: "PubMed", icite: "NIH iCite", reporter: "NIH RePORTER", ctgov: "ClinicalTrials.gov" };
  return h("p", { class: "subtle", title: `Ingest run ${p.run_id}` },
    `From ${names[p.source] ?? p.source}, retrieved ${relative(p.retrieved_at)}`,
    p.source_version ? ` (source data as of ${date(p.source_version)})` : "", ".");
}

// --- Publication -------------------------------------------------------------------------------

export function publication(main, params) {
  const pmid = params.pmid;
  const reload = () => publication(main, params);
  return show(main, {
    title: (d) => d.title ?? `PMID ${pmid}`,
    load: async () => (await get(`/api/v1/publications/${encodeURIComponent(pmid)}`, null, { fresh: true })).data,
    view: (p) => {
      const placed = p.authors.filter((a) => a.people.length);
      const authors = h("ol", { class: "author-list" }, p.authors.map((a) => {
        const name = a.collective_name ?? [a.given, a.family].filter(Boolean).join(" ");
        return h("li", { class: a.people.length ? "author has-people" : "author" },
          h("span", {}, name),
          a.orcid ? h("a", { href: `https://orcid.org/${a.orcid}`, target: "_blank", rel: "noopener noreferrer", class: "subtle", title: `ORCID ${a.orcid}` }, icon("orcid", "icon icon-sm")) : null,
          a.people.map((m) => h("span", { class: "row-sm wrap" },
            chip("person", m.person.name ?? m.person.id, `/people/${m.person.id}`),
            statusMark(m.assertion, () => openEvidence(m.assertion.id, { onChange: reload })))));
      }));
      const metrics = p.metrics;
      return h("div", { class: "stack-lg" },
        pageHeader({
          lead: entityIcon("publication"),
          eyebrow: [h("span", {}, p.record_type === "book" ? "Book chapter" : "Publication"), p.is_retracted ? pill("Retracted", "danger") : null],
          title: p.title ?? `PMID ${p.pmid}`,
          meta: [p.journal ? h("span", {}, p.journal) : null, p.pub_year ? h("span", { class: "tabular" }, p.pub_year) : null,
            p.volume ? h("span", { class: "tabular" }, `${p.volume}${p.issue ? `(${p.issue})` : ""}${p.pages ? `:${p.pages}` : ""}`) : null,
            h("span", { class: "tabular" }, `PMID ${p.pmid}`)],
          actions: [externalLink(p.url, "PubMed"), p.doi_url ? externalLink(p.doi_url, "Publisher") : null],
        }),
        p.is_retracted ? callout("This paper has been retracted. It is excluded from metrics by default.", "warn") : null,
        h("div", { class: "grid-main" },
          h("div", { class: "stack-lg" },
            card({ title: `Authors (${p.authors.length})`, action: placed.length ? h("span", { class: "muted" }, `${placed.length} matched to people`) : null,
              pad: true, body: authors }),
            p.abstract ? card({ title: "Abstract", pad: true, body: prose(p.abstract) }) : null,
            p.grants.length ? card({ title: "Grants acknowledged", body: h("div", { class: "items" }, p.grants.map((g) =>
              grantItem(g.summary, { assertion: g.assertion, onEvidence: () => openEvidence(g.assertion.id) }))) }) : null,
            p.studies.length ? card({ title: "Studies citing this paper", body: h("div", { class: "items" }, p.studies.map((s) =>
              studyItem(s.summary, { assertion: s.assertion, onEvidence: () => openEvidence(s.assertion.id) }))) }) : null),
          h("div", { class: "stack-lg" },
            metrics ? card({ title: "Citation metrics", pad: true, body: h("div", { class: "stack-sm" },
              kv([["Citations", h("span", { class: "tabular", title: metrics.definitions.citation_count }, number(metrics.citation_count))],
                ["Relative Citation Ratio", h("span", { class: "tabular", title: metrics.definitions.relative_citation_ratio }, metrics.relative_citation_ratio?.toFixed(2) ?? "–")],
                ["NIH percentile", h("span", { class: "tabular", title: metrics.definitions.nih_percentile }, metrics.nih_percentile?.toFixed(0) ?? "–")],
                ["Translation (APT)", h("span", { class: "tabular", title: metrics.definitions.apt }, metrics.apt ?? "–")],
                ["Clinical research", metrics.is_clinical == null ? "–" : metrics.is_clinical ? "Yes" : "No"]]),
              h("p", { class: "subtle" }, "Citation measures, not quality scores. Hover a value for its definition."),
              provenance(metrics.provenance)) }) : null,
            p.mesh.length ? card({ title: "Topics (MeSH)", pad: true, attrs: { "data-entity": "concept" }, body: h("div", { class: "chips" },
              p.mesh.map((m) => h("span", { class: "chip", "data-entity": m.major_topic ? "concept" : "unit", title: m.qualifiers?.length ? m.qualifiers.join(", ") : m.descriptor }, m.descriptor))) }) : null,
            p.grants_acknowledged.length ? card({ title: "Funding as printed", pad: true, body: h("ul", { class: "plain-list" },
              p.grants_acknowledged.map((g) => h("li", {}, h("span", { class: "tabular" }, g.grant_id_raw ?? "–"), g.agency ? h("span", { class: "subtle" }, ` · ${g.agency}`) : null))) }) : null,
            card({ title: "Record", pad: true, body: h("div", { class: "stack-sm" }, kv([["DOI", p.doi], ["PMCID", p.pmcid], ["Status", p.publication_status],
              ["Types", p.publication_types?.join(", ")], ["Language", p.languages?.join(", ")]]), provenance(p.provenance)) }))));
    },
  });
}

// --- Grant -------------------------------------------------------------------------------------

export function grant(main, params) {
  const core = params.core;
  return show(main, {
    title: (g) => g.title ?? core,
    load: async () => (await get(`/api/v1/grants/${encodeURIComponent(core)}`, null, { fresh: true })).data,
    view: (g) => {
      const years = g.fiscal_years.filter((y) => !y.subproject_id);
      return h("div", { class: "stack-lg" },
        pageHeader({
          lead: entityIcon("grant"),
          eyebrow: [h("span", {}, `Grant · ${g.activity_code ?? ""}`), pill(g.is_active ? "Active" : "Ended", g.is_active ? "ok" : "")],
          title: g.title ?? g.core_project_num,
          meta: [h("span", { class: "tabular" }, g.core_project_num), g.institute ? h("span", {}, g.institute) : null,
            g.organization ? h("span", {}, g.organization) : null,
            g.project_start ? h("span", { class: "tabular" }, `${date(g.project_start)} – ${date(g.project_end)}`) : null],
          actions: [externalLink(g.url, "NIH RePORTER")],
        }),
        h("div", { class: "grid-main" },
          h("div", { class: "stack-lg" },
            years.length ? card({ title: "Awards by fiscal year", pad: true, attrs: { "data-entity": "grant" }, body: h("div", { class: "stack-sm" },
              barChart(years.map((y) => ({ label: `FY${String(y.fiscal_year).slice(-2)}`, value: y.award_amount ?? 0 })),
                { format: (v) => money(v, { compactForm: true }), caption: "Total-cost award by fiscal year" }),
              h("p", { class: "subtle" }, g.totals.counting_rule)) }) : null,
            g.abstract ? card({ title: "Abstract", pad: true, body: prose(g.abstract) }) : null,
            card({ title: `Publications (${g.publications.length})`, body: g.publications.length
              ? h("div", { class: "items" }, g.publications.slice(0, 50).map((p) => publicationItem(p.summary, { assertion: p.assertion, onEvidence: () => openEvidence(p.assertion.id) })))
              : emptyState({ kind: "publication", title: "No linked publications", text: "Neither RePORTER nor PubMed links a paper to this grant yet." }) }),
            g.studies.length ? card({ title: "Studies", body: h("div", { class: "items" }, g.studies.map((s) =>
              studyItem(s.summary, { assertion: s.assertion, onEvidence: () => openEvidence(s.assertion.id) }))) }) : null),
          h("div", { class: "stack-lg" },
            card({ title: "Investigators", pad: true, body: h("div", { class: "stack-sm" }, g.investigators.map((i) =>
              h("div", { class: "row-sm" }, i.person_id ? chip("person", personName(i.name), `/people/${i.person_id}`) : chip("plain", personName(i.name)),
                i.contact_pi ? pill("Contact PI", "accent") : null))) }),
            card({ title: "Totals", pad: true, body: h("div", { class: "stack-sm" },
              h("div", { class: "stat-value" }, money(g.totals.total_awarded)),
              h("p", { class: "subtle" }, "Awarded across the fiscal years in the data. See the note under the chart.")) }),
            g.terms?.length ? card({ title: "Terms", pad: true, body: h("div", { class: "chips" }, g.terms.slice(0, 16).map((t) => chip("plain", t))) }) : null,
            card({ title: "Record", pad: true, body: provenance(g.provenance) }))));
    },
  });
}

// --- Study -------------------------------------------------------------------------------------

export function study(main, params) {
  const nct = params.nct;
  const reload = () => study(main, params);
  return show(main, {
    title: (s) => s.brief_title ?? nct,
    load: async () => (await get(`/api/v1/studies/${encodeURIComponent(nct)}`, null, { fresh: true })).data,
    view: (s) => h("div", { class: "stack-lg" },
      pageHeader({
        lead: entityIcon("study"),
        eyebrow: [h("span", {}, `Study · ${enumLabel(s.study_type)}`), s.phases?.filter((p) => p !== "NA").map((p) => pill(enumLabel(p), "accent"))],
        title: s.brief_title ?? s.nct_id,
        meta: [h("span", { class: "tabular" }, s.nct_id), s.lead_sponsor ? h("span", {}, s.lead_sponsor) : null,
          s.start_date ? h("span", {}, `Started ${date(s.start_date)}`) : null],
        actions: [externalLink(s.url, "ClinicalTrials.gov")],
      }),
      callout(h("span", {}, h("strong", {}, `Registry status: ${enumLabel(s.registry_status)}. `), s.registry_status_note)),
      h("div", { class: "grid-main" },
        h("div", { class: "stack-lg" },
          s.brief_summary ? card({ title: "Summary", pad: true, body: prose(s.brief_summary) }) : null,
          card({ title: "Officials", pad: true, body: s.officials.length ? h("div", { class: "stack" }, s.officials.map((o) =>
            h("div", { class: "stack-sm" },
              h("div", { class: "row-sm wrap" }, h("strong", {}, personName(o.name) || "–"), o.role ? pill(enumLabel(o.role), "") : null,
                o.affiliation ? h("span", { class: "muted" }, o.affiliation) : null),
              o.people.length ? h("div", { class: "row-sm wrap" }, o.people.slice(0, 3).map((m) => h("span", { class: "row-sm wrap" },
                chip("person", m.person.name ?? m.person.id, `/people/${m.person.id}`),
                statusMark(m.assertion, () => openEvidence(m.assertion.id, { onChange: reload }))))) : null)))
            : h("p", { class: "muted" }, "No officials listed.") }),
          card({ title: `Publications (${s.publications.length})`, body: s.publications.length
            ? h("div", { class: "items" }, s.publications.map((p) => p.summary ? publicationItem(p.summary)
              : h("div", { class: "item" }, h("a", { href: `https://pubmed.ncbi.nlm.nih.gov/${p.pmid}/`, target: "_blank", rel: "noopener noreferrer" }, `PMID ${p.pmid}`))))
            : emptyState({ kind: "publication", title: "No publications listed" }) }),
          s.grants.length ? card({ title: "Grants", body: h("div", { class: "items" }, s.grants.map((g) =>
            grantItem(g.summary, { assertion: g.assertion, onEvidence: () => openEvidence(g.assertion.id) }))) }) : null),
        h("div", { class: "stack-lg" },
          card({ title: "Design", pad: true, body: kv([["Type", enumLabel(s.study_type)], ["Phase", s.phases?.map(enumLabel).join(", ")],
            ["Enrollment", s.enrollment != null ? `${number(s.enrollment)}${s.enrollment_type ? ` (${s.enrollment_type.toLowerCase()})` : ""}` : null],
            ["Start", s.start_date ? `${date(s.start_date)}${s.start_date_type ? ` (${s.start_date_type.toLowerCase()})` : ""}` : null],
            ["Primary completion", s.primary_completion_date ? `${date(s.primary_completion_date)}${s.primary_completion_date_type ? ` (${s.primary_completion_date_type.toLowerCase()})` : ""}` : null],
            ["Has results", s.has_results ? "Yes" : "No"]]) }),
          s.conditions?.length ? card({ title: "Conditions", pad: true, body: h("div", { class: "chips" }, s.conditions.map((c) => chip("plain", c))) }) : null,
          s.interventions?.length ? card({ title: "Interventions", pad: true, body: h("div", { class: "chips" },
            s.interventions.map((i) => chip("plain", `${i.name}${i.type ? ` (${enumLabel(i.type).toLowerCase()})` : ""}`))) }) : null,
          card({ title: `Sites (${s.sites.length})`, pad: true, body: h("div", { class: "stack-sm" },
            s.organization_sites ? h("p", {}, `${number(s.organization_sites)} at this organization`) : h("p", { class: "muted" }, "No sites at this organization"),
            h("ul", { class: "plain-list" }, s.sites.filter((x) => x.is_organization).concat(s.sites.filter((x) => !x.is_organization)).slice(0, 12).map((x) =>
              h("li", { class: x.is_organization ? "site org-site" : "site" }, x.facility ?? "–",
                h("span", { class: "subtle" }, [x.city, x.country].filter(Boolean).length ? ` · ${[x.city, x.country].filter(Boolean).join(", ")}` : "")))),
            s.sites.length > 12 ? h("p", { class: "subtle" }, `and ${number(s.sites.length - 12)} more`) : null) }),
          card({ title: "Record", pad: true, body: provenance(s.provenance) })))),
  });
}
