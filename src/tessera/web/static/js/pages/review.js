// Review queue: suggested authorships in priority order, decided from the keyboard.
//
// The list is the server's queue (strong matches grouped by person first, then conflicts,
// then ambiguous names). A decision removes the item at once and is sent in the
// background; the next items' details are prefetched so the panel never waits.

import { get, post, prefetch } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { number, plural, scoreLabel } from "../core/format.js";
import { navigate, query, setQuery } from "../core/router.js";
import { assertionDetail, confirmAction, showShortcuts } from "../components/drawer.js";
import { toast } from "../components/toast.js";
import { callout, card, chip, emptyState, pageHeader, segmented } from "../components/ui.js";
import { show } from "./page.js";

const BANDS = [
  { id: "open", label: "Strong + possible", bands: ["strong", "possible"] },
  { id: "strong", label: "Strong", bands: ["strong"] },
  { id: "possible", label: "Possible", bands: ["possible"] },
  { id: "unlikely", label: "Unlikely", bands: ["unlikely"] },
];
const PAGE = 50;
const AHEAD = 5;
const DONE = { accept: "Accepted", reject: "Rejected", defer: "Deferred" };
const KEYS = [
  ["j / ↓", "Next suggestion"], ["k / ↑", "Previous suggestion"], ["a", "Accept: this person wrote it"],
  ["r", "Reject: someone else with a similar name"], ["d", "Defer: decide later"], ["u", "Undo the last decision"],
  ["o", "Open the publication"], ["p", "Only this person, or everyone again"], ["?", "Show these keys"],
];

