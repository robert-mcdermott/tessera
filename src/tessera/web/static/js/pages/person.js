// A person's profile: identity, topics, activity, and every linked record with evidence.

import { get, post } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { enumLabel, number, relative } from "../core/format.js";
import { query, setQuery } from "../core/router.js";
import { openDrawer, openEvidence } from "../components/drawer.js";
import { toast } from "../components/toast.js";
import { identifiersCard } from "../components/identifiers.js";
import { grantItem, publicationItem, studyItem } from "../components/items.js";
import { avatar, barChart, callout, card, emptyState, pageHeader, pager, pill, segmented, statTile, tabs, topicChips } from "../components/ui.js";
import { show } from "./page.js";

const LIMIT = 20;

export function person(main, params, { samePage = false } = {}) {
  const id = params.id;
  const tab = query().get("tab") ?? "publications";
  const status = query().get("status") ?? "all";
  const band = query().get("band") ?? "likely";
  const sort = query().get("sort") ?? "year";
  const pageNo = Math.max(1, Number(query().get("page") ?? 1) || 1);
  const refresh = () => person(main, params, { samePage: true });

  return show(main, {
    title: (d) => d.profile.name,
    quiet: samePage,
    load: async () => {
      const profile = (await get(`/api/v1/people/${encodeURIComponent(id)}`, null, { fresh: samePage })).data;
      let list = null;
      if (tab === "publications") {
        const statuses = { all: ["accepted", "suggested", "deferred"], accepted: ["accepted"], suggested: ["suggested", "deferred"], rejected: ["rejected"] }[status] ?? ["accepted", "suggested"];
        const bands = band === "all" ? ["strong", "possible", "unlikely"] : band === "unlikely" ? ["unlikely"] : ["strong", "possible"];
        list = await get(`/api/v1/people/${encodeURIComponent(id)}/publications`,
          { status: statuses, band: bands, sort, limit: LIMIT, offset: (pageNo - 1) * LIMIT }, { fresh: samePage });
      } else if (tab === "grants") {
        list = await get(`/api/v1/people/${encodeURIComponent(id)}/grants`, null, { fresh: samePage });
      } else if (tab === "studies") {
        list = await get(`/api/v1/people/${encodeURIComponent(id)}/studies`, null, { fresh: samePage });
      } else if (tab === "history") {
        list = await get(`/api/v1/people/${encodeURIComponent(id)}/history`, null, { fresh: true });
      }
      return { profile, list };
    },
    view: ({ profile, list }) => {
      const c = profile.counts;
      const pubs = c.publications ?? {};
      const suggested = (c.suggested_by_band?.strong ?? 0) + (c.suggested_by_band?.possible ?? 0);
      const member = profile.status === "active" || profile.status === "former";
      const orcid = profile.identifiers.find((i) => i.scheme === "orcid" && i.status === "accepted");
      const header = pageHeader({
        lead: avatar(profile.name, "lg"),
        eyebrow: [h("span", {}, "Person"), profile.status !== "active" ? pill(enumLabel(profile.status), "") : null],
        title: profile.name,
        meta: [
          profile.units.length ? h("span", { class: "row-sm" }, icon("unit", "icon icon-sm"), profile.units.map((u) => u.unit).join(", ")) : null,
          orcid ? h("a", { class: "row-sm", href: `https://orcid.org/${orcid.value}`, target: "_blank", rel: "noopener noreferrer" },
            icon("orcid", "icon icon-sm"), orcid.value) : null,
          profile.concepts.length ? h("span", { class: "muted" }, profile.concepts.slice(0, 3).join(" · ")) : null,
        ],
        actions: member ? [
          suggested ? h("a", { class: "btn", href: `/review?person=${encodeURIComponent(profile.id)}` }, icon("review", "icon icon-sm"), "Review suggestions") : null,
          h("button", { type: "button", class: "btn", onclick: () => addPublication(profile, refresh) }, icon("publication", "icon icon-sm"), "Add a publication"),
        ] : null,
      });
      const candidateNote = member ? null : h("div", { class: "callout", role: "note" },
        icon("info", "icon icon-sm"),
        h("div", { class: "grow" }, profile.status === "dismissed"
          ? "A curator dismissed this person as not a member of the organization."
          : "Tessera found this person in the data. They are not in the directory until a curator promotes them."),
        h("div", { class: "row-sm" },
          h("button", { type: "button", class: "btn btn-sm btn-accept", onclick: () => decidePerson(profile, "promote", refresh) }, "Promote"),
          profile.status === "candidate" ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => decidePerson(profile, "dismiss", refresh) }, "Dismiss") : null));

      const stats = h("div", { class: "stats" },
        statTile({ kind: "publication", label: "Confirmed papers", value: pubs.accepted ?? 0 }),
        statTile({ kind: "publication", label: "Suggested papers", value: suggested,
          note: c.suggested_by_band?.strong ? `${number(c.suggested_by_band.strong)} strong matches` : null }),
        statTile({ kind: "grant", label: "Grants", value: c.grants }),
        statTile({ kind: "study", label: "Studies", value: c.studies }));

      const tabBar = tabs([
        { id: "publications", label: "Publications", count: (pubs.accepted ?? 0) + suggested },
        { id: "grants", label: "Grants", count: c.grants },
        { id: "studies", label: "Studies", count: c.studies },
        { id: "history", label: "History" },
      ], tab, (next) => setQuery({ tab: next, page: null, status: null, band: null, sort: null }, { replace: false }));

      let panel;
      if (tab === "publications") panel = publicationsPanel(list, { status, band, sort, pageNo, refresh });
      else if (tab === "grants") panel = linkedPanel(list.data, "grant", refresh, "No grants found for this person.");
      else if (tab === "studies") panel = linkedPanel(list.data, "study", refresh, "No study roles suggested for this person.");
      else panel = historyPanel(list.data);

      const side = h("div", { class: "stack-lg" },
        profile.concepts.length ? card({ title: "Topics", pad: true, attrs: { "data-entity": "concept" },
          body: h("div", { class: "stack-sm" }, topicChips(profile.concepts, 8),
            h("p", { class: "subtle" }, "Most distinctive MeSH topics across confirmed and strongly suggested papers.")) }) : null,
        profile.timeline.length > 1 ? card({ title: "Activity", pad: true, attrs: { "data-entity": "publication" },
          body: h("div", { class: "stack-sm" },
            barChart(profile.timeline.slice(-10).map((t) => ({ label: `’${String(t.year).slice(-2)}`, value: t.publications, secondary: t.accepted })),
              { caption: "Papers per year (confirmed inside all likely papers)", height: 120 }),
            h("div", { class: "legend" }, h("span", {}, h("span", { class: "legend-swatch swatch-strong" }), "Confirmed"),
              h("span", {}, h("span", { class: "legend-swatch swatch-soft" }), "Likely")) ) }) : null,
        identifiersCard(profile, refresh),
        profile.names.length > 1 ? card({ title: "Names in the data", pad: true,
          body: h("div", { class: "chips" }, profile.names.map((n) => h("span", { class: "chip chip-plain" }, `${n.given ? `${n.given} ` : ""}${n.family}`))) }) : null);

      return h("div", { class: "stack-lg" }, header, candidateNote, stats,
        h("div", { class: "grid-main" }, h("div", {}, tabBar, panel), side));
    },
  });
}

