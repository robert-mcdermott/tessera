// The optional assistant, in the web app. The server names this module in /api/v1/meta only when
// [ai] is enabled, and serves it only then; the rest of the app never names it.

import { get, post } from "/js/core/api.js";
import { h, icon } from "/js/core/dom.js";
import { date, number, paragraphs, relative } from "/js/core/format.js";
import { navigate } from "/js/core/router.js";
import { csrfToken } from "/js/core/session.js";
import { closeDrawer, openDrawer } from "/js/components/drawer.js";
import { toast } from "/js/components/toast.js";
import { callout, card, entityIcon, pageHeader, pill } from "/js/components/ui.js";
import { show } from "/js/pages/page.js";

const TONE = { answered: "ok", declined: "", failed: "danger", limited: "warn", stopped: "" };
const KIND_ICONS = { group: "unit", figure: "concept", article: "publication" };
const EXAMPLES = [
  "Who works on T cell exhaustion?",
  "How many papers has the organization published each year?",
  "Which open funding opportunities fit our immunology program?",
];

// --- the conversation, kept while the app is open ------------------------------------------------

const talk = { turns: [], previous: null, controller: null, status: null, panel: null };

function stylesheet() {
  if (document.querySelector("link[data-ask]")) return;
  document.head.append(h("link", { rel: "stylesheet", href: "/js/ai/assistant.css", "data-ask": "" }));
}

/** Text with **bold**, `code`, and [ref] citations, as nodes (never as HTML). */
function inline(text, cite) {
  const out = [];
  const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^[\]\s]+\])/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("**")) out.push(h("strong", {}, inline(token.slice(2, -2), cite)));
    else if (token.startsWith("`")) out.push(h("code", {}, token.slice(1, -1)));
    else out.push(cite(token.slice(1, -1)) ?? token);
    last = match.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** A short answer's paragraphs and lists. */
function prose(text, cite) {
  const nodes = [];
  for (const block of text.split(/\n{2,}/)) {
    const lines = block.split("\n").filter((l) => l.trim());
    if (!lines.length) continue;
    const listed = lines.every((l) => /^\s*([-*•]|\d+[.)])\s+/.test(l));
    if (listed) {
      const ordered = /^\s*\d/.test(lines[0]);
      nodes.push(h(ordered ? "ol" : "ul", {}, lines.map((l) =>
        h("li", {}, inline(l.replace(/^\s*([-*•]|\d+[.)])\s+/, ""), cite)))));
    } else {
      const heading = lines[0].match(/^#{1,4}\s+(.*)$/);
      if (heading) nodes.push(h("p", { class: "ask-heading" }, inline(heading[1], cite)));
      const rest = heading ? lines.slice(1) : lines;
      if (rest.length) nodes.push(h("p", {}, rest.flatMap((l, i) => [i ? h("br") : null, ...inline(l, cite)])));
    }
  }
  return nodes;
}

// Records open in the app, closing the panel (it keeps the conversation); articles that are not
// Tessera's records open at PubMed in a new tab.
function linkAttrs(record) {
  if (/^https?:/.test(record.href)) return { href: record.href, target: "_blank", rel: "noopener" };
  return { href: record.href, onclick: (event) => follow(event, record.href) };
}

function citationLink(record, n) {
  return h("a", { class: "ask-cite", title: record.title, "aria-label": `Source ${n}: ${record.title}`,
    ...linkAttrs(record) }, String(n));
}

function follow(event, href) {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
  event.preventDefault();
  closeDrawer();
  navigate(href);
}

function sourceCard(record, n) {
  return h("a", { class: "ask-source", ...linkAttrs(record) },
    h("span", { class: "ask-source-n" }, String(n)),
    entityIcon(KIND_ICONS[record.kind] ?? record.kind, "sm"),
    h("span", { class: "ask-source-text" }, h("span", { class: "ask-source-title" }, record.title),
      record.subtitle ? h("span", { class: "ask-source-sub" }, record.subtitle) : null));
}

