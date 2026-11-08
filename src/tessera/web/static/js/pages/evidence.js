// Admin → Evidence: how Tessera weighs evidence, measured on this organization's decisions;
// learned weights to release; and auto-accept, which may be switched on only where the
// measured precision allows. Curators can look; admins can change.

import { get, post } from "../core/api.js";
import { h, icon } from "../core/dom.js";
import { date, number, percent, relative } from "../core/format.js";
import { can } from "../core/session.js";
import { confirmAction } from "../components/drawer.js";
import { toast } from "../components/toast.js";
import { callout, card, pageHeader, pill } from "../components/ui.js";
import { show } from "./page.js";

const FEATURES = {
  orcid_match: "ORCID iD matches", orcid_conflict: "A different ORCID iD", orcid_rejected: "An iD marked not theirs",
  orcid_work: "On their ORCID record", orcid_consistent: "Same iD on their other papers", orcid_consistent_one: "Same iD on one other paper",
  orcid_inconsistent: "A different iD than their other papers", email_match: "Email address", org_affiliation: "Organization in the affiliation",
  grant_link: "Their grant", coauthor_overlap: "Shared coauthors", given_full_match: "Full first name", short_given_name: "Short first name",
  middle_conflict: "Middle initial differs", given_conflict: "First name differs", topic_fit: "Topics", journal_history: "Journals",
  career_window: "Outside their career", name_ambiguity: "Common name", rejected_coauthors: "Coauthors of rejected papers",
  slot_conflict: "Someone else fits better",
};

export function evidence(main, ctx = {}) {
  const reload = () => evidence(main, { samePage: true });
  const admin = can("admin");
  return show(main, {
    title: "Evidence",
    quiet: ctx.samePage,
    load: async () => (await get("/api/v1/evidence", null, { fresh: true })).data,
    view: (data) => {
      const { model, evaluation: report, auto_accept: auto, batches } = data;
      const act = (label, path, body, done, tone = "btn-ghost") =>
        h("button", { type: "button", class: `btn btn-sm ${tone}`, onclick: async (event) => {
          event.currentTarget.disabled = true;
          try {
            await post(path, body);
          } catch (failure) {
            toast(failure.detail || "That did not work.");
            reload();
            return;
          }
          reload();
          toast(done);
        } }, label);

      const changed = Object.entries(model.multipliers ?? {}).filter(([, m]) => Math.abs(m - 1) >= 0.05);
      const modelCard = card({
        title: "Active model",
        action: admin && !model.hand_set ? act("Use hand-set weights", "/api/v1/evidence/revert", {}, "Back to the hand-set weights from the next sync.") : null,
        pad: true,
        body: h("div", { class: "stack" },
          h("div", { class: "row-sm" }, h("strong", { class: "mono" }, model.method),
            model.hand_set ? pill("Hand-set weights", "") : pill("Learned from your decisions", "ok")),
          model.hand_set
            ? h("p", { class: "muted" }, "Every suggestion adds up evidence with hand-set weights. Once curators have made enough decisions, Tessera can learn how much each kind of evidence counts here.")
            : h("p", { class: "muted" }, `Fitted ${relative(model.fitted_at)} to ${number(model.trained_on?.examples ?? 0)} decisions about ${number(model.trained_on?.people ?? 0)} people.`),
          changed.length ? h("dl", { class: "kv" }, changed.flatMap(([f, m]) => [h("dt", {}, FEATURES[f] ?? f), h("dd", {}, `${m.toFixed(2)} × the hand-set weight`)])) : null),
      });

      const evaluationCard = card({
        title: "Evaluation",
        action: admin ? act("Evaluate now", "/api/v1/evidence/evaluate", {}, "Evaluated on held-out people.") : null,
        pad: true,
        body: report ? evaluationBody(report, data.decisions, admin, act) : h("p", { class: "muted" },
          `${number(data.decisions)} decisions so far. An evaluation fits learned weights to them and compares both models on people left out of the fit.`),
      });

      const measures = !report ? null : model.hand_set ? report.base
        : report.candidate?.method === model.method ? report.fitted : null;
      const autoCard = card({
        title: "Auto-accept",
        action: admin && auto.enabled ? act("Switch off", "/api/v1/evidence/auto-accept", { enabled: false }, "Auto-accept is off.", "btn-reject") : null,
        pad: true,
        body: h("div", { class: "stack" },
          auto.enabled
            ? callout(`On: each sync accepts open suggestions scored at least ${auto.threshold} with no conflicting evidence, where the evaluated precision is ${percent(auto.precision, 1)} over ${number(auto.support)} decisions.`)
            : h("p", { class: "muted" }, `Off${auto.disabled_because ? ` (${auto.disabled_because})` : ""}. It can be switched on only at a score where the evaluation measured at least ${percent(auto.precision_bar)} precision over at least ${auto.min_support} decisions. Automatic acceptances are marked, never used for learning, and undone together.`),
          measures ? curveTable(measures, auto, admin, act) : h("p", { class: "subtle" }, "Evaluate the active model to see where auto-accept would be safe.")),
      });

      const batchCard = batches.length ? card({ title: "Automatic batches", body: h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Accepted"), h("th", {}, "At"), h("th", {}, "Status"), h("th", { class: "actions" }, h("span", { class: "sr-only" }, "Actions")))),
        h("tbody", {}, batches.map((b) => h("tr", {},
          h("td", { class: "nowrap", title: date(b.created_at) }, relative(b.created_at)),
          h("td", {}, number(b.accepted)),
          h("td", { class: "muted" }, `≥ ${b.threshold} · ${percent(b.precision, 1)}`),
          h("td", {}, b.undone_at ? h("span", { class: "muted" }, `Undone by ${b.undone_by}`) : pill("Accepted", "ok")),
          h("td", { class: "actions" }, admin && !b.undone_at ? h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => confirmAction({
            title: "Undo this batch",
            text: h("p", {}, `Return its ${number(b.accepted)} links to suggestions (any a person has decided since stay as they are). Auto-accept switches off, so that the next sync does not accept them again.`),
            confirmLabel: "Undo batch",
            tone: "reject",
            run: async () => {
              try {
                await post(`/api/v1/evidence/auto-accept/batches/${b.id}/undo`, {});
              } catch (failure) {
                toast(failure.detail || "That did not work.");
              }
              reload();
            },
          }) }, icon("undo", "icon icon-sm"), "Undo") : null))))) ) }) : null;

      return h("div", { class: "stack-lg" },
        pageHeader({ eyebrow: "Admin", title: "Evidence", meta: h("span", {}, "How suggestions are scored, measured on your own decisions.") }),
        modelCard, evaluationCard, autoCard, batchCard);
    },
  });
}

