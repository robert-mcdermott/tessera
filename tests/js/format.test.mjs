// Unit tests for the pure helpers in the web app. Run with: node --test tests/js
// (tests/test_web.py runs them too when Node.js is installed.)

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  enumLabel, hue, initials, paragraphs, personName, plural, relative, scoreLabel, titleCase,
} from "../../src/tessera/web/static/js/core/format.js";

test("scoreLabel never claims certainty", () => {
  assert.equal(scoreLabel(0.9999), ">99%");
  assert.equal(scoreLabel(0.995), ">99%");
  assert.equal(scoreLabel(0.994), "99%");
  assert.equal(scoreLabel(0.5), "50%");
  assert.equal(scoreLabel(0.001), "<1%");
  assert.equal(scoreLabel(0), "0%");
  assert.equal(scoreLabel(null), "");
});

test("plural", () => {
  assert.equal(plural(1, "study", "studies"), "1 study");
  assert.equal(plural(2, "study", "studies"), "2 studies");
  assert.equal(plural(0, "grant"), "0 grants");
});

test("initials and hue", () => {
  assert.equal(initials("Ada Lovelace"), "AL");
  assert.equal(initials("Cher"), "C");
  assert.equal(initials(""), "?");
  assert.equal(hue("same"), hue("same"));
  assert.ok(hue("anything", 6) >= 0 && hue("anything", 6) < 6);
});

test("enumLabel and titleCase", () => {
  assert.equal(enumLabel("ACTIVE_NOT_RECRUITING"), "Active, not recruiting");
  assert.equal(enumLabel("PHASE2"), "Phase 2");
  assert.equal(enumLabel("EARLY_PHASE1"), "Early phase 1");
  assert.equal(enumLabel("NA"), "Not applicable");
  assert.equal(enumLabel(null), "–");
  assert.equal(titleCase("circadian_rhythm"), "Circadian Rhythm");
});

test("personName fixes all-capital source names only", () => {
  assert.equal(personName("MARIA M SMITH"), "Maria M Smith");
  assert.equal(personName("JOHN O'NEIL-MCDONALD"), "John O'Neil-McDonald");
  assert.equal(personName("de la Cruz"), "de la Cruz");
  assert.equal(personName("Bing Ren"), "Bing Ren");
  assert.equal(personName(null), "");
});

test("paragraphs reflows hard-wrapped text", () => {
  const text = "Abstract\nUnderstanding cells across\nthe brain is a\nfirst step.\nAt present, little\nis known.\n\nAims follow.";
  assert.deepEqual(paragraphs(text), [
    "Understanding cells across the brain is a first step.",
    "At present, little is known.",
    "Aims follow.",
  ]);
  assert.deepEqual(paragraphs("PROJECT SUMMARY/ABSTRACT\nOne line."), ["One line."]);
  assert.deepEqual(paragraphs(""), []);
  assert.deepEqual(paragraphs(null), []);
});

test("relative time", () => {
  const now = new Date("2026-01-10T12:00:00Z");
  assert.match(relative("2026-01-10T11:00:00Z", now), /hour/);
  assert.match(relative("2026-01-03T12:00:00Z", now), /week/);
  assert.equal(relative(null, now), "never");
});
