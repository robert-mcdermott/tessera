#!/usr/bin/env node
// Takes the documentation's screenshots from the real app, serving the demo organization.
//
//   node docs/capture/capture.mjs                    every scene, light and dark
//   node docs/capture/capture.mjs profile evidence   only these scenes
//   node docs/capture/capture.mjs --theme dark       only one theme
//   node docs/capture/capture.mjs --demo PATH        reuse a demo built with `tessera demo PATH`
//   node docs/capture/capture.mjs social             only the social preview card (from home-light)
//   node docs/capture/capture.mjs --audit            also audit each scene for WCAG 2.2 AA (audit.mjs),
//                                                    and fail if anything is found; --audit-only
//                                                    audits without writing screenshots
//   node docs/capture/capture.mjs --ai MODEL         also the assistant's scenes, with MODEL at an
//                                                    OpenAI-compatible endpoint (--ai-url, Ollama's
//                                                    on this machine by default)
//
// It builds the demo in a temporary directory (unless --demo is given), serves it on a free
// port, opens each scene in headless Chrome, and saves docs/images/<scene>-<theme>.webp.
// Motion is reduced, so graphs are shown settled. Needs Node.js 22, uv, and Chrome.

import { execFileSync, spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { audit } from "./audit.mjs";
import { launch } from "./cdp.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");
const IMAGES = join(REPO, "docs", "images");

const WIDE = { width: 1440, height: 900 };
const ready = "document.querySelector('main .page') && !document.querySelector('.skeleton')";
// Insight pages: every figure has loaded.
const FIGURES = "document.querySelector('.figure-card') && !document.querySelector('.figure-card[aria-busy]')";

// The unit pair whose interests are most alike (the strongest shade off the diagonal).
const STRONGEST_CELL = `(() => {
  const shade = (c) => Number(c.querySelector('.cell-interest')?.getAttribute('fill-opacity') || 0);
  const cells = [...document.querySelectorAll('.unit-matrix .cell')].filter((c) => c.dataset.i !== c.dataset.j);
  cells.sort((a, b) => shade(b) - shade(a))[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
})()`;

