#!/usr/bin/env node
// Builds the documentation site from the guides in docs/, the changelog, and the OpenAPI
// document. Needs Node.js 22 and nothing else.
//
//   node docs/site/build.mjs                 build into ./site
//   node docs/site/build.mjs --out DIR       build somewhere else
//   node docs/site/build.mjs --publish DIR   build, then copy into DIR (a gh-pages worktree)
//
// The guides stay the source: links between them are written as GitHub reads them
// (curation.md#bulk-decisions) and rewritten here. The build checks every local link,
// anchor, and image, and fails on a broken one.

import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");
const DOCS = join(REPO, "docs");
const STATIC = join(REPO, "src", "tessera", "web", "static");
const GITHUB = "https://github.com/robert-mcdermott/tessera";
const SITE = "https://robert-mcdermott.github.io/tessera/"; // where the site is published

// Reading order, in groups. Each guide becomes a page of the same name.
export const MENU = [
  ["Start", [["index", "Overview"], ["getting-started", "Getting started"], ["adopting", "Adopting Tessera"], ["organization-profile", "Describing your organization"]]],
  ["Use", [["curation", "Curating"], ["discovery", "Finding experts"], ["groups", "Groups"], ["profiles", "Profiles"], ["insight", "Insight"], ["opportunities", "Opportunities"], ["public-site", "The public site"]]],
  ["Run", [["branding", "Look and words"], ["configuration", "Configuration"], ["operations", "Running Tessera"], ["assistant", "The assistant"]]],
  ["Reference", [["api", "The API"], ["api-reference", "API reference"], ["cli", "Command reference"], ["compatibility", "Compatibility"], ["changelog", "Changelog"]]],
];

// --- Markdown (the subset the guides use) ----------------------------------------------------

export function slug(text) {
  return text.trim().toLowerCase().replace(/`/g, "").replace(/[^\p{L}\p{N}_\- ]/gu, "").replace(/ /g, "-");
}

export function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** Inline Markdown to HTML: code, images, links, autolinks, bold, italics. */
export function inline(text, link = (href) => href) {
  const codes = [];
  let out = text.replace(/`([^`]+)`/g, (_, code) => `\u0000${codes.push(`<code>${escapeHtml(code)}</code>`) - 1}\u0000`);
  out = escapeHtml(out);
  out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;([^&]*)&quot;)?\)/g, (_, alt, src, title) =>
    `<img src="${link(src)}" alt="${alt}"${title ? ` title="${title}"` : ""} loading="lazy">`);
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
    const target = link(href.replace(/&amp;/g, "&"));
    const external = /^https?:/.test(target);
    return `<a href="${escapeHtml(target)}"${external ? ' rel="noopener"' : ""}>${label}</a>`;
  });
  out = out.replace(/&lt;(https?:\/\/[^&\s]+)&gt;/g, (_, url) => `<a href="${url}" rel="noopener">${url}</a>`);
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  out = out.replace(/(^|[\s(])_([^_\s][^_]*)_(?=[\s.,;:)]|$)/g, "$1<em>$2</em>");
  return out.replace(/\u0000(\d+)\u0000/g, (_, i) => codes[Number(i)]);
}

function splitRow(line) {
  const cells = [];
  let cell = "";
  let code = false;
  const body = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  for (const ch of body) {
    if (ch === "`") code = !code;
    if (ch === "|" && !code) { cells.push(cell.trim()); cell = ""; } else cell += ch;
  }
  cells.push(cell.trim());
  return cells;
}

