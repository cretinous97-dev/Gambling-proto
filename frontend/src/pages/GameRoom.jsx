import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api.js'
import { fmtCents, fmtMultiplier, GAME_ICONS } from '../lib/format.js'
import { Alert, Card, Loading, StakeInput } from '../components/ui.jsx'
import { useStore } from '../lib/store.jsx'

import Mines from './games/Mines.jsx'
import Blackjack from './games/Blackjack.jsx'
import Dice from './games/Dice.jsx'
import Limbo from './games/Limbo.jsx'
import Plinko from './games/Plinko.jsx'
import Wheel from './games/Wheel.jsx'
import Keno from './games/Keno.jsx'
import CoinFlip from './games/CoinFlip.jsx'
import Roulette from './games/Roulette.jsx'
import Slots from './games/Slots.jsx'

const PANELS = {
  mines: Mines,
  blackjack: Blackjack,
  dice: Dice,
  limbo: Limbo,
  plinko: Plinko,
  wheel: Wheel,
  keno: Keno,
  coinflip: CoinFlip,
  roulette: Roulette,
  slots: Slots,
}

export default function GameRoom() {
  const { t } = useTranslation()
  const { slug } = useParams()
  const { config, isAuthed, refreshWallet } = useStore()
  const [meta, setMeta] = useState(null)
  const [rules, setRules] = useState(null)
  const [error, setError] = useState('')
  const [showRules, setShowRules] = useState(false)

  useEffect(() => {
    setMeta(null)
    setError('')
    ;(async () => {
      try {
        const cat = await api.catalog()
        const all = [...(cat.games || []), ...(cat.live || [])]
        const found = all.find((g) => g.slug === slug)
        if (!found) throw new Error(`Unknown game "${slug}"`)
        setMeta(found)
        setRules(await api.rules(slug))
      } catch (err) {
        setError(err.message)
      }
    })()
  }, [slug])

  if (!PANELS[slug]) {
    return (
      <div className="page">
        <Alert kind="error">Unknown game “{slug}”. <Link to="/">Back to the lobby</Link>.</Alert>
      </div>
    )
  }

  if (error) return <div className="page"><Alert kind="error">{error}</Alert></div>
  if (!meta) return <div className="page"><Loading /></div>

  const Panel = PANELS[slug]
  const minBet = config?.limits?.min_bet ?? 10
  const maxBet = config?.limits?.max_bet ?? 200000

  return (
    <div className="page medium">
      <div className="row between" style={{ marginBottom: 12 }}>
        <div className="row">
          <span style={{ fontSize: 26 }}>{GAME_ICONS[slug]}</span>
          <div>
            <h1 style={{ margin: 0, fontSize: 22 }}>{meta.name}</h1>
            <span className="tiny muted">
              RTP {(meta.rtp * 100).toFixed(1)}% · house edge {meta.edge} · provably fair
            </span>
          </div>
        </div>
        <button className="btn btn-sm" onClick={() => setShowRules((s) => !s)}>
          {showRules ? 'Hide rules' : 'How this works'}
        </button>
      </div>

      {showRules && rules && (
        <Card className="card" style={{ marginBottom: 14 }}>
          <div className="small">
            <p style={{ marginTop: 0 }}>{rules.how_to_play}</p>
            <p><strong>Payout:</strong> {rules.payout}</p>
            {rules.cashout_rules && <p><strong>Cash-out:</strong> {rules.cashout_rules}</p>}
            {rules.rounding && <p><strong>Rounding:</strong> {rules.rounding}</p>}
            {rules.note && <p><strong>Note:</strong> {rules.note}</p>}
            <p className="muted" style={{ marginBottom: 0 }}>
              House edge {rules.house_edge}. All payouts are floored to the cent. Results are
              generated from HMAC-SHA256(server_seed, client_seed:nonce) — you can verify any
              hand on the Account page after rotating your seed.
            </p>
          </div>
        </Card>
      )}

      {!isAuthed ? (
        <Card>
          <p className="muted">Log in or create an account to play {meta.name}.</p>
          <div className="row">
            <Link className="btn btn-primary" to="/register">{t('auth.signup')}</Link>
            <Link className="btn" to="/login">{t('auth.signin')}</Link>
          </div>
        </Card>
      ) : (
        <Panel minBet={minBet} maxBet={maxBet} onSettled={refreshWallet} />
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ shared */
export function ResultBanner({ bet }) {
  if (!bet) return null
  const won = (bet.payout || 0) > 0
  return (
    <div className={`alert ${won ? 'alert-ok' : 'alert-error'} result-flash`}>
      <div className="row between">
        <span>
          {won ? 'Won' : 'Lost'} · {fmtMultiplier(bet.multiplier)}
        </span>
        <strong>{fmtCents(bet.payout)}</strong>
      </div>
      {bet.result?.roll !== undefined && (
        <div className="tiny">
          Roll {bet.result.roll} vs target {bet.result.target} ({bet.result.direction})
        </div>
      )}
      {bet.result?.bucket !== undefined && (
        <div className="tiny">Bucket {bet.result.bucket} · {fmtMultiplier(bet.result.multiplier)}</div>
      )}
      {bet.result?.hits !== undefined && (
        <div className="tiny">{bet.result.hits} hits — {fmtCents(bet.payout)}</div>
      )}
    </div>
  )
}

export function ProvablyFairNote({ bet }) {
  if (!bet?.fair) return null
  return (
    <p className="tiny muted" style={{ marginTop: 10 }}>
      Fairness: seed hash <span className="mono">{String(bet.fair.server_seed_hash || '').slice(0, 16)}…</span> · nonce{' '}
      {bet.fair.nonce} · client seed <span className="mono">{bet.fair.client_seed}</span>. Rotate your seed
      to reveal the server seed and recompute this result.
    </p>
  )
}

export { useBetSubmit } from './games/useBet.js'
export { default as StakeRow } from './games/StakeRow.jsx'
export { StakeInput }
