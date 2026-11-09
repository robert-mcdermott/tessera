// Admin → Branding (PLT-13): the product's name, palette, the organization's own colors by day
// and by night, logos, and footer links, with a live preview of the app and the public site and
// every color pair's contrast. Saving writes the [branding] section of the configuration file,
// and is refused while any pair is below WCAG AA.

import { get, post, request, upload } from "../core/api.js";
import { h } from "../core/dom.js";
import { callout, card, debounce, pageHeader } from "../components/ui.js";
import { toast } from "../components/toast.js";
import { show } from "./page.js";

const COLOR_LABELS = {
  bg: "Page", surface: "Cards and panels", text: "Text", muted: "Secondary text", border: "Lines",
  accent: "Accent: links and buttons", on_accent: "Text on the accent", stripe: "Header stripe",
  person: "People", publication: "Publications", grant: "Grants", study: "Studies",
};
const IMAGE_LABELS = {
  logo_light: ["Logo", "For light backgrounds. SVG, or PNG at twice its size (about 380 by 60 pixels)."],
  logo_dark: ["Logo after dark", "For dark backgrounds; the light one is used if there is none."],
  favicon: ["Browser icon", "Square: SVG, or PNG at least 64 pixels, or ICO."],
  social_image: ["Link preview", "Shown when someone shares a link to the public site: PNG or JPEG, 1200 by 630 pixels."],
};
const PALETTE_LABELS = { tessera: "Tessera", harbor: "Harbor", cassette: "Cassette" };
const HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;

function full(hex) {
  return hex.length === 4 ? `#${[...hex.slice(1)].map((c) => c + c).join("")}` : hex;
}

/** One color: a picker and its hex value, empty meaning Tessera's own. */
function colorInput(id, label, value, fallback, onChange) {
  const text = h("input", { class: "input mono color-hex", id, value: value ?? "", placeholder: fallback,
    "aria-label": label, spellcheck: "false", autocomplete: "off", maxlength: 7 });
  const picker = h("input", { type: "color", class: "color-swatch", value: full(value || fallback),
    "aria-label": `${label}: pick a color`, tabindex: "-1" });
  const reset = h("button", { type: "button", class: "btn btn-ghost btn-sm", title: "Use Tessera's color",
    "aria-label": `${label}: use Tessera's color`, hidden: !value }, "Reset");
  text.addEventListener("input", () => {
    const v = text.value.trim();
    if (HEX.test(v)) picker.value = full(v);
    reset.hidden = !v;
    onChange(v);
  });
  picker.addEventListener("input", () => {
    text.value = picker.value;
    reset.hidden = false;
    onChange(picker.value);
  });
  reset.addEventListener("click", () => {
    text.value = "";
    picker.value = full(fallback);
    reset.hidden = true;
    onChange("");
  });
  return h("span", { class: "color-field" }, picker, text, reset);
}

