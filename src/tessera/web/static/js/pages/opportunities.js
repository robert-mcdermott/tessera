// Funding opportunities (INT-09, INT-12): one opportunity's page, and Admin → Opportunities for
// those the organization tracks itself (foundation calls, internal funds, limited submissions).
// Loaded on first use.

import { get, invalidate, post, request } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { date, enumLabel, money, number, paragraphs, relative } from "../core/format.js";
import { navigate, query, queryList, setQuery } from "../core/router.js";
import { can } from "../core/session.js";
import { openDrawer } from "../components/drawer.js";
import { eligibilityPill, opportunityWhen } from "../components/items.js";
import { toast } from "../components/toast.js";
import { callout, card, chip, debounce, emptyState, entityIcon, kv, pageHeader, pill } from "../components/ui.js";
import { show } from "./page.js";

const TONE = { posted: "ok", forecasted: "accent", closed: "", archived: "" };
const KINDS = ["foundation", "internal", "federal", "state", "industry", "other"];

function external(href, label) {
  return href ? h("a", { class: "btn btn-sm", href, target: "_blank", rel: "noopener noreferrer" }, label, icon("external", "icon icon-sm")) : null;
}

function range(o) {
  if (o.award_floor && o.award_ceiling) return `${money(o.award_floor)} to ${money(o.award_ceiling)}`;
  if (o.award_ceiling) return `Up to ${money(o.award_ceiling)}`;
  if (o.award_floor) return `From ${money(o.award_floor)}`;
  return null;
}

const RESULT = {
  meets: { icon: "check", tone: "ok", label: "Meets" },
  check: { icon: "warning", tone: "warn", label: "Check" },
  does_not_meet: { icon: "close", tone: "danger", label: "Does not meet" },
  note: { icon: "info", tone: "", label: "Note" },
};

function eligibilityCard(e) {
  return card({ title: "Can the organization apply?", pad: true, attrs: { class: "card eligibility" }, body: h("div", { class: "stack-sm" },
    h("div", { class: "row-sm" }, eligibilityPill(e), h("span", { class: "subtle" }, "Kept apart from who fits.")),
    h("ul", { class: "plain-list eligibility-list" }, e.checks.map((c) => h("li", { "data-result": c.result },
      h("span", { class: `eligibility-mark tone-${RESULT[c.result].tone || "plain"}`, title: RESULT[c.result].label }, icon(RESULT[c.result].icon, "icon icon-sm")),
      h("div", { class: "grow" }, h("strong", {}, c.label), h("span", { class: "sr-only" }, `: ${RESULT[c.result].label}.`), " ", c.detail,
        h("span", { class: "subtle" }, ` From ${c.from}.`)))))) });
}

function conceptReader(f) {
  const drop = queryList("drop");
  const added = queryList("concept");
  const suggestions = h("div", { class: "chips" });
  const input = h("input", { class: "input", type: "search", placeholder: "Add a topic", "aria-label": "Add a topic to the reading" });
  const lookup = debounce(async () => {
    const text = input.value.trim();
    if (text.length < 2) { suggestions.replaceChildren(); return; }
    const found = (await get("/api/v1/concepts", { q: text })).data.slice(0, 6);
    suggestions.replaceChildren(...found.map((c) => h("button", { type: "button", class: "chip", "data-entity": "concept",
      onclick: () => setQuery({ concept: [...added, c.descriptor_ui], drop: drop.filter((d) => d !== c.descriptor_ui) }) }, icon("plus", "icon icon-sm"), c.descriptor)));
  }, 250);
  input.addEventListener("input", lookup);
  return h("div", { class: "stack-sm" },
    h("p", { class: "subtle" }, "Read as these topics, from the words it uses and the organization's papers on the same subject. Remove a topic, or add one, to read it again."),
    f.concepts.length ? h("div", { class: "chips" }, f.concepts.map((c) => h("span", { class: "chip", "data-entity": "concept", title: c.via ? `From “${c.via}”` : null },
      c.descriptor, h("button", { type: "button", class: "chip-remove", "aria-label": `Leave out ${c.descriptor}`,
        onclick: () => setQuery({ drop: [...drop, c.descriptor_ui], concept: added.filter((a) => a !== c.descriptor_ui) }) }, icon("close", "icon icon-sm")))))
      : h("p", { class: "muted" }, "No topic was recognized."),
    h("div", { class: "row-sm wrap" }, input, f.adjusted ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => setQuery({ concept: null, drop: null }) }, "Reset") : null),
    suggestions);
}

