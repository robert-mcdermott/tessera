// Groups: the directory (/groups) and each group's showcase (/groups/<address>): what it does,
// its leaders and people, the publications attributed to it, its grants, studies, topics, and
// the groups it works with. Loaded on first use.

import { get, post } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { number, paragraphs, percent, plural } from "../core/format.js";
import { query, setQuery } from "../core/router.js";
import { can } from "../core/session.js";
import { openDrawer, openEvidence } from "../components/drawer.js";
import { grantItem, opportunityList, publicationItem, studyItem } from "../components/items.js";
import { toast } from "../components/toast.js";
import { avatar, barChart, callout, card, debounce, emptyState, pageHeader, pager, pill, tabs } from "../components/ui.js";
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

/** One group's showcase, built from its page's blocks (or a draft or older version, to preview). */
export function groupPage(main, params, ctx = {}) {
  const ref = params.slug;
  const state = query();
  const peopleTab = state.get("people") === "alumni" ? "alumni" : "current";
  const pubTab = state.get("papers") === "suggested" ? "suggested" : "accepted";
  const offset = Number(state.get("offset") || 0);
  const version = state.get("preview") === "draft" ? "draft" : (state.get("version") || "published");
  const reload = () => groupPage(main, params, { samePage: true });
  return show(main, {
    title: (d) => d.group.name,
    quiet: ctx.samePage,
    load: async () => {
      const base = `/api/v1/groups/${encodeURIComponent(ref)}`;
      const [overview, page, papers, opportunities] = await Promise.all([
        get(`${base}/overview`, null, { fresh: ctx.samePage }),
        get(`${base}/page`, { version }, { fresh: true }),
        get(`${base}/publications`, { status: pubTab, limit: PAGE, offset }, { fresh: ctx.samePage }),
        get(`${base}/opportunities`).then((r) => r.data).catch(() => []),
      ]);
      return { group: overview.data, vocab: overview.meta.vocabulary, meshYear: overview.meta.mesh_year, page: page.data, papers, opportunities };
    },
    view: ({ group, vocab, meshYear, page, papers, opportunities }) => {
      if (group.slug !== ref) window.history.replaceState(null, "", `/groups/${group.slug}${window.location.search}`);
      const editor = page.can_edit;
      const actions = [
        group.website ? h("a", { class: "btn", href: group.website, rel: "noopener", target: "_blank" }, icon("external", "icon icon-sm"), "Website") : null,
        group.email ? h("a", { class: "btn", href: `mailto:${group.email}` }, "Contact") : null,
        editor ? h("a", { class: "btn btn-primary", href: `/groups/${group.slug}/edit` }, "Edit page") : null,
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
        actions: actions.length ? actions : null,
      });
      const banner = page.version === "published" ? null : callout(h("span", {},
        page.version === "draft" ? "You are looking at the draft of this page, as it will look once published. "
          : `You are looking at version ${page.version} of this page. `,
        h("a", { href: `/groups/${group.slug}` }, "See the published page"),
        editor ? [" or ", h("a", { href: `/groups/${group.slug}/edit` }, "go back to the editor")] : null, "."), "warn");
      const stats = h("div", { class: "stats" },
        stat("person", "Members", group.people.length, group.alumni.length ? `${number(group.alumni.length)} alumni` : null),
        stat("publication", "Publications", group.publications.accepted,
          group.publications.suggested && group.can_edit ? `${number(group.publications.suggested)} to review` : "attributed to the group"),
        stat("grant", "Grants", group.grants.length, "held by members"),
        stat("study", "Studies", group.studies.length, "led by members"));
      const ctxBlocks = { group, vocab, meshYear, page, papers, peopleTab, pubTab, offset, reload };
      const main_ = [];
      const side = [];
      for (const block of page.blocks) {
        const nodes = [RENDER[block.type]?.(block, ctxBlocks)].flat().filter(Boolean);
        (SIDE.has(block.type) ? side : main_).push(...nodes);
      }
      if (opportunities.length) {
        side.push(card({ title: "Opportunities that fit", pad: true, attrs: { "data-entity": "opportunity" },
          action: h("a", { href: `/opportunities?group=${encodeURIComponent(group.slug)}&status=posted&status=forecasted` }, "All"),
          body: h("div", { class: "stack-sm" }, opportunityList(opportunities, 4),
            h("p", { class: "subtle" }, "Open opportunities that fit through the group's members. Shown only inside the organization.")) }));
      }
      if (group.children.length) {
        side.push(card({ title: "Within this group", pad: true, attrs: { "data-entity": "unit" },
          body: h("ul", { class: "plain-list" }, group.children.map((c) => h("li", { class: "row-sm" },
            h("a", { class: "grow", href: `/groups/${c.slug}` }, c.name),
            h("span", { class: "subtle" }, `${c.kind_label} · ${plural(c.members, "member")}`)))) }));
      }
      return h("div", { class: "stack-lg" }, banner, header, stats,
        h("div", { class: "grid-main" }, h("div", { class: "stack-lg" }, main_), h("div", { class: "stack-lg" }, side)));
    },
  });
}

