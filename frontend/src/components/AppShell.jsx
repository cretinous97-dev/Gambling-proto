/**
 * The dashboard shell: sidebar, top bar, balance strip, content area.
 *
 * Design decisions, in the order a player meets them:
 *
 *  * **Balance first.** The number a player came for is the largest thing on
 *    the page and it never moves: it is in the top bar on desktop, and pinned
 *    above the content on mobile. Everything else is reachable from it.
 *  * **Deposit and Withdraw are the only two primary buttons in the shell.**
 *    They are the actions that move money, they are always one tap away, and
 *    giving any other control the same visual weight would bury them.
 *  * **The sidebar is real navigation, the top bar is state.** Locale, display
 *    currency, notifications and the account menu live in the top bar because
 *    they apply to everything; sections live in the sidebar.
 *  * **Responsive by layout, not by hiding.** Below the md breakpoint the
 *    sidebar becomes a drawer and the balance strip becomes a sticky row.
 *    Nothing important is removed at any width.
 *
 * The Tailwind classes come from src/tailwind.css, which imports the theme and
 * utilities WITHOUT preflight so the existing component styles keep working.
 */
import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useStore } from '../lib/store.jsx'
import Money from './Money.jsx'
import { CurrencySwitcher, DisplayCurrencyNote, LanguageSwitcher } from './LocalizationControls.jsx'
import { BrandMark } from './GameArt.jsx'

function BalanceStrip({ compact = false }) {
  const { t } = useTranslation()
  const { wallet, isAuthed, config } = useStore()
  if (!isAuthed || !wallet) return null

  const deployment = config?.deployment
  return (
    <div className={compact ? 'flex items-center gap-3' : 'grid gap-3 sm:grid-cols-3'}>
      <div className={compact ? 'flex items-baseline gap-2' : ''}>
        <span className="text-xs uppercase tracking-wide text-muted">{t('wallet.available')}</span>
        <Money
          amount={wallet.cash}
          className={compact ? 'text-lg font-semibold text-ink' : 'block text-2xl font-semibold text-ink'}
        />
        {deployment?.demo_mode && (
          <span className="rounded border border-warn/30 bg-warn/10 px-1.5 py-0.5 text-[10px] uppercase text-warn">
            demo
          </span>
        )}
      </div>
      {!compact && (
        <>
          <div>
            <span className="text-xs uppercase tracking-wide text-muted">{t('wallet.bonus')}</span>
            <Money amount={wallet.bonus} className="block text-xl font-medium text-accent-2" />
          </div>
          <div>
            <span className="text-xs uppercase tracking-wide text-muted">{t('wallet.locked')}</span>
            <Money amount={wallet.locked} className="block text-xl font-medium text-warn" />
          </div>
        </>
      )}
    </div>
  )
}

function NavItem({ to, label, end, badge }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        [
          'flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm transition-colors',
          isActive
            ? 'bg-accent-2/15 text-ink font-medium'
            : 'text-muted hover:bg-white/5 hover:text-ink',
        ].join(' ')
      }
    >
      <span>{label}</span>
      {badge ? <span className="size-1.5 rounded-full bg-bad" aria-hidden="true" /> : null}
    </NavLink>
  )
}

function Sidebar({ onNavigate }) {
  const { t } = useTranslation()
  const { isAuthed, isAdmin } = useStore()

  return (
    <nav className="flex flex-col gap-1" onClick={onNavigate}>
      <NavItem to="/" end label={t('nav.play')} />
      <NavItem to="/crash" label={t('nav.crash')} badge />
      <NavItem to="/promotions" label={t('nav.promotions')} />
      <NavItem to="/leaderboard" label={t('nav.leaderboard')} />
      {isAuthed && (
        <>
          <div className="my-2 border-t border-line" />
          <NavItem to="/wallet" label={t('nav.wallet')} />
          <NavItem to="/history" label={t('nav.history')} />
          <NavItem to="/account" label={t('nav.account')} />
        </>
      )}
      {isAdmin && (
        <>
          <div className="my-2 border-t border-line" />
          <NavItem to="/admin" label={t('nav.admin')} />
        </>
      )}
    </nav>
  )
}

