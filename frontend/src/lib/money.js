/**
 * Money display.
 *
 * Two rules, and the second is the one that matters:
 *
 *  1. Every amount from the API is an INTEGER in minor units of the settlement
 *     currency (cents). It is never a float, because 0.1 + 0.2 !== 0.3 and a
 *     ledger that cannot add up is not a ledger.
 *  2. A converted amount is a *rendering*. It is never sent back to the API,
 *     never compared against a limit, and never stored. The server's own note
 *     on this is in backend/app/i18n.py; this file is the other half of it.
 *
 * `Intl.NumberFormat` does the formatting because it already knows that
 * German writes 1.234,56 €, Hindi groups as 1,23,456, Arabic uses its own
 * digits, and JPY has no decimal part. Hand-rolling that is how a currency
 * ends up displayed with the wrong precision in a market you have not thought
 * about yet.
 */

/** Minor-unit divisor for a currency. 100 for cents, 1 for JPY, 1e8 for BTC. */
export function minorDivisor(decimals) {
  return 10 ** decimals
}

/**
 * Convert a settlement minor amount into a display amount.
 * Returns both, so callers can always show the truth if they need to.
 */
export function convert(minor, { currency, rate = 1, decimals = 2, settlement = 'USD' }) {
  const amount = Number(minor) || 0
  if (!currency || currency === settlement || !rate) {
    return { minor: amount, currency: settlement, converted: false }
  }
  const value = (amount / 100) * rate                 // settlement is always 2dp
  return {
    minor: Math.round(value * minorDivisor(decimals)),
    currency,
    converted: true,
  }
}

/**
 * Format a settlement minor amount for display.
 *
 * @param {number} minor      amount in settlement minor units (integer)
 * @param {object} options    currency, rate, decimals, locale, signed, compact
 */
export function formatMoney(minor, options = {}) {
  const {
    currency = 'USD',
    rate = 1,
    decimals = 2,
    locale = undefined,
    settlement = 'USD',
    signed = false,
    showCurrency = true,
  } = options

  if (minor === null || minor === undefined || Number.isNaN(Number(minor))) return '—'

  const display = convert(minor, { currency, rate, decimals, settlement })
  const value = display.minor / minorDivisor(decimals)

  // Currency is rendered from the code + locale rather than a hand-written
  // symbol table, so a new currency works without touching this file.
  const formatted = new Intl.NumberFormat(locale, {
    style: showCurrency ? 'currency' : 'decimal',
    currency: display.currency,
    currencyDisplay: 'narrowSymbol',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(signed ? value : Math.abs(value))

  if (signed && value > 0) return `+${formatted}`
  if (value < 0) return `-${formatted}`.replace('--', '-')
  return formatted
}

/** The same amount, formatted as a positive magnitude (for colour-coded rows). */
export function formatSignedMoney(minor, options = {}) {
  const amount = Number(minor) || 0
  const body = formatMoney(Math.abs(amount), options)
  if (amount === 0) return body
  return `${amount > 0 ? '+' : '−'}${body}`
}

/** A short form for chips and headers: 12.4k, 1.2M. */
export function formatCompact(minor, options = {}) {
  const { currency = 'USD', rate = 1, decimals = 2, locale, settlement = 'USD' } = options
  if (minor === null || minor === undefined) return '—'
  const display = convert(minor, { currency, rate, decimals, settlement })
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: display.currency,
    currencyDisplay: 'narrowSymbol',
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(display.minor / minorDivisor(decimals))
}

/** Parse a user-typed amount into the 2dp string the API expects. */
export function toAmountString(input) {
  const cleaned = String(input ?? '').replace(/[^0-9.]/g, '')
  if (!cleaned) return ''
  const [whole, ...rest] = cleaned.split('.')
  const decimals = rest.join('').slice(0, 2)
  return rest.length ? `${whole}.${decimals}` : whole
}

/** Percentage change between two amounts, for the dashboard's stat cards. */
export function percentChange(current, previous) {
  const now = Number(current) || 0
  const before = Number(previous) || 0
  if (before === 0) return null
  return ((now - before) / Math.abs(before)) * 100
}
