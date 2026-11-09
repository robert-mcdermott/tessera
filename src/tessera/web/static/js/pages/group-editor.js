// The group page editor (/groups/<address>/edit), for the group's managers and curators:
// choose and arrange blocks from the approved set, set each one up, save a draft, preview it,
// publish, and restore any earlier version. Nothing here is code or markup: every block is
// plain settings, checked by the server. Loaded on first use.

import { get, invalidate, post, request } from "../core/api.js";
import { h, icon, iconButton } from "../core/dom.js";
import { relative } from "../core/format.js";
import { navigate } from "../core/router.js";
import { confirmAction } from "../components/drawer.js";
import { toast } from "../components/toast.js";
import { callout, card, emptyState, pageHeader, pill, segmented } from "../components/ui.js";
import { show } from "./page.js";
import { publicToggle } from "./person.js";

const HELP = {
  about: "A few paragraphs about the group. Leave a blank line between paragraphs.",
  people: "Leaders, then current members; alumni on their own tab.",
  publications: "Papers attributed to the group, newest first, with a chart by year. Choose papers to feature above them.",
  grants: "Grants held by current members.",
  studies: "Clinical studies led by current members.",
  topics: "What current members work on, from their papers' MeSH headings.",
  connections: "The labs and programs the group writes most with.",
  themes: "The group's research themes and projects.",
  openings: "Open positions and training opportunities; each disappears on its closing date.",
  contact: "The group's contact address and website, with an optional note.",
  news: "Short dated items, newest first.",
};

export function groupEditor(main, params) {
  const ref = params.slug;
  return show(main, {
    title: (d) => `Edit ${d.group.name}`,
    load: async () => {
      const base = `/api/v1/groups/${encodeURIComponent(ref)}`;
      const [group, page, revisions, papers] = await Promise.all([
        get(base, null, { fresh: true }),
        get(`${base}/page`, { version: "draft" }, { fresh: true }),
        get(`${base}/page/revisions`, null, { fresh: true }),
        get(`${base}/publications`, { limit: 200 }, { fresh: true }),
      ]);
      return { group: group.data, page: page.data, revisions: revisions.data, papers: papers.data };
    },
    view: (data) => editorView(main, params, data),
  });
}