// One line each: the page, its size, and an optional step before the picture is taken.
export const SCENES = [
  { name: "home", url: "/", ...WIDE },
  { name: "people", url: "/people", ...WIDE },
  { name: "profile", url: "/people/{person}?tab=publications", ...WIDE },
  { name: "about", url: "/people/{person}", ...WIDE, scroll: "[data-profile-section=overview]" },
  { name: "evidence", url: "/people/{suggested}?tab=publications", ...WIDE, step: "document.querySelector('button.mark-suggested, button.mark').click()", wait: "document.querySelector('.drawer')" },
  { name: "review", url: "/review", ...WIDE, wait: "document.querySelector('.review-page')" },
  { name: "experts", url: "/experts?q=memory%20T%20cells", ...WIDE, wait: "document.querySelector('.expert')" },
  { name: "network", scroll: ".tabs", url: "/graph", ...WIDE, wait: "document.querySelector('.graph-canvas')", settle: 1500 },
  { name: "focus", scroll: ".tabs", url: "/graph?view=focus&person={person}", ...WIDE, wait: "document.querySelector('.graph-canvas')", settle: 1500 },
  { name: "landscape", scroll: ".tabs", url: "/graph?view=landscape", ...WIDE, wait: "document.querySelector('.graph-canvas')", settle: 1500 },
  { name: "missed", scroll: ".tabs", url: "/graph?view=missed&min=0.15", ...WIDE, wait: "document.querySelector('.missed')" },
  { name: "units", scroll: ".tabs", url: "/graph?view=units", ...WIDE, step: STRONGEST_CELL, wait: "document.querySelector('.graph-side h2') && !document.querySelector('.graph-side').textContent.includes('Loading')" },
  { name: "groups", url: "/groups", ...WIDE },
  { name: "group", url: "/groups/cancer-immunotherapy-program", ...WIDE },
  { name: "group-themes", scroll: ".theme-list", url: "/groups/cancer-immunotherapy-program", ...WIDE },
  { name: "group-editor", url: "/groups/cancer-immunotherapy-program/edit", ...WIDE, wait: "document.querySelector('.block-editor')" },
  { name: "inbox", url: "/inbox?group=cancer-immunotherapy-program", ...WIDE, wait: "document.querySelector('.inbox-groups')" },
  { name: "admin-groups", url: "/admin/groups/cancer-immunotherapy-program", ...WIDE },
  { name: "public-group", file: "groups/cancer-immunotherapy-program/index.html", ...WIDE, ready: "document.querySelector('main h1')" },
  { name: "public-home", file: "index.html", ...WIDE, ready: "document.querySelector('main h1')" },
  { name: "insight", url: "/insight", ...WIDE, wait: FIGURES },
  { name: "insight-funding", url: "/insight/funding", ...WIDE, wait: FIGURES },
  { name: "insight-topics", url: "/insight/topics?level=area", ...WIDE, wait: FIGURES },
  { name: "insight-studies", url: "/insight/studies", ...WIDE, wait: FIGURES },
  { name: "insight-about", url: "/insight?cohort=group:cancer-immunotherapy-program", ...WIDE, wait: FIGURES,
    step: `(async () => { await new Promise((r) => { const t = setInterval(() => { if (document.querySelector('.figure-card button[aria-label^="About"]')) { clearInterval(t); r(); } }, 50); }); document.querySelector('.figure-card button[aria-label^="About"]').click(); })()`,
    then: "document.querySelector('.drawer .about-section')", after: "document.querySelector('.drawer-body').scrollTop = 0" },
  { name: "insight-snapshot", url: "/insight/snapshots/{snapshot}", ...WIDE, wait: FIGURES },
  { name: "saved-search", url: "/publications?q=lymphoma&saved={saved}", ...WIDE, wait: "document.querySelector('.saved-panel .saved-lead')" },
  { name: "operations", url: "/admin/operations", ...WIDE },
  { name: "branding", url: "/admin/branding", ...WIDE },
  { name: "embed", file: "groups/cancer-immunotherapy-program/embed/publications.html", width: 760, height: 560, ready: "document.querySelector('main h1')" },
  { name: "duplicates", url: "/review/duplicates", ...WIDE },
  { name: "discovered", url: "/review/people", ...WIDE },
  { name: "evidence-model", url: "/admin/evidence", ...WIDE },
  { name: "sources", url: "/admin/sources", ...WIDE },
  { name: "palette", url: "/", ...WIDE, step: "document.dispatchEvent(new KeyboardEvent('keydown', { key: '/', bubbles: true }))", wait: "document.querySelector('.palette input')", type: "grant", keepFocus: true },
  { name: "thread", url: "/grants/R01CA990016", ...WIDE, wait: "document.querySelector('.thread-column')", scroll: ".thread" },
  { name: "insight-impact", url: "/insight/impact", ...WIDE, wait: FIGURES },
  { name: "opportunities", url: "/opportunities?status=posted&status=forecasted", ...WIDE },
  { name: "opportunity", url: "/opportunities/9900101", ...WIDE, wait: "document.querySelector('.fit-heading')" },
  { name: "team", url: "/teams/{team}", ...WIDE },
  { name: "mobile", url: "/people/{person}", width: 390, height: 844, mobile: true, scale: 2 },
  { name: "profile-edit", url: "/people/{person}", ...WIDE, step: "document.querySelector('[data-profile-section=education] button[aria-label^=\"Change\"]').click()", wait: "document.querySelector('#profile-entry-form input')" },
  { name: "public-choices", url: "/people/{person}", ...WIDE, step: "document.querySelector('.profile-public').open = true", scroll: ".profile-public" },
  { name: "public-profile", file: "people/{slug}/index.html", ...WIDE, ready: "document.querySelector('main h1')" },
  { name: "cv", url: "/people/{lab}/cvs/{cv}", ...WIDE, wait: "document.querySelector('.cv-paper .cv-section')" },
  { name: "biosketch", url: "/people/{lab}/cvs/{biosketch}", ...WIDE, wait: "document.querySelector('.bio-checks') && document.querySelector('.cv-paper .cv-section')" },
  // With --ai only: the assistant answers live, so these differ a little from run to run.
  { name: "ask", ai: true, url: "/", ...WIDE, settle: 1200,
    step: `(async () => { document.querySelector('.ask-open').click(); await new Promise((r) => { const t = setInterval(() => { if (document.querySelector('.ask-input')) { clearInterval(t); r(); } }, 50); });
      const input = document.querySelector('.ask-input'); input.value = 'Who works on CAR-T cell therapy?';
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()`,
    wait: "document.querySelector('.ask-sources')", after: "document.querySelector('.drawer-body').scrollTop = 0" },
  { name: "summary", ai: true, url: "/people/{person}", ...WIDE, wait: "document.querySelector('.lay-text')", setup: approvedSummary },
  { name: "assistant-log", ai: true, url: "/admin/assistant", ...WIDE, wait: "document.querySelector('.table')" },
  { name: "biosketch-draft", ai: true, url: "/people/{lab}/cvs/{biosketch}", ...WIDE, settle: 1200,
    step: `(async () => { const until = (q) => new Promise((r) => { const t = setInterval(() => { const el = document.querySelector(q); if (el) { clearInterval(t); r(el); } }, 50); });
      [...document.querySelectorAll('button')].find((b) => b.textContent.includes('Draft with the assistant')).click();
      await until('.drawer .cv-ed-entries input');
      [...document.querySelectorAll('.drawer .cv-ed-entries input')].slice(0, 3).forEach((box) => box.click());
      [...document.querySelectorAll('.drawer button')].find((b) => b.textContent.trim() === 'Draft').click(); })()`,
    wait: "document.querySelector('.drawer .lay-draft')" },
];

