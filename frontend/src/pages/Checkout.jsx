import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api.js'
import { fmtCents } from '../lib/format.js'
import { Alert, Card, Copyable, Loading } from '../components/ui.jsx'
import { useStore } from '../lib/store.jsx'

/**
 * Standalone checkout page for a deposit intent.
 *
 * With the sandbox provider this is where you emulate the customer paying.
 * With a live provider the same page shows the crypto address / bank details
 * and polls for the provider's webhook to land.
 */
export default function Checkout() {
  const { depositId } = useParams()
  const navigate = useNavigate()
  const { refreshWallet, toast, config } = useStore()
  const [deposit, setDeposit] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [poll, setPoll] = useState(0)

  const simulation = config?.provider?.simulation

  const load = async () => {
    try {
      setDeposit(await api.deposit(depositId))
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    load()
  }, [depositId])

  // Poll while the deposit is still open so a provider webhook shows up here
  // without the player needing to refresh.
  useEffect(() => {
    if (!deposit || !['pending', 'requires_action'].includes(deposit.status)) return undefined
    const timer = setInterval(() => {
      setPoll((p) => p + 1)
      load()
    }, 4000)
    return () => clearInterval(timer)
  }, [deposit?.status])

  const act = async (outcome) => {
    setBusy(true)
    setError('')
    try {
      const updated = await api.simulateDeposit(depositId, outcome)
      setDeposit(updated)
      await refreshWallet()
      if (updated.status === 'succeeded') {
        toast(`${fmtCents(updated.credited)} credited to your balance`, 'success')
        setTimeout(() => navigate('/wallet'), 900)
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (!deposit) return <div className="page narrow"><Loading /></div>

  const done = deposit.status === 'succeeded'

  return (
    <div className="page narrow">
      <Card title="Deposit checkout">
        {error && <Alert kind="error">{error}</Alert>}

        <div className="row between">
          <div className="stat">
            <span className="label">Amount</span>
            <span className="value">{fmtCents(deposit.amount)}</span>
          </div>
          <div className="stat">
            <span className="label">Status</span>
            <span className={`badge ${done ? 'badge-ok' : 'badge-warn'}`}>{deposit.status}</span>
          </div>
          <div className="stat">
            <span className="label">Reference</span>
            <span className="tiny mono">{deposit.reference}</span>
          </div>
        </div>

        <hr style={{ border: 0, borderTop: '1px solid var(--line)', margin: '16px 0' }} />

        {deposit.instructions?.address && (
          <div className="alert alert-info">
            <div className="small">Send exactly {fmtCents(deposit.amount)} to this address:</div>
            <Copyable value={deposit.instructions.address} />
            <div className="tiny" style={{ marginTop: 6 }}>
              {deposit.instructions.network} · {deposit.instructions.confirmations_required} confirmations
              required before credit
            </div>
          </div>
        )}

        {deposit.instructions?.iban && (
          <div className="alert alert-info">
            <div className="small">Bank transfer details</div>
            <div className="mono tiny">IBAN {deposit.instructions.iban}</div>
            <div className="mono tiny">SWIFT {deposit.instructions.swift}</div>
            <div className="mono tiny">Beneficiary {deposit.instructions.beneficiary}</div>
            <div className="tiny">Include reference {deposit.instructions.reference_required}</div>
          </div>
        )}

        {done ? (
          <Alert kind="ok">
            Payment confirmed. {fmtCents(deposit.credited)} is in your balance
            {deposit.bonus_credited > 0 && <> plus {fmtCents(deposit.bonus_credited)} bonus</>}.
          </Alert>
        ) : deposit.status === 'failed' ? (
          <Alert kind="error">
            Payment failed: {deposit.failure_reason || 'declined'}. Nothing was charged.
          </Alert>
        ) : deposit.status === 'chargeback' ? (
          <Alert kind="error">This payment was reversed by the bank.</Alert>
        ) : simulation ? (
          <>
            <Alert kind="warn">
              Sandbox provider — these buttons stand in for the customer completing the payment on
              the provider's page.
            </Alert>
            <div className="row">
              <button className="btn btn-ok" disabled={busy} onClick={() => act('succeed')}>
                I paid — confirm
              </button>
              <button className="btn" disabled={busy} onClick={() => act('fail')}>
                Payment declined
              </button>
            </div>
          </>
        ) : (
          <p className="small muted">
            Waiting for the provider to confirm (checked automatically{poll ? ` · check #${poll}` : ''}).
            You can safely close this page — the deposit will be credited by webhook.
          </p>
        )}

        <div className="row" style={{ marginTop: 18 }}>
          <Link className="btn btn-sm" to="/wallet">Back to wallet</Link>
          <Link className="btn btn-sm btn-ghost" to="/">Lobby</Link>
        </div>
      </Card>
    </div>
  )
}
