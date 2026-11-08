// Groups: the directory (/groups) and each group's showcase (/groups/<address>): what it does,
// its leaders and people, the publications attributed to it, its grants, studies, topics, and
// the groups it works with. Loaded on first use.

import { get, post } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { number, percent, plural } from "../core/format.js";
import { query, setQuery } from "../core/router.js";
import { can } from "../core/session.js";
import { openEvidence } from "../components/drawer.js";
import { grantItem, publicationItem, studyItem } from "../components/items.js";
import { toast } from "../components/toast.js";
import { avatar, barChart, card, debounce, emptyState, pageHeader, pager, pill, tabs } from "../components/ui.js";
import { show } from "./page.js";

const PAGE = 20;

function areaBars(areas, noun) {
  const list = h("ul", { class: "plain-list area-list", "aria-label": "Research areas" }, areas.map((a) => h("li", {},
    h("a", { href: a.area_ui ? `/experts?concept=${a.area_ui}` : null }, a.area),
    h("span", { class: "area-bar", role: "img", "aria-label": `${Math.round(a.share * 100)}% of ${noun}` },
      h("span", { "data-w": String(Math.max(4, Math.round(a.share * 100))) })),
    h("span", { class: "subtle tabular" }, percent(a.share)))));
  for (const bar of list.querySelectorAll(".area-bar > span")) bar.style.setProperty("width", `${bar.dataset.w}%`);
  return list;
}

/** The directory: every active group, by kind, with its leaders. */
export function groupsDirectory(main, ctx = {}) {
  const params = query();
  const kind = params.get("kind") || "";
  const q = params.get("q") || "";
  return show(main, {
    title: "Groups",
    quiet: ctx.samePage,
    load: () => get("/api/v1/groups", { q: q || null, kind: kind || null, limit: 200 }),
    view: (result) => {
      const vocab = result.meta.vocabulary;
      const kinds = result.meta.kinds.filter((k) => k.count);
      const search = h("input", { class: "input", type: "search", value: q, placeholder: `Find a ${vocab.group.toLowerCase()}`,
        "aria-label": `Find a ${vocab.group.toLowerCase()}`,
        oninput: debounce((event) => setQuery({ q: event.target.value.trim() || null }), 250) });
      const kindFilter = h("div", { class: "segmented", role: "group", "aria-label": "Kind" },
        [{ id: "", label: "All" }, ...kinds].map((k) => h("button", { type: "button", "aria-pressed": String(k.id === kind),
          onclick: () => setQuery({ kind: k.id || null }) }, k.label)));
      const groupCard = (g) => h("a", { class: "card card-pad group-card", href: `/groups/${g.slug}` },
        h("div", { class: "group-card-head" },
          h("span", { class: "entity-icon", "data-entity": "unit" }, icon("unit")),
          h("div", { class: "stack-xs grow" },
            h("strong", { class: "group-card-name" }, g.name),
            h("span", { class: "subtle" }, [g.kind_label, g.parents.map((p) => p.name).join(", ")].filter(Boolean).join(" · ")))),
        g.description ? h("p", { class: "muted clamp-2" }, g.description) : null,
        h("div", { class: "group-card-foot subtle" },
          g.leaders?.length ? h("span", {}, `Led by ${g.leaders.join(" and ")}`) : null,
          h("span", { class: "tabular" }, plural(g.members, "member"))));
      const sections = kind || q
        ? [h("div", { class: "grid-cards" }, result.data.map(groupCard))]
        : kinds.map((k) => {
          const items = result.data.filter((g) => g.kind === k.id);
          return items.length ? h("section", { class: "stack" },
            h("h2", { class: "section-title" }, `${k.label} `, h("span", { class: "subtle tabular" }, number(items.length))),
            h("div", { class: "grid-cards" }, items.map(groupCard))) : null;
        });
      return h("div", { class: "stack-lg" },
        pageHeader({
          eyebrow: "Explore",
          title: vocab.groups,
          meta: h("span", {}, "Divisions, programs, labs, and shared resources: who leads them, who belongs, and what they work on."),
          actions: can("admin") ? [h("a", { class: "btn", href: "/admin/groups" }, "Manage")] : null,
        }),
        h("div", { class: "toolbar" }, h("div", { class: "search-field" }, icon("search"), search)),
        kinds.length > 1 ? kindFilter : null,
        result.data.length ? sections : card({ pad: true, body: emptyState({ iconName: "unit",
          title: q || kind ? "No matches" : `No ${vocab.groups.toLowerCase()} yet`,
          text: q || kind ? "Try other words or another kind." : "An admin can add groups, or import them from your organization's records." }) }));
    },
  });
}

function personTile(m, { alumni = false } = {}) {
  const span = alumni ? [m.start, m.end].filter(Boolean).join(" – ") : m.start ? `since ${m.start}` : null;
  return h("li", { class: "row-sm person-tile" },
    avatar(m.name, "sm"),
    h("div", { class: "stack-xs grow" },
      h("a", { href: `/people/${m.person_id}` }, m.name),
      h("span", { class: "subtle" }, [m.role_label, m.title, span].filter(Boolean).join(" · "))));
}