function whoFits(f) {
  const people = f.people.length
    ? h("ul", { class: "plain-list fit-list" }, f.people.map((p) => h("li", {},
      h("div", { class: "row-sm wrap" }, h("a", { href: `/people/${p.id}` }, p.name), pill(p.band_label, p.band === "strong" ? "ok" : "")),
      h("span", { class: "subtle" }, p.matches.slice(0, 3).map((m) => `${m.descriptor}${m.papers ? ` (${number(m.papers)} ${m.papers === 1 ? "paper" : "papers"})` : ""}`).join(" · ")))))
    : h("p", { class: "muted" }, "Nobody's confirmed work matches it closely.");
  const groups = f.groups.length
    ? h("ul", { class: "plain-list fit-list" }, f.groups.map((g) => h("li", {},
      h("div", { class: "row-sm wrap" }, g.slug ? h("a", { href: `/groups/${g.slug}` }, g.name) : h("span", {}, g.name), pill(g.band_label, g.band === "strong" ? "ok" : ""),
        g.kind ? h("span", { class: "subtle" }, enumLabel(g.kind)) : null),
      g.members.length ? h("div", { class: "chips" }, g.members.slice(0, 6).map((m) => chip("person", m.name, `/people/${m.id}`))) : null)))
    : h("p", { class: "muted" }, "No group fits it through its members.");
  return h("div", { class: "grid-2 fit-grid" },
    h("section", { class: "stack-sm", "aria-label": "People" }, h("h3", { class: "fit-heading" }, "People"), people),
    h("section", { class: "stack-sm", "aria-label": "Groups" }, h("h3", { class: "fit-heading" }, "Groups"), groups));
}

function interestButtons(o, interest, reload) {
  const set = async (state) => {
    await post(`/api/v1/opportunities/${encodeURIComponent(o.id)}/interest`, { state });
    invalidate();
    reload();
    toast(state === "watching" ? "Watching. It is on your home page." : state === "dismissed" ? "Dismissed from your lists." : "Done.");
  };
  return [
    h("button", { type: "button", class: `btn${interest === "watching" ? " btn-primary" : ""}`, "aria-pressed": interest === "watching" ? "true" : "false",
      onclick: () => set(interest === "watching" ? null : "watching") }, icon("bookmark", "icon icon-sm"), interest === "watching" ? "Watching" : "Watch"),
    h("button", { type: "button", class: "btn btn-ghost", "aria-pressed": interest === "dismissed" ? "true" : "false",
      onclick: () => set(interest === "dismissed" ? null : "dismissed") }, interest === "dismissed" ? "Dismissed" : "Dismiss"),
  ];
}

