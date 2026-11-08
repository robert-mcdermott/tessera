// The documentation site: theme choice, the menu on small screens, search, and copy buttons.

const root = document.documentElement;

// --- Theme ------------------------------------------------------------------------------------
function applyTheme(choice) {
  if (choice === "light" || choice === "dark") root.setAttribute("data-theme", choice);
  else root.removeAttribute("data-theme");
  try {
    if (choice === "system") localStorage.removeItem("tessera-docs-theme");
    else localStorage.setItem("tessera-docs-theme", choice);
  } catch {
    // Not remembered; it still applies to this page.
  }
  for (const button of document.querySelectorAll("[data-theme-choice]")) {
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === choice));
  }
}
for (const button of document.querySelectorAll("[data-theme-choice]")) {
  button.addEventListener("click", () => applyTheme(button.dataset.themeChoice));
}
applyTheme(root.getAttribute("data-theme") || "system");

// --- Menu on small screens --------------------------------------------------------------------
const menuButton = document.querySelector(".menu-button");
menuButton?.addEventListener("click", () => {
  const open = document.body.classList.toggle("menu-open");
  menuButton.setAttribute("aria-expanded", String(open));
});

// --- Copy buttons on code -----------------------------------------------------------------------
for (const block of document.querySelectorAll(".code")) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy";
  button.textContent = "Copy";
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(block.querySelector("code").textContent);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Select and copy";
    }
    setTimeout(() => { button.textContent = "Copy"; }, 1600);
  });
  block.append(button);
}

// --- Search -------------------------------------------------------------------------------------
const input = document.getElementById("search");
const results = document.getElementById("results");
let entries = null;
let active = -1;

async function load() {
  if (!entries) entries = await (await fetch("assets/search.json")).json();
  return entries;
}

function terms(text) {
  return text.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((t) => t.length > 1);
}

function score(entry, words) {
  const heading = entry.heading.toLowerCase();
  const text = entry.text.toLowerCase();
  let total = 0;
  for (const word of words) {
    const inHeading = heading.includes(word);
    const inText = text.includes(word);
    if (!inHeading && !inText) return 0; // every word must appear somewhere
    total += (inHeading ? 6 : 0) + (inText ? 1 + Math.min(3, text.split(word).length - 2) * 0.3 : 0);
  }
  return total + (entry.page === "index" ? -1 : 0);
}

function snippet(text, words) {
  const lower = text.toLowerCase();
  const at = Math.max(0, Math.min(...words.map((w) => (lower.indexOf(w) + 1 || Infinity) - 1)) - 40);
  const piece = (at > 0 ? "…" : "") + text.slice(at, at + 150) + (text.length > at + 150 ? "…" : "");
  return piece;
}

function render(found, words) {
  results.replaceChildren(...found.map((entry, i) => {
    const link = document.createElement("a");
    link.href = entry.url;
    link.setAttribute("role", "option");
    link.id = `result-${i}`;
    const title = document.createElement("strong");
    title.textContent = entry.heading;
    const where = document.createElement("span");
    where.className = "where";
    where.textContent = entry.title;
    const text = document.createElement("span");
    text.className = "snippet";
    text.textContent = snippet(entry.text, words);
    link.append(title, where, text);
    return link;
  }));
  if (!found.length && words.length) {
    const none = document.createElement("p");
    none.className = "none";
    none.textContent = "Nothing matches every word.";
    results.append(none);
  }
  results.hidden = !words.length;
  active = -1;
}

input?.addEventListener("input", async () => {
  const words = terms(input.value);
  const all = await load();
  const found = words.length ? all.map((e) => [score(e, words), e]).filter(([s]) => s > 0).sort((a, b) => b[0] - a[0]).slice(0, 8).map(([, e]) => e) : [];
  render(found, words);
});

input?.addEventListener("keydown", (event) => {
  const links = [...results.querySelectorAll("a")];
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (!links.length) return;
    active = (active + (event.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
    links.forEach((l, i) => l.classList.toggle("active", i === active));
    input.setAttribute("aria-activedescendant", links[active].id);
  } else if (event.key === "Enter" && links.length) {
    event.preventDefault();
    window.location.href = (links[active] || links[0]).href;
  } else if (event.key === "Escape") {
    input.value = "";
    results.hidden = true;
    input.blur();
  }
});

document.addEventListener("keydown", (event) => {
  const typing = event.target.closest?.("input, textarea, select");
  if (event.key === "/" && !typing) {
    event.preventDefault();
    input?.focus();
  }
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".search")) results.hidden = true;
});
