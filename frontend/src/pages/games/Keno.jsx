import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Alert, Card } from '../../components/ui.jsx'
import { useBetSubmit } from './useBet.js'
import StakeRow from './StakeRow.jsx'
import { ResultBanner, ProvablyFairNote } from '../GameRoom.jsx'
import { fmtCents } from '../../lib/format.js'

// Published table - mirrors app/games/instant.py::KENO_PAYTABLE.
const PAYTABLE = {
  1: [0, 3.84],
  2: [0, 0, 15.96],
  3: [0, 0, 4.94, 19.76],
  4: [0, 0, 1.84, 9.21, 55.3],
  5: [0, 0, 0, 6.14, 29.5, 135.24],
  6: [0, 0, 0, 3.5, 11.68, 46.72, 210.24],
  7: [0, 0, 0, 0, 8.41, 42.08, 189.38, 757.55],
  8: [0, 0, 0, 0, 5.24, 18.35, 65.56, 236.04, 786.8],
  9: [0, 0, 0, 0, 3.11, 10.38, 31.14, 124.56, 415.21, 1245.64],
  10: [0, 0, 0, 0, 0, 8.06, 26.88, 107.54, 403.28, 1344.29, 4032.88],
}

export default function Keno({ minBet, maxBet, onSettled }) {
  const { t } = useTranslation()
  const [stake, setStake] = useState('1.00')
  const [picks, setPicks] = useState([1, 7, 13, 22, 30])
  const [bet, setBet] = useState(null)
  const { busy, error, play } = useBetSubmit(onSettled)

  const toggle = (n) => {
    setPicks((p) => (p.includes(n) ? p.filter((x) => x !== n) : p.length < 10 ? [...p, n] : p))
  }
  const clear = () => setPicks([])
  const random = () => {
    const set = new Set()
    while (set.size < Math.max(picks.length, 5)) set.add(1 + Math.floor(Math.random() * 80))
    setPicks([...set])
  }

  const drawn = new Set(bet?.result?.drawn || [])
  const hits = new Set(bet?.result?.hit_numbers || [])
  const table = PAYTABLE[Math.max(picks.length, 1)] || []

  const submit = async () => {
    if (!picks.length) return
    const result = await play('keno', stake, { picks: [...picks].sort((a, b) => a - b) })
    if (result) setBet(result)
  }

  return (
    <>
      {error && <Alert kind="error">{error}</Alert>}

      <Card>
        <div className="row between" style={{ marginBottom: 10 }}>
          <div className="small muted">{t('games.keno_pick')}</div>
          <div className="row" style={{ gap: 6 }}>
            <button className="btn btn-sm" onClick={random}>Random</button>
            <button className="btn btn-sm btn-ghost" onClick={clear}>Clear</button>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(10, 1fr)', gap: 5 }}>
          {Array.from({ length: 80 }, (_, i) => i + 1).map((n) => {
            const picked = picks.includes(n)
            const wasDrawn = drawn.has(n)
            const hit = hits.has(n)
            return (
              <button
                key={n}
                onClick={() => toggle(n)}
                disabled={busy}
                style={{
                  padding: '8px 0', borderRadius: 7, fontSize: 12, fontWeight: 700,
                  border: `1px solid ${picked ? 'var(--accent)' : 'var(--line)'}`,
                  background: hit
                    ? 'rgba(53,208,127,.85)'
                    : picked
                      ? 'rgba(255,204,77,.25)'
                      : wasDrawn
                        ? 'rgba(124,92,255,.22)'
                        : 'var(--panel-2)',
                  color: 'var(--text)',
                }}
              >
                {n}
              </button>
            )
          })}
        </div>

        <div className="row between tiny muted" style={{ marginTop: 10 }}>
          <span>{t('games.keno_legend')}</span>
          <span>{picks.length} selected</span>
        </div>

        {table.length > 1 && (
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table>
              <thead>
                <tr><th>Hits</th>{table.map((_, i) => <th key={i}>{i}</th>)}</tr>
              </thead>
              <tbody>
                <tr>
                  <td><strong>Pays</strong></td>
                  {table.map((m, i) => (
                    <td key={i} style={{ color: m > 0 ? 'var(--ok)' : 'var(--muted)' }}>
                      {m > 0 ? `${m}x` : '—'}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        )}

        {bet && (
          <div className="stat" style={{ marginTop: 12 }}>
            <span className="label">Result</span>
            <span className="value">
              {bet.result.hits} hits · {fmtCents(bet.payout)}
            </span>
          </div>
        )}
      </Card>

      <div style={{ marginTop: 14 }}>
        <StakeRow stake={stake} setStake={setStake} min={minBet} max={maxBet}
          onPlay={submit} busy={busy} label="Draw" disabled={!picks.length} children={null} />
      </div>

      <ResultBanner bet={bet} />
      <ProvablyFairNote bet={bet} />
    </>
  )
}
