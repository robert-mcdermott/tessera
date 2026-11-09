// An automated WCAG 2.2 AA pass over a page, run with the documentation's screenshots
// (node docs/capture/capture.mjs --audit). It checks what a machine can check reliably:
//
//   name        every control has an accessible name (Chrome's own accessibility tree)       4.1.2
//   structure   one main landmark, a level-one heading, no skipped heading levels, lang,
//               a title, and an alternative for every image                       1.3.1, 2.4.2, 3.1.1, 1.1.1
//   contrast    rendered text against what is behind it (4.5:1, or 3:1 for large text)     1.4.3
//   target      controls at least 24 by 24 pixels, or spaced so a 24-pixel circle fits    2.5.8
//   focus       a real Tab walk: focus moves, is visible, and is not hidden under sticky
//               bars, and the keyboard is never trapped                     2.1.2, 2.4.7, 2.4.11
//
// What it cannot judge (meaningful names and alternatives, reading order, how a screen reader
// announces a change) is for the manual pass.

const INTERACTIVE_ROLES = new Set([
  "button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "tab",
  "menuitem", "menuitemcheckbox", "menuitemradio", "slider", "spinbutton", "listbox", "option",
]);

/** The checks that run in the page: structure, contrast, and target size. */
const IN_PAGE = String.raw`(() => {
  const findings = [];
  const add = (rule, el, detail) => findings.push({ rule, where: describe(el), detail });
  function describe(el) {
    if (!el || el.nodeType !== 1) return String(el);
    const id = el.id ? "#" + el.id : "";
    const cls = el.classList?.length ? "." + [...el.classList].slice(0, 2).join(".") : "";
    const text = (el.getAttribute("aria-label") || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 40);
    return el.tagName.toLowerCase() + id + cls + (text ? ' "' + text + '"' : "");
  }
  function visible(el) {
    if (el.closest("[hidden], [inert], [aria-hidden=true]")) return false;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    // Visually hidden on purpose (the sr-only pattern): read by screen readers, not seen.
    if (style.clip === "rect(0px, 0px, 0px, 0px)" || style.clipPath === "inset(50%)") return false;
    return true;
  }

  // --- structure ---
  if (!document.documentElement.lang) add("structure", document.documentElement, "the page has no lang");
  if (!document.title.trim()) add("structure", document.documentElement, "the page has no title");
  const mains = [...document.querySelectorAll("main, [role=main]")].filter((m) => !m.closest("[hidden]"));
  if (mains.length !== 1) add("structure", document.body, mains.length + " main landmarks (want one)");
  const headings = [...document.querySelectorAll("h1, h2, h3, h4, h5, h6, [role=heading]")].filter(visible);
  // With a modal open, the page behind it is out of reach, its heading included: that is right.
  const modal = [...document.querySelectorAll("[aria-modal=true]")].some(visible);
  if (!modal && !headings.some((h) => (h.getAttribute("aria-level") || h.tagName[1]) === "1")) add("structure", document.body, "no level-one heading");
  let last = 0;
  for (const h of headings) {
    const level = Number(h.getAttribute("aria-level") || h.tagName[1]);
    if (last && level > last + 1) add("structure", h, "heading level " + level + " follows level " + last);
    last = level;
  }
  for (const img of document.querySelectorAll("img")) {
    if (!img.hasAttribute("alt") && !img.closest("[aria-hidden=true]")) add("structure", img, "an image without alt");
  }

  // --- contrast ---
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  function rgba(color) {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "rgba(0,0,0,0)";
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  }
  const over = (top, under) => {
    const a = top[3];
    return [0, 1, 2].map((i) => top[i] * a + under[i] * (1 - a)).concat(1);
  };
  function backdrop(el) {
    const layers = [];
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.backgroundImage !== "none" && !style.backgroundImage.startsWith("linear-gradient(transparent")) return null;
      const color = rgba(style.backgroundColor);
      if (color[3] > 0) layers.push(color);
      if (color[3] >= 1) break;
    }
    let result = [255, 255, 255, 1];
    for (const layer of layers.reverse()) result = over(layer, result);
    return result;
  }
  const lum = ([r, g, b]) => {
    const c = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * c(r) + 0.7152 * c(g) + 0.0722 * c(b);
  };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const el = node.parentElement;
    if (!el || seen.has(el) || !node.textContent.trim()) continue;
    seen.add(el);
    if (!visible(el) || el.closest("svg, [disabled], [aria-disabled=true], option")) continue;
    const style = getComputedStyle(el);
    let opacity = 1;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) opacity *= Number(getComputedStyle(n).opacity);
    if (opacity < 0.1) continue;
    const back = backdrop(el);
    if (!back) continue;
    const fore = rgba(style.color);
    fore[3] *= opacity;
    const r = ratio(over(fore, back), back);
    const size = parseFloat(style.fontSize);
    const bold = Number(style.fontWeight) >= 700;
    const large = size >= 24 || (bold && size >= 18.66);
    const needed = large ? 3 : 4.5;
    if (r < needed - 0.01) add("contrast", el, r.toFixed(2) + ":1, needs " + needed + ":1 (" + style.color + " at " + size + "px)");
  }

  // --- target size ---
  const targets = [...document.querySelectorAll("button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=checkbox], [role=switch], [role=menuitem]")]
    .filter(visible);
  const rects = targets.map((t) => t.getBoundingClientRect());
  targets.forEach((t, i) => {
    const r = rects[i];
    if (r.width >= 24 && r.height >= 24) return;
    // Exempt: a link inside a line of text; a box or radio whose label also takes the click.
    if (t.tagName === "A" && getComputedStyle(t).display === "inline" && t.parentElement && /\S/.test([...t.parentElement.childNodes].filter((n) => n !== t).map((n) => n.textContent).join(""))) return;
    if ((t.type === "checkbox" || t.type === "radio") && t.labels?.length) return;
    if (t.type === "color" && t.labels?.length === 0 && t.nextElementSibling?.matches("input")) return;
    // The spacing exception: a 24-pixel circle on the target's center meets no other target.
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const crowded = rects.some((o, j) => {
      if (j === i || targets[j].contains(t) || t.contains(targets[j])) return false;
      const dx = Math.max(o.left - cx, 0, cx - o.right), dy = Math.max(o.top - cy, 0, cy - o.bottom);
      return dx * dx + dy * dy < 144;
    });
    if (crowded) add("target", t, Math.round(r.width) + " by " + Math.round(r.height) + " pixels, with another control within 12");
  });
  return findings;
})()`;

