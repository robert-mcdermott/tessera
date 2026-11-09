// A CV: the sections it includes, their order, and their entries, beside a live preview that
// prints as the CV itself. Built from the profile and confirmed records each time it is shown.

import { get, invalidate, post, request } from "../core/api.js";
import { h, icon, iconButton } from "../core/dom.js";
import { relative } from "../core/format.js";
import { navigate } from "../core/router.js";
import { confirmAction, openDrawer } from "../components/drawer.js";
import { toast } from "../components/toast.js";
import { card, pageHeader } from "../components/ui.js";
import { show } from "./page.js";

const base = (personId) => `/api/v1/people/${encodeURIComponent(personId)}/cvs`;

/** Ask for a name, then make a CV (or a copy of one) and open it. */
export function newDocument(personId, { copyOf = null, suggested = "", kind = "cv" } = {}) {
  const noun = kind === "nih_biosketch" ? "NIH biosketch" : "CV";
  openDrawer({
    title: copyOf ? "Copy this one" : `New ${noun}`,
    subtitle: kind === "nih_biosketch" ? "A name for this version, such as the application it is for." : "A name for this version, such as “Promotion dossier” or “Visiting scholar”.",
    render: (body, footer, close) => {
      const problem = h("p", { class: "field-error", role: "alert", hidden: true });
      const name = h("input", { class: "input", id: "cv-name", maxlength: 200, value: suggested, required: true });
      const form = h("form", { id: "cv-new", class: "stack", onsubmit: async (event) => {
        event.preventDefault();
        try {
          const made = (await post(base(personId), { name: name.value, kind, copy_of: copyOf })).data;
          close();
          navigate(`/people/${encodeURIComponent(personId)}/cvs/${made.document.id}`);
        } catch (failure) {
          problem.textContent = failure.detail || failure.message;
          problem.hidden = false;
        }
      } }, problem, h("div", { class: "field" }, h("label", { for: "cv-name" }, "Name"), name));
      body.append(form);
      footer.replaceChildren(h("div", { class: "row wrap" },
        h("button", { type: "submit", class: "btn btn-primary", form: "cv-new" }, copyOf ? "Copy" : "Start it"),
        h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel")));
      footer.hidden = false;
      name.focus();
      name.select();
    },
  });
}

// --- the preview (what prints) --------------------------------------------------------------------

function paper(data) {
  const head = data.header;
  return h("article", { class: "cv-paper", "aria-label": "The CV as it prints" },
    h("header", { class: "cv-head" }, h("h1", {}, head.name),
      h("p", {}, [data.document.name, head.organization].filter(Boolean).join(" · ")),
      head.orcid ? h("p", {}, `ORCID iD: https://orcid.org/${head.orcid}`) : null),
    data.sections.length ? data.sections.map((s) => h("section", { class: "cv-section" }, h("h2", {}, s.title),
      s.id === "overview" ? s.items.map((i) => h("p", {}, i.text))
        : s.id === "publications" ? h(s.numbered ? "ol" : "ul", { class: "cv-citations" }, s.items.map((p) => h("li", {},
          p.authors.flatMap((a, n) => [n ? ", " : "", a.self ? h("strong", {}, a.name) : a.name]),
          p.authors.length ? ". " : "", p.rest)))
          : h("ul", { class: "cv-items" }, s.items.map((i) => h("li", {},
            h("div", { class: "cv-item-main" }, h("strong", {}, i.main), i.dates ? h("span", { class: "cv-dates" }, i.dates) : null),
            i.meta ? h("div", {}, i.meta) : null, i.note ? h("div", { class: "cv-note" }, i.note) : null)))))
      : h("p", { class: "subtle" }, "Nothing is included yet. Choose sections on the left."));
}

// --- the editor ------------------------------------------------------------------------------------

function editor(data, save) {
  const spec = structuredClone(data.document.spec);
  const included = new Map(spec.sections.map((s) => [s.id, s]));
  const order = [...spec.sections.map((s) => s.id), ...data.sections_available.map((s) => s.id).filter((id) => !included.has(id))];
  const label = Object.fromEntries(data.sections_available.map((s) => [s.id, s.label]));
  const commit = () => save({ sections: order.filter((id) => included.has(id)).map((id) => included.get(id)) });

  const rows = order.map((sid, index) => {
    const on = included.get(sid);
    const options = data.options[sid] ?? [];
    const move = (step) => () => {
      const next = index + step;
      if (next < 0 || next >= order.length) return;
      [order[index], order[next]] = [order[next], order[index]];
      commit();
    };
    const head = h("div", { class: "cv-ed-head" },
      h("label", { class: "check grow" }, h("input", { type: "checkbox", checked: Boolean(on), onchange: (event) => {
        if (event.currentTarget.checked) included.set(sid, sid === "publications" ? { id: sid, pick: null, mode: "all", numbered: true } : { id: sid, pick: null });
        else included.delete(sid);
        commit();
      } }), label[sid], h("span", { class: "subtle" }, ` (${options.length})`)),
      iconButton("chevronUp", `Move ${label[sid]} up`, { disabled: index === 0, onclick: move(-1) }),
      iconButton("chevronDown", `Move ${label[sid]} down`, { disabled: index === order.length - 1, onclick: move(1) }));
    if (!on || !options.length) return h("li", { class: "cv-ed-section" }, head);
    const chosen = on.pick ? new Set(on.pick) : null;
    const pickAll = sid === "publications" ? on.mode !== "chosen" : !chosen;
    const controls = [];
    if (sid === "publications") {
      const mode = h("select", { class: "input", "aria-label": "Which publications", onchange: (event) => {
        on.mode = event.currentTarget.value;
        if (on.mode === "since" && !on.since) on.since = new Date().getFullYear() - 5;
        if (on.mode === "chosen" && !on.pick) on.pick = options.slice(0, 10).map((o) => o.id);
        commit();
      } }, [["all", "All confirmed"], ["since", "Since a year"], ["chosen", "Chosen ones"]].map(([v, t]) =>
        h("option", { value: v, selected: (on.mode ?? "all") === v }, t)));
      controls.push(h("div", { class: "row-sm wrap" }, mode,
        on.mode === "since" ? h("input", { class: "input cv-year", type: "number", min: 1900, max: 2100, value: on.since,
          "aria-label": "Since the year", onchange: (event) => { on.since = Number(event.currentTarget.value); commit(); } }) : null,
        h("label", { class: "check" }, h("input", { type: "checkbox", checked: on.numbered !== false,
          onchange: (event) => { on.numbered = event.currentTarget.checked; commit(); } }), "Numbered")));
    } else {
      controls.push(h("label", { class: "check" }, h("input", { type: "checkbox", checked: pickAll, onchange: (event) => {
        on.pick = event.currentTarget.checked ? null : options.map((o) => o.id);
        commit();
      } }), "All of them, including any added later"));
    }
    if (!pickAll) {
      const picked = on.pick ?? [];
      const byId = Object.fromEntries(options.map((o) => [o.id, o]));
      const list = [...picked.filter((id) => byId[id]), ...options.map((o) => o.id).filter((id) => !chosen?.has(id))];
      controls.push(h("ul", { class: "cv-ed-entries" }, list.map((id) => {
        const position = picked.indexOf(id);
        const shift = (step) => () => {
          const next = position + step;
          if (position < 0 || next < 0 || next >= picked.length) return;
          [picked[position], picked[next]] = [picked[next], picked[position]];
          on.pick = picked;
          commit();
        };
        return h("li", { class: "row-sm" },
          h("label", { class: "check grow" }, h("input", { type: "checkbox", checked: position >= 0, onchange: (event) => {
            on.pick = event.currentTarget.checked ? [...picked, id] : picked.filter((x) => x !== id);
            commit();
          } }), byId[id].label),
          position >= 0 ? iconButton("chevronUp", `Move ${byId[id].label} up`, { disabled: position === 0, onclick: shift(-1) }) : null,
          position >= 0 ? iconButton("chevronDown", `Move ${byId[id].label} down`, { disabled: position === picked.length - 1, onclick: shift(1) }) : null);
      })));
    }
    return h("li", { class: "cv-ed-section is-on" }, head, h("div", { class: "cv-ed-body" }, controls));
  });
  return card({ title: "What it includes", pad: true, attrs: { class: "card cv-editor no-print" },
    body: h("div", { class: "stack-sm" },
      h("p", { class: "subtle" }, "Tick the sections to include and put them in order. Entries come from the profile and confirmed records, so the CV stays current."),
      h("ul", { class: "plain-list cv-ed-sections" }, rows)) });
}

/** The page header shared by CVs and biosketches: print, download, rename, copy, delete. */
export function documentHeader(data, { url, personId, docId, reload, eyebrow }) {
  const noun = data.document.kind === "nih_biosketch" ? "biosketch" : "CV";
  const rename = h("button", { type: "button", class: "btn btn-ghost", onclick: () => openDrawer({
    title: `Rename this ${noun}`,
    render: (body, footer, close) => {
      const name = h("input", { class: "input", id: "cv-rename", maxlength: 200, value: data.document.name });
      const form = h("form", { id: "cv-rename-form", onsubmit: async (event) => {
        event.preventDefault();
        try {
          await post(url, { name: name.value });
          close();
          reload();
        } catch (failure) {
          toast(failure.detail || failure.message);
        }
      } }, h("div", { class: "field" }, h("label", { for: "cv-rename" }, "Name"), name));
      body.append(form);
      footer.replaceChildren(h("div", { class: "row wrap" },
        h("button", { type: "submit", class: "btn btn-primary", form: "cv-rename-form" }, "Rename"),
        h("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel")));
      footer.hidden = false;
      name.focus();
    },
  }) }, icon("edit", "icon icon-sm"), "Rename");
  const actions = [
    h("button", { type: "button", class: "btn btn-primary", onclick: () => window.print() }, icon("download", "icon icon-sm"), "Print or save as PDF"),
    h("a", { class: "btn", href: `${url}/markdown`, download: "" }, icon("download", "icon icon-sm"), "Download Markdown"),
    rename,
    h("button", { type: "button", class: "btn btn-ghost", onclick: () => newDocument(personId, { copyOf: docId, suggested: `${data.document.name} (copy)` }) }, "Copy"),
    h("button", { type: "button", class: "btn btn-ghost btn-reject", onclick: () => confirmAction({
      title: `Delete this ${noun}?`, text: h("p", {}, `“${data.document.name}” will be deleted. The profile and its records stay as they are.`),
      confirmLabel: "Delete", tone: "reject", run: async () => {
        try {
          await request("DELETE", url);
          invalidate();
          navigate(`/people/${encodeURIComponent(personId)}`);
        } catch (failure) {
          toast(failure.detail || failure.message);
        }
      } }) }, "Delete"),
  ];
  return h("div", { class: "no-print" }, pageHeader({
    eyebrow: h("a", { href: `/people/${encodeURIComponent(personId)}` }, `${eyebrow} · ${data.header.name}`),
    title: data.document.name,
    meta: h("span", { class: "subtle" }, `Changed ${relative(data.document.updated_at)} by ${data.document.updated_by}`),
    actions,
  }));
}

export function cvPage(main, params, { samePage = false } = {}) {
  const personId = params.id;
  const docId = params.cv;
  const url = `${base(personId)}/${encodeURIComponent(docId)}`;
  const reload = () => cvPage(main, params, { samePage: true });
  return show(main, {
    title: (data) => `${data.document.name} · ${data.header.name}`,
    quiet: samePage,
    load: async () => {
      const data = (await get(url, null, { fresh: true })).data;
      // A biosketch has its own editor, loaded when one is opened.
      if (data.document.kind === "nih_biosketch") data.B = await import("./biosketch.js");
      return data;
    },
    view: (data) => {
      const ctx = { url, personId, docId, reload };
      if (data.B) return data.B.biosketchView(data, { ...ctx, header: documentHeader(data, { ...ctx, eyebrow: "NIH biosketch" }) });
      const preview = h("div", { class: "cv-preview" }, paper(data));
      const save = async (spec) => {
        try {
          const next = (await post(url, { spec })).data;
          Object.assign(data, next);
          preview.replaceChildren(paper(next));
          editorSlot.replaceChildren(editor(next, save));
        } catch (failure) {
          toast(failure.detail || failure.message);
        }
      };
      const editorSlot = h("div", {}, editor(data, save));
      return h("div", { class: "stack-lg" }, documentHeader(data, { ...ctx, eyebrow: "CV" }),
        h("div", { class: "cv-layout" }, editorSlot, preview));
    },
  });
}

/** The CVs and biosketches card on a profile's About tab, for those who keep it. */
export function documentsCard(personId) {
  const holder = h("div", {});
  get(base(personId), null, { fresh: true }).then(({ data }) => {
    const label = { cv: "CV", nih_biosketch: "NIH biosketch" };
    holder.replaceChildren(card({ title: "CVs and biosketches", pad: true, attrs: { "data-profile-section": "cvs" },
      action: h("div", { class: "row-sm" },
        h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => newDocument(personId) }, icon("plus", "icon icon-sm"), "New CV"),
        h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => newDocument(personId, { kind: "nih_biosketch" }) }, icon("plus", "icon icon-sm"), "New NIH biosketch")),
      body: data.length ? h("ul", { class: "plain-list profile-entries" }, data.map((d) => h("li", { class: "profile-entry" },
        h("div", { class: "stack-xs grow" }, h("a", { class: "strong", href: `/people/${encodeURIComponent(personId)}/cvs/${d.id}` }, d.name),
          h("span", { class: "subtle" }, `${label[d.kind] ?? d.kind} · changed ${relative(d.updated_at)} by ${d.updated_by}`)))))
        : h("p", { class: "subtle" }, "CVs and NIH biosketches built from this profile and confirmed records: choose what each includes, keep several versions, and print, save as PDF, or download them. A biosketch follows NIH's current form and checks its limits, ready to paste into SciENcv.") }));
  }).catch(() => holder.replaceChildren());
  return holder;
}
