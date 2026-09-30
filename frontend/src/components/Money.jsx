/**
 * <Money> - render a settlement amount in the player's chosen currency.
 *
 * Reads the conversion table from `/api/config` (the same table the backend
 * publishes, so there is exactly one source of rates) and the player's
 * preference from their profile. Falls back to the settlement currency when
 * anything is missing: showing the true settlement figure is always correct,
 * showing a conversion with no rate is not.
 */
import { useTranslation } from 'react-i18next'

import { useStore } from '../lib/store.jsx'
import { formatCompact, formatMoney } from '../lib/money.js'

export default function Money({
  amount,
  signed = false,
  className = '',
  compact = false,
  title,
  showCurrency = true,
  forceSettlement = false,
}) {
  const { i18n } = useTranslation()
  const { user, config } = useStore()

  const settlement = config?.i18n?.settlement_currency || 'USD'
  const fx = config?.i18n?.fx
  const preferred = forceSettlement ? settlement : (user?.display_currency || settlement)
  const meta = config?.i18n?.currencies?.find((c) => c.code === preferred)

  const rate = preferred === settlement ? 1 : (fx?.rates?.[preferred] ?? 1)
  const decimals = meta?.minor ?? 2
  const options = {
    currency: preferred,
    rate,
    decimals,
    settlement,
    locale: i18n.language,
    showCurrency,
  }

  const text = compact ? formatCompact(amount, options) : formatMoney(amount, { ...options, signed })

  // The tooltip carries the settlement amount whenever a conversion is on the
  // screen, so the real figure is always one hover away - including for the
  // operator reading over a player's shoulder.
  const tooltip = title
    || (rate !== 1
      ? `${formatMoney(amount, { currency: settlement, decimals: 2, locale: i18n.language })} ${settlement}`
      : undefined)

  return (
    // dir="auto" rather than a fixed direction: Arabic renders its own digits
    // and places the currency marker on the other side, and forcing ltr here
    // would put it in the wrong place for exactly the locales that need care.
    <span className={`money ${className}`} title={tooltip} dir="auto">
      {text}
    </span>
  )
}
