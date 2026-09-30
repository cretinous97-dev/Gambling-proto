/**
 * Display helpers. All amounts arriving from the API are INTEGER cents; these
 * functions only ever format for the eye. Never compute money in the browser.
 */

export function fmtCents(cents, { currency = 'USD', decimals = 2 } = {}) {
  if (cents === null || cents === undefined || Number.isNaN(cents)) return '—'
  const value = Number(cents) / 100
  const symbols = { USD: '$', BTN: 'Nu.', EUR: '€', INR: '₹' }
  const symbol = symbols[currency] || '$'
  const negative = value < 0
  const body = Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
  return `${negative ? '-' : ''}${symbol}${body}`
}

/** Parse a user-typed amount into a canonical 2dp string for the API. */
export function toAmountString(input) {
  const cleaned = String(input ?? '').replace(/[^0-9.]/g, '')
  if (!cleaned) return ''
  const [whole, ...rest] = cleaned.split('.')
  const decimals = rest.join('').slice(0, 2)
  return rest.length ? `${whole}.${decimals}` : whole
}

export function fmtMultiplier(mult) {
  const value = Number(mult || 0)
  if (value >= 1000) return `${(value / 1000).toFixed(1)}kx`
  return `${value.toFixed(2)}x`
}

export function fmtDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  return date.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

export function fmtRelative(value) {
  if (!value) return '—'
  const seconds = Math.floor((Date.now() - new Date(value).getTime()) / 1000)
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

export const STATUS_STYLES = {
  succeeded: 'badge badge-ok',
  paid: 'badge badge-ok',
  approved: 'badge badge-ok',
  verified: 'badge badge-ok',
  win: 'badge badge-ok',
  pending: 'badge badge-warn',
  requires_action: 'badge badge-warn',
  under_review: 'badge badge-warn',
  requested: 'badge badge-warn',
  none: 'badge badge-muted',
  cancelled: 'badge badge-muted',
  failed: 'badge badge-bad',
  rejected: 'badge badge-bad',
  chargeback: 'badge badge-bad',
  lose: 'badge badge-bad',
  push: 'badge badge-muted',
  blackjack: 'badge badge-ok',
}

export function statusClass(status) {
  return STATUS_STYLES[status] || 'badge badge-muted'
}

export const GAME_ICONS = {
  crash: '🚀', dice: '🎲', mines: '💣', plinko: '🔻', wheel: '🎡',
  keno: '🔢', coinflip: '🪙', roulette: '🎯', slots: '🎰', blackjack: '🃏',
}

export function timeAgoShort(iso) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m`
}