const SIDE = new Set(["topics", "connections", "contact"]);

function textBody(text) {
  return paragraphs(text).map((p) => h("p", {}, p));
}

const RENDER = {
  about: (b) => b.text
    ? card({ title: b.title ?? "About", pad: true, body: h("div", { class: "prose" }, textBody(b.text)) })
    : null,
  people: (b, { group, peopleTab }) => {
    const leaders = group.leaders.length ? card({ title: group.leaders.length > 1 ? "Leaders" : "Leader", pad: true, attrs: { "data-entity": "person" },
      body: h("ul", { class: "plain-list leader-list" }, group.leaders.map((m) => h("li", { class: "row" },
        avatar(m.name, "lg"), h("div", { class: "stack-xs" },
          h("a", { class: "strong", href: `/people/${m.person_id}` }, m.name),
          h("span", { class: "subtle" }, [m.role_label, m.title].filter(Boolean).join(" · ")))))) }) : null;
    const showAlumni = b.show_alumni !== false && group.alumni.length;
    const tab = showAlumni ? peopleTab : "current";
    const shown = tab === "alumni" ? group.alumni : group.people;
    const people = card({
      title: b.title ?? "People",
      attrs: { "data-entity": "person" },
      action: showAlumni ? tabs([{ id: "current", label: "Current", count: group.people.length },
        { id: "alumni", label: "Alumni", count: group.alumni.length }], tab,
      (id) => setQuery({ people: id === "alumni" ? "alumni" : null })) : null,
      pad: true,
      body: shown.length
        ? h("ul", { class: "plain-list people-grid" }, shown.map((m) => personTile(m, { alumni: tab === "alumni" })))
        : h("p", { class: "subtle" }, tab === "alumni" ? "No alumni recorded." : "No current members recorded."),
    });
    return [leaders, people];
  },
  publications: (b, { group, page, papers, pubTab, offset, reload }) => {
    const featured = (b.featured ?? []).map((pmid) => page.featured[pmid]).filter(Boolean);
    const paperTabs = group.can_edit && group.publications.suggested
      ? tabs([{ id: "accepted", label: "Attributed", count: group.publications.accepted },
        { id: "suggested", label: "To review", count: group.publications.suggested }], pubTab,
      (id) => setQuery({ papers: id === "suggested" ? "suggested" : null, offset: null }))
      : null;
    const chart = pubTab === "accepted" && b.show_chart !== false && group.publications.by_year.length > 1
      ? barChart(group.publications.by_year.map((y) => ({ label: String(y.year).slice(2), value: y.count })),
        { caption: "Publications attributed to the group, by year" })
      : null;
    const limit = b.limit ?? PAGE;
    const rows = papers.data.slice(0, limit).map((item) => pubTab === "suggested"
      ? suggestionRow(group, item, reload)
      : publicationItem(item.summary));
    return [
      featured.length ? card({ title: "Featured publications", attrs: { "data-entity": "publication" },
        body: h("div", { class: "items" }, featured.map((s) => publicationItem(s))) }) : null,
      card({
        title: b.title ?? "Publications",
        attrs: { "data-entity": "publication" },
        action: paperTabs,
        body: h("div", {},
          chart ? h("div", { class: "card-body" }, chart) : null,
          rows.length ? h("div", { class: "items" }, rows)
            : h("div", { class: "card-body" }, emptyState({ kind: "publication",
              title: pubTab === "suggested" ? "Nothing to review" : "No publications attributed yet",
              text: pubTab === "suggested" ? "Every suggested paper has been decided."
                : "A paper becomes the group's when someone who curates the group says so; a member's authorship alone is not enough." })),
          pager({ total: Math.min(papers.meta.total, limit === PAGE ? papers.meta.total : limit), offset, limit: Math.min(limit, PAGE),
            onPage: (next) => setQuery({ offset: next || null }) })),
      }),
    ];
  },
  grants: (b, { group }) => group.grants.length ? card({ title: b.title ?? "Grants", attrs: { "data-entity": "grant" },
    body: h("div", { class: "items" }, group.grants.slice(0, b.limit ?? 12).map((g) => grantItem(g.summary))) }) : null,
  studies: (b, { group }) => group.studies.length ? card({ title: b.title ?? "Studies", attrs: { "data-entity": "study" },
    body: h("div", { class: "items" }, group.studies.slice(0, b.limit ?? 12).map((s) => studyItem(s.summary))) }) : null,
  topics: (b, { group, meshYear }) => group.topics.length ? card({ title: b.title ?? "Topics", pad: true, attrs: { "data-entity": "concept" },
    body: h("div", { class: "stack" },
      group.areas.length ? areaBars(group.areas, "the group's topics") : null,
      h("div", { class: "chips" }, group.topics.map((t) => h("a", { class: "chip", href: `/experts?concept=${t.descriptor_ui}`,
        title: `${plural(t.people, "member")} work on this` }, t.descriptor))),
      h("p", { class: "subtle" }, "What the group's current members work on, from their papers' MeSH headings.",
        meshYear ? ` Areas: MeSH ${meshYear}, from the U.S. National Library of Medicine.` : "")) }) : null,
  connections: (b, { group }) => group.connections.length ? card({ title: b.title ?? "Writes most with", pad: true, attrs: { "data-entity": "unit" },
    body: h("ul", { class: "plain-list" }, group.connections.map((c) => h("li", { class: "row-sm" },
      h("a", { class: "grow", href: `/groups/${c.slug}` }, c.name),
      h("span", { class: "subtle tabular nowrap", title: `${plural(c.people, "person", "people")} there` }, plural(c.papers, "paper"))))) }) : null,
  contact: (b, { group }) => (group.email || group.website || b.text) ? card({ title: b.title ?? "Contact", pad: true,
    body: h("div", { class: "stack-sm" },
      b.text ? textBody(b.text) : null,
      group.email ? h("a", { href: `mailto:${group.email}` }, group.email) : null,
      group.website ? h("a", { href: group.website, rel: "noopener", target: "_blank" }, group.website.replace(/^https?:\/\//, "")) : null) }) : null,
  news: (b) => b.items?.length ? card({ title: b.title ?? "News", pad: true,
    body: h("ol", { class: "plain-list news-list" }, b.items.map((n) => h("li", { class: "stack-xs" },
      n.date ? h("span", { class: "eyebrow tabular" }, n.date) : null,
      n.url ? h("a", { class: "strong", href: n.url, rel: "noopener", target: "_blank" }, n.title) : h("strong", {}, n.title),
      n.text ? h("p", { class: "muted" }, n.text) : null))) }) : null,
  themes: (b, ctx) => themesBlock(b, ctx),
  openings: (b, ctx) => openingsBlock(b, ctx),
};

const RECORD_PATH = { person: "/people/", grant: "/grants/", publication: "/publications/", study: "/studies/" };

function linkChip(link) {
  const id = link.ref.includes(":") ? link.ref.split(":")[1] : link.ref;
  const label = link.label.length > 70 ? `${link.label.slice(0, 68)}…` : link.label;
  return h("a", { class: "chip", "data-entity": link.kind, href: `${RECORD_PATH[link.kind]}${id}`, title: link.label }, label);
}

function themesBlock(b, { group, page, reload }) {
  const editor = page.can_edit;
  if (!group.themes.length && !editor) return null;
  return card({
    title: b.title ?? "Themes and projects",
    attrs: { "data-entity": "concept" },
    action: editor ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => themeDrawer(group, null, reload) },
      icon("plus", "icon icon-sm"), "Add") : null,
    pad: true,
    body: group.themes.length ? h("ul", { class: "plain-list theme-list" }, group.themes.map((t) => h("li", { class: "stack-sm" },
      h("div", { class: "row-sm" }, h("strong", { class: "grow" }, t.name), pill(t.kind_label, t.kind === "project" ? "info" : ""),
        editor ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => themeDrawer(group, t, reload) }, "Edit") : null),
      t.summary ? h("div", { class: "prose" }, textBody(t.summary)) : null,
      t.links.length ? h("div", { class: "chips" }, t.links.map(linkChip)) : null)))
      : h("p", { class: "subtle" }, "No themes or projects yet. Add one to show what the group works on, with its people, grants, papers, and studies."),
  });
}

function openingsBlock(b, { group, page, reload }) {
  const editor = page.can_edit;
  if (!group.openings.length && !editor) return null;
  return card({
    title: b.title ?? "Openings",
    action: editor ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => openingDrawer(group, null, reload) },
      icon("plus", "icon icon-sm"), "Post") : null,
    pad: true,
    body: group.openings.length ? h("ul", { class: "plain-list opening-list" }, group.openings.map((o) => h("li", { class: "stack-xs" },
      h("div", { class: "row-sm wrap" }, h("strong", { class: "grow" }, o.title), pill(o.kind_label, "accent"),
        h("span", { class: "subtle tabular" }, `closes ${o.closes_on}`),
        editor ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => openingDrawer(group, o, reload) }, "Edit") : null),
      o.description ? h("div", { class: "prose muted" }, textBody(o.description)) : null,
      h("div", { class: "row-sm" },
        o.url ? h("a", { class: "btn btn-sm", href: o.url, rel: "noopener", target: "_blank" }, "How to apply") : null,
        o.contact ? h("a", { href: `mailto:${o.contact}` }, o.contact) : null))))
      : h("p", { class: "subtle" }, "No open positions. Openings you post close by themselves on their closing date."),
  });
}

