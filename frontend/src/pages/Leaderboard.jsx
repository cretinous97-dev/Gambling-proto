import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { fmtCents, fmtMultiplier } from '../lib/format.js'
import { Card, Empty, Loading } from '../components/ui.jsx'

export default function Leaderboard() {
  const [data, setData] = useState(null)
  const [rounds, setRounds] = useState([])

  useEffect(() => {
    (async () => {
      const [lb, hist] = await Promise.all([api.leaderboard(), api.crashHistory()])
      setData(lb)
      setRounds(hist.rounds || [])
    })()
  }, [])

  if (!data) return <div className="page"><Loading /></div>

  return (
    <div className="page medium">
      <h1 style={{ marginTop: 0 }}>Leaderboard</h1>
      <p className="muted small">
        Usernames are partially masked. Only multipliers and payouts from the last 24 hours are shown.
      </p>

      <div className="grid grid-2" style={{ alignItems: 'start' }}>
        <Card title="Biggest multipliers (24h)">
          <div className="table-wrap">
            <table>
              <thead><tr><th>#</th><th>Player</th><th>Game</th><th style={{ textAlign: 'right' }}>Multiplier</th><th style={{ textAlign: 'right' }}>Payout</th></tr></thead>
              <tbody>
                {(data.top_multipliers || []).map((l, i) => (
                  <tr key={i}>
                    <td>{i + 1}</td>
                    <td className="mono">{l.username}</td>
                    <td>{l.game}</td>
                    <td style={{ textAlign: 'right' }} className="badge badge-ok">{fmtMultiplier(l.multiplier)}</td>
                    <td style={{ textAlign: 'right' }}>{fmtCents(l.payout)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.top_multipliers?.length && <Empty>No wins recorded yet.</Empty>}
          </div>
        </Card>

        <Card title="Crash round history">
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: 'auto' }}>
            <table>
              <thead><tr><th>Round</th><th style={{ textAlign: 'right' }}>Crash point</th><th>Seed hash</th></tr></thead>
              <tbody>
                {rounds.map((r) => (
                  <tr key={r.round_number}>
                    <td>#{r.round_number}</td>
                    <td style={{ textAlign: 'right' }} className={r.crash_point >= 10 ? 'badge badge-warn' : ''}>
                      {Number(r.crash_point).toFixed(2)}x
                    </td>
                    <td className="mono tiny">{String(r.server_seed_hash).slice(0, 18)}…</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  )
}
