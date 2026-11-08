// The optional assistant, in the web app. The server names this module in /api/v1/meta only when
// [ai] is enabled, and serves it only then; the rest of the app never names it.

import { get, post } from "/js/core/api.js";
import { h, icon } from "/js/core/dom.js";
import { date, number, relative } from "/js/core/format.js";
import { toast } from "/js/components/toast.js";
import { callout, card, pageHeader, pill } from "/js/components/ui.js";
import { show } from "/js/pages/page.js";

const TONE = { answered: "ok", declined: "", failed: "danger", limited: "warn" };

function adminPage(main) {
  return show(main, {
    title: "Assistant",
    load: async () => {
      const [status, exchanges, usage] = await Promise.all([
        get("/api/v1/ai/status", null, { fresh: true }), get("/api/v1/ai/exchanges", { limit: 100 }, { fresh: true }),
        get("/api/v1/ai/usage", null, { fresh: true })]);
      return { status: status.data, exchanges, usage: usage.data };
    },
    view: ({ status, exchanges, usage }) => {
      const tokens = usage.reduce((n, d) => n + d.input_tokens + d.output_tokens, 0);
      const checkButton = h("button", { type: "button", class: "btn", onclick: async (event) => {
        event.currentTarget.disabled = true;
        try {
          const out = (await post("/api/v1/ai/check", {})).data;
          toast(`${out.provider} answered in ${out.seconds} s: “${out.reply.trim().slice(0, 40)}”`);
        } catch (failure) {
          toast(failure.detail || failure.message);
        } finally {
          event.currentTarget.disabled = false;
        }
      } }, "Check the connection");
      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: "Admin", title: "Assistant", meta: [h("span", {}, `${status.provider} · ${status.model}`),
          h("span", {}, status.daily_limit ? `${number(status.daily_limit)} questions per person per day` : "No daily limit")],
          actions: [checkButton] }),
        callout("Every exchange is kept here: who asked, the model and prompt version, the tool calls and the records they returned, the answer, and its citations. Nothing is sent to the model that the person asking may not read, and no contact details are sent.", "info"),
        card({ title: "The last 30 days", pad: true, body: h("p", {}, `${number(usage.reduce((n, d) => n + d.exchanges, 0))} exchanges, ${number(tokens)} tokens, ${number(usage.reduce((n, d) => n + d.declined, 0))} declined, ${number(usage.reduce((n, d) => n + d.failed, 0))} failed.`) }),
        card({ title: `Exchanges (${number(exchanges.meta.total)})`, body: h("div", { class: "table-wrap" }, h("table", { class: "table", "data-dense": "" },
          h("thead", {}, h("tr", {}, ["When", "Who", "Kind", "Question", "Outcome", "Citations", "Tokens", "Time"].map((label) => h("th", { scope: "col" }, label)))),
          h("tbody", {}, exchanges.data.map((x) => h("tr", {},
            h("td", { title: x.started_at }, relative(x.started_at)), h("td", {}, x.asked_by), h("td", {}, x.kind),
            h("td", { class: "ops-error", title: x.question ?? "" }, x.question ?? ""),
            h("td", {}, pill(x.status, TONE[x.status] ?? "")), h("td", { class: "num" }, number((x.citations ?? []).length)),
            h("td", { class: "num" }, number((x.usage?.input ?? 0) + (x.usage?.output ?? 0))),
            h("td", { class: "num" }, x.seconds != null ? `${x.seconds} s` : "–")))))) }));
    },
  });
}

export function install({ main, route, shell }) {
  route("/admin/assistant", () => adminPage(main));
  shell.addNavItem("Admin", { href: "/admin/assistant", label: "Assistant", iconName: "sparkle", when: "admin",
    match: (p) => p.startsWith("/admin/assistant") });
}
