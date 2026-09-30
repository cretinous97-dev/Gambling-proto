import { useState } from 'react'
import { Alert, Card } from '../../components/ui.jsx'
import { useBetSubmit } from './useBet.js'
import StakeRow from './StakeRow.jsx'
import { ResultBanner, ProvablyFairNote } from '../GameRoom.jsx'
import { fmtCents } from '../../lib/format.js'

const GLYPH = {
  J: '🃏', Q: '👸', K: '🤴', A: '🅰️', cherry: '🍒', bell: '🔔',
  seven: '7️⃣', diamond: '💎', wild: '🌟', scatter: '⭐',
}

/**
 * 5-reel, 20-line video slot. The reel window rendered here is the one the
 * SERVER returned - the client never animates a result it invented, so what
 * the player sees is exactly what was settled.
 */
export default function Slots({ minBet, maxBet, onSettled }) {
  const [stake, setStake] = useState('1.00')
  const [bet, setBet] = useState(null)
  const { busy, error, play } = useBetSubmit(onSettled)

  const grid = bet?.result?.grid
  const winningCells = new Set()
  ;(bet?.result?.lines || []).forEach((line) => {
    const pattern = line.pattern || []
    for (let col = 0; col < line.count; col += 1) {
      winningCells.add(`${pattern[col]}-${col}`)
    }
  })

  const submit = async () => {
    const result = await play('slots', stake, {})
    if (result) setBet(result)
  }

  const lineBet = Math.floor((Number(stake || 0) * 100) / 20)

  return (
    <>
      {error && <Alert kind="error">{error}</Alert>}

      <Card>
        <div style={{ background: '#0b1020', borderRadius: 14, padding: 12, border: '1px solid var(--line)' }}>
          {grid ? (
            <div className="slot-reels">
              {grid.map((row, r) => (
                <div key={r} style={{ display: 'grid', gap: 8 }}>
                  {row.map((sym, c) => (
                    <div key={c} className={`slot-cell ${winningCells.has(`${r}-${c}`) ? 'win' : ''}`}>
                      {GLYPH[sym] || sym}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ) : (
            <div className="slot-reels">
              {Array.from({ length: 5 }, (_, r) => (
                <div key={r} style={{ display: 'grid', gap: 8 }}>
                  {Array.from({ length: 3 }, (_, c) => (
                    <div key={c} className="slot-cell muted">—</div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="grid grid-4" style={{ marginTop: 14 }}>
          <div className="stat"><span className="label">Line bet</span><span className="value">{fmtCents(lineBet)}</span></div>
          <div className="stat"><span className="label">Lines</span><span className="value">20</span></div>
          <div className="stat"><span className="label">Winning lines</span><span className="value">{bet?.result?.lines?.length ?? '—'}</span></div>
          <div className="stat"><span className="label">Scatters</span><span className="value">{bet?.result?.scatters ?? '—'}</span></div>
        </div>

        {bet?.result?.lines?.length > 0 && (
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table>
              <thead><tr><th>Line</th><th>Symbol</th><th>Count</th><th style={{ textAlign: 'right' }}>Pays</th></tr></thead>
              <tbody>
                {bet.result.lines.map((l, i) => (
                  <tr key={i}>
                    <td>{l.line}</td>
                    <td>{GLYPH[l.symbol] || l.symbol}</td>
                    <td>{l.count}</td>
                    <td style={{ textAlign: 'right' }}>{l.pay_multiple}x line · {fmtCents(l.pay_cents)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p className="tiny muted" style={{ marginTop: 10 }}>
          Wild 🌟 substitutes for every symbol except scatter ⭐. Bet must be a multiple of $0.20
          (1 cent per line). Published RTP 96%.
        </p>
      </Card>

      <div style={{ marginTop: 14 }}>
        <StakeRow stake={stake} setStake={setStake} min={Math.max(minBet, 20)} max={maxBet}
          onPlay={submit} busy={busy} label="Spin" children={null} />
      </div>

      <ResultBanner bet={bet} />
      <ProvablyFairNote bet={bet} />
    </>
  )
}
