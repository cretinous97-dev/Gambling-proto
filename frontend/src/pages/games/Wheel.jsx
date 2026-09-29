import { useState } from 'react'
import { Alert, Card } from '../../components/ui.jsx'
import { useBetSubmit } from './useBet.js'
import StakeRow from './StakeRow.jsx'
import { ResultBanner, ProvablyFairNote } from '../GameRoom.jsx'
import { fmtCents } from '../../lib/format.js'

// Published segment tables - mirrors app/games/instant.py::WHEEL_SEGMENTS.
const SEGMENTS = {
  low: [...Array(15).fill(0), ...Array(30).fill(1.2), ...Array(6).fill(1.5), ...Array(2).fill(2), 3],
  medium: [...Array(24).fill(0), ...Array(20).fill(1.2), ...Array(5).fill(1.5), ...Array(2).fill(2), ...Array(2).fill(3), 10],
  high: [...Array(37).fill(0), ...Array(11).fill(1.5), ...Array(3).fill(2), ...Array(2).fill(5), 20],
}

const COLOURS = {
  0: 'rgba(255,84,112,.75)',
  1.2: 'rgba(53,208,127,.7)',
  1.5: 'rgba(53,208,127,.85)',
  2: 'rgba(255,204,77,.85)',
  3: '#ffb020',
  5: '#ff8a45',
  10: '#ff5470',
  20: '#c04bff',
}

export default function Wheel({ minBet, maxBet, onSettled }) {
  const [stake, setStake] = useState('1.00')
  const [risk, setRisk] = useState('medium')
  const [bet, setBet] = useState(null)
  const [spinning, setSpinning] = useState(false)
  const { busy, error, play } = useBetSubmit(onSettled)

  const segments = SEGMENTS[risk]
  const landed = bet?.result?.segment
  const rtp = risk === 'low' ? '96.3%' : risk === 'medium' ? '95.4%' : '97.2%'

  const submit = async () => {
    setSpinning(true)
    const result = await play('wheel', stake, { risk })
    if (result) setBet(result)
    setTimeout(() => setSpinning(false), 600)
  }

  return (
    <>
      {error && <Alert kind="error">{error}</Alert>}

      <Card>
        <div className="field">
          <label>Risk profile</label>
          <div className="row">
            {['low', 'medium', 'high'].map((r) => (
              <button key={r} className={`btn btn-sm ${risk === r ? 'btn-primary' : ''}`} onClick={() => setRisk(r)}>
                {r}
              </button>
            ))}
            <span className="tiny muted">RTP {rtp}</span>
          </div>
        </div>

        {/* the wheel drawn as a ring of segments */}
        <div style={{ display: 'grid', placeItems: 'center', padding: '18px 0' }}>
          <div
            style={{
              width: 220, height: 220, borderRadius: '50%', position: 'relative',
              background: 'conic-gradient(from 0deg, transparent 0 100%)',
              boxShadow: '0 0 0 6px var(--panel-2), 0 0 30px rgba(0,0,0,.5)',
              transition: 'transform 0.6s cubic-bezier(.2,.8,.3,1)',
              transform: spinning ? 'rotate(720deg)' : 'rotate(0deg)',
            }}
          >
            {segments.map((m, i) => {
              const angle = (360 / segments.length) * i
              return (
                <div
                  key={i}
                  title={`${m}x`}
                  style={{
                    position: 'absolute', inset: 0, transform: `rotate(${angle}deg)`,
                  }}
                >
                  <span
                    style={{
                      position: 'absolute', top: 6, left: '50%', width: 3,
                      height: landed === i ? 34 : 20,
                      background: COLOURS[m] || '#666',
                      transform: 'translateX(-50%)',
                      borderRadius: 2,
                      boxShadow: landed === i ? '0 0 12px var(--accent)' : 'none',
                    }}
                  />
                </div>
              )
            })}
            <div
              style={{
                position: 'absolute', inset: 42, borderRadius: '50%', background: 'var(--panel)',
                display: 'grid', placeItems: 'center', border: '1px solid var(--line)',
              }}
            >
              <div style={{ textAlign: 'center' }}>
                <div className="tiny muted">Segment</div>
                <div style={{ fontSize: 26, fontWeight: 900 }}>
                  {bet ? `${bet.result.multiplier}x` : '—'}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="grid grid-3">
          <div className="stat"><span className="label">Segments</span><span className="value">{segments.length}</span></div>
          <div className="stat">
            <span className="label">Top prize</span>
            <span className="value">{Math.max(...segments)}x</span>
          </div>
          <div className="stat">
            <span className="label">Payout on win</span>
            <span className="value">
              {bet ? fmtCents(bet.payout) : fmtCents(Math.floor(Number(stake || 0) * 100 * 1.2))}
            </span>
          </div>
        </div>
      </Card>

      <div style={{ marginTop: 14 }}>
        <StakeRow stake={stake} setStake={setStake} min={minBet} max={maxBet}
          onPlay={submit} busy={busy} label="Spin" children={null} />
      </div>

      <ResultBanner bet={bet} />
      <ProvablyFairNote bet={bet} />
    </>
  )
}
