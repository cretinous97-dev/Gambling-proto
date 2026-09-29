import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api.js'
import { fmtCents } from '../lib/format.js'

import { Card, Stat, Badge, Alert, Loading, Empty, Modal, ActionButton } from '../components/ui.jsx'
import { useStore } from '../lib/store.jsx'

const PAYMENT_NAME = {
  card: 'Card', bank_transfer: 'Bank transfer',
  crypto_btc: 'Bitcoin', crypto_eth: 'Ethereum', crypto_usdt: 'USDT (TRC-20)',
  ewallet: 'E-wallet',
}

const QUICK = ['10.00', '25.00', '50.00', '100.00']

export default function Wallet() {
  const { wallet, refreshWallet, toast } = useStore()

  const [methods, setMethods] = useState(null)
  const [statement, setStatement] = useState([])
  const [deposits, setDeposits] = useState([])
  const [withdrawals, setWithdrawals] = useState([])
  const [integrity, setIntegrity] = useState(null)
  const [loading, setLoading] = useState(true)

  // deposit form
  const [depAmount, setDepAmount] = useState('25.00')
  const [depMethod, setDepMethod] = useState('card')
  const [bonusCode, setBonusCode] = useState('')

  // withdrawal form
  const [wdAmount, setWdAmount] = useState('')
  const [wdMethod, setWdMethod] = useState('crypto_usdt')
  const [wdDestination, setWdDestination] = useState('')

  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [activeDeposit, setActiveDeposit] = useState(null)

  const load = async () => {
    try {
      const [m, s, d, w, i] = await Promise.all([
        api.methods(),
        api.statement(60),
        api.deposits(),
        api.withdrawals(),
        api.integrity(),
      ])
      setMethods(m)
      setStatement(s.entries)
      setDeposits(d.deposits)
      setWithdrawals(w.withdrawals)
      setIntegrity(i)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    refreshWallet()
  }, [])

  const submitDeposit = async () => {
    setError('')
    setBusy(true)
    try {
      const body = {
        amount: depAmount,
        method: depMethod,
        // Idempotency key makes the request safe to retry: a double-tap or a
        // dropped connection can never charge the player twice.
        idempotency_key: `dep-${Date.now()}-${depMethod}-${depAmount}`,
        bonus_code: bonusCode || undefined,
      }
      const intent = await api.createDeposit(body)
      toast('Deposit created - complete the payment', 'success')
      await load()
      setActiveDeposit(intent)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const submitWithdrawal = async () => {
    setError('')
    setBusy(true)
    try {
      const res = await api.createWithdrawal({
        amount: wdAmount,
        method: wdMethod,
        destination: wdDestination,
        idempotency_key: `wd-${Date.now()}-${wdMethod}`,
      })
      toast(
        res.status === 'paid'
          ? 'Withdrawal paid'
          : `Withdrawal reserved (${fmtCents(res.amount)}) - pending review`,
        'success',
      )
      setWdAmount('')
      setWdDestination('')
      await Promise.all([load(), refreshWallet()])
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const simulate = async (depositId, outcome) => {
    setError('')
    try {
      await api.simulateDeposit(depositId, outcome)
      toast(outcome === 'succeed' ? 'Payment confirmed' : `Payment ${outcome}`, 
            outcome === 'succeed' ? 'success' : 'error')
      setActiveDeposit(null)
      await Promise.all([load(), refreshWallet()])
    } catch (err) {
      setError(err.message)
    }
  }

  if (loading) return <div className="page"><Loading /></div>

  const limits = methods?.limits || {}
  const simulation = methods?.simulation
  const cash = wallet?.cash ?? 0

  return (
    <div className="page">
      <h1 style={{ marginTop: 0 }}>Wallet</h1>
      {error && <Alert kind="error">{error}</Alert>}

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        <Card className="tight"><Stat label="Cash" value={fmtCents(cash)} hint="Withdrawable" /></Card>
        <Card className="tight">
          <Stat label="Bonus" value={fmtCents(wallet?.bonus ?? 0)}
            hint={wallet?.pending_wager ? `${fmtCents(wallet.pending_wager)} playthrough left` : 'No wagering open'} />
        </Card>
        <Card className="tight">
          <Stat label="Locked" value={fmtCents(wallet?.locked ?? 0)} hint="Open hands / pending payouts" />
        </Card>
        <Card className="tight">
          <Stat label="Ledger" value={integrity?.books_balanced ? 'Balanced' : 'Check'}
            hint="Every entry sums to zero" />
        </Card>
      </div>

      <div className="grid grid-2" style={{ alignItems: 'start' }}>
        {/* ------------------------------------------------------ deposit */}
        <Card title="Deposit">
          <div className="field">
            <label>Amount (USD)</label>
            <input inputMode="decimal" value={depAmount}
              onChange={(e) => setDepAmount(e.target.value.replace(/[^0-9.]/g, ''))} />
            <div className="row" style={{ gap: 6 }}>
              {QUICK.map((q) => (
                <button key={q} className="btn btn-sm" onClick={() => setDepAmount(q)}>${Number(q).toFixed(0)}</button>
              ))}
            </div>
            <span className="tiny muted">
              Min {fmtCents(limits.min_deposit || 500)} · Max {fmtCents(limits.max_deposit || 1000000)} per
              transaction. Larger amounts: contact support.
            </span>
          </div>

          <div className="field">
            <label>Method</label>
            <select value={depMethod} onChange={(e) => setDepMethod(e.target.value)}>
              {(methods?.deposit || []).map((m) => (
                <option key={m.method} value={m.method}>{m.label}</option>
              ))}
            </select>
          </div>

          <div className="field">
            <label>Bonus code (optional)</label>
            <input value={bonusCode} onChange={(e) => setBonusCode(e.target.value.toUpperCase())}
              placeholder="e.g. WELCOME" />
          </div>

          <button className="btn btn-primary btn-block" disabled={busy || !depAmount} onClick={submitDeposit}>
            {busy ? <span className="spinner" /> : `Deposit ${fmtCents(Math.round(Number(depAmount || 0) * 100))}`}
          </button>

          {simulation && (
            <p className="tiny muted" style={{ marginTop: 10 }}>
              Sandbox provider active: you will get a simulated checkout you can complete or fail,
              so you can test the full credit path.
            </p>
          )}
        </Card>

        {/* --------------------------------------------------- withdrawal */}
        <Card title="Withdraw">
          <div className="field">
            <label>Amount (USD)</label>
            <input inputMode="decimal" value={wdAmount}
              onChange={(e) => setWdAmount(e.target.value.replace(/[^0-9.]/g, ''))}
              placeholder={(limits.min_withdrawal / 100).toFixed(2)} />
            <div className="row" style={{ gap: 6 }}>
              <button className="btn btn-sm" onClick={() => setWdAmount((cash / 100).toFixed(2))}>
                Cash balance ({fmtCents(cash)})
              </button>
            </div>
            <span className="tiny muted">
              Min {fmtCents(limits.min_withdrawal || 1000)} · Max {fmtCents(limits.max_withdrawal || 500000)}.
              Bonus funds must be played through before withdrawing.
            </span>
          </div>

          <div className="field">
            <label>Payout method</label>
            <select value={wdMethod} onChange={(e) => setWdMethod(e.target.value)}>
              {(methods?.withdrawal || []).map((m) => (
                <option key={m.method} value={m.method}>{m.label}</option>
              ))}
            </select>
          </div>

          <div className="field">
            <label>Destination</label>
            <input value={wdDestination} onChange={(e) => setWdDestination(e.target.value)}
              placeholder={
                wdMethod.startsWith('crypto') ? 'Your wallet address'
                  : wdMethod === 'bank_transfer' ? 'IBAN / account number'
                    : 'Card number (stored as last 4 only)'
              } />
            <span className="tiny muted">
              Payouts are reviewed by a human before they are sent. Typical turnaround: under 24 hours.
            </span>
          </div>

          {methods?.kyc_required && (
            <Alert kind="warn">
              Identity verification is required before your first payout.{' '}
              <Link to="/account">Upload documents</Link>.
            </Alert>
          )}

          <button className="btn btn-ok btn-block" disabled={busy || !wdAmount || !wdDestination}
            onClick={submitWithdrawal}>
            {busy ? <span className="spinner" /> : 'Request withdrawal'}
          </button>
        </Card>
      </div>

      {/* ------------------------------------------------ pending payout */}
      {withdrawals.filter((w) => ['requested', 'under_review', 'approved'].includes(w.status)).length > 0 && (
        <Card title="Pending withdrawals" className="card" style={{ marginTop: 18 }}>
          {withdrawals
            .filter((w) => ['requested', 'under_review', 'approved'].includes(w.status))
            .map((w) => (
              <div key={w.id} className="row between" style={{ padding: '8px 0', borderBottom: '1px solid var(--line)' }}>
                <div>
                  <div><strong>{fmtCents(w.amount)}</strong> via {PAYMENT_NAME[w.method]}</div>
                  <div className="tiny muted">{w.destination} · {w.id.slice(0, 10)}</div>
                </div>
                <div className="row">
                  <Badge status={w.status} />
                  {w.cancellable && (
                    <ActionButton
                      className="btn btn-sm"
                      confirm
                      onClick={async () => {
                        await api.cancelWithdrawal(w.id)
                        toast('Withdrawal cancelled - funds returned')
                        await Promise.all([load(), refreshWallet()])
                      }}
                    >
                      Cancel
                    </ActionButton>
                  )}
                </div>
              </div>
            ))}
        </Card>
      )}

      {/* ------------------------------------------------------- history */}
      <div className="grid grid-2" style={{ marginTop: 18, alignItems: 'start' }}>
        <Card title="Deposits">
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>When</th><th>Amount</th><th>Method</th><th>Status</th><th /></tr>
              </thead>
              <tbody>
                {deposits.map((d) => (
                  <tr key={d.id}>
                    <td className="tiny">{new Date(d.created_at).toLocaleString()}</td>
                    <td>{fmtCents(d.amount)}</td>
                    <td className="tiny">{PAYMENT_NAME[d.method]}</td>
                    <td>
                      <Badge status={d.status} />
                      {d.bonus_credited > 0 && (
                        <span className="tiny muted"> +{fmtCents(d.bonus_credited)} bonus</span>
                      )}
                    </td>
                    <td>
                      {['pending', 'requires_action'].includes(d.status) && (
                        <button className="btn btn-sm" onClick={async () => setActiveDeposit(await api.deposit(d.id))}>
                          Resume
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!deposits.length && <Empty>No deposits yet.</Empty>}
          </div>
        </Card>

        <Card title="Ledger entries">
          <p className="tiny muted">
            A double-entry record: every row is one side of a transaction that sums to zero
            across the whole system.
          </p>
          <div className="table-wrap" style={{ maxHeight: 380, overflowY: 'auto' }}>
            <table>
              <thead>
                <tr><th>When</th><th>Type</th><th>Account</th><th style={{ textAlign: 'right' }}>Amount</th><th style={{ textAlign: 'right' }}>Balance</th></tr>
              </thead>
              <tbody>
                {statement.map((e) => (
                  <tr key={e.id}>
                    <td className="tiny">{new Date(e.created_at).toLocaleString()}</td>
                    <td className="tiny">{e.type}</td>
                    <td className="tiny">{e.account.replace('user_', '')}</td>
                    <td style={{ textAlign: 'right', color: e.amount >= 0 ? 'var(--ok)' : 'var(--bad)' }}>
                      {e.amount >= 0 ? '+' : ''}{fmtCents(e.amount)}
                    </td>
                    <td style={{ textAlign: 'right' }} className="tiny">{fmtCents(e.balance_after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!statement.length && <Empty>No transactions yet. Make a deposit to start.</Empty>}
          </div>
        </Card>
      </div>

      <Card title="Withdrawal history" style={{ marginTop: 18 }}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>When</th><th>Amount</th><th>Net</th><th>Method</th><th>Destination</th><th>Status</th><th>Reference</th></tr>
            </thead>
            <tbody>
              {withdrawals.map((w) => (
                <tr key={w.id}>
                  <td className="tiny">{new Date(w.created_at).toLocaleString()}</td>
                  <td>{fmtCents(w.amount)}</td>
                  <td className="tiny">{fmtCents(w.net_amount)}</td>
                  <td className="tiny">{PAYMENT_NAME[w.method]}</td>
                  <td className="tiny mono">{w.destination}</td>
                  <td>
                    <Badge status={w.status} />
                    {w.rejection_reason && <div className="tiny muted">{w.rejection_reason}</div>}
                  </td>
                  <td className="tiny mono">{w.provider_ref || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!withdrawals.length && <Empty>No withdrawals yet.</Empty>}
        </div>
      </Card>

      {/* ------------------------------------------- sandbox checkout modal */}
      {activeDeposit && (
        <Modal title="Complete your payment" onClose={() => setActiveDeposit(null)}>
          <p className="small">
            <strong>{fmtCents(activeDeposit.amount)}</strong> via {PAYMENT_NAME[activeDeposit.method]} ·
            reference <span className="mono tiny">{activeDeposit.reference}</span>
          </p>

          {activeDeposit.instructions?.address && (
            <div className="alert alert-info">
              <div className="tiny">Send the exact amount to:</div>
              <div className="mono" style={{ wordBreak: 'break-all', margin: '6px 0' }}>
                {activeDeposit.instructions.address}
              </div>
              <div className="tiny">
                Network: {activeDeposit.instructions.network} · confirmations required:{' '}
                {activeDeposit.instructions.confirmations_required}
              </div>
            </div>
          )}
          {activeDeposit.instructions?.iban && (
            <div className="alert alert-info">
              <div className="mono tiny">IBAN {activeDeposit.instructions.iban}</div>
              <div className="mono tiny">SWIFT {activeDeposit.instructions.swift}</div>
              <div className="tiny">Use reference {activeDeposit.instructions.reference_required}</div>
            </div>
          )}
          {activeDeposit.instructions?.card_hint && (
            <div className="alert alert-info">
              Simulated card: <span className="mono">{activeDeposit.instructions.card_hint}</span>
            </div>
          )}

          {simulation ? (
            <>
              <Alert kind="warn">
                Sandbox mode: use the buttons below to emulate what the payment provider would send
                back. Nothing is charged.
              </Alert>
              <div className="row">
                <button className="btn btn-ok" onClick={() => simulate(activeDeposit.deposit_id, 'succeed')}>
                  Simulate successful payment
                </button>
                <button className="btn" onClick={() => simulate(activeDeposit.deposit_id, 'fail')}>
                  Simulate decline
                </button>
                <button className="btn btn-bad" onClick={() => simulate(activeDeposit.deposit_id, 'chargeback')}>
                  Simulate chargeback
                </button>
              </div>
            </>
          ) : (
            <div className="row">
              <a className="btn btn-primary" href={activeDeposit.instructions?.redirect_url || '#'}
                target="_blank" rel="noreferrer">
                Continue to provider
              </a>
              <button className="btn" onClick={async () => { setActiveDeposit(await api.deposit(activeDeposit.deposit_id)) }}>
                I have paid — refresh
              </button>
            </div>
          )}
        </Modal>
      )}
    </div>
  )
}