/** For the summary scene: a summary of {person}'s work, drafted and approved. */
async function approvedSummary(base, chosen) {
  const draft = await (await fetch(`${base}/api/v1/ai/summaries/${chosen.person}/draft`, { method: "POST" })).json();
  if (!draft.data) throw new Error(`Could not draft a summary: ${JSON.stringify(draft)}`);
  await fetch(`${base}/api/v1/ai/summaries/${draft.data.id}/approve`, { method: "POST",
    headers: { "Content-Type": "application/json" }, body: "{}" });
}

function freePort() {
  return new Promise((ok) => {
    const server = createServer().listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => ok(port));
    });
  });
}

async function waitForServer(base, timeout = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try {
      if ((await fetch(`${base}/api/v1/health`)).ok) return;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("Tessera did not start");
}

/** People to show: {person}, the most connected curated person (confirmed papers, grants,
 * and studies), {suggested}, the person with the most open suggestions, and {lab}, the lab head
 * the demo began a CV ({cv}) and a biosketch ({biosketch}) for. */
async function people(base) {
  const body = await (await fetch(`${base}/api/v1/people?limit=200`)).json();
  const best = (rank) => [...body.data].sort((a, b) => rank(b) - rank(a))[0];
  const snapshots = await (await fetch(`${base}/api/v1/insight/snapshots`)).json();
  const saved = await (await fetch(`${base}/api/v1/saved-searches`)).json();
  const teams = await (await fetch(`${base}/api/v1/teams`)).json();
  const person = best((p) => (p.counts.accepted || 0) + 5 * (p.counts.grants || 0) + 20 * (p.counts.studies || 0));
  const chosen = {
    team: teams.data[0]?.id,
    person: person?.id,
    slug: slugify(person?.name ?? ""),
    suggested: best((p) => p.counts.suggested || 0)?.id,
    snapshot: snapshots.data[0]?.id,
    saved: saved.data[0]?.id,
  };
  for (const p of [...body.data].sort((a, b) => (b.counts.accepted || 0) - (a.counts.accepted || 0))) {
    const docs = (await (await fetch(`${base}/api/v1/people/${p.id}/cvs`)).json()).data ?? [];
    if (!docs.length) continue;
    chosen.lab = p.id;
    chosen.cv = docs.find((d) => d.kind === "cv")?.id;
    chosen.biosketch = docs.find((d) => d.kind === "nih_biosketch")?.id;
    break;
  }
  return chosen;
}

/** As the public site names a person's page (publish/projection.py slugify). */
function slugify(text) {
  const plain = text.normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase();
  return plain.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80).replace(/-+$/, "") || "page";
}

