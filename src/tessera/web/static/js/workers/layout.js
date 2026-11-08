// Runs the force layout off the main thread, so the page stays responsive while it settles.
// Messages in: start {nodes, links, options, reduced}, pin {index, x, y}, unpin {index}, stop.
// Messages out: positions {positions: Float32Array, done}.

import { Simulation, settle } from "../core/force.js";

const WARM = 40; // ticks run before the first frame, so the opening chaos is never drawn
const PER_FRAME = 3;

let sim = null;
let timer = null;
let reduced = false;

function post() {
  const positions = sim.positions();
  self.postMessage({ type: "positions", positions, done: sim.done }, [positions.buffer]);
}

function step() {
  timer = null;
  if (!sim) return;
  sim.tick(PER_FRAME);
  post();
  if (!sim.done) timer = setTimeout(step, 16);
}

function run() {
  clearTimeout(timer);
  if (reduced) {
    settle(sim); // reduced motion: only the settled layout is ever shown
    post();
    return;
  }
  step();
}

self.onmessage = ({ data }) => {
  if (data.type === "start") {
    sim = new Simulation(data.nodes, data.links, data.options);
    reduced = Boolean(data.reduced);
    if (!reduced) sim.tick(WARM);
    run();
  } else if (data.type === "pin" && sim) {
    sim.pin(data.index, data.x, data.y);
    if (reduced) {
      post();
    } else {
      sim.reheat(0.12);
      if (!timer) run();
    }
  } else if (data.type === "unpin" && sim) {
    sim.unpin(data.index);
  } else if (data.type === "settle" && sim) {
    sim.reheat(0.3);
    run();
  } else if (data.type === "stop") {
    clearTimeout(timer);
    sim = null;
  }
};
