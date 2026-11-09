// Teams for an opportunity (INT-10): who is on it, which of its topics they cover, who could
// fill the gaps, who has worked together, and a brief to send. Loaded on first use.

import { get, invalidate, post, request } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { date, number, plural, relative } from "../core/format.js";
import { navigate } from "../core/router.js";
import { openDrawer } from "../components/drawer.js";
import { toast } from "../components/toast.js";
import { avatar, callout, card, debounce, emptyState, pageHeader, pill } from "../components/ui.js";
import { show } from "./page.js";

function api(id, path = "") {
  return `/api/v1/teams/${encodeURIComponent(id)}${path}`;
}

function shortName(name) {
  const parts = name.split(" ");
  return parts.length > 1 ? `${parts[0][0]}. ${parts.at(-1)}` : name;
}

// --- the list ---------------------------------------------------------------------------------

export function teamsPage(main) {
  return show(main, {
    title: "Teams",
    load: () => get("/api/v1/teams", null, { fresh: true }),
    view: ({ data }) => h("div", { class: "stack-lg" },
      pageHeader({ eyebrow: "Explore", title: "Teams",
        meta: [h("span", {}, "People assembled for a funding opportunity, with the topics they cover and those still missing.")],
        actions: [h("button", { type: "button", class: "btn btn-primary", onclick: () => newTeam() }, icon("plus", "icon icon-sm"), "Start a team")] }),
      data.length ? card({ body: h("div", { class: "items" }, data.map((t) => h("article", { class: "item" },
        h("div", { class: "item-main" },
          h("a", { class: "item-title", href: `/teams/${t.id}` }, t.name),
          h("div", { class: "item-meta" }, h("span", {}, plural(t.members, "member")), h("span", { class: "sep" }),
            h("span", {}, plural(t.concepts, "topic")), h("span", { class: "sep" }),
            h("span", {}, t.mine ? "Yours" : `Shared by ${t.owner_name}`), h("span", { class: "sep" }), h("span", {}, `changed ${relative(t.updated_at)}`)),
          t.purpose ? h("p", { class: "muted clamp-2" }, t.purpose) : null)))) })
        : emptyState({ kind: "person", title: "No teams yet", text: "Open an opportunity and choose Start a team, or start one for topics you choose." })),
  });
}

