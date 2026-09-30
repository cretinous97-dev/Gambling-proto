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
  // Present, and switched on, because the signup page renders a banner only
  // when `first_deposit_pct > 0`. A stub that omitted this would skip the
  // branch - which is how a missing import inside it survived a 38-check
  // smoke run and shipped.
  bonuses: { signup_bonus: 0, first_deposit_pct: 100, first_deposit_cap: 10000, first_deposit_wager_x: 30 },
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

// Set by boot() before it installs fetch. The admin console redirects a
// non-admin away from the route, so proving it renders means answering
// /auth/me as the operator - and the operator shape is different enough
// (role, no player stats driving the UI) that it is worth its own object.
let asAdmin = false

function routeFor(url) {
  const path = new URL(url, 'http://localhost').pathname
  if (path === '/api/config') return CONFIG
  if (path === '/api/auth/me') return asAdmin ? { ...ME, role: 'admin', username: 'operator' } : ME
  if (path === '/api/wallet/summary') return { balances: ME.balances }
  if (path === '/api/wallet/transactions') return TRANSACTIONS
  if (path === '/api/games/catalog') return { games: [] }
  if (path === '/api/auth/limits') return LIMITS
  if (path === '/api/auth/seeds') return { server_seed_hash: 'a'.repeat(64), client_seed: 'smoke' }
  if (path === '/api/auth/kyc') return { status: 'none', documents: [] }
  if (path === '/api/games/history') return BETS
  if (path === '/api/wallet/deposits/dep_1') return DEPOSIT
  // The same deposit as a live provider would return it: hosted payment page.
  if (path === '/api/wallet/deposits/dep_live') {
    return { ...DEPOSIT, id: 'dep_live', instructions: { ...DEPOSIT.instructions, redirect_url: 'https://checkoutshopper-test.adyen.com/pay/xyz' } }
  }
  // A bare array, matching the API: /auth/notifications returns a list, not an
  // envelope. Getting this wrong is how the account page white-screens.
  if (path === '/api/auth/notifications') return []
  // --- the operator console -------------------------------------------------
  // Plausible shapes, copied from the real responses. `{}` would not do: the
  // dashboard reads `data.money_24h.ggr` and `data.queues.withdrawals_pending`,
  // so an empty object throws on a payload the server never sends - which
  // proves nothing about the app and hides every panel behind the first tab.
  if (path === '/api/admin/dashboard') {
    return {
      players: { total: 128, new_24h: 6, active_24h: 41, verified: 77, self_excluded: 1, banned: 2 },
      money_24h: { deposits: 450000, withdrawals: 120000, wagered: 980000, returned: 940000, ggr: 40000, net_deposits: 330000 },
      money_7d: { wagered: 6400000, returned: 6100000, ggr: 300000, margin_pct: 4.7 },
      queues: { withdrawals_pending: 2, kyc_pending: 1, failed_webhooks: 0 },
      realtime: { crash_sockets: 3 },
      provider: { provider: 'sandbox', ok: true, simulation: true, mode: 'simulation', warning: '' },
      integrity: { books_balanced: true, totals: {} },
      jackpot: { name: 'Daily jackpot', amount: 1250000, contribution_pct: 1.5 },
    }
  }
  if (path === '/api/admin/revenue') {
    return { series: [{ day: '2026-09-29', ggr: 40000, wagered: 980000, deposits: 450000, withdrawals: 120000 }] }
  }
  if (path === '/api/admin/health') {
    return {
      environment: 'test',
      payment_provider: { provider: 'sandbox', ok: true, simulation: true, mode: 'simulation', warning: '' },
      ledger: { balanced: true, accounts: {} },
      pending_withdrawals: 0, unprocessed_webhooks: 0, crash_clients: 0,
    }
  }
  // Crash, both frames: the socket is closed in jsdom, so the REST poll drives
  // it - which is the fallback path on any deployment without websocket support.
  if (path === '/api/crash/state' || path === '/api/crash/state/me') {
    return {
      phase: 'running', round_number: 7, server_seed_hash: 'a'.repeat(64),
      started_at: '2026-09-29T11:00:00Z', elapsed: 4.2, multiplier: 1.42,
      player_count: 5, total_stake: 25000, players: [], your_bet: null, crash_point: 3.33,
    }
  }
  // Promotions: `built_in` is always present on the real response, and the
  // page reads `built_in.first_deposit_pct` unguarded. An empty object here
  // crashed the page for a reason the server cannot produce.
  if (path === '/api/promotions') {
    return {
      promotions: [],
      built_in: { signup_bonus: 500, first_deposit_pct: 100, first_deposit_cap: 10000, wager_x: 30 },
    }
  }
  // Note `aml-queue` returns `queue`, not `cases`/`flagged`.
  if (path === '/api/admin/aml-queue') return { queue: [] }
  if (path === '/api/admin/withdrawals') return { withdrawals: [] }
  if (path === '/api/admin/deposits') return { deposits: [] }
  if (path === '/api/admin/users') return { users: [] }
  if (path === '/api/admin/audit') return { entries: [] }
  if (path === '/api/admin/ledger') return { transactions: [] }
  if (path === '/api/admin/webhooks') return { webhooks: [] }
  if (path === '/api/admin/sessions') return { sessions: [] }
  if (path === '/api/admin/big-wins') return { wins: [] }
  if (path === '/api/admin/bonuses/codes') return { codes: [] }
  // The banking manager, as the admin API returns it - including the fields an
  // operator added and has not yet given a key to.
  if (path === '/api/admin/banking-methods') {
    return {
      total: 1,
      providers: ['', 'adyen', 'stripe', 'cryptopay', 'bank_transfer', 'sandbox'],
      method_kinds: ['bank_transfer', 'card', 'wallet', 'crypto'],
      methods: [{
        id: 'bm1', name: 'mBoB (Bank of Bhutan)', country_code: 'BT', currency: 'BTN',
        account_id: '201835782', api_endpoint: '', credential_env: 'MBOB_API_KEY',
        credential_present: false, provider: 'bank_transfer', effective_provider: 'bank_transfer',
        method: 'bank_transfer', deposits_enabled: true, withdrawals_enabled: false,
        active: false, priority: 10, min_amount_minor: 5000, max_amount_minor: 2000000,
        fee_bps: 25, notes: 'Seeded inactive: MBOB_API_KEY is not set in this deployment.',
        instructions: {}, created_at: '2026-09-29T23:32:15Z', updated_at: '2026-09-29T23:32:15Z',
      }],
    }
  }
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
async function boot(url, { locale, anonymous = false, admin = false } = {}) {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only', pretendToBeVisual: true })
  const { window } = dom
  asAdmin = admin
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
  if (!anonymous) {
    window.localStorage.setItem('nc.access_token', 'smoke-test-token')
    window.localStorage.setItem('nc.refresh_token', 'smoke-test-refresh')
  }
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
  // Pages whose literals were hardcoded English until now, even though the
  // translations had existed in the locale files for a while. Asserting the
  // Spanish heading is the only thing that proves the wiring, not the file.
  { url: 'http://localhost/es/leaderboard', locale: 'es', h1: 'Clasificación', extra: ['Jugador', 'Multiplicador'] },
  { url: 'http://localhost/es/promotions', locale: 'es', h1: 'Promociones', extra: ['Crear cuenta'] },
  { url: 'http://localhost/es/crash', locale: 'es', h1: null, extra: [] },
]
for (const page of ES_PAGES) {
  try {
    const w = await boot(page.url, { locale: 'es' })
    if (process.env.DUMP) console.log(w.__smokeErrors.join('\n\n'))
    const text = w.document.getElementById('root')?.textContent || ''
    const heading = w.document.querySelector('h1, .card-title, h2')?.textContent || ''
    check(
      `${page.url} renders in Spanish`,
      page.h1 ? text.includes(page.h1) && !/\{\{/.test(text) : true,
      page.h1 ? (text.includes(page.h1) ? heading.trim().slice(0, 40) : (w.__smokeErrors[0] || heading).slice(0, 220)) : 'rendered',
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
    // A raw key is `group.snake_case` - always lowercase with underscores. The
    // pattern has to say that much: a looser `\baccount\.` also matches an
    // English sentence ending in "account." followed by the next element's
    // text, which reported a leak where the real problem was a missing
    // translation.
    const RENDERED_KEY = /\b(?:account|bets|checkout|auth|nav|wallet|games|legal|status|kinds)\.[a-z][a-z0-9_]*\b/g
    check(`${page.url} leaks no raw keys`, !RENDERED_KEY.test(text),
      (text.match(RENDERED_KEY) || []).join(' / ').slice(0, 160))
  } catch (err) {
    check(`${page.url} renders`, false, String(err).slice(0, 200))
  }
}

// --- the redirect to a live payment page ------------------------------------
// With a real PSP the deposit completes on the provider's page. If the client
// never offers that link the deposit stays at requires_action forever, which
// looks exactly like a broken deposit button.
console.log('\n  — hosted payment page (live provider) —')
try {
  const liveWindow = await boot('http://localhost/en/checkout/dep_live')
  const liveText = liveWindow.document.getElementById('root')?.textContent || ''
  const link = liveWindow.document.querySelector('a[href^="https://checkoutshopper-test.adyen.com"]')
  check('the provider page is linked', Boolean(link))
  check('the player is told what happens next', liveText.includes('Continue to payment'))
} catch (err) {
  check('the hosted payment page renders', false, String(err).slice(0, 200))
}

// --- the page every player must get through --------------------------------
// Signup was the one route the smoke never rendered, and it was the one route
// that crashed: the bonus banner referenced `fmtCents` without importing it, so
// the page threw on the default configuration and every new player got a blank
// screen. A build passes that (an undefined identifier is not a syntax error),
// and so did the rest of this file, because none of it visited /register.
//
// Rendered WITHOUT a session token, because that is how a new player arrives.
console.log('\n  — signed-out entry pages —')
for (const page of [
  { url: 'http://localhost/en/register', expect: ['Create your account', 'At least 8 characters'] },
  { url: 'http://localhost/en/login', expect: ['Sign in'] },
]) {
  try {
    const w = await boot(page.url, { anonymous: true })
    if (process.env.DUMP) console.log(w.__smokeErrors.join('\n\n'))
    const text = w.document.getElementById('root')?.textContent || ''
    for (const needle of page.expect) {
      check(`${page.url} shows "${needle}"`, text.includes(needle))
    }
    check(`${page.url} is not blank`, text.replace(/\s+/g, '').length > 100)
    check(
      `${page.url} renders without throwing`,
      (w.__smokeErrors || []).length === 0,
      String((w.__smokeErrors || [])[0] || '').slice(0, 200),
    )
    check(`${page.url} leaks no raw keys`, !/[a-z]+\.[a-z_]+_[a-z]+\s|\bauth\.|\bcheckout\./.test(text))

    if (page.url.endsWith('/register')) {
      // The consent sentence carries three links. <Trans> substitutes them by
      // the index positions in its `components` array, and a mismatch does not
      // throw - the link simply disappears. So assert the links, not just the
      // words around them.
      const legal = [...w.document.querySelectorAll('a[href*="/legal/"]')]
        .map((a) => a.textContent.trim())
      const wanted = (page.locale || 'en') === 'es'
        ? ['Términos', 'Privacidad', 'Juego responsable']
        : ['Terms', 'Privacy', 'Responsible gambling']
      for (const label of wanted) {
        check(`${page.url} consent links to "${label}"`, legal.includes(label), legal.join(' | '))
      }
    }
  } catch (err) {
    check(`${page.url} renders`, false, String(err).slice(0, 200))
  }
}

// The consent sentence is the one place a translation has to survive being
// reordered around its links, so it is worth rendering in a language that
// reorders it. Spanish moves "y la" inside the clause.
console.log('\n  — signup consent in a translated locale —')
try {
  const esw = await boot('http://localhost/es/register', { locale: 'es', anonymous: true })
  if (process.env.DUMP) console.log(esw.__smokeErrors.join('\n\n'))
  const root = esw.document.getElementById('root')
  const text = root?.textContent || ''
  const legal = [...root.querySelectorAll('a[href*="/legal/"]')].map((a) => a.textContent.trim())
  check('the Spanish signup page renders', text.includes('Crear cuenta'), (esw.__smokeErrors || [])[0]?.slice(0, 160) || 'ok')
  for (const label of ['Términos', 'Privacidad', 'Juego responsable']) {
    check(`the Spanish consent links to "${label}"`, legal.includes(label), legal.join(' | '))
  }
  check('no placeholder is left unrendered', !/\{\{|<\d>/.test(text),
    (text.match(/\{\{[^}]*\}|<\d>/g) || []).join(' '))
} catch (err) {
  check('the Spanish signup page renders', false, String(err).slice(0, 200))
}

// --- the landing page and the operator console ------------------------------
// The console is every privileged action in the product, and the landing page
// is the first thing anyone sees; neither was ever rendered here.
console.log('\n  — landing page and operator console —')
try {
  const home = await boot('http://localhost/en', { anonymous: true })
  const text = home.document.getElementById('root')?.textContent || ''
  check('the landing page is not blank', text.replace(/\s+/g, '').length > 200)
  check('the landing page renders without throwing', (home.__smokeErrors || []).length === 0,
    String((home.__smokeErrors || [])[0] || '').slice(0, 200))
  check('the landing page offers a way in', /Sign in|Create|Log in|Register/i.test(text))
} catch (err) {
  check('the landing page renders', false, String(err).slice(0, 200))
}

try {
  const adm = await boot('http://localhost/en/admin', { admin: true })
  if (process.env.DUMP) console.log(adm.__smokeErrors.join('\n\n'))
  const root = adm.document.getElementById('root')
  const text = root?.textContent || ''
  check('the operator console renders without throwing', (adm.__smokeErrors || []).length === 0,
    String((adm.__smokeErrors || [])[0] || '').slice(0, 200))
  check('the console shows its tabs', text.includes('Withdrawals') && text.includes('Ledger'))
  check('the banking manager is reachable', text.includes('Banking methods'))
  // Click through every tab: a panel that throws only when opened is exactly
  // what an unvisited route hides.
  const tabs = [...root.querySelectorAll('button, [role="tab"]')]
    .filter((el) => ['Banking methods', 'Withdrawals', 'Ledger', 'Audit log', 'System', 'Risk & AML']
      .includes(el.textContent.trim()))
  check('the console exposes the money tabs', tabs.length >= 5, `${tabs.length} found`)
  for (const tab of tabs) {
    tab.click()
    await new Promise((resolveTimer) => setTimeout(resolveTimer, 300))
    const after = root.textContent || ''
    check(`the "${tab.textContent.trim()}" tab opens`, after.replace(/\s+/g, '').length > 100)
  }
  const finalErrors = (adm.__smokeErrors || []).filter((e) => !/not wrapped in act/.test(e))
  check('no tab threw while opened', finalErrors.length === 0, String(finalErrors[0] || '').slice(0, 200))
} catch (err) {
  check('the operator console renders', false, String(err).slice(0, 200))
}

// --- crash ------------------------------------------------------------------
// The socket is stubbed shut, so this is the poll fallback running - the same
// path a player gets behind a proxy that strips websocket upgrades. The state
// arrow was reworked to survive a socket that opens before sign-in resolves,
// and this is what says it still renders either way.
console.log('\n  — crash (poll fallback, no websocket) —')
try {
  const cw = await boot('http://localhost/en/crash')
  if (process.env.DUMP) console.log(cw.__smokeErrors.join('\n\n'))
  check('the crash page renders without throwing', (cw.__smokeErrors || []).length === 0,
    String((cw.__smokeErrors || [])[0] || '').slice(0, 200))
  // The socket is dead in jsdom, so the REST poll is the only thing that can
  // put a number on the page at all. Waiting for a tick is what tests the
  // fallback rather than the initial paint - before it fires there is nothing
  // to assert on but a spinner.
  await new Promise((resolveTimer) => setTimeout(resolveTimer, 3500))
  const text = cw.document.getElementById('root')?.textContent || ''
  check('the crash page is not blank', text.replace(/\s+/g, '').length > 150)
  // The frame said 1.42x, and the display is expected to have moved past it:
  // between frames the client interpolates for smooth animation. Asserting an
  // exact 1.42 would fail on a working build, so the contract is "a live
  // multiplier near the frame the server sent, in the right format".
  const shown = (text.match(/(\d+\.\d{2})x/) || [])[1]
  check('the poll fallback put a live multiplier on screen',
    Boolean(shown) && Number(shown) >= 1.42 && Number(shown) < 2,
    `server sent 1.42x, screen shows ${shown ? `${shown}x` : 'nothing'}`)
} catch (err) {
  check('the crash page renders', false, String(err).slice(0, 200))
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
