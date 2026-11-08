#!/usr/bin/env node
// Draws the documentation's diagrams as SVG, in light and dark, from one color table.
//
//   node docs/capture/diagrams.mjs        writes docs/images/<diagram>-light.svg and -dark.svg

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const IMAGES = resolve(HERE, "..", "images");

// Close to Tessera's own tokens (tokens.css), by day and by night.
export const COLORS = {
  light: {
    bg: "#ffffff", surface: "#f5f6fa", border: "#d8dbe6", text: "#1b2033", muted: "#5a6074",
    accent: "#4b4bd6", source: "#3f5fd1", store: "#0b7f7a", engine: "#7a3fc4", people: "#b03a74", serve: "#a06a00",
  },
  dark: {
    bg: "#16181d", surface: "#1f2229", border: "#363b46", text: "#eceef3", muted: "#a2a7b4",
    accent: "#a5a8ff", source: "#9db2ff", store: "#6fd3c8", engine: "#c9a4ff", people: "#f29cc4", serve: "#f0c56a",
  },
};

const FONT = "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif";
const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;");

function box(c, { x, y, w, h, title, lines = [], tone }) {
  const color = c[tone];
  const body = lines.map((line, i) => `<text x="${x + 16}" y="${y + 50 + i * 19}" font-size="13" fill="${c.muted}">${esc(line)}</text>`).join("");
  return `<g><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12" fill="${c.surface}" stroke="${c.border}"/>`
    + `<rect x="${x}" y="${y}" width="5" height="${h}" rx="2.5" fill="${color}"/>`
    + `<text x="${x + 16}" y="${y + 27}" font-size="15" font-weight="650" fill="${c.text}">${esc(title)}</text>${body}</g>`;
}