function newTeam() {
  const name = h("input", { class: "input", id: "team-name", required: true });
  const purpose = h("textarea", { class: "input", id: "team-purpose", rows: 3 });
  openDrawer({ title: "Start a team", label: "Start a team", render: (body, footer, close) => {
    const error = h("p", { class: "field-error", role: "alert", hidden: true });
    body.append(h("form", { class: "stack", novalidate: true, onsubmit: async (event) => {
      event.preventDefault();
      try {
        const made = (await post("/api/v1/teams", { name: name.value.trim(), purpose: purpose.value.trim() || null })).data;
        close();
        navigate(`/teams/${made.id}`);
      } catch (failure) {
        error.textContent = failure.detail || failure.message;
        error.hidden = false;
      }
    } },
    h("div", { class: "field" }, h("label", { for: "team-name" }, "Name"), name),
    h("div", { class: "field" }, h("label", { for: "team-purpose" }, "Purpose"), purpose),
    h("p", { class: "subtle" }, "To start from an opportunity instead, open it and choose Start a team: its topics come with it."),
    error, h("div", { class: "row" }, h("button", { type: "submit", class: "btn btn-primary" }, "Start"),
      h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel"))));
  } });
}

// --- the builder --------------------------------------------------------------------------------

function coverageTable(t) {
  const c = t.coverage;
  if (!t.members.length) return h("p", { class: "muted" }, "Add people to see which topics they cover.");
  return h("div", { class: "table-wrap" }, h("table", { class: "table coverage-table", "data-dense": "" },
    h("caption", { class: "sr-only" }, "Topics the team needs, and who covers each"),
    h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Topic"),
      t.members.map((m) => h("th", { scope: "col", class: "num", title: m.name }, shortName(m.name))))),
    h("tbody", {}, c.concepts.map((row) => h("tr", { "data-missing": Object.keys(row.covered_by).length ? null : "" },
      h("th", { scope: "row" }, row.descriptor),
      t.members.map((m) => {
        const hit = row.covered_by[m.id];
        return h("td", { class: "num", title: hit ? `${hit.descriptor}${hit.papers ? `, ${plural(hit.papers, "paper")}` : ""}` : "Not among their topics" },
          hit ? h("span", { class: "covered-mark" }, icon("check", "icon icon-sm"), h("span", { class: "sr-only" }, `${m.name} covers it`)) : h("span", { class: "subtle" }, "–"));
      }))))));
}

function addPersonButton(t, person, reload, label = "Add") {
  return h("button", { type: "button", class: "btn btn-sm", "aria-label": `${label} ${person.name} to the team`, onclick: async () => {
    await post(api(t.id, "/members"), { person_id: person.id, role: "collaborator" });
    invalidate();
    toast(`Added ${person.name}.`);
    reload();
  } }, icon("plus", "icon icon-sm"), label);
}

function gaps(t, reload) {
  const missing = t.coverage.missing;
  if (!t.members.length || !missing.length) return null;
  return card({ title: `Missing topics (${number(missing.length)})`, pad: true, attrs: { "data-entity": "concept" },
    body: h("div", { class: "stack" },
      h("p", { class: "subtle" }, "Nobody on the team covers these. People who do are suggested by how well they cover the gap, with their reasons."),
      h("ul", { class: "plain-list fit-list" }, missing.map((m) => h("li", {},
        h("strong", {}, m.descriptor),
        m.suggestions.length ? h("ul", { class: "plain-list pick-list" }, m.suggestions.map((s) => h("li", {},
          h("a", { class: "grow", href: `/people/${s.id}` }, s.name),
          h("span", { class: "subtle" }, s.matches.slice(0, 2).map((x) => `${x.descriptor}${x.papers ? ` (${number(x.papers)})` : ""}`).join(" · ")),
          addPersonButton(t, s, reload))))
          : h("span", { class: "subtle" }, "Nobody in the organization covers it yet."))))) });
}

function members(t, reload) {
  const results = h("ul", { class: "plain-list pick-list" });
  const input = h("input", { class: "input", type: "search", placeholder: "Find someone to add", "aria-label": "Find someone to add" });
  input.addEventListener("input", debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { results.replaceChildren(); return; }
    const found = (await get("/api/v1/people", { q, limit: 6 })).data;
    results.replaceChildren(...found.filter((p) => !t.members.some((m) => m.id === p.id)).map((p) => h("li", {},
      h("span", { class: "grow" }, p.name), addPersonButton(t, p, reload))));
  }, 250));
  return card({ title: `Members (${number(t.members.length)})`, pad: true, attrs: { "data-entity": "person" }, body: h("div", { class: "stack" },
    t.members.length ? h("ul", { class: "plain-list fit-list" }, t.members.map((m) => {
      const role = h("select", { class: "input input-sm", "aria-label": `Role of ${m.name}`, onchange: async (event) => {
        await post(api(t.id, "/members"), { person_id: m.id, role: event.target.value, note: m.note });
        invalidate();
        reload();
      } }, t.roles.map((r) => h("option", { value: r.value, selected: r.value === m.role ? true : null }, r.label)));
      return h("li", {}, h("div", { class: "row-sm" }, avatar(m.name, "sm"), h("a", { class: "grow", href: `/people/${m.id}` }, m.name), role,
        h("button", { type: "button", class: "icon-button", "aria-label": `Take ${m.name} off the team`, title: "Take off the team", onclick: async () => {
          await request("DELETE", api(t.id, `/members/${m.id}`));
          invalidate();
          reload();
        } }, icon("close", "icon icon-sm"))));
    })) : h("p", { class: "muted" }, "Nobody yet."),
    input, results) });
}

function topics(t, reload) {
  const save = async (concepts) => {
    await post(api(t.id), { concepts });
    invalidate();
    reload();
  };
  const results = h("div", { class: "chips" });
  const input = h("input", { class: "input", type: "search", placeholder: "Add a topic", "aria-label": "Add a topic the team needs" });
  input.addEventListener("input", debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { results.replaceChildren(); return; }
    const found = (await get("/api/v1/concepts", { q })).data.slice(0, 6);
    results.replaceChildren(...found.map((c) => h("button", { type: "button", class: "chip", "data-entity": "concept",
      onclick: () => save([...t.concepts, { descriptor_ui: c.descriptor_ui, descriptor: c.descriptor }]) }, icon("plus", "icon icon-sm"), c.descriptor)));
  }, 250));
  return card({ title: "Topics it needs", pad: true, attrs: { "data-entity": "concept" }, body: h("div", { class: "stack-sm" },
    t.concepts.length ? h("div", { class: "chips" }, t.concepts.map((c) => h("span", { class: "chip", "data-entity": "concept" }, c.descriptor,
      h("button", { type: "button", class: "chip-remove", "aria-label": `Remove ${c.descriptor}`,
        onclick: () => save(t.concepts.filter((x) => x.descriptor_ui !== c.descriptor_ui)) }, icon("close", "icon icon-sm")))))
      : h("p", { class: "muted" }, "No topics yet. Add the ones the work needs."),
    input, results) });
}