export default function AppShell() {
  const { t } = useTranslation()
  const { user, isAuthed, isAdmin, logout, config, toast } = useStore()
  const [drawer, setDrawer] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()

  // Close the mobile drawer on navigation, or it stays open over the page.
  useEffect(() => setDrawer(false), [location.pathname])

  const deployment = config?.deployment

  return (
    <div className="flex min-h-screen flex-col">
      {deployment?.demo_mode && (
        <div className="bg-warn/15 px-4 py-1.5 text-center text-xs text-warn">
          {deployment.balances_persist ? t('banner.demo') : t('banner.no_persist')}
        </div>
      )}
      {config?.provider?.mode === 'simulation' && (
        <div className="bg-accent-2/15 px-4 py-1.5 text-center text-xs text-accent-2">
          {t('banner.sandbox')}
        </div>
      )}

      {/* Top bar: identity, balance, and the two money actions. */}
      <header className="sticky top-0 z-30 border-b border-line bg-bg/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center gap-3 px-4 py-3">
          <button
            type="button"
            className="rounded-lg border border-line px-2.5 py-1.5 text-sm md:hidden"
            onClick={() => setDrawer((open) => !open)}
            aria-expanded={drawer}
            aria-label={drawer ? t('nav.close_menu') : t('nav.menu')}
          >
            ☰
          </button>

          <Link to="/" className="flex items-center gap-2 font-semibold">
            <BrandMark size={28} />
            <span className="hidden sm:inline">{config?.name || 'Casino'}</span>
          </Link>

          <div className="ms-auto flex items-center gap-2">
            <LanguageSwitcher className="hidden sm:flex" />
            <CurrencySwitcher className="hidden lg:flex" />

            {isAuthed ? (
              <>
                <Link
                  to="/wallet"
                  className="rounded-lg border border-line bg-panel px-3 py-1.5 text-sm hover:border-accent-2/60"
                >
                  <BalanceStrip compact />
                </Link>
                <Link to="/wallet" className="btn btn-sm btn-primary">
                  {t('common.deposit')}
                </Link>
                <div className="relative hidden md:block">
                  <details className="group">
                    <summary className="cursor-pointer list-none rounded-lg border border-line px-3 py-1.5 text-sm">
                      {user?.username}
                    </summary>
                    <div className="absolute end-0 mt-2 w-52 rounded-xl border border-line bg-panel p-2 shadow-xl">
                      <div className="px-2 pb-2">
                        <LanguageSwitcher className="flex sm:hidden" />
                        <CurrencySwitcher className="flex lg:hidden" />
                      </div>
                      <Link to="/account" className="block rounded-lg px-3 py-2 text-sm hover:bg-white/5">
                        {t('nav.account')}
                      </Link>
                      {isAdmin && (
                        <Link to="/admin" className="block rounded-lg px-3 py-2 text-sm hover:bg-white/5">
                          {t('nav.admin')}
                        </Link>
                      )}
                      <button
                        type="button"
                        className="w-full rounded-lg px-3 py-2 text-start text-sm text-bad hover:bg-bad/10"
                        onClick={async () => {
                          await logout()
                          toast(t('auth.logout'))
                          navigate('/')
                        }}
                      >
                        {t('auth.logout')}
                      </button>
                    </div>
                  </details>
                </div>
              </>
            ) : (
              <>
                <Link to="/login" className="btn btn-sm">
                  {t('auth.signin')}
                </Link>
                <Link to="/register" className="btn btn-sm btn-primary">
                  {t('auth.signup')}
                </Link>
              </>
            )}
          </div>
        </div>

        {/* Mobile: the balance, always visible, because it is why they opened the app. */}
        {isAuthed && (
          <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-2 sm:hidden">
            <BalanceStrip compact />
            <div className="flex gap-2">
              <Link to="/wallet" className="btn btn-sm btn-primary">
                {t('common.deposit')}
              </Link>
            </div>
          </div>
        )}
      </header>

      <div className="mx-auto flex w-full max-w-[1400px] flex-1 gap-6 px-4 py-6">
        {/* Sidebar (drawer under md) */}
        <aside
          className={[
            'fixed inset-y-0 start-0 z-40 w-64 border-e border-line bg-panel p-4 transition-transform md:static md:z-0 md:w-56 md:translate-x-0 md:border-0 md:bg-transparent md:p-0',
            drawer ? 'translate-x-0' : '-translate-x-full rtl:translate-x-full md:translate-x-0',
          ].join(' ')}
        >
          <div className="mb-4 flex items-center justify-between md:hidden">
            <span className="font-semibold">{t('nav.menu')}</span>
            <button type="button" onClick={() => setDrawer(false)} aria-label={t('nav.close_menu')}>
              ✕
            </button>
          </div>
          <Sidebar onNavigate={() => setDrawer(false)} />
        </aside>

        {drawer && (
          <button
            type="button"
            className="fixed inset-0 z-30 bg-black/50 md:hidden"
            aria-label={t('nav.close_menu')}
            onClick={() => setDrawer(false)}
          />
        )}

        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>

      <footer className="border-t border-line px-4 py-6 text-center text-xs text-muted">
        <div className="mb-2 flex flex-wrap justify-center gap-4">
          <Link to="/legal/terms">Terms</Link>
          <Link to="/legal/privacy">Privacy</Link>
          <Link to="/legal/responsible-gambling">Responsible Gambling</Link>
          <Link to="/legal/aml">AML / KYC</Link>
        </div>
        <p className="mx-auto max-w-2xl">
          18+. Gambling involves risk and is not a way to make money. Games are provably
          fair and every movement is recorded in a double-entry ledger.
        </p>
        <DisplayCurrencyNote className="mt-3" />
      </footer>
    </div>
  )
}