/** One group's showcase. */
export function groupPage(main, params, ctx = {}) {
  const ref = params.slug;
  const state = query();
  const peopleTab = state.get("people") === "alumni" ? "alumni" : "current";
  const pubTab = state.get("papers") === "suggested" ? "suggested" : "accepted";
  const offset = Number(state.get("offset") || 0);
  const reload = () => groupPage(main, params, { samePage: true });
  return show(main, {
    title: (d) => d.group.name,
    quiet: ctx.samePage,
    load: async () => {
      const [overview, papers] = await Promise.all([
        get(`/api/v1/groups/${encodeURIComponent(ref)}/overview`, null, { fresh: ctx.samePage }),
        get(`/api/v1/groups/${encodeURIComponent(ref)}/publications`, { status: pubTab, limit: PAGE, offset }, { fresh: ctx.samePage }),
      ]);
      return { group: overview.data, vocab: overview.meta.vocabulary, meshYear: overview.meta.mesh_year, papers };
    },
    view: ({ group, vocab, meshYear, papers }) => {
      if (group.slug !== ref) window.history.replaceState(null, "", `/groups/${group.slug}${window.location.search}`);
      const contact = [
        group.website ? h("a", { class: "btn", href: group.website, rel: "noopener", target: "_blank" }, icon("external", "icon icon-sm"), "Website") : null,
        group.email ? h("a", { class: "btn", href: `mailto:${group.email}` }, "Contact") : null,
        can("admin") ? h("a", { class: "btn btn-ghost", href: `/admin/groups/${group.slug}` }, "Manage") : null,
      ].filter(Boolean);
      const header = pageHeader({
        lead: h("span", { class: "entity-icon entity-icon-lg", "data-entity": "unit" }, icon("unit")),
        eyebrow: h("span", { class: "row-sm wrap" }, h("a", { href: `/groups?kind=${group.kind}` }, group.kind_label),
          group.parents.length ? h("span", { class: "subtle" }, "within ",
            group.parents.map((p, i) => [i ? ", " : "", h("a", { href: `/groups/${p.id}` }, p.name)])) : null,
          group.status === "archived" ? pill("Archived", "warn") : null),
        title: group.name,
        meta: group.description ? h("p", { class: "lede" }, group.description) : null,
        actions: contact.length ? contact : null,
      });
      const stats = h("div", { class: "stats" },
        stat("person", "Members", group.people.length, group.alumni.length ? `${number(group.alumni.length)} alumni` : null),
        stat("publication", "Publications", group.publications.accepted,
          group.publications.suggested && group.can_edit ? `${number(group.publications.suggested)} to review` : "attributed to the group"),
        stat("grant", "Grants", group.grants.length, "held by members"),
        stat("study", "Studies", group.studies.length, "led by members"));
      const leaders = group.leaders.length ? card({ title: group.leaders.length > 1 ? "Leaders" : "Leader", pad: true, attrs: { "data-entity": "person" },
        body: h("ul", { class: "plain-list leader-list" }, group.leaders.map((m) => h("li", { class: "row" },
          avatar(m.name, "lg"), h("div", { class: "stack-xs" },
            h("a", { class: "strong", href: `/people/${m.person_id}` }, m.name),
            h("span", { class: "subtle" }, [m.role_label, m.title].filter(Boolean).join(" · ")))))) }) : null;
      const shownPeople = peopleTab === "alumni" ? group.alumni : group.people;
      const people = card({
        title: "People",
        attrs: { "data-entity": "person" },
        action: group.alumni.length ? tabs([{ id: "current", label: "Current", count: group.people.length },
          { id: "alumni", label: "Alumni", count: group.alumni.length }], peopleTab,
        (id) => setQuery({ people: id === "alumni" ? "alumni" : null })) : null,
        pad: true,
        body: shownPeople.length
          ? h("ul", { class: "plain-list people-grid" }, shownPeople.map((m) => personTile(m, { alumni: peopleTab === "alumni" })))
          : h("p", { class: "subtle" }, peopleTab === "alumni" ? "No alumni recorded." : "No current members recorded."),
      });
      const paperTabs = group.can_edit && group.publications.suggested
        ? tabs([{ id: "accepted", label: "Attributed", count: group.publications.accepted },
          { id: "suggested", label: "To review", count: group.publications.suggested }], pubTab,
        (id) => setQuery({ papers: id === "suggested" ? "suggested" : null, offset: null }))
        : null;
      const chart = pubTab === "accepted" && group.publications.by_year.length > 1
        ? barChart(group.publications.by_year.map((y) => ({ label: String(y.year).slice(2), value: y.count })),
          { caption: "Publications attributed to the group, by year" })
        : null;
      const paperRows = papers.data.map((item) => pubTab === "suggested"
        ? suggestionRow(group, item, reload)
        : publicationItem(item.summary));
      const publications = card({
        title: "Publications",
        attrs: { "data-entity": "publication" },
        action: paperTabs,
        body: h("div", {},
          chart ? h("div", { class: "card-body" }, chart) : null,
          paperRows.length ? h("div", { class: "items" }, paperRows)
            : h("div", { class: "card-body" }, emptyState({ kind: "publication",
              title: pubTab === "suggested" ? "Nothing to review" : "No publications attributed yet",
              text: pubTab === "suggested" ? "Every suggested paper has been decided."
                : "A paper becomes the group's when someone who curates the group says so; a member's authorship alone is not enough." })),
          pager({ total: papers.meta.total, offset, limit: PAGE, onPage: (next) => setQuery({ offset: next || null }) })),
      });
      const grants = group.grants.length ? card({ title: "Grants", attrs: { "data-entity": "grant" },
        body: h("div", { class: "items" }, group.grants.slice(0, 12).map((g) => grantItem(g.summary))) }) : null;
      const studies = group.studies.length ? card({ title: "Studies", attrs: { "data-entity": "study" },
        body: h("div", { class: "items" }, group.studies.slice(0, 12).map((s) => studyItem(s.summary))) }) : null;
      const topics = group.topics.length ? card({ title: "Topics", pad: true, attrs: { "data-entity": "concept" },
        body: h("div", { class: "stack" },
          group.areas.length ? areaBars(group.areas, "the group's topics") : null,
          h("div", { class: "chips" }, group.topics.map((t) => h("a", { class: "chip", href: `/experts?concept=${t.descriptor_ui}`,
            title: `${plural(t.people, "member")} work on this` }, t.descriptor))),
          h("p", { class: "subtle" }, "What the group's current members work on, from their papers' MeSH headings.",
            meshYear ? ` Areas: MeSH ${meshYear}, from the U.S. National Library of Medicine.` : "")) }) : null;
      const connections = group.connections.length ? card({ title: "Writes most with", pad: true, attrs: { "data-entity": "unit" },
        body: h("ul", { class: "plain-list" }, group.connections.map((c) => h("li", { class: "row-sm" },
          h("a", { class: "grow", href: `/groups/${c.slug}` }, c.name),
          h("span", { class: "subtle tabular nowrap", title: `${plural(c.people, "person", "people")} there` }, plural(c.papers, "paper"))))),
          }) : null;
      const within = group.children.length ? card({ title: "Within this group", pad: true, attrs: { "data-entity": "unit" },
        body: h("ul", { class: "plain-list" }, group.children.map((c) => h("li", { class: "row-sm" },
          h("a", { class: "grow", href: `/groups/${c.slug}` }, c.name),
          h("span", { class: "subtle" }, `${c.kind_label} · ${plural(c.members, "member")}`)))) }) : null;
      return h("div", { class: "stack-lg" }, header, stats,
        h("div", { class: "grid-main" },
          h("div", { class: "stack-lg" }, leaders, people, publications, grants, studies),
          h("div", { class: "stack-lg" }, topics, connections, within)));
    },
  });
}