export function review(main) {
  const params = query();
  const band = BANDS.find((b) => b.id === params.get("band")) ?? BANDS[0];
  const personFilter = params.get("person");
  const state = {
    items: [], remaining: 0, counts: {}, selected: 0, exhausted: false, loading: false,
    undo: [], decided: 0, opened: Date.now(), detailToken: 0, closeToast: null,
    inflight: [], // decisions sent but not yet saved, so server counts lag behind them
  };

  async function fetchMore() {
    if (state.loading || state.exhausted) return;
    state.loading = true;
    try {
      const body = await get("/api/v1/review", { band: band.bands, person: personFilter, limit: PAGE,
        offset: state.items.length }, { fresh: true });
      const known = new Set(state.items.map((i) => i.assertion.id));
      state.items.push(...body.data.filter((i) => !known.has(i.assertion.id)));
      // The server has not seen decisions still in flight; subtract them so counts never jump.
      state.remaining = Math.max(0, body.meta.total - state.inflight.length);
      state.counts = { ...(body.meta.bands ?? {}) };
      for (const it of state.inflight) {
        const key = it.assertion.band;
        if (state.counts[key] != null) state.counts[key] = Math.max(0, state.counts[key] - 1);
      }
      state.exhausted = body.data.length < PAGE;
    } finally {
      state.loading = false;
    }
  }

  return show(main, {
    title: "Review queue",
    load: async () => {
      await fetchMore();
      const person = personFilter
        ? (await get(`/api/v1/people/${encodeURIComponent(personFilter)}`).catch(() => null))?.data
        : null;
      return { person };
    },
    view: ({ person }) => {
      const listEl = h("ol", { class: "review-list", role: "listbox", "aria-label": "Suggestions" });
      const detailEl = h("div", { class: "review-detail" });
      const metaEl = h("span");
      const sessionEl = h("span", { class: "subtle", "aria-live": "polite" });
      const bandBar = h("div", { class: "band-bar" });
      const filterEl = person ? h("span", { class: "row-sm" }, chip("person", person.name, `/people/${person.id}`),
        h("a", { href: hrefWith({ person: null }), class: "subtle" }, "Show everyone")) : null;

      const root = h("div", { class: "stack-lg review-page" },
        pageHeader({
          eyebrow: "Curate",
          title: "Review queue",
          meta: [metaEl, sessionEl],
          actions: [h("button", { type: "button", class: "btn btn-ghost", onclick: () => showShortcuts("Keyboard shortcuts", KEYS) },
            icon("keyboard", "icon icon-sm"), "Shortcuts")],
        }),
        h("div", { class: "toolbar" }, bandBar, filterEl),
        h("div", { class: "review" }, h("div", { class: "review-pane card" }, listEl), detailEl));

      function bandLabel(b) {
        const count = b.id === "open" ? (state.counts.strong ?? 0) + (state.counts.possible ?? 0) : state.counts[b.id];
        return count != null ? `${b.label} · ${number(count)}` : b.label;
      }

      function hrefWith(updates) {
        const next = new URLSearchParams(window.location.search);
        for (const [k, v] of Object.entries(updates)) {
          if (v == null) next.delete(k); else next.set(k, v);
        }
        const search = next.toString();
        return `/review${search ? `?${search}` : ""}`;
      }

      function renderMeta() {
        bandBar.replaceChildren(segmented(BANDS.map((b) => ({ id: b.id, label: bandLabel(b) })), band.id,
          (id) => setQuery({ band: id === "open" ? null : id }), "Likelihood band"));
        metaEl.textContent = state.remaining
          ? `${plural(state.remaining, "suggestion")} to review${person ? ` for ${person.name}` : ""}`
          : "Nothing waiting";
        if (state.decided) {
          const each = (Date.now() - state.opened) / 1000 / state.decided;
          sessionEl.textContent = `${plural(state.decided, "decision")} this session · ${each < 60 ? `${each.toFixed(1)} s` : `${Math.round(each / 60)} min`} each`;
        } else {
          sessionEl.textContent = "";
        }
      }

      function renderList() {
        let lastPerson = null;
        const nodes = [];
        state.items.forEach((it, index) => {
          const groupStrong = it.assertion.band === "strong" && !personFilter;
          if (groupStrong && it.person.id !== lastPerson) {
            nodes.push(h("li", { class: "review-group", role: "presentation" },
              h("span", { class: "grow" }, it.person.name),
              h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: (event) => {
                event.stopPropagation();
                bulkAccept(it.person);
              } }, "Accept all strong")));
          }
          lastPerson = groupStrong ? it.person.id : null;
          const pub = it.publication;
          nodes.push(h("li", {
            class: "review-item", role: "option", id: `review-${it.assertion.id}`,
            "aria-selected": String(index === state.selected), onclick: () => select(index),
          },
          h("div", { class: "review-item-title" }, pub.title ?? `PMID ${pub.pmid}`),
          h("div", { class: "review-item-meta" },
            h("span", { class: "truncate" }, it.person.name),
            pub.year ? h("span", { class: "tabular" }, pub.year) : null,
            h("span", { class: "band-score", "data-band": it.assertion.band }, scoreLabel(it.assertion.score)))));
        });
        if (!state.exhausted && state.items.length) {
          nodes.push(h("li", { class: "review-more", role: "presentation" },
            h("button", { type: "button", class: "btn btn-sm", onclick: async () => { await fetchMore(); renderList(); } }, "Load more")));
        }
        listEl.replaceChildren(...nodes);
        listEl.setAttribute("aria-activedescendant", state.items[state.selected] ? `review-${state.items[state.selected].assertion.id}` : "");
        listEl.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
        renderMeta();
      }

      function select(index) {
        if (!state.items.length) return;
        state.selected = Math.max(0, Math.min(index, state.items.length - 1));
        renderList();
        renderDetail();
        if (state.items.length - state.selected < 10) fetchMore().then(renderList).catch(() => {});
      }

      function prefetchAhead() {
        for (const it of state.items.slice(state.selected + 1, state.selected + 1 + AHEAD)) {
          prefetch(`/api/v1/assertions/${it.assertion.id}`);
          prefetch(`/api/v1/publications/${encodeURIComponent(it.publication.pmid)}`);
        }
      }

      async function renderDetail() {
        const it = state.items[state.selected];
        const token = ++state.detailToken;
        if (!it) {
          detailEl.replaceChildren(card({ pad: true, body: emptyState({ iconName: "check", title: "All caught up",
            text: state.decided ? `You made ${plural(state.decided, "decision")}. New suggestions arrive with the next sync.`
              : "No suggestions are waiting in this band.",
            action: band.id !== "unlikely" ? h("a", { class: "btn", href: hrefWith({ band: "unlikely" }) }, "Look at unlikely matches") : null }) }));
          return;
        }
        const pub = it.publication;
        const note = h("input", { class: "input", type: "text", placeholder: "Note (optional)", "aria-label": "Note for this decision", maxlength: 2000 });
        const actions = h("div", { class: "review-actions" },
          h("button", { type: "button", class: "btn btn-accept", onclick: () => act("accept", note.value) }, icon("check", "icon icon-sm"), "Accept", h("kbd", {}, "A")),
          h("button", { type: "button", class: "btn btn-reject", onclick: () => act("reject", note.value) }, icon("close", "icon icon-sm"), "Reject", h("kbd", {}, "R")),
          h("button", { type: "button", class: "btn btn-ghost", onclick: () => act("defer", note.value) }, "Defer", h("kbd", {}, "D")),
          h("button", { type: "button", class: "btn btn-ghost", onclick: () => select(state.selected + 1) }, "Skip", h("kbd", {}, "J")),
          h("button", { type: "button", class: "btn btn-ghost", disabled: !state.undo.length, onclick: undo }, icon("undo", "icon icon-sm"), "Undo", h("kbd", {}, "U")),
          note);
        const head = h("div", { class: "review-head" },
          h("h2", { class: "review-title" }, h("a", { href: `/publications/${encodeURIComponent(pub.pmid)}` }, pub.title ?? `PMID ${pub.pmid}`)),
          h("div", { class: "page-meta" }, pub.journal ? h("span", {}, pub.journal) : null, pub.year ? h("span", { class: "tabular" }, pub.year) : null,
            h("span", { class: "tabular" }, `PMID ${pub.pmid}`)),
          h("div", { class: "row-sm wrap" }, chip("person", it.person.name, `/people/${it.person.id}`),
            personFilter ? null : h("a", { class: "subtle", href: hrefWith({ person: it.person.id }) }, "Review only this person"),
            it.assertion.band === "strong" ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => bulkAccept(it.person) }, "Accept all strong for this person") : null));
        detailEl.replaceChildren(card({ pad: true, body: h("div", { class: "stack" }, head, actions,
          h("div", { class: "skeleton skeleton-line" }), h("div", { class: "skeleton skeleton-line" })) }));
        let assertion; let record;
        try {
          [assertion, record] = await Promise.all([
            get(`/api/v1/assertions/${it.assertion.id}`).then((r) => r.data),
            get(`/api/v1/publications/${encodeURIComponent(pub.pmid)}`).then((r) => r.data).catch(() => null),
          ]);
        } catch (error) {
          if (token === state.detailToken) detailEl.append(callout(error.detail || "The evidence could not be loaded.", "warn"));
          return;
        }
        if (token !== state.detailToken) return;
        detailEl.replaceChildren(card({ pad: true, body: h("div", { class: "stack-lg" },
          h("div", { class: "stack" }, head, actions),
          record ? authorSlots(record, assertion.qualifiers?.author_position) : null,
          ...assertionDetail(assertion)) }));
        prefetchAhead();
      }

      function count(it, delta) {
        const key = it.assertion.band;
        if (key && state.counts[key] != null) state.counts[key] = Math.max(0, state.counts[key] + delta);
      }

      function keep(id) {
        // Prefetched records and other assertions are unaffected by this decision.
        return (url) => url.startsWith("/api/v1/publications/") || (url.startsWith("/api/v1/assertions/") && !url.includes(id));
      }

      function notify(message, withUndo) {
        state.closeToast?.();
        state.closeToast = toast(message, withUndo ? { action: { label: "Undo", run: undo } } : {});
      }

      function act(action, noteText = "") {
        const it = state.items[state.selected];
        if (!it) return;
        const index = state.selected;
        state.items.splice(index, 1);
        state.remaining = Math.max(0, state.remaining - 1);
        state.decided += 1;
        count(it, -1);
        if (state.selected >= state.items.length) state.selected = Math.max(0, state.items.length - 1);
        const entry = { item: it, index, pending: null };
        state.inflight.push(it);
        const settled = () => { state.inflight = state.inflight.filter((x) => x !== it); };
        entry.pending = post(`/api/v1/assertions/${it.assertion.id}/decision`, { action, note: noteText.trim() || null }, { keep: keep(it.assertion.id) })
          .then(() => {
            settled();
            notify(`${DONE[action]}: ${shorten(it.publication.title)}`, true);
          })
          .catch((error) => {
            settled();
            state.undo = state.undo.filter((e) => e !== entry);
            state.items.splice(Math.min(index, state.items.length), 0, it);
            state.remaining += 1;
            state.decided -= 1;
            count(it, 1);
            renderList();
            notify(error.detail || "That decision was not saved.", false);
          });
        state.undo.push(entry);
        renderList();
        renderDetail();
        if (state.items.length - state.selected < 10) fetchMore().then(renderList).catch(() => {});
      }

      async function undo() {
        const last = state.undo.pop();
        if (!last) {
          notify("Nothing to undo in this session.", false);
          return;
        }
        await last.pending;
        try {
          await post(`/api/v1/assertions/${last.item.assertion.id}/decision`, { action: "undo" }, { keep: keep(last.item.assertion.id) });
        } catch (error) {
          state.undo.push(last);
          notify(error.detail || "Undo did not work.", false);
          return;
        }
        const at = Math.min(last.index, state.items.length);
        state.items.splice(at, 0, last.item);
        state.selected = at;
        state.remaining += 1;
        state.decided = Math.max(0, state.decided - 1);
        count(last.item, 1);
        renderList();
        renderDetail();
        notify("Undone.", false);
      }

      async function bulkAccept(who) {
        let count = 0;
        try {
          count = (await get("/api/v1/review", { person: who.id, band: "strong", limit: 1 }, { fresh: true })).meta.total;
        } catch {
          count = state.items.filter((i) => i.person.id === who.id && i.assertion.band === "strong").length;
        }
        confirmAction({
          title: "Accept all strong matches",
          text: [h("p", {}, `Accept ${plural(count, "strong suggestion")} for ${who.name}?`),
            h("p", { class: "muted" }, "Each is recorded as its own decision, and you can undo them together right after.")],
          confirmLabel: `Accept ${number(count)}`,
          tone: "accept",
          run: async () => {
            let result;
            try {
              result = (await post("/api/v1/review/bulk", { person_id: who.id, band: "strong", action: "accept" })).data;
            } catch (error) {
              notify(error.detail || "The bulk decision did not work.", false);
              return;
            }
            const done = new Set(result.ids);
            state.items = state.items.filter((i) => !done.has(i.assertion.id));
            state.remaining = Math.max(0, state.remaining - result.decided);
            state.decided += result.decided;
            if (state.counts.strong != null) state.counts.strong = Math.max(0, state.counts.strong - result.decided);
            state.selected = Math.min(state.selected, Math.max(0, state.items.length - 1));
            renderList();
            renderDetail();
            state.closeToast?.();
            state.closeToast = toast(`Accepted ${plural(result.decided, "suggestion")} for ${who.name}.`, { action: { label: "Undo", run: async () => {
              await post("/api/v1/assertions/decisions", { ids: result.ids, action: "undo" });
              state.decided = Math.max(0, state.decided - result.decided);
              state.items = [];
              state.exhausted = false;
              await fetchMore();
              select(0);
              notify(`Undid ${plural(result.decided, "decision")}.`, false);
            } } });
          },
        });
      }

      function openPublication() {
        const it = state.items[state.selected];
        if (it) navigate(`/publications/${encodeURIComponent(it.publication.pmid)}`);
      }

      function togglePerson() {
        const it = state.items[state.selected];
        if (personFilter) setQuery({ person: null });
        else if (it) setQuery({ person: it.person.id });
      }

      const handlers = {
        j: () => select(state.selected + 1), ArrowDown: () => select(state.selected + 1),
        k: () => select(state.selected - 1), ArrowUp: () => select(state.selected - 1),
        a: () => act("accept"), r: () => act("reject"), d: () => act("defer"), u: undo,
        o: openPublication, p: togglePerson, "?": () => showShortcuts("Keyboard shortcuts", KEYS),
      };
      const onKey = (event) => {
        if (!root.isConnected) {
          document.removeEventListener("keydown", onKey);
          return;
        }
        if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
        if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
        if (document.querySelector(".drawer, .palette")) return;
        const handler = handlers[event.key];
        if (!handler) return;
        event.preventDefault();
        handler();
      };
      document.addEventListener("keydown", onKey);

      renderList();
      renderDetail();
      return root;
    },
  });
}

