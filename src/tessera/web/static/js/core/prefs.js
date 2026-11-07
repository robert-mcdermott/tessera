// Per-browser preferences. Storage may be unavailable (private mode); fail quietly.

export function read(key, fallback = null) {
  try {
    const value = localStorage.getItem(`tessera.${key}`);
    return value == null ? fallback : JSON.parse(value);
  } catch {
    return fallback;
  }
}

export function write(key, value) {
  try {
    localStorage.setItem(`tessera.${key}`, JSON.stringify(value));
  } catch {
    /* preference lasts for this page only */
  }
}

export const THEMES = ["system", "light", "dark"];

export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
  else root.removeAttribute("data-theme");
}

export function currentTheme() {
  const theme = read("theme", "system");
  return THEMES.includes(theme) ? theme : "system";
}

// Palettes (css/palettes.css). The organization's default comes from configuration; a
// viewer may choose another. Preview colors are for the Appearance picker only.
export const PALETTES = [
  {
    id: "tessera", name: "Tessera", description: "Calm neutrals with an indigo accent; graphite after dark.",
    light: { bg: "#f9fafc", surface: "#ffffff", accent: "#4f46e5", text: "#1b2030" },
    dark: { bg: "#141518", surface: "#1b1c20", accent: "#a6a2f2", text: "#f0f1f3" },
    bands: ["#4f46e5", "#0e8a8a", "#b7791f", "#c2364f", "#7c3aed"],
  },
  {
    id: "harbor", name: "Harbor", description: "Deep navy with teal, bright blue, purple, and gold.",
    light: { bg: "#f4f4f4", surface: "#ffffff", accent: "#08708f", text: "#112345" },
    dark: { bg: "#0d1b36", surface: "#112345", accent: "#00abc8", text: "#f4f7fb" },
    bands: ["#18365d", "#0a799a", "#00abc8", "#aa4ac4", "#ffb500"],
  },
  {
    id: "cassette", name: "Cassette", description: "Label paper and rainbow stripes by day; black labels and a sunset at night.",
    light: { bg: "#eee5d1", surface: "#f8f2e4", accent: "#b3321a", text: "#1f1812" },
    dark: { bg: "#110f10", surface: "#1a1718", accent: "#f39a45", text: "#f4ebdd" },
    bands: ["#d9402f", "#ec8a26", "#f0c02e", "#2e9b58", "#2a6db5"],
  },
];
const PALETTE_IDS = new Set(PALETTES.map((p) => p.id));

/** The organization's default, remembered so the right palette paints before /meta loads. */
export function organizationPalette() {
  const id = read("orgPalette", "tessera");
  return PALETTE_IDS.has(id) ? id : "tessera";
}

export function setOrganizationPalette(id) {
  if (!PALETTE_IDS.has(id)) return;
  write("orgPalette", id);
  applyPalette(currentPalette());
}

/** The viewer's own choice, or null to follow the organization. */
export function viewerPalette() {
  const id = read("palette", null);
  return PALETTE_IDS.has(id) ? id : null;
}

export function currentPalette() {
  return viewerPalette() ?? organizationPalette();
}

export function choosePalette(id) {
  write("palette", PALETTE_IDS.has(id) ? id : null);
  applyPalette(currentPalette());
}

export function applyPalette(id) {
  const root = document.documentElement;
  if (id && id !== "tessera" && PALETTE_IDS.has(id)) root.setAttribute("data-palette", id);
  else root.removeAttribute("data-palette");
}

/** Mode and palette together, for page start-up. */
export function applyAppearance() {
  applyTheme(currentTheme());
  applyPalette(currentPalette());
}
