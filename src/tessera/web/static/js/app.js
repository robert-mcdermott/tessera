// Tessera placeholder page. The full single-page app arrives in wave 1, slice 5.

const THEMES = ["system", "light", "dark"];
const THEME_KEY = "tessera.theme";

function readTheme() {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    return THEMES.includes(stored) ? stored : "system";
  } catch {
    return "system";
  }
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  const label = document.querySelector(".theme-toggle-label");
  if (label) label.textContent = `Theme: ${theme}`;
}

function bind(name, value) {
  for (const el of document.querySelectorAll(`[data-bind="${name}"]`)) el.textContent = value;
}

async function getJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  return { ok: response.ok, body: await response.json() };
}

async function loadMeta() {
  try {
    const { body } = await getJson("/api/v1/meta");
    const { product, organization, mode } = body.data;
    bind("product-name", product.name);
    bind("tagline", product.tagline);
    bind("organization", organization.name);
    bind("version", product.version);
    bind("mode", mode.accounts ? "Accounts" : "Single operator");
    document.title = `${product.name} · ${organization.short_name || organization.name}`;
    const footer = document.querySelector('[data-bind="powered-by"]');
    if (footer) footer.hidden = !product.show_powered_by;
  } catch {
    bind("version", "unavailable");
  }
}

async function loadHealth() {
  const pill = document.querySelector('[data-bind="health"]');
  try {
    const { ok, body } = await getJson("/api/v1/health");
    pill.textContent = ok ? "Healthy" : "Degraded";
    pill.dataset.state = ok ? "ok" : "warn";
  } catch {
    pill.textContent = "Unreachable";
    pill.dataset.state = "warn";
  }
}

let theme = readTheme();
applyTheme(theme);
document.querySelector('[data-action="toggle-theme"]')?.addEventListener("click", () => {
  theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
  applyTheme(theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
});

loadMeta();
loadHealth();
