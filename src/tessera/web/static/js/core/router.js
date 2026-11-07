// History-API router. The URL is the state: pages read filters from the query string.

const routes = [];
let notFound = null;
let current = null;
const listeners = new Set();

/** Register a route such as "/people/:id". */
export function route(pattern, load) {
  const keys = [];
  const source = pattern
    .replace(/[.+?^${}()|[\]\\]/g, "\\$&")
    .replace(/:([a-zA-Z_]+)/g, (_, key) => {
      keys.push(key);
      return "([^/]+)";
    });
  routes.push({ pattern, regex: new RegExp(`^${source}/?$`), keys, load });
}

export function fallback(load) {
  notFound = load;
}

export function match(pathname) {
  for (const r of routes) {
    const found = r.regex.exec(pathname);
    if (found) {
      const params = {};
      r.keys.forEach((key, i) => {
        params[key] = decodeURIComponent(found[i + 1]);
      });
      return { route: r, params };
    }
  }
  return null;
}

export function query() {
  return new URLSearchParams(window.location.search);
}

/** Read a query parameter that may repeat (?x=a&x=b) or be comma-separated. */
export function queryList(name) {
  return query().getAll(name).flatMap((v) => v.split(",")).map((v) => v.trim()).filter(Boolean);
}

export function navigate(href, { replace = false, scroll = true } = {}) {
  const url = new URL(href, window.location.origin);
  if (url.origin !== window.location.origin) {
    window.location.assign(url.href);
    return;
  }
  const next = url.pathname + url.search + url.hash;
  if (replace) history.replaceState({}, "", next);
  else history.pushState({}, "", next);
  render({ scroll, samePage: current && current.pathname === url.pathname });
}

/** Update query parameters in place (filters, tabs, sort), without adding history noise. */
export function setQuery(updates, { replace = true } = {}) {
  const params = query();
  for (const [key, value] of Object.entries(updates)) {
    params.delete(key);
    if (Array.isArray(value)) value.forEach((v) => params.append(key, v));
    else if (value != null && value !== "") params.set(key, value);
  }
  const search = params.toString();
  navigate(window.location.pathname + (search ? `?${search}` : ""), { replace, scroll: false });
}

export function onNavigate(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export async function render({ scroll = true, samePage = false } = {}) {
  const pathname = window.location.pathname;
  const found = match(pathname);
  current = { pathname, ...(found ?? {}) };
  for (const listener of listeners) listener(current, { samePage });
  const load = found ? found.route.load : notFound;
  if (!load) return;
  await load(found?.params ?? {}, { samePage });
  if (scroll && !samePage) window.scrollTo(0, 0);
}

export function start() {
  window.addEventListener("popstate", () => render({ scroll: false }));
  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = event.target.closest("a[href]");
    if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
    const url = new URL(anchor.href, window.location.origin);
    if (url.origin !== window.location.origin || url.pathname.startsWith("/api/")) return;
    event.preventDefault();
    navigate(url.pathname + url.search + url.hash);
  });
  return render();
}