/** Block Markdown to HTML. Returns { html, headings: [{level, text, id}] }. */
export function markdown(source, { link = (href) => href, figure = null } = {}) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const headings = [];
  const used = new Map();
  const out = [];
  let i = 0;
  const isBlockStart = (line) => /^(#{1,6}\s|```|>|\s*([-*]|\d+\.)\s|\|)/.test(line) || /^(-{3,}|\*{3,})$/.test(line.trim());
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m;
    if ((m = line.match(/^```(\w*)\s*$/))) {
      const body = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) body.push(lines[i++]);
      i++;
      const lang = m[1] || "text";
      out.push(`<div class="code"><pre><code class="language-${lang}">${escapeHtml(body.join("\n"))}</code></pre></div>`);
      continue;
    }
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      const level = m[1].length;
      const text = m[2].trim();
      let id = slug(text);
      if (used.has(id)) { used.set(id, used.get(id) + 1); id = `${id}-${used.get(id) - 1}`; } else used.set(id, 1);
      headings.push({ level, text: text.replace(/`/g, ""), id });
      const anchor = level > 1 ? `<a class="anchor" href="#${id}" aria-label="Link to this section">#</a>` : "";
      out.push(`<h${level} id="${id}">${inline(text, link)}${anchor}</h${level}>`);
      i++;
      continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) { out.push("<hr>"); i++; continue; }
    if (line.startsWith("|") && i + 1 < lines.length && /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(splitRow(lines[i++]));
      out.push(`<div class="table-wrap"><table><thead><tr>${head.map((c) => `<th>${inline(c, link)}</th>`).join("")}</tr></thead>`
        + `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c, link)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    if (line.startsWith(">")) {
      const body = [];
      while (i < lines.length && lines[i].startsWith(">")) body.push(lines[i++].replace(/^>\s?/, ""));
      out.push(`<blockquote>${markdown(body.join("\n"), { link }).html}</blockquote>`);
      continue;
    }
    if ((m = line.match(/^(\s*)([-*]|\d+\.)\s+/))) {
      const ordered = /\d/.test(m[2]);
      const start = ordered ? parseInt(m[2], 10) : 1;
      const items = [];
      while (i < lines.length) {
        const item = lines[i].match(/^\s*([-*]|\d+\.)\s+(.*)$/);
        if (item && /\d/.test(item[1]) === ordered) {
          items.push([item[2]]);
          i++;
        } else if (lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && items.length) {
          items[items.length - 1].push(lines[i++].trim());
        } else if (!lines[i].trim() && i + 1 < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i + 1])) {
          i++;
        } else break;
      }
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}${ordered && start !== 1 ? ` start="${start}"` : ""}>${items.map((p) => `<li>${inline(p.join(" "), link)}</li>`).join("")}</${tag}>`);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !(para.length && isBlockStart(lines[i]))) para.push(lines[i++]);
    const text = para.join("\n");
    const image = text.match(/^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)$/);
    if (image && figure) out.push(figure(image[1], image[2], image[3]));
    else out.push(`<p>${inline(text, link)}</p>`);
  }
  return { html: out.join("\n"), headings };
}

// --- Pages -----------------------------------------------------------------------------------

function version() {
  const match = readFileSync(join(REPO, "pyproject.toml"), "utf8").match(/^version = "([^"]+)"/m);
  return match ? match[1] : "dev";
}

/** Where a link in a guide (or the README, or the changelog) points on the site. */
function linker(fromDir) {
  return (href) => {
    if (/^[a-z]+:/.test(href) || href.startsWith("#")) return href;
    const [path, anchor] = href.split("#");
    const target = relative(REPO, resolve(fromDir, path)).replaceAll("\\", "/");
    const hash = anchor ? `#${anchor}` : "";
    if (target === "docs/README.md" || target === "README.md") return `index.html${hash}`;
    if (target === "CHANGELOG.md") return `changelog.html${hash}`;
    if (target.startsWith("docs/images/")) return target.slice("docs/".length);
    if (target.startsWith("docs/") && target.endsWith(".md")) return `${target.slice(5, -3)}.html${hash}`;
    return `${GITHUB}/blob/main/${target}${hash}`;
  };
}

/** {{shot:name|alt}} in the home page: a picture from docs/images, following the theme. */
function shots(html) {
  return html.replace(/\{\{shot:([\w-]+)\|([^}]*)\}\}/g, (_, name, alt) => {
    const ext = ["webp", "svg", "png"].find((e) => existsSync(join(DOCS, "images", `${name}-light.${e}`)));
    if (!ext) throw new Error(`No image docs/images/${name}-light.(webp|svg|png) for the home page`);
    return figure(alt, `images/${name}-light.${ext}`, "", "shot");
  });
}

