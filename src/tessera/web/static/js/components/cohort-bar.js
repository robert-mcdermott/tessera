// The cohort and period bar at the top of every Insight page. Its state lives in the address
// (UX-02): ?cohort=…&start=…&end=…&basis=…, so a view can be bookmarked and shared.

import { get, post, request } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { plural } from "../core/format.js";
import { query, setQuery } from "../core/router.js";
import { can } from "../core/session.js";
import { closeDrawer, openDrawer } from "./drawer.js";
import { toast } from "./toast.js";
import { callout, segmented } from "./ui.js";

/** The figure parameters the address holds (cohort, years, and options), for figureCard. */
export function insightParams() {
  const out = {};
  for (const [key, value] of query()) if (value) out[key] = value;
  return out;
}

/** Saving and deleting cohorts is for managers and anyone who reviews (the API's MANAGE). */
const mayShare = () => can("manage") || can("review") || can("admin");

function groupSpec(slug, below, current) {
  return `group:${slug}${below ? "+below" : ""}${current ? ";current" : ""}`;
}

function parseGroup(spec) {
  if (!spec?.startsWith("group:")) return null;
  let value = spec.slice(6);
  const current = value.endsWith(";current");
  value = value.replace(/;current$/, "");
  const below = value.endsWith("+below");
  return { slug: value.replace(/\+below$/, ""), below, current };
}

function chooseCohort(currentSpec, cohort) {
  openDrawer({
    title: "Choose a cohort",
    subtitle: "Whose work the figures count",
    render: async (body, footer) => {
      const group = parseGroup(currentSpec);
      let below = group?.below ?? false;
      let current = group?.current ?? false;
      const apply = (spec) => {
        closeDrawer();
        setQuery({ cohort: spec === "organization" ? null : spec }, { replace: false });
      };
      const filter = h("input", { class: "input", type: "search", placeholder: "Filter groups", "aria-label": "Filter groups" });
      const list = h("ul", { class: "choice-list" }, h("li", { class: "muted" }, "Loading groups…"));
      const belowBox = h("input", { type: "checkbox", id: "cohort-below", checked: below || null,
        onchange: (e) => { below = e.target.checked; } });
      const windowChoice = segmented([
        { id: "membership", label: "While members" },
        { id: "current", label: "Current members, all their work" },
      ], current ? "current" : "membership", (id) => {
        current = id === "current";
        windowChoice.querySelectorAll("button").forEach((b, i) => b.setAttribute("aria-pressed", String((i === 1) === current)));
      }, "Which work counts");
      const saved = h("ul", { class: "choice-list" });
      body.append(
        h("section", { class: "stack-sm" },
          h("button", { type: "button", class: "choice", "aria-pressed": String(!currentSpec || currentSpec === "organization"), onclick: () => apply("organization") },
            icon("home", "icon icon-sm"), h("span", { class: "grow" }, h("strong", {}, "The organization"), h("span", { class: "muted" }, "Everyone in the directory, current and former")))),
        h("section", { class: "stack-sm" },
          h("h3", {}, "A group"),
          filter,
          h("label", { class: "check", for: "cohort-below" }, belowBox, "Include the groups beneath it"),
          h("div", { class: "stack-sm" }, h("span", { class: "muted" }, "Count"), windowChoice),
          list),
        h("section", { class: "stack-sm" }, h("h3", {}, "Saved cohorts"), saved));
      const draw = (groups) => {
        const words = filter.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
        const shown = groups.filter((g) => words.every((w) => `${g.name} ${g.code ?? ""} ${g.kind_label}`.toLowerCase().includes(w)));
        list.replaceChildren(...shown.slice(0, 80).map((g) => h("li", {}, h("button", { type: "button", class: "choice",
          "aria-pressed": String(group?.slug === g.slug),
          onclick: () => apply(groupSpec(g.slug, below, current)) },
        h("span", { class: "grow" }, h("strong", {}, g.name), h("span", { class: "muted" }, g.kind_label))))),
        shown.length ? null : h("li", { class: "muted" }, "No group matches."));
      };
      try {
        const [groups, cohorts] = await Promise.all([get("/api/v1/groups", { limit: 200 }), get("/api/v1/insight/cohorts")]);
        draw(groups.data);
        filter.addEventListener("input", () => draw(groups.data));
        saved.replaceChildren(...cohorts.data.map((c) => h("li", {}, h("div", { class: "choice-row" },
          h("button", { type: "button", class: "choice", "aria-pressed": String(currentSpec === c.id), onclick: () => apply(c.id) },
            h("span", { class: "grow" }, h("strong", {}, c.name), h("span", { class: "muted" }, c.description || `Saved by ${c.created_by}`))),
          mayShare() ? h("button", { type: "button", class: "icon-button", "aria-label": `Delete ${c.name}`, title: "Delete", onclick: async () => {
            try {
              await request("DELETE", `/api/v1/insight/cohorts/${c.id}`);
              toast(`Deleted ${c.name}.`);
              closeDrawer();
              if (currentSpec === c.id) setQuery({ cohort: null });
            } catch (error) {
              toast(error.detail || error.message, "error");
            }
          } }, icon("trash", "icon icon-sm")) : null))));
        if (!cohorts.data.length) saved.append(h("li", { class: "muted" }, "None yet."));
      } catch (error) {
        list.replaceChildren(callout(error.detail || error.message, "warn"));
      }
      if (mayShare() && cohort && !cohort.saved && cohort.kind !== "organization") {
        const name = h("input", { class: "input", required: true, maxlength: 200, value: cohort.label, "aria-label": "Name for this cohort" });
        footer.hidden = false;
        footer.append(h("form", { class: "row-sm grow", onsubmit: async (event) => {
          event.preventDefault();
          try {
            const saved = (await post("/api/v1/insight/cohorts", { name: name.value, cohort: cohort.spec })).data;
            toast(`Saved ${saved.name}.`);
            apply(saved.id);
          } catch (error) {
            toast(error.detail || error.message, "error");
          }
        } }, name, h("button", { class: "btn", type: "submit" }, icon("plus", "icon icon-sm"), "Save this cohort")));
      }
    },
  });
}