/** The mock app and public site, drawn with one mode's colors. */
function previewPane(mode, settings) {
  const pane = h("div", { class: "brand-preview", "data-mode": mode, role: "img",
    "aria-label": mode === "day" ? "Preview by day" : "Preview after dark" });
  const logoSlot = h("span", { class: "bp-logo" });
  const name = h("span", { class: "bp-name" });
  const site = h("span", { class: "bp-site-name" });
  pane.append(
    h("div", { class: "bp-stripe" }),
    h("div", { class: "bp-body" },
      h("div", { class: "bp-side" }, logoSlot, name,
        h("span", { class: "bp-nav bp-nav-on" }, "People"), h("span", { class: "bp-nav" }, "Publications"),
        h("span", { class: "bp-nav" }, "Grants")),
      h("div", { class: "bp-main" },
        h("div", { class: "bp-card" },
          h("strong", { class: "bp-text" }, "Single-cell maps of the tumor immune niche"),
          h("span", { class: "bp-muted" }, "Journal of Example Research · 2026"),
          h("span", { class: "bp-link" }, "Read the paper"),
          h("span", { class: "bp-chips" },
            ["person", "publication", "grant", "study"].map((kind) => h("span", { class: `bp-chip bp-${kind}` },
              { person: "Person", publication: "Paper", grant: "Grant", study: "Study" }[kind]))),
          h("span", { class: "bp-button" }, "Confirm")),
        h("div", { class: "bp-site" }, h("div", { class: "bp-site-head" }, site), h("div", { class: "bp-stripe" })))));
  pane.update = (colors, data) => {
    for (const [key, value] of Object.entries(colors)) pane.style.setProperty(`--pv-${key.replace("_", "-")}`, value);
    name.textContent = data.product_name || "Tessera";
    site.textContent = data.product_name || "Tessera";
    const own = mode === "night" ? (data.preview.logo_dark || data.preview.logo_light) : data.preview.logo_light;
    logoSlot.replaceChildren(h("img", { src: own || "/img/tessera-mark.svg", alt: "" }));
    name.hidden = Boolean(own);  // a logo stands for the name, as in the app
  };
  return pane;
}

function contrastTable(rows) {
  const byPair = new Map();
  for (const row of rows) {
    const key = `${row.foreground}/${row.background}`;
    if (!byPair.has(key)) byPair.set(key, { label: row.label, minimum: row.minimum });
    byPair.get(key)[row.mode] = row;
  }
  const cell = (row) => h("td", { class: "num", "data-fails": row.passes ? null : "" },
    row.passes ? h("span", {}, `${row.ratio.toFixed(2)}:1 `, h("span", { class: "sr-only" }, "passes"))
      : h("strong", {}, `${row.ratio.toFixed(2)}:1 `, h("span", {}, "too low")));
  return h("div", { class: "table-wrap" }, h("table", { class: "table", "data-dense": "" },
    h("caption", { class: "sr-only" }, "Contrast of each pair of colors that carries text"),
    h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Pair"), h("th", { scope: "col", class: "num" }, "Needs"),
      h("th", { scope: "col", class: "num" }, "By day"), h("th", { scope: "col", class: "num" }, "After dark"))),
    h("tbody", {}, [...byPair.values()].map((pair) => h("tr", {},
      h("td", {}, pair.label[0].toUpperCase() + pair.label.slice(1)), h("td", { class: "num" }, `${pair.minimum}:1`),
      cell(pair.day), cell(pair.night))))));
}