function publicationsPanel(list, { status, band, sort, pageNo, refresh }) {
  const { data, meta } = list;
  const t = meta.tabs ?? {};
  const counts = {
    all: (t.accepted ?? 0) + (t["suggested:strong"] ?? 0) + (t["suggested:possible"] ?? 0) + (t.deferred ?? 0),
    accepted: t.accepted ?? 0,
    suggested: (t["suggested:strong"] ?? 0) + (t["suggested:possible"] ?? 0) + (t.deferred ?? 0),
    rejected: t.rejected ?? 0,
  };
  const filters = h("div", { class: "toolbar" },
    segmented([
      { id: "all", label: `All ${number(counts.all)}` },
      { id: "accepted", label: `Confirmed ${number(counts.accepted)}` },
      { id: "suggested", label: `Suggested ${number(counts.suggested)}` },
      ...(counts.rejected ? [{ id: "rejected", label: `Rejected ${number(counts.rejected)}` }] : []),
    ], status, (id) => setQuery({ status: id, page: null }), "Status"),
    status !== "accepted" && status !== "rejected" ? h("label", { class: "facet-option" },
      h("input", { type: "checkbox", checked: band === "unlikely", onchange: (e) => setQuery({ band: e.target.checked ? "unlikely" : null, page: null }) }),
      `Show only unlikely (${number(t["suggested:unlikely"] ?? 0)})`) : null,
    h("select", { class: "input spacer", "aria-label": "Sort", onchange: (e) => setQuery({ sort: e.target.value, page: null }) },
      h("option", { value: "year", selected: sort === "year" }, "Newest first"),
      h("option", { value: "score", selected: sort === "score" }, "Most likely first")));
  const body = data.length
    ? h("div", { class: "items" }, data.map((item) => publicationItem(item.publication, {
      assertion: item.assertion, onEvidence: () => openEvidence(item.assertion.id, { onChange: refresh }) })))
    : emptyState({ kind: "publication", title: "Nothing here", text: "No publications match this filter." });
  const pages = pager({ total: meta.total, offset: (pageNo - 1) * LIMIT, limit: LIMIT,
    onPage: (offset) => setQuery({ page: offset / LIMIT + 1 }, { replace: false }) });
  return h("div", { class: "stack" }, filters, card({ body: h("div", {}, body, pages) }));
}

