import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { fmtCents, fmtDate, fmtMultiplier } from '../lib/format.js'
import { ActionButton, Alert, Badge, Card, Empty, Loading, Modal, Stat, Tabs } from '../components/ui.jsx'
import { useStore } from '../lib/store.jsx'
import BankingManager from './admin/BankingManager.jsx'

/**
 * Back office. Everything here is a privileged operation and every mutation is
 * written to the server-side audit log with the admin's id, before/after
 * values and a free-text reason.
 *
 * On `onError`: every panel takes the parent's `setError`, which is a `useState`
 * dispatcher and therefore referentially stable for the life of the component.
 * That is what makes it safe to list in an effect's dependencies - the rule is
 * satisfied, the fetch still runs once, and the panel can no longer call a
 * stale closure. Passing an inline arrow here instead would create a new
 * function every render and turn these effects into fetch loops, so don't.
 */
export default function Admin() {
  const { toast } = useStore()
  const [tab, setTab] = useState('dashboard')
  const [error, setError] = useState('')

  return (
    <div className="page">
      <h1 style={{ marginTop: 0 }}>Operations</h1>
      {error && <Alert kind="error">{error}</Alert>}
      <Tabs
        active={tab}
        onChange={setTab}
        tabs={[
          { id: 'dashboard', label: 'Dashboard' },
          { id: 'withdrawals', label: 'Withdrawals' },
          { id: 'deposits', label: 'Deposits' },
          { id: 'users', label: 'Players' },
          { id: 'bonuses', label: 'Bonuses' },
          { id: 'banking', label: 'Banking methods' },
          { id: 'risk', label: 'Risk & AML' },
          { id: 'ledger', label: 'Ledger' },
          { id: 'audit', label: 'Audit log' },
          { id: 'system', label: 'System' },
        ]}
      />
      {tab === 'dashboard' && <Dashboard onError={setError} />}
      {tab === 'withdrawals' && <Withdrawals onError={setError} toast={toast} />}
      {tab === 'deposits' && <Deposits onError={setError} />}
      {tab === 'users' && <Users onError={setError} toast={toast} />}
      {tab === 'bonuses' && <Bonuses onError={setError} toast={toast} />}
      {tab === 'banking' && <BankingManager onError={setError} toast={toast} />}
      {tab === 'risk' && <Risk />}
      {tab === 'ledger' && <Ledger onError={setError} />}
      {tab === 'audit' && <Audit onError={setError} />}
      {tab === 'system' && <System onError={setError} />}
    </div>
  )
}