export function branding(main) {
  return show(main, {
    title: "Branding",
    load: () => get("/api/v1/admin/branding", null, { fresh: true }),
    view: ({ data: state }) => {
      const settings = structuredClone(state.settings);
      settings.preview = Object.fromEntries(Object.keys(IMAGE_LABELS).map((image) => [image,
        settings.images[image] ? `/branding/${image.replace("_", "-")}` : null]));
      const panes = { day: previewPane("day", settings), night: previewPane("night", settings) };
      const contrastSlot = h("div");
      const problemsSlot = h("div", { role: "status", "aria-live": "polite" });
      const toml = h("pre", { class: "code-block" });
      const save = h("button", { type: "button", class: "btn btn-primary" }, "Save to the configuration file");

      const refresh = (checked) => {
        for (const mode of ["day", "night"]) panes[mode].update(checked.colors[mode], settings);
        contrastSlot.replaceChildren(contrastTable(checked.contrast));
        problemsSlot.replaceChildren(...(checked.problems?.length
          ? [callout(h("span", {}, h("strong", {}, "Not ready to save. "), checked.problems.join(" ")), "warn")]
          : [h("p", { class: "subtle" }, "Every pair meets WCAG 2.2 AA by day and after dark.")]));
        toml.textContent = checked.toml;
        save.disabled = Boolean(checked.problems?.length) || !state.file.writable;
      };
      const body = () => ({
        product_name: settings.product_name, theme: settings.theme, accent_color: settings.accent_color || null,
        show_powered_by: settings.show_powered_by, day: settings.day, night: settings.night,
        footer_links: settings.footer_links.filter((l) => l.label || l.url), images: settings.images,
      });
      const check = debounce(async () => {
        try {
          refresh((await request("POST", "/api/v1/admin/branding/check", { body: body() })).data);
        } catch (error) {
          problemsSlot.replaceChildren(callout(error.body?.problems?.join(" ") || error.message, "warn"));
          save.disabled = true;
        }
      }, 150);

      const nameInput = h("input", { class: "input", id: "brand-name", value: settings.product_name, maxlength: 60,
        oninput: () => { settings.product_name = nameInput.value; check(); } });
      const powered = h("input", { type: "checkbox", id: "brand-powered", checked: settings.show_powered_by,
        onchange: () => { settings.show_powered_by = powered.checked; check(); } });
      const theme = h("select", { class: "input", id: "brand-theme", onchange: () => { settings.theme = theme.value; check(); } },
        state.palettes.map((p) => h("option", { value: p, selected: p === settings.theme }, PALETTE_LABELS[p] ?? p)));
      const accent = colorInput("brand-accent", "Accent", settings.accent_color, state.defaults.day.accent,
        (v) => { settings.accent_color = v || null; check(); });

      const colorRows = state.color_names.map((name) => h("tr", {},
        h("td", {}, h("span", { class: "brand-color-name" }, COLOR_LABELS[name] ?? name), h("span", { class: "subtle mono" }, ` ${name}`)),
        ["day", "night"].map((mode) => h("td", {}, colorInput(`brand-${mode}-${name}`, `${COLOR_LABELS[name]} ${mode === "day" ? "by day" : "after dark"}`,
          settings[mode][name], state.defaults[mode][name], (v) => {
            if (v) settings[mode][name] = v.toLowerCase(); else delete settings[mode][name];
            check();
          })))));

      const imageRows = Object.entries(IMAGE_LABELS).map(([image, [label, hint]]) => {
        const thumb = h("span", { class: "brand-thumb" });
        const showThumb = (src) => thumb.replaceChildren(src ? h("img", { src, alt: "" }) : h("span", { class: "subtle" }, "None"));
        showThumb(settings.preview[image]);
        const remove = h("button", { type: "button", class: "btn btn-ghost btn-sm", hidden: !settings.images[image],
          onclick: () => { settings.images[image] = null; settings.preview[image] = null; showThumb(null); remove.hidden = true; check(); } }, "Remove");
        // Hidden, and opened by a visible button: keyboard focus lands on something one can see.
        const file = h("input", { type: "file", hidden: true, tabindex: "-1", "aria-hidden": "true",
          accept: image === "social_image" ? "image/png,image/jpeg" : image === "favicon" ? "image/svg+xml,image/png,.ico" : "image/svg+xml,image/png,image/jpeg,image/webp",
          onchange: async () => {
            const chosen = file.files?.[0];
            if (!chosen) return;
            try {
              const stored = (await upload(`/api/v1/admin/branding/images/${image}`, chosen)).data;
              settings.images[image] = stored.path;
              settings.preview[image] = URL.createObjectURL(chosen);
              showThumb(settings.preview[image]);
              remove.hidden = false;
              check();
            } catch (error) {
              toast(error.body?.problems?.join(" ") || error.message);
            }
            file.value = "";
          } });
        return h("div", { class: "brand-image" }, thumb,
          h("div", { class: "stack-sm" }, h("strong", {}, label), h("span", { class: "subtle" }, hint),
            h("div", { class: "row-sm" }, file, h("button", { type: "button", class: "btn btn-sm", onclick: () => file.click(),
              "aria-label": `${settings.images[image] ? "Replace" : "Upload"} the ${label.toLowerCase()}` }, settings.images[image] ? "Replace" : "Upload"), remove)));
      });

      const linksList = h("div", { class: "stack-sm" });
      const drawLinks = () => linksList.replaceChildren(...settings.footer_links.map((linkItem, i) => h("div", { class: "row-sm wrap" },
        h("input", { class: "input", value: linkItem.label, placeholder: "Label, such as Privacy", "aria-label": `Footer link ${i + 1}: label`,
          oninput: (e) => { linkItem.label = e.target.value; check(); } }),
        h("input", { class: "input", value: linkItem.url, placeholder: "https://…", "aria-label": `Footer link ${i + 1}: address`,
          oninput: (e) => { linkItem.url = e.target.value; check(); } }),
        h("button", { type: "button", class: "btn btn-ghost btn-sm", "aria-label": `Remove footer link ${i + 1}`,
          onclick: () => { settings.footer_links.splice(i, 1); drawLinks(); check(); } }, "Remove"))),
      settings.footer_links.length < 8 ? h("div", { class: "row-sm" }, h("button", { type: "button", class: "btn btn-sm",
        onclick: () => { settings.footer_links.push({ label: "", url: "" }); drawLinks(); } }, "Add a link")) : null);
      drawLinks();

      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          await post("/api/v1/admin/branding", body());
          toast("Saved to the configuration file. Everyone sees it on their next page load.");
          window.setTimeout(() => window.location.reload(), 900);
        } catch (error) {
          problemsSlot.replaceChildren(callout(error.body?.problems?.join(" ") || error.message, "warn"));
          save.disabled = false;
        }
      });

      refresh({ colors: state.colors, contrast: state.contrast, problems: [], toml: state.toml });
      return h("div", { class: "stack" },
        pageHeader({ eyebrow: "Admin", title: "Branding",
          meta: "How Tessera looks to everyone: its name, colors, and logos, in the app and on the public site. Saving writes the [branding] section of the configuration file." }),
        state.file.writable ? null : callout(`The server cannot write ${state.file.name ?? "the configuration file"}. Copy the section below into it by hand.`, "warn"),
        h("div", { class: "brand-layout" },
          h("div", { class: "stack" },
            card({ title: "Name", pad: true, body: h("div", { class: "stack-sm" },
              h("div", { class: "field" }, h("label", { for: "brand-name" }, "Product name"), nameInput),
              h("label", { class: "row-sm" }, powered, "Show “Powered by Tessera” in the sidebar")) }),
            card({ title: "Palette", pad: true, body: h("div", { class: "stack-sm" },
              h("div", { class: "field" }, h("label", { for: "brand-theme" }, "What everyone sees first"), theme,
                h("span", { class: "subtle" }, "Each person can still choose another under Appearance.")),
              h("div", { class: "field" }, h("label", { for: "brand-accent" }, "Accent"), accent,
                h("span", { class: "subtle" }, "Used as given by day and lightened after dark, unless you set both below."))) }),
            card({ title: "Your colors", pad: false, body: h("div", { class: "table-wrap" }, h("table", { class: "table brand-colors", "data-dense": "" },
              h("caption", { class: "sr-only" }, "The organization's colors by day and after dark; empty means Tessera's"),
              h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Color"), h("th", { scope: "col" }, "By day"), h("th", { scope: "col" }, "After dark"))),
              h("tbody", {}, colorRows))) }),
            card({ title: "Logos and images", pad: true, body: h("div", { class: "stack" }, imageRows) }),
            card({ title: "Footer links", pad: true, body: h("div", { class: "stack-sm" },
              h("p", { class: "subtle" }, "Shown at the foot of the sidebar and of every public page, such as privacy and accessibility statements."), linksList) })),
          h("div", { class: "stack brand-aside" },
            card({ title: "Preview", pad: true, body: h("div", { class: "stack-sm" }, panes.day, panes.night) }),
            card({ title: "Contrast", pad: false, body: h("div", {}, contrastSlot) }),
            problemsSlot,
            h("div", { class: "row wrap" }, save),
            h("details", { class: "card-details" }, h("summary", {}, "What will be written"), toml))));
    },
  });
}
