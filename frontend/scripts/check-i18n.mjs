#!/usr/bin/env node
/**
 * Translation parity checker.
 *
 * A locale file that is missing keys does not fail loudly in the browser - it
 * falls back to English, or to the raw key, and nobody notices until a player
 * in Cairo sees `wallet.deposit_cta` on a button. This script makes that a
 * build failure instead.
 *
 * What it verifies, per locale:
 *
 *   1. Key parity with `en` - no missing keys, no strays.
 *   2. Placeholder parity - `{{amount}}` in English must appear in every
 *      translation, or the sentence renders with a hole in it.
 *   3. Plural forms match the language's CLDR categories. Arabic needs
 *      `_few` and `_many` where English needs only `_other`; Chinese needs
 *      nothing but `_other`. Comparing raw key sets would report all of that
 *      as an error, so plural keys are grouped and checked against
 *      `Intl.PluralRules` for the language.
 *   4. Nothing is left in English by accident - a long string that is
 *      byte-identical to the English source is almost always a copy-paste
 *      that was never translated.
 *   5. Rich-text tag parity - a translation of a sentence containing links
 *      must keep every `<0>`/`<1>` tag, or a link vanishes from the sentence.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const localesDir = join(here, '..', 'src', 'i18n', 'locales')

const PLURAL_SUFFIXES = ['zero', 'one', 'two', 'few', 'many', 'other']

/**
 * `zero` and `two` are excluded from the required set on purpose: no counter in
 * this app can render 0 or 2 in those groups, and i18next falls back to
 * `_other` when a category is absent. Requiring them would only add dead keys.
 * They are still *allowed* - a translator may add them - hence the subset rule.
 */
const OPTIONAL_CATEGORIES = new Set(['zero', 'two'])

/** Long enough that an exact match with English is more likely copy-paste than coincidence. */
const UNTRANSLATED_MIN_LENGTH = 18
/** A handful of acceptable exact matches: product names, protocol terms. */
const UNTRANSLATED_ALLOWANCE = 6

function flatten(value, prefix = '', out = {}) {
  for (const [key, child] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (child && typeof child === 'object' && !Array.isArray(child)) flatten(child, path, out)
    else out[path] = String(child)
  }
  return out
}

function splitPlural(key) {
  const match = key.match(/^(.*)_([a-z]+)$/)
  if (!match || !PLURAL_SUFFIXES.includes(match[2])) return null
  return { base: match[1], category: match[2] }
}

function placeholders(text) {
  return [...text.matchAll(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g)].map((m) => m[1]).sort()
}

/** Numbered rich-text tags, as `<Trans>` uses them: `<0>`, `</2>`. */
function richTags(text) {
  return [...text.matchAll(/<(\/?)(\d+)>/g)].map((m) => `${m[1]}${m[2]}`).sort()
}

function pluralCategories(locale) {
  try {
    return new Set(new Intl.PluralRules(locale).resolvedOptions().pluralCategories)
  } catch {
    return new Set(['one', 'other'])
  }
}

const files = readdirSync(localesDir).filter((f) => f.endsWith('.json')).sort()
const locales = files.map((file) => ({
  code: file.replace(/\.json$/, ''),
  strings: flatten(JSON.parse(readFileSync(join(localesDir, file), 'utf8'))),
}))

const reference = locales.find((l) => l.code === 'en')
if (!reference) {
  console.error('no en.json to compare against')
  process.exit(1)
}

const problems = []
const report = []

