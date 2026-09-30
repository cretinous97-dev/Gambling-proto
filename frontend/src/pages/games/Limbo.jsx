import { useState } from 'react'
import { Alert, Card } from '../../components/ui.jsx'
import { useBetSubmit } from './useBet.js'
import StakeRow from './StakeRow.jsx'
import { ResultBanner, ProvablyFairNote } from '../GameRoom.jsx'
import { fmtCents } from '../../lib/format.js'

export default function Limbo({ minBet, maxBet, onSettled }) {
  const [stake, setStake] = useState('1.00')
  const [target, setTarget] = useState(2)
  const [bet, setBet] = useState(null)
  const { busy, error, play } = useBetSubmit(onSettled)

  const chance = (0.99 / target) * 100
  const submit = async () => {
    const result = await play('limbo', stake, { target: Number(target) })
    if (result) setBet(result)
  }

  return (
    <>
      {error && <Alert kind="error">{error}</Alert>}

      <Card>
        <div style={{ textAlign: 'center', padding: '26px 0' }}>
          <div className="label muted tiny">Result</div>
          <div style={{ fontSize: 46, fontWeight: 900, color: bet?.result?.win ? 'var(--ok)' : 'var(--text)' }}>
            {bet?.result?.result !== undefined ? `${bet.result.result.toFixed(2)}x` : '—'}
          </div>
          {bet?.result?.target !== undefined && (
            <div className="small muted">target {bet.result.target.toFixed(2)}x</div>
          )}
        </div>

        <div className="field">
          <label>Target multiplier: <strong>{Number(target).toFixed(2)}x</strong></label>
          <input type="range" min="1.01" max="100" step="0.01" value={target}
            onChange={(e) => setTarget(Number(e.target.value))} style={{ padding: 0 }} />
        </div>

        <div className="grid grid-3">
          <div className="stat"><span className="label">Win chance</span><span className="value">{chance.toFixed(2)}%</span></div>
          <div className="stat"><span className="label">Payout on win</span><span className="value">{fmtCents(Math.floor(Number(stake || 0) * 100 * target))}</span></div>
          <div className="stat"><span className="label">Edge</span><span className="value">1.00%</span></div>
        </div>
      </Card>

      <div style={{ marginTop: 14 }}>
        <StakeRow stake={stake} setStake={setStake} min={minBet} max={maxBet}
          onPlay={submit} busy={busy} label="Play" children={null} />
      </div>

      <ResultBanner bet={bet} />
      <ProvablyFairNote bet={bet} />
    </>
  )
}
