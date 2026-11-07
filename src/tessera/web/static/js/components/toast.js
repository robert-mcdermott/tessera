// Toasts with an optional action (e.g. Undo). Announced to screen readers.

import { h } from "../core/dom.js";

let region = null;

function container() {
  if (!region) {
    region = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(region);
  }
  return region;
}

export function toast(message, { action = null, duration = action ? 8000 : 5000 } = {}) {
  const node = h("div", { class: "toast" }, h("span", { class: "grow" }, message));
  let timer = null;
  const close = () => {
    clearTimeout(timer);
    node.remove();
  };
  if (action) {
    node.append(h("button", { type: "button", class: "btn btn-sm", onclick: async () => {
      close();
      await action.run();
    } }, action.label));
  }
  container().append(node);
  timer = setTimeout(close, duration);
  return close;
}
