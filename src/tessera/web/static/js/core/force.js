// A small force-directed layout: repulsion (approximated on a grid), link springs, collision,
// and a gentle pull to the centre. Pure and deterministic, so it runs in a Web Worker and in
// unit tests alike. Positions start on a sunflower spiral; the layout cools and stops.

const GOLDEN = Math.PI * (3 - Math.sqrt(5));

export const DEFAULTS = {
  charge: -80, // repulsion per unit of node mass (negative pushes apart)
  cell: 90, // grid cell size: nodes in neighbouring cells repel exactly, farther ones by cell
  gravity: 0.035, // pull towards the origin, which keeps separate groups in view
  collide: 0.7, // how firmly overlapping nodes are pushed apart
  padding: 2, // space kept between node edges
  velocityDecay: 0.42,
  alphaMin: 0.002,
  alphaDecay: 1 - Math.pow(0.002, 1 / 280), // about 280 ticks from 1 to alphaMin
};

export class Simulation {
  /**
   * nodes: [{ r, mass?, x?, y? }]; links: [{ source, target, distance?, strength? }] by index.
   */
  constructor(nodes, links, options = {}) {
    this.o = { ...DEFAULTS, ...options };
    const n = nodes.length;
    this.n = n;
    this.x = new Float64Array(n);
    this.y = new Float64Array(n);
    this.vx = new Float64Array(n);
    this.vy = new Float64Array(n);
    this.r = new Float64Array(n);
    this.mass = new Float64Array(n);
    this.fixed = new Uint8Array(n);
    nodes.forEach((node, i) => {
      const spiral = 12 * Math.sqrt(0.5 + i);
      this.x[i] = Number.isFinite(node.x) ? node.x : spiral * Math.cos(i * GOLDEN);
      this.y[i] = Number.isFinite(node.y) ? node.y : spiral * Math.sin(i * GOLDEN);
      this.r[i] = node.r ?? 6;
      this.mass[i] = node.mass ?? 1;
    });
    const degree = new Float64Array(n);
    this.links = links
      .filter((l) => l.source !== l.target && l.source >= 0 && l.target >= 0 && l.source < n && l.target < n)
      .map((l) => ({ s: l.source, t: l.target, distance: l.distance ?? 60, strength: l.strength ?? 0.4 }));
    for (const l of this.links) { degree[l.s] += 1; degree[l.t] += 1; }
    for (const l of this.links) l.bias = degree[l.s] / (degree[l.s] + degree[l.t]);
    this.alpha = 1;
    this.alphaTarget = 0;
  }

  get done() {
    return this.alpha < this.o.alphaMin && this.alphaTarget === 0;
  }

  reheat(alpha = 0.3) {
    this.alpha = Math.max(this.alpha, alpha);
  }

  pin(i, x, y) {
    this.fixed[i] = 1;
    this.x[i] = x; this.y[i] = y;
    this.vx[i] = 0; this.vy[i] = 0;
  }

  unpin(i) {
    this.fixed[i] = 0;
  }

  /** Interleaved [x0, y0, x1, y1, ...] for drawing. */
  positions(out = new Float32Array(this.n * 2)) {
    for (let i = 0; i < this.n; i++) { out[2 * i] = this.x[i]; out[2 * i + 1] = this.y[i]; }
    return out;
  }

  tick(count = 1) {
    for (let k = 0; k < count; k++) {
      this.alpha += (this.alphaTarget - this.alpha) * this.o.alphaDecay;
      this.springs();
      this.repel();
      this.centre();
      this.move();
      this.separate();
    }
    return this;
  }

  springs() {
    const { x, y, vx, vy, alpha } = this;
    for (const l of this.links) {
      let dx = x[l.t] + vx[l.t] - x[l.s] - vx[l.s];
      let dy = y[l.t] + vy[l.t] - y[l.s] - vy[l.s];
      let d = Math.hypot(dx, dy);
      if (d < 1e-6) { dx = jiggle(l.s, l.t); dy = jiggle(l.t, l.s); d = Math.hypot(dx, dy); }
      const pull = ((d - l.distance) / d) * alpha * l.strength;
      dx *= pull; dy *= pull;
      vx[l.t] -= dx * l.bias; vy[l.t] -= dy * l.bias;
      vx[l.s] += dx * (1 - l.bias); vy[l.s] += dy * (1 - l.bias);
    }
  }

