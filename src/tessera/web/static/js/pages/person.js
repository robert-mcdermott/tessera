// A person's profile: identity, topics, activity, and every linked record with evidence.

import { get, post } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { enumLabel, number, relative } from "../core/format.js";
import { navigate, query, setQuery } from "../core/router.js";
import { can } from "../core/session.js";
import { openDrawer, openEvidence } from "../components/drawer.js";
import { toast } from "../components/toast.js";
import { identifiersCard } from "../components/identifiers.js";
import { grantItem, publicationItem, studyItem } from "../components/items.js";
import { avatar, barChart, callout, card, debounce, emptyState, pageHeader, pager, pill, segmented, statTile, tabs, topicChips } from "../components/ui.js";
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
      const [profile, concepts, similar] = await Promise.all([
        get(`/api/v1/people/${encodeURIComponent(id)}`, null, { fresh: samePage }).then((r) => r.data),
        get(`/api/v1/people/${encodeURIComponent(id)}/concepts`).catch(() => ({ data: [], meta: {} })),
        get(`/api/v1/people/${encodeURIComponent(id)}/similar`).then((r) => r.data).catch(() => []),
      ]);
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
      return { profile, list, concepts: concepts.data, areas: concepts.meta.areas ?? [], meshYear: concepts.meta.mesh_year, similar };
    },
    view: ({ profile, list, concepts, areas, meshYear, similar }) => {
      const c = profile.counts;
      const pubs = c.publications ?? {};
      const suggested = (c.suggested_by_band?.strong ?? 0) + (c.suggested_by_band?.possible ?? 0);
      const member = profile.status === "active" || profile.status === "former";
      const orcid = profile.identifiers.find((i) => i.scheme === "orcid" && i.status === "accepted");
      const currentUnits = profile.units.filter((u) => u.current);
      const header = pageHeader({
        lead: avatar(profile.name, "lg"),
        eyebrow: [h("span", {}, "Person"), profile.status !== "active" ? pill(enumLabel(profile.status), "") : null],
        title: profile.name,
        meta: [
          currentUnits.length ? h("span", { class: "row-sm" }, icon("unit", "icon icon-sm"), currentUnits.map((u) => u.unit).join(", ")) : null,
          orcid ? h("a", { class: "row-sm", href: `https://orcid.org/${orcid.value}`, target: "_blank", rel: "noopener noreferrer" },
            icon("orcid", "icon icon-sm"), orcid.value) : null,
          profile.concepts.length ? h("span", { class: "muted" }, profile.concepts.slice(0, 3).join(" · ")) : null,
        ],
        actions: member && profile.can_curate ? [
          suggested ? h("a", { class: "btn", href: `/review?person=${encodeURIComponent(profile.id)}` }, icon("review", "icon icon-sm"), "Review suggestions") : null,
          h("button", { type: "button", class: "btn", onclick: () => addPublication(profile, refresh) }, icon("publication", "icon icon-sm"), "Add a publication"),
          can("curate_any") ? h("button", { type: "button", class: "btn btn-ghost", onclick: () => mergeWith(profile, refresh) }, icon("layers", "icon icon-sm"), "Merge…") : null,
          publicToggle(`/api/v1/people/${profile.id}/public`, profile.public, "profile", refresh),
        ] : null,
      });
      const mergedNote = profile.merged_into ? h("div", { class: "callout callout-warn", role: "note" },
        icon("layers", "icon icon-sm"),
        h("div", { class: "grow" }, "This record was merged into ",
          h("a", { href: `/people/${profile.merged_into.id}` }, profile.merged_into.name ?? profile.merged_into.id),
          ` ${relative(profile.merged_into.at)} by ${profile.merged_into.actor}. Its links, names, and identifiers are there now.`),
        profile.can_curate ? h("div", { class: "row-sm" },
          h("button", { type: "button", class: "btn btn-sm", onclick: (e) => undoMerge(profile.merged_into.merge_id, e.currentTarget, refresh) },
            icon("undo", "icon icon-sm"), "Undo merge")) : null) : null;
      const candidateNote = member || profile.merged_into ? null : h("div", { class: "callout", role: "note" },
        icon("info", "icon icon-sm"),
        h("div", { class: "grow" }, profile.status === "dismissed"
          ? "A curator dismissed this person as not a member of the organization."
          : "Tessera found this person in the data. They are not in the directory until a curator promotes them."),
        !can("curate_all") ? null : h("div", { class: "row-sm" },
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
        concepts.length ? card({ title: "Topics", pad: true, attrs: { "data-entity": "concept" },
          action: h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => showProfile(profile, concepts, areas, meshYear) }, "How this is built"),
          body: h("div", { class: "stack-sm" },
            areas.length ? areaList(areas.slice(0, 4)) : null,
            h("div", { class: "chips" }, concepts.slice(0, 8).map((c) => h("a", { class: "chip", href: `/experts?concept=${c.descriptor_ui}`,
              title: `Find others who work on ${c.descriptor}` }, c.descriptor))),
            h("p", { class: "subtle" }, "The most distinctive topics of their papers; select one to find others who work on it.")) })
          : profile.concepts.length ? card({ title: "Topics", pad: true, attrs: { "data-entity": "concept" },
            body: h("div", { class: "stack-sm" }, topicChips(profile.concepts, 8),
              h("p", { class: "subtle" }, "Most distinctive MeSH topics across confirmed and strongly suggested papers.")) }) : null,
        similar.length ? card({ title: "Similar people", pad: true, attrs: { "data-entity": "person" },
          action: h("a", { class: "btn btn-sm btn-ghost", href: `/graph?view=focus&person=${profile.id}` }, icon("network", "icon icon-sm"), "Connections"),
          body: h("ul", { class: "plain-list similar" }, similar.slice(0, 6).map((p) => h("li", {},
            h("div", { class: "row-sm" }, avatar(p.name, "sm"), h("div", { class: "stack-xs grow" },
              h("a", { href: `/people/${p.id}` }, p.name),
              h("span", { class: "subtle" }, `shares ${p.shared.slice(0, 3).map((s) => s.descriptor).join(", ")}`),
              h("span", { class: "subtle" }, p.together
                ? `worked together: ${number(p.together.papers)} ${p.together.papers === 1 ? "paper" : "papers"}${p.together.grants ? `, ${number(p.together.grants)} ${p.together.grants === 1 ? "grant" : "grants"}` : ""}`
                : "not yet worked together")))))) }) : null,
        profile.timeline.length > 1 ? card({ title: "Activity", pad: true, attrs: { "data-entity": "publication" },
          body: h("div", { class: "stack-sm" },
            barChart(profile.timeline.slice(-10).map((t) => ({ label: `’${String(t.year).slice(-2)}`, value: t.publications, secondary: t.accepted })),
              { caption: "Papers per year (confirmed inside all likely papers)", height: 120 }),
            h("div", { class: "legend" }, h("span", {}, h("span", { class: "legend-swatch swatch-strong" }), "Confirmed"),
              h("span", {}, h("span", { class: "legend-swatch swatch-soft" }), "Likely")) ) }) : null,
        membershipsCard(profile),
        identifiersCard(profile, refresh),
        profile.merged.length ? card({ title: "Merged records", pad: true, body: h("ul", { class: "plain-list" }, profile.merged.map((m) =>
          h("li", { class: "row-sm" },
            h("div", { class: "grow stack-xs" }, h("a", { href: `/people/${m.merged_id}` }, m.merged_name),
              h("span", { class: "subtle" }, `merged ${relative(m.at)} by ${m.actor}`)),
            profile.can_curate ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: (e) => undoMerge(m.merge_id, e.currentTarget, refresh) }, "Undo") : null))) }) : null,
        profile.names.length > 1 ? card({ title: "Names in the data", pad: true,
          body: h("div", { class: "chips" }, profile.names.map((n) => h("span", { class: "chip chip-plain" }, `${n.given ? `${n.given} ` : ""}${n.family}`))) }) : null);

      return h("div", { class: "stack-lg" }, header, mergedNote, candidateNote, stats,
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

/** Groups the person belongs to, with roles, titles, and dates; past memberships last. */
function membershipsCard(profile) {
  if (!profile.units.length) return null;
  const span = (u) => u.start && u.end ? `${u.start} – ${u.end}` : u.start ? `since ${u.start}` : u.end ? `until ${u.end}` : null;
  const role = (u) => (u.role && u.role !== "member") || u.leader ? u.role_label : null;
  return card({ title: "Groups", pad: true, attrs: { "data-entity": "unit" },
    body: h("ul", { class: "plain-list memberships" }, profile.units.map((u) => {
      const details = [u.kind_label, role(u), u.title, span(u), u.current ? null : "past"].filter(Boolean);
      return h("li", { class: u.current ? "" : "past" },
        u.group_slug && u.group_status !== "merged"
          ? h("a", { class: u.current ? "strong" : "", href: `/groups/${u.group_slug}` }, u.unit)
          : h("span", { class: u.current ? "strong" : "" }, u.unit),
        details.length ? h("span", { class: "subtle" }, ` · ${details.join(" · ")}`) : null);
    })) });
}

async function undoMerge(mergeId, button, refresh) {
  button.disabled = true;
  try {
    await post(`/api/v1/merges/${encodeURIComponent(mergeId)}/undo`, {});
  } catch (error) {
    button.disabled = false;
    toast(error.detail || "The merge could not be undone.");
    return;
  }
  refresh();
  toast("Merge undone. Both records are back as they were.");
}

/** Merge this person with another record of the same person, found by name. */
function mergeWith(profile, refresh) {
  openDrawer({
    title: "Merge with another record",
    subtitle: profile.name,
    label: "Merge with another record",
    render: (body, footer, close) => {
      let chosen = null;
      const results = h("ul", { class: "plain-list merge-results", role: "listbox", "aria-label": "Matching people" });
      const keepThis = h("input", { type: "radio", name: "keep", value: "this", checked: true, id: "keep-this" });
      const keepOther = h("input", { type: "radio", name: "keep", value: "other", id: "keep-other" });
      const otherLabel = h("label", { for: "keep-other" }, "Keep the other record");
      const error = h("p", { class: "field-error", role: "alert", hidden: true });
      const submit = h("button", { type: "button", class: "btn btn-primary", disabled: true, onclick: async () => {
        const keep = keepThis.checked ? profile.id : chosen.id;
        const fold = keepThis.checked ? chosen.id : profile.id;
        submit.disabled = true;
        let result;
        try {
          result = (await post("/api/v1/merges", { merge: fold, into: keep })).data;
        } catch (failure) {
          error.textContent = failure.detail || "The records could not be merged.";
          error.hidden = false;
          submit.disabled = false;
          return;
        }
        close();
        if (fold === profile.id) navigate(`/people/${keep}`); else refresh();
        toast(`Merged. ${result.links} ${result.links === 1 ? "link" : "links"} moved to the record kept.`, {
          action: { label: "Undo", run: async () => {
            await post(`/api/v1/merges/${result.merge_id}/undo`, {});
            if (fold === profile.id) navigate(`/people/${profile.id}`); else refresh();
          } },
        });
      } }, "Merge");
      const search = h("input", { class: "input", type: "search", id: "merge-search", autocomplete: "off",
        placeholder: "Search people by name", value: profile.family_name ?? "",
        oninput: debounce(() => find(), 250) });
      async function find() {
        const text = search.value.trim();
        if (!text) { results.replaceChildren(); return; }
        const found = (await get("/api/v1/people", { q: text, limit: 8 })).data.filter((p) => p.id !== profile.id);
        results.replaceChildren(...(found.length ? found.map((p) => h("li", {},
          h("button", { type: "button", class: "merge-option", role: "option", "aria-selected": "false", onclick: (event) => {
            chosen = p;
            for (const b of results.querySelectorAll(".merge-option")) b.setAttribute("aria-selected", String(b === event.currentTarget));
            otherLabel.textContent = `Keep ${p.name}`;
            submit.disabled = false;
          } }, avatar(p.name, "sm"), h("span", { class: "stack-xs" }, h("strong", {}, p.name),
            h("span", { class: "subtle" }, [p.units?.join(", "), `${number((p.counts?.accepted ?? 0) + (p.counts?.suggested ?? 0))} papers`].filter(Boolean).join(" · "))))))
          : [h("li", { class: "muted" }, "No other person matches.")]));
      }
      body.append(h("div", { class: "stack" },
        h("p", { class: "muted" }, "For two records of the same person. Links, names, and identifiers move to the record you keep, and the merge can be undone."),
        h("div", { class: "field" }, h("label", { for: "merge-search" }, "The other record"), search),
        results,
        h("fieldset", { class: "field" }, h("legend", {}, "Which record to keep"),
          h("div", { class: "row-sm" }, keepThis, h("label", { for: "keep-this" }, `Keep ${profile.name}`)),
          h("div", { class: "row-sm" }, keepOther, otherLabel)),
        error));
      footer.replaceChildren(h("div", { class: "row wrap" }, submit, h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel")));
      footer.hidden = false;
      find();
    },
  });
}

/** What drives a concept profile, so its owner (and anyone) can see why it says what it says. */
/** Broader research areas, as shares with bars (widths set through the CSSOM). */
function areaList(areas) {
  const list = h("ul", { class: "plain-list area-list", "aria-label": "Research areas" }, areas.map((a) => h("li", {},
    h("a", { href: a.area_ui ? `/experts?concept=${a.area_ui}` : null, title: `Includes ${a.concepts.join(", ")}` }, a.area),
    h("span", { class: "area-bar", role: "img", "aria-label": `${Math.round(a.share * 100)}% of their topics` }, h("span", { "data-w": String(Math.max(4, Math.round(a.share * 100))) })),
    h("span", { class: "subtle tabular" }, `${Math.round(a.share * 100)}%`))));
  for (const bar of list.querySelectorAll(".area-bar > span")) bar.style.setProperty("width", `${bar.dataset.w}%`);
  return list;
}

function showProfile(profile, concepts, areas = [], meshYear = null) {
  const top = concepts[0]?.weight || 1;
  openDrawer({
    title: "How the topics are built",
    subtitle: profile.name,
    label: "How the topics are built",
    render: (body) => {
      const rows = concepts.map((c) => h("li", { class: "concept-row" },
        h("div", { class: "row-sm" }, h("strong", {}, c.descriptor), c.provisional ? h("span", { class: "pill" }, "from keywords") : null),
        h("div", { class: "concept-bar-track" }, h("span", { "data-w": String(Math.max(4, Math.round((c.weight / top) * 100))) })),
        h("span", { class: "subtle" }, `${number(c.papers)} ${c.papers === 1 ? "paper" : "papers"}${c.confirmed === c.papers ? "" : c.confirmed ? `, ${number(c.confirmed)} confirmed` : ", none confirmed yet"}`)));
      body.append(h("div", { class: "stack" },
        h("p", { class: "muted" }, "Each topic is a MeSH heading from their papers, counting major topics double, first and last authorship half again, and recent papers more (weight halves every five years). Papers suggested strongly but not yet confirmed count half. Topics many people share count less, so these are what set them apart."),
        areas.length ? h("h3", { class: "drawer-subhead" }, "Broader areas") : null,
        areas.length ? h("p", { class: "subtle" }, "Their topics rolled up the MeSH hierarchy to the diseases, body systems, organisms, and processes they study; areas almost everyone shares count less.") : null,
        areas.length ? areaList(areas) : null,
        h("h3", { class: "drawer-subhead" }, "Topics"),
        h("ol", { class: "plain-list concept-list" }, rows),
        meshYear ? h("p", { class: "subtle" }, `Headings, synonyms, and hierarchy: MeSH ${meshYear}, from the U.S. National Library of Medicine.`) : null));
      for (const bar of body.querySelectorAll(".concept-bar-track > span")) bar.style.setProperty("width", `${bar.dataset.w}%`);
    },
  });
}

/** A switch for whether a person or group has a page on the public site (tessera publish). */
export function publicToggle(path, isPublic, noun, refresh) {
  return h("label", { class: "row-sm public-toggle", title: `Whether ${noun === "profile" ? "this person has a profile" : "this group has a page"} on the public site` },
    h("input", { type: "checkbox", checked: Boolean(isPublic), onchange: async (event) => {
      const on = event.target.checked;
      try {
        await post(path, { public: on });
      } catch (error) {
        event.target.checked = !on;
        toast(error.detail || "That did not work.");
        return;
      }
      toast(on ? `The public site will show this ${noun} from its next publication.` : `This ${noun} leaves the public site at its next publication.`);
      refresh?.();
    } }), `Public ${noun}`);
}

