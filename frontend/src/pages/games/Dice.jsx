import { useMemo, useState } from 'react'
import { Alert, Card } from '../../components/ui.jsx'
import { useBetSubmit } from './useBet.js'
import StakeRow from './StakeRow.jsx'
import { ResultBanner, ProvablyFairNote } from '../GameRoom.jsx'

/**
 * Dice. The player sets a target and direction; the multiplier is derived
 * client-side ONLY for display - the server computes the real payout from the
 * same formula, and the server's answer always wins.
 */
export default function Dice({ minBet, maxBet, onSettled }) {
  const [stake, setStake] = useState('1.00')
  const [target, setTarget] = useState(50)
  const [direction, setDirection] = useState('over')
  const [bet, setBet] = useState(null)
  const { busy, error, play } = useBetSubmit(onSettled)

  const winChance = direction === 'over' ? 100 - target : target
  const multiplier = winChance > 0 ? (0.99 / (winChance / 100)) : 0
  const potential = useMemo(() => Math.floor(Number(stake || 0) * 100 * multiplier), [stake, multiplier])

  const submit = async () => {
    const result = await play('dice', stake, { target: Number(target), direction })
    if (result) setBet(result)
  }

  const flip = () => {
    setDirection((d) => (d === 'over' ? 'under' : 'over'))
    setTarget((t) => (t === 50 ? 50 : 100 - t))
  }

  return (
    <>
      {error && <Alert kind="error">{error}</Alert>}

      <Card>
        <div className="dice-bar" style={{ marginBottom: 14 }}>
          <div
            className="dice-fill"
            style={
              direction === 'over'
                ? { left: `${target}%`, right: 0 }
                : { left: 0, width: `${target}%` }
            }
          />
          <div className="dice-marker" style={{ left: `${target}%` }} />
          <div className="dice-number">
            {bet?.result?.roll !== undefined ? bet.result.roll.toFixed(2) : '—'}
          </div>
        </div>

        <div className="row between small muted">
          <span>0.00</span>
          <span>Roll · win {direction} {target.toFixed(2)}</span>
          <span>99.99</span>
        </div>

        <div className="row" style={{ marginTop: 16, gap: 16, alignItems: 'flex-end' }}>
          <div className="field" style={{ flex: 1, minWidth: 200 }}>
            <label>
              Target: <strong>{Number(target).toFixed(2)}</strong>
            </label>
            <input
              type="range" min="1" max="99" step="0.01" value={target}
              onChange={(e) => setTarget(Number(e.target.value))} style={{ padding: 0 }}
            />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>Direction</label>
            <div className="row">
              <button className={`btn btn-sm ${direction === 'over' ? 'btn-primary' : ''}`}
                onClick={() => setDirection('over')}>Over</button>
              <button className={`btn btn-sm ${direction === 'under' ? 'btn-primary' : ''}`}
                onClick={() => setDirection('under')}>Under</button>
              <button className="btn btn-sm btn-ghost" onClick={flip}>Flip</button>
            </div>
          </div>
        </div>

        <div className="grid grid-3" style={{ marginTop: 16 }}>
          <div className="stat"><span className="label">Win chance</span><span className="value">{winChance.toFixed(2)}%</span></div>
          <div className="stat"><span className="label">Multiplier</span><span className="value">{multiplier.toFixed(4)}x</span></div>
          <div className="stat"><span className="label">Profit on win</span><span className="value">${(potential / 100).toFixed(2)}</span></div>
        </div>
      </Card>

      <div style={{ marginTop: 14 }}>
        <StakeRow
          stake={stake} setStake={setStake} min={minBet} max={maxBet}
          onPlay={submit} busy={busy} label="Roll dice" children={null}
        />
      </div>

      <ResultBanner bet={bet} />
      <ProvablyFairNote bet={bet} />
    </>
  )
}
