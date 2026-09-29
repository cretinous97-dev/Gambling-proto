/**
 * The wallet dashboard.
 *
 * Layout, top to bottom, in the order a player's questions arrive:
 *
 *  1. **How much do I have?** - one hero figure, with bonus and locked shown
 *     underneath so the total is never a surprise. Locked money is explained
 *     as "in play" rather than hidden, because a player who cannot see their
 *     money assumes it is gone.
 *  2. **What can I do?** - Add funds and Cash out, the only two primary
 *     actions, above the fold. Both open a sheet; the sheet is where the form
 *     lives so the dashboard never turns into a form.
 *  3. **What is happening right now?** - in-flight deposits and withdrawals,
 *     with the status that says who we are waiting for. A withdrawal that was
 *     auto-approved looks different from one a human has to review, and both
 *     look different from money still sitting at a payment processor.
 *  4. **What happened?** - the settled ledger, newest first, filterable.
 *
 * Every amount goes through <Money>, which converts for display and keeps the
 * settlement figure in the tooltip. Nothing on this page computes money.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import Money from '../components/Money.jsx'
import StatusChip from '../components/StatusChip.jsx'
import { DisplayCurrencyNote } from '../components/LocalizationControls.jsx'
import { api } from '../lib/api.js'
import { useStore } from '../lib/store.jsx'
import { toAmountString } from '../lib/money.js'
import { fmtDate } from '../lib/format.js'

const PAGE_SIZE = 15

function StatCard({ label, children, tone = 'default', hint }) {
  const tones = {
    default: 'border-line bg-panel',
    accent: 'border-accent-2/40 bg-accent-2/10',
    warn: 'border-warn/40 bg-warn/10',
  }
  return (
    <div className={`rounded-2xl border p-4 ${tones[tone]}`}>
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{children}</div>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  )
}

function Sheet({ title, onClose, children }) {
  const { t } = useTranslation()
  useEffect(() => {
    const onKey = (event) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 sm:items-center sm:p-6">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="max-h-[92vh] w-full overflow-y-auto rounded-t-3xl border border-line bg-panel p-5 sm:max-w-lg sm:rounded-2xl"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button type="button" onClick={onClose} className="btn btn-sm" aria-label={t('common.close')}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

function DepositSheet({ onDone }) {
  const { t } = useTranslation()
  const { config } = useStore()
  const navigate = useNavigate()
  const [amount, setAmount] = useState('')
  const [method, setMethod] = useState('card')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const limits = config?.limits || {}
  const suggestions = [10, 25, 50, 100, 250]

  const submit = async (event) => {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      const body = { amount: toAmountString(amount), method }
      const created = await api.createDeposit(body)
      onDone?.()
      // The checkout page owns the next step (redirect, wallet address, or the
      // sandbox confirmation), because every provider continues differently.
      navigate(`/checkout/${created.deposit_id}`)
    } catch (err) {
      setError(err.message || 'Deposit could not be started')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div>
        <label className="mb-1 block text-sm text-muted" htmlFor="deposit-amount">
          {t('common.deposit')}
        </label>
        <input
          id="deposit-amount"
          className="w-full rounded-xl border border-line bg-panel-2 px-4 py-3 text-2xl money"
          inputMode="decimal"
          autoFocus
          value={amount}
          onChange={(event) => setAmount(toAmountString(event.target.value))}
          placeholder="0.00"
          required
        />
        <div className="mt-2 flex flex-wrap gap-2">
          {suggestions.map((value) => (
            <button
              key={value}
              type="button"
              className="rounded-lg border border-line px-3 py-1 text-sm hover:border-accent-2"
              onClick={() => setAmount(String(value))}
            >
              {value}
            </button>
          ))}
        </div>
        {limits.min_deposit ? (
          <p className="mt-2 text-xs text-muted">
            min {limits.min_deposit / 100} · max {limits.max_deposit / 100}
          </p>
        ) : null}
      </div>

      <div>
        <label className="mb-1 block text-sm text-muted" htmlFor="deposit-method">
          Payment method
        </label>
        <select
          id="deposit-method"
          className="w-full rounded-xl border border-line bg-panel-2 px-3 py-2"
          value={method}
          onChange={(event) => setMethod(event.target.value)}
        >
          <option value="card">Card</option>
          <option value="bank_transfer">Bank transfer</option>
          <option value="crypto_usdt">USDT</option>
          <option value="crypto_btc">Bitcoin</option>
          <option value="crypto_eth">Ethereum</option>
        </select>
      </div>

      {error && <p className="rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad">{error}</p>}

      <button type="submit" className="btn btn-primary btn-block" disabled={busy || !amount}>
        {busy ? t('common.loading') : t('common.continue')}
      </button>
      <DisplayCurrencyNote />
    </form>
  )
}

function WithdrawSheet({ onClose, onDone, wallet }) {
  const { t } = useTranslation()
  const { config, toast } = useStore()
  const [amount, setAmount] = useState('')
  const [method, setMethod] = useState('crypto_usdt')
  const [destination, setDestination] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const limits = config?.limits || {}
  const available = wallet?.withdrawable ?? 0

  const submit = async (event) => {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      await api.createWithdrawal({
        amount: toAmountString(amount),
        method,
        destination: destination.trim(),
      })
      toast('Withdrawal requested', 'success')
      onDone?.()
      onClose()
    } catch (err) {
      setError(err.message || 'Withdrawal could not be requested')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="rounded-xl border border-line bg-panel-2 p-3 text-sm">
        <div className="flex justify-between">
          <span className="text-muted">{t('wallet.withdrawable')}</span>
          <Money amount={available} className="font-semibold" />
        </div>
        {wallet?.locked > 0 && (
          <div className="mt-1 flex justify-between text-xs text-muted">
            <span>{t('wallet.locked')}</span>
            <Money amount={wallet.locked} />
          </div>
        )}
      </div>

      <div>
        <label className="mb-1 block text-sm text-muted" htmlFor="withdraw-amount">
          {t('common.withdraw')}
        </label>
        <input
          id="withdraw-amount"
          className="w-full rounded-xl border border-line bg-panel-2 px-4 py-3 text-2xl money"
          inputMode="decimal"
          autoFocus
          value={amount}
          onChange={(event) => setAmount(toAmountString(event.target.value))}
          placeholder="0.00"
          required
        />
        <button
          type="button"
          className="mt-2 text-xs text-accent-2 underline"
          onClick={() => setAmount((available / 100).toFixed(2))}
        >
          Use full balance
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-sm text-muted" htmlFor="withdraw-method">
            Method
          </label>
          <select
            id="withdraw-method"
            className="w-full rounded-xl border border-line bg-panel-2 px-3 py-2"
            value={method}
            onChange={(event) => setMethod(event.target.value)}
          >
            <option value="crypto_usdt">USDT (TRC-20)</option>
            <option value="crypto_btc">Bitcoin</option>
            <option value="crypto_eth">Ethereum</option>
            <option value="bank_transfer">Bank transfer</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-sm text-muted" htmlFor="withdraw-destination">
            Destination
          </label>
          <input
            id="withdraw-destination"
            className="w-full rounded-xl border border-line bg-panel-2 px-3 py-2"
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
            placeholder={method.startsWith('crypto') ? 'Wallet address' : 'IBAN / account'}
            required
            minLength={4}
          />
        </div>
      </div>

      <p className="text-xs text-muted">
        min {limits.min_withdrawal / 100} · max {limits.max_withdrawal / 100} · identity
        verification is required above {limits.kyc_required_above / 100} in lifetime
        withdrawals.
      </p>

      {error && <p className="rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad">{error}</p>}

      <button type="submit" className="btn btn-primary btn-block" disabled={busy || !amount}>
        {busy ? t('common.loading') : t('common.withdraw')}
      </button>
    </form>
  )
}

function PendingRow({ item }) {
  const { t } = useTranslation()
  return (
    <li className="flex items-center justify-between gap-3 rounded-xl border border-line bg-panel-2/60 px-3 py-2.5">
      <div className="min-w-0">
        <div className="truncate text-sm font-medium">
          {t(`kinds.${item.kind === 'deposit' ? 'deposit' : 'withdrawal_hold'}`)}
        </div>
        <div className="text-xs text-muted">{fmtDate(item.created_at)}</div>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <Money amount={item.amount} signed className="font-medium" />
        <StatusChip status={item.status} />
        {item.kind === 'deposit' && item.resumable && (
          <Link to={`/checkout/${item.id}`} className="btn btn-sm">
            {t('common.continue')}
          </Link>
        )}
      </div>
    </li>
  )
}

export default function Wallet() {
  const { t } = useTranslation()
  const { wallet, refreshWallet, toast } = useStore()
  const [sheet, setSheet] = useState(null)
  const [history, setHistory] = useState({ entries: [], pending: [], total: 0 })
  const [page, setPage] = useState(0)
  const [kind, setKind] = useState('')
  const [loading, setLoading] = useState(true)

  const loadHistory = useCallback(async () => {
    setLoading(true)
    try {
      const query = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(page * PAGE_SIZE),
      })
      if (kind) query.set('kind', kind)
      const data = await api.transactions(`?${query.toString()}`)
      setHistory(data)
    } catch (err) {
      toast(err.message || 'Could not load transactions', 'error')
    } finally {
      setLoading(false)
    }
  }, [page, kind, toast])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  const refreshAll = async () => {
    await Promise.all([refreshWallet(), loadHistory()])
  }

  const kinds = useMemo(
    () => ['', 'deposit', 'withdrawal_settled', 'bet_stake', 'bet_payout', 'bonus_claim'],
    [],
  )

  const pages = Math.max(1, Math.ceil((history.total || 0) / PAGE_SIZE))

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{t('wallet.title')}</h1>
          <p className="text-sm text-muted">{t('wallet.subtitle')}</p>
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn btn-primary" onClick={() => setSheet('deposit')}>
            {t('wallet.deposit_cta')}
          </button>
          <button type="button" className="btn" onClick={() => setSheet('withdraw')}>
            {t('wallet.withdraw_cta')}
          </button>
        </div>
      </div>

      {/* 1. the balance, in a hierarchy that adds up */}
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label={t('wallet.available')} tone="accent">
          <Money amount={wallet?.cash ?? 0} />
        </StatCard>
        <StatCard label={t('wallet.bonus')} hint={t('wallet.pending_wager')}>
          <Money amount={wallet?.bonus ?? 0} />
        </StatCard>
        <StatCard label={t('wallet.locked')} tone={wallet?.locked ? 'warn' : 'default'}>
          <Money amount={wallet?.locked ?? 0} />
        </StatCard>
        <StatCard label={t('wallet.total')}>
          <Money amount={(wallet?.cash ?? 0) + (wallet?.bonus ?? 0)} />
        </StatCard>
      </section>

      <DisplayCurrencyNote />

      {/* 2. what is in flight right now */}
      {history.pending?.length > 0 && (
        <section className="rounded-2xl border border-line bg-panel p-4">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
            {t('tx.in_flight')}
          </h2>
          <ul className="space-y-2">
            {history.pending.map((item) => (
              <PendingRow key={`${item.kind}-${item.id}`} item={item} />
            ))}
          </ul>
        </section>
      )}

      {/* 3. the settled ledger */}
      <section className="rounded-2xl border border-line bg-panel">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line p-4">
          <h2 className="font-semibold">{t('tx.title')}</h2>
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-xs text-muted" htmlFor="tx-kind">
              {t('tx.kind')}
            </label>
            <select
              id="tx-kind"
              className="rounded-lg border border-line bg-panel-2 px-2 py-1.5 text-sm"
              value={kind}
              onChange={(event) => {
                setPage(0)
                setKind(event.target.value)
              }}
            >
              {kinds.map((value) => (
                <option key={value || 'all'} value={value}>
                  {value ? t(`kinds.${value}`) : t('common.all')}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn-sm" onClick={refreshAll}>
              ↻
            </button>
          </div>
        </div>

        {loading ? (
          <p className="p-6 text-sm text-muted">{t('common.loading')}</p>
        ) : history.entries.length === 0 ? (
          <p className="p-6 text-sm text-muted">{t('tx.empty')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wide text-muted">
                <tr className="border-b border-line">
                  <th className="p-3 text-start font-medium">{t('tx.kind')}</th>
                  <th className="p-3 text-start font-medium">{t('tx.date')}</th>
                  <th className="p-3 text-end font-medium">{t('tx.amount')}</th>
                  <th className="hidden p-3 text-end font-medium sm:table-cell">
                    {t('tx.balance_after')}
                  </th>
                  <th className="p-3 text-end font-medium">{t('tx.status')}</th>
                </tr>
              </thead>
              <tbody>
                {history.entries.map((entry) => (
                  <tr key={entry.id} className="border-b border-line/50 last:border-0">
                    <td className="p-3">
                      <div className="font-medium">
                        {t(`kinds.${entry.kind}`, { defaultValue: entry.kind })}
                      </div>
                      {entry.memo && <div className="text-xs text-muted">{entry.memo}</div>}
                    </td>
                    <td className="whitespace-nowrap p-3 text-muted">{fmtDate(entry.created_at)}</td>
                    <td
                      className={`p-3 text-end font-medium ${
                        entry.amount > 0 ? 'text-ok' : 'text-ink'
                      }`}
                    >
                      <Money amount={entry.amount} signed />
                    </td>
                    <td className="hidden p-3 text-end text-muted sm:table-cell">
                      <Money amount={entry.balance_after} />
                    </td>
                    <td className="p-3 text-end">
                      <StatusChip status={entry.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pages > 1 && (
          <div className="flex items-center justify-between border-t border-line p-3 text-sm">
            <button
              type="button"
              className="btn btn-sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              {t('common.previous')}
            </button>
            <span className="text-muted">
              {page + 1} {t('common.of')} {pages}
            </span>
            <button
              type="button"
              className="btn btn-sm"
              disabled={page + 1 >= pages}
              onClick={() => setPage((p) => p + 1)}
            >
              {t('common.next')}
            </button>
          </div>
        )}
      </section>

      {sheet === 'deposit' && (
        <Sheet title={t('common.deposit')} onClose={() => setSheet(null)}>
          <DepositSheet onDone={refreshAll} />
        </Sheet>
      )}
      {sheet === 'withdraw' && (
        <Sheet title={t('common.withdraw')} onClose={() => setSheet(null)}>
          <WithdrawSheet onClose={() => setSheet(null)} onDone={refreshAll} wallet={wallet} />
        </Sheet>
      )}
    </div>
  )
}