/** A paragraph that is only an image becomes a figure; light and dark versions follow the theme. */
function figure(alt, src, caption, cls = "") {
  const dark = src.replace(/-light\.(webp|png|svg)$/, "-dark.$1");
  const hasDark = dark !== src && existsSync(join(DOCS, dark));
  const img = (file, cls) => `<img src="${file}" alt="${escapeHtml(alt)}" loading="lazy"${cls ? ` class="${cls}"` : ""}>`;
  return `<figure${cls ? ` class="${cls}"` : ""}>${hasDark ? img(src, "only-light") + img(dark, "only-dark") : img(src, "")}`
    + `${caption ? `<figcaption>${escapeHtml(caption)}</figcaption>` : ""}</figure>`;
}

function shell({ name, title, body, headings, description, ver, prev, next }) {
  const menu = MENU.map(([group, items]) => `<div class="menu-group"><div class="menu-title">${group}</div><ul>${items
    .map(([page, label]) => `<li><a href="${page}.html"${page === name ? ' aria-current="page"' : ""}>${label}</a></li>`).join("")}</ul></div>`).join("");
  const toc = headings.filter((h) => h.level === 2 || h.level === 3);
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)}${name === "index" ? "" : " · Tessera"}</title>
<meta name="description" content="${escapeHtml(description)}">
<meta property="og:title" content="${escapeHtml(title)}">
<meta property="og:description" content="${escapeHtml(description)}">
<meta property="og:type" content="website">
<meta property="og:image" content="${SITE}images/social-preview.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" type="image/svg+xml" href="assets/tessera-mark.svg">
<link rel="stylesheet" href="assets/tokens.css">
<link rel="stylesheet" href="assets/site.css">
<script src="assets/theme.js"></script>
<script src="assets/site.js" defer></script>
</head>
<body class="page-${name}">
<a class="skip" href="#content">Skip to content</a>
<header class="top">
  <button class="menu-button" type="button" aria-label="Menu" aria-expanded="false" aria-controls="menu">☰</button>
  <a class="brand" href="index.html"><img src="assets/tessera-mark.svg" alt="" width="26" height="26"><span>Tessera</span><span class="version">v${ver}</span></a>
  <div class="search" role="search">
    <input id="search" type="search" placeholder="Search the docs" aria-label="Search the docs" autocomplete="off" spellcheck="false">
    <kbd>/</kbd>
    <div class="results" id="results" role="listbox" hidden></div>
  </div>
  <div class="theme" role="group" aria-label="Theme">
    <button type="button" data-theme-choice="light" title="Light">☀</button><button type="button" data-theme-choice="system" title="System">◐</button><button type="button" data-theme-choice="dark" title="Dark">☾</button>
  </div>
  <a class="github" href="${GITHUB}" rel="noopener">GitHub</a>
</header>
<div class="layout">
  <nav class="menu" id="menu" aria-label="Documentation">${menu}</nav>
  <main id="content" class="content">
${body}
    <nav class="pager" aria-label="Pages">${prev ? `<a class="prev" href="${prev[0]}.html"><span>Previous</span>${prev[1]}</a>` : "<span></span>"}${next ? `<a class="next" href="${next[0]}.html"><span>Next</span>${next[1]}</a>` : ""}</nav>
    <footer class="foot">Describes Tessera ${ver}. Apache License 2.0. Screenshots show an invented organization; every person and record in them is fictional.</footer>
  </main>
  ${toc.length > 2 ? `<aside class="toc" aria-label="On this page"><div class="menu-title">On this page</div><ul>${toc
    .map((h) => `<li class="l${h.level}"><a href="#${h.id}">${escapeHtml(h.text)}</a></li>`).join("")}</ul></aside>` : ""}
