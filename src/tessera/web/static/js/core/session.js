// Who is signed in, and what they may do (from GET /api/v1/session). In single-operator
// mode the operator may do everything.

let current = null;

export function setSession(value) {
  current = value;
}

export function session() {
  return current;
}

/** permission: "admin", "curate_all", "curate_any", or "review". */
export function can(permission) {
  return Boolean(current?.permissions?.[permission]);
}

export function csrfToken() {
  return current?.csrf_token ?? null;
}

export function setCsrfToken(token) {
  if (current) current = { ...current, csrf_token: token };
}
