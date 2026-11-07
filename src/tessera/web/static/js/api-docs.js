// Self-hosted API reference: renders /api/openapi.json without third-party code.

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function resolve(spec, schema) {
  if (schema && schema.$ref) {
    const name = schema.$ref.split("/").pop();
    return { name, schema: spec.components?.schemas?.[name] ?? {} };
  }
  return { name: null, schema: schema ?? {} };
}

function typeOf(spec, schema) {
  const { name, schema: s } = resolve(spec, schema);
  if (name) return name;
  if (s.anyOf) return s.anyOf.map((x) => typeOf(spec, x)).join(" | ");
  if (s.type === "array") return `${typeOf(spec, s.items)}[]`;
  if (s.enum) return s.enum.join(" | ");
  return s.type ?? "any";
}

function parameters(spec, op) {
  const params = op.parameters ?? [];
  if (!params.length) return null;
  return el("div", {},
    el("h3", {}, "Parameters"),
    el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Name"), el("th", {}, "In"), el("th", {}, "Type"), el("th", {}, "Description"))),
      el("tbody", {}, params.map((p) => el("tr", {},
        el("td", {}, el("code", {}, p.name), p.required ? " *" : ""),
        el("td", {}, p.in),
        el("td", {}, typeOf(spec, p.schema)),
        el("td", {}, p.description ?? p.schema?.description ?? ""),
      )))));
}

function requestBody(spec, op) {
  const schema = op.requestBody?.content?.["application/json"]?.schema;
  if (!schema) return null;
  const { name, schema: s } = resolve(spec, schema);
  const rows = Object.entries(s.properties ?? {}).map(([key, prop]) => el("tr", {},
    el("td", {}, el("code", {}, key), (s.required ?? []).includes(key) ? " *" : ""),
    el("td", {}, typeOf(spec, prop)),
    el("td", {}, prop.description ?? ""),
  ));
  return el("div", {},
    el("h3", {}, `Body${name ? ` (${name})` : ""}`),
    el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Field"), el("th", {}, "Type"), el("th", {}, "Description"))),
      el("tbody", {}, rows)));
}

function render(spec) {
  const groups = new Map();
  for (const [path, methods] of Object.entries(spec.paths ?? {})) {
    for (const [method, op] of Object.entries(methods)) {
      const tag = op.tags?.[0] ?? "other";
      if (!groups.has(tag)) groups.set(tag, []);
      groups.get(tag).push({ path, method, op });
    }
  }
  const container = el("div");
  for (const [tag, endpoints] of groups) {
    const group = el("section", { class: "tag-group", "data-tag": tag }, el("h2", {}, tag));
    for (const { path, method, op } of endpoints) {
      const details = el("details", { class: "endpoint", "data-search": `${tag} ${method} ${path} ${op.summary ?? ""}`.toLowerCase() },
        el("summary", {},
          el("span", { class: `method method-${method}` }, method.toUpperCase()),
          el("span", { class: "path" }, path),
          el("span", { class: "summary-text" }, op.summary ?? "")),
        el("div", { class: "endpoint-body" },
          op.description ? el("p", {}, op.description) : null,
          parameters(spec, op),
          requestBody(spec, op)));
      group.append(details);
    }
    container.append(group);
  }
  return container;
}

async function main() {
  const target = document.querySelector('[data-bind="endpoints"]');
  try {
    const spec = await (await fetch("/api/openapi.json")).json();
    document.querySelector('[data-bind="version"]').textContent = `v${spec.info.version}`;
    target.replaceChildren(render(spec));
  } catch {
    target.replaceChildren(el("p", { class: "note" }, "The API description could not be loaded."));
    return;
  }
  document.querySelector('[data-action="filter"]').addEventListener("input", (event) => {
    const needle = event.target.value.trim().toLowerCase();
    for (const item of document.querySelectorAll(".endpoint")) {
      item.hidden = needle !== "" && !item.dataset.search.includes(needle);
    }
    for (const group of document.querySelectorAll(".tag-group")) {
      group.hidden = [...group.querySelectorAll(".endpoint")].every((e) => e.hidden);
    }
  });
}

main();
