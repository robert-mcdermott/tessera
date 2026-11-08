// Draws a graph on a canvas, laid out by the force worker. Hovering a node dims everything
// but it and its neighbours; dragging pins a node; the wheel, the keyboard, or the buttons
// zoom; the view follows the layout until the reader moves it. The canvas is never the only
// way in: every graph page also has a list with the same nodes and relationships.

import { h, iconButton } from "../core/dom.js";

const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const CELL = 32; // hit-testing grid, in layout units
const LABEL_PX = 12;

/**
 * nodes: [{id, label, kind, ...}], edges: [{source, target, kind, ...}] (ids).
 * look: { radius(node), color(node) -> CSS colour, edge(edge) -> {color, width, dash, alpha},
 *         force(edge) -> {distance, strength}, always(node) -> bool (label always),
 *         describe(node) -> [lines] }
 * on: { select(node | null), open(node) }
 */
export function graphView({ nodes, edges, look, on = {}, label = "Graph" }) {
  const n = nodes.length;
  const index = new Map(nodes.map((node, i) => [node.id, i]));
  const links = edges
    .map((e) => ({ ...e, s: index.get(e.source), t: index.get(e.target) }))
    .filter((e) => e.s != null && e.t != null && e.s !== e.t);
  const neighbours = nodes.map(() => new Set());
  for (const e of links) { neighbours[e.s].add(e.t); neighbours[e.t].add(e.s); }
  const radius = Float32Array.from(nodes, (node) => look.radius(node));
  // The largest nodes compete for a label even when zoomed out.
  const prominent = new Set([...radius.keys()].sort((a, b) => radius[b] - radius[a]).slice(0, 15));
  let pos = new Float32Array(n * 2);

  const canvas = h("canvas", { class: "graph-canvas", tabindex: "0", role: "img",
    "aria-label": `${label}. Drag to move, scroll or use + and − to zoom, 0 to fit. The List view has the same information as a table.` });
  const tip = h("div", { class: "graph-tip", hidden: true });
  const probe = h("span", { class: "graph-probe", "aria-hidden": "true" });
  const zoomButton = (name, title, run) => iconButton(name, title, { onclick: run });
  const stage = h("div", { class: "graph-stage" }, canvas, tip, probe,
    h("div", { class: "graph-zoom" },
      zoomButton("plus", "Zoom in", () => zoomBy(1.4)),
      zoomButton("minus", "Zoom out", () => zoomBy(1 / 1.4)),
      zoomButton("fit", "Fit to view", () => fit(true))));
  const ctx = canvas.getContext("2d");

  let view = { k: 1, x: 0, y: 0 };
  let userMoved = false;
  let hover = -1;
  let selected = -1;
  let matches = new Set();
  let drag = null;
  let frame = 0;
  let flight = 0;
  let grid = new Map();

  // Colours come from the theme's tokens, resolved through a probe element, so the graph
  // follows the palette and light or dark mode like the rest of the app.
  const colours = new Map();
  const colour = (expr) => {
    if (!colours.has(expr)) {
      probe.style.setProperty("color", expr);
      colours.set(expr, getComputedStyle(probe).color);
    }
    return colours.get(expr);
  };
  const retheme = () => { colours.clear(); render(); };
  const themeWatch = new MutationObserver(retheme);
  themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-palette", "style", "class"] });
  const schemes = matchMedia("(prefers-color-scheme: dark)");
  schemes.addEventListener("change", retheme);

  const worker = new Worker(new URL("../workers/layout.js", import.meta.url), { type: "module" });
  let attached = false;
  worker.onmessage = ({ data }) => {
    if (stage.isConnected) attached = true;
    else if (attached) { destroy(); return; } // the page moved on
    pos = data.positions;
    rebuildGrid();
    if (!userMoved) fit(false);
    render();
  };
  worker.postMessage({
    type: "start",
    reduced: reducedMotion(),
    nodes: nodes.map((node, i) => ({ r: radius[i], mass: 0.6 + radius[i] / 10 })),
    links: links.map((e) => ({ source: e.s, target: e.t, ...look.force(e) })),
  });

  const resize = new ResizeObserver(() => {
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(canvas.clientWidth * ratio));
    canvas.height = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (!userMoved) fit(false);
    render();
  });
  resize.observe(canvas);

  function rebuildGrid() {
    grid = new Map();
    for (let i = 0; i < n; i++) {
      const key = `${Math.floor(pos[2 * i] / CELL)},${Math.floor(pos[2 * i + 1] / CELL)}`;
      (grid.get(key) ?? grid.set(key, []).get(key)).push(i);
    }
  }

  const toWorld = (sx, sy) => [(sx - view.x) / view.k, (sy - view.y) / view.k];

  function hit(sx, sy) {
    const [wx, wy] = toWorld(sx, sy);
    const reach = Math.ceil((Math.max(...radius, 4) + 6 / view.k) / CELL);
    const ci = Math.floor(wx / CELL);
    const cj = Math.floor(wy / CELL);
    let best = -1;
    let bestDistance = Infinity;
    for (let a = ci - reach; a <= ci + reach; a++) {
      for (let b = cj - reach; b <= cj + reach; b++) {
        for (const i of grid.get(`${a},${b}`) ?? []) {
          const d = Math.hypot(pos[2 * i] - wx, pos[2 * i + 1] - wy);
          if (d <= radius[i] + 4 / view.k && d < bestDistance) { best = i; bestDistance = d; }
        }
      }
    }
    return best;
  }

  function bounds() {
    let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity;
    for (let i = 0; i < n; i++) {
      x0 = Math.min(x0, pos[2 * i] - radius[i]); x1 = Math.max(x1, pos[2 * i] + radius[i]);
      y0 = Math.min(y0, pos[2 * i + 1] - radius[i]); y1 = Math.max(y1, pos[2 * i + 1] + radius[i]);
    }
    return n ? { x0, y0, x1, y1 } : { x0: -1, y0: -1, x1: 1, y1: 1 };
  }

  function fit(animate) {
    const w = canvas.clientWidth; const ht = canvas.clientHeight;
    if (!w || !ht) return;
    const b = bounds();
    const pad = 48;
    const k = Math.min((w - pad * 2) / Math.max(1, b.x1 - b.x0), (ht - pad * 2) / Math.max(1, b.y1 - b.y0), 2.2);
    const target = { k, x: w / 2 - ((b.x0 + b.x1) / 2) * k, y: ht / 2 - ((b.y0 + b.y1) / 2) * k };
    if (animate) { userMoved = false; glide(target); } else { view = target; }
  }

  function glide(target) {
    cancelAnimationFrame(flight);
    if (reducedMotion()) { view = target; render(); return; }
    const from = { ...view };
    const start = performance.now();
    const stepFlight = (now) => {
      const p = Math.min(1, (now - start) / 420);
      const e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
      view = { k: from.k + (target.k - from.k) * e, x: from.x + (target.x - from.x) * e, y: from.y + (target.y - from.y) * e };
      render();
      if (p < 1) flight = requestAnimationFrame(stepFlight);
    };
    flight = requestAnimationFrame(stepFlight);
  }

  function zoomBy(factor, sx = canvas.clientWidth / 2, sy = canvas.clientHeight / 2) {
    const k = Math.min(8, Math.max(0.15, view.k * factor));
    const [wx, wy] = toWorld(sx, sy);
    view = { k, x: sx - wx * k, y: sy - wy * k };
    userMoved = true;
    render();
  }

  function flyTo(id) {
    const i = index.get(id);
    if (i == null) return;
    const k = Math.max(view.k, 1.6);
    userMoved = true;
    glide({ k, x: canvas.clientWidth / 2 - pos[2 * i] * k, y: canvas.clientHeight / 2 - pos[2 * i + 1] * k });
  }

  function render() {
    if (!frame) frame = requestAnimationFrame(draw);
  }

  function draw() {
    frame = 0;
    const ratio = window.devicePixelRatio || 1;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    ctx.translate(view.x, view.y);
    ctx.scale(view.k, view.k);
    const active = hover >= 0 ? hover : selected;
    const lit = active >= 0 ? new Set([active, ...neighbours[active]]) : matches.size ? matches : null;
    const surface = colour("var(--surface-1)");

    for (const e of links) {
      const style = look.edge(e);
      const on = !lit || (active >= 0 ? e.s === active || e.t === active : lit.has(e.s) && lit.has(e.t));
      ctx.globalAlpha = on ? style.alpha ?? 0.55 : 0.06;
      ctx.strokeStyle = colour(style.color);
      ctx.lineWidth = (style.width ?? 1) / Math.sqrt(view.k);
      ctx.setLineDash(style.dash ? [5 / view.k, 4 / view.k] : []);
      ctx.beginPath();
      ctx.moveTo(pos[2 * e.s], pos[2 * e.s + 1]);
      ctx.lineTo(pos[2 * e.t], pos[2 * e.t + 1]);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    for (let i = 0; i < n; i++) {
      const node = nodes[i];
      ctx.globalAlpha = !lit || lit.has(i) ? 1 : 0.18;
      ctx.beginPath();
      ctx.arc(pos[2 * i], pos[2 * i + 1], radius[i], 0, Math.PI * 2);
      ctx.fillStyle = colour(look.color(node));
      ctx.fill();
      ctx.lineWidth = 1.5 / view.k;
      ctx.strokeStyle = surface;
      ctx.stroke();
      const ring = i === selected || i === active ? "var(--text)" : node.focus ? "var(--accent)" : matches.has(i) ? "var(--warn)" : look.ring?.(node) ?? null;
      if (ring) {
        ctx.beginPath();
        ctx.arc(pos[2 * i], pos[2 * i + 1], radius[i] + 3 / view.k, 0, Math.PI * 2);
        ctx.lineWidth = 2 / view.k;
        ctx.strokeStyle = colour(ring);
        ctx.stroke();
      }
    }
    drawLabels(active, lit);
    ctx.globalAlpha = 1;
  }

  /** Labels by priority, skipping any that would overlap one already placed. */
  function drawLabels(active, lit) {
    const font = getComputedStyle(stage).fontFamily;
    const size = LABEL_PX / view.k;
    ctx.font = `500 ${size}px ${font}`;
    ctx.textBaseline = "middle";
    const order = [];
    const priority = (i) => (i === active ? 5 : i === selected ? 4 : nodes[i].focus ? 3 : matches.has(i) ? 2
      : look.always?.(nodes[i]) || (lit && lit.has(i) && lit.size <= 40) ? 1 : view.k >= 1.5 ? 0.5 : prominent.has(i) ? 0.2 : -1);
    for (let i = 0; i < n; i++) {
      const p = priority(i);
      if (p >= 0) order.push([p + radius[i] / 100, i]);
    }
    order.sort((a, b) => b[0] - a[0]);
    const placed = [];
    const overNode = (box, own) => {
      for (let j = 0; j < n; j++) {
        if (j === own) continue;
        const cx = Math.max(box[0], Math.min(pos[2 * j], box[2]));
        const cy = Math.max(box[1], Math.min(pos[2 * j + 1], box[3]));
        if (Math.hypot(cx - pos[2 * j], cy - pos[2 * j + 1]) < radius[j]) return true;
      }
      return false;
    };
    const text = colour("var(--text)");
    const halo = colour("var(--surface-1)");
    for (const [p, i] of order) {
      const label = nodes[i].label ?? "";
      if (!label) continue;
      const x = pos[2 * i] + radius[i] + 4 / view.k;
      const y = pos[2 * i + 1];
      const w = ctx.measureText(label).width;
      const box = [x, y - size * 0.7, x + w, y + size * 0.7];
      // Hovered, selected, in focus, or found: always labelled. Labelled by design: may cross
      // other circles, never other labels. Everything else needs a clear space.
      if (p < 2 && placed.some((o) => box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1])) continue;
      if (p < 1 && overNode(box, i)) continue;
      placed.push(box);
      ctx.globalAlpha = !lit || lit.has(i) ? 1 : 0.3;
      ctx.lineWidth = 3 / view.k;
      ctx.strokeStyle = halo;
      ctx.lineJoin = "round";
      ctx.strokeText(label, x, y);
      ctx.fillStyle = text;
      ctx.fillText(label, x, y);
    }
  }

  function showTip(i, sx, sy) {
    if (i < 0) { tip.hidden = true; return; }
    const lines = [nodes[i].label, ...(look.describe?.(nodes[i]) ?? [])].filter(Boolean);
    tip.replaceChildren(h("strong", {}, lines[0]), ...lines.slice(1).map((line) => h("span", {}, line)));
    tip.hidden = false;
    // Above the pointer, where it does not cover the node's own label (below, near the top).
    const x = Math.min(sx - tip.offsetWidth / 2, canvas.clientWidth - tip.offsetWidth - 8);
    const above = sy - tip.offsetHeight - 16;
    const y = above >= 8 ? above : Math.min(sy + 20, canvas.clientHeight - tip.offsetHeight - 8);
    tip.style.setProperty("left", `${Math.max(8, x)}px`);
    tip.style.setProperty("top", `${Math.max(8, y)}px`);
  }

  function select(i, notify = true) {
    selected = i;
    if (notify) on.select?.(i >= 0 ? nodes[i] : null);
    render();
  }

  canvas.addEventListener("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId);
    drag = { i: hit(event.offsetX, event.offsetY), x0: event.offsetX, y0: event.offsetY, vx: view.x, vy: view.y, moved: false };
  });
  canvas.addEventListener("pointermove", (event) => {
    if (drag) {
      const dx = event.offsetX - drag.x0;
      const dy = event.offsetY - drag.y0;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      drag.moved = true;
      userMoved = true;
      tip.hidden = true;
      if (drag.i >= 0) {
        const [wx, wy] = toWorld(event.offsetX, event.offsetY);
        pos[2 * drag.i] = wx; pos[2 * drag.i + 1] = wy;
        worker.postMessage({ type: "pin", index: drag.i, x: wx, y: wy });
      } else {
        view = { ...view, x: drag.vx + dx, y: drag.vy + dy };
      }
      render();
      return;
    }
    const i = hit(event.offsetX, event.offsetY);
    canvas.classList.toggle("is-over-node", i >= 0);
    showTip(i, event.offsetX, event.offsetY);
    if (i !== hover) { hover = i; render(); }
  });
  canvas.addEventListener("pointerup", () => {
    if (drag && !drag.moved) select(drag.i);
    drag = null;
  });
  canvas.addEventListener("pointerleave", () => {
    if (drag) return;
    tip.hidden = true;
    if (hover >= 0) { hover = -1; render(); }
  });
  canvas.addEventListener("dblclick", (event) => {
    const i = hit(event.offsetX, event.offsetY);
    if (i >= 0) on.open?.(nodes[i]);
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomBy(Math.exp(-event.deltaY * 0.0015), event.offsetX, event.offsetY);
  }, { passive: false });
  canvas.addEventListener("keydown", (event) => {
    const pan = 60;
    const moves = { ArrowLeft: [pan, 0], ArrowRight: [-pan, 0], ArrowUp: [0, pan], ArrowDown: [0, -pan] };
    if (event.key === "+" || event.key === "=") zoomBy(1.25);
    else if (event.key === "-") zoomBy(0.8);
    else if (event.key === "0") fit(true);
    else if (moves[event.key]) { userMoved = true; view = { ...view, x: view.x + moves[event.key][0], y: view.y + moves[event.key][1] }; render(); }
    else if (event.key === "Escape") select(-1);
    else return;
    event.preventDefault();
  });

  function destroy() {
    worker.postMessage({ type: "stop" });
    worker.terminate();
    resize.disconnect();
    themeWatch.disconnect();
    schemes.removeEventListener("change", retheme);
    cancelAnimationFrame(frame);
    cancelAnimationFrame(flight);
  }

  return {
    element: stage,
    destroy,
    fit: () => fit(true),
    flyTo,
    /** Select a node by id (from the side panel or list) without a callback loop. */
    select: (id) => select(index.get(id) ?? -1, false),
    /** Highlight nodes whose label contains the text; returns the matches, best first. */
    search(text) {
      const q = text.trim().toLowerCase();
      const found = q ? nodes.filter((node) => (node.label ?? "").toLowerCase().includes(q)) : [];
      found.sort((a, b) => Number(!(a.label ?? "").toLowerCase().startsWith(q)) - Number(!(b.label ?? "").toLowerCase().startsWith(q)));
      matches = new Set(found.map((node) => index.get(node.id)));
      render();
      return found;
    },
  };
}