function shorten(text, max = 60) {
  if (!text) return "this paper";
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** The author list around the matched position, with that slot marked and its affiliation. */
function authorSlots(record, position) {
  const authors = record.authors ?? [];
  if (!authors.length) return null;
  const show = new Set();
  const n = authors.length;
  if (n <= 12) authors.forEach((_, i) => show.add(i));
  else {
    [0, 1, 2, n - 2, n - 1].forEach((i) => show.add(i));
    if (position) [position - 2, position - 1, position].forEach((i) => i >= 0 && i < n && show.add(i));
  }
  const items = [];
  let gap = false;
  authors.forEach((a, i) => {
    if (!show.has(i)) {
      if (!gap) items.push(h("li", { class: "author-gap", "aria-hidden": "true" }, "…"));
      gap = true;
      return;
    }
    gap = false;
    const name = a.collective_name ?? [a.given, a.family].filter(Boolean).join(" ");
    const slot = a.position === position;
    items.push(h("li", { class: slot ? "author author-slot" : "author", value: a.position },
      h("span", {}, name),
      slot ? h("span", { class: "pill pill-accent" }, "This position") : null,
      slot && a.affiliations?.length ? h("div", { class: "author-affiliation" }, a.affiliations[0]) : null,
      a.people?.length && !slot ? h("span", { class: "subtle" }, ` · ${a.people.map((m) => m.person.name).join(", ")}`) : null));
  });
  return h("section", { class: "drawer-section" },
    h("h3", {}, `Authors (${n})`),
    h("ol", { class: "author-list compact" }, items));
}
