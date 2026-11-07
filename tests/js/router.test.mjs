// Route matching and URL building, without a browser.

import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.window = { location: { origin: "http://tessera.test", search: "" } };

const { route, match } = await import("../../src/tessera/web/static/js/core/router.js");
const { buildUrl } = await import("../../src/tessera/web/static/js/core/api.js");

route("/", () => "home");
route("/people", () => "people");
route("/people/:id", () => "person");
route("/grants/:core", () => "grant");

test("match finds the route and decodes parameters", () => {
  assert.equal(match("/").route.pattern, "/");
  assert.equal(match("/people").route.pattern, "/people");
  assert.equal(match("/people/").route.pattern, "/people");
  const found = match("/people/per_01ABC");
  assert.equal(found.route.pattern, "/people/:id");
  assert.deepEqual(found.params, { id: "per_01ABC" });
  assert.deepEqual(match("/grants/R01%20CA").params, { core: "R01 CA" });
});

test("match rejects unknown and deeper paths", () => {
  assert.equal(match("/nowhere"), null);
  assert.equal(match("/people/a/b"), null);
});

test("buildUrl skips empty values and repeats arrays", () => {
  assert.equal(buildUrl("/api/v1/people", { q: "panda", empty: "", none: null }), "/api/v1/people?q=panda");
  assert.equal(buildUrl("/api/v1/grants", { status: ["active", "", "ended"] }), "/api/v1/grants?status=active&status=ended");
  assert.equal(buildUrl("/api/v1/meta", null), "/api/v1/meta");
  assert.equal(buildUrl("/api/v1/search", { q: "a&b" }), "/api/v1/search?q=a%26b");
});