/** Remember how each focusable element looks when it does not have focus. */
const MARK_UNFOCUSED = String.raw`(() => {
  window.__unfocused = new WeakMap();
  const keys = ["outlineStyle", "outlineWidth", "outlineColor", "boxShadow", "borderColor", "backgroundColor", "textDecorationLine", "color"];
  for (const el of document.querySelectorAll("a[href], button, input, select, textarea, summary, [tabindex]")) {
    const s = getComputedStyle(el);
    window.__unfocused.set(el, keys.map((k) => s[k]).join("|"));
  }
  window.__focusKeys = keys;
  return true;
})()`;

/** Where focus is now: what it is, whether it looks focused, and whether it can be seen. */
const FOCUS_NOW = String.raw`(() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return { where: "body" };
  const s = getComputedStyle(el);
  const now = window.__focusKeys.map((k) => s[k]).join("|");
  const before = window.__unfocused.get(el);
  const outline = s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0;
  const changed = before === undefined ? outline : before !== now;
  const r = el.getBoundingClientRect();
  const points = [[0.5, 0.5], [0.15, 0.5], [0.85, 0.5], [0.5, 0.2], [0.5, 0.8]]
    .map(([fx, fy]) => [r.left + r.width * fx, r.top + r.height * fy])
    .filter(([x, y]) => x >= 0 && y >= 0 && x < innerWidth && y < innerHeight);
  const hit = points.some(([x, y]) => { const at = document.elementFromPoint(x, y); return at && (at === el || el.contains(at) || at.contains(el)); });
  const tag = el.tagName.toLowerCase();
  const label = (el.getAttribute("aria-label") || el.textContent || el.value || "").trim().replace(/\s+/g, " ").slice(0, 40);
  return { where: tag + (el.id ? "#" + el.id : "") + (el.classList.length ? "." + [...el.classList].slice(0, 2).join(".") : "") + (label ? ' "' + label + '"' : ""),
           visible: outline || changed, obscured: r.width > 0 && !hit, key: tag + "|" + label + "|" + Math.round(r.left) + "," + Math.round(r.top + scrollY) };
})()`;

async function accessibleNames(page) {
  const { nodes } = await page.cdp("Accessibility.getFullAXTree", {});
  const missing = [];
  for (const node of nodes) {
    const role = node.role?.value;
    if (node.ignored || !INTERACTIVE_ROLES.has(role)) continue;
    if (!String(node.name?.value ?? "").trim()) {
      let where = role;
      if (node.backendDOMNodeId) {
        try {
          const { object } = await page.cdp("DOM.resolveNode", { backendNodeId: node.backendDOMNodeId });
          const { result } = await page.cdp("Runtime.callFunctionOn", {
            objectId: object.objectId, returnByValue: true,
            functionDeclaration: "function () { const c = this.classList?.length ? '.' + [...this.classList].slice(0, 2).join('.') : ''; return this.tagName.toLowerCase() + (this.id ? '#' + this.id : '') + c; }",
          });
          where = `${role} ${result.value}`;
        } catch { /* the node went away */ }
      }
      missing.push({ rule: "name", where, detail: "no accessible name" });
    }
  }
  return missing;
}

async function keyboardWalk(page, { presses = 120 } = {}) {
  const findings = [];
  await page.evaluate("document.activeElement?.blur(); window.scrollTo(0, 0); true");
  await page.evaluate(MARK_UNFOCUSED);
  const seen = new Map();
  let previous = null;
  let stuck = 0;
  for (let i = 0; i < presses; i += 1) {
    await page.key("Tab");
    const now = await page.evaluate(FOCUS_NOW);
    if (now.where === "body") { if (i > 0) break; continue; }
    if (seen.has(now.key)) break;  // round the page and back to the start: no trap
    seen.set(now.key, true);
    if (!now.visible) findings.push({ rule: "focus", where: now.where, detail: "focus is not visible" });
    if (now.obscured) findings.push({ rule: "focus", where: now.where, detail: "focus is hidden behind something else" });
    stuck = previous === now.key ? stuck + 1 : 0;
    if (stuck >= 3) { findings.push({ rule: "focus", where: now.where, detail: "the keyboard is trapped here" }); break; }
    previous = now.key;
  }
  return findings;
}

/** Every finding on the page as it is now (run after its screenshot: the walk moves focus). */
export async function audit(page) {
  await page.cdp("Accessibility.enable", {});
  await page.cdp("DOM.enable", {});
  const findings = [...await accessibleNames(page), ...await page.evaluate(IN_PAGE)];
  findings.push(...await keyboardWalk(page));
  // One line for each distinct problem.
  const unique = new Map(findings.map((f) => [`${f.rule}|${f.where}|${f.detail}`, f]));
  return [...unique.values()];
}
