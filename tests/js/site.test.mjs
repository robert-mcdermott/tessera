// The documentation site's Markdown, anchors, and link check, without building the site.

import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

const { check, inline, markdown, slug } = await import("../../docs/site/build.mjs");

test("anchors match GitHub's", () => {
  assert.equal(slug("`tessera build discovery`"), "tessera-build-discovery");
  assert.equal(slug("Wave 2: Connections (v0.2.0) ◐"), "wave-2-connections-v020-");
  assert.equal(slug("Missed connections and units"), "missed-connections-and-units");
});

test("repeated headings get numbered anchors", () => {
  const { headings } = markdown("## Notes\n\ntext\n\n## Notes\n");
  assert.deepEqual(headings.map((h) => h.id), ["notes", "notes-1"]);
});

test("tables keep pipes inside code, and code is escaped", () => {
  const { html } = markdown("| A | B |\n|---|---|\n| `x | y` | <b> |\n");
  assert.match(html, /<td><code>x \| y<\/code><\/td>/);
  assert.match(html, /<td>&lt;b&gt;<\/td>/);
});

test("lists, fences, and links", () => {
  const { html } = markdown("1. one\n2. two\n\n```bash\necho <hi>\n```\n\nSee [the API](api.md#paging).", {
    link: (href) => href.replace(".md", ".html"),
  });
  assert.match(html, /<ol><li>one<\/li><li>two<\/li><\/ol>/);
  assert.match(html, /<code class="language-bash">echo &lt;hi&gt;<\/code>/);
  assert.match(html, /<a href="api.html#paging">the API<\/a>/);
  assert.equal(inline("**bold** and *it*"), "<strong>bold</strong> and <em>it</em>");
});

test("the link check finds missing pages and anchors", () => {
  const dir = mkdtempSync(join(tmpdir(), "site-"));
  writeFileSync(join(dir, "a.html"), '<h2 id="here">x</h2><a href="b.html">b</a><a href="a.html#here">ok</a><a href="a.html#gone">bad</a><a href="https://example.org">out</a>');
  const problems = check(dir);
  assert.equal(problems.length, 2);
  assert.ok(problems.some((p) => p.includes("b.html is missing")));
  assert.ok(problems.some((p) => p.includes("#gone")));
});
