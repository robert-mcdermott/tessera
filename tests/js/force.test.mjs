// The force layout, without a browser: it settles, it is deterministic, and it keeps
// connected nodes together and every node apart.

import assert from "node:assert/strict";
import { test } from "node:test";

const { Simulation, settle } = await import("../../src/tessera/web/static/js/core/force.js");

/** Two groups of ten, densely linked inside, with one bridge between them. */
function twoGroups() {
  const nodes = Array.from({ length: 20 }, () => ({ r: 6 }));
  const links = [];
  for (const base of [0, 10]) {
    for (let i = 0; i < 10; i++) for (let j = i + 1; j < 10; j++) links.push({ source: base + i, target: base + j, distance: 40 });
  }
  links.push({ source: 0, target: 10, distance: 40, strength: 0.05 });
  return { nodes, links };
}

const dist = (p, a, b) => Math.hypot(p[2 * a] - p[2 * b], p[2 * a + 1] - p[2 * b + 1]);

test("the layout settles with finite positions", () => {
  const { nodes, links } = twoGroups();
  const sim = new Simulation(nodes, links);
  const ticks = settle(sim);
  assert.ok(sim.done && ticks < 2000, `took ${ticks} ticks`);
  for (const v of sim.positions()) assert.ok(Number.isFinite(v));
});

test("the same input gives the same layout", () => {
  const run = () => {
    const { nodes, links } = twoGroups();
    const sim = new Simulation(nodes, links);
    settle(sim);
    return sim.positions();
  };
  assert.deepEqual(run(), run());
});

test("linked nodes end up closer than unlinked ones", () => {
  const sim = new Simulation(...Object.values(twoGroups()));
  settle(sim);
  const p = sim.positions();
  let inside = 0;
  let across = 0;
  for (let i = 1; i < 10; i++) { inside += dist(p, 1, i); across += dist(p, 1, 10 + i); }
  assert.ok(inside * 1.5 < across, `inside ${inside.toFixed(0)}, across ${across.toFixed(0)}`);
});

test("nodes do not overlap once settled", () => {
  const nodes = Array.from({ length: 60 }, (_, i) => ({ r: 4 + (i % 5) }));
  const links = nodes.slice(1).map((_, i) => ({ source: 0, target: i + 1, distance: 10 }));
  const sim = new Simulation(nodes, links);
  settle(sim);
  const p = sim.positions();
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      assert.ok(dist(p, i, j) >= (nodes[i].r + nodes[j].r) * 0.9, `${i} and ${j} overlap`);
    }
  }
});

test("a pinned node stays where it was put", () => {
  const sim = new Simulation(...Object.values(twoGroups()));
  sim.pin(3, 500, -200);
  settle(sim);
  const p = sim.positions();
  assert.equal(p[6], 500);
  assert.equal(p[7], -200);
  sim.unpin(3);
  sim.reheat();
  assert.ok(!sim.done);
});

test("150 nodes settle quickly", () => {
  const nodes = Array.from({ length: 150 }, (_, i) => ({ r: 5 + (i % 7) }));
  const links = [];
  for (let i = 1; i < 150; i++) links.push({ source: i, target: Math.floor((i * 7) % i), distance: 50 });
  const start = performance.now();
  settle(new Simulation(nodes, links));
  const took = performance.now() - start;
  assert.ok(took < 1500, `took ${took.toFixed(0)} ms`);
});
