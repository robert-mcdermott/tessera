// Tiny DOM helpers. Data is only ever inserted as text nodes, never as HTML.

import { icon } from "./icons.js";

const MARK_START = "";
const MARK_END = "";

/**
 * Create an element. attrs: class, dataset via data-*, aria-*, on<Event> handlers,
 * and any other attribute. Children may be nodes, strings, numbers, arrays, or null.
 */
export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs ?? {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "style") {
      throw new Error("Inline styles are not allowed (CSP); use classes or CSS variables.");
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function frag(...children) {
  return append(document.createDocumentFragment(), children);
}

export function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value != null) node.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child == null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** Render text containing search-highlight markers as text and <mark> nodes. */
export function highlighted(text) {
  if (!text) return document.createTextNode("");
  if (!text.includes(MARK_START)) return document.createTextNode(text);
  const out = document.createDocumentFragment();
  for (const part of text.split(MARK_START)) {
    const end = part.indexOf(MARK_END);
    if (end === -1) {
      out.append(document.createTextNode(part));
    } else {
      out.append(h("mark", {}, part.slice(0, end)));
      out.append(document.createTextNode(part.slice(end + 1)));
    }
  }
  return out;
}

/** Plain text with the highlight markers removed. */
export function plain(text) {
  return (text ?? "").replaceAll(MARK_START, "").replaceAll(MARK_END, "");
}

export function link(href, attrs = {}, ...children) {
  return h("a", { href, ...attrs }, ...children);
}

export function iconButton(name, label, attrs = {}) {
  return h("button", { type: "button", class: "icon-button", "aria-label": label, title: label, ...attrs },
    icon(name));
}

export function replace(target, ...children) {
  target.replaceChildren();
  append(target, children);
  return target;
}

export { icon };