function arrow(c, x1, y1, x2, y2, label, { dashed = false, labelAt = 0.5, dy = -8 } = {}) {
  const lx = x1 + (x2 - x1) * labelAt;
  const ly = y1 + (y2 - y1) * labelAt + dy;
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${c.muted}" stroke-width="1.6"${dashed ? ' stroke-dasharray="5 4"' : ""} marker-end="url(#head)"/>`
    + (label ? `<text x="${lx}" y="${ly}" font-size="12" fill="${c.muted}" text-anchor="middle">${esc(label)}</text>` : "");
}

function svg(c, width, height, title, body) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="${esc(title)}" font-family="${FONT}">
<title>${esc(title)}</title>
<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="${c.muted}"/></marker></defs>
<rect width="${width}" height="${height}" fill="${c.bg}"/>
${body}
</svg>
`;
}

export const DIAGRAMS = {
  architecture(c) {
    const W = 1040;
    const H = 560;
    const parts = [
      box(c, { x: 30, y: 40, w: 230, h: 120, title: "Public sources", tone: "source",
        lines: ["PubMed, NIH RePORTER,", "ClinicalTrials.gov, iCite, ORCID"] }),
      box(c, { x: 330, y: 40, w: 230, h: 120, title: "Connectors", tone: "source",
        lines: ["Rate-limited, incremental,", "replayable from raw payloads"] }),
      box(c, { x: 630, y: 40, w: 380, h: 120, title: "Raw zone and Parquet lake", tone: "store",
        lines: ["Every response kept as received;", "normalized records, queried with DuckDB"] }),
      box(c, { x: 630, y: 220, w: 380, h: 120, title: "Evidence engine", tone: "engine",
        lines: ["Builds the directory and suggests links,", "each with its evidence and a likelihood"] }),
      box(c, { x: 330, y: 220, w: 230, h: 120, title: "Ledger", tone: "people",
        lines: ["People, links, decisions,", "accounts (SQLite or PostgreSQL)"] }),
      box(c, { x: 30, y: 220, w: 230, h: 120, title: "Curators", tone: "people",
        lines: ["Confirm or reject", "suggestions; decisions stick"] }),
      box(c, { x: 630, y: 400, w: 380, h: 120, title: "Derived data", tone: "serve",
        lines: ["Search index, concept profiles,", "similar people, collaboration graph"] }),
      box(c, { x: 330, y: 400, w: 230, h: 120, title: "API and web app", tone: "serve",
        lines: ["JSON API (/api/v1) and a", "fast, accessible web app"] }),
      box(c, { x: 30, y: 400, w: 230, h: 120, title: "Everyone", tone: "accent",
        lines: ["Search, profiles, experts,", "connections, reports"] }),
      arrow(c, 262, 100, 326, 100, "fetch"),
      arrow(c, 562, 100, 626, 100, "store"),
      arrow(c, 820, 162, 820, 216, "read", { labelAt: 0.5, dy: 4 }),
      arrow(c, 626, 280, 564, 280, "suggest"),
      arrow(c, 262, 280, 326, 280, "decide"),
      arrow(c, 820, 342, 820, 396, "build", { dy: 4 }),
      arrow(c, 626, 460, 564, 460, "serve"),
      arrow(c, 326, 460, 264, 460, ""),
      arrow(c, 445, 342, 445, 396, "", { dashed: true }),
    ];
    return svg(c, W, H, "How Tessera works: public sources are fetched into a lake, the evidence engine suggests links that curators decide in the ledger, and derived data feeds the API and web app", parts.join("\n"));
  },
  evidence(c) {
    const W = 1040;
    const H = 330;
    const features = [
      ["ORCID iD matches", "+", c.store], ["Organization in the affiliation", "+", c.store], ["Acknowledges their grant", "+", c.store],
      ["Shared coauthors", "+", c.store], ["Different first name", "−", c.people], ["Common name", "−", c.people],
    ];
    const rows = features.map(([label, sign, color], i) => {
      const y = 70 + i * 36;
      return `<rect x="30" y="${y}" width="270" height="28" rx="8" fill="${c.surface}" stroke="${c.border}"/>`
        + `<text x="44" y="${y + 19}" font-size="13" fill="${c.text}">${esc(label)}</text>`
        + `<text x="284" y="${y + 19}" font-size="15" font-weight="700" fill="${color}" text-anchor="end">${sign}</text>`;
    }).join("");
    const parts = [
      `<text x="30" y="46" font-size="15" font-weight="650" fill="${c.text}">Evidence for one candidate paper</text>`,
      rows,
      arrow(c, 304, 165, 372, 165, "weights"),
      box(c, { x: 376, y: 105, w: 190, h: 120, title: "Likelihood", tone: "engine", lines: ["Weights add up to a", "score, shown as a %"] }),
      arrow(c, 568, 165, 632, 165, ""),
      `<g>${[["Strong", "95% and above", c.store, 80], ["Possible", "60% to 95%", c.serve, 148], ["Unlikely", "below 60%", c.muted, 216]].map(([name, range, color, y]) =>
        `<rect x="636" y="${y}" width="170" height="52" rx="10" fill="${c.surface}" stroke="${c.border}"/><circle cx="656" cy="${y + 26}" r="6" fill="${color}"/>`
        + `<text x="672" y="${y + 22}" font-size="14" font-weight="650" fill="${c.text}">${name}</text><text x="672" y="${y + 40}" font-size="12" fill="${c.muted}">${range}</text>`).join("")}</g>`,
      arrow(c, 808, 165, 862, 165, ""),
      box(c, { x: 866, y: 105, w: 150, h: 120, title: "A curator", tone: "people", lines: ["accepts or rejects;", "the decision is kept"] }),
    ];
    return svg(c, W, H, "How a suggestion is scored: evidence for and against adds up to a likelihood, which sets its band; a curator makes the decision", parts.join("\n"));
  },
};

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  mkdirSync(IMAGES, { recursive: true });
  for (const [name, draw] of Object.entries(DIAGRAMS)) {
    for (const [theme, colors] of Object.entries(COLORS)) {
      writeFileSync(join(IMAGES, `${name}-${theme}.svg`), draw(colors));
      console.log(`  ${name} (${theme})`);
    }
  }
}
