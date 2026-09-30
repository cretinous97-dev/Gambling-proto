import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Alert, Card } from '../../components/ui.jsx'
import { api } from '../../lib/api.js'
import { useBetSubmit } from './useBet.js'
import { fmtCents } from '../../lib/format.js'
import { ProvablyFairNote } from '../GameRoom.jsx'

function PlayingCard({ card, small = false }) {
  if (!card) return null
  const red = ['H', 'D'].includes(card.suit)
  const suit = { S: '♠', H: '♥', D: '♦', C: '♣' }[card.suit] || ''
  const hidden = card.rank === '?'
  return (
    <span className={`playing-card ${hidden ? 'back' : ''} ${red && !hidden ? 'red' : ''} ${small ? 'small' : ''}`}>
      {hidden ? '🂠' : `${card.rank}${suit}`}
    </span>
  )
}

export default function Blackjack({ onSettled }) {
  const { t } = useTranslation()
  const [stake, setStake] = useState('5.00')
  const [bet, setBet] = useState(null)
  const [table, setTable] = useState(null)
  const { busy, error, run } = useBetSubmit(onSettled)

  const deal = async () => {
    const result = await run(() =>
      api.blackjackDeal({
        stake,
        idempotency_key: `bj-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      }),
    )
    if (result) {
      setBet(result)
      setTable(result.table)
    }
  }

  const act = async (action) => {
    const result = await run(() => api.blackjackAction(bet.id, action))
    if (result) {
      setBet(result)
      setTable(result.table)
    }
  }

  const inPlay = table && !table.finished
  const totalStake = table?.total_stake || 0

  return (
    <>
      {error && <Alert kind="error">{error}</Alert>}

      <Card>
        {/* dealer */}
        <div style={{ marginBottom: 18 }}>
          <div className="row between">
            <span className="small muted">Dealer</span>
            <span className="small">
              {table ? (
                <>
                  {table.dealer.total}
                  {table.dealer.hidden && ' + ?'}
                  {table.dealer.blackjack && ' · blackjack'}
                  {table.dealer.bust && ' · bust'}
                </>
              ) : null}
            </span>
          </div>
          <div className="row" style={{ gap: 8, minHeight: 80, marginTop: 6 }}>
            {table?.dealer?.cards?.map((c, i) => <PlayingCard key={i} card={c} />) || (
              <span className="muted small">{t('games.press_deal')}</span>
            )}
          </div>
        </div>

        <hr style={{ border: 0, borderTop: '1px solid var(--line)' }} />

        {/* player hands */}
        <div style={{ marginTop: 14 }}>
          <div className="row between">
            <span className="small muted">You</span>
            <span className="small">
              {table && `Total staked ${fmtCents(totalStake)}`}
            </span>
          </div>
          <div className="row" style={{ gap: 22, alignItems: 'flex-start', flexWrap: 'wrap', marginTop: 6 }}>
            {table?.hands?.map((hand, i) => (
              <div
                key={i}
                style={{
                  padding: 10, borderRadius: 12, minWidth: 200,
                  border: `1px solid ${table.active === i && inPlay ? 'var(--accent)' : 'var(--line)'}`,
                  background: table.active === i && inPlay ? 'rgba(255,204,77,.07)' : 'transparent',
                }}
              >
                <div className="row" style={{ gap: 6 }}>
                  {hand.cards.map((c, j) => <PlayingCard key={j} card={c} small />)}
                </div>
                <div className="small" style={{ marginTop: 8 }}>
                  <strong>{hand.total}</strong>
                  {hand.soft && ' (soft)'}
                  {hand.bust && ' · bust'}
                  {hand.blackjack && ' · blackjack'}
                  {hand.doubled && ' · doubled'}
                  {hand.result && ` · ${hand.result}`}
                </div>
                <div className="tiny muted">
                  stake {fmtCents(hand.stake)}
                  {hand.payout > 0 && ` · returns ${fmtCents(hand.payout)}`}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* actions */}
        <div className="row" style={{ marginTop: 18, gap: 8 }}>
          {!inPlay ? (
            <>
              <div className="field" style={{ marginBottom: 0, minWidth: 150 }}>
                <label>Bet amount</label>
                <input inputMode="decimal" value={stake}
                  onChange={(e) => setStake(e.target.value.replace(/[^0-9.]/g, ''))} />
              </div>
              <button className="btn btn-primary" style={{ alignSelf: 'flex-end' }} onClick={deal} disabled={busy || !stake}>
                {busy ? <span className="spinner" /> : 'Deal'}
              </button>
              <span className="tiny muted" style={{ alignSelf: 'flex-end', paddingBottom: 10 }}>
                6-deck shoe · dealer stands on all 17 · blackjack pays 3:2 · split up to 4 hands
              </span>
            </>
          ) : (
            table.actions.map((a) => (
              <button
                key={a}
                className={`btn ${a === 'stand' ? '' : 'btn-primary'}`}
                onClick={() => act(a)}
                disabled={busy}
              >
                {busy ? <span className="spinner" /> : a}
              </button>
            ))
          )}
        </div>
      </Card>

      {table?.finished && (
        <div className={`alert ${bet?.payout > 0 ? 'alert-ok' : 'alert-error'} result-flash`} style={{ marginTop: 14 }}>
          <div className="row between">
            <span>
              Hand settled · {table.hands.map((h) => h.result).join(', ')}
            </span>
            <strong>{fmtCents(bet?.payout || 0)}</strong>
          </div>
          <div className="tiny">
            Net {bet?.profit >= 0 ? '+' : ''}{fmtCents(bet?.profit || 0)}
          </div>
        </div>
      )}

      <ProvablyFairNote bet={bet} />
    </>
  )
}
