import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { fmtCents, fmtMultiplier, GAME_ICONS } from '../lib/format.js'
import { Alert, Badge, Card, Empty, Loading, Modal, Tabs } from '../components/ui.jsx'

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'crash', label: 'Crash' },
  { id: 'dice', label: 'Dice' },
  { id: 'mines', label: 'Mines' },
  { id: 'blackjack', label: 'Blackjack' },
  { id: 'slots', label: 'Slots' },
  { id: 'roulette', label: 'Roulette' },
  { id: 'plinko', label: 'Plinko' },
]

export default function History() {
  const [tab, setTab] = useState('all')
  const [bets, setBets] = useState(null)
  const [error, setError] = useState('')
  const [detail, setDetail] = useState(null)

  useEffect(() => {
    setBets(null)
    ;(async () => {
      try {
        const res = await api.history(tab === 'all' ? undefined : tab)
        setBets(res.bets)
      } catch (err) { setError(err.message) }
    })()
  }, [tab])

  if (error) return <div className="page"><Alert kind="error">{error}</Alert></div>
  if (!bets) return <div className="page"><Loading /></div>

  const wagered = bets.reduce((s, b) => s + b.stake, 0)
  const returned = bets.reduce((s, b) => s + b.payout, 0)

  return (
    <div className="page">
      <h1 style={{ marginTop: 0 }}>My bets</h1>
      <Tabs tabs={FILTERS} active={tab} onChange={setTab} />

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Card className="tight"><div className="stat"><span className="label">Shown</span><span className="value">{bets.length}</span></div></Card>
        <Card className="tight"><div className="stat"><span className="label">Wagered</span><span className="value">{fmtCents(wagered)}</span></div></Card>
        <Card className="tight"><div className="stat"><span className="label">Returned</span><span className="value">{fmtCents(returned)}</span></div></Card>
        <Card className="tight">
          <div className="stat">
            <span className="label">Net</span>
            <span className="value" style={{ color: returned - wagered >= 0 ? 'var(--ok)' : 'var(--bad)' }}>
              {fmtCents(returned - wagered)}
            </span>
          </div>
        </Card>
      </div>

      <Card>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>When</th><th>Game</th><th style={{ textAlign: 'right' }}>Stake</th>
                <th style={{ textAlign: 'right' }}>Multiplier</th>
                <th style={{ textAlign: 'right' }}>Payout</th>
                <th style={{ textAlign: 'right' }}>Net</th><th>Source</th><th />
              </tr>
            </thead>
            <tbody>
              {bets.map((b) => (
                <tr key={b.id}>
                  <td className="tiny">{new Date(b.created_at).toLocaleString()}</td>
                  <td>{GAME_ICONS[b.game] || '🎮'} {b.game}</td>
                  <td style={{ textAlign: 'right' }}>{fmtCents(b.stake)}</td>
                  <td style={{ textAlign: 'right' }}>
                    {b.multiplier > 0 ? fmtMultiplier(b.multiplier) : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }}>{fmtCents(b.payout)}</td>
                  <td style={{ textAlign: 'right', color: b.profit >= 0 ? 'var(--ok)' : 'var(--bad)' }}>
                    {b.profit >= 0 ? '+' : ''}{fmtCents(b.profit)}
                  </td>
                  <td><Badge status={b.stake_source === 'user_bonus' ? 'pending' : 'succeeded'}>
                    {b.stake_source === 'user_bonus' ? 'bonus' : 'cash'}
                  </Badge></td>
                  <td>
                    <button className="btn btn-sm" onClick={async () => setDetail(await api.betDetail(b.id))}>
                      Details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {bets.length === 0 && <Empty>No bets yet — head to the lobby and place one.</Empty>}
        </div>
      </Card>

      {detail && (
        <Modal title={`Bet ${detail.id.slice(0, 12)}`} onClose={() => setDetail(null)}>
          <div className="table-wrap">
            <table>
              <tbody>
                <tr><td className="muted">Game</td><td>{detail.game}</td></tr>
                <tr><td className="muted">Stake</td><td>{fmtCents(detail.stake)}</td></tr>
                <tr><td className="muted">Payout</td><td>{fmtCents(detail.payout)}</td></tr>
                <tr><td className="muted">Multiplier</td><td>{fmtMultiplier(detail.multiplier)}</td></tr>
                <tr><td className="muted">Settled</td><td>{String(detail.settled)}</td></tr>
                <tr><td className="muted">Server seed hash</td><td className="mono tiny">{detail.fair?.server_seed_hash}</td></tr>
                <tr><td className="muted">Client seed</td><td className="mono tiny">{detail.fair?.client_seed}</td></tr>
                <tr><td className="muted">Nonce</td><td>{detail.fair?.nonce}</td></tr>
              </tbody>
            </table>
          </div>
          <h4 className="small">Raw result payload</h4>
          <pre className="tiny mono" style={{ background: '#0d1223', padding: 12, borderRadius: 10, overflowX: 'auto', maxHeight: 260 }}>
            {JSON.stringify(detail.result, null, 2)}
          </pre>
        </Modal>
      )}
    </div>
  )
}
