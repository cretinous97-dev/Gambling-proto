import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Alert, Card } from '../../components/ui.jsx'
import { useBetSubmit } from './useBet.js'
import StakeRow from './StakeRow.jsx'
import { ResultBanner, ProvablyFairNote } from '../GameRoom.jsx'
import { fmtCents } from '../../lib/format.js'

export default function CoinFlip({ minBet, maxBet, onSettled }) {
  const { t } = useTranslation()
  const [stake, setStake] = useState('1.00')
  const [side, setSide] = useState('heads')
  const [bet, setBet] = useState(null)
  const { busy, error, play } = useBetSubmit(onSettled)

  const submit = async () => {
    const result = await play('coinflip', stake, { side })
    if (result) setBet(result)
  }

  return (
    <>
      {error && <Alert kind="error">{error}</Alert>}

      <Card>
        <div style={{ textAlign: 'center', padding: '18px 0' }}>
          <div style={{ fontSize: 64 }}>{bet?.result?.win ? '🪙' : bet ? '💤' : '🪙'}</div>
          <div className="small muted">
            {bet ? (
              <>Landed on <strong>{bet.result.result}</strong> — you called {bet.result.side}</>
            ) : (
              'Call the flip. Correct calls pay 1.98x.'
            )}
          </div>
        </div>

        <div className="row" style={{ justifyContent: 'center' }}>
          {['heads', 'tails'].map((s) => (
            <button key={s} className={`btn ${side === s ? 'btn-primary' : ''}`} onClick={() => setSide(s)}>
              {s === 'heads' ? '👑 Heads' : '🦅 Tails'}
            </button>
          ))}
        </div>

        <div className="grid grid-3" style={{ marginTop: 16 }}>
          <div className="stat"><span className="label">{t('games.win_chance')}</span><span className="value">50.00%</span></div>
          <div className="stat"><span className="label">{t('bets.multiplier')}</span><span className="value">1.98x</span></div>
          <div className="stat"><span className="label">{t('games.payout_on_win')}</span><span className="value">{fmtCents(Math.floor(Number(stake || 0) * 100 * 1.98))}</span></div>
        </div>
      </Card>

      <div style={{ marginTop: 14 }}>
        <StakeRow stake={stake} setStake={setStake} min={minBet} max={maxBet}
          onPlay={submit} busy={busy} label="Flip" children={null} />
      </div>

      <ResultBanner bet={bet} />
      <ProvablyFairNote bet={bet} />
    </>
  )
}