function editorView(main, params, { group, page, revisions, papers }) {
  const types = Object.fromEntries(page.block_types.map((t) => [t.id, t]));
  let blocks = structuredClone(page.blocks);
  let dirty = false;
  const list = h("ol", { class: "plain-list block-list", "aria-label": "Blocks, in order" });
  // What moving or removing a block did, for screen readers (the list itself is redrawn).
  const said = h("p", { class: "sr-only", "aria-live": "polite" });
  const kindOf = (block) => types[block.type]?.label ?? block.type;
  const status = h("span", { class: "subtle" });
  const saveButton = h("button", { type: "button", class: "btn", onclick: () => save() }, "Save draft");
  const markDirty = () => {
    dirty = true;
    status.textContent = "Unsaved changes";
  };
  const describeStatus = () => {
    const parts = [];
    parts.push(page.revision ? `Published version ${page.revision}${page.published_by ? ` by ${page.published_by}` : ""}, ${relative(page.published_at)}` : "Not published yet: the group's page shows the standard arrangement");
    if (page.has_draft) parts.push(`draft saved ${page.draft_updated_by ? `by ${page.draft_updated_by} ` : ""}${relative(page.draft_updated_at)}`);
    status.textContent = parts.join(" · ");
  };
  describeStatus();

  const save = async ({ quiet = false } = {}) => {
    try {
      const saved = await post(`/api/v1/groups/${group.id}/page/draft`, { blocks });
      page = saved.data;
      blocks = structuredClone(page.blocks);
      dirty = false;
      describeStatus();
      draw();
      if (!quiet) toast("Draft saved.");
      return true;
    } catch (failure) {
      toast(failure.detail || "The draft could not be saved.");
      return false;
    }
  };

  const move = (i, by) => {
    const j = i + by;
    if (j < 0 || j >= blocks.length) return;
    [blocks[i], blocks[j]] = [blocks[j], blocks[i]];
    markDirty();
    draw();
    list.children[j]?.querySelector(`button[data-move="${by}"]:not([disabled])`)?.focus()
      ?? list.children[j]?.querySelector(`button[data-move="${-by}"]`)?.focus();
    said.textContent = `Moved ${kindOf(blocks[j])} to position ${j + 1} of ${blocks.length}.`;
  };
  const remove = (i) => {
    const kind = kindOf(blocks[i]);
    blocks.splice(i, 1);
    markDirty();
    draw();
    // Focus stays in the list: on the block that took this one's place, or the one before it.
    const next = list.children[Math.min(i, blocks.length - 1)];
    (next?.querySelector("button[data-remove]") ?? list.closest("section, .card, main")?.querySelector("h2, h1"))?.focus();
    said.textContent = `Removed the ${kind} block.`;
  };

  const field = (label, control) => h("label", { class: "field" }, h("span", {}, label), control);
  const settings = (block) => {
    const set = (key) => (event) => {
      const value = event.target.type === "checkbox" ? event.target.checked : event.target.value;
      if (value === "" || value == null) delete block[key]; else block[key] = event.target.type === "number" ? Number(value) : value;
      markDirty();
    };
    const title = field("Title (optional)", h("input", { class: "input", value: block.title ?? "", placeholder: types[block.type].label, maxlength: 120, oninput: set("title") }));
    const out = [title];
    if (block.type === "about" || block.type === "contact") {
      out.push(field(block.type === "about" ? "Text" : "Note (optional)", h("textarea", { class: "input", rows: block.type === "about" ? 6 : 3,
        maxlength: block.type === "about" ? 6000 : 1000, oninput: set("text") }, block.text ?? "")));
    }
    if (block.type === "people") {
      out.push(h("label", { class: "row-sm" }, h("input", { type: "checkbox", checked: block.show_alumni !== false,
        onchange: (e) => { block.show_alumni = e.target.checked; markDirty(); } }), "Show alumni"));
    }
    if (block.type === "publications") {
      out.push(h("label", { class: "row-sm" }, h("input", { type: "checkbox", checked: block.show_chart !== false,
        onchange: (e) => { block.show_chart = e.target.checked; markDirty(); } }), "Show the chart by year"));
      const chosen = new Set(block.featured ?? []);
      const options = papers.slice(0, 200).map((item) => {
        const pmid = item.summary.pmid;
        return h("label", { class: "row-sm feature-option" },
          h("input", { type: "checkbox", checked: chosen.has(pmid), onchange: (e) => {
            if (e.target.checked) chosen.add(pmid); else chosen.delete(pmid);
            if (chosen.size > 20) {
              e.target.checked = false;
              chosen.delete(pmid);
              toast("Feature at most 20 papers.");
              return;
            }
            block.featured = [...chosen];
            if (!block.featured.length) delete block.featured;
            markDirty();
          } }),
          h("span", { class: "grow" }, item.summary.title, h("span", { class: "subtle" }, ` · ${item.summary.year ?? ""}`)));
      });
      out.push(h("details", { class: "feature-picker" }, h("summary", {}, `Featured papers (${chosen.size})`),
        options.length ? h("div", { class: "stack-xs" }, options) : h("p", { class: "subtle" }, "No papers are attributed to the group yet.")));
    }
    if (["publications", "grants", "studies"].includes(block.type)) {
      out.push(field("How many to show", h("input", { class: "input input-narrow", type: "number", min: 1, max: 50, value: block.limit ?? "",
        placeholder: block.type === "publications" ? "20" : "12", oninput: set("limit") })));
    }
    if (block.type === "news") out.push(newsEditor(block, markDirty));
    return out;
  };

  function draw() {
    list.replaceChildren(...blocks.map((block, i) => h("li", { class: "card card-pad block-editor" },
      h("div", { class: "row" },
        h("span", { class: "block-index tabular" }, String(i + 1).padStart(2, "0")),
        h("div", { class: "stack-xs grow" }, h("strong", {}, types[block.type]?.label ?? block.type),
          h("span", { class: "subtle" }, HELP[block.type] ?? "")),
        types[block.type]?.side ? pill("Side column") : null,
        iconButton("chevronUp", `Move ${kindOf(block)} up`, { "data-move": "-1", disabled: i === 0, onclick: () => move(i, -1) }),
        iconButton("chevronDown", `Move ${kindOf(block)} down`, { "data-move": "1", disabled: i === blocks.length - 1, onclick: () => move(i, 1) }),
        iconButton("close", `Remove the ${kindOf(block)} block`, { "data-remove": "", onclick: () => remove(i) })),
      h("div", { class: "stack-sm block-settings" }, settings(block)))));
    if (!blocks.length) list.append(h("li", {}, emptyState({ iconName: "grid", title: "No blocks", text: "Add a block to start the page." })));
    adder.replaceChildren(h("option", { value: "" }, "Add a block…"),
      ...page.block_types.filter((t) => t.repeatable || !blocks.some((b) => b.type === t.id))
        .map((t) => h("option", { value: t.id }, t.label)));
  }
  const adder = h("select", { class: "input", "aria-label": "Add a block", onchange: (e) => {
    if (!e.target.value) return;
    blocks.push({ type: e.target.value, ...(e.target.value === "news" ? { items: [] } : {}) });
    markDirty();
    draw();
    list.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "center" });
  } });
  draw();

  const preview = h("button", { type: "button", class: "btn", onclick: async () => {
    if ((dirty || !page.has_draft) && !(await save({ quiet: true }))) return;
    navigate(`/groups/${group.slug}?preview=draft`);
  } }, icon("external", "icon icon-sm"), "Preview");
  const publish = h("button", { type: "button", class: "btn btn-primary", onclick: () => {
    const note = h("input", { class: "input", maxlength: 500, placeholder: "What changed (optional)" });
    confirmAction({
      title: "Publish this page?",
      text: h("div", { class: "stack-sm" }, h("p", {}, "Everyone who opens the group's page will see this version. The current one stays in the history, and you can restore it."), note),
      confirmLabel: "Publish",
      run: async () => {
        if ((dirty || !page.has_draft) && !(await save({ quiet: true }))) return;
        try {
          await post(`/api/v1/groups/${group.id}/page/publish`, { note: note.value.trim() || null });
        } catch (failure) {
          toast(failure.detail || "The page could not be published.");
          return;
        }
        toast("Published.");
        navigate(`/groups/${group.slug}`);
      },
    });
  } }, "Publish");
  const discard = h("button", { type: "button", class: "btn btn-ghost", onclick: () => confirmAction({
    title: "Discard the draft?",
    text: h("p", {}, "The page goes back to the published version; your unpublished changes are lost."),
    confirmLabel: "Discard", tone: "reject",
    run: async () => {
      try {
        await request("DELETE", `/api/v1/groups/${group.id}/page/draft`);
        invalidate();
      } catch (failure) {
        toast(failure.detail || "That did not work.");
        return;
      }
      toast("Draft discarded.");
      groupEditor(main, params);
    },
  }) }, "Discard draft");

  const history = card({ title: "Published versions", pad: true, body: revisions.length
    ? h("ol", { class: "history" }, revisions.map((r) => h("li", { class: "history-item" },
      icon("history", "icon icon-sm"),
      h("div", { class: "stack-xs grow" },
        h("span", {}, `Version ${r.number}`, r.action === "rollback" ? " (restored)" : "", r.number === page.revision ? h("span", {}, " ", pill("Live", "ok")) : null),
        h("span", { class: "subtle" }, `${r.actor}, ${relative(r.at)}${r.note ? ` · ${r.note}` : ""}`),
        h("span", { class: "row-sm" },
          h("a", { href: `/groups/${group.slug}?version=${r.number}` }, "View"),
          r.number !== page.revision ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => confirmAction({
            title: `Restore version ${r.number}?`,
            text: h("p", {}, "It is published again as a new version; nothing is lost. Your draft stays as it is."),
            confirmLabel: "Restore",
            run: async () => {
              try {
                await post(`/api/v1/groups/${group.id}/page/rollback`, { revision: r.number });
              } catch (failure) {
                toast(failure.detail || "That did not work.");
                return;
              }
              toast(`Restored version ${r.number}.`);
              groupEditor(main, params);
            },
          }) }, "Restore") : null)))))
    : h("p", { class: "subtle" }, "Nothing published yet.") });

  return h("div", { class: "stack-lg" },
    pageHeader({
      eyebrow: h("a", { href: `/groups/${group.slug}` }, group.name),
      title: "Edit the page",
      meta: status,
      actions: [publicToggle(`/api/v1/groups/${group.id}/public`, group.public, "page", null), saveButton, preview, publish],
    }),
    callout("Choose the blocks the page shows and their order. Blocks marked Side column appear beside the others on wide screens. Changes stay in the draft until you publish."),
    h("div", { class: "grid-main" },
      h("div", { class: "stack" }, list, said, h("div", { class: "row wrap" }, adder, page.has_draft ? discard : null)),
      h("div", { class: "stack-lg" }, history, embedCard(group))));
}

