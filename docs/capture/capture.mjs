#!/usr/bin/env node
// Takes the documentation's screenshots from the real app, serving the demo organization.
//
//   node docs/capture/capture.mjs                    every scene, light and dark
//   node docs/capture/capture.mjs profile evidence   only these scenes
//   node docs/capture/capture.mjs --theme dark       only one theme
//   node docs/capture/capture.mjs --demo PATH        reuse a demo built with `tessera demo PATH`
//
// It builds the demo in a temporary directory (unless --demo is given), serves it on a free
// port, opens each scene in headless Chrome, and saves docs/images/<scene>-<theme>.webp.
// Motion is reduced, so graphs are shown settled. Needs Node.js 22, uv, and Chrome.

import { execFileSync, spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { launch } from "./cdp.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");
const IMAGES = join(REPO, "docs", "images");

const WIDE = { width: 1440, height: 900 };
const ready = "document.querySelector('main .page') && !document.querySelector('.skeleton')";

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
  { name: "profile", url: "/people/{person}", ...WIDE },
  { name: "evidence", url: "/people/{suggested}", ...WIDE, step: "document.querySelector('button.mark-suggested, button.mark').click()", wait: "document.querySelector('.drawer')" },
  { name: "review", url: "/review", ...WIDE, wait: "document.querySelector('.review-page')" },
  { name: "experts", url: "/experts?q=memory%20T%20cells", ...WIDE, wait: "document.querySelector('.expert')" },
  { name: "network", scroll: ".tabs", url: "/graph", ...WIDE, wait: "document.querySelector('.graph-canvas')", settle: 1500 },
  { name: "focus", scroll: ".tabs", url: "/graph?view=focus&person={person}", ...WIDE, wait: "document.querySelector('.graph-canvas')", settle: 1500 },
  { name: "landscape", scroll: ".tabs", url: "/graph?view=landscape", ...WIDE, wait: "document.querySelector('.graph-canvas')", settle: 1500 },
  { name: "missed", scroll: ".tabs", url: "/graph?view=missed&min=0.15", ...WIDE, wait: "document.querySelector('.missed')" },
  { name: "units", scroll: ".tabs", url: "/graph?view=units", ...WIDE, step: STRONGEST_CELL, wait: "document.querySelector('.graph-side h2') && !document.querySelector('.graph-side').textContent.includes('Loading')" },
  { name: "duplicates", url: "/review/duplicates", ...WIDE },
  { name: "discovered", url: "/review/people", ...WIDE },
  { name: "evidence-model", url: "/admin/evidence", ...WIDE },
  { name: "sources", url: "/admin/sources", ...WIDE },
  { name: "palette", url: "/", ...WIDE, step: "document.dispatchEvent(new KeyboardEvent('keydown', { key: '/', bubbles: true }))", wait: "document.querySelector('.palette input')", type: "grant", keepFocus: true },
  { name: "mobile", url: "/people/{person}", width: 390, height: 844, mobile: true, scale: 2 },
];

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
 * and studies), and {suggested}, the person with the most open suggestions. */
async function people(base) {
  const body = await (await fetch(`${base}/api/v1/people?limit=200`)).json();
  const best = (rank) => [...body.data].sort((a, b) => rank(b) - rank(a))[0]?.id;
  return {
    person: best((p) => (p.counts.accepted || 0) + 5 * (p.counts.grants || 0) + 20 * (p.counts.studies || 0)),
    suggested: best((p) => p.counts.suggested || 0),
  };
}

async function main() {
  const args = process.argv.slice(2);
  const option = (name) => { const i = args.indexOf(name); if (i < 0) return null; const v = args[i + 1]; args.splice(i, 2); return v; };
  const theme = option("--theme");
  const themeArg = theme ? [theme] : null;
  const demoArg = option("--demo");
  const wanted = new Set(args);
  const scenes = SCENES.filter((s) => !wanted.size || wanted.has(s.name));
  const unknown = [...wanted].filter((w) => !SCENES.some((s) => s.name === w));
  if (unknown.length) throw new Error(`No such scene: ${unknown.join(", ")}`);

  const work = demoArg ? null : mkdtempSync(join(tmpdir(), "tessera-demo-"));
  const demo = demoArg ? resolve(demoArg) : join(work, "demo");
  if (!demoArg) {
    console.log("Building the demo organization …");
    execFileSync("uv", ["run", "--project", REPO, "tessera", "demo", demo, "--force"], { stdio: "inherit", cwd: REPO });
  }
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const server = spawn("uv", ["run", "--project", REPO, "tessera", "--config", join(demo, "tessera.toml"), "serve", "--port", String(port)],
    { cwd: REPO, stdio: "ignore" });
  const browser = await launch();
  try {
    await waitForServer(base);
    const chosen = await people(base);
    mkdirSync(IMAGES, { recursive: true });
    for (const scene of scenes) {
      for (const theme of themeArg || ["light", "dark"]) {
        const page = await browser.page();
        await page.viewport({ width: scene.width, height: scene.height, scale: scene.scale ?? 1.5, mobile: Boolean(scene.mobile) });
        await page.media({ "prefers-color-scheme": theme, "prefers-reduced-motion": "reduce" });
        await page.goto(base + scene.url.replace(/\{(\w+)\}/g, (_, key) => chosen[key]));
        await page.until(ready);
        if (scene.step) {
          await page.evaluate(scene.step);
        }
        if (scene.wait) await page.until(scene.wait);
        if (scene.type) {
          await page.evaluate(`(() => { const input = document.querySelector('.palette input'); input.value = ${JSON.stringify(scene.type)}; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
        }
        if (!scene.keepFocus) await page.evaluate("document.activeElement?.blur()");
        if (scene.scroll) {
          // Bring the part that matters to just below the sticky top bar.
          await page.evaluate(`(() => { const el = document.querySelector(${JSON.stringify(scene.scroll)}); window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 76); })()`);
        }
        await new Promise((r) => setTimeout(r, scene.settle ?? 600));
        const file = join(IMAGES, `${scene.name}-${theme}.webp`);
        writeFileSync(file, await page.screenshot({ quality: 80 }));
        await page.close();
        console.log(`  ${scene.name} (${theme})`);
      }
    }
  } finally {
    await browser.close();
    server.kill();
    if (work) rmSync(work, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
