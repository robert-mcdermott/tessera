// Per-browser preferences. Storage may be unavailable (private mode); fail quietly.

export function read(key, fallback = null) {
  try {
    const value = localStorage.getItem(`tessera.${key}`);
    return value == null ? fallback : JSON.parse(value);
  } catch {
    return fallback;
  }
}

export function write(key, value) {
  try {
    localStorage.setItem(`tessera.${key}`, JSON.stringify(value));
  } catch {
    /* preference lasts for this page only */
  }
}

export const THEMES = ["system", "light", "dark"];

export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
  else root.removeAttribute("data-theme");
}

export function currentTheme() {
  const theme = read("theme", "system");
  return THEMES.includes(theme) ? theme : "system";
}