function pairings(t) {
  const c = t.coverage;
  if (t.members.length < 2) return null;
  const name = (id) => t.members.find((m) => m.id === id)?.name ?? id;
  return card({ title: "Worked together", pad: true, body: h("div", { class: "stack-sm" },
    c.together.length ? h("ul", { class: "plain-list" }, c.together.map((p) => h("li", {},
      `${p.people.map(name).join(" and ")}: `, [p.papers ? plural(p.papers, "paper") : null, p.grants ? plural(p.grants, "grant") : null,
        p.studies ? plural(p.studies, "study", "studies") : null].filter(Boolean).join(", ") || "in the data", p.last_year ? `, last in ${p.last_year}` : "")))
      : h("p", { class: "muted" }, "Nobody on the team has worked with another member yet."),
    c.new_pairs.length ? h("p", { class: "subtle" }, `${plural(c.new_pairs.length, "pairing")} would be new.`) : null) });
}

function groupsSpanned(t) {
  const groups = t.coverage.groups;
  if (!groups.length) return null;
  return card({ title: "Groups it spans", pad: true, attrs: { "data-entity": "unit" }, body: h("ul", { class: "plain-list" }, groups.slice(0, 10).map((g) =>
    h("li", { class: "row-sm" }, h("a", { class: "grow", href: `/groups/${g.slug}` }, g.name), h("span", { class: "subtle" }, plural(g.members.length, "member"))))) });
}

function editTeam(t, reload) {
  const name = h("input", { class: "input", id: "team-name", value: t.name });
  const purpose = h("textarea", { class: "input", id: "team-purpose", rows: 3 }, t.purpose ?? "");
  const notes = h("textarea", { class: "input", id: "team-notes", rows: 4 }, t.notes ?? "");
  openDrawer({ title: "Edit the team", label: "Edit the team", render: (body, footer, close) => {
    const error = h("p", { class: "field-error", role: "alert", hidden: true });
    body.append(h("form", { class: "stack", novalidate: true, onsubmit: async (event) => {
      event.preventDefault();
      try {
        await post(api(t.id), { name: name.value.trim(), purpose: purpose.value.trim() || null, notes: notes.value.trim() || null });
        invalidate();
        close();
        reload();
      } catch (failure) {
        error.textContent = failure.detail || failure.message;
        error.hidden = false;
      }
    } },
    h("div", { class: "field" }, h("label", { for: "team-name" }, "Name"), name),
    h("div", { class: "field" }, h("label", { for: "team-purpose" }, "Purpose"), purpose),
    h("div", { class: "field" }, h("label", { for: "team-notes" }, "Notes"), notes),
    error,
    h("div", { class: "row wrap" }, h("button", { type: "submit", class: "btn btn-primary" }, "Save"),
      h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel"),
      t.can_share ? h("button", { type: "button", class: "btn btn-ghost", onclick: async () => {
        await request("DELETE", api(t.id));
        invalidate();
        close();
        navigate("/teams");
        toast("Deleted the team.");
      } }, icon("trash", "icon icon-sm"), "Delete the team") : null)));
  } });
}

export function teamPage(main, params) {
  const id = params.id;
  const reload = () => teamPage(main, params);
  return show(main, {
    title: (t) => t.name,
    load: async () => (await get(api(id), null, { fresh: true })).data,
    view: (t) => {
      const c = t.coverage;
      const o = t.opportunity_view;
      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: o ? h("span", {}, "Team for ", h("a", { href: `/opportunities/${encodeURIComponent(o.id.replace(/^opp:/, ""))}` }, o.number ?? "an opportunity")) : "Team",
          title: t.name,
          meta: [t.purpose ? h("span", {}, t.purpose) : null, h("span", { class: "subtle" }, `Started by ${t.owner_name}`),
            o?.closes ? h("span", {}, `Closes ${date(o.closes)}`) : null],
          actions: [h("a", { class: "btn", href: `/teams/${t.id}/brief` }, icon("download", "icon icon-sm"), "Team brief"),
            h("button", { type: "button", class: "btn btn-ghost", onclick: () => editTeam(t, reload) }, icon("edit", "icon icon-sm"), "Edit")] }),
        c.ready ? null : callout("Topics are matched once the concept profiles are built (tessera sync, or tessera build discovery).", "info"),
        h("div", { class: "grid-main" },
          h("div", { class: "stack-lg" },
            card({ title: "Coverage", pad: true, action: t.concepts.length ? pill(`${number(c.covered)} of ${number(t.concepts.length)} topics covered`, c.missing.length ? "warn" : "ok") : null,
              body: h("div", { class: "stack-sm" }, coverageTable(t),
                h("p", { class: "subtle" }, "A member covers a topic when it, or a narrower one, is a prominent part of the topics of their confirmed work. Hover a mark for the heading and its papers. This says what their papers are about, not what they could do.")) }),
            gaps(t, reload),
            t.people_who_fit.length ? card({ title: "Others who fit the opportunity", pad: true, attrs: { "data-entity": "opportunity" },
              body: h("ul", { class: "plain-list pick-list" }, t.people_who_fit.map((p) => h("li", {},
                h("a", { class: "grow", href: `/people/${p.id}` }, p.name), pill(p.band_label, p.band === "strong" ? "ok" : ""), addPersonButton(t, p, reload)))) }) : null),
          h("div", { class: "stack-lg" }, members(t, reload), topics(t, reload), pairings(t), groupsSpanned(t))));
    },
  });
}