/* ------------------------------------------------------------- dashboard */
function Dashboard({ onError }) {
  const [data, setData] = useState(null)
  const [revenue, setRevenue] = useState([])

  useEffect(() => {
    (async () => {
      try {
        const [d, r] = await Promise.all([api.adminDashboard(), api.adminRevenue(14)])
        setData(d)
        setRevenue(r.series || [])
      } catch (err) { onError(err.message) }
    })()
  }, [onError])

  if (!data) return <Loading />

  const maxGgr = Math.max(1, ...revenue.map((r) => Math.abs(r.ggr)))

  return (
    <>
      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Card className="tight"><Stat label="GGR (24h)" value={fmtCents(data.money_24h.ggr)} hint="Wagered − returned" /></Card>
        <Card className="tight"><Stat label="Deposits (24h)" value={fmtCents(data.money_24h.deposits)} /></Card>
        <Card className="tight"><Stat label="Withdrawals (24h)" value={fmtCents(data.money_24h.withdrawals)} /></Card>
        <Card className="tight">
          <Stat label="Net cash (24h)" value={fmtCents(data.money_24h.net_deposits)}
            hint={data.money_24h.net_deposits >= 0 ? 'Positive = players funding' : 'Negative = paying out'} />
        </Card>
      </div>

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Card className="tight"><Stat label="Players" value={data.players.total} hint={`${data.players.new_24h} new today`} /></Card>
        <Card className="tight"><Stat label="Active (24h)" value={data.players.active_24h} /></Card>
        <Card className="tight">
          <Stat label="Payout queue" value={data.queues.withdrawals_pending}
            hint="Awaiting a human decision" />
        </Card>
        <Card className="tight">
          <Stat label="Books" value={data.integrity.books_balanced ? 'Balanced' : 'BROKEN'}
            hint={data.integrity.books_balanced ? 'Ledger sums to zero' : 'Stop and investigate'} />
        </Card>
      </div>

      <Card title="Gross gaming revenue — last 14 days">
        {revenue.length === 0 ? <Empty>No wagering yet.</Empty> : (
          <div className="bar-chart">
            {revenue.map((r) => (
              <div
                key={r.day}
                className={`bar ${r.ggr < 0 ? 'neg' : ''}`}
                style={{ height: `${(Math.abs(r.ggr) / maxGgr) * 100}%` }}
                data-label={`${r.day}: GGR ${fmtCents(r.ggr)} · ${r.bets} bets · ${r.players} players`}
              />
            ))}
          </div>
        )}
        <div className="row between tiny muted" style={{ marginTop: 8 }}>
          <span>{data.money_7d.margin_pct}% actual hold over 7 days</span>
          <span>Wagered 7d {fmtCents(data.money_7d.wagered)} · returned {fmtCents(data.money_7d.returned)}</span>
        </div>
      </Card>

      <div className="grid grid-3" style={{ marginTop: 16 }}>
        <Card title="Payment provider">
          <div className="table-wrap">
            <table>
              <tbody>
                <tr><td className="muted">Provider</td><td>{data.provider.provider}</td></tr>
                <tr><td className="muted">Mode</td>
                  <td><Badge status={data.provider.mode === 'live' ? 'succeeded' : 'pending'}>{data.provider.mode}</Badge></td></tr>
                <tr><td className="muted">Healthy</td><td>{String(data.provider.ok)}</td></tr>
              </tbody>
            </table>
          </div>
          {data.provider.warning && <Alert kind="warn">{data.provider.warning}</Alert>}
        </Card>

        <Card title="Compliance position">
          <div className="grid grid-2">
            <Stat label="KYC verified" value={data.players.verified} />
            <Stat label="Self-excluded" value={data.players.self_excluded} />
            <Stat label="Suspended" value={data.players.banned} />
            <Stat label="KYC pending" value={data.queues.kyc_pending} />
          </div>
        </Card>

        <Card title="System">
          <div className="grid grid-2">
            <Stat label="Crash sockets" value={data.realtime.crash_sockets} />
            <Stat label="Failed webhooks" value={data.queues.failed_webhooks} />
            <Stat label="Jackpot pool" value={fmtCents(data.jackpot.amount)} />
            <Stat label="Environment" value={data.provider.mode === 'live' ? 'LIVE' : 'demo'} />
          </div>
        </Card>
      </div>
    </>
  )
}