</div>
</body>
</html>
`;
}

/** The home page: a hand-written fragment (docs/site/pages/index.html) with screenshots. */
function home() {
  const html = shots(readFileSync(join(HERE, "pages", "index.html"), "utf8"));
  const headings = [...html.matchAll(/<h([1-3]) id="([^"]+)">(.*?)<\/h\1>/g)].map((m) => ({ level: Number(m[1]), id: m[2], text: plain(m[3]) }));
  return { html, headings };
}

function plain(html) {
  return html.replace(/<[^>]+>/g, " ").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
}

/** Search entries: one per section of every page. */
function sections(name, title, html) {
  const parts = html.replace(/<a class="anchor"[^>]*>#<\/a>/g, "").split(/(?=<h[23] id=")/);
  return parts.map((part) => {
    const head = part.match(/^<h[23] id="([^"]+)">(.*?)<\/h[23]>/);
    return {
      page: name, title, heading: head ? plain(head[2]) : title,
      url: `${name}.html${head ? `#${head[1]}` : ""}`, text: plain(head ? part.slice(head[0].length) : part).slice(0, 700),
    };
  }).filter((s) => s.text || s.heading !== title);
}

// --- The API reference, from the OpenAPI document -------------------------------------------

const TAGS = {
  system: "System", access: "Sessions and accounts", overview: "Search and overview", people: "People",
  discovery: "Discovery", records: "Publications, grants, and studies", curation: "Curation",
  evidence: "Evidence model", merges: "Duplicates and merges", candidates: "Discovered people",
  groups: "Groups",
};

function schemaType(schema, components) {
  if (!schema) return "";
  if (schema.$ref) return schema.$ref.split("/").pop();
  if (schema.anyOf) return schema.anyOf.map((s) => schemaType(s, components)).filter((t) => t !== "null").join(" or ");
  if (schema.type === "array") return `list of ${schemaType(schema.items, components) || "values"}`;
  return schema.type || "";
}

function apiReference(spec) {
  const groups = new Map();
  for (const [path, methods] of Object.entries(spec.paths)) {
    for (const [method, op] of Object.entries(methods)) {
      const tag = (op.tags || ["other"])[0];
      if (!groups.has(tag)) groups.set(tag, []);
      groups.get(tag).push({ path, method: method.toUpperCase(), op });
    }
  }
  const components = spec.components?.schemas || {};
  const order = Object.keys(TAGS).filter((t) => groups.has(t)).concat([...groups.keys()].filter((t) => !(t in TAGS)));
  let md = `# API reference\n\nEvery endpoint of the JSON API, generated from Tessera's OpenAPI document (version ${spec.info.version}). For conventions, sign-in, and examples, see [The API](api.md). A running instance serves the same reference at \`/api/docs\` and the document itself at \`/api/openapi.json\`.\n\n`;
  const blocks = [];
  for (const tag of order) {
    md += `## ${TAGS[tag] || tag}\n\n`;
    for (const { path, method, op } of groups.get(tag)) {
      const id = slug(`${method} ${path}`);
      const meaning = (description, schema) => [description, schema?.default !== undefined && schema.default !== null ? `Default: ${JSON.stringify(schema.default)}.` : ""]
        .filter(Boolean).join(" ");
      const params = (op.parameters || []).map((p) => [p.name, p.in, schemaType(p.schema, components), p.required ? "yes" : "", meaning(p.description || "", p.schema)]);
      const bodyRef = op.requestBody?.content?.["application/json"]?.schema;
      const bodyName = schemaType(bodyRef, components);
      const fields = bodyRef?.$ref ? Object.entries(components[bodyName]?.properties || {}).map(([k, v]) =>
        [k, schemaType(v, components), (components[bodyName]?.required || []).includes(k) ? "yes" : "", v.description || ""]) : [];
      blocks.push({ id, method, path, summary: op.summary || "", description: op.description || "", params, fields });
      md += `@@${blocks.length - 1}@@\n\n`;
    }
  }
  return { md, blocks };
}

function renderEndpoint(b) {
  const table = (head, rows) => rows.length ? `<div class="table-wrap"><table class="fields fields-${head.length}"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows
    .map((r) => `<tr>${r.map((c, i) => `<td>${i === 0 ? `<code>${escapeHtml(c)}</code>` : escapeHtml(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : "";
  return `<section class="endpoint" id="${b.id}"><h3><span class="method method-${b.method.toLowerCase()}">${b.method}</span> <code>${escapeHtml(b.path)}</code><a class="anchor" href="#${b.id}" aria-label="Link to this endpoint">#</a></h3>`
    + `<p>${escapeHtml(b.summary)}</p>${b.description && b.description !== b.summary ? `<p class="muted">${escapeHtml(b.description)}</p>` : ""}`
    + table(["Parameter", "In", "Type", "Required", "Meaning"], b.params)
    + (b.fields.length ? `<p class="muted">JSON body:</p>${table(["Field", "Type", "Required", "Meaning"], b.fields)}` : "")
    + `</section>`;
}

// --- Checks ----------------------------------------------------------------------------------

export function check(out) {
  const problems = [];
  const pages = readdirSync(out).filter((f) => f.endsWith(".html"));
  const ids = new Map(pages.map((p) => [p, new Set([...readFileSync(join(out, p), "utf8").matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]))]));
  for (const page of pages) {
    const html = readFileSync(join(out, page), "utf8");
    for (const [, attr, target] of html.matchAll(/\s(href|src)="([^"]+)"/g)) {
      if (/^(https?:|mailto:|data:)/.test(target)) continue;
      const [file, anchor] = target.split("#");
      const path = file || page;
      if (file && !existsSync(join(out, file))) { problems.push(`${page}: ${attr} ${target} is missing`); continue; }
      if (anchor && path.endsWith(".html") && !ids.get(path)?.has(anchor)) problems.push(`${page}: no #${anchor} in ${path}`);
    }
  }
  return problems;
}

