// Admin: data health for every source, the evidence engine, the index, and the lake.

import { get } from "../core/api.js";
import { h } from "../core/dom.js";
import { date, number, plural, relative } from "../core/format.js";
import { callout, card, freshness, pageHeader, pill } from "../components/ui.js";
import { show } from "./page.js";

const STATUS_TONE = { succeeded: "ok", partial: "warn", failed: "danger", running: "info" };
const DOC_NOUNS = { person: ["person", "people"], publication: ["publication"], grant: ["grant"], study: ["study", "studies"],
  opportunity: ["opportunity", "opportunities"], candidate: ["discovered person", "discovered people"] };

/** A checkpoint scope is "mode:hash"; the mode is what a reader needs. */
function checkpoint(c) {
  const mode = c.scope.split(":")[0];
  return h("div", { class: "row-sm", title: c.scope }, h("span", { class: "subtle" }, mode), h("span", { class: "tabular" }, c.watermark));
}

export function sources(main) {
  return show(main, {
    title: "Sources",
    load: async () => {
      const [health, runs] = await Promise.all([get("/api/v1/sources", null, { fresh: true }), get("/api/v1/runs", { limit: 15 }, { fresh: true })]);
      return { health: health.data, runs: runs.data };
    },
    view: ({ health, runs }) => {
      const labels = { evidence: "Evidence engine", ...Object.fromEntries(health.sources.map((s) => [s.name, s.label])) };
      const stale = health.sources.filter((s) => s.enabled && (s.stale || s.last_run?.status === "failed"));
      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: "Admin", title: "Sources and data health",
          meta: h("span", {}, "Where Tessera's data comes from, how fresh it is, and what happened on recent runs.") }),
        stale.length ? callout(`${stale.map((s) => s.label).join(", ")} ${stale.length === 1 ? "is" : "are"} out of date or failed on the last run. Run \`tessera sync\` to refresh.`, "warn") : null,
        card({ title: "Public sources", body: h("div", { class: "table-wrap" }, h("table", { class: "table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Source"), h("th", {}, "Last success"), h("th", {}, "Last run"),
            h("th", { class: "num" }, "Found"), h("th", {}, "Checkpoint"))),
          h("tbody", {}, health.sources.map((s) => h("tr", {},
            h("td", {}, freshness(s)),
            h("td", { class: "muted" }, s.last_success_at ? relative(s.last_success_at) : "never"),
            h("td", {}, s.last_run ? h("span", { class: "row-sm" }, pill(s.last_run.status, STATUS_TONE[s.last_run.status]),
              h("span", { class: "muted" }, s.last_run.mode ?? "")) : "–",
            s.last_run?.error ? h("div", { class: "subtle clamp-2" }, s.last_run.error) : null),
            h("td", { class: "num" }, s.last_run?.total_found != null ? number(s.last_run.total_found) : "–"),
            h("td", { class: "muted" }, s.checkpoints.length ? s.checkpoints.map(checkpoint) : "–")))))) }),
        h("div", { class: "grid-2" },
          card({ title: "Evidence engine", pad: true, body: health.evidence ? h("div", { class: "stack-sm" },
            h("div", { class: "row-sm" }, pill(health.evidence.status, STATUS_TONE[health.evidence.status]), h("span", { class: "muted" }, `last resolve ${relative(health.evidence.finished_at ?? health.evidence.started_at)}`)),
            health.evidence.error ? h("p", { class: "subtle" }, health.evidence.error) : null)
            : h("p", { class: "muted" }, "Not run yet. `tessera sync` resolves people and links.") }),
          card({ title: "Search index", pad: true, body: health.search_index ? h("div", { class: "stack-sm" },
            h("p", { class: "muted" }, `Built ${relative(health.search_index.built_at)}`),
            h("p", {}, Object.entries(health.search_index.documents).map(([k, v]) => plural(v, ...(DOC_NOUNS[k] ?? [k]))).join(" · ")))
            : h("p", { class: "muted" }, "Not built yet. `tessera build search` builds it.") })),
        card({ title: "Recent runs", body: h("div", { class: "table-wrap" }, h("table", { class: "table", "data-dense": "" },
          h("thead", {}, h("tr", {}, h("th", {}, "Started"), h("th", {}, "Source"), h("th", {}, "Kind"), h("th", {}, "Status"), h("th", { class: "num" }, "Found"), h("th", {}, "Notes"))),
          h("tbody", {}, runs.map((r) => h("tr", {},
            h("td", { class: "muted nowrap", title: r.started_at }, date(r.started_at)),
            h("td", {}, labels[r.source] ?? r.source), h("td", { class: "muted" }, [r.kind, r.mode].filter(Boolean).join(" · ")),
            h("td", {}, pill(r.status, STATUS_TONE[r.status])),
            h("td", { class: "num" }, r.total_found != null ? number(r.total_found) : "–"),
            h("td", { class: "subtle" }, h("span", { class: "clamp-2" }, r.error ?? (r.notes ?? []).join(" ") ?? "")))))) ) }),
        card({ title: "Lake tables", body: h("div", { class: "table-wrap" }, h("table", { class: "table", "data-dense": "" },
          h("thead", {}, h("tr", {}, h("th", {}, "Table"), h("th", { class: "num" }, "Current rows"), h("th", { class: "num" }, "Files"), h("th", {}, "Updated"))),
          h("tbody", {}, health.lake.map((t) => h("tr", {},
            h("td", { class: "tabular" }, t.table), h("td", { class: "num" }, number(t.rows_current)),
            h("td", { class: "num" }, number(t.files)), h("td", { class: "muted" }, t.updated_at ? relative(t.updated_at) : "never")))))) }));
    },
  });
}