function linkedPanel(items, kind, refresh, emptyText) {
  if (!items.length) return card({ body: emptyState({ kind, title: "Nothing here", text: emptyText }) });
  const render = kind === "grant" ? grantItem : studyItem;
  return card({ body: h("div", { class: "items" }, items.map((item) => render(item.summary, {
    assertion: item.assertion, onEvidence: () => openEvidence(item.assertion.id, { onChange: refresh }) }))) });
}

const VERBS = { accept: "Accepted", reject: "Rejected", defer: "Deferred", undo: "Undid a decision on" };

function historyPanel(events) {
  if (!events.length) {
    return card({ body: emptyState({ iconName: "clock", title: "No decisions yet",
      text: "Accepting or rejecting a suggestion records who decided, when, and why." }) });
  }
  return card({ body: h("div", { class: "items" }, events.map((e) => h("div", { class: "item" },
    h("span", { class: "entity-icon", "data-entity": "unit" }, icon(e.action === "undo" ? "undo" : e.action === "reject" ? "close" : "check")),
    h("div", { class: "item-main" },
      h("div", {}, `${VERBS[e.action] ?? e.action} `, h("a", { href: e.object_id.startsWith("pub:") ? `/publications/${e.object_id.slice(4)}` : "#" }, e.object_id),
        e.note ? h("span", { class: "muted" }, ` — “${e.note}”`) : null),
      h("div", { class: "item-meta" }, h("span", {}, e.actor), h("span", { class: "sep" }), h("span", {}, relative(e.at))))))) });
}

async function decidePerson(profile, action, refresh) {
  try {
    await post("/api/v1/candidates/decisions", { ids: [profile.id], action });
  } catch (error) {
    toast(error.detail || "That did not work.");
    return;
  }
  refresh();
  toast(action === "promote" ? `${profile.name} is now in the directory.` : `${profile.name} was dismissed.`, {
    action: { label: "Undo", run: async () => {
      await post("/api/v1/candidates/decisions", { ids: [profile.id], action: "undo" });
      refresh();
    } },
  });
}

/** A form to attach a paper the evidence engine missed, by PubMed ID. */
function addPublication(profile, refresh) {
  openDrawer({
    title: "Add a publication",
    subtitle: profile.name,
    label: "Add a publication",
    render: (body, footer, close) => {
      const pmid = h("input", { class: "input", id: "add-pmid", inputmode: "numeric", autocomplete: "off",
        pattern: "[1-9][0-9]{0,8}", required: true, placeholder: "e.g. 31452104" });
      const note = h("textarea", { class: "input", id: "add-note", rows: 2, placeholder: "Why this paper belongs here (optional)" });
      const error = h("p", { class: "field-error", role: "alert", hidden: true });
      const submit = h("button", { type: "submit", class: "btn btn-primary" }, "Add publication");
      const form = h("form", { class: "stack", novalidate: true, onsubmit: async (event) => {
        event.preventDefault();
        const value = pmid.value.trim().replace(/^pmid:?\s*/i, "");
        if (!/^[1-9][0-9]{0,8}$/.test(value)) {
          error.textContent = "Enter a PubMed ID: digits only, such as 31452104.";
          error.hidden = false;
          pmid.focus();
          return;
        }
        submit.disabled = true;
        submit.textContent = "Adding…";
        try {
          await post(`/api/v1/people/${encodeURIComponent(profile.id)}/publications`, { pmid: value, note: note.value.trim() || null });
        } catch (failure) {
          error.textContent = failure.detail || "The paper could not be added.";
          error.hidden = false;
          submit.disabled = false;
          submit.textContent = "Add publication";
          return;
        }
        close();
        refresh();
        toast(`Added PMID ${value} to ${profile.name}'s publications.`);
      } },
      h("p", { class: "muted" }, "For a paper the evidence engine missed. Tessera fetches it from PubMed if needed and records it as confirmed, entered by you."),
      h("div", { class: "field" }, h("label", { for: "add-pmid" }, "PubMed ID"), pmid),
      h("div", { class: "field" }, h("label", { for: "add-note" }, "Note"), note),
      error,
      h("div", { class: "row-sm" }, submit, h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel")));
      body.append(form);
    },
  });
}
