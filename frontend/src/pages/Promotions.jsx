import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api.js'
import { fmtCents } from '../lib/format.js'
import { Alert, Card, Empty, Loading } from '../components/ui.jsx'
import { Link } from 'react-router-dom'

export default function Promotions() {
  const { t } = useTranslation()
  const [data, setData] = useState(null)
  useEffect(() => { api.promotions().then(setData) }, [])
  if (!data) return <div className="page"><Loading /></div>

  const b = data.built_in
  return (
    <div className="page medium">
      <h1 style={{ marginTop: 0 }}>{t('nav.promotions')}</h1>
      <Alert kind="warn">
        Every bonus carries a playthrough requirement, is non-withdrawable until it is met, and
        expires after 30 days. Game contributions differ — slots count 100%, blackjack only 10%.
      </Alert>

      <div className="grid grid-2">
        {b.first_deposit_pct > 0 && (
          <Card>
            <h3 className="card-title">🎁 {b.first_deposit_pct}% first deposit bonus</h3>
            <p className="small muted">
              Up to {fmtCents(b.first_deposit_cap)} credited to your bonus balance on your first
              deposit, with a {b.wager_x}x playthrough requirement.
            </p>
            <Link className="btn btn-primary" to="/wallet">Claim on deposit</Link>
          </Card>
        )}

        {b.signup_bonus > 0 && (
          <Card>
            <h3 className="card-title">👋 Welcome bonus</h3>
            <p className="small muted">
              {fmtCents(b.signup_bonus)} credited automatically when you create an account.
            </p>
            <Link className="btn" to="/register">{t('auth.signup')}</Link>
          </Card>
        )}

        {(data.promotions || []).map((p) => (
          <Card key={p.code}>
            <h3 className="card-title">🏷️ Code {p.code}</h3>
            <p className="small muted">
              {p.type === 'percent' ? `${p.value}% deposit match` : `${fmtCents(p.value)} fixed bonus`}
              {p.max_amount ? ` (capped at ${fmtCents(p.max_amount)})` : ''} ·{' '}
              {p.wager_multiplier}x playthrough · minimum deposit {fmtCents(p.min_deposit)}
            </p>
            <p className="tiny muted">{p.uses_left} uses remaining. Enter the code on the deposit form.</p>
          </Card>
        ))}

        {(data.promotions || []).length === 0 && b.first_deposit_pct <= 0 && b.signup_bonus <= 0 && (
          <Card><Empty>{t('promotions.none_running')}</Empty></Card>
        )}
      </div>

      <Card title="How playthrough works" style={{ marginTop: 16 }}>
        <p className="small">
          Example: a {fmtCents(1000)} bonus at 30x requires {fmtCents(30000)} of wagering before any of
          that bonus becomes withdrawable cash. Each settled bet contributes its stake, weighted by the
          game: slots 100%, dice/limbo/plinko/mines/crash 50%, coinflip 25%, roulette 20%, blackjack 10%.
        </p>
        <p className="small muted">
          While a requirement is open, your bets draw from the bonus balance first. Once playthrough is
          complete, whatever is left in the bonus balance converts to cash automatically — the more you
          lose before completing, the less converts, so there is no way to "keep" the bonus by stalling.
        </p>
      </Card>
    </div>
  )
}
