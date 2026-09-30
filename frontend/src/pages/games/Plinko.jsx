import { useState } from 'react'
import { Alert, Card } from '../../components/ui.jsx'
import { useBetSubmit } from './useBet.js'
import StakeRow from './StakeRow.jsx'
import { ResultBanner, ProvablyFairNote } from '../GameRoom.jsx'
import { fmtCents } from '../../lib/format.js'

// Mirrors the server's published tables (app/games/instant.py::PLINKO_TABLES).
// These are for the preview only - the payout always comes from the server.
const TABLES = {
  8: {
    low: [5.6, 2.1, 1.1, 1.0, 0.5, 1.0, 1.1, 2.1, 5.6],
    medium: [13, 3, 1.3, 0.7, 0.4, 0.7, 1.3, 3, 13],
    high: [29, 4, 1.5, 0.3, 0.2, 0.3, 1.5, 4, 29],
  },
  12: {
    low: [10, 3, 1.6, 1.4, 1.1, 1.0, 0.5, 1.0, 1.1, 1.4, 1.6, 3, 10],
    medium: [33, 11, 4, 2, 1.1, 0.6, 0.3, 0.6, 1.1, 2, 4, 11, 33],
    high: [170, 24, 8.1, 2, 0.7, 0.2, 0.2, 0.2, 0.7, 2, 8.1, 24, 170],
  },
  16: {
    low: [16, 9, 2, 1.4, 1.4, 1.2, 1.1, 1.0, 0.5, 1.0, 1.1, 1.2, 1.4, 1.4, 9, 16],
    medium: [110, 41, 10, 5, 3, 1.5, 1.0, 0.5, 0.3, 0.5, 1.0, 1.5, 3, 5, 10, 41, 110],
    high: [1000, 130, 26, 9, 4, 2, 0.2, 0.2, 0.2, 0.2, 0.2, 2, 4, 9, 26, 130, 1000],
  },
}

export default function Plinko({ minBet, maxBet, onSettled }) {
  const [stake, setStake] = useState('1.00')
  const [rows, setRows] = useState(12)
  const [risk, setRisk] = useState('medium')
  const [bet, setBet] = useState(null)
  const { busy, error, play } = useBetSubmit(onSettled)

  const table = TABLES[rows][risk]
  const landed = bet?.result?.bucket

  const submit = async () => {
    const result = await play('plinko', stake, { rows: Number(rows), risk })
    if (result) setBet(result)
  }

  return (
    <>
      {error && <Alert kind="error">{error}</Alert>}

      <Card>
        <div className="row" style={{ marginBottom: 14, gap: 12, alignItems: 'flex-end' }}>
          <div className="field" style={{ flex: 1 }}>
            <label>Rows</label>
            <select value={rows} onChange={(e) => setRows(Number(e.target.value))}>
              {[8, 12, 16].map((r) => <option key={r} value={r}>{r} rows</option>)}
            </select>
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>Risk</label>
            <select value={risk} onChange={(e) => setRisk(e.target.value)}>
              {['low', 'medium', 'high'].map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        </div>

        {/* bucket preview - width proportional to each bucket's multiplier */}
        <div className="row" style={{ gap: 4, alignItems: 'flex-end', height: 150, marginBottom: 12 }}>
          {table.map((m, i) => {
            const max = Math.max(...table)
            const height = 22 + (m / max) * 110
            const hit = landed === i
            return (
              <div
                key={i}
                title={`${m}x`}
                style={{
                  flex: 1, height, borderRadius: 6,
                  background: hit
                    ? 'linear-gradient(180deg, var(--accent), #ff8a45)'
                    : m >= 1
                      ? 'linear-gradient(180deg, rgba(124,92,255,.8), rgba(124,92,255,.25))'
                      : 'linear-gradient(180deg, rgba(255,84,112,.7), rgba(255,84,112,.2))',
                  display: 'grid', placeItems: 'center', fontSize: 11, fontWeight: 700,
                  transition: '0.2s',
                }}
              >
                {m}x
              </div>
            )
          })}
        </div>

        {landed !== undefined && (
          <p className="small muted" style={{ marginBottom: 0 }}>
            Ball landed in bucket {landed} after {bet.result.path.join('')} ·{' '}
            <strong>{fmtCents(bet.payout)}</strong>
          </p>
        )}
      </Card>

      <div style={{ marginTop: 14 }}>
        <StakeRow stake={stake} setStake={setStake} min={minBet} max={maxBet}
          onPlay={submit} busy={busy} label="Drop ball" children={null} />
      </div>

      <ResultBanner bet={bet} />
      <ProvablyFairNote bet={bet} />
    </>
  )
}
