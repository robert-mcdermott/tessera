// The sign-in page, shown instead of the app when accounts are on and nobody is signed in.

import { request } from "../core/api.js";
import { h } from "../core/dom.js";
import { setTitle } from "./page.js";

/** Where to go after signing in: a path on this site only. */
export function nextPath() {
  const next = new URLSearchParams(window.location.search).get("next");
  if (!next) return "/";
  try {
    // Resolve it as a browser would ("/\\host" and "//host" leave this site) and keep same-site only.
    const url = new URL(next, window.location.origin);
    if (url.origin !== window.location.origin || url.pathname.startsWith("/sign-in")) return "/";
    return url.pathname + url.search + url.hash;
  } catch {
    return "/";
  }
}

// What the single sign-on callback reports (/sign-in?sso=…).
const SSO_OUTCOMES = {
  cancelled: "Sign-in was cancelled.",
  expired: "That sign-in expired or was started in another browser. Try again.",
  refused: "You were signed in by your organization, but Tessera has no account or role for you. Ask an administrator.",
  failed: "Single sign-on did not work. Try again, or ask an administrator to check the server's log.",
};

export function signIn(root, meta) {
  setTitle("Sign in");
  const product = meta.product;
  const org = meta.organization;
  const sso = meta.mode?.sso;
  const outcome = SSO_OUTCOMES[new URLSearchParams(window.location.search).get("sso")];
  const username = h("input", { class: "input", id: "username", name: "username", autocomplete: "username",
    autocapitalize: "none", spellcheck: "false", required: true });
  const password = h("input", { class: "input", id: "password", name: "password", type: "password",
    autocomplete: "current-password", required: true });
  const error = h("p", { class: "field-error", role: "alert", hidden: true });
  const submit = h("button", { type: "submit", class: "btn btn-primary btn-block" }, "Sign in");
  const form = h("form", { class: "stack", novalidate: true, onsubmit: async (event) => {
    event.preventDefault();
    if (!username.value.trim() || !password.value) {
      error.textContent = "Enter your username and password.";
      error.hidden = false;
      (username.value.trim() ? password : username).focus();
      return;
    }
    submit.disabled = true;
    submit.textContent = "Signing in…";
    try {
      await request("POST", "/api/v1/session", { body: { username: username.value.trim(), password: password.value } });
    } catch (failure) {
      error.textContent = failure.status === 0 ? "The server could not be reached."
        : failure.detail || "That did not work.";
      error.hidden = false;
      submit.disabled = false;
      submit.textContent = "Sign in";
      password.value = "";
      password.focus();
      return;
    }
    window.location.assign(nextPath()); // a full load, so the app starts with the session
  } },
  h("div", { class: "field" }, h("label", { for: "username" }, "Username"), username),
  h("div", { class: "field" }, h("label", { for: "password" }, "Password"), password),
  error,
  submit);

  // With single sign-on, its button comes first and the password form folds away (for local
  // accounts, such as an administrator's).
  const ssoNotice = outcome ? h("p", { class: "field-error", role: "alert" }, outcome) : null;
  const methods = sso
    ? h("div", { class: "stack" },
      ssoNotice,
      h("a", { class: "btn btn-primary btn-block", href: `/api/v1/auth/sso/start?next=${encodeURIComponent(nextPath())}` }, sso.label),
      h("details", { class: "signin-password" },
        h("summary", {}, "Sign in with a password"),
        h("div", { class: "stack signin-password-form" }, form)))
    : form;

  const brand = product.logo
    ? h("div", { class: "signin-brand" },
      h("img", { class: "brand-logo brand-logo-light", src: product.logo.light, alt: product.name }),
      h("img", { class: "brand-logo brand-logo-dark", src: product.logo.dark, alt: "" }))
    : h("div", { class: "signin-brand" },
      h("img", { class: "brand-mark", src: "/img/tessera-mark.svg", alt: "", width: 34, height: 34 }),
      h("span", { class: "brand-text" }, h("span", {}, product.name), h("span", { class: "brand-org" }, org.short_name || org.name)));

  root.replaceChildren(h("main", { class: "signin", id: "main" },
    h("section", { class: "card signin-card", "aria-labelledby": "signin-title" },
      h("div", { class: "card-body stack-lg" },
        brand,
        h("div", { class: "stack-xs" },
          h("h1", { id: "signin-title" }, "Sign in"),
          h("p", { class: "muted" }, org.name)),
        methods)),
    h("p", { class: "subtle signin-help" }, sso ? "Trouble signing in? Ask an administrator."
      : "Forgotten your password? Ask an administrator to reset it.")));
  if (!sso) username.focus();
}
