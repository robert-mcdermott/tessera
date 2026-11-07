// API client: envelopes, problem details, and a small time-limited GET cache.

export class ApiError extends Error {
  constructor(status, title, detail, body) {
    super(detail || title || `HTTP ${status}`);
    this.status = status;
    this.title = title;
    this.detail = detail;
    this.body = body;
  }
}

const cache = new Map();
const TTL_MS = 30_000;

export function buildUrl(path, params = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value == null || value === "") continue;
    if (Array.isArray(value)) {
      for (const v of value) if (v != null && v !== "") url.searchParams.append(key, v);
    } else {
      url.searchParams.set(key, value);
    }
  }
  return url.pathname + url.search;
}

async function request(method, path, { params, body, signal } = {}) {
  const url = buildUrl(path, params);
  let response;
  try {
    response = await fetch(url, {
      method,
      signal,
      headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(0, "Network error", "The server could not be reached.");
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new ApiError(response.status, payload?.title, payload?.detail, payload);
  }
  return payload;
}

export async function get(path, params, { fresh = false, signal } = {}) {
  const key = buildUrl(path, params);
  const hit = cache.get(key);
  if (!fresh && hit && Date.now() - hit.at < TTL_MS) return hit.value;
  const value = await request("GET", path, { params, signal });
  cache.set(key, { at: Date.now(), value });
  return value;
}

export async function post(path, body) {
  const value = await request("POST", path, { body });
  cache.clear(); // decisions change counts and lists everywhere
  return value;
}

export function invalidate() {
  cache.clear();
}
