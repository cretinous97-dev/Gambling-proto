import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api.js'
import { fmtCents, fmtMultiplier, GAME_ICONS, fmtRelative } from '../lib/format.js'
import { Card, Stat, Loading, Empty } from '../components/ui.jsx'
import { useStore } from '../lib/store.jsx'

export default function Home() {
  const { user, config } = useStore()
  const [catalog, setCatalog] = useState(null)
  const [jackpot, setJackpot] = useState(null)
  const [leaders, setLeaders] = useState([])
  const [promos, setPromos] = useState(null)
  const [chat, setChat] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [error, setError] = useState('')
  const { toast, isAuthed } = useStore()

  useEffect(() => {
    ;(async () => {
      try {
        const [cat, jp, lb, pr, ch] = await Promise.all([
          api.catalog(), api.jackpot(), api.leaderboard(), api.promotions(), api.chat(),
        ])
        setCatalog(cat)
        setJackpot(jp)
        setLeaders(lb.top_multipliers || [])
        setPromos(pr)
        setChat(ch.messages || [])
      } catch (err) {
        setError(err.message)
      }
    })()
  }, [])

  const sendChat = async (e) => {
    e.preventDefault()
    if (!chatInput.trim()) return
    try {
      const res = await api.postChat({ body: chatInput.trim() })
      setChat((prev) => [...prev, res.message])
      setChatInput('')
    } catch (err) {
      toast(err.message, 'error')
    }
  }

  if (!catalog) return <div className="page"><Loading /></div>

  const games = catalog.games || []
  const bonus = config?.bonuses

  return (
    <div className="page">
      {error && <div className="alert alert-error">{error}</div>}

      {/* ------------------------------------------------------------ hero */}
      <div className="card" style={{ marginBottom: 18, background: 'linear-gradient(120deg, #1d2450, #12172c 60%)' }}>
        <div className="row between">
          <div style={{ maxWidth: 620 }}>
            <h1 style={{ margin: '0 0 6px', fontSize: 30 }}>
              {user ? `Welcome back, ${user.username}` : 'Play provably fair casino games'}
            </h1>
            <p className="muted" style={{ margin: 0 }}>
              Every outcome is committed to in advance with a SHA-256 seed hash and can be
              recomputed by you afterwards. Every cent moves through a double-entry ledger.
            </p>
            <div className="row" style={{ marginTop: 14 }}>
              {user ? (
                <>
                  <Link className="btn btn-primary" to="/wallet">Deposit</Link>
                  <Link className="btn" to="/crash">Play Crash</Link>
                </>
              ) : (
                <>
                  <Link className="btn btn-primary" to="/register">
                    Sign up{bonus?.first_deposit_pct ? ` · ${bonus.first_deposit_pct}% first deposit bonus` : ''}
                  </Link>
                  <Link className="btn" to="/login">Log in</Link>
                </>
              )}
            </div>
          </div>

          <div style={{ textAlign: 'right', minWidth: 190 }}>
            <div className="stat">
              <span className="label">Jackpot pool</span>
              <span className="value" style={{ color: 'var(--accent)', fontSize: 28 }}>
                {fmtCents(jackpot?.amount ?? 0)}
              </span>
              <span className="tiny muted">Contributed from every wager</span>
            </div>
          </div>
        </div>
      </div>

      {/* ----------------------------------------------------------- games */}
      <h2 style={{ fontSize: 18 }}>Games</h2>
      <div className="game-grid" style={{ marginBottom: 22 }}>
        <Link to="/crash" className="game-tile" style={{ borderColor: 'var(--accent)' }}>
          <span className="rtp">99% RTP · LIVE</span>
          <div className="icon">🚀</div>
          <div className="name">Crash</div>
          <div className="small muted">Shared live round. Cash out before the curve busts.</div>
        </Link>
        {games.map((g) => (
          <Link key={g.slug} to={`/game/${g.slug}`} className="game-tile">
            <span className="rtp">{(g.rtp * 100).toFixed(1)}% RTP</span>
            <div className="icon">{GAME_ICONS[g.slug] || '🎮'}</div>
            <div className="name">{g.name}</div>
            <div className="small muted">{g.blurb}</div>
          </Link>
        ))}
      </div>

      {/* ---------------------------------------------------------- panels */}
      <div className="grid grid-3" style={{ alignItems: 'start' }}>
        <Card title="Recent big multipliers">
          {leaders.length === 0 && <Empty>No wins in the last 24 hours yet.</Empty>}
          {leaders.map((l, i) => (
            <div key={i} className="row between small" style={{ padding: '6px 0', borderBottom: '1px solid rgba(36,48,82,.5)' }}>
              <span>
                <strong className="mono">{l.username}</strong>
                <span className="muted"> · {l.game}</span>
              </span>
              <span>
                <span className="badge badge-ok">{fmtMultiplier(l.multiplier)}</span>{' '}
                <span className="tiny muted">{fmtCents(l.payout)}</span>
              </span>
            </div>
          ))}
        </Card>

        <Card title="Live chat">
          <div className="chat-box">
            <div className="chat-scroll">
              {chat.length === 0 && <Empty>Be the first to say hello.</Empty>}
              {chat.map((m) => (
                <div key={m.id} className="chat-msg">
                  <span className={`who ${m.vip_tier > 0 ? 'vip' : ''}`}>{m.username}</span>
                  <span>{m.body}</span>
                </div>
              ))}
            </div>
            <form onSubmit={sendChat} className="row" style={{ marginTop: 10 }}>
              <input value={chatInput} onChange={(e) => setChatInput(e.target.value)}
                placeholder={isAuthed ? 'Say something…' : 'Log in to chat'} disabled={!isAuthed} maxLength={400} />
              <button className="btn btn-sm btn-primary" disabled={!isAuthed || !chatInput.trim()}>Send</button>
            </form>
          </div>
        </Card>

        <Card title="Promotions">
          {promos && (
            <>
              {promos.built_in?.first_deposit_pct > 0 && (
                <div className="alert alert-ok">
                  <strong>{promos.built_in.first_deposit_pct}% first deposit bonus</strong>
                  <div className="tiny">
                    Up to {fmtCents(promos.built_in.first_deposit_cap)}, {promos.built_in.wager_x}x playthrough.
                  </div>
                </div>
              )}
              {promos.built_in?.signup_bonus > 0 && (
                <div className="alert alert-info">
                  <strong>{fmtCents(promos.built_in.signup_bonus)} sign-up bonus</strong>
                  <div className="tiny">Credited the moment you register.</div>
                </div>
              )}
              {(promos.promotions || []).map((p) => (
                <div key={p.code} className="alert alert-info">
                  <strong className="mono">{p.code}</strong>
                  <div className="tiny">
                    {p.type === 'percent' ? `${p.value}% match` : `${fmtCents(p.value)} fixed bonus`} ·{' '}
                    {p.wager_multiplier}x playthrough
                  </div>
                </div>
              ))}
              <Link className="btn btn-sm btn-block" to="/promotions">All promotions</Link>
            </>
          )}
        </Card>
      </div>
    </div>
  )
}
