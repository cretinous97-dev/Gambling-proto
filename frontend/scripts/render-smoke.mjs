/**
 * Render smoke test: does the built site actually put something on the screen?
 *
 * A green `vite build` proves the code compiles. It does not prove the app
 * renders - an undefined import, a hook used outside a provider, or a bad
 * destructure all build cleanly and produce a blank page in production. That
 * failure mode looks identical to "the deployment is broken" from the outside,
 * and it is the one that wastes an afternoon.
 *
 * So this loads the real built bundle in jsdom with the API stubbed, and
 * asserts that specific, meaningful text is present. It runs offline, in a
 * second, and it fails loudly.
 *
 *   node scripts/render-smoke.mjs
 */
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { JSDOM } from 'jsdom'

const HERE = dirname(fileURLToPath(import.meta.url))
const DIST = resolve(HERE, '..', 'dist')

let passed = 0
const failures = []

function check(label, ok, detail = '') {
  if (ok) {
    passed += 1
    console.log(`  PASS  ${label}${detail ? `  ${detail}` : ''}`)
  } else {
    failures.push(label)
    console.log(`  FAIL  ${label}${detail ? `  ${detail}` : ''}`)
  }
}

// --- the API, stubbed -------------------------------------------------------
const CONFIG = {
  name: 'Naktsang Casino',
  environment: 'test',
  provider: { provider: 'sandbox', mode: 'simulation', ok: true, simulation: true },
  limits: {
    min_deposit: 500, max_deposit: 1_000_000,
    min_withdrawal: 1000, max_withdrawal: 500_000,
    kyc_required_above: 100_000, min_bet: 10, max_bet: 200_000,
  },
  jurisdiction: { mode: 'allow_all', blocklist: [], restricted: [], open_to_every_country: true },
  i18n: {
    settlement_currency: 'USD',
    default_locale: 'en',
    locales: [
      { code: 'en', native: 'English', dir: 'ltr' },
      { code: 'ar', native: 'العربية', dir: 'rtl' },
    ],
    currencies: [
      { code: 'USD', minor: 2, symbol: '$', rate: 1 },
      { code: 'EUR', minor: 2, symbol: '€', rate: 0.92 },
    ],
    country_currency: { DE: 'EUR' },
    country_locale: { DE: 'de' },
    fx: { base: 'USD', rates: { USD: 1, EUR: 0.92 }, display_only: true, updated_at: null },
  },
  region: { country: 'BT', source: 'vercel', enforced: false, suggested_locale: 'en', suggested_currency: 'BTN' },
  deployment: { serverless: false, demo_mode: false, balances_persist: true, real_money: false },
}

const ME = {
  id: 'u1', email: 'player@example.com', username: 'smoketester', role: 'player',
  display_currency: 'USD', country: 'BT', kyc_status: 'none',
  email_verified: true, created_at: '2026-01-15T09:00:00Z',
  balances: { currency: 'USD', display_currency: 'USD', cash: 123456, bonus: 2000, locked: 0, total: 125456, withdrawable: 123456, pending_wager: 0, cash_display: '$1,234.56' },
  stats: { wagered: 450000, net: -12500, bets: 312, biggest_win: 88000, rtp_actual: 97.2 },
  vip: { name: 'Bronze', next_name: 'Silver', rakeback_pct: 0.5, wagered_lifetime: 450000, next_at: 1000000, progress_pct: 45 },
}

const LIMITS = {
  loss_limit_daily: null, deposit_limit_daily: null, kyc_required_above: 100000,
  self_excluded_until: null, cool_off_until: null,
  today: { deposits: 20000, losses: 1500, wagered: 32000 },
}

const BETS = {
  bets: [
    { id: 'b1', game: 'crash', stake: 2500, payout: 6250, profit: 3750, multiplier: 2.5,
      stake_source: 'cash', created_at: '2026-09-29T11:00:00Z', settled: true },
  ],
}

const DEPOSIT = {
  id: 'dep_1', status: 'requires_action', amount: 5000, credited: 0, bonus_credited: 0,
  reference: 'dep_1', failure_reason: null,
  instructions: { address: 'TJmvQ1xSmokeTestAddress', network: 'TRC20', confirmations_required: 19 },
}

const TRANSACTIONS = {
  entries: [
    { id: 'e1', transaction_id: 't1', kind: 'deposit', status: 'posted', account: 'user_available',
      amount: 100000, balance_after: 123456, memo: 'Card deposit', reference: 'dep_1',
      created_at: '2026-09-29T10:00:00Z' },
    { id: 'e2', transaction_id: 't2', kind: 'bet_stake', status: 'posted', account: 'user_available',
      amount: -2500, balance_after: 23456, memo: 'Slots', reference: 'bet_1',
      created_at: '2026-09-29T11:00:00Z' },
  ],
  pending: [
    { id: 'w1', kind: 'withdrawal', status: 'under_review', amount: -25000,
      method: 'crypto_usdt', created_at: '2026-09-29T12:00:00Z', resumable: false },
  ],
  total: 2, limit: 15, offset: 0, settlement_currency: 'USD', amounts_are_minor_units: true,
}