/** The card shown when the site is linked: docs/capture/social.html, 1200 x 630. */
async function social(browser) {
  const page = await browser.page();
  await page.viewport({ width: 1200, height: 630, scale: 1 });
  await page.media({ "prefers-color-scheme": "light" });
  await page.goto(`file://${join(HERE, "social.html")}`);
  await page.until("[...document.images].every((i) => i.complete && i.naturalWidth > 0)");
  writeFileSync(join(IMAGES, "social-preview.png"), await page.screenshot({ format: "png" }));
  await page.close();
  console.log("  social preview");
}

async function main() {
  const args = process.argv.slice(2);
  const option = (name) => { const i = args.indexOf(name); if (i < 0) return null; const v = args[i + 1]; args.splice(i, 2); return v; };
  const flag = (name) => { const i = args.indexOf(name); if (i < 0) return false; args.splice(i, 1); return true; };
  const auditOnly = flag("--audit-only");
  const auditing = flag("--audit") || auditOnly;
  const findings = [];
  const theme = option("--theme");
  const themeArg = theme ? [theme] : null;
  const demoArg = option("--demo");
  const aiModel = option("--ai");
  const aiUrl = option("--ai-url") ?? "http://127.0.0.1:11434/v1";
  const wanted = new Set(args);
  const scenes = SCENES.filter((s) => (!wanted.size && (aiModel || !s.ai)) || wanted.has(s.name));
  if (!aiModel && scenes.some((s) => s.ai)) throw new Error("The assistant's scenes need --ai MODEL.");
  const withSocial = !wanted.size || wanted.has("social");
  wanted.delete("social");
  if (!scenes.length && withSocial) {
    const browser = await launch();
    try { await social(browser); } finally { await browser.close(); }
    return;
  }
  const unknown = [...wanted].filter((w) => !SCENES.some((s) => s.name === w));
  if (unknown.length) throw new Error(`No such scene: ${unknown.join(", ")}`);

  const work = demoArg ? null : mkdtempSync(join(tmpdir(), "tessera-demo-"));
  const demo = demoArg ? resolve(demoArg) : join(work, "demo");
  if (!demoArg) {
    console.log("Building the demo organization …");
    execFileSync("uv", ["run", "--project", REPO, "tessera", "demo", demo, "--force"], { stdio: "inherit", cwd: REPO });
  }
  // Two passes: everything else with the assistant off, as an installation starts, and then the
  // assistant's own scenes with it on.
  const passes = [scenes.filter((s) => !s.ai), scenes.filter((s) => s.ai)];
  const browser = await launch();
  try {
    mkdirSync(IMAGES, { recursive: true });
    for (const [n, list] of passes.entries()) {
      if (!list.length) continue;
      if (n === 1) {
        const path = join(demo, "tessera.toml");
        const text = readFileSync(path, "utf-8");
        if (!/^\[ai\]/m.test(text)) {
          writeFileSync(path, `${text}\n[ai]\nenabled = true\nprovider = "openai"\nbase_url = ${JSON.stringify(aiUrl)}\nmodel = ${JSON.stringify(aiModel)}\ndaily_limit = 0\n`);
        }
      }
      const port = await freePort();
      const base = `http://127.0.0.1:${port}`;
      const server = spawn("uv", ["run", "--project", REPO, "tessera", "--config", join(demo, "tessera.toml"), "serve", "--port", String(port)],
        { cwd: REPO, stdio: "ignore" });
      try {
        await waitForServer(base);
        const chosen = await people(base);
        await capture(browser, list, { base, chosen, demo, themeArg, auditing, auditOnly, findings });
      } finally {
        server.kill();
      }
    }
    if (withSocial && !auditOnly) await social(browser);
  } finally {
    await browser.close();
    if (work) rmSync(work, { recursive: true, force: true });
  }
  if (auditing) report(findings);
}