// --- the brief -----------------------------------------------------------------------------------

export function briefPage(main, params) {
  const id = params.id;
  return show(main, {
    title: (b) => `${b.team.name}: brief`,
    load: async () => (await get(api(id, "/brief"), null, { fresh: true })).data,
    view: (b) => {
      const o = b.opportunity;
      return h("article", { class: "stack-lg brief" },
        h("div", { class: "row wrap no-print" },
          h("a", { class: "btn btn-ghost", href: `/teams/${id}` }, icon("chevronLeft", "icon icon-sm"), "Back to the team"),
          h("span", { class: "grow" }),
          h("button", { type: "button", class: "btn", onclick: () => window.print() }, "Print or save as PDF"),
          h("a", { class: "btn", href: api(id, "/brief?format=markdown") }, icon("download", "icon icon-sm"), "Markdown"),
          h("a", { class: "btn", href: api(id, "/brief?format=csv") }, icon("download", "icon icon-sm"), "Members (CSV)")),
        h("header", { class: "stack-sm" }, h("p", { class: "eyebrow" }, `${b.organization} · team brief`), h("h1", { class: "page-title" }, b.team.name),
          b.team.purpose ? h("p", { class: "lede" }, b.team.purpose) : null,
          o ? h("p", {}, h("strong", {}, "Opportunity: "), `${o.title} (${o.number ?? o.id}; ${o.sponsor}${o.closes ? `; closes ${date(o.closes)}` : ""}).`) : null),
        h("section", { class: "stack" }, h("h2", {}, "Members"),
          b.members.map((m) => h("section", { class: "brief-member" },
            h("h3", {}, `${m.name}, ${m.role_label.toLowerCase()}`),
            m.note ? h("p", {}, m.note) : null,
            m.covers.length ? h("p", { class: "subtle" }, `Covers ${m.covers.slice(0, 8).join(", ")}${m.covers.length > 8 ? ", and more" : ""}.`) : null,
            h("ul", {},
              m.papers.length ? m.papers.map((p) => h("li", {}, `${p.title} `, h("em", {}, p.journal ?? ""), ` ${p.year ?? ""} (PMID ${p.pmid})`)) : h("li", { class: "subtle" }, "No confirmed papers yet."),
              m.grants.map((g) => h("li", {}, `Grant ${g.core} (${g.activity}): ${g.title}`)),
              m.studies.map((s) => h("li", {}, `Study ${s.nct_id}: ${s.title} (registry status: ${s.status})`)))))),
        h("section", { class: "stack-sm" }, h("h2", {}, "Coverage"),
          h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Topic"), h("th", { scope: "col" }, "Covered by"))),
            h("tbody", {}, b.coverage.map((c) => h("tr", {}, h("td", {}, c.topic), h("td", {}, c.covered_by.join(", ") || "Nobody yet")))))),
        b.groups.length ? h("section", { class: "stack-sm" }, h("h2", {}, "Groups spanned"), h("ul", {}, b.groups.map((g) => h("li", {}, `${g.name} (${g.members})`)))) : null,
        h("footer", { class: "stack-xs subtle" }, b.notes.map((n) => h("p", {}, n)),
          h("p", {}, `From Tessera at ${b.organization}, ${date(b.generated_at)}. `,
            b.sources.filter((s) => s.last_success_at).map((s) => `${s.label} fetched ${date(s.last_success_at)}`).join("; "), ".")));
    },
  });
}

export function startTeamFor(opportunityId) {
  return post("/api/v1/teams", { opportunity: opportunityId }).then((r) => {
    invalidate();
    navigate(`/teams/${r.data.id}`);
  });
}