const EMBED_PARTS = [["publications", "Publications"], ["people", "People"], ["openings", "Openings"], ["news", "News"]];
const EMBED_THEMES = [{ id: "", label: "Reader's choice" }, { id: "-light", label: "Light" }, { id: "-dark", label: "Dark" }];

/** The code to put the group's public content on another website (LAB-09). */
function embedCard(group) {
  const body = h("div", { class: "stack" });
  const panel = card({ title: "Embed on another website", pad: true, body });
  if (!group.public) {
    body.append(h("p", { class: "muted" }, "Turn on Public page and publish the public site (tessera publish) to embed this group's publications, people, openings, or news in another website."));
    return panel;
  }
  get("/api/v1/meta").then(({ data: meta }) => {
    const site = meta.public_site;
    if (!site) {
      body.append(h("p", { class: "muted" }, "Set the public site's address ([publish] base_url) to get the code to embed."));
      return;
    }
    let part = "publications";
    let theme = "-light";
    const code = h("textarea", { class: "input mono embed-code", rows: 4, readonly: true, "aria-label": "Code to embed" });
    const open = h("a", { target: "_blank", rel: "noopener" }, "Open it");
    const draw = () => {
      const label = EMBED_PARTS.find(([key]) => key === part)[1];
      open.href = `${site}/groups/${group.slug}/embed/${part}${theme}.html`;
      code.value = `<iframe src="${site}/groups/${group.slug}/embed/${part}${theme}.html" title="${label} of ${group.name.replaceAll('"', "&quot;")}" `
        + 'width="100%" height="600" style="border:0" loading="lazy"></iframe>';
    };
    const partSelect = h("select", { class: "input input-sm", "aria-label": "What to embed", onchange: (e) => { part = e.target.value; draw(); } },
      EMBED_PARTS.map(([key, label]) => h("option", { value: key }, label)));
    const themeChoice = segmented(EMBED_THEMES, theme, (id) => {
      theme = id;
      themeChoice.querySelectorAll("button").forEach((b, i) => b.setAttribute("aria-pressed", String(EMBED_THEMES[i].id === id)));
      draw();
    }, "Light or dark");
    draw();
    body.append(
      h("p", { class: "muted" }, "Paste this into the other website's page. It shows what the public site shows and changes when the site is published again."),
      h("div", { class: "row-sm wrap" }, partSelect, themeChoice),
      code,
      h("div", { class: "row-sm" }, h("button", { type: "button", class: "btn btn-sm", onclick: async () => {
        try {
          await navigator.clipboard.writeText(code.value);
          toast("Copied.");
        } catch {
          code.select();
        }
      } }, "Copy"), open),
      h("p", { class: "muted" }, "For sites that show content themselves: ",
        h("a", { href: `${site}/groups/${group.slug}/feed.json`, target: "_blank", rel: "noopener" }, "the group's JSON feed"),
        " of news, openings, and publications."));
  }).catch(() => {});
  return panel;
}

