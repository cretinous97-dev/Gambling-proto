/**
 * Internationalization.
 *
 * The routing decision, and why it works this way
 * -----------------------------------------------
 * The locale is the first path segment: `/es/wallet`, `/ar/crash`, `/zh/admin`.
 * That is what makes a localised URL shareable and indexable, and it is what
 * the backend advertises in `/api/config` (`i18n.locales`).
 *
 * Rather than prefix every `to="/wallet"` in twenty page components, the router
 * is mounted with `basename="/es"`. React Router then resolves every existing
 * relative link and `useNavigate` call against the locale automatically, so the
 * prefix stays on every navigation with no call site needing to know it exists.
 * That is the whole trick - and the reason a locale change is a full page load
 * (the basename cannot change under a mounted router).
 *
 * Detection order: URL segment, then the player's saved choice, then the
 * browser, then the country the edge reported, then English. The order matters:
 * an explicit choice beats a guess, and a guess about a *country* is the
 * weakest signal available, so it goes last.
 */
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import ar from './locales/ar.json'
import de from './locales/de.json'
import en from './locales/en.json'
import es from './locales/es.json'
import fr from './locales/fr.json'
import hi from './locales/hi.json'
import it from './locales/it.json'
import pt from './locales/pt.json'
import zh from './locales/zh.json'

export const DEFAULT_LOCALE = 'en'
export const LOCALE_STORAGE_KEY = 'casino.locale'

/** Mirrors app/i18n.py::LOCALES. `dir` drives the document direction. */
export const LOCALES = [
  { code: 'en', name: 'English', native: 'English', dir: 'ltr', flag: '🇬🇧' },
  { code: 'es', name: 'Spanish', native: 'Español', dir: 'ltr', flag: '🇪🇸' },
  { code: 'pt', name: 'Portuguese', native: 'Português', dir: 'ltr', flag: '🇧🇷' },
  { code: 'de', name: 'German', native: 'Deutsch', dir: 'ltr', flag: '🇩🇪' },
  { code: 'fr', name: 'French', native: 'Français', dir: 'ltr', flag: '🇫🇷' },
  { code: 'it', name: 'Italian', native: 'Italiano', dir: 'ltr', flag: '🇮🇹' },
  { code: 'zh', name: 'Chinese (Simplified)', native: '简体中文', dir: 'ltr', flag: '🇨🇳' },
  { code: 'hi', name: 'Hindi', native: 'हिन्दी', dir: 'ltr', flag: '🇮🇳' },
  { code: 'ar', name: 'Arabic', native: 'العربية', dir: 'rtl', flag: '🇸🇦' },
]

export const LOCALE_CODES = LOCALES.map((l) => l.code)

const RESOURCES = { en, es, pt, de, fr, it, zh, hi, ar }

export function isLocale(code) {
  return LOCALE_CODES.includes(String(code || '').toLowerCase())
}

export function localeMeta(code) {
  return LOCALES.find((l) => l.code === code) || LOCALES[0]
}

export function dirFor(code) {
  return localeMeta(code).dir
}

i18n.use(initReactI18next).init({
  resources: Object.fromEntries(
    Object.entries(RESOURCES).map(([code, translation]) => [code, { translation }]),
  ),
  lng: DEFAULT_LOCALE,
  fallbackLng: DEFAULT_LOCALE,
  supportedLngs: LOCALE_CODES,
  // 'en-GB' should resolve to 'en' rather than rendering raw keys.
  load: 'languageOnly',
  nonExplicitSupportedLngs: true,
  interpolation: { escapeValue: false },   // React already escapes
  returnNull: false,
})

/**
 * Read the locale out of a pathname: `/es/wallet` -> `es`.
 * Returns null when the first segment is not a locale.
 */
export function localeFromPath(pathname) {
  const first = String(pathname || '/').split('?')[0].split('/').filter(Boolean)[0]
  return isLocale(first) ? first.toLowerCase() : null
}

/** The same path with a different locale prefix: `/es/wallet` -> `/ar/wallet`. */
export function pathWithLocale(pathname, locale) {
  const parts = String(pathname || '/').split('/').filter(Boolean)
  if (isLocale(parts[0])) parts.shift()
  return `/${locale}${parts.length ? `/${parts.join('/')}` : ''}`
}

/** Pick the best locale for a first visit. */
export function detectLocale({ region, acceptLanguage } = {}) {
  try {
    const saved = window.localStorage.getItem(LOCALE_STORAGE_KEY)
    if (isLocale(saved)) return saved
  } catch {
    /* private mode / storage disabled - fall through */
  }

  const languages = acceptLanguage
    || (typeof navigator !== 'undefined' ? navigator.languages?.join(',') || navigator.language : '')
  for (const tag of String(languages || '').split(',')) {
    const base = tag.split(';')[0].trim().toLowerCase().split('-')[0]
    if (isLocale(base)) return base
  }

  // Region is a hint about where the player is, not about what they read.
  if (region && isLocale(region)) return String(region).toLowerCase()

  return DEFAULT_LOCALE
}

export function rememberLocale(code) {
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, code)
  } catch {
    /* not fatal: the URL still carries the choice */
  }
}

/** Apply language + direction to the document (and to i18next). */
export function applyLocale(code) {
  const meta = localeMeta(code)
  i18n.changeLanguage(meta.code)
  if (typeof document !== 'undefined') {
    document.documentElement.lang = meta.code
    document.documentElement.dir = meta.dir
  }
  rememberLocale(meta.code)
  return meta
}

export default i18n