function evaluationBody(report, decisions, admin, act) {
  const d = report.decisions;
  const row = (label, pick) => h("tr", {}, h("th", { scope: "row" }, label),
    h("td", {}, report.base ? pick(report.base) : "–"), h("td", {}, report.fitted ? pick(report.fitted) : "–"));
  const pct = (v) => (v == null ? "–" : percent(v, 1));
  return h("div", { class: "stack" },
    h("p", { class: "muted" }, `Evaluated ${relative(report.generated_at)} on ${number(d.examples)} decisions (${number(d.negatives)} rejections) about ${number(d.people)} people.${decisions !== d.examples ? ` There are ${number(decisions)} now.` : ""}`),
    report.base ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "On your decisions"), h("th", {}, "Hand-set"), h("th", {}, "Learned, on people left out of each fit"))),
      h("tbody", {},
        row("Strong band: precision", (m) => pct(m.strong.precision)),
        row("Strong band: recall", (m) => pct(m.strong.recall)),
        row("Review threshold: precision", (m) => pct(m.review.precision)),
        row("Review threshold: recall", (m) => pct(m.review.recall)),
        row("Log loss (lower is better)", (m) => m.log_loss.toFixed(4))))) : null,
    report.better
      ? h("div", { class: "callout", role: "note" }, icon("check", "icon icon-sm"),
        h("div", { class: "grow" }, "The learned model predicts decisions better on people it was not fitted to."),
        admin ? act("Release it", "/api/v1/evidence/release", {}, "Released. Scores change at the next sync; auto-accept is off.", "btn-primary") : null)
      : h("ul", { class: "plain-list" }, report.reasons.map((r) => h("li", { class: "muted" }, r))));
}

function curveTable(measures, auto, admin, act) {
  return h("div", { class: "table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Score at least"), h("th", {}, "Precision"), h("th", {}, "Decisions"), h("th", {}, "Recall"),
      h("th", { class: "actions" }, h("span", { class: "sr-only" }, "Actions")))),
    h("tbody", {}, measures.curve.map((p) => {
      const safe = p.precision != null && p.precision >= auto.precision_bar && p.support >= auto.min_support;
      const current = auto.enabled && Math.abs(auto.threshold - p.threshold) < 1e-9;
      return h("tr", {},
        h("td", { class: "mono" }, String(p.threshold)),
        h("td", {}, p.precision == null ? "–" : percent(p.precision, 1)),
        h("td", {}, number(p.support)),
        h("td", { class: "muted" }, p.recall == null ? "–" : percent(p.recall, 1)),
        h("td", { class: "actions" }, current ? pill("On", "ok")
          : admin && safe ? act("Switch on here", "/api/v1/evidence/auto-accept", { enabled: true, threshold: p.threshold }, `Auto-accept is on at ${p.threshold}; it runs at the next sync.`, "btn-accept")
          : safe ? null : h("span", { class: "subtle" }, p.support < auto.min_support ? "too few" : "below the bar")));
    }))));
}