function renderTurn(turn) {
  const done = turn.done;
  const kept = (done?.citations ?? []).filter((c) => !c.removed);
  const removed = (done?.citations ?? []).filter((c) => c.removed);
  const numbers = new Map(kept.map((c, i) => [c.ref, i + 1]));
  const byRef = new Map((done?.citations ?? []).map((c) => [c.ref, c]));
  const cite = (ref) => {
    if (!/^(per_|pub:|grt:|stu:|opp:|grp:|fig:)/.test(ref)) return null;
    if (!done) return h("span", { class: "ask-cite ask-cite-pending", "aria-hidden": "true" }, "·");
    const record = byRef.get(ref);
    if (record && !record.removed) return citationLink(record, numbers.get(ref));
    return h("span", { class: "ask-cite ask-cite-removed", title: "Removed: no record the assistant was given has this reference" }, "removed");
  };
  const steps = turn.steps.length ? (done
    ? h("details", { class: "ask-steps" }, h("summary", {}, `Looked ${turn.steps.length === 1 ? "once" : `${turn.steps.length} times`}`),
      h("ul", {}, turn.steps.map((s) => h("li", {}, s))))
    : h("ul", { class: "ask-steps ask-steps-live", "aria-live": "polite" }, turn.steps.map((s, i) =>
      h("li", { class: i === turn.steps.length - 1 && !turn.text ? "ask-step-now" : "" }, s)))) : null;
  let answer;
  if (turn.error) answer = h("div", { class: "callout callout-warn", role: "alert" }, icon("warning", "icon icon-sm"), h("div", {}, turn.error));
  else if (done && done.status !== "answered") {
    answer = h("div", { class: `ask-note ask-note-${done.status}` }, icon(done.status === "declined" ? "info" : "warning", "icon icon-sm"),
      h("div", {}, done.answer));
  } else {
    // While streaming, a citation still being written ("[per_0") is held back until it closes.
    const text = done ? done.answer : turn.text.replace(/\[[^\]\n]*$/, "");
    answer = h("div", { class: "ask-answer" }, text ? prose(text, cite)
      : h("p", { class: "ask-waiting" }, turn.stopped ? "Stopped." : h("span", { class: "ask-dots", "aria-label": "Working" }, h("i"), h("i"), h("i"))));
  }
  const sources = done?.status === "answered" && kept.length ? h("div", { class: "ask-sources" },
    h("div", { class: "ask-label" }, kept.length === 1 ? "Source" : `Sources (${kept.length})`),
    kept.map((c, i) => sourceCard(c, i + 1)),
    removed.length ? h("p", { class: "ask-removed" }, `${removed.length === 1 ? "One citation was" : `${removed.length} citations were`} removed: the records it named were not among those the assistant was given.`) : null) : null;
  return h("article", { class: "ask-turn" },
    h("p", { class: "ask-question" }, turn.question),
    steps, answer, turn.stopped && done == null && turn.text ? h("p", { class: "ask-removed" }, "Stopped before the answer was checked; its citations are not shown.") : null,
    sources);
}

function scrolledToEnd(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
}

let frame = 0;
function redraw() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    const panel = talk.panel;
    if (!panel || !panel.list.isConnected) return;
    const stick = scrolledToEnd(panel.body);
    panel.list.replaceChildren(...talk.turns.map(renderTurn));
    panel.intro.hidden = talk.turns.length > 0;
    panel.newButton.hidden = !talk.turns.length || Boolean(talk.controller);
    panel.send.hidden = Boolean(talk.controller);
    panel.stop.hidden = !talk.controller;
    panel.input.disabled = Boolean(talk.controller);
    const left = talk.status?.left_today;
    panel.left.textContent = left == null ? "" : `${number(left)} question${left === 1 ? "" : "s"} left today`;
    if (stick) panel.body.scrollTop = panel.body.scrollHeight;
  });
}