function stat(kind, label, value, note) {
  return h("div", { class: "card stat", "data-entity": kind },
    h("div", { class: "stat-label" }, label),
    h("div", { class: "stat-value" }, number(value)),
    note ? h("div", { class: "stat-note" }, note) : null);
}

function suggestionRow(group, item, reload) {
  const a = item.assertion;
  const decide = (action) => async () => {
    try {
      await post(`/api/v1/groups/${group.id}/publications/decisions`, { ids: [a.id], action });
    } catch (failure) {
      toast(failure.detail || "That did not work.");
      return;
    }
    toast(action === "accept" ? "Attributed to the group." : "Not the group's.");
    reload();
  };
  const reasons = (a.evidence ?? []).map((e) => e.explanation).filter(Boolean);
  return h("article", { class: "item" },
    h("div", { class: "item-main" },
      h("a", { class: "item-title", href: `/publications/${item.summary.pmid}` }, item.summary.title ?? "(untitled)"),
      h("div", { class: "item-meta" }, item.summary.journal ? h("span", {}, item.summary.journal) : null,
        item.summary.year ? [h("span", { class: "sep" }), h("span", { class: "tabular" }, item.summary.year)] : null),
      reasons.length ? h("ul", { class: "plain-list compact reasons" }, reasons.map((r) => h("li", { class: "subtle" }, r))) : null),
    h("div", { class: "item-side" },
      h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => openEvidence(a.id, { onChange: reload }) }, "Evidence"),
      h("div", { class: "row-sm" },
        h("button", { type: "button", class: "btn btn-sm btn-accept", onclick: decide("accept") }, icon("check", "icon icon-sm"), "The group's"),
        h("button", { type: "button", class: "btn btn-sm btn-reject", onclick: decide("reject") }, "Not"))));
}