/* ----------------------------------------------------------- withdrawals */
function Withdrawals({ onError, toast }) {
  const [rows, setRows] = useState(null)
  const [filter, setFilter] = useState('under_review')

  const load = useCallback(async () => {
    try {
      const res = await api.adminWithdrawals(filter || undefined)
      setRows(res.withdrawals)
    } catch (err) { onError(err.message) }
  }, [filter, onError])
  useEffect(() => { load() }, [load])

  const decide = async (w, approve) => {
    const reason = approve ? window.prompt('Review note (optional)') : window.prompt('Rejection reason (required)')
    if (!approve && !reason) return
    try {
      const res = await api.adminReview(w.id, { approve, note: reason, reason })
      toast(`Withdrawal ${res.status}`, 'success')
      await load()
    } catch (err) { onError(err.message) }
  }

  if (!rows) return <Loading />

  return (
    <Card
      title={`Withdrawal queue (${rows.length})`}
      actions={
        <select value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: 200 }}>
          <option value="under_review">Under review</option>
          <option value="requested">Requested</option>
          <option value="approved">Approved</option>
          <option value="paid">Paid</option>
          <option value="rejected">Rejected</option>
          <option value="">All</option>
        </select>
      }
    >
      <Alert kind="info">
        Approving submits the payout to the payment provider immediately and cannot be undone.
        Rejecting releases the hold back into the player's spendable balance.
      </Alert>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>When</th><th>Player</th><th style={{ textAlign: 'right' }}>Amount</th>
              <th>Method</th><th>Destination</th><th>Risk</th><th>Status</th><th />
            </tr>
          </thead>
          <tbody>
            {rows.map((w) => (
              <tr key={w.id}>
                <td className="tiny">{fmtDate(w.created_at)}</td>
                <td>
                  <div>{w.username}</div>
                  <div className="tiny muted">{w.email}</div>
                </td>
                <td style={{ textAlign: 'right' }}>
                  {fmtCents(w.amount)}
                  {w.fee > 0 && <div className="tiny muted">fee {fmtCents(w.fee)}</div>}
                </td>
                <td className="tiny">{w.method}</td>
                <td className="tiny mono">{w.destination}</td>
                <td>
                  <div className="row" style={{ gap: 4 }}>
                    <Badge status={w.kyc_status === 'verified' ? 'succeeded' : 'pending'}>KYC {w.kyc_status}</Badge>
                    {(w.risk || []).map((f) => (
                      <span key={f} className="badge badge-warn" title={f}>{f.replace(/_/g, ' ')}</span>
                    ))}
                  </div>
                  <div className="tiny muted">lifetime {fmtCents(w.lifetime_withdrawn)}</div>
                </td>
                <td><Badge status={w.status} /></td>
                <td>
                  {['requested', 'under_review'].includes(w.status) && (
                    <div className="row" style={{ gap: 6 }}>
                      <button className="btn btn-sm btn-ok" onClick={() => decide(w, true)}>Approve</button>
                      <button className="btn btn-sm btn-bad" onClick={() => decide(w, false)}>Reject</button>
                    </div>
                  )}
                  {w.rejection_reason && <div className="tiny muted">{w.rejection_reason}</div>}
                  {w.provider_ref && <div className="tiny mono">{w.provider_ref}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <Empty>Queue is clear.</Empty>}
      </div>
    </Card>
  )
}

/* -------------------------------------------------------------- deposits */
function Deposits({ onError }) {
  const [rows, setRows] = useState(null)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    (async () => {
      try {
        const res = await api.adminDeposits(filter || undefined)
        setRows(res.deposits)
      } catch (err) { onError(err.message) }
    })()
  }, [filter, onError])

  if (!rows) return <Loading />

  return (
    <Card title="Deposits" actions={
      <select value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: 200 }}>
        <option value="">All</option>
        <option value="succeeded">Succeeded</option>
        <option value="requires_action">Awaiting payment</option>
        <option value="failed">Failed</option>
        <option value="chargeback">Chargeback</option>
      </select>
    }>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>When</th><th>Player</th><th style={{ textAlign: 'right' }}>Amount</th><th>Method</th>
              <th>Provider</th><th>Reference</th><th>Confirmations</th><th>Status</th></tr>
          </thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.id}>
                <td className="tiny">{fmtDate(d.created_at)}</td>
                <td className="tiny">{d.username}</td>
                <td style={{ textAlign: 'right' }}>
                  {fmtCents(d.amount)}
                  {d.bonus_credited > 0 && <div className="tiny muted">+{fmtCents(d.bonus_credited)} bonus</div>}
                </td>
                <td className="tiny">{d.method}</td>
                <td className="tiny">{d.provider}</td>
                <td className="tiny mono">{d.reference}</td>
                <td className="tiny">{d.confirmations || '—'}</td>
                <td><Badge status={d.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <Empty>No deposits.</Empty>}
      </div>
    </Card>
  )
}

