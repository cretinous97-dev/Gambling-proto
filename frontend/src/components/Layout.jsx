import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useStore } from '../lib/store.jsx'
import { ProviderBanner } from './ui.jsx'
import { BrandMark } from './GameArt.jsx'
import { fmtCents } from '../lib/format.js'

export default function Layout() {
  const { user, wallet, isAuthed, isAdmin, logout, config, toast } = useStore()
  const navigate = useNavigate()

  const nav = [
    { to: '/', label: 'Lobby', end: true },
    { to: '/crash', label: 'Crash', live: true },
    { to: '/promotions', label: 'Promotions' },
    { to: '/leaderboard', label: 'Leaderboard' },
    { to: '/history', label: 'My Bets', auth: true },
    { to: '/account', label: 'Account', auth: true },
  ]

  return (
    <div className="app">
      <ProviderBanner provider={config?.provider} deployment={config?.deployment} />

      <header className="topbar">
        <Link to="/" className="brand">
          <BrandMark size={32} />
          <span>{config?.name || 'Naktsang Casino'}</span>
        </Link>

        <nav className="nav">
          {nav
            .filter((item) => !item.auth || isAuthed)
            .map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end}>
                {item.label}
                {item.live && <span style={{ color: 'var(--bad)', marginLeft: 5 }}>●</span>}
              </NavLink>
            ))}
          {isAdmin && <NavLink to="/admin">Admin</NavLink>}
        </nav>

        <div className="topbar-right">
          {isAuthed ? (
            <>
              <Link to="/wallet" className="balance-chip">
                <span>{fmtCents(wallet?.cash ?? 0)}</span>
                {wallet?.bonus > 0 && (
                  <small title="Bonus balance - playable, not withdrawable">
                    +{fmtCents(wallet.bonus)}
                  </small>
                )}
              </Link>
              {wallet?.locked > 0 && (
                <span className="badge badge-warn" title="Reserved for a pending withdrawal or open bet">
                  {fmtCents(wallet.locked)} locked
                </span>
              )}
              <button
                className="btn btn-sm"
                onClick={async () => {
                  await logout()
                  toast('Signed out')
                  navigate('/')
                }}
              >
                Sign out
              </button>
            </>
          ) : (
            <>
              <Link to="/login" className="btn btn-sm">Log in</Link>
              <Link to="/register" className="btn btn-sm btn-primary">Sign up</Link>
            </>
          )}
        </div>
      </header>

      <main style={{ flex: 1 }}>
        <Outlet />
      </main>

      <footer className="footer">
        <div className="links">
          <Link to="/legal/terms">Terms</Link>
          <Link to="/legal/privacy">Privacy</Link>
          <Link to="/legal/responsible-gambling">Responsible Gambling</Link>
          <Link to="/legal/aml">AML / KYC</Link>
          <Link to="/promotions">Promotions</Link>
        </div>
        <div>
          18+ only. Gambling involves risk and is not a way to make money. All games are
          provably fair and every transaction is recorded in a double-entry ledger you can audit.
        </div>
        <div className="tiny" style={{ marginTop: 6 }}>
          {config?.environment === 'development'
            ? 'Development build — operator must complete licensing, KYC/AML providers and live payment onboarding before accepting real money (see README).'
            : 'Licensed operator details must appear here.'}
        </div>
      </footer>

      {toast && (
        <div className={`toast ${toast.kind === 'error' ? 'error' : toast.kind === 'success' ? 'success' : ''}`}>
          {toast.message}
        </div>
      )}
    </div>
  )
}
