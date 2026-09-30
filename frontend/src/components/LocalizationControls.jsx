/**
 * Language and display-currency pickers.
 *
 * Both are deliberately two separate controls. "I read Arabic" and "I think in
 * dirhams" are independent facts - plenty of people want English with a local
 * currency, and plenty want their own language with USD because that is what
 * their card is denominated in. One control that changes both is a control that
 * is wrong for most of the world.
 *
 * The language picker navigates to the same page under the new locale prefix
 * (the router is mounted per-locale, so this is a real navigation rather than
 * a state change). The currency picker saves to the profile, because it is a
 * preference that should follow the player to their next device.
 */
import { useTranslation } from 'react-i18next'
import { useState } from 'react'

import { LOCALES, LOCALE_CODES, localeMeta, pathWithLocale } from '../i18n/index.js'
import { api } from '../lib/api.js'
import { useStore } from '../lib/store.jsx'

export function LanguageSwitcher({ className = '' }) {
  const { t, i18n } = useTranslation()
  const current = localeMeta(i18n.language)

  const change = (code) => {
    if (code === current.code) return
    const { pathname, search } = window.location
    // A real navigation, not a state change: BrowserRouter's basename is fixed
    // at mount, so the new prefix needs a new router.
    window.location.assign(pathWithLocale(pathname, code) + search)
  }

  return (
    <label className={`inline-flex items-center gap-2 ${className}`}>
      <span className="sr-only">{t('lang.label')}</span>
      <span aria-hidden="true">🌐</span>
      <select
        value={LOCALE_CODES.includes(i18n.language) ? i18n.language : 'en'}
        onChange={(event) => change(event.target.value)}
        aria-label={t('lang.label')}
        className="bg-panel-2 border border-line rounded-lg px-2 py-1.5 text-sm text-ink outline-none focus:border-accent-2"
      >
        {LOCALES.map((locale) => (
          <option key={locale.code} value={locale.code}>
            {locale.native}
          </option>
        ))}
      </select>
    </label>
  )
}

export function CurrencySwitcher({ className = '' }) {
  const { t } = useTranslation()
  const { user, config, refreshUser, isAuthed, toast } = useStore()
  const [busy, setBusy] = useState(false)

  const currencies = config?.i18n?.currencies || []
  const settlement = config?.i18n?.settlement_currency || 'USD'
  const current = user?.display_currency || settlement

  if (!isAuthed || currencies.length <= 1) return null

  const change = async (code) => {
    if (code === current) return
    setBusy(true)
    try {
      await api.updateProfile({ display_currency: code })
      await refreshUser()
      toast(`${t('currency.label')}: ${code}`, 'success')
    } catch (err) {
      toast(err.message || 'Could not change currency', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <label className={`inline-flex items-center gap-2 ${className}`}>
      <span className="sr-only">{t('currency.label')}</span>
      <span aria-hidden="true">💱</span>
      <select
        value={current}
        disabled={busy}
        onChange={(event) => change(event.target.value)}
        aria-label={t('currency.label')}
        className="bg-panel-2 border border-line rounded-lg px-2 py-1.5 text-sm text-ink outline-none focus:border-accent-2 disabled:opacity-50"
      >
        {currencies.map((currency) => (
          <option key={currency.code} value={currency.code}>
            {currency.code}
          </option>
        ))}
      </select>
    </label>
  )
}

/**
 * The reminder that a converted figure is a rendering.
 *
 * Shown wherever a localised amount is displayed next to money the player
 * might act on. It is one line, it is translated, and it prevents the single
 * most common support question about a multi-currency casino: "why did my
 * balance change?"
 */
export function DisplayCurrencyNote({ className = '' }) {
  const { t } = useTranslation()
  const { user, config } = useStore()

  const settlement = config?.i18n?.settlement_currency || 'USD'
  const preferred = user?.display_currency || settlement
  if (preferred === settlement) return null

  return (
    <p className={`text-xs text-muted ${className}`}>
      {t('wallet.conversion_note', { settlement })}
    </p>
  )
}