async function ask(question) {
  question = question.trim();
  if (!question || talk.controller) return;
  const turn = { question, steps: [], text: "", done: null, error: null, stopped: false };
  talk.turns.push(turn);
  const controller = new AbortController();
  talk.controller = controller;
  redraw();
  try {
    const headers = { "Content-Type": "application/json", Accept: "application/x-ndjson" };
    const token = csrfToken();
    if (token) headers["X-CSRF-Token"] = token;
    const response = await fetch("/api/v1/ai/ask", { method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({ question, previous: talk.previous }), signal: controller.signal });
    if (!response.ok) {
      const problem = await response.json().catch(() => null);
      throw new Error(problem?.detail || `The server answered ${response.status}.`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, end).trim();
        buffer = buffer.slice(end + 1);
        if (line) handle(turn, JSON.parse(line));
      }
    }
  } catch (failure) {
    if (failure.name === "AbortError") turn.stopped = true;
    else turn.error = failure.message;
  } finally {
    talk.controller = null;
    redraw();
    talk.panel?.input.focus();
  }
}

function handle(turn, event) {
  if (event.type === "step") turn.steps.push(event.label);
  else if (event.type === "text") turn.text += event.text;
  else if (event.type === "discard") turn.text = "";
  else if (event.type === "done") {
    turn.done = event;
    if (event.status === "answered" || event.status === "declined") talk.previous = event.exchange;
    if (talk.status?.left_today != null && event.status !== "limited") talk.status.left_today = Math.max(talk.status.left_today - 1, 0);
  }
  redraw();
}

export function openAsk(question = "") {
  stylesheet();
  openDrawer({
    title: "Ask",
    subtitle: "Answers from Tessera's records, with the records they rest on.",
    label: "Ask about the organization's research",
    render: (body, footer) => {
      body.classList.add("ask-body");
      const list = h("div", { class: "ask-list" });
      const input = h("textarea", { class: "input ask-input", rows: 2, maxlength: 2000, "aria-label": "Your question",
        placeholder: "Ask about people, papers, grants, studies, or opportunities…" });
      const submit = () => {
        const text = input.value;
        input.value = "";
        ask(text);
      };
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          submit();
        }
      });
      const intro = h("div", { class: "ask-intro" },
        h("p", {}, "Ask a question about the organization's research. The answer comes only from what Tessera holds and you may see, and names the records it rests on."),
        h("div", { class: "ask-examples" }, EXAMPLES.map((q) => h("button", { type: "button", class: "btn btn-sm", onclick: () => ask(q) }, q))));
      const send = h("button", { type: "button", class: "btn btn-primary", onclick: submit }, "Ask");
      const stop = h("button", { type: "button", class: "btn", hidden: true, onclick: () => talk.controller?.abort() }, "Stop");
      const newButton = h("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: () => {
        talk.turns = [];
        talk.previous = null;
        redraw();
        input.focus();
      } }, "New conversation");
      const left = h("span", { class: "ask-left" });
      body.append(intro, list);
      footer.hidden = false;
      footer.append(input, h("div", { class: "ask-row" }, left, h("span", { class: "spacer" }), newButton, stop, send),
        h("p", { class: "ask-fine" }, `Drafted by ${talk.status?.model ?? "a language model"} from Tessera's records. Check the sources before relying on an answer.`));
      talk.panel = { body, list, input, intro, send, stop, newButton, left };
      redraw();
      if (question) ask(question);
      else setTimeout(() => input.focus(), 0);
    },
  });
}

// --- plain-language summaries -----------------------------------------------------------------------

const ACTIONS = { drafted: "drafted", edited: "edited", approved: "approved", rejected: "rejected", replaced: "replaced" };

function summaryText(text) {
  return h("div", { class: "prose lay-text" }, paragraphs(text).map((p) => h("p", {}, p)));
}

function approvedNote(s) {
  return h("p", { class: "lay-note" }, icon("sparkle", "icon icon-sm"),
    `${s.label}; approved by ${s.decided_by}${s.decided_at ? ` on ${date(s.decided_at)}` : ""}.`);
}

async function act(button, label, run) {
  const before = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    await run();
  } catch (failure) {
    toast(failure.detail || failure.message);
    button.disabled = false;
    button.textContent = before;
  }
}