// --- Build -----------------------------------------------------------------------------------

export function build(out) {
  const ver = version();
  rmSync(out, { recursive: true, force: true });
  mkdirSync(join(out, "assets"), { recursive: true });
  for (const file of ["site.css", "site.js", "theme.js"]) cpSync(join(HERE, "assets", file), join(out, "assets", file));
  cpSync(join(STATIC, "css", "tokens.css"), join(out, "assets", "tokens.css"));
  cpSync(join(STATIC, "img", "tessera-mark.svg"), join(out, "assets", "tessera-mark.svg"));
  if (existsSync(join(DOCS, "images"))) cpSync(join(DOCS, "images"), join(out, "images"), { recursive: true });

  const order = MENU.flatMap(([, items]) => items);
  const search = [];
  const spec = JSON.parse(readFileSync(join(DOCS, "openapi.json"), "utf8"));
  for (const [index, [name, label]] of order.entries()) {
    let source;
    let blocks = [];
    let fromDir = DOCS;
    if (name === "index") source = null;
    else if (name === "changelog") { source = readFileSync(join(REPO, "CHANGELOG.md"), "utf8"); fromDir = REPO; }
    else if (name === "api-reference") ({ md: source, blocks } = apiReference(spec));
    else source = readFileSync(join(DOCS, `${name}.md`), "utf8");
    const { html: rendered, headings } = source === null
      ? home()
      : markdown(source, { link: linker(fromDir), figure });
    let html = rendered.replace(/<p>@@(\d+)@@<\/p>/g, (_, i) => renderEndpoint(blocks[Number(i)]));
    for (const b of blocks) headings.push({ level: 3, text: `${b.method} ${b.path}`, id: b.id });
    if (name === "api-reference") headings.splice(0, headings.length, ...headings.filter((h) => h.level === 2));
    const first = plain(rendered.match(/<p class="lead">(.*?)<\/p>/s)?.[1] || rendered.match(/<p>(.*?)<\/p>/s)?.[1] || label);
    const title = name === "index" ? "Tessera: every contribution, connected" : (headings.find((h) => h.level === 1)?.text || label);

    writeFileSync(join(out, `${name}.html`), shell({
      name, title, body: html, headings, description: first.slice(0, 200), ver,
      prev: order[index - 1], next: order[index + 1],
    }));
    search.push(...sections(name, label, html));
  }
  writeFileSync(join(out, "assets", "search.json"), JSON.stringify(search));
  writeFileSync(join(out, ".nojekyll"), "");
  const problems = check(out);
  return { pages: order.length, sections: search.length, problems };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const flag = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : null; };
  const out = resolve(flag("--out") || join(REPO, "site"));
  const result = build(out);
  if (result.problems.length) {
    console.error(`Broken links:\n  ${result.problems.join("\n  ")}`);
    process.exit(1);
  }
  console.log(`Built ${result.pages} pages (${result.sections} searchable sections) in ${relative(process.cwd(), out) || "."}`);
  const target = flag("--publish");
  if (target) {
    const dest = resolve(target);
    if (!existsSync(join(dest, ".git"))) {
      console.error(`${dest} is not a git worktree of the gh-pages branch; nothing was published.`);
      process.exit(1);
    }
    for (const entry of readdirSync(dest)) if (entry !== ".git") rmSync(join(dest, entry), { recursive: true, force: true });
    cpSync(out, dest, { recursive: true });
    console.log(`Copied into ${dest}. Review, then commit and push the gh-pages branch to publish.`);
  }
}

