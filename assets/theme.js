// Applies the reader's chosen theme before the page paints, so it never flashes the other one.
(() => {
  try {
    const theme = localStorage.getItem("tessera-docs-theme");
    if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
  } catch {
    // Storage may be unavailable (private windows); the system theme applies.
  }
})();
