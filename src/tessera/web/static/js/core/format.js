// Formatting helpers. Pure functions (unit-tested with node --test).

const LOCALE = undefined; // the viewer's locale

export function number(value) {
  if (value == null || Number.isNaN(value)) return "–";
  return new Intl.NumberFormat(LOCALE).format(value);
}

export function compact(value) {
  if (value == null || Number.isNaN(value)) return "–";
  return new Intl.NumberFormat(LOCALE, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function money(value, { compactForm = false } = {}) {
  if (value == null || Number.isNaN(value)) return "–";
  return new Intl.NumberFormat(LOCALE, {
    style: "currency", currency: "USD", maximumFractionDigits: 0,
    ...(compactForm ? { notation: "compact", maximumFractionDigits: 1 } : {}),
  }).format(value);
}

export function percent(value, digits = 0) {
  if (value == null || Number.isNaN(value)) return "–";
  return `${(value * 100).toFixed(digits)}%`;
}

export function plural(count, singular, pluralForm = `${singular}s`) {
  return `${number(count)} ${count === 1 ? singular : pluralForm}`;
}

export function date(value) {
  if (!value) return "–";
  const d = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  if (Number.isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat(LOCALE, { year: "numeric", month: "short", day: "numeric" }).format(d);
}

export function relative(value, now = new Date()) {
  if (!value) return "never";
  const then = new Date(value);
  if (Number.isNaN(then.getTime())) return value;
  const seconds = Math.round((then.getTime() - now.getTime()) / 1000);
  const units = [["year", 31_536_000], ["month", 2_592_000], ["week", 604_800], ["day", 86_400],
    ["hour", 3_600], ["minute", 60], ["second", 1]];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size || unit === "second") {
      return new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto" }).format(Math.round(seconds / size), unit);
    }
  }
  return value;
}

export function initials(name) {
  const words = (name ?? "").replace(/[^\p{L}\s'-]/gu, " ").split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  const first = words[0][0];
  const last = words.length > 1 ? words[words.length - 1][0] : "";
  return (first + last).toUpperCase();
}

/** A stable small integer from a string (for avatar colors). */
export function hue(seed, buckets = 6) {
  let hash = 2166136261;
  for (const ch of seed ?? "") {
    hash ^= ch.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return Math.abs(hash) % buckets;
}

export function titleCase(text) {
  return (text ?? "").toLowerCase().replace(/(^|[\s_-])(\p{L})/gu, (_, sep, ch) => sep + ch.toUpperCase())
    .replaceAll("_", " ");
}

/** Registry enums such as ACTIVE_NOT_RECRUITING -> "Active, not recruiting". */
export function enumLabel(value) {
  if (!value) return "–";
  const special = { NA: "Not applicable", EARLY_PHASE1: "Early phase 1" };
  if (special[value]) return special[value];
  const text = value.toLowerCase().replaceAll("_", " ");
  const phase = text.match(/^phase(\d)$/);
  if (phase) return `Phase ${phase[1]}`;
  return text.charAt(0).toUpperCase() + text.slice(1).replace(" not ", ", not ");
}

/** A likelihood for display. The model never reaches certainty, so neither does the label. */
export function scoreLabel(score) {
  if (score == null) return "";
  if (score >= 0.995) return ">99%";
  if (score > 0 && score < 0.005) return "<1%";
  return `${Math.round(score * 100)}%`;
}

/**
 * Source names are sometimes all capitals ("MARIA M SMITH"). Show those in name case;
 * names that already mix cases are left exactly as the source wrote them.
 */
export function personName(name) {
  if (!name || /\p{Ll}/u.test(name) || !/\p{Lu}{2}/u.test(name)) return name ?? "";
  return name.toLowerCase()
    .replace(/(^|[\s'’-])(\p{L})/gu, (_, sep, ch) => sep + ch.toUpperCase())
    .replace(/\b(Mc)(\p{L})/gu, (_, mc, ch) => mc + ch.toUpperCase());
}

const HEADING = /^\s*(project\s+summary(\s*\/\s*abstract)?|abstract|summary|description)\s*[:.–-]?\s*\n/i;

/**
 * Split source text into paragraphs. Registry and grant text is often hard-wrapped, so
 * a single line break inside a sentence becomes a space; a blank line, or a break after
 * sentence-ending punctuation, starts a new paragraph. A leading "Abstract" heading goes.
 */
export function paragraphs(text) {
  if (!text) return [];
  const body = text.replace(/\r\n?/g, "\n").replace(HEADING, "");
  return body.split(/\n\s*\n|(?<=[.?!:])\s*\n/)
    .map((part) => part.replace(/\s*\n\s*/g, " ").replace(/[ \t]+/g, " ").trim())
    .filter(Boolean);
}
