import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { fmtCents, fmtDate } from '../lib/format.js'
import { Alert, Badge, Card, Copyable, Empty, Loading, Tabs } from '../components/ui.jsx'
import { useStore } from '../lib/store.jsx'

export default function Account() {
  const { user, refreshUser, toast } = useStore()
  const [tab, setTab] = useState('profile')
  const [limits, setLimits] = useState(null)
  const [seeds, setSeeds] = useState(null)
  const [kyc, setKyc] = useState(null)
  const [notifications, setNotifications] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const [limitForm, setLimitForm] = useState({ loss_limit_daily: '', deposit_limit_daily: '' })
  const [exclude, setExclude] = useState({ days: '7', hours: '' })
  const [kycForm, setKycForm] = useState({ full_name: '', doc_type: 'passport', file_ref: '' })
  const [pwd, setPwd] = useState({ current_password: '', new_password: '' })
  const [rotation, setRotation] = useState(null)
  const [clientSeed, setClientSeed] = useState('')
  const [verify, setVerify] = useState({ round: '', result: null, error: '' })

  const load = async () => {
    try {
      const [l, s, k, n] = await Promise.all([
        api.limits(), api.seeds(), api.kyc(), api.notifications(),
      ])
      setLimits(l)
      setSeeds(s)
      setKyc(k)
      setNotifications(n)
      setClientSeed(s.client_seed)
      setLimitForm({
        loss_limit_daily: l.loss_limit_daily ? (l.loss_limit_daily / 100).toFixed(2) : '',
        deposit_limit_daily: l.deposit_limit_daily ? (l.deposit_limit_daily / 100).toFixed(2) : '',
      })
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => { load() }, [])

  const saveLimits = async () => {
    setBusy(true); setError('')
    try {
      await api.setLimits({
        loss_limit_daily: limitForm.loss_limit_daily || null,
        deposit_limit_daily: limitForm.deposit_limit_daily || null,
      })
      toast('Limits updated', 'success')
      await load()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const applyExclusion = async () => {
    setBusy(true); setError('')
    try {
      const body = exclude.days
        ? { self_exclude_days: Number(exclude.days) }
        : { cool_off_hours: Number(exclude.hours) }
      await api.setLimits(body)
      toast('Applied. This cannot be undone early.', 'success')
      await Promise.all([load(), refreshUser()])
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const submitKyc = async (e) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      await api.submitKyc(kycForm)
      toast('Documents submitted for review', 'success')
      await Promise.all([load(), refreshUser()])
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const rotate = async () => {
    setBusy(true); setError('')
    try {
      const res = await api.rotateSeeds({ client_seed: clientSeed })
      setRotation(res)
      toast('Server seed revealed and rotated', 'success')
      await load()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const changePassword = async (e) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      await api.changePassword(pwd)
      setPwd({ current_password: '', new_password: '' })
      toast('Password changed', 'success')
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const verifyRound = async () => {
    setVerify({ ...verify, error: '', result: null })
    try {
      const res = await api.crashVerify(verify.round)
      setVerify((v) => ({ ...v, result: res }))
    } catch (err) {
      setVerify((v) => ({ ...v, error: err.message }))
    }
  }

  if (!user || !limits) return <div className="page"><Loading /></div>

  const excluded = user.self_excluded_until && new Date(user.self_excluded_until) > new Date()
  const cooling = user.cool_off_until && new Date(user.cool_off_until) > new Date()

  return (
    <div className="page">
      <h1 style={{ marginTop: 0 }}>Account</h1>
      {error && <Alert kind="error">{error}</Alert>}
      {excluded && (
        <Alert kind="error">
          Self-exclusion active until {fmtDate(user.self_excluded_until)}. Deposits and play are blocked.
        </Alert>
      )}
      {cooling && (
        <Alert kind="warn">Cool-off active until {fmtDate(user.cool_off_until)}.</Alert>
      )}

      <Tabs
        active={tab}
        onChange={setTab}
        tabs={[
          { id: 'profile', label: 'Profile' },
          { id: 'wallet_limits', label: 'Responsible gambling' },
          { id: 'kyc', label: 'Verification' },
          { id: 'fair', label: 'Provably fair' },
          { id: 'security', label: 'Security' },
          { id: 'inbox', label: `Notifications${notifications.filter((n) => !n.read).length ? ` (${notifications.filter((n) => !n.read).length})` : ''}` },
        ]}
      />

      {/* --------------------------------------------------- profile */}
      {tab === 'profile' && (
        <div className="grid grid-2" style={{ alignItems: 'start' }}>
          <Card title="Your details">
            <div className="table-wrap">
              <table>
                <tbody>
                  <tr><td className="muted">Username</td><td>{user.username}</td></tr>
                  <tr><td className="muted">Email</td><td>{user.email}
                    {user.email_verified ? <span className="badge badge-ok" style={{ marginLeft: 8 }}>verified</span>
                      : <span className="badge badge-warn" style={{ marginLeft: 8 }}>unverified</span>}</td></tr>
                  <tr><td className="muted">Country</td><td>{user.country}</td></tr>
                  <tr><td className="muted">KYC</td><td><Badge status={user.kyc_status} /></td></tr>
                  <tr><td className="muted">VIP</td><td>{user.vip?.name} · {user.vip?.rakeback_pct}% rakeback</td></tr>
                  <tr><td className="muted">Member since</td><td>{fmtDate(user.created_at)}</td></tr>
                </tbody>
              </table>
            </div>
            {user.vip?.next_name && (
              <div style={{ marginTop: 14 }}>
                <div className="row between tiny muted">
                  <span>Progress to {user.vip.next_name}</span>
                  <span>{fmtCents(user.vip.wagered_lifetime)} / {fmtCents(user.vip.next_at)}</span>
                </div>
                <div style={{ height: 8, background: 'var(--panel-2)', borderRadius: 6, marginTop: 6, overflow: 'hidden' }}>
                  <div style={{ width: `${user.vip.progress_pct}%`, height: '100%', background: 'linear-gradient(90deg, var(--accent-2), var(--accent))' }} />
                </div>
              </div>
            )}
          </Card>

          <Card title="Lifetime play">
            <div className="grid grid-2">
              <div className="stat"><span className="label">Total wagered</span><span className="value">{fmtCents(user.stats.wagered)}</span></div>
              <div className="stat"><span className="label">Net result</span>
                <span className="value" style={{ color: user.stats.net >= 0 ? 'var(--ok)' : 'var(--bad)' }}>
                  {fmtCents(user.stats.net)}
                </span></div>
              <div className="stat"><span className="label">Bets placed</span><span className="value">{user.stats.bets}</span></div>
              <div className="stat"><span className="label">Biggest win</span><span className="value">{fmtCents(user.stats.biggest_win)}</span></div>
              <div className="stat"><span className="label">Actual RTP</span><span className="value">{user.stats.rtp_actual}%</span></div>
              <div className="stat"><span className="label">Bonus wager left</span><span className="value">{fmtCents(user.balances.pending_wager)}</span></div>
            </div>
            <p className="tiny muted" style={{ marginTop: 12 }}>
              Your actual RTP drifts around the published figure over small samples; with enough
              hands it converges on the game's stated RTP.
            </p>
          </Card>
        </div>
      )}

      {/* ----------------------------------------- responsible gambling */}
      {tab === 'wallet_limits' && (
        <div className="grid grid-2" style={{ alignItems: 'start' }}>
          <Card title="Daily limits">
            <div className="field">
              <label>Daily loss limit (USD)</label>
              <input inputMode="decimal" value={limitForm.loss_limit_daily}
                onChange={(e) => setLimitForm((f) => ({ ...f, loss_limit_daily: e.target.value.replace(/[^0-9.]/g, '') }))}
                placeholder="No limit" />
              <span className="tiny muted">Betting is blocked once your net losses for the UTC day reach this.</span>
            </div>
            <div className="field">
              <label>Daily deposit limit (USD)</label>
              <input inputMode="decimal" value={limitForm.deposit_limit_daily}
                onChange={(e) => setLimitForm((f) => ({ ...f, deposit_limit_daily: e.target.value.replace(/[^0-9.]/g, '') }))}
                placeholder="No limit" />
            </div>
            <button className="btn btn-primary" onClick={saveLimits} disabled={busy}>Save limits</button>
            <p className="tiny muted" style={{ marginTop: 10 }}>
              Limits take effect immediately. Increasing a limit is deliberately restricted — contact
              support, and the change only applies after a cooling-off period.
            </p>

            <div className="alert alert-info" style={{ marginTop: 12 }}>
              <strong>Today</strong>
              <div className="tiny">
                Deposited {fmtCents(limits.today.deposits)} · lost {fmtCents(limits.today.losses)} · wagered {fmtCents(limits.today.wagered)}
              </div>
            </div>
          </Card>

          <Card title="Time out or self-exclude">
            <Alert kind="warn">
              Self-exclusion cannot be reversed early, by you or by support. It blocks deposits and all
              game play for the period you choose.
            </Alert>
            <div className="field">
              <label>Cool-off</label>
              <div className="row">
                {['24', '72'].map((h) => (
                  <button key={h} className={`btn btn-sm ${exclude.hours === h ? 'btn-primary' : ''}`}
                    onClick={() => setExclude({ days: '', hours: h })}>
                    {h} hours
                  </button>
                ))}
                <button className={`btn btn-sm ${exclude.hours === '168' ? 'btn-primary' : ''}`}
                  onClick={() => setExclude({ days: '', hours: '168' })}>
                  7 days
                </button>
              </div>
            </div>
            <div className="field">
              <label>Self-exclusion</label>
              <div className="row">
                {['1', '7', '30', '180'].map((d) => (
                  <button key={d} className={`btn btn-sm ${exclude.days === d ? 'btn-primary' : ''}`}
                    onClick={() => setExclude({ days: d, hours: '' })}>
                    {d} day{d === '1' ? '' : 's'}
                  </button>
                ))}
              </div>
            </div>
            <button className="btn btn-bad" onClick={applyExclusion}
              disabled={busy || (!exclude.days && !exclude.hours)}>
              Apply to my account
            </button>

            <p className="tiny muted" style={{ marginTop: 12 }}>
              Need to talk to someone? Contact your local gambling support service. This platform
              must publish local helpline numbers before going live.
            </p>
          </Card>
        </div>
      )}

      {/* ------------------------------------------------------ KYC */}
      {tab === 'kyc' && (
        <div className="grid grid-2" style={{ alignItems: 'start' }}>
          <Card title="Identity verification">
            <p className="small muted">
              Required before your cumulative withdrawals pass {fmtCents(limits.kyc_required_above)}.
              This is an AML/CTF requirement, not a marketing step.
            </p>
            {kyc?.status === 'verified' ? (
              <Alert kind="ok">Your identity is verified.</Alert>
            ) : (
              <form onSubmit={submitKyc}>
                <div className="field">
                  <label>Full name (as on the document)</label>
                  <input value={kycForm.full_name} required
                    onChange={(e) => setKycForm((f) => ({ ...f, full_name: e.target.value }))} />
                </div>
                <div className="field">
                  <label>Document type</label>
                  <select value={kycForm.doc_type}
                    onChange={(e) => setKycForm((f) => ({ ...f, doc_type: e.target.value }))}>
                    <option value="passport">Passport</option>
                    <option value="national_id">National ID</option>
                    <option value="drivers_license">Driving licence</option>
                    <option value="proof_of_address">Proof of address</option>
                    <option value="selfie">Selfie holding ID</option>
                  </select>
                </div>
                <div className="field">
                  <label>Document reference</label>
                  <input value={kycForm.file_ref} required placeholder="storage key or upload reference"
                    onChange={(e) => setKycForm((f) => ({ ...f, file_ref: e.target.value }))} />
                  <span className="tiny muted">
                    This build stores a reference only. Wire it to a KYC provider or encrypted object
                    store before going live — never send documents through this form in production.
                  </span>
                </div>
                <button className="btn btn-primary" disabled={busy}>Submit for review</button>
              </form>
            )}
          </Card>

          <Card title="Submitted documents">
            {(kyc?.documents || []).length === 0 && <Empty>No documents submitted.</Empty>}
            {(kyc?.documents || []).map((d) => (
              <div key={d.id} className="row between" style={{ padding: '8px 0', borderBottom: '1px solid var(--line)' }}>
                <div>
                  <div className="small">{d.doc_type}</div>
                  <div className="tiny muted">{fmtDate(d.created_at)}</div>
                </div>
                <Badge status={d.status} />
              </div>
            ))}
          </Card>
        </div>
      )}

      {/* -------------------------------------------- provably fair */}
      {tab === 'fair' && (
        <div className="grid grid-2" style={{ alignItems: 'start' }}>
          <Card title="Your seeds">
            <p className="small muted">
              Your outcomes come from HMAC-SHA256(server_seed, client_seed:nonce). The server commits
              to its seed by publishing the hash below before you play. Rotating reveals the old seed
              so you can recompute every past result.
            </p>
            <div className="field">
              <label>Current server seed hash (commitment)</label>
              <Copyable value={seeds?.server_seed_hash || ''} />
            </div>
            <div className="field">
              <label>Client seed (yours to change)</label>
              <input value={clientSeed} onChange={(e) => setClientSeed(e.target.value)} />
            </div>
            <div className="row">
              <button className="btn btn-primary" onClick={rotate} disabled={busy || !clientSeed}>
                Rotate & reveal server seed
              </button>
              <span className="tiny muted">Nonce resets to 0 on rotation.</span>
            </div>

            {rotation && (
              <div className="alert alert-ok" style={{ marginTop: 14 }}>
                <strong>Previous server seed revealed</strong>
                <Copyable value={rotation.revealed.server_seed} />
                <div className="tiny">
                  Used for {rotation.revealed.nonce_reached} nonces with client seed{' '}
                  <span className="mono">{seeds?.client_seed}</span>
                </div>
              </div>
            )}
          </Card>

          <Card title="Verify a crash round">
            <p className="small muted">
              Crash reveals its seed with every round. Enter a round number to recompute the bust
              point yourself.
            </p>
            <div className="field">
              <label>Round number</label>
              <div className="row">
                <input inputMode="numeric" value={verify.round}
                  onChange={(e) => setVerify((v) => ({ ...v, round: e.target.value.replace(/[^0-9]/g, '') }))} />
                <button className="btn" onClick={verifyRound} disabled={!verify.round}>Verify</button>
              </div>
            </div>
            {verify.error && <Alert kind="error">{verify.error}</Alert>}
            {verify.result && (
              <div className={`alert ${verify.result.honest ? 'alert-ok' : 'alert-error'}`}>
                <div><strong>Round {verify.result.round_number}</strong></div>
                <div className="tiny">Server seed <span className="mono">{verify.result.server_seed.slice(0, 32)}…</span></div>
                <div className="tiny">Seed hash matches: {String(verify.result.seed_hash_matches)}</div>
                <div className="small">
                  Reported {Number(verify.result.reported_crash_point).toFixed(2)}x ·
                  recomputed {Number(verify.result.recomputed_crash_point).toFixed(2)}x
                </div>
                <div className="small"><strong>{verify.result.honest ? 'Verified honest' : 'MISMATCH — contact support'}</strong></div>
              </div>
            )}

            {rotation?.previous?.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <h4 className="small">Revealed seed history</h4>
                <div className="table-wrap" style={{ maxHeight: 220, overflowY: 'auto' }}>
                  <table>
                    <thead><tr><th>Hash</th><th>Nonces</th></tr></thead>
                    <tbody>
                      {rotation.previous.map((p, i) => (
                        <tr key={i}>
                          <td className="mono tiny">{p.server_seed_hash.slice(0, 24)}…</td>
                          <td className="tiny">{p.nonce_reached}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* ------------------------------------------------ security */}
      {tab === 'security' && (
        <Card title="Change password" className="page narrow" style={{ maxWidth: 460 }}>
          <form onSubmit={changePassword}>
            <div className="field">
              <label>Current password</label>
              <input type="password" value={pwd.current_password} required
                onChange={(e) => setPwd((p) => ({ ...p, current_password: e.target.value }))} />
            </div>
            <div className="field">
              <label>New password</label>
              <input type="password" value={pwd.new_password} required minLength={8}
                onChange={(e) => setPwd((p) => ({ ...p, new_password: e.target.value }))} />
            </div>
            <button className="btn btn-primary" disabled={busy}>Change password</button>
            <p className="tiny muted" style={{ marginTop: 10 }}>
              Changing your password signs out every other session.
            </p>
          </form>
        </Card>
      )}

      {/* ------------------------------------------------- inbox */}
      {tab === 'inbox' && (
        <Card title="Notifications" actions={
          notifications.some((n) => !n.read) ? (
            <button className="btn btn-sm" onClick={async () => { await api.markRead(); await load() }}>
              Mark all read
            </button>
          ) : null
        }>
          {notifications.length === 0 && <Empty>Nothing yet.</Empty>}
          {notifications.map((n) => (
            <div key={n.id} style={{ padding: '10px 0', borderBottom: '1px solid var(--line)' }}>
              <div className="row between">
                <strong className="small">{n.title}</strong>
                <span className="tiny muted">{fmtDate(n.created_at)}</span>
              </div>
              <div className="small muted">{n.body}</div>
            </div>
          ))}
        </Card>
      )}
    </div>
  )
}
