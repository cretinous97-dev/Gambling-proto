import { useState } from 'react'
import { Alert, Card } from '../../components/ui.jsx'
import { useBetSubmit } from './useBet.js'
import StakeRow from './StakeRow.jsx'
import { ResultBanner, ProvablyFairNote } from '../GameRoom.jsx'
import { fmtCents } from '../../lib/format.js'

const RED = [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]
const OUTSIDE = [
  { kind: 'red', label: 'Red', colour: '#c0364b' },
  { kind: 'black', label: 'Black', colour: '#22283d' },
  { kind: 'odd', label: 'Odd' },
  { kind: 'even', label: 'Even' },
  { kind: 'low', label: '1–18' },
  { kind: 'high', label: '19–36' },
  { kind: 'dozen', label: '1st 12', dozen: 1 },
  { kind: 'dozen', label: '2nd 12', dozen: 2 },
  { kind: 'dozen', label: '3rd 12', dozen: 3 },
  { kind: 'column', label: 'Col 1', column: 1 },
  { kind: 'column', label: 'Col 2', column: 2 },
  { kind: 'column', label: 'Col 3', column: 3 },
]

/**
 * European roulette. Every leg is placed in ONE request so the whole spin is a
 * single atomic wager - partial placement cannot happen, and the server
 * validates that the leg stakes sum to the total wager.
 */
export default function Roulette({ minBet, maxBet, onSettled }) {
  const [legs, setLegs] = useState({})   // key -> { kind, stake, ...coverage }
  const [chip, setChip] = useState('1.00')
  const [bet, setBet] = useState(null)
  const { busy, error, play } = useBetSubmit(onSettled)

  const keyOf = (leg) => `${leg.kind}:${leg.number ?? leg.dozen ?? leg.column ?? ''}`
  const addLeg = (leg) => {
    const key = keyOf(leg)
    setLegs((prev) => ({
      ...prev,
      [key]: { ...leg, stake: (prev[key]?.stake || 0) + Math.round(Number(chip) * 100) },
    }))
  }
  const clearLegs = () => setLegs({})

  const totalStake = Object.values(legs).reduce((sum, l) => sum + l.stake, 0)
  const pocket = bet?.result?.pocket

  const spin = async () => {
    if (!totalStake) return
    const payload = Object.values(legs).map((l) => ({
      kind: l.kind,
      stake: l.stake,
      ...(l.number !== undefined ? { number: l.number } : {}),
      ...(l.dozen !== undefined ? { dozen: l.dozen } : {}),
      ...(l.column !== undefined ? { column: l.column } : {}),
    }))
    const result = await play('roulette', (totalStake / 100).toFixed(2), { bets: payload })
    if (result) {
      setBet(result)
      clearLegs()
    }
  }

  return (
    <>
      {error && <Alert kind="error">{error}</Alert>}

      <Card>
        <div className="row between" style={{ marginBottom: 10 }}>
          <div className="row" style={{ gap: 6 }}>
            <span className="small muted">Chip:</span>
            {['0.25', '1.00', '5.00', '25.00'].map((c) => (
              <button key={c} className={`btn btn-sm ${chip === c ? 'btn-primary' : ''}`} onClick={() => setChip(c)}>
                ${Number(c).toFixed(2)}
              </button>
            ))}
          </div>
          <div className="row" style={{ gap: 6 }}>
            <button className="btn btn-sm btn-ghost" onClick={clearLegs}>Clear bets</button>
          </div>
        </div>

        {/* straight numbers */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(13, 1fr)', gap: 4 }}>
          <button
            onClick={() => addLeg({ kind: 'straight', number: 0 })}
            style={{
              padding: '10px 0', borderRadius: 7, border: '1px solid var(--line)',
              background: pocket === 0 ? 'var(--ok)' : 'rgba(53,208,127,.35)',
              fontWeight: 800, color: 'var(--text)',
            }}
          >
            0
          </button>
          {Array.from({ length: 36 }, (_, i) => i + 1).map((n) => (
            <button
              key={n}
              onClick={() => addLeg({ kind: 'straight', number: n })}
              style={{
                padding: '10px 0', borderRadius: 7, fontWeight: 700,
                border: pocket === n ? '2px solid var(--accent)' : '1px solid var(--line)',
                background: RED.includes(n) ? 'rgba(192,54,75,.55)' : 'rgba(34,40,61,.9)',
                color: 'var(--text)',
              }}
            >
              {n}
            </button>
          ))}
        </div>

        {/* outside bets */}
        <div className="row" style={{ marginTop: 8, gap: 5 }}>
          {OUTSIDE.map((o) => (
            <button
              key={o.label}
              className="btn btn-sm"
              style={o.colour ? { background: o.colour } : undefined}
              onClick={() => addLeg({ kind: o.kind, dozen: o.dozen, column: o.column })}
            >
              {o.label}
            </button>
          ))}
        </div>

        {Object.keys(legs).length > 0 && (
          <div className="row" style={{ marginTop: 12, gap: 6 }}>
            {Object.entries(legs).map(([key, l]) => (
              <span key={key} className="badge badge-warn">
                {l.kind}
                {l.number !== undefined ? ` ${l.number}` : ''}
                {l.dozen ? ` D${l.dozen}` : ''}
                {l.column ? ` C${l.column}` : ''} · {fmtCents(l.stake)}
              </span>
            ))}
          </div>
        )}

        <div className="grid grid-3" style={{ marginTop: 14 }}>
          <div className="stat"><span className="label">Total on table</span><span className="value">{fmtCents(totalStake)}</span></div>
          <div className="stat">
            <span className="label">Pocket</span>
            <span className="value">
              {pocket !== undefined ? (
                <span style={{ color: pocket === 0 ? 'var(--ok)' : RED.includes(pocket) ? 'var(--bad)' : 'var(--text)' }}>
                  {pocket} {bet.result.colour}
                </span>
              ) : '—'}
            </span>
          </div>
          <div className="stat">
            <span className="label">Returned</span>
            <span className="value">{bet ? fmtCents(bet.payout) : '—'}</span>
          </div>
        </div>

        {bet?.result?.legs && (
          <p className="tiny muted" style={{ marginTop: 10 }}>
            {bet.result.legs.filter((l) => l.won).length} of {bet.result.legs.length} bets won ·
            RTP 97.30% (single-zero wheel)
          </p>
        )}
      </Card>

      <div style={{ marginTop: 14 }}>
        <StakeRow
          stake={(totalStake / 100).toFixed(2)}
          setStake={() => {}}
          min={minBet}
          max={maxBet}
          onPlay={spin}
          busy={busy}
          label={`Spin (${fmtCents(totalStake)})`}
          disabled={!totalStake}
        >
          <div className="alert alert-info tiny" style={{ margin: 0, maxWidth: 260 }}>
            The wager is the total of all chips on the table. Add chips above; the stake field
            mirrors the table total.
          </div>
        </StakeRow>
      </div>

      <ResultBanner bet={bet} />
      <ProvablyFairNote bet={bet} />
    </>
  )
}