function newsEditor(block, markDirty) {
  const box = h("div", { class: "stack-sm" });
  const draw = () => {
    box.replaceChildren(
      ...block.items.map((item, i) => h("div", { class: "news-item-editor stack-xs" },
        h("div", { class: "row-sm" },
          h("input", { class: "input input-narrow mono", type: "date", value: item.date ?? "", "aria-label": "Date",
            oninput: (e) => { if (e.target.value) item.date = e.target.value; else delete item.date; markDirty(); } }),
          h("input", { class: "input grow", value: item.title ?? "", placeholder: "Title", maxlength: 200, "aria-label": "Title",
            oninput: (e) => { item.title = e.target.value; markDirty(); } }),
          iconButton("close", "Remove this item", { onclick: () => { block.items.splice(i, 1); markDirty(); draw(); } })),
        h("textarea", { class: "input", rows: 2, placeholder: "A sentence or two (optional)", maxlength: 2000, "aria-label": "Text",
          oninput: (e) => { if (e.target.value) item.text = e.target.value; else delete item.text; markDirty(); } }, item.text ?? ""),
        h("input", { class: "input", type: "url", value: item.url ?? "", placeholder: "https://… (optional)", "aria-label": "Link",
          oninput: (e) => { if (e.target.value) item.url = e.target.value; else delete item.url; markDirty(); } }))),
      block.items.length < 20 ? h("button", { type: "button", class: "btn btn-sm", onclick: () => {
        block.items.unshift({ date: new Date().toISOString().slice(0, 10), title: "" });
        markDirty();
        draw();
      } }, icon("plus", "icon icon-sm"), "Add an item") : null);
  };
  draw();
  return box;
}
