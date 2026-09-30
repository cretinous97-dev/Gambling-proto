import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api.js'
import { fmtCents } from '../lib/format.js'
import { useTranslation } from 'react-i18next'
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
  const { t } = useTranslation()
  const { depositId } = useParams()
  const navigate = useNavigate()
  const { refreshWallet, toast, config } = useStore()
  const [deposit, setDeposit] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [poll, setPoll] = useState(0)

  const simulation = config?.provider?.simulation

  const load = useCallback(async () => {
    try {
      setDeposit(await api.deposit(depositId))
    } catch (err) {
      setError(err.message)
    }
  }, [depositId])

  useEffect(() => {
    load()
  }, [load])

  // Poll while the deposit is still open so a provider webhook shows up here
  // without the player needing to refresh.
  //
  // The dependency is this boolean, not `deposit`: the poll replaces the
  // deposit object every four seconds, so depending on the object would tear
  // down and rebuild the interval on each tick and the timer would drift
  // further behind the thing it is polling for.
  const awaitingPayment = Boolean(deposit) && ['pending', 'requires_action'].includes(deposit.status)
  useEffect(() => {
    if (!awaitingPayment) return undefined
    const timer = setInterval(() => {
      setPoll((p) => p + 1)
      load()
    }, 4000)
    return () => clearInterval(timer)
  }, [awaitingPayment, load])

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
  // A live PSP hosts its own payment page (Adyen's /sessions returns one, so
  // does Stripe Checkout). Without this redirect the deposit would sit at
  // requires_action forever and the player would never see a card form - the
  // exact "nothing happens when I press Deposit" failure.
  const redirectUrl = deposit.instructions?.redirect_url || null

  return (
    <div className="page narrow">
      <Card title={t('checkout.title')}>
        {error && <Alert kind="error">{error}</Alert>}

        <div className="row between">
          <div className="stat">
            <span className="label">{t('checkout.amount')}</span>
            <span className="value">{fmtCents(deposit.amount)}</span>
          </div>
          <div className="stat">
            <span className="label">{t('checkout.status')}</span>
            <span className={`badge ${done ? 'badge-ok' : 'badge-warn'}`}>{deposit.status}</span>
          </div>
          <div className="stat">
            <span className="label">{t('checkout.reference')}</span>
            <span className="tiny mono">{deposit.reference}</span>
          </div>
        </div>

        <hr style={{ border: 0, borderTop: '1px solid var(--line)', margin: '16px 0' }} />

        {redirectUrl && !done && (
          <div className="alert alert-info">
            <div className="small" style={{ marginBottom: 8 }}>{t('checkout.redirect_note')}</div>
            <a className="btn btn-primary" href={redirectUrl}>{t('checkout.continue_to_payment')}</a>
          </div>
        )}

        {deposit.instructions?.address && (
          <div className="alert alert-info">
            <div className="small">
              {t('checkout.send_exactly', { amount: fmtCents(deposit.amount) })}
            </div>
            <Copyable value={deposit.instructions.address} />
            <div className="tiny" style={{ marginTop: 6 }}>
              {deposit.instructions.network} ·{' '}
              {t('checkout.confirmations_required', { count: deposit.instructions.confirmations_required })}
            </div>
          </div>
        )}

        {deposit.instructions?.iban && (
          <div className="alert alert-info">
            <div className="small">{t('checkout.bank_transfer')}</div>
            <div className="mono tiny">IBAN {deposit.instructions.iban}</div>
            <div className="mono tiny">SWIFT {deposit.instructions.swift}</div>
            <div className="mono tiny">Beneficiary {deposit.instructions.beneficiary}</div>
            <div className="tiny">
              {t('checkout.include_reference', { reference: deposit.instructions.reference_required })}
            </div>
          </div>
        )}

        {done ? (
          <Alert kind="ok">
            {t('checkout.confirmed', { amount: fmtCents(deposit.credited) })}
            {deposit.bonus_credited > 0 && (
              <> {t('checkout.plus_bonus', { amount: fmtCents(deposit.bonus_credited) })}</>
            )}.
          </Alert>
        ) : deposit.status === 'failed' ? (
          <Alert kind="error">
            {t('checkout.failed', { reason: deposit.failure_reason || t('checkout.declined') })}
          </Alert>
        ) : deposit.status === 'chargeback' ? (
          <Alert kind="error">{t('checkout.chargeback')}</Alert>
        ) : simulation ? (
          <>
            <Alert kind="warn">{t('checkout.sandbox_note')}</Alert>
            <div className="row">
              <button className="btn btn-ok" disabled={busy} onClick={() => act('succeed')}>
                {t('checkout.paid_confirm')}
              </button>
              <button className="btn" disabled={busy} onClick={() => act('fail')}>
                {t('checkout.declined_action')}
              </button>
            </div>
          </>
        ) : (
          <p className="small muted">
            {t('checkout.waiting', { poll: poll ? t('checkout.poll_suffix', { count: poll }) : '' })}
          </p>
        )}

        <div className="row" style={{ marginTop: 18 }}>
          <Link className="btn btn-sm" to="/wallet">{t('checkout.back_to_wallet')}</Link>
          <Link className="btn btn-sm btn-ghost" to="/">{t('checkout.lobby')}</Link>
        </div>
      </Card>
    </div>
  )
}