function routeFor(url) {
  const path = new URL(url, 'http://localhost').pathname
  if (path === '/api/config') return CONFIG
  if (path === '/api/auth/me') return ME
  if (path === '/api/wallet/summary') return { balances: ME.balances }
  if (path === '/api/wallet/transactions') return TRANSACTIONS
  if (path === '/api/games/catalog') return { games: [] }
  if (path === '/api/auth/limits') return LIMITS
  if (path === '/api/auth/seeds') return { server_seed_hash: 'a'.repeat(64), client_seed: 'smoke' }
  if (path === '/api/auth/kyc') return { status: 'none', documents: [] }
  if (path === '/api/games/history') return BETS
  if (path === '/api/wallet/deposits/dep_1') return DEPOSIT
  // A bare array, matching the API: /auth/notifications returns a list, not an
  // envelope. Getting this wrong is how the account page white-screens.
  if (path === '/api/auth/notifications') return []
  if (path.startsWith('/api/')) return {}
  return null
}

function installFetch(window) {
  window.fetch = async (url, options = {}) => {
    const body = routeFor(String(url))
    if (body === null) {
      return { ok: false, status: 404, json: async () => ({ detail: 'not found' }), text: async () => 'not found' }
    }
    // The API returns 401 for /auth/me when logged out; the stub is always
    // logged in, which is the state the dashboard needs to render.
    return {
      ok: true,
      status: 200,
      headers: { get: () => 'application/json' },
      json: async () => body,
      text: async () => JSON.stringify(body),
      ...options,
    }
  }
}

// --- boot the real bundle ---------------------------------------------------
const html = readFileSync(join(DIST, 'index.html'), 'utf8')
const assetDir = join(DIST, 'assets')
const jsFile = readdirSync(assetDir).find((f) => f.startsWith('index-') && f.endsWith('.js'))
if (!jsFile) {
  console.error('no built JS found in dist/assets - run `npm run build` first')
  process.exit(1)
}

const bundle = readFileSync(join(assetDir, jsFile), 'utf8')

/** Boot the real bundle at a URL and let React settle. */
async function boot(url, { locale } = {}) {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only', pretendToBeVisual: true })
  const { window } = dom
  installFetch(window)

  // React reports a render crash through console.error, complete with the
  // component stack. Without capturing it the only symptom is a blank page
  // and a minified one-line TypeError, which is not a debuggable failure.
  const errors = []
  window.__smokeErrors = errors
  window.addEventListener('error', (event) =>
    errors.push(String(event.error?.stack || event.message || event)),
  )
  window.addEventListener('unhandledrejection', (event) =>
    errors.push(String(event.reason?.stack || event.reason)),
  )

  // A signed-in session, because the pages worth smoke-testing sit behind auth.
  // The token is never checked by the stub - what matters is that the store
  // takes the authenticated path instead of bouncing to /login.
  window.localStorage.setItem('nc.access_token', 'smoke-test-token')
  window.localStorage.setItem('nc.refresh_token', 'smoke-test-refresh')
  if (locale) window.localStorage.setItem('casino.locale', locale)

  // Browser APIs the bundle expects.
  window.matchMedia ||= () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} })
  window.scrollTo = () => {}
  window.WebSocket = class { constructor() { this.readyState = 3 } close() {} addEventListener() {} removeEventListener() {} send() {} }

  // `runScripts: 'outside-only'` means we evaluate explicitly: a syntax error
  // or a module-level throw surfaces here instead of as a blank page. Nothing
  // is copied onto globalThis - the code runs inside the jsdom window, so
  // `document`, `navigator` and `fetch` resolve to the stubs above.
  window.eval(bundle)
  await new Promise((resolveTimer) => setTimeout(resolveTimer, 800))
  return window
}

console.log('render smoke: the built bundle in jsdom\n')

const window = await boot('http://localhost/en/wallet')
check('the bundle evaluates without throwing', true)

// React renders asynchronously (effects, promises). Give it real time.
await new Promise((resolveTimer) => setTimeout(resolveTimer, 800))

const root = window.document.getElementById('root')
const text = root?.textContent || ''
const html5 = root?.innerHTML || ''

if (process.env.DUMP) {
  console.log('\n--- rendered text ---\n' + text.replace(/\s+/g, ' ').slice(0, 1200) + '\n')
  console.log('--- location ---', window.location.pathname)
}

