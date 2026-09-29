import { useState } from 'react'
import { Alert, Card } from '../../components/ui.jsx'
import { api } from '../../lib/api.js'
import { useBetSubmit } from './useBet.js'
import { fmtCents } from '../../lib/format.js'
import { ProvablyFairNote } from '../GameRoom.jsx'

/**
 * Mines. The board lives on the server (stored on the bet row), so a refresh,
 * a second tab, or a browser crash can never reveal the mine layout or lose
 * the player's stake - the hand is resumed from the server's state.
 */
export default function Mines({ minBet, maxBet, onSettled }) {
  const [stake, setStake] = useState('1.00')
  const [mineCount, setMineCount] = useState(3)
  const [bet, setBet] = useState(null)
  const { busy, error, run, setError } = useBetSubmit(onSettled)

  const board = bet?.result?.board || []
  const revealed = bet?.result?.revealed || []
  const exploded = bet?.result?.hit_mine

  const start = async () => {
    const result = await run(() =>
      api.minesStart({
        stake,
        mines: Number(mineCount),
        idempotency_key: `mines-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      }),
    )
    if (result) setBet(result)
  }

  const open = async (index) => {
    if (!bet || bet.settled || revealed.includes(index)) return
    const result = await run(() => api.minesOpen(bet.id, index))
    if (result) setBet(result)
  }

  const cashout = async () => {
    const result = await run(() => api.minesCashout(bet.id))
    if (result) setBet(result)
  }

  const inPlay = bet && !bet.settled
  const currentMultiplier = bet?.result?.current_multiplier || 0
  const nextMultiplier = bet?.result?.next_multiplier || 0

  const tileContent = (i) => {
    if (revealed.includes(i)) return '💎'
    if (bet?.settled && board.includes(i)) return '💣'
    return ''
  }

  const tileClass = (i) => {
    if (revealed.includes(i)) return 'mine-tile safe'
    if (bet?.settled && (board.includes(i) || i === bet.result?.exploded_at)) return 'mine-tile boom'
    return 'mine-tile'
  }

  return (
    <>
      {error && <Alert kind="error">{error}</Alert>}

      {!inPlay ? (
        <Card>
          <div className="row" style={{ gap: 14, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div className="field" style={{ flex: 1, minWidth: 160 }}>
              <label>Bet amount</label>
              <input inputMode="decimal" value={stake}
                onChange={(e) => setStake(e.target.value.replace(/[^0-9.]/g, ''))} />
              <span className="tiny muted">Min {(minBet / 100).toFixed(2)} · Max {(maxBet / 100).toFixed(2)}</span>
            </div>
            <div className="field" style={{ flex: 1, minWidth: 160 }}>
              <label>Mines: <strong>{mineCount}</strong></label>
              <input type="range" min="1" max="24" value={mineCount}
                onChange={(e) => setMineCount(Number(e.target.value))} style={{ padding: 0 }} />
            </div>
            <button className="btn btn-primary" onClick={start} disabled={busy || !stake}>
              {busy ? <span className="spinner" /> : 'Start game'}
            </button>
          </div>
          <p className="tiny muted" style={{ marginTop: 10 }}>
            Reveal tiles to raise your multiplier. One mine ends the round and takes the stake.
            Cash out any time. First pick pays {fmtCents(0)} — the multiplier applies from the first
            safe tile.
          </p>
        </Card>
      ) : (
        <Card>
          <div className="row between" style={{ marginBottom: 12 }}>
            <div className="stat">
              <span className="label">Current multiplier</span>
              <span className="value" style={{ color: 'var(--ok)' }}>{currentMultiplier.toFixed(4)}x</span>
            </div>
            <div className="stat">
              <span className="label">Cash-out value</span>
              <span className="value">{fmtCents(bet.result.potential_payout || 0)}</span>
            </div>
            <div className="stat">
              <span className="label">Next tile</span>
              <span className="value">{nextMultiplier.toFixed(4)}x</span>
            </div>
            <button className="btn btn-ok" onClick={cashout} disabled={busy || !revealed.length}>
              {busy ? <span className="spinner" /> : `Cash out ${fmtCents(bet.result.potential_payout || 0)}`}
            </button>
          </div>

          <div className="mines-grid" style={{ margin: '0 auto' }}>
            {Array.from({ length: 25 }, (_, i) => (
              <button key={i} className={tileClass(i)} disabled={busy} onClick={() => open(i)}>
                {tileContent(i)}
              </button>
            ))}
          </div>

          <p className="tiny muted" style={{ marginTop: 12, textAlign: 'center' }}>
            {mineCount} mines · {revealed.length} safe tile{revealed.length === 1 ? '' : 's'} revealed
          </p>
        </Card>
      )}

      {bet?.settled && (
        <div className={`alert ${bet.payout > 0 ? 'alert-ok' : 'alert-error'} result-flash`} style={{ marginTop: 14 }}>
          <div className="row between">
            <span>
              {exploded ? '💥 Hit a mine — stake lost' : bet.result?.auto_cashout ? 'Board cleared — auto cashed out' : 'Cashed out'}
            </span>
            <strong>{fmtCents(bet.payout)}</strong>
          </div>
          {bet.profit !== 0 && (
            <div className="tiny">Net {bet.profit > 0 ? '+' : ''}{fmtCents(bet.profit)}</div>
          )}
        </div>
      )}

      {bet?.settled && (
        <button className="btn btn-primary btn-block" style={{ marginTop: 12 }} onClick={() => setBet(null)}>
          Play another round
        </button>
      )}

      <ProvablyFairNote bet={bet} />
    </>
  )
}
