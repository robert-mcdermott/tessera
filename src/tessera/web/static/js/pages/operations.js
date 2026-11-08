// Admin → Operations (PLT-12): is the installation healthy? Runs over the last 30 days with
// failures and durations, freshness, sizes, the last backup, and what needs attention.

import { get } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { date, number, relative } from "../core/format.js";
import { callout, card, pageHeader, pill, sparkline } from "../components/ui.js";
import { show } from "./page.js";

function size(bytes) {
  if (bytes == null) return "–";
  const units = ["bytes", "KB", "MB", "GB", "TB"];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return i ? `${n.toFixed(1)} ${units[i]}` : `${number(n)} bytes`;
}

function seconds(s) {
  if (s == null) return "–";
  return s < 90 ? `${s.toFixed(s < 10 ? 1 : 0)} s` : `${(s / 60).toFixed(1)} min`;
}

const TONES = { succeeded: "ok", partial: "warn", failed: "danger", running: "" };

function statusPill(status) {
  return status ? pill(status, TONES[status] ?? "") : h("span", { class: "subtle" }, "–");
}

function sourcesTable(sources) {
  return h("div", { class: "table-wrap" }, h("table", { class: "table", "data-dense": "" },
    h("caption", { class: "sr-only" }, "Sources and jobs over the last 30 days"),
    h("thead", {}, h("tr", {}, ["Source or job", "Last run", "Last success", "Runs", "Failures", "Typical time", "Recent times", "Last error"]
      .map((label, i) => h("th", { scope: "col", class: [3, 4, 5].includes(i) ? "num" : null }, label)))),
    h("tbody", {}, sources.map((s) => h("tr", { "data-stale": s.stale ? "" : null },
      h("th", { scope: "row" }, s.label, s.stale ? h("span", { class: "small-mark", title: `No success in ${s.expected_days} days` }, " stale") : null,
        !s.enabled ? h("span", { class: "subtle" }, " · off") : null),
      h("td", {}, statusPill(s.last_status), s.last_run_at ? h("span", { class: "subtle" }, ` ${relative(s.last_run_at)}`) : null),
      h("td", {}, s.last_success_at ? relative(s.last_success_at) : "never"),
      h("td", { class: "num" }, number(s.runs)),
      h("td", { class: "num" }, s.failures ? h("strong", { class: "text-danger" }, number(s.failures)) : "0"),
      h("td", { class: "num" }, seconds(s.median_seconds)),
      h("td", {}, s.durations.length > 1 ? sparkline(s.durations, { width: 120, height: 24 }) : h("span", { class: "subtle" }, "–")),
      h("td", { class: "ops-error", title: s.last_error ?? "" }, s.last_error ? s.last_error.split("\n")[0] : ""))))));
}

function recentTable(runs) {
  return h("div", { class: "table-wrap" }, h("table", { class: "table", "data-dense": "" },
    h("caption", { class: "sr-only" }, "Recent runs"),
    h("thead", {}, h("tr", {}, ["Started", "Source or job", "Kind", "Status", "Time", "Records", "Error"]
      .map((label, i) => h("th", { scope: "col", class: [4, 5].includes(i) ? "num" : null }, label)))),
    h("tbody", {}, runs.map((r) => h("tr", {},
      h("td", { title: r.started_at }, `${date(r.started_at)} · ${relative(r.started_at)}`),
      h("td", {}, r.source), h("td", {}, r.kind), h("td", {}, statusPill(r.status)),
      h("td", { class: "num" }, seconds(r.seconds)),
      h("td", { class: "num" }, r.records == null ? "–" : number(r.records)),
      h("td", { class: "ops-error", title: r.error ?? "" }, r.error ? r.error.split("\n")[0] : ""))))));
}

function storage(sizes) {
  const parts = [["Ledger", sizes.ledger], ["Lake", sizes.lake], ["Raw responses", sizes.raw], ["Search and discovery", sizes.derived],
    ["Report snapshots", sizes.snapshots], ["Backups in the data home", sizes.backups]];
  const max = Math.max(...parts.map(([, v]) => v ?? 0), 1);
  return h("div", { class: "stack" },
    h("ol", { class: "category-bars" }, parts.map(([label, value]) => {
      const meter = h("span", { class: "category-meter" });
      meter.style.setProperty("--w", `${Math.max(((value ?? 0) / max) * 100, 0.8).toFixed(1)}%`);
      return h("li", {}, h("span", { class: "category-name" }, label), h("span", { class: "category-track", "aria-hidden": "true" }, meter),
        h("span", { class: "category-value tabular" }, size(value)), h("span", {}));
    })),
    h("details", {}, h("summary", {}, "Lake tables"),
      h("div", { class: "table-wrap" }, h("table", { class: "table", "data-dense": "" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Table"), h("th", { scope: "col", class: "num" }, "Rows written"),
          h("th", { scope: "col", class: "num" }, "Files"), h("th", { scope: "col", class: "num" }, "Size"))),
        h("tbody", {}, sizes.lake_tables.map((t) => h("tr", {}, h("td", {}, h("code", {}, t.table)),
          h("td", { class: "num" }, number(t.rows)), h("td", { class: "num" }, number(t.files)), h("td", { class: "num" }, size(t.bytes)))))))));
}

export function operations(main) {
  return show(main, {
    title: "Operations",
    load: () => get("/api/v1/operations", null, { fresh: true }),
    view: ({ data }) => {
      const backup = data.backup;
      const alerts = data.alerts;
      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: "Admin", title: "Operations",
          meta: [h("span", {}, data.job_running ? "A data job is running now" : "No data job is running"),
            h("span", {}, `As of ${date(data.generated_at)} ${new Date(data.generated_at).toLocaleTimeString()}`)] }),
        data.issues.length
          ? card({ title: `Needs attention (${data.issues.length})`, pad: true, attrs: { class: "card ops-issues" },
            body: h("ul", { class: "plain-list" }, data.issues.map((i) => h("li", { class: "row-sm" },
              icon(i.kind === "failure" ? "warning" : i.kind === "backup" ? "history" : "clock", "icon icon-sm"), h("span", {}, i.text)))) })
          : callout("Nothing needs attention: no failed runs in the last two days, every source is fresh, and backups are current."),
        h("div", { class: "grid-2" },
          card({ title: "Backups", pad: true, body: backup
            ? h("dl", { class: "kv" }, h("dt", {}, "Last"), h("dd", {}, `${date(backup.at)} (${relative(backup.at)})`),
              h("dt", {}, "Size"), h("dd", {}, size(backup.bytes)), h("dt", {}, "Where"), h("dd", {}, h("code", {}, backup.path)),
              h("dt", {}, "Schema"), h("dd", {}, backup.revision))
            : h("p", { class: "muted" }, "No backup yet. Run ", h("code", {}, "tessera backup"), " and schedule it; see Running Tessera in the guides.") }),
          card({ title: "Alerts", pad: true, body: h("dl", { class: "kv" },
            h("dt", {}, "Email to"), h("dd", {}, alerts.email.length ? alerts.email.join(", ") : "nobody ([alerts] email is empty)"),
            h("dt", {}, "Mail server"), h("dd", {}, alerts.mail ? "set ([mail])" : "not set: alerts show here only"),
            h("dt", {}, "Backups"), h("dd", {}, alerts.backup_days ? `warn after ${alerts.backup_days} days` : "not watched")) })),
        card({ title: "Sources and jobs, last 30 days", body: sourcesTable(data.sources) }),
        card({ title: "Storage", pad: true, body: storage(data.sizes) }),
        card({ title: "Recent runs", body: recentTable(data.recent) }));
    },
  });
}