for (const locale of locales) {
  const isReference = locale.code === reference.code
  const problemsHere = []
  const refKeys = Object.keys(reference.strings)
  const keys = Object.keys(locale.strings)

  // --- 1. key parity, plural-aware -----------------------------------------
  const refPluralBases = new Set()
  for (const key of refKeys) {
    const plural = splitPlural(key)
    if (plural && refKeys.includes(`${plural.base}_other`)) refPluralBases.add(plural.base)
  }

  const groupOf = (key) => {
    const plural = splitPlural(key)
    return plural && refPluralBases.has(plural.base) ? plural.base : key
  }

  const refGroups = new Set(refKeys.map(groupOf))
  const groups = new Set(keys.map(groupOf))
  for (const group of refGroups) {
    if (!groups.has(group)) problemsHere.push(`missing: ${group}`)
  }
  for (const group of groups) {
    if (!refGroups.has(group)) problemsHere.push(`not in en: ${group}`)
  }

  // --- 3. plural categories match the language ------------------------------
  const categories = pluralCategories(locale.code)
  const present = new Map()
  for (const key of keys) {
    const plural = splitPlural(key)
    if (!plural || !refPluralBases.has(plural.base)) continue
    if (!present.has(plural.base)) present.set(plural.base, new Set())
    present.get(plural.base).add(plural.category)
    if (!categories.has(plural.category)) {
      problemsHere.push(
        `plural: ${key} - "${plural.category}" is not a CLDR category for ${locale.code} ` +
          `(${[...categories].sort().join(', ')})`,
      )
    }
  }
  if (!isReference) {
    for (const [base, have] of present) {
      for (const category of categories) {
        if (OPTIONAL_CATEGORIES.has(category) || have.has(category)) continue
        problemsHere.push(`plural: ${base}_${category} is missing (${locale.code} needs it)`)
      }
    }
  }

  // --- 2. placeholders ------------------------------------------------------
  for (const [key, value] of Object.entries(locale.strings)) {
    const expected = reference.strings[key]
    if (expected === undefined) continue
    const want = placeholders(expected)
    const got = placeholders(value)
    if (want.join(',') !== got.join(',')) {
      problemsHere.push(`placeholders: ${key} - en has [${want.join(', ')}], got [${got.join(', ')}]`)
    }
  }

  // --- 5. rich-text tag parity ----------------------------------------------
  // `<Trans>` renders a sentence with links inside it by substituting numbered
  // tags. If a translation drops <1>, the link silently disappears - the
  // sentence still reads, minus the thing the player was asked to agree to.
  // Placeholder checking does not cover it, because tags are not `{{...}}`.
  for (const [key, value] of Object.entries(locale.strings)) {
    const expected = reference.strings[key]
    if (expected === undefined) continue
    const want = richTags(expected)
    const got = richTags(value)
    if (want.join(',') !== got.join(',')) {
      problemsHere.push(`tags: ${key} - en has [${want.join(', ')}], got [${got.join(', ')}]`)
    }
  }

  // --- 4. accidental English -----------------------------------------------
  let englishLeft = 0
  if (!isReference) {
    for (const [key, value] of Object.entries(locale.strings)) {
      const expected = reference.strings[key]
      if (expected === undefined || expected.length < UNTRANSLATED_MIN_LENGTH) continue
      if (value === expected) englishLeft += 1
    }
    if (englishLeft > UNTRANSLATED_ALLOWANCE) {
      problemsHere.push(
        `untranslated: ${englishLeft} strings are identical to English ` +
          `(allowance ${UNTRANSLATED_ALLOWANCE})`,
      )
    }
  }

  report.push({
    code: locale.code,
    keys: keys.length,
    englishLeft,
    groups: groups.size,
    problems: problemsHere,
  })
  for (const problem of problemsHere) problems.push(`${locale.code}: ${problem}`)
}

const width = Math.max(...report.map((r) => r.code.length))
for (const row of report) {
  const status = row.problems.length ? 'FAIL' : 'ok  '
  console.log(
    `${status} ${row.code.padEnd(width)}  ${String(row.keys).padStart(4)} keys  ` +
      `${String(row.groups).padStart(4)} groups` +
      (row.code === 'en' ? '' : `  ${row.englishLeft} left in English`),
  )
}

if (problems.length) {
  console.error(`\n${problems.length} problem(s):`)
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}

console.log(`\nall ${locales.length} locales complete and consistent`)
