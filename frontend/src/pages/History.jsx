import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { fmtCents, fmtMultiplier, GAME_ICONS } from '../lib/format.js'
import { useTranslation } from 'react-i18next'
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
  const { t } = useTranslation()
  const [tab, setTab] = useState('all')
  // Game names stay as written - they are titles, not vocabulary.
  const tabs = FILTERS.map((f) => (f.id === 'all' ? { ...f, label: t('common.all') } : f))
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
      <h1 style={{ marginTop: 0 }}>{t('bets.title')}</h1>
      <Tabs tabs={tabs} active={tab} onChange={setTab} />

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Card className="tight"><div className="stat"><span className="label">{t('bets.shown')}</span><span className="value">{bets.length}</span></div></Card>
        <Card className="tight"><div className="stat"><span className="label">{t('bets.wagered')}</span><span className="value">{fmtCents(wagered)}</span></div></Card>
        <Card className="tight"><div className="stat"><span className="label">{t('bets.returned')}</span><span className="value">{fmtCents(returned)}</span></div></Card>
        <Card className="tight">
          <div className="stat">
            <span className="label">{t('bets.net')}</span>
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
                <th>{t('bets.when')}</th><th>{t('bets.game')}</th><th style={{ textAlign: 'right' }}>{t('bets.stake')}</th>
                <th style={{ textAlign: 'right' }}>{t('bets.multiplier')}</th>
                <th style={{ textAlign: 'right' }}>{t('bets.payout')}</th>
                <th style={{ textAlign: 'right' }}>{t('bets.net')}</th><th>{t('bets.source')}</th><th />
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
                    {b.stake_source === 'user_bonus' ? t('bets.bonus') : t('bets.cash')}
                  </Badge></td>
                  <td>
                    <button className="btn btn-sm" onClick={async () => setDetail(await api.betDetail(b.id))}>
                      {t('bets.details')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {bets.length === 0 && <Empty>{t('bets.empty')}</Empty>}
        </div>
      </Card>

      {detail && (
        <Modal title={t('bets.modal_title', { id: detail.id.slice(0, 12) })} onClose={() => setDetail(null)}>
          <div className="table-wrap">
            <table>
              <tbody>
                <tr><td className="muted">{t('bets.game')}</td><td>{detail.game}</td></tr>
                <tr><td className="muted">{t('bets.stake')}</td><td>{fmtCents(detail.stake)}</td></tr>
                <tr><td className="muted">{t('bets.payout')}</td><td>{fmtCents(detail.payout)}</td></tr>
                <tr><td className="muted">{t('bets.multiplier')}</td><td>{fmtMultiplier(detail.multiplier)}</td></tr>
                <tr><td className="muted">{t('bets.settled')}</td><td>{String(detail.settled)}</td></tr>
                <tr><td className="muted">{t('bets.server_seed_hash')}</td><td className="mono tiny">{detail.fair?.server_seed_hash}</td></tr>
                <tr><td className="muted">{t('bets.client_seed')}</td><td className="mono tiny">{detail.fair?.client_seed}</td></tr>
                <tr><td className="muted">{t('bets.nonce')}</td><td>{detail.fair?.nonce}</td></tr>
              </tbody>
            </table>
          </div>
          <h4 className="small">{t('bets.raw_payload')}</h4>
          <pre className="tiny mono" style={{ background: '#0d1223', padding: 12, borderRadius: 10, overflowX: 'auto', maxHeight: 260 }}>
            {JSON.stringify(detail.result, null, 2)}
          </pre>
        </Modal>
      )}
    </div>
  )
}
