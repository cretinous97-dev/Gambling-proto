/**
 * API client: one place that knows about tokens, money formatting and errors.
 *
 * Money never travels as a float. Requests send decimal strings ("12.34"),
 * responses carry integer cents (`*_minor` fields) and are formatted here.
 */

const TOKEN_KEY = 'nc.access_token'
const REFRESH_KEY = 'nc.refresh_token'

export const tokens = {
  get access() {
    return localStorage.getItem(TOKEN_KEY)
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY)
  },
  set({ access_token, refresh_token }) {
    if (access_token) localStorage.setItem(TOKEN_KEY, access_token)
    if (refresh_token) localStorage.setItem(REFRESH_KEY, refresh_token)
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_KEY)
  },
}

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

/** Turn FastAPI's various error shapes into a single readable sentence. */
function readError(payload, fallback) {
  if (!payload) return fallback
  const { detail } = payload
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // pydantic validation errors
    return detail
      .map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`)
      .join('; ')
  }
  return fallback
}

async function refreshTokens() {
  const refresh = tokens.refresh
  if (!refresh) return false
  try {
    const res = await fetch(`/api/auth/refresh?refresh_token=${encodeURIComponent(refresh)}`, {
      method: 'POST',
    })
    if (!res.ok) return false
    tokens.set(await res.json())
    return true
  } catch {
    return false
  }
}

export async function request(path, { method = 'GET', body, auth = true, retry = true } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth && tokens.access) headers.Authorization = `Bearer ${tokens.access}`

  let res
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (err) {
    throw new ApiError('Network error - is the backend running?', 0, err)
  }

  if (res.status === 401 && auth && retry) {
    // One silent refresh, then give up and let the UI redirect to /login.
    if (await refreshTokens()) {
      return request(path, { method, body, auth, retry: false })
    }
    tokens.clear()
  }

  if (res.status === 204) return null

  const text = await res.text()
  const payload = text ? safeJson(text) : null
  if (!res.ok) {
    if (res.status === 401) tokens.clear()
    throw new ApiError(
      readError(payload, `Request failed (${res.status})`),
      res.status,
      payload,
    )
  }
  return payload
}

function safeJson(text) {
  try {
    return JSON.parse(text)
  } catch {
    return { detail: text.slice(0, 300) }
  }
}

export const api = {
  // --- auth ---------------------------------------------------------------
  register: (body) => request('/api/auth/register', { method: 'POST', body, auth: false }),
  login: (body) => request('/api/auth/login', { method: 'POST', body, auth: false }),
  logout: () => request('/api/auth/logout', { method: 'POST' }),
  me: () => request('/api/auth/me'),
  updateProfile: (body) => request('/api/auth/me', { method: 'PATCH', body }),
  changePassword: (body) => request('/api/auth/password', { method: 'POST', body }),
  setLimits: (body) => request('/api/auth/responsible-gambling', { method: 'POST', body }),
  limits: () => request('/api/auth/limits'),
  seeds: () => request('/api/auth/seeds'),
  rotateSeeds: (body) => request('/api/auth/seeds/rotate', { method: 'POST', body }),
  kyc: () => request('/api/auth/kyc'),
  submitKyc: (body) => request('/api/auth/kyc', { method: 'POST', body }),
  notifications: () => request('/api/auth/notifications'),
  markRead: () => request('/api/auth/notifications/read', { method: 'POST' }),

  // --- wallet -------------------------------------------------------------
  wallet: () => request('/api/wallet/summary'),
  statement: (limit = 100) => request(`/api/wallet/statement?limit=${limit}`),
  transactions: (query = '') => request(`/api/wallet/transactions${query}`),
  methods: () => request('/api/wallet/methods'),
  integrity: () => request('/api/wallet/integrity'),
  createDeposit: (body) => request('/api/wallet/deposits', { method: 'POST', body }),
  deposits: () => request('/api/wallet/deposits'),
  deposit: (id) => request(`/api/wallet/deposits/${id}`),
  simulateDeposit: (id, outcome = 'succeed') =>
    request(`/api/wallet/deposits/${id}/simulate`, { method: 'POST', body: { outcome } }),
  createWithdrawal: (body) => request('/api/wallet/withdrawals', { method: 'POST', body }),
  withdrawals: () => request('/api/wallet/withdrawals'),
  cancelWithdrawal: (id) => request(`/api/wallet/withdrawals/${id}/cancel`, { method: 'POST' }),

  // --- games --------------------------------------------------------------
  catalog: () => request('/api/games/catalog', { auth: false }),
  rules: (slug) => request(`/api/games/rules/${slug}`, { auth: false }),
  play: (body) => request('/api/games/play', { method: 'POST', body }),
  history: (game) => request(`/api/games/history${game ? `?game=${game}` : ''}`),
  leaderboard: () => request('/api/games/leaderboard', { auth: false }),
  betDetail: (id) => request(`/api/games/history/${id}`),

  minesStart: (body) => request('/api/games/mines/start', { method: 'POST', body }),
  minesOpen: (id, index) => request(`/api/games/mines/${id}/open`, { method: 'POST', body: { index } }),
  minesCashout: (id) => request(`/api/games/mines/${id}/cashout`, { method: 'POST' }),

  blackjackDeal: (body) => request('/api/games/blackjack/deal', { method: 'POST', body }),
  blackjackAction: (id, action) =>
    request(`/api/games/blackjack/${id}/action`, { method: 'POST', body: { action } }),

  crashState: () => request('/api/crash/state', { auth: false }),
  crashMe: () => request('/api/crash/state/me'),
  crashBet: (body) => request('/api/crash/bet', { method: 'POST', body }),
  crashCashout: (body = {}) => request('/api/crash/cashout', { method: 'POST', body }),
  crashHistory: () => request('/api/crash/history', { auth: false }),
  crashVerify: (round) => request(`/api/crash/verify/${round}`, { auth: false }),

  // --- public -------------------------------------------------------------
  config: () => request('/api/config', { auth: false }),
  promotions: () => request('/api/promotions', { auth: false }),
  jackpot: () => request('/api/jackpot', { auth: false }),
  chat: () => request('/api/chat', { auth: false }),
  postChat: (body) => request('/api/chat', { method: 'POST', body }),
  legal: (doc) => request(`/api/legal/${doc}`, { auth: false }),

  // --- admin --------------------------------------------------------------
  adminDashboard: () => request('/api/admin/dashboard'),
  adminRevenue: (days = 14) => request(`/api/admin/revenue?days=${days}`),
  adminHealth: () => request('/api/admin/health'),
  adminUsers: (q) => request(`/api/admin/users${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  adminUser: (id) => request(`/api/admin/users/${id}`),
  adminUpdateUser: (id, body) => request(`/api/admin/users/${id}`, { method: 'PATCH', body }),
  adminAdjust: (id, body) => request(`/api/admin/users/${id}/adjust`, { method: 'POST', body }),
  adminWithdrawals: (status) =>
    request(`/api/admin/withdrawals${status ? `?status=${status}` : ''}`),
  adminReview: (id, body) =>
    request(`/api/admin/withdrawals/${id}/review`, { method: 'POST', body }),
  adminDeposits: (status) => request(`/api/admin/deposits${status ? `?status=${status}` : ''}`),
  adminAml: () => request('/api/admin/aml-queue'),
  adminAudit: () => request('/api/admin/audit'),
  adminLedger: () => request('/api/admin/ledger'),
  adminWebhooks: () => request('/api/admin/webhooks'),

  // --- banking method manager ---------------------------------------------
  // The admin's registry of payment pathways. Everything here is data: adding
  // a bank here makes it live for its markets on the next transaction, with no
  // deploy. No endpoint in this block accepts or returns a credential - a
  // pathway carries the NAME of the environment variable that holds its key.
  bankingMethods: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== ''),
    ).toString()
    return request(`/api/admin/banking-methods${qs ? `?${qs}` : ''}`)
  },
  createBankingMethod: (body) =>
    request('/api/admin/banking-methods', { method: 'POST', body }),
  updateBankingMethod: (id, body) =>
    request(`/api/admin/banking-methods/${id}`, { method: 'PATCH', body }),
  toggleBankingMethod: (id) =>
    request(`/api/admin/banking-methods/${id}/toggle`, { method: 'POST' }),
  retireBankingMethod: (id) =>
    request(`/api/admin/banking-methods/${id}`, { method: 'DELETE' }),
  explainRouting: (params) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== ''),
    ).toString()
    return request(`/api/admin/banking-methods/any/explain?${qs}`)
  },
  adminBigWins: () => request('/api/admin/big-wins'),
  adminSessions: () => request('/api/admin/sessions'),
  adminGrantBonus: (body) => request('/api/admin/bonuses/grant', { method: 'POST', body }),
  adminBonusCodes: () => request('/api/admin/bonuses/codes'),
  adminCreateCode: (body) => request('/api/admin/bonuses/codes', { method: 'POST', body }),
}

/** Open the crash websocket through the same origin the page was served from. */
export function openCrashSocket(onMessage, onOpen) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = tokens.access ? `?token=${encodeURIComponent(tokens.access)}` : ''
  let socket
  let closed = false
  let retry = 0

  const connect = () => {
    socket = new WebSocket(`${proto}//${window.location.host}/api/crash/ws${token}`)
    socket.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data))
      } catch {
        /* ignore malformed frame */
      }
    }
    socket.onopen = () => {
      retry = 0
      opened = true
      onOpen?.()
    }
    let opened = false
    let failures = 0
    socket.onclose = () => {
      if (closed) return
      // A deployment with no websocket support (serverless) rejects the
      // upgrade immediately. Give up after a few tries and let the REST poll
      // in the page take over, instead of reconnecting forever.
      if (!opened) {
        failures += 1
        if (failures >= 3) return
      }
      // exponential backoff, capped - a dropped socket must never hammer the API
      retry = Math.min(retry + 1, 6)
      setTimeout(connect, 500 * 2 ** retry)
    }
  }
  connect()

  return {
    close() {
      closed = true
      if (socket) socket.close()
    },
  }
}