export function opportunityPage(main, params) {
  const id = params.id;
  const reload = () => opportunityPage(main, params);
  return show(main, {
    title: (o) => o.title,
    load: async () => {
      const [o, f] = await Promise.all([
        get(`/api/v1/opportunities/${encodeURIComponent(id)}`, null, { fresh: true }).then((r) => r.data),
        get(`/api/v1/opportunities/${encodeURIComponent(id)}/fit`, { concept: queryList("concept"), drop: queryList("drop") }, { fresh: true }).then((r) => r.data),
      ]);
      return { ...o, fit: f };
    },
    view: (o) => {
      const local = o.source === "local";
      const f = o.fit;
      const fetched = (o.sources ?? []).find((s) => s.last_success_at);
      return h("div", { class: "stack-lg" },
        pageHeader({
          lead: entityIcon("opportunity"),
          eyebrow: [h("span", {}, `Opportunity · ${o.source_label}`), pill(o.status_label, TONE[o.status] ?? "")],
          title: o.title,
          meta: [o.number ? h("span", { class: "tabular" }, o.number) : null, o.sponsor ? h("span", {}, o.sponsor) : null,
            h("span", {}, opportunityWhen(o))],
          actions: [...interestButtons(o, f.interest, reload), external(o.url, local ? "Website" : "Grants.gov"), external(o.guide_url, "NIH Guide"),
            local && can("curate_all") ? h("button", { type: "button", class: "btn", onclick: () => editOpportunity(o, reload) }, icon("edit", "icon icon-sm"), "Edit") : null],
        }),
        o.forecast && o.status === "forecasted" ? callout("This is a forecast: the sponsor expects to post a notice. Its dates and amounts are estimates.", "info") : null,
        h("div", { class: "grid-main" },
          h("div", { class: "stack-lg" },
            o.description ? card({ title: "About", pad: true, body: h("div", { class: "abstract" }, paragraphs(o.description).map((p) => h("p", {}, p))) }) : null,
            card({ title: "Who fits", pad: true, attrs: { id: "fit" }, body: h("div", { class: "stack" },
              h("p", { class: "subtle" }, "People whose confirmed work matches its topics, and groups that fit through their members. Fit is shown in bands, never as a ranking, and says nothing about eligibility."),
              whoFits(f), conceptReader(f)) }),
            card({ title: "Who may apply", pad: true, body: h("div", { class: "stack-sm" },
              o.applicant_type_labels.length ? h("ul", { class: "plain-list" }, o.applicant_type_labels.map((t) => h("li", {}, t)))
                : h("p", { class: "muted" }, "The sponsor lists no applicant types; see the notice."),
              o.eligibility_notes ? h("p", {}, o.eligibility_notes) : null,
              o.limited_submission ? callout("A limited submission: the organization may send only a few applications, chosen internally first.", "warn") : null) })),
          h("div", { class: "stack-lg" },
            eligibilityCard(f.eligibility),
            card({ title: "Dates and money", pad: true, body: kv([
              ["Status", o.status_label],
              [o.forecast ? "Expected" : "Posted", date(o.forecast ? o.expected_post ?? o.posted : o.posted)],
              [o.forecast ? "Expected to close" : "Closes", o.closes ? date(o.closes) : "No closing date given"],
              ["Internal deadline", o.internal_deadline ? date(o.internal_deadline) : null],
              ["Award", range(o)],
              ["Total funding", o.estimated_funding ? money(o.estimated_funding) : null],
              ["Expected awards", o.expected_awards ? number(o.expected_awards) : null],
              ["Cost sharing", o.cost_sharing == null ? null : o.cost_sharing ? "Required" : "Not required"],
            ].filter(([, v]) => v != null)) }),
            card({ title: "Details", pad: true, body: h("div", { class: "stack-sm" }, kv([
              ["Activity codes", o.activity_codes.join(", ") || null],
              ["Clinical trial", o.clinical_trial ? enumLabel(o.clinical_trial) : null],
              ["Instrument", o.instruments.join(", ") || null],
              ["Category", o.categories.join(", ") || null],
              ["Assistance listings", o.assistance_listings.join(", ") || null],
              ["Kind", enumLabel(o.kind)],
              ["Last updated", o.last_updated ? date(o.last_updated) : null],
            ].filter(([, v]) => v != null)),
            fetched ? h("p", { class: "subtle" }, `From Grants.gov, fetched ${relative(fetched.last_success_at)}.`) : null,
            local ? h("p", { class: "subtle" }, `Tracked here; last changed by ${o.updated_by} ${relative(o.updated_at)}.`) : null) }))));
    },
  });
}

// --- the organization's own ---------------------------------------------------------------------

function field(id, label, node, hint = null) {
  node.id = id;
  return h("div", { class: "field" }, h("label", { for: id }, label), node, hint ? h("p", { class: "subtle" }, hint) : null);
}