function draftPanel(d, reload) {
  const box = h("textarea", { class: "input lay-edit", rows: 7, maxlength: 4000, "aria-label": "The draft, to edit before approving" }, d.draft);
  const approve = h("button", { type: "button", class: "btn btn-sm btn-accept" }, "Approve");
  const reject = h("button", { type: "button", class: "btn btn-sm btn-reject" }, "Reject");
  approve.onclick = () => act(approve, "Approving…", async () => {
    await post(`/api/v1/ai/summaries/${d.id}/approve`, box.value.trim() === d.draft.trim() ? {} : { text: box.value });
    toast("Approved. It now shows wherever the summary appears.");
    reload();
  });
  reject.onclick = () => act(reject, "Rejecting…", async () => {
    await post(`/api/v1/ai/summaries/${d.id}/reject`, {});
    toast("Rejected.");
    reload();
  });
  return h("div", { class: "lay-draft" },
    h("div", { class: "row-sm wrap" }, pill("Draft", "warn"),
      h("span", { class: "subtle" }, `Asked for by ${d.requested_by} ${relative(d.requested_at)} · ${d.model}. Only reviewers see drafts.`)),
    box,
    h("p", { class: "subtle" }, "Check every statement against the records. Edit it if you like; both the draft and your edit are kept."),
    h("div", { class: "row-sm" }, approve, reject));
}

function summaryBody(data, reload, { aside = false } = {}) {
  const s = data.approved;
  const parts = [];
  if (s && !aside) parts.push(summaryText(s.text), approvedNote(s));
  if (!data.may_review) return parts;
  if (aside) {
    parts.push(h("p", { class: "subtle" }, s
      ? "The group's summary is approved but not on its page. To show it, add a Summary block when you edit the page."
      : "A short summary of the group's work for a general reader, drafted from its confirmed records. Nobody else sees it until you approve it, and it shows only where you add a Summary block to the page."));
  } else if (!s && !(data.drafts ?? []).length) {
    parts.push(h("p", { class: "subtle" }, "A short summary of this work for a general reader, drafted from confirmed records only. Nobody else sees it until a reviewer approves it."));
  }
  if (data.stale?.changes?.length) {
    parts.push(h("div", { class: "callout callout-warn", role: "note" }, icon("info", "icon icon-sm"),
      h("div", {}, `The records have changed since this summary was approved: ${data.stale.changes.join(", ")}. Draft a new one to review; this one stays until you approve another.`)));
  }
  for (const d of data.drafts ?? []) parts.push(draftPanel(d, reload));
  const make = h("button", { type: "button", class: "btn btn-sm" }, icon("sparkle", "icon icon-sm"),
    s || (data.drafts ?? []).length ? "Draft a new summary" : "Draft a summary");
  make.onclick = () => act(make, "Drafting… (a few seconds)", async () => {
    await post(`/api/v1/ai/summaries/${encodeURIComponent(data.subject.ref)}/draft`, {});
    reload();
  });
  parts.push(h("div", { class: "row-sm" }, make));
  if ((data.history ?? []).length) {
    parts.push(h("details", { class: "lay-history" }, h("summary", {}, "History"),
      h("ul", { class: "plain-list" }, data.history.map((e) => h("li", { class: "subtle" },
        `${e.actor} ${ACTIONS[e.action] ?? e.action} a summary ${relative(e.at)}${e.note ? ` (${e.note})` : ""}`)))));
  }
  return parts;
}