/* ----------------------------------------------------------------- users */
function Users({ onError, toast }) {
  const [rows, setRows] = useState(null)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [adjust, setAdjust] = useState({ amount: '', reason: '' })

  // The query arrives as an argument, not through the closure. Depending on the
  // `query` state here would re-create this callback on every keystroke, and the
  // effect below would fire a request per character typed into the search box.
  const load = useCallback(async (q = '') => {
    try {
      const res = await api.adminUsers(q || undefined)
      setRows(res.users)
    } catch (err) { onError(err.message) }
  }, [onError])
  useEffect(() => { load() }, [load])

  const open = async (id) => {
    setSelected(id)
    setDetail(null)
    try { setDetail(await api.adminUser(id)) } catch (err) { onError(err.message) }
  }

  const patch = async (body) => {
    try {
      await api.adminUpdateUser(selected, body)
      toast('Player updated', 'success')
      await open(selected)
      await load(query)
    } catch (err) { onError(err.message) }
  }

  const doAdjust = async () => {
    try {
      await api.adminAdjust(selected, adjust)
      toast('Balance adjusted and audited', 'success')
      setAdjust({ amount: '', reason: '' })
      await open(selected)
      await load(query)
    } catch (err) { onError(err.message) }
  }

  if (!rows) return <Loading />

  return (
    <>
      <Card
        title={`Players (${rows.length})`}
        actions={
          <div className="row">
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="email or username"
              style={{ width: 220 }} />
            <button className="btn btn-sm" onClick={() => load(query)}>Search</button>
          </div>
        }
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Player</th><th>Country</th><th>KYC</th><th>VIP</th>
                <th style={{ textAlign: 'right' }}>Cash</th><th style={{ textAlign: 'right' }}>Bonus</th>
                <th style={{ textAlign: 'right' }}>Wagered</th><th style={{ textAlign: 'right' }}>Net</th>
                <th /><th /></tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div>{u.username} {u.is_banned && <span className="badge badge-bad">banned</span>}</div>
                    <div className="tiny muted">{u.email}</div>
                  </td>
                  <td className="tiny">{u.country}</td>
                  <td><Badge status={u.kyc_status} /></td>
                  <td className="tiny">{u.vip_tier}</td>
                  <td style={{ textAlign: 'right' }}>{fmtCents(u.balances.cash)}</td>
                  <td style={{ textAlign: 'right' }} className="muted">{fmtCents(u.balances.bonus)}</td>
                  <td style={{ textAlign: 'right' }}>{fmtCents(u.wagered)}</td>
                  <td style={{ textAlign: 'right', color: u.net >= 0 ? 'var(--ok)' : 'var(--bad)' }}>
                    {fmtCents(u.net)}
                  </td>
                  <td className="tiny muted">{u.self_excluded_until ? 'self-excluded' : ''}</td>
                  <td><button className="btn btn-sm" onClick={() => open(u.id)}>Open</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <Empty>No players match.</Empty>}
        </div>
      </Card>

      {selected && (
        <Modal title="Player detail" onClose={() => { setSelected(null); setDetail(null) }}>
          {!detail ? <Loading /> : (
            <>
              <div className="grid grid-2">
                <div>
                  <h4 style={{ marginBottom: 6 }}>{detail.user.username}</h4>
                  <table><tbody>
                    <tr><td className="muted">Email</td><td>{detail.user.email}</td></tr>
                    <tr><td className="muted">Country</td><td>{detail.user.country}</td></tr>
                    <tr><td className="muted">Born</td><td>{String(detail.user.date_of_birth).slice(0, 10)}</td></tr>
                    <tr><td className="muted">Joined</td><td>{fmtDate(detail.user.created_at)}</td></tr>
                    <tr><td className="muted">Lifetime wagered</td><td>{fmtCents(detail.user.wagered_lifetime)}</td></tr>
                    <tr><td className="muted">Lifetime withdrawn</td><td>{fmtCents(detail.lifetime_withdrawn)}</td></tr>
                    <tr><td className="muted">Loss limit/day</td><td>{detail.user.loss_limit_daily ? fmtCents(detail.user.loss_limit_daily) : '—'}</td></tr>
                    <tr><td className="muted">Deposit limit/day</td><td>{detail.user.deposit_limit_daily ? fmtCents(detail.user.deposit_limit_daily) : '—'}</td></tr>
                  </tbody></table>
                </div>
                <div>
                  <h4 style={{ marginBottom: 6 }}>Risk flags</h4>
                  {detail.aml_flags.length === 0
                    ? <p className="small muted">No flags.</p>
                    : detail.aml_flags.map((f) => <div key={f} className="badge badge-warn" style={{ marginRight: 6, marginBottom: 6 }}>{f}</div>)}

                  <h4 style={{ margin: '16px 0 6px' }}>Manual balance adjustment</h4>
                  <div className="row" style={{ gap: 6 }}>
                    <input value={adjust.amount} placeholder="-10.00 or 10.00" style={{ width: 130 }}
                      onChange={(e) => setAdjust((a) => ({ ...a, amount: e.target.value }))} />
                    <input value={adjust.reason} placeholder="reason (audited)" style={{ flex: 1 }}
                      onChange={(e) => setAdjust((a) => ({ ...a, reason: e.target.value }))} />
                    <ActionButton className="btn btn-sm btn-primary" onClick={doAdjust}
                      disabled={!adjust.amount || adjust.reason.length < 3}>
                      Apply
                    </ActionButton>
                  </div>

                  <h4 style={{ margin: '16px 0 6px' }}>Controls</h4>
                  <div className="row" style={{ gap: 6 }}>
                    <ActionButton className="btn btn-sm" confirm
                      onClick={() => patch({ is_banned: !detail.user.is_banned })}>
                      {detail.user.is_banned ? 'Unban' : 'Ban'}
                    </ActionButton>
                    <ActionButton className="btn btn-sm" confirm
                      onClick={() => patch({ is_active: !detail.user.is_active })}>
                      {detail.user.is_active ? 'Deactivate' : 'Activate'}
                    </ActionButton>
                    <ActionButton className="btn btn-sm btn-ok" confirm
                      onClick={() => patch({ kyc_status: 'verified' })}>
                      Mark KYC verified
                    </ActionButton>
                    <ActionButton className="btn btn-sm" confirm
                      onClick={() => patch({ kyc_status: 'rejected' })}>
                      Reject KYC
                    </ActionButton>
                    <ActionButton className="btn btn-sm" confirm
                      onClick={() => patch({ role: 'admin' })}>
                      Make admin
                    </ActionButton>
                  </div>
                </div>
              </div>

              <h4 style={{ margin: '18px 0 6px' }}>Recent ledger activity</h4>
              <div className="table-wrap" style={{ maxHeight: 220, overflowY: 'auto' }}>
                <table>
                  <thead><tr><th>When</th><th>Type</th><th>Account</th><th style={{ textAlign: 'right' }}>Amount</th></tr></thead>
                  <tbody>
                    {detail.ledger.map((e) => (
                      <tr key={e.id}>
                        <td className="tiny">{fmtDate(e.created_at)}</td>
                        <td className="tiny">{e.type}</td>
                        <td className="tiny">{e.account}</td>
                        <td style={{ textAlign: 'right' }}>{fmtCents(e.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <h4 style={{ margin: '18px 0 6px' }}>Withdrawals</h4>
              <div className="table-wrap" style={{ maxHeight: 180, overflowY: 'auto' }}>
                <table>
                  <thead><tr><th>When</th><th style={{ textAlign: 'right' }}>Amount</th><th>Method</th><th>Status</th></tr></thead>
                  <tbody>
                    {detail.withdrawals.map((w) => (
                      <tr key={w.id}>
                        <td className="tiny">{fmtDate(w.created_at)}</td>
                        <td style={{ textAlign: 'right' }}>{fmtCents(w.amount)}</td>
                        <td className="tiny">{w.method}</td>
                        <td><Badge status={w.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Modal>
      )}
    </>
  )
}

/* --------------------------------------------------------------- bonuses */
function Bonuses({ onError, toast }) {
  const [codes, setCodes] = useState(null)
  const [form, setForm] = useState({
    user_id: '', amount: '10.00', code: 'MANUAL', wager_multiplier: '30', reason: '',
  })
  const [newCode, setNewCode] = useState({
    code: '', bonus_type: 'percent', value: '50', max_amount: '50.00',
    wager_multiplier: '30', uses_left: '100', min_deposit: '10.00',
  })

  const load = useCallback(async () => {
    try { setCodes((await api.adminBonusCodes()).codes) } catch (err) { onError(err.message) }
  }, [onError])
  useEffect(() => { load() }, [load])

  const grant = async () => {
    try {
      await api.adminGrantBonus(form)
      toast('Bonus granted', 'success')
      setForm((f) => ({ ...f, user_id: '', reason: '' }))
    } catch (err) { onError(err.message) }
  }

  const create = async () => {
    try {
      await api.adminCreateCode(newCode)
      toast('Bonus code created', 'success')
      setNewCode((c) => ({ ...c, code: '' }))
      await load()
    } catch (err) { onError(err.message) }
  }

  return (
    <div className="grid grid-2" style={{ alignItems: 'start' }}>
      <Card title="Grant a manual bonus">
        <div className="field">
          <label>Player ID</label>
          <input value={form.user_id} onChange={(e) => setForm((f) => ({ ...f, user_id: e.target.value }))}
            placeholder="paste from the Players tab" />
          <span className="tiny muted">Bonuses are audited with your admin id and the reason you give.</span>
        </div>
        <div className="row" style={{ gap: 10 }}>
          <div className="field" style={{ flex: 1 }}>
            <label>Amount (USD)</label>
            <input value={form.amount} onChange={(e) => setForm((f) => ({ ...f, amount: e.target.value }))} />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>Wager multiplier</label>
            <input value={form.wager_multiplier}
              onChange={(e) => setForm((f) => ({ ...f, wager_multiplier: e.target.value }))} />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>Code</label>
            <input value={form.code} onChange={(e) => setForm((f) => ({ ...f, code: e.target.value.toUpperCase() }))} />
          </div>
        </div>
        <div className="field">
          <label>Reason (audited)</label>
          <input value={form.reason} onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
            placeholder="e.g. goodwill for a support ticket" />
        </div>
        <button className="btn btn-primary" onClick={grant}
          disabled={!form.user_id || form.amount <= 0 || form.reason.length < 3}>
          Grant bonus
        </button>
      </Card>

      <Card title="Promo codes">
        {!codes ? <Loading /> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Code</th><th>Type</th><th>Value</th><th>Cap</th><th>Wager</th><th>Uses left</th></tr></thead>
              <tbody>
                {codes.map((c) => (
                  <tr key={c.id}>
                    <td className="mono">{c.code}</td>
                    <td className="tiny">{c.type}</td>
                    <td>{c.type === 'percent' ? `${c.value}%` : fmtCents(c.value)}</td>
                    <td>{c.max_amount ? fmtCents(c.max_amount) : '—'}</td>
                    <td>{c.wager_multiplier}x</td>
                    <td>{c.uses_left}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {codes.length === 0 && <Empty>No codes yet.</Empty>}
          </div>
        )}

        <h4 style={{ marginTop: 18 }}>Create a code</h4>
        <div className="row" style={{ gap: 8 }}>
          <input value={newCode.code} placeholder="CODE"
            onChange={(e) => setNewCode((c) => ({ ...c, code: e.target.value.toUpperCase() }))} style={{ width: 130 }} />
          <select value={newCode.bonus_type} style={{ width: 110 }}
            onChange={(e) => setNewCode((c) => ({ ...c, bonus_type: e.target.value }))}>
            <option value="percent">percent</option>
            <option value="fixed">fixed</option>
          </select>
          <input value={newCode.value} placeholder="value"
            onChange={(e) => setNewCode((c) => ({ ...c, value: e.target.value }))} style={{ width: 90 }} />
          <input value={newCode.max_amount} placeholder="cap"
            onChange={(e) => setNewCode((c) => ({ ...c, max_amount: e.target.value }))} style={{ width: 90 }} />
          <input value={newCode.uses_left} placeholder="uses"
            onChange={(e) => setNewCode((c) => ({ ...c, uses_left: e.target.value }))} style={{ width: 80 }} />
          <button className="btn btn-sm btn-primary" onClick={create} disabled={!newCode.code}>Create</button>
        </div>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------ risk */
function Risk() {
  const [queue, setQueue] = useState(null)
  const [wins, setWins] = useState([])

  useEffect(() => {
    (async () => {
      const [q, w] = await Promise.all([api.adminAml(), api.adminBigWins()])
      setQueue(q.queue)
      setWins(w.wins)
    })()
  }, [])

  if (!queue) return <Loading />

  return (
    <div className="grid grid-2" style={{ alignItems: 'start' }}>
      <Card title="AML review queue">
        <p className="tiny muted">Ranked by number of risk flags and size. A human decides every case.</p>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Player</th><th style={{ textAlign: 'right' }}>Amount</th><th>Flags</th><th>Priority</th></tr></thead>
            <tbody>
              {queue.map((r) => (
                <tr key={r.id}>
                  <td>{r.username}</td>
                  <td style={{ textAlign: 'right' }}>{fmtCents(r.amount)}</td>
                  <td>{r.flags.map((f) => <span key={f} className="badge badge-warn" style={{ marginRight: 4 }}>{f.replace(/_/g,' ')}</span>)}</td>
                  <td><span className="badge badge-bad">{r.priority}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          {queue.length === 0 && <Empty>Nothing awaiting review.</Empty>}
        </div>
      </Card>

      <Card title="Largest wins">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Player</th><th>Game</th><th style={{ textAlign: 'right' }}>Stake</th><th style={{ textAlign: 'right' }}>Payout</th><th style={{ textAlign: 'right' }}>Multiplier</th></tr></thead>
            <tbody>
              {wins.map((w, i) => (
                <tr key={i}>
                  <td>{w.username}</td>
                  <td>{w.game}</td>
                  <td style={{ textAlign: 'right' }}>{fmtCents(w.stake)}</td>
                  <td style={{ textAlign: 'right' }}>{fmtCents(w.payout)}</td>
                  <td style={{ textAlign: 'right' }}>{fmtMultiplier(w.multiplier)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}

/* ---------------------------------------------------------------- ledger */
function Ledger({ onError }) {
  const [rows, setRows] = useState(null)
  useEffect(() => {
    api.adminLedger().then((r) => setRows(r.transactions)).catch((e) => onError(e.message))
  }, [onError])
  if (!rows) return <Loading />

  return (
    <Card title="Ledger explorer">
      <Alert kind="info">
        Every transaction below must sum to zero across its entries. A non-zero sum is a bug —
        the books are the source of truth for player balances.
      </Alert>
      <div className="table-wrap" style={{ maxHeight: 620, overflowY: 'auto' }}>
        <table>
          <thead><tr><th>When</th><th>Type</th><th>Reference</th><th>Entries</th><th style={{ textAlign: 'right' }}>Sum</th></tr></thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id}>
                <td className="tiny">{fmtDate(t.created_at)}</td>
                <td className="tiny">{t.type}</td>
                <td className="tiny mono">{t.reference}</td>
                <td className="tiny">
                  {t.entries.map((e, i) => (
                    <div key={i}>
                      {e.account} <strong>{fmtCents(e.amount)}</strong>
                    </div>
                  ))}
                </td>
                <td style={{ textAlign: 'right' }} className={t.sum === 0 ? 'badge badge-ok' : 'badge badge-bad'}>
                  {fmtCents(t.sum)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

/* ----------------------------------------------------------------- audit */
function Audit({ onError }) {
  const [rows, setRows] = useState(null)
  useEffect(() => {
    api.adminAudit().then((r) => setRows(r.entries)).catch((e) => onError(e.message))
  }, [onError])
  if (!rows) return <Loading />

  return (
    <Card title="Audit log">
      <p className="tiny muted">Append-only record of every privileged action.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Before</th><th>After</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="tiny">{fmtDate(r.created_at)}</td>
                <td className="tiny">{r.actor}</td>
                <td className="tiny">{r.action}</td>
                <td className="tiny mono">{r.target}</td>
                <td className="tiny mono">{(JSON.stringify(r.before) || '').slice(0, 70)}</td>
                <td className="tiny mono">{(JSON.stringify(r.after) || '').slice(0, 70)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <Empty>No privileged actions recorded yet.</Empty>}
      </div>
    </Card>
  )
}

/* ---------------------------------------------------------------- system */
function System({ onError }) {
  const [health, setHealth] = useState(null)
  const [hooks, setHooks] = useState([])
  const [sessions, setSessions] = useState([])

  useEffect(() => {
    (async () => {
      try {
        const [h, w, s] = await Promise.all([api.adminHealth(), api.adminWebhooks(), api.adminSessions()])
        setHealth(h)
        setHooks(w.webhooks)
        setSessions(s.sessions)
      } catch (err) { onError(err.message) }
    })()
  }, [onError])

  if (!health) return <Loading />

  return (
    <>
      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Card className="tight"><Stat label="Environment" value={health.environment} /></Card>
        <Card className="tight"><Stat label="Ledger" value={health.ledger.balanced ? 'Balanced' : 'BROKEN'} /></Card>
        <Card className="tight"><Stat label="Pending payouts" value={health.pending_withdrawals} /></Card>
        <Card className="tight"><Stat label="Crash sockets" value={health.crash_clients} /></Card>
      </div>

      <Card title="Account balances (system)">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Account</th><th style={{ textAlign: 'right' }}>Balance</th><th>Meaning</th></tr></thead>
            <tbody>
              {Object.entries(health.ledger.accounts).map(([account, amount]) => (
                <tr key={account}>
                  <td className="mono">{account}</td>
                  <td style={{ textAlign: 'right' }}>{fmtCents(amount)}</td>
                  <td className="tiny muted">
                    {account === 'house_revenue' && 'Gross gaming revenue retained'}
                    {account === 'payment_clearing' && 'Negative = player money currently held (net deposits − payouts)'}
                    {account === 'bonus_pool' && 'Promotional budget paid out (negative = granted)'}
                    {account === 'fee_income' && 'Withdrawal fees collected'}
                    {account === 'chargeback_loss' && 'Reversed payments absorbed'}
                    {account === 'rakeback_pool' && 'Rakeback paid'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid grid-2" style={{ marginTop: 16, alignItems: 'start' }}>
        <Card title={`Webhooks (${hooks.length})`}>
          <div className="table-wrap" style={{ maxHeight: 340, overflowY: 'auto' }}>
            <table>
              <thead><tr><th>When</th><th>Provider</th><th>Event</th><th>Signature</th><th>Processed</th></tr></thead>
              <tbody>
                {hooks.map((h) => (
                  <tr key={h.id}>
                    <td className="tiny">{fmtDate(h.created_at)}</td>
                    <td className="tiny">{h.provider}</td>
                    <td className="tiny">{h.event_type}</td>
                    <td><Badge status={h.signature_valid ? 'succeeded' : 'failed'}>{String(h.signature_valid)}</Badge></td>
                    <td>
                      <Badge status={h.processed ? 'succeeded' : 'pending'}>{String(h.processed)}</Badge>
                      {h.error && <div className="tiny" style={{ color: 'var(--bad)' }}>{h.error}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {hooks.length === 0 && <Empty>No provider callbacks received yet.</Empty>}
          </div>
        </Card>

        <Card title="Recent sessions / security events">
          <div className="table-wrap" style={{ maxHeight: 340, overflowY: 'auto' }}>
            <table>
              <thead><tr><th>When</th><th>Action</th><th>User</th><th>IP</th></tr></thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id}>
                    <td className="tiny">{fmtDate(s.created_at)}</td>
                    <td className="tiny">{s.action}</td>
                    <td className="tiny mono">{String(s.user_id || '').slice(0, 10)}</td>
                    <td className="tiny">{s.ip}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </>
  )
}