function opportunityForm(o, { title, submitLabel, onSubmit, extra = null }) {
  const v = o ?? {};
  const inputs = {
    title: h("input", { class: "input", value: v.title ?? "", required: true }),
    source: h("input", { class: "input", value: local(v) ?? "", required: true, placeholder: "A foundation, or this organization's office" }),
    kind: h("select", { class: "input" }, KINDS.map((k) => h("option", { value: k, selected: (v.kind ?? "foundation") === k ? true : null }, enumLabel(k)))),
    sponsor: h("input", { class: "input", value: v.source === "local" && v.sponsor !== v.source_label ? v.sponsor ?? "" : "" }),
    number: h("input", { class: "input", value: v.number ?? "" }),
    url: h("input", { class: "input", type: "url", value: v.url ?? "" }),
    posted_on: h("input", { class: "input", type: "date", value: v.posted ?? "" }),
    closes_on: h("input", { class: "input", type: "date", value: v.closes ?? "" }),
    internal_deadline: h("input", { class: "input", type: "date", value: v.internal_deadline ?? "" }),
    award_ceiling: h("input", { class: "input", inputmode: "numeric", value: v.award_ceiling ?? "" }),
    description: h("textarea", { class: "input", rows: 5 }, v.description ?? ""),
    eligibility_notes: h("textarea", { class: "input", rows: 3 }, v.eligibility_notes ?? ""),
  };
  const limited = h("input", { type: "checkbox", id: "opp-limited", checked: v.limited_submission ? true : null });
  const value = () => ({
    ...Object.fromEntries(Object.entries(inputs).map(([k, node]) => [k, node.value.trim()])),
    limited_submission: limited.checked,
  });
  const fields = [
    field("opp-title", "Title", inputs.title), field("opp-source", "Source", inputs.source, "Who offers or lists it."),
    field("opp-kind", "Kind", inputs.kind), field("opp-sponsor", "Sponsor (if not the source)", inputs.sponsor),
    field("opp-number", "Number", inputs.number), field("opp-url", "Web page", inputs.url),
    h("div", { class: "grid-2" }, field("opp-posted", "Posted", inputs.posted_on), field("opp-closes", "Closes", inputs.closes_on)),
    h("div", { class: "grid-2" }, field("opp-internal", "Internal deadline", inputs.internal_deadline), field("opp-ceiling", "Largest award (dollars)", inputs.award_ceiling)),
    h("label", { class: "row-sm", for: "opp-limited" }, limited, "A limited submission (the organization nominates)"),
    field("opp-description", "About", inputs.description), field("opp-eligibility", "Who may apply", inputs.eligibility_notes),
  ];
  openDrawer({
    title, label: title,
    render: (body, footer, close) => {
      const error = h("p", { class: "field-error", role: "alert", hidden: true });
      const submit = h("button", { type: "submit", class: "btn btn-primary" }, submitLabel);
      body.append(h("form", { class: "stack", novalidate: true, onsubmit: async (event) => {
        event.preventDefault();
        submit.disabled = true;
        try {
          await onSubmit(value());
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

function local(o) {
  return o?.source === "local" ? o.source_label : null;
}

function editOpportunity(o, reload) {
  const archived = o.status === "archived";
  opportunityForm(o, {
    title: "Edit the opportunity", submitLabel: "Save",
    onSubmit: async (values) => {
      await post(`/api/v1/opportunities/${encodeURIComponent(o.id)}`, values);
      invalidate();
      reload();
      toast("Saved.");
    },
    extra: h("button", { type: "button", class: "btn btn-ghost", onclick: async () => {
      if (archived) await post(`/api/v1/opportunities/${encodeURIComponent(o.id)}`, { archived: false });
      else await request("DELETE", `/api/v1/opportunities/${encodeURIComponent(o.id)}`);
      invalidate();
      reload();
      toast(archived ? "Restored." : "Archived. It is kept, and can be restored.");
    } }, archived ? "Restore" : "Archive"),
  });
}

function importDrawer(reload) {
  const text = h("textarea", { class: "input", rows: 10, placeholder: "source,title,closes_on,url\nExample Foundation,Seed grants,2027-03-01,https://…" });
  const source = h("input", { class: "input", placeholder: "Optional: the source of every row" });
  openDrawer({
    title: "Import opportunities", label: "Import opportunities",
    render: (body, footer, close) => {
      const result = h("div", { role: "status" });
      const run = async (dryRun) => {
        try {
          const out = (await post("/api/v1/opportunities/import", { csv: text.value, source: source.value.trim() || null, dry_run: dryRun })).data;
          if (out.problems.length) {
            result.replaceChildren(callout(`Nothing ${dryRun ? "would be" : "was"} imported: ${out.problems.slice(0, 5).join("; ")}`, "warn"));
            return;
          }
          result.replaceChildren(callout(`${dryRun ? "Would import" : "Imported"}: ${out.created} new, ${out.updated} updated, ${out.unchanged} unchanged.`, "info"));
          if (!dryRun) { invalidate(); reload(); }
        } catch (failure) {
          result.replaceChildren(callout(failure.detail || failure.message, "warn"));
        }
      };
      body.append(h("div", { class: "stack" },
        h("p", { class: "muted" }, "Paste CSV with a header row. Columns: source, external_id, kind, number, title, sponsor, description, url, posted_on, closes_on, internal_deadline, award_floor, award_ceiling, limited_submission, eligibility_notes, applicant_types. Importing the same rows again updates them."),
        field("opp-import-source", "Source for every row", source), field("opp-import-text", "CSV", text), result,
        h("div", { class: "row wrap" }, h("button", { type: "button", class: "btn", onclick: () => run(true) }, "Check"),
          h("button", { type: "button", class: "btn btn-primary", onclick: () => run(false) }, "Import"),
          h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Close"))));
    },
  });
}

export function opportunitiesAdmin(main) {
  const reload = () => opportunitiesAdmin(main);
  return show(main, {
    title: "Opportunities",
    load: () => get("/api/v1/opportunities", { source: "local", limit: 200, sort: "closes" }, { fresh: true }),
    view: ({ data, meta }) => h("div", { class: "stack-lg" },
      pageHeader({ eyebrow: "Admin", title: "Opportunities",
        meta: [h("span", {}, "Opportunities the organization tracks itself. Federal ones come from Grants.gov by themselves.")],
        actions: [h("button", { type: "button", class: "btn", onclick: () => importDrawer(reload) }, icon("upload", "icon icon-sm"), "Import"),
          h("button", { type: "button", class: "btn btn-primary", onclick: () => opportunityForm(null, { title: "Add an opportunity", submitLabel: "Add",
            onSubmit: async (values) => {
              const made = (await post("/api/v1/opportunities", values)).data;
              invalidate();
              navigate(`/opportunities/${encodeURIComponent(made.id.replace(/^opp:/, ""))}`);
            } }) }, icon("plus", "icon icon-sm"), "Add an opportunity")] }),
      data.length ? card({ body: h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["Title", "Source", "Kind", "Closes", "Internal deadline", "Status"].map((label) => h("th", { scope: "col" }, label)))),
        h("tbody", {}, data.map((o) => h("tr", {},
          h("td", {}, h("a", { href: `/opportunities/${encodeURIComponent(o.id.replace(/^opp:/, ""))}` }, o.title)),
          h("td", {}, o.source_label), h("td", {}, enumLabel(o.kind)),
          h("td", { class: "tabular" }, o.closes ? date(o.closes) : "–"),
          h("td", { class: "tabular" }, o.internal_deadline ? date(o.internal_deadline) : "–"),
          h("td", {}, pill(o.status === "posted" ? "Open" : enumLabel(o.status), TONE[o.status] ?? "")))))),
        meta.total > data.length ? h("p", { class: "subtle" }, `Showing ${number(data.length)} of ${number(meta.total)}.`) : null) })
        : emptyState({ kind: "opportunity", title: "None tracked yet", text: "Add foundation calls, internal pilot funds, and limited submissions, or import them from a spreadsheet." })),
  });
}