/** A card that fills itself: the approved summary for everyone, and drafts for its reviewers. */
function summaryCard(ref, { title = "Summary", aside = false } = {}) {
  stylesheet();
  const holder = h("div", { class: "lay-holder" });
  const load = async () => {
    let data;
    try {
      data = (await get(`/api/v1/ai/summaries/${encodeURIComponent(ref)}`, null, { fresh: true })).data;
    } catch {
      holder.replaceChildren();
      return;
    }
    const nothingYet = data.may_review && !data.approved && !(data.drafts ?? []).length;
    if (nothingYet && !aside) {
      // Reviewers (a curator may review everyone) get a quiet line, not a card on every profile.
      const make = h("button", { type: "button", class: "btn btn-sm btn-ghost" }, icon("sparkle", "icon icon-sm"), "Draft a plain-language summary");
      make.onclick = () => act(make, "Drafting… (a few seconds)", async () => {
        await post(`/api/v1/ai/summaries/${encodeURIComponent(data.subject.ref)}/draft`, {});
        load();
      });
      holder.replaceChildren(h("div", { class: "lay-empty" }, make,
        h("span", { class: "subtle" }, "From confirmed records; nobody else sees it until a reviewer approves it.")));
      return;
    }
    const body = summaryBody(data, load, { aside });
    holder.replaceChildren(...(body.length ? [card({ title, pad: true, attrs: { "data-lay": "" }, body: h("div", { class: "stack" }, body) })] : []));
  };
  load();
  return holder;
}

function installSummaries(provide) {
  provide("person:intro", ({ profile }) => summaryCard(profile.id));
  provide("group:summary", ({ block, group }) => summaryCard(`grp:${group.slug}`, { title: block.title || "Summary" }));
  provide("group:aside", ({ group }) => summaryCard(`grp:${group.slug}`, { aside: true }));
  provide("group:inbox", ({ group, items }) => items.summary ? card({ title: "Summary to draft again", pad: true, body: h("div", { class: "stack-sm" },
    h("p", {}, `The group's records have changed since its summary was approved: ${items.summary.changes.join(", ")}.`),
    h("a", { class: "btn btn-sm", href: `/groups/${group.slug}` }, "Open the group to draft a new one")) }) : null);
  provide("inbox:waiting", ({ digest }) => digest.summary ? h("li", {}, h("a", { href: `/people/${digest.summary.subject_id}` },
    `The summary on your profile: your records have changed (${digest.summary.changes.join(", ")})`)) : null);
}

// --- the log, for admins ---------------------------------------------------------------------------

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
          h("thead", {}, h("tr", {}, ["When", "Who", "Kind", "Question", "Outcome", "Tools", "Citations", "Tokens", "Time"].map((label) => h("th", { scope: "col" }, label)))),
          h("tbody", {}, exchanges.data.map((x) => {
            const cited = x.citations ?? [];
            const removed = cited.filter((c) => c.removed).length;
            return h("tr", {},
              h("td", { title: x.started_at }, relative(x.started_at)), h("td", {}, x.asked_by), h("td", {}, x.kind),
              h("td", { class: "ops-error", title: x.answer ?? "" }, x.question ?? ""),
              h("td", {}, pill(x.status, TONE[x.status] ?? "")),
              h("td", { class: "num", title: (x.request?.tools ?? []).map((t) => `${t.name} ${JSON.stringify(t.arguments)}`).join("\n") }, number((x.request?.tools ?? []).length)),
              h("td", { class: "num" }, removed ? `${number(cited.length - removed)} (+${number(removed)} removed)` : number(cited.length)),
              h("td", { class: "num" }, number((x.usage?.input ?? 0) + (x.usage?.output ?? 0))),
              h("td", { class: "num" }, x.seconds != null ? `${x.seconds} s` : "–"));
          })))) }));
    },
  });
}

export function install({ main, route, shell, addAction, provide }) {
  route("/admin/assistant", () => adminPage(main));
  if (provide) installSummaries(provide);
  shell.addNavItem("Admin", { href: "/admin/assistant", label: "Assistant", iconName: "sparkle", when: "admin",
    match: (p) => p.startsWith("/admin/assistant") });
  get("/api/v1/ai/status", null, { fresh: true }).then(({ data }) => {
    talk.status = data;
    if (!data.may_use) return;
    stylesheet();
    shell.addTopbarItem(h("button", { type: "button", class: "btn btn-ghost ask-open", title: "Ask about the organization's research",
      onclick: () => openAsk() }, icon("sparkle", "icon icon-sm"), h("span", {}, "Ask")));
    addAction({ group: "Assistant", title: "Ask a question…", iconName: "sparkle", run: () => openAsk() });
  }).catch(() => {});
}