function yearSelect(label, value, first, last, onChange, prefix = "") {
  const options = [];
  for (let y = last; y >= first; y -= 1) options.push(h("option", { value: y, selected: y === value || null }, `${prefix}${y}`));
  return h("label", { class: "field-inline" }, h("span", { class: "muted" }, label),
    h("select", { class: "input input-sm", onchange: (e) => onChange(Number(e.target.value)) }, options));
}

/**
 * The bar. figure: a figure already loaded (for its cohort and period), or null to load
 * just the cohort. withBasis: show the papers-and-studies choice.
 */
export function cohortBar({ period = null, years = null, withBasis = true, fiscal = false, options = [] } = {}) {
  const params = insightParams();
  const chip = h("button", { type: "button", class: "btn cohort-chip", "aria-haspopup": "dialog" },
    icon("people", "icon icon-sm"), h("span", { class: "cohort-label" }, "Loading…"), icon("chevronDown", "icon icon-sm"));
  const bar = h("div", { class: "cohort-bar", role: "group", "aria-label": "Cohort and period" },
    h("div", { class: "cohort-pick" }, h("span", { class: "eyebrow" }, "Cohort"), chip));
  let cohort = null;
  chip.addEventListener("click", () => chooseCohort(params.cohort, cohort));
  get("/api/v1/insight/cohort", { cohort: params.cohort, limit: 0 }).then(({ data }) => {
    cohort = data;
    chip.querySelector(".cohort-label").replaceChildren(h("strong", {}, data.label), h("span", { class: "muted" }, ` · ${plural(data.size, "person", "people")}`));
    chip.title = data.note;
  }).catch((error) => {
    chip.querySelector(".cohort-label").textContent = error.detail || "Unknown cohort";
  });
  if (years) {
    const span = fiscal ? years.fiscal : years.calendar;
    const start = Number(params.start) || period?.start || span.default.start;
    const end = Number(params.end) || period?.end || span.default.end;
    bar.append(h("div", { class: "cohort-period" }, h("span", { class: "eyebrow" }, fiscal ? "Fiscal years" : "Years"),
      h("div", { class: "row-sm" },
        yearSelect("From", start, span.first, span.last, (v) => setQuery({ start: v, end: Math.max(v, end) }), fiscal ? "FY" : ""),
        yearSelect("to", end, span.first, span.last, (v) => setQuery({ end: v, start: Math.min(v, start) }), fiscal ? "FY" : ""))));
  }
  for (const option of options) {
    bar.append(h("div", { class: "cohort-option" }, h("span", { class: "eyebrow" }, option.label),
      segmented(option.choices.map((c) => ({ id: c.value, label: c.label })), params[option.key] || option.default,
        (id) => setQuery({ [option.key]: id === option.default ? null : id }), option.label)));
  }
  if (withBasis) {
    bar.append(h("div", { class: "cohort-basis" }, h("span", { class: "eyebrow" }, "Papers and studies"),
      segmented([{ id: "likely", label: "Confirmed and likely" }, { id: "confirmed", label: "Confirmed only" }],
        params.basis || "likely", (id) => setQuery({ basis: id === "likely" ? null : id }), "Which links count")));
  }
  return bar;
}
