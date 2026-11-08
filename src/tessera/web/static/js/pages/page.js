// Page frame: loading skeleton, errors, titles, and focus management.

import { h } from "../core/dom.js";
import { errorState, skeletonPage } from "../components/ui.js";

let product = "Tessera";
let token = 0;

export function setProduct(name) {
  product = name;
}

export function setTitle(title) {
  document.title = title ? `${title} · ${product}` : product;
}

/**
 * Render a page: show a skeleton, load data, then build the view. Later navigations win:
 * a slow earlier load never overwrites a newer page.
 */
export async function show(main, { title, load, view, quiet = false }) {
  const mine = ++token;
  const page = h("div", { class: "page" });
  if (!quiet) {
    page.append(skeletonPage());
    main.replaceChildren(page);
  }
  let data;
  try {
    data = await load();
  } catch (error) {
    if (mine !== token) return;
    page.replaceChildren(errorState(error, () => show(main, { title, load, view })));
    main.replaceChildren(page);
    setTitle(error.status === 404 ? "Not found" : "Error");
    return;
  }
  if (mine !== token) return;
  const content = view(data);
  page.replaceChildren(content);
  main.replaceChildren(page);
  setTitle(typeof title === "function" ? title(data) : title);
  if (!quiet) page.querySelector("h1")?.focus({ preventScroll: true });
}

export function isCurrent(mine) {
  return mine === token;
}
