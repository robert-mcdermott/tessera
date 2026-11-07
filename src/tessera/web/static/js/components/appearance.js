// Appearance: light, dark, or system, and a color palette. Choices are this browser's; the
// organization's default palette comes from configuration (branding.theme).

import { h, icon } from "../core/dom.js";
import {
  applyTheme, choosePalette, currentPalette, currentTheme, organizationPalette, PALETTES, viewerPalette, write,
} from "../core/prefs.js";
import { openDrawer } from "./drawer.js";

const MODES = [
  { id: "system", label: "System", iconName: "system" },
  { id: "light", label: "Light", iconName: "sun" },
  { id: "dark", label: "Dark", iconName: "moon" },
];

/** A small picture of a palette: its day and night surfaces with the accent, over its bands. */
function preview(palette) {
  const half = (mode) => {
    const colors = palette[mode];
    const tile = h("span", { class: "swatch-tile" },
      h("span", { class: "swatch-card" }, h("span", { class: "swatch-line" }), h("span", { class: "swatch-dot" })));
    tile.style.setProperty("--sw-bg", colors.bg);
    tile.style.setProperty("--sw-surface", colors.surface);
    tile.style.setProperty("--sw-accent", colors.accent);
    tile.style.setProperty("--sw-text", colors.text);
    return tile;
  };
  const bands = h("span", { class: "swatch-bands" }, palette.bands.map((color) => {
    const band = h("span");
    band.style.setProperty("background", color);
    return band;
  }));
  return h("span", { class: "swatch", "aria-hidden": "true" }, h("span", { class: "swatch-halves" }, half("light"), half("dark")), bands);
}

export function openAppearance({ onChange = () => {} } = {}) {
  openDrawer({
    title: "Appearance",
    label: "Appearance",
    render: (body) => {
      const draw = (focusKey = null) => {
        const mode = currentTheme();
        const chosen = currentPalette();
        const following = viewerPalette() == null;
        const org = organizationPalette();
        body.replaceChildren(
          h("section", { class: "drawer-section stack-sm" },
            h("h3", { id: "appearance-mode" }, "Mode"),
            h("div", { class: "segmented", role: "group", "aria-labelledby": "appearance-mode" }, MODES.map((m) =>
              h("button", { type: "button", "aria-pressed": String(m.id === mode), "data-key": `mode-${m.id}`,
                onclick: () => { write("theme", m.id); applyTheme(m.id); onChange(); draw(`mode-${m.id}`); } },
              icon(m.iconName, "icon icon-sm"), m.label))),
            h("p", { class: "subtle" }, "System follows your device's light or dark setting.")),
          h("section", { class: "drawer-section stack-sm" },
            h("h3", { id: "appearance-palette" }, "Palette"),
            h("div", { class: "palette-options", role: "group", "aria-labelledby": "appearance-palette" }, PALETTES.map((p) =>
              h("button", { type: "button", class: "palette-option", "aria-pressed": String(p.id === chosen), "data-key": `palette-${p.id}`,
                onclick: () => { choosePalette(p.id === org ? null : p.id); onChange(); draw(`palette-${p.id}`); } },
              preview(p),
              h("span", { class: "palette-text" },
                h("span", { class: "palette-name" }, p.name, p.id === org ? h("span", { class: "badge" }, "Default") : null),
                h("span", { class: "subtle" }, p.description)),
              p.id === chosen ? icon("check", "icon icon-sm palette-check") : null))),
            h("p", { class: "subtle" }, following
              ? "You are using your organization's default. Choosing another palette affects only this browser."
              : "This choice affects only this browser. Choose the default to follow your organization again.")));
        // Redrawing replaces the buttons; keep keyboard focus on the one just used.
        if (focusKey) body.querySelector(`[data-key="${focusKey}"]`)?.focus();
      };
      draw();
    },
  });
}