function drawerForm({ title, subtitle = null, fields, submitLabel, onSubmit, extra = null }) {
  openDrawer({
    title, subtitle, label: title,
    render: (body, footer, close) => {
      const error = h("p", { class: "field-error", role: "alert", hidden: true });
      const submit = h("button", { type: "submit", class: "btn btn-primary" }, submitLabel);
      body.append(h("form", { class: "stack", novalidate: true, onsubmit: async (event) => {
        event.preventDefault();
        submit.disabled = true;
        try {
          await onSubmit();
        } catch (failure) {
          error.textContent = failure.detail || failure.message || "That did not work.";
          error.hidden = false;
          submit.disabled = false;
          return;
        }
        close();
      } }, fields, error, h("div", { class: "row wrap" }, submit, h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel"), extra)));
    },
  });
}

const labelled = (id, label, control) => h("div", { class: "field" }, h("label", { for: id }, label), control);

async function themeDrawer(group, theme, reload) {
  const papers = (await get(`/api/v1/groups/${group.id}/publications`, { limit: 200 })).data;
  const chosen = new Set((theme?.links ?? []).map((l) => l.ref));
  const name = h("input", { class: "input", id: "theme-name", value: theme?.name ?? "", maxlength: 200 });
  const kind = h("select", { class: "input", id: "theme-kind" }, h("option", { value: "theme" }, "Theme"), h("option", { value: "project" }, "Project"));
  kind.value = theme?.kind ?? "theme";
  const summary = h("textarea", { class: "input", id: "theme-summary", rows: 4, maxlength: 4000 }, theme?.summary ?? "");
  const choices = (label, items) => {
    if (!items.length) return null;
    const count = h("span", { class: "subtle" });
    const update = () => { count.textContent = ` (${items.filter((i) => chosen.has(i.ref)).length} of ${items.length})`; };
    update();
    return h("details", { class: "feature-picker", open: items.some((i) => chosen.has(i.ref)) },
      h("summary", {}, label, count),
      h("div", { class: "stack-xs" }, items.map((i) => h("label", { class: "row-sm feature-option" },
        h("input", { type: "checkbox", checked: chosen.has(i.ref), onchange: (e) => {
          if (e.target.checked) chosen.add(i.ref); else chosen.delete(i.ref);
          update();
        } }), h("span", { class: "grow" }, i.label)))));
  };
  const people = [...group.people, ...group.alumni].map((m) => ({ ref: m.person_id, label: m.name }));
  const uniquePeople = [...new Map(people.map((p) => [p.ref, p])).values()];
  const archive = theme ? h("label", { class: "row-sm subtle" },
    h("input", { type: "checkbox", id: "theme-archived", checked: theme.status === "archived" }), "Archived (hidden from the page)") : null;
  drawerForm({
    title: theme ? `Edit ${theme.name}` : "Add a theme or project",
    subtitle: group.name,
    fields: [
      labelled("theme-name", "Name", name),
      labelled("theme-kind", "Kind", kind),
      labelled("theme-summary", "Summary", summary),
      h("div", { class: "stack-sm" }, h("strong", {}, "Linked to"),
        choices("People", uniquePeople),
        choices("Grants", group.grants.map((g) => ({ ref: g.ref, label: g.summary.title ?? g.ref }))),
        choices("Publications", papers.map((p) => ({ ref: `pub:${p.summary.pmid}`, label: `${p.summary.title} (${p.summary.year ?? "?"})` }))),
        choices("Studies", group.studies.map((st) => ({ ref: st.ref, label: st.summary.title ?? st.ref })))),
      archive,
    ],
    submitLabel: theme ? "Save" : "Add",
    onSubmit: async () => {
      const body = { name: name.value.trim(), kind: kind.value, summary: summary.value.trim() || null, links: [...chosen] };
      if (theme) body.status = archive.querySelector("input").checked ? "archived" : "active";
      await post(theme ? `/api/v1/groups/${group.id}/themes/${theme.id}` : `/api/v1/groups/${group.id}/themes`, body);
      toast(theme ? `Saved ${body.name}.` : `Added ${body.name}.`);
      reload();
    },
  });
}

function openingDrawer(group, opening, reload) {
  const inMonths = (n) => { const d = new Date(); d.setMonth(d.getMonth() + n); return d.toISOString().slice(0, 10); };
  const title = h("input", { class: "input", id: "opening-title", value: opening?.title ?? "", maxlength: 200, placeholder: "Postdoctoral fellow in …" });
  const kind = h("select", { class: "input", id: "opening-kind" },
    h("option", { value: "job" }, "Job"), h("option", { value: "training" }, "Training"), h("option", { value: "other" }, "Other opportunity"));
  kind.value = opening?.kind ?? "job";
  const closes = h("input", { class: "input mono", id: "opening-closes", type: "date", value: opening?.closes_on ?? inMonths(2) });
  const description = h("textarea", { class: "input", id: "opening-description", rows: 5, maxlength: 4000 }, opening?.description ?? "");
  const url = h("input", { class: "input", id: "opening-url", type: "url", value: opening?.url ?? "", placeholder: "https://… (how to apply)" });
  const contact = h("input", { class: "input", id: "opening-contact", type: "email", value: opening?.contact ?? group.email ?? "" });
  const closeNow = opening ? h("button", { type: "button", class: "btn btn-reject", onclick: async () => {
    try {
      await post(`/api/v1/groups/${group.id}/openings/${opening.id}`, { closed: true });
    } catch (failure) {
      toast(failure.detail || "That did not work.");
      return;
    }
    document.querySelector(".drawer .btn-ghost")?.click();
    toast(`Closed ${opening.title}.`);
    reload();
  } }, "Close now") : null;
  drawerForm({
    title: opening ? `Edit ${opening.title}` : "Post an opening",
    subtitle: group.name,
    fields: [
      labelled("opening-title", "Title", title),
      h("div", { class: "grid-2" }, labelled("opening-kind", "Kind", kind), labelled("opening-closes", "Closes after", closes)),
      labelled("opening-description", "Description", description),
      labelled("opening-url", "Link (optional)", url),
      labelled("opening-contact", "Contact (optional)", contact),
      h("p", { class: "subtle" }, "It leaves the page by itself after its closing date."),
    ],
    submitLabel: opening ? "Save" : "Post",
    extra: closeNow ? h("span", { class: "spacer" }, closeNow) : null,
    onSubmit: async () => {
      const body = { title: title.value.trim(), kind: kind.value, closes_on: closes.value,
        description: description.value.trim() || null, url: url.value.trim() || null, contact: contact.value.trim() || null };
      await post(opening ? `/api/v1/groups/${group.id}/openings/${opening.id}` : `/api/v1/groups/${group.id}/openings`, body);
      toast(opening ? `Saved ${body.title}.` : `Posted ${body.title}.`);
      reload();
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
