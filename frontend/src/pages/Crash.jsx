import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, openCrashSocket } from '../lib/api.js'
import { fmtCents } from '../lib/format.js'
import { Alert, Card, Loading, Stat } from '../components/ui.jsx'
import { useStore } from '../lib/store.jsx'

/**
 * Crash. The multiplier clock is driven by SERVER frames (websocket, with a
 * REST poll as fallback). Between frames the client interpolates purely for
 * smooth animation - cash-out is always priced by the server, so a client with
 * a doctored clock cannot buy a better number.
 */
export default function Crash() {
  const { isAuthed, config, refreshWallet, toast, wallet } = useStore()
  const [state, setState] = useState(null)
  const [, setBet] = useState(null)
  const [stake, setStake] = useState('1.00')
  const [autoCashout, setAutoCashout] = useState('')
  const [myBet, setMyBet] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [history, setHistory] = useState([])
  const lastFrame = useRef({ at: 0, multiplier: 1, phase: 'betting' })
  const connected = useRef(false)
  const [display, setDisplay] = useState(1)

  const refreshMyBet = useCallback(async () => {
    if (!isAuthed) return
    try {
      setMyBet((await api.crashMe()).your_bet)
    } catch {
      /* not fatal */
    }
  }, [isAuthed])

  // The feed is a subscription: rebuilding it every time a callback's identity
  // changes would drop frames mid-round, and a socket that reconnects during a
  // running round is a socket that misses the crash. So the handlers the feed
  // calls live in a ref that always points at the current render's closures.
  //
  // This is not a nicety. The effect below opens the socket once, on mount,
  // when the session check has not finished and `isAuthed` is still false -
  // and `refreshMyBet` captured from that render returns immediately, forever.
  // The player's own bet then never updated on a crash, on exactly the load
  // order most players hit.
  const handlers = useRef({})
  useEffect(() => {
    handlers.current = { refreshMyBet, refreshWallet }
  }, [refreshMyBet, refreshWallet])

  // ---- live feed ---------------------------------------------------------
  useEffect(() => {
    const onMessage = (msg) => {
      if (msg.type !== 'crash.state') return
      lastFrame.current = {
        at: Date.now(),
        multiplier: msg.multiplier ?? 1,
        phase: msg.phase,
      }
      setState(msg)
      setDisplay(msg.multiplier ?? 1)
      if (msg.history) setHistory(msg.history)
      if (msg.phase === 'crashed') {
        handlers.current.refreshWallet?.()
        handlers.current.refreshMyBet?.()
      }
    }
    // Wire the live feed. If the transport never connects - serverless rejects
    // the upgrade, a proxy strips it, a corporate network blocks it - the poll
    // below is the only thing keeping the game on screen, so it must not wait
    // for the socket to fail first.
    let socket = null
    try {
      socket = openCrashSocket(onMessage, () => {
        connected.current = true
      })
    } catch {
      socket = null
    }

    // REST fallback so the page still works if websockets are blocked
    const poll = setInterval(async () => {
      // A live socket sends a frame every 250ms, so a fresh frame means the
      // socket is healthy. Otherwise poll - which is the normal path on a
      // deployment without websocket support.
      if (connected.current && Date.now() - lastFrame.current.at < 4000) return
      try {
        const s = await api.crashState()
        onMessage({ type: 'crash.state', ...s, multiplier: s.multiplier ?? 1 })
      } catch {
        /* ignore */
      }
    }, 3000)

    return () => {
      socket?.close()
      clearInterval(poll)
    }
  }, [])

  // smooth animation between frames (display only, never used for settlement)
  useEffect(() => {
    if (lastFrame.current.phase !== 'running') return undefined
    const timer = setInterval(() => {
      const { at, multiplier } = lastFrame.current
      const elapsed = (Date.now() - at) / 1000
      setDisplay(multiplier * Math.pow(1.0718, elapsed))
    }, 80)
    return () => clearInterval(timer)
  }, [state?.phase, state?.round_number])

  useEffect(() => {
    if (isAuthed) refreshMyBet()
  }, [isAuthed, state?.round_number, refreshMyBet])

  const placeBet = async () => {
    setError('')
    setBusy(true)
    try {
      const res = await api.crashBet({
        stake,
        auto_cashout: autoCashout ? Number(autoCashout) : null,
        idempotency_key: `crash-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      })
      setBet(res)
      toast('Bet placed - good luck', 'success')
      await Promise.all([refreshWallet(), refreshMyBet()])
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const cashout = async () => {
    setError('')
    setBusy(true)
    try {
      const res = await api.crashCashout({})
      toast(`Cashed out at ${res.multiplier.toFixed(2)}x for ${fmtCents(res.payout)}`, 'success')
      await Promise.all([refreshWallet(), refreshMyBet()])
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (!state) return <div className="page medium"><Loading label="Connecting to the live round…" /></div>

  const phase = state.phase
  const running = phase === 'running'
  const betting = phase === 'betting'
  const crashed = phase === 'crashed' || phase === 'settled'
  const shownMultiplier = crashed ? state.crash_point ?? display : running ? display : 1

  const myActiveBet = myBet && !myBet.settled
  const curveHeight = Math.min(100, Math.max(6, (Math.log(Math.max(shownMultiplier, 1.01)) / Math.log(20)) * 100))

  return (
    <div className="page medium">
      <div className="row between" style={{ marginBottom: 12 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>🚀 Crash</h1>
          <span className="tiny muted">
            Round #{state.round_number} · seed hash{' '}
            <span className="mono">{String(state.server_seed_hash || '').slice(0, 20)}…</span>
          </span>
        </div>
        <div className="row">
          <span className={`badge ${betting ? 'badge-warn' : running ? 'badge-ok' : 'badge-bad'}`}>
            {betting ? 'Betting open' : running ? 'In flight' : 'Crashed'}
          </span>
          {state.player_count > 0 && (
            <span className="badge badge-muted">{state.player_count} players</span>
          )}
        </div>
      </div>

      <div className="crash-history">
        {history.map((h) => (
          <span key={h.round_number} className={`crash-pill ${h.crash_point >= 10 ? 'hot' : ''}`}>
            {Number(h.crash_point).toFixed(2)}x
          </span>
        ))}
      </div>

      <div className="crash-canvas" style={{ marginTop: 8 }}>
        <div
          style={{
            position: 'absolute', bottom: 0, left: 0, right: 0,
            height: `${curveHeight}%`,
            background: crashed
              ? 'linear-gradient(180deg, rgba(255,84,112,.35), rgba(255,84,112,0))'
              : 'linear-gradient(180deg, rgba(53,208,127,.3), rgba(53,208,127,0))',
            borderTop: crashed ? '2px solid var(--bad)' : '2px solid var(--ok)',
            transition: 'height .12s linear',
          }}
        />
        <div className={`crash-multiplier ${crashed ? 'crashed' : 'running'}`}>
          {crashed && 'CRASHED @ '}
          {Number(shownMultiplier || 1).toFixed(2)}x
        </div>
        {crashed && (
          <div style={{ position: 'absolute', bottom: 10, width: '100%', textAlign: 'center' }} className="tiny muted">
            Round seed revealed: <span className="mono">{String(state.server_seed || '').slice(0, 24)}…</span> —
            verify on the Account page
          </div>
        )}
      </div>

      {error && <Alert kind="error">{error}</Alert>}

      <div className="grid grid-2" style={{ marginTop: 16, alignItems: 'start' }}>
        <Card title={myActiveBet ? 'Your bet this round' : 'Place a bet'}>
          {!isAuthed ? (
            <>
              <p className="muted small">Log in to join the round.</p>
              <div className="row">
                <Link className="btn btn-primary" to="/login">Log in</Link>
                <Link className="btn" to="/register">Sign up</Link>
              </div>
            </>
          ) : myActiveBet ? (
            <>
              <div className="grid grid-3">
                <Stat label="Stake" value={fmtCents(myActiveBet.stake)} />
                <Stat label="Now worth" value={fmtCents(myActiveBet.current_value || myActiveBet.stake)} />
                <Stat label="Auto cash-out" value={myActiveBet.auto_cashout ? `${myActiveBet.auto_cashout}x` : 'off'} />
              </div>
              <button
                className="btn btn-ok btn-block"
                style={{ marginTop: 14 }}
                onClick={cashout}
                disabled={busy || !running}
              >
                {busy ? <span className="spinner" /> : running
                  ? `Cash out ${fmtCents(myActiveBet.current_value || myActiveBet.stake)}`
                  : 'Waiting for the round…'}
              </button>
            </>
          ) : (
            <>
              <div className="field">
                <label>Bet amount (USD)</label>
                <input inputMode="decimal" value={stake}
                  onChange={(e) => setStake(e.target.value.replace(/[^0-9.]/g, ''))} />
                <span className="tiny muted">
                  Min {(config?.limits?.min_bet / 100 || 0.1).toFixed(2)} · Max{' '}
                  {(config?.limits?.max_bet / 100 || 2000).toFixed(2)}
                </span>
              </div>
              <div className="field">
                <label>Auto cash-out (optional)</label>
                <input inputMode="decimal" value={autoCashout}
                  onChange={(e) => setAutoCashout(e.target.value.replace(/[^0-9.]/g, ''))}
                  placeholder="e.g. 2.00" />
                <span className="tiny muted">
                  Fires automatically if the curve reaches your target — it cannot fire after the bust.
                </span>
              </div>
              <button className="btn btn-primary btn-block" onClick={placeBet} disabled={busy || !betting || !stake}>
                {busy ? <span className="spinner" /> : betting ? 'Place bet' : 'Betting closed — next round soon'}
              </button>
              {myBet && myBet.settled && (
                <Alert kind={myBet.payout > 0 ? 'ok' : 'error'}>
                  Last round: {myBet.payout > 0
                    ? `cashed out at ${myBet.multiplier}x for ${fmtCents(myBet.payout)}`
                    : `lost ${fmtCents(myBet.stake)}`}
                </Alert>
              )}
            </>
          )}
        </Card>

        <Card title="Players in this round">
          <div className="players-list">
            {(state.players || []).length === 0 && <p className="muted small">No players yet — be first.</p>}
            {(state.players || []).map((p, i) => (
              <div key={i} className={`player-row ${p.is_you ? 'you' : ''}`}>
                <span>{p.username}{p.is_you ? ' (you)' : ''}</span>
                <span className="row" style={{ gap: 8 }}>
                  <span>{fmtCents(p.stake)}</span>
                  {p.auto_cashout && <span className="badge badge-muted">auto {p.auto_cashout}x</span>}
                </span>
              </div>
            ))}
          </div>
          <hr style={{ border: 0, borderTop: '1px solid var(--line)', margin: '12px 0' }} />
          <div className="grid grid-2">
            <Stat label="Total staked" value={fmtCents(state.total_stake || 0)} />
            <Stat label="Cash balance" value={fmtCents(wallet?.cash ?? 0)} />
          </div>
        </Card>
      </div>

      <p className="tiny muted" style={{ marginTop: 14 }}>
        The bust point is fixed from a committed seed before betting opens and revealed when the
        round ends, so every round can be verified afterwards. Cash-outs are timed by the server
        clock: a request that arrives after the bust is not honoured. House edge 1%.
        {' '}<Link to="/account" style={{ color: 'var(--accent)' }}>Verify a past round →</Link>
      </p>
    </div>
  )
}