  /** Nodes in neighbouring cells repel one another exactly; farther cells act as one mass. */
  repel() {
    const { x, y, vx, vy, mass, n, alpha } = this;
    const { charge, cell } = this.o;
    const grid = this.grid();
    for (let i = 0; i < n; i++) {
      const ci = Math.floor(x[i] / cell);
      const cj = Math.floor(y[i] / cell);
      let fx = 0;
      let fy = 0;
      for (const c of grid.cells.values()) {
        const near = Math.abs(c.i - ci) <= 1 && Math.abs(c.j - cj) <= 1;
        if (near) {
          for (const j of c.members) {
            if (j === i) continue;
            [fx, fy] = push(fx, fy, x[j] - x[i], y[j] - y[i], mass[j], i, j);
          }
        } else {
          [fx, fy] = push(fx, fy, c.cx - x[i], c.cy - y[i], c.mass, i, -1);
        }
      }
      vx[i] += fx * charge * alpha;
      vy[i] += fy * charge * alpha;
    }
  }

  grid() {
    const { x, y, mass, n } = this;
    const { cell } = this.o;
    const cells = new Map();
    for (let i = 0; i < n; i++) {
      const ci = Math.floor(x[i] / cell);
      const cj = Math.floor(y[i] / cell);
      const key = `${ci},${cj}`;
      let c = cells.get(key);
      if (!c) cells.set(key, (c = { i: ci, j: cj, members: [], mass: 0, sx: 0, sy: 0 }));
      c.members.push(i);
      c.mass += mass[i];
      c.sx += x[i] * mass[i];
      c.sy += y[i] * mass[i];
    }
    for (const c of cells.values()) { c.cx = c.sx / c.mass; c.cy = c.sy / c.mass; }
    return { cells };
  }

  centre() {
    const { x, y, vx, vy, n, alpha } = this;
    const g = this.o.gravity * alpha;
    for (let i = 0; i < n; i++) { vx[i] -= x[i] * g; vy[i] -= y[i] * g; }
  }

  move() {
    const { x, y, vx, vy, n, fixed } = this;
    const keep = 1 - this.o.velocityDecay;
    for (let i = 0; i < n; i++) {
      if (fixed[i]) { vx[i] = 0; vy[i] = 0; continue; }
      vx[i] *= keep; vy[i] *= keep;
      x[i] += vx[i]; y[i] += vy[i];
    }
  }

  /** Overlapping circles are pushed apart, so labels and hover targets stay distinct. */
  separate() {
    const { x, y, r, n, fixed } = this;
    const { collide, padding } = this.o;
    let largest = 0;
    for (let i = 0; i < n; i++) largest = Math.max(largest, r[i]);
    const size = Math.max(1, 2 * largest + padding);
    const cells = new Map();
    for (let i = 0; i < n; i++) {
      const key = `${Math.floor(x[i] / size)},${Math.floor(y[i] / size)}`;
      (cells.get(key) ?? cells.set(key, []).get(key)).push(i);
    }
    for (let i = 0; i < n; i++) {
      const ci = Math.floor(x[i] / size);
      const cj = Math.floor(y[i] / size);
      for (let a = ci - 1; a <= ci + 1; a++) {
        for (let b = cj - 1; b <= cj + 1; b++) {
          for (const j of cells.get(`${a},${b}`) ?? []) {
            if (j <= i) continue;
            let dx = x[j] - x[i];
            let dy = y[j] - y[i];
            const min = r[i] + r[j] + padding;
            let d = Math.hypot(dx, dy);
            if (d >= min) continue;
            if (d < 1e-6) { dx = jiggle(i, j); dy = jiggle(j, i); d = Math.hypot(dx, dy); }
            const overlap = ((min - d) / d) * collide * 0.5;
            const share = fixed[i] ? 1 : fixed[j] ? 0 : 0.5;
            if (!fixed[j]) { x[j] += dx * overlap * 2 * share; y[j] += dy * overlap * 2 * share; }
            if (!fixed[i]) { x[i] -= dx * overlap * 2 * (1 - share); y[i] -= dy * overlap * 2 * (1 - share); }
          }
        }
      }
    }
  }
}

function push(fx, fy, dx, dy, m, i, j) {
  let d2 = dx * dx + dy * dy;
  if (d2 < 1e-6) { dx = jiggle(i, j); dy = jiggle(j, i); d2 = dx * dx + dy * dy; }
  d2 = Math.max(d2, 25); // no singular kicks between nodes that nearly coincide
  return [fx + (dx * m) / d2, fy + (dy * m) / d2];
}

/** A tiny, deterministic nudge for nodes that sit exactly on top of one another. */
function jiggle(a, b) {
  return ((((a * 7919 + b * 104729) % 1000) / 1000) - 0.5) * 1e-3 || 1e-4;
}

/** Run to rest in one go (for reduced motion, and tests). */
export function settle(sim, limit = 2000) {
  let ticks = 0;
  while (!sim.done && ticks < limit) { sim.tick(); ticks++; }
  return ticks;
}
