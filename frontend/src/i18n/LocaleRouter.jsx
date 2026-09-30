/**
 * Locale-prefixed routing.
 *
 * The problem: the locale has to be in the URL (`/es/wallet`) because that is
 * what makes a link shareable and indexable, but there are twenty page
 * components and hundreds of `<Link to="/wallet">` and `navigate('/crash')`
 * calls. Threading a prefix through every one of them is a large diff whose
 * failure mode is a link that quietly drops the locale.
 *
 * The solution: `BrowserRouter basename={locale}`. React Router resolves every
 * relative `to` and every `navigate()` against the basename, so the prefix is
 * applied by the router, not by the call site - no component changes, nothing
 * to forget. The cost is that the basename cannot change while the router is
 * mounted, which is why switching language is a navigation (see
 * LanguageSwitcher) rather than a state update.
 *
 * Two details worth knowing:
 *
 *  * A URL with no locale (`/wallet`) is redirected to the detected locale
 *    once, preserving the rest of the path. Users paste links without prefixes.
 *  * The document `lang` and `dir` are set from the locale, so Arabic renders
 *    right-to-left without any component knowing about it.
 */
import { useEffect, useMemo } from 'react'
import { BrowserRouter } from 'react-router-dom'

import {
  DEFAULT_LOCALE,
  applyLocale,
  detectLocale,
  localeFromPath,
  pathWithLocale,
} from './index.js'

/**
 * Send the browser to the localised version of the path it asked for, once,
 * before anything renders. A full navigation avoids mounting the router twice
 * with two different basenames.
 */
function redirectToLocale(locale) {
  const { pathname, search, hash } = window.location
  const target = pathWithLocale(pathname, locale) + search + hash
  window.location.replace(target)
}

export default function LocaleRouter({ children, region }) {
  const locale = useMemo(
    () => localeFromPath(window.location.pathname),
    [],
  )

  // Missing or unknown prefix: pick one and redirect. done once, at boot.
  const needsRedirect = locale === null
  const resolved = locale || detectLocale({ region })

  useEffect(() => {
    if (needsRedirect) redirectToLocale(resolved)
  }, [needsRedirect, resolved])

  useEffect(() => {
    applyLocale(resolved)
  }, [resolved])

  if (needsRedirect) {
    // Nothing renders while the redirect is in flight; a flash of English
    // before the Arabic page loads is worse than a blank frame.
    return null
  }

  return (
    <BrowserRouter basename={`/${resolved}`}>
      {children}
    </BrowserRouter>
  )
}

export { DEFAULT_LOCALE }