/** The audit's findings, grouped by rule; exits non-zero when there are any. */
function report(findings) {
  const out = join(tmpdir(), "tessera-audit.json");
  writeFileSync(out, JSON.stringify(findings, null, 1));
  if (!findings.length) {
    console.log("Audit: nothing found (WCAG 2.2 AA, automated checks).");
    return;
  }
  const byRule = new Map();
  for (const f of findings) byRule.set(f.rule, [...(byRule.get(f.rule) ?? []), f]);
  for (const [rule, list] of byRule) {
    console.log(`\n${rule}: ${list.length}`);
    for (const f of list.slice(0, 60)) console.log(`  ${f.scene} (${f.theme})  ${f.where}  ${f.detail}`);
  }
  console.log(`\nAudit: ${findings.length} findings; all of them in ${out}`);
  process.exitCode = 1;
}

async function capture(browser, scenes, { base, chosen, demo, themeArg, auditing = false, auditOnly = false, findings = [] }) {
  for (const scene of scenes) {
    if (scene.setup) await scene.setup(base, chosen);
    for (const theme of themeArg || ["light", "dark"]) {
      const page = await browser.page();
      await page.viewport({ width: scene.width, height: scene.height, scale: scene.scale ?? 1.5, mobile: Boolean(scene.mobile) });
      await page.media({ "prefers-color-scheme": theme, "prefers-reduced-motion": "reduce" });
      // The public site is files the demo published (var/public); the rest is the app.
      const fill = (text) => text.replace(/\{(\w+)\}/g, (_, key) => chosen[key]);
      await page.goto(scene.file ? `file://${join(demo, "var", "public", fill(scene.file))}` : base + fill(scene.url));
      await page.until(scene.ready ?? ready);
      if (scene.step) {
        await page.evaluate(scene.step);
      }
      if (scene.wait) await page.until(scene.wait);
      if (scene.then) await page.until(scene.then);
      if (scene.after) await page.evaluate(scene.after);
      if (scene.type) {
        await page.evaluate(`(() => { const input = document.querySelector('.palette input'); input.value = ${JSON.stringify(scene.type)}; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
      }
      if (!scene.keepFocus) await page.evaluate("document.activeElement?.blur()");
      if (scene.scroll) {
        // Bring the part that matters to just below the sticky top bar.
        await page.evaluate(`(() => { const el = document.querySelector(${JSON.stringify(scene.scroll)}); window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 76); })()`);
      }
      await new Promise((r) => setTimeout(r, scene.settle ?? 600));
      if (!auditOnly) {
        const file = join(IMAGES, `${scene.name}-${theme}.webp`);
        writeFileSync(file, await page.screenshot({ quality: 80 }));
      }
      let note = "";
      if (auditing && !scene.noAudit) {
        const found = await audit(page);
        findings.push(...found.map((f) => ({ scene: scene.name, theme, ...f })));
        note = found.length ? `  ${found.length} audit findings` : "";
      }
      await page.close();
      console.log(`  ${scene.name} (${theme})${note}`);
    }
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
