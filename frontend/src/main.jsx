import React, { useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import AppShell from './components/AppShell.jsx'
import LocaleRouter from './i18n/LocaleRouter.jsx'
import {
  LOCALE_CODES,
  LOCALE_STORAGE_KEY,
  LOCALES,
  localeFromPath,
  pathWithLocale,
} from './i18n/index.js'
import { StoreProvider, useStore } from './lib/store.jsx'
import './styles.css'

import Home from './pages/Home.jsx'
import Login from './pages/Login.jsx'
import Register from './pages/Register.jsx'
import Wallet from './pages/Wallet.jsx'
import Checkout from './pages/Checkout.jsx'
import Account from './pages/Account.jsx'
import History from './pages/History.jsx'
import Leaderboard from './pages/Leaderboard.jsx'
import Promotions from './pages/Promotions.jsx'
import Legal from './pages/Legal.jsx'
import GameRoom from './pages/GameRoom.jsx'
import Crash from './pages/Crash.jsx'
import Admin from './pages/Admin.jsx'
import NotFound from './pages/NotFound.jsx'

function RequireAuth({ children }) {
  const { isAuthed, loading } = useStore()
  const location = useLocation()
  if (loading) return null
  if (!isAuthed) return <Navigate to="/login" state={{ from: location }} replace />
  return children
}

function RequireAdmin({ children }) {
  const { user, loading } = useStore()
  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'admin') return <Navigate to="/" replace />
  return children
}

/**
 * Offer the language the region suggests - never impose it.
 *
 * The edge knows the caller's country; it does not know which language they
 * read. A silent redirect to the country's default would be wrong for every
 * German speaker in Brazil, every English speaker in Dubai and every tourist
 * anywhere, so this asks once and remembers the answer either way.
 */
function RegionLanguageHint() {
  const { t } = useTranslation()
  const { config } = useStore()
  const [dismissed, setDismissed] = useState(
    () => window.localStorage.getItem('casino.region_hint_dismissed') === '1',
  )

  const current = localeFromPath(window.location.pathname) || 'en'
  const suggested = config?.region?.suggested_locale
  const alreadyChosen = Boolean(window.localStorage.getItem(LOCALE_STORAGE_KEY))

  if (dismissed || alreadyChosen || !suggested) return null
  if (suggested === current || !LOCALE_CODES.includes(suggested)) return null

  const target = LOCALES.find((l) => l.code === suggested)

  const dismiss = () => {
    window.localStorage.setItem('casino.region_hint_dismissed', '1')
    setDismissed(true)
  }

  return (
    <div className="flex items-center justify-center gap-3 bg-accent-2/15 px-4 py-2 text-xs">
      <span>{target.native}?</span>
      <button
        type="button"
        className="btn btn-sm btn-primary"
        onClick={() => {
          const { pathname, search } = window.location
          window.location.assign(pathWithLocale(pathname, suggested) + search)
        }}
      >
        {target.native}
      </button>
      <button type="button" className="btn btn-sm" onClick={dismiss}>
        {t('common.close')}
      </button>
    </div>
  )
}

function App() {
  useEffect(() => {
    // Keep the tab title in the player's language too; it is the first thing
    // they see in a task switcher.
    document.title = 'Naktsang Casino'
  }, [])

  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<Home />} />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/wallet" element={<RequireAuth><Wallet /></RequireAuth>} />
        <Route path="/checkout/:depositId" element={<RequireAuth><Checkout /></RequireAuth>} />
        <Route path="/account" element={<RequireAuth><Account /></RequireAuth>} />
        <Route path="/history" element={<RequireAuth><History /></RequireAuth>} />
        <Route path="/leaderboard" element={<Leaderboard />} />
        <Route path="/promotions" element={<Promotions />} />
        <Route path="/legal/:doc" element={<Legal />} />
        <Route path="/crash" element={<Crash />} />
        <Route path="/game/:slug" element={<GameRoom />} />
        <Route path="/admin/*" element={<RequireAdmin><Admin /></RequireAdmin>} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <LocaleRouter>
      <StoreProvider>
        <RegionLanguageHint />
        <App />
      </StoreProvider>
    </LocaleRouter>
  </React.StrictMode>,
)