check('the app mounted into #root', html5.length > 500, `${html5.length} bytes of markup`)
check('the balance is rendered', /1,234\.56/.test(text), text.match(/\$[\d,.]+/g)?.slice(0, 3).join(' '))
check('the wallet heading is rendered', text.includes('Wallet'))
check('a deposit call to action exists', /Add funds|Deposit/.test(text))
check('a withdraw call to action exists', /Cash out|Withdraw/.test(text))
check('the transaction table has the settled ledger', text.includes('Card deposit'))
check('an in-flight item is shown with its status', /Under review/.test(text))
check('the language switcher is present', Boolean(root?.querySelector('select[aria-label="Language"]')))
check('the navigation renders', text.includes('Games') && text.includes('Promotions'))

// A white screen is the failure this whole script exists to catch.
const visible = text.replace(/\s+/g, '').length
check('the page is not blank', visible > 200, `${visible} non-space characters`)

// --- the same app in a translated, right-to-left locale ----------------------
// English rendering correctly proves nothing about Arabic: a missing bundle, a
// broken direction attribute or an untranslated shell all look perfect in en.
console.log('\n  — Arabic (right-to-left) —')
try {
  const arWindow = await boot('http://localhost/ar/wallet', { locale: 'ar' })
  const arRoot = arWindow.document.getElementById('root')
  const arText = arRoot?.textContent || ''

  check(
    'the document direction is right-to-left',
    arWindow.document.documentElement.dir === 'rtl',
    `dir=${arWindow.document.documentElement.dir}`,
  )
  check(
    'the document language is set',
    arWindow.document.documentElement.lang === 'ar',
    `lang=${arWindow.document.documentElement.lang}`,
  )
  check(
    'the shell is translated',
    arText.includes('المحفظة') && !arText.includes('wallet.title'),
    arText.includes('wallet.title') ? 'raw key rendered' : 'translated',
  )
  check('the page is not blank in Arabic', arText.replace(/\s+/g, '').length > 200)
} catch (err) {
  check('the Arabic locale renders', false, String(err).slice(0, 200))
}

// --- the money pages in a translated locale ---------------------------------
// Keys existing in es.json proves nothing about the page: a component that
// still has a literal string renders English inside a Spanish layout. These
// assertions are on the rendered DOM, in Spanish, of the three screens a
// player touches money on.
console.log('\n  — Spanish money pages —')
const ES_PAGES = [
  {
    url: 'http://localhost/es/account',
    h1: 'Cuenta',
    extra: ['Juego responsable', 'Resultado neto', 'Tus datos', 'VIP'],
    // The limits form lives behind a tab; clicking exercises the real tab
    // wiring rather than just the tab labels.
    click: 'Juego responsable',
    afterClick: ['Límite de pérdida diaria (USD)', 'Límite de depósito diario (USD)'],
  },
  { url: 'http://localhost/es/history', h1: 'Mis apuestas', extra: ['Multiplicador', 'Cuándo', 'Origen'] },
  { url: 'http://localhost/es/checkout/dep_1', h1: 'Pago del depósito', extra: ['Ya pagué — confirmar', 'Importe'] },
]
for (const page of ES_PAGES) {
  try {
    const w = await boot(page.url, { locale: 'es' })
    if (process.env.DUMP) console.log(w.__smokeErrors.join('\n\n'))
    const text = w.document.getElementById('root')?.textContent || ''
    const heading = w.document.querySelector('h1, .card-title, h2')?.textContent || ''
    check(
      `${page.url} renders in Spanish`,
      text.includes(page.h1) && !/\{\{/.test(text),
      text.includes(page.h1) ? heading.trim().slice(0, 40) : (w.__smokeErrors[0] || heading).slice(0, 220),
    )
    for (const needle of page.extra) {
      check(`${page.url} shows "${needle}"`, text.includes(needle))
    }
    if (page.click) {
      const tab = [...w.document.querySelectorAll('button, [role="tab"], a')].find(
        (el) => el.textContent.trim() === page.click,
      )
      check(`${page.url} has a "${page.click}" tab`, Boolean(tab))
      tab?.click()
      await new Promise((resolveTimer) => setTimeout(resolveTimer, 250))
      const after = w.document.getElementById('root')?.textContent || ''
      for (const needle of page.afterClick || []) {
        check(`${page.url} after "${page.click}" shows "${needle}"`, after.includes(needle))
      }
    }
    check(`${page.url} is not blank`, text.replace(/\s+/g, '').length > 200)
    check(`${page.url} leaks no raw keys`, !/[a-z]+\.[a-z_]+_[a-z]+\s|\baccount\.|\bbets\.|\bcheckout\./.test(text))
  } catch (err) {
    check(`${page.url} renders`, false, String(err).slice(0, 200))
  }
}

console.log(`\nfinal: ${passed} passed, ${failures.length} failed`)
if (failures.length) {
  for (const name of failures) console.log(`  FAILED: ${name}`)
  process.exit(1)
}
console.log('the built site renders with money, statuses and calls to action')

// The checkout page polls for the provider's confirmation, so its interval
// keeps Node's event loop alive after the assertions are done. Exiting
// explicitly is what makes this script safe to run from `npm run check`
// instead of hanging there until someone presses Ctrl-C.
process.exit(0)
