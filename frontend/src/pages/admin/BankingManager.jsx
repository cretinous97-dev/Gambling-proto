/**
 * Banking Method Manager.
 *
 * The panel an operator uses to add a bank, wallet or acquirer and have it
 * live for the markets it names - no deploy, no restart, no developer. It is
 * the front end of `banking_methods`, and `services/routing.py` is the code
 * that reads the same rows when a player pays.
 *
 * Three things this screen is deliberate about:
 *
 * 1. **It never handles a credential.** The credential field takes the NAME of
 *    an environment variable (`MBOB_API_KEY`), never the key. The panel then
 *    reports whether that variable is actually present in the running process,
 *    which is the only fact an operator needs and the one thing that cannot be
 *    discovered from the database.
 *
 * 2. **Specificity is visible.** Rows are listed in the order routing resolves
 *    them, with the wildcard rows at the bottom, because "why did this player
 *    get that bank" is answered by reading this list top to bottom.
 *
 * 3. **Off is not deleted.** Deactivating keeps the row forever; settled
 *    transactions point at it, and a reconciliation report with holes in it is
 *    worse than no report.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'

import { Badge, Card, Empty, Loading, Modal } from '../../components/ui.jsx'
import { api } from '../../lib/api.js'

const EMPTY_FORM = {
  name: '',
  country_code: '*',
  currency: '*',
  account_id: '',
  api_endpoint: '',
  credential_env: '',
  provider: '',
  method: 'bank_transfer',
  deposits_enabled: true,
  withdrawals_enabled: false,
  active: true,
  priority: 100,
  min_amount_minor: 0,
  max_amount_minor: 0,
  fee_bps: 0,
  notes: '',
}

const PROVIDER_LABELS = {
  '': 'Deployment default (PAYMENT_PROVIDER)',
  adyen: 'Adyen',
  stripe: 'Stripe',
  cryptopay: 'CryptoPay',
  bank_transfer: 'Direct bank / wallet rail',
  sandbox: 'Sandbox (simulation only)',
}

/** Minor units to a readable decimal, without ever using a float for money. */
function money(minor, currency) {
  if (minor === 0 || minor === null || minor === undefined) return 'no limit'
  const value = (minor / 100).toFixed(2)
  return currency && currency !== '*' ? `${value} ${currency}` : value
}

export default function BankingManager({ onError, toast }) {
  const [rows, setRows] = useState(null)
  const [meta, setMeta] = useState({ providers: [], method_kinds: [] })
  const [form, setForm] = useState(null)      // null = closed; {} = creating
  const [editing, setEditing] = useState(null)
  const [saving, setSaving] = useState(false)
  const [filter, setFilter] = useState({ country: '', currency: '' })
  const [explain, setExplain] = useState(null)

  const load = useCallback(async () => {
    try {
      const data = await api.bankingMethods(filter)
      setRows(data.methods)
      setMeta({ providers: data.providers, method_kinds: data.method_kinds })
    } catch (err) {
      onError(err.message)
      setRows([])
    }
  }, [filter, onError])

  useEffect(() => {
    load()
  }, [load])

  const openCreate = () => {
    setForm({ ...EMPTY_FORM })
    setEditing(null)
  }
  const openEdit = (row) => {
    setForm({
      name: row.name,
      country_code: row.country_code,
      currency: row.currency,
      account_id: row.account_id || '',
      api_endpoint: row.api_endpoint || '',
      credential_env: row.credential_env || '',
      provider: row.provider || '',
      method: row.method,
      deposits_enabled: row.deposits_enabled,
      withdrawals_enabled: row.withdrawals_enabled,
      active: row.active,
      priority: row.priority,
      min_amount_minor: row.min_amount_minor,
      max_amount_minor: row.max_amount_minor,
      fee_bps: row.fee_bps,
      notes: row.notes || '',
    })
    setEditing(row)
  }

  const save = async (event) => {
    event.preventDefault()
    setSaving(true)
    onError('')
    try {
      const payload = {
        ...form,
        priority: Number(form.priority) || 0,
        min_amount_minor: Number(form.min_amount_minor) || 0,
        max_amount_minor: Number(form.max_amount_minor) || 0,
        fee_bps: Number(form.fee_bps) || 0,
      }
      if (editing) {
        await api.updateBankingMethod(editing.id, payload)
        toast?.(`${payload.name} updated`, 'success')
      } else {
        await api.createBankingMethod(payload)
        toast?.(`${payload.name} is live for its markets`, 'success')
      }
      setForm(null)
      setEditing(null)
      await load()
    } catch (err) {
      // The server's validation messages say what to fix; show them verbatim
      // rather than replacing them with something generic.
      onError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (row) => {
    onError('')
    try {
      const updated = await api.toggleBankingMethod(row.id)
      toast?.(`${row.name} ${updated.active ? 'enabled' : 'disabled'}`, 'success')
      await load()
    } catch (err) {
      onError(err.message)
    }
  }

  const retire = async (row) => {
    onError('')
    try {
      await api.retireBankingMethod(row.id)
      toast?.(`${row.name} retired`, 'success')
      await load()
    } catch (err) {
      onError(err.message)
    }
  }

  const explainFor = async (row) => {
    onError('')
    try {
      const data = await api.explainRouting({
        country: row.country_code === '*' ? 'BT' : row.country_code,
        currency: row.currency === '*' ? 'BTN' : row.currency,
        amount_minor: row.min_amount_minor || 10_000,
        direction: row.deposits_enabled ? 'deposit' : 'withdrawal',
      })
      setExplain({ row, data })
    } catch (err) {
      onError(err.message)
    }
  }

  const summary = useMemo(() => {
    if (!rows?.length) return null
    return {
      total: rows.length,
      live: rows.filter((r) => r.active).length,
      missingKey: rows.filter((r) => r.active && !r.credential_present).length,
      deposits: rows.filter((r) => r.active && r.deposits_enabled).length,
      payouts: rows.filter((r) => r.active && r.withdrawals_enabled).length,
    }
  }, [rows])

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 style={{ margin: 0 }}>Banking methods</h2>
          <p className="text-sm text-muted" style={{ margin: '4px 0 0' }}>
            Add a bank, wallet or acquirer and it is live for the countries and
            currencies it names. No deploy required.
          </p>
        </div>
        <button type="button" className="btn btn-primary" onClick={openCreate}>
          Add payment method
        </button>
      </div>

      {summary && (
        <div className="grid gap-3 sm:grid-cols-4" style={{ margin: '16px 0' }}>
          <Metric label="Configured" value={summary.total} hint={`${summary.live} active`} />
          <Metric label="Taking deposits" value={summary.deposits} />
          <Metric label="Paying out" value={summary.payouts} />
          <Metric
            label="Missing credentials"
            value={summary.missingKey}
            tone={summary.missingKey ? 'warn' : 'default'}
            hint={
              summary.missingKey
                ? 'active, but the environment variable is not set'
                : 'every active rail can authenticate'
            }
          />
        </div>
      )}

      <Card>
        <div className="flex flex-wrap gap-2" style={{ marginBottom: 12 }}>
          <input
            className="input"
            placeholder="Filter by country (BT)"
            value={filter.country}
            onChange={(e) => setFilter({ ...filter, country: e.target.value.toUpperCase() })}
            style={{ maxWidth: 200 }}
          />
          <input
            className="input"
            placeholder="Filter by currency (BTN)"
            value={filter.currency}
            onChange={(e) => setFilter({ ...filter, currency: e.target.value.toUpperCase() })}
            style={{ maxWidth: 200 }}
          />
        </div>

        {rows === null ? (
          <Loading />
        ) : rows.length === 0 ? (
          <Empty>
            No payment methods configured for this filter. Players in these
            markets cannot deposit until you add one.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Market</th>
                  <th>Provider</th>
                  <th>Account / merchant ID</th>
                  <th>Limits</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} style={{ opacity: row.active ? 1 : 0.55 }}>
                    <td>
                      <strong>{row.name}</strong>
                      <div className="text-xs text-muted">
                        priority {row.priority}
                        {row.fee_bps ? ` · ${(row.fee_bps / 100).toFixed(2)}% fee` : ''}
                      </div>
                    </td>
                    <td>
                      <Badge status="none">{row.country_code}</Badge> <Badge status="none">{row.currency}</Badge>
                    </td>
                    <td>
                      <div>{PROVIDER_LABELS[row.provider] ?? row.provider}</div>
                      {row.credential_env && (
                        <div className="text-xs">
                          {row.credential_present ? (
                            <span className="text-ok">{row.credential_env} is set</span>
                          ) : (
                            <span className="text-warn">
                              {row.credential_env} is not set in this deployment
                            </span>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="text-xs">{row.account_id || <span className="text-muted">-</span>}</td>
                    <td className="text-xs">
                      {money(row.min_amount_minor, row.currency)} &ndash;{' '}
                      {money(row.max_amount_minor, row.currency)}
                    </td>
                    <td>
                      <Badge status={row.active ? 'succeeded' : 'none'}>
                        {row.active ? 'active' : 'inactive'}
                      </Badge>
                      <div className="text-xs text-muted">
                        {row.deposits_enabled ? 'deposits' : ''}
                        {row.deposits_enabled && row.withdrawals_enabled ? ' · ' : ''}
                        {row.withdrawals_enabled ? 'payouts' : ''}
                      </div>
                    </td>
                    <td>
                      <div className="flex gap-1">
                        <button type="button" className="btn btn-sm" onClick={() => openEdit(row)}>
                          Edit
                        </button>
                        <button type="button" className="btn btn-sm" onClick={() => toggle(row)}>
                          {row.active ? 'Disable' : 'Enable'}
                        </button>
                        <button type="button" className="btn btn-sm" onClick={() => explainFor(row)}>
                          Why
                        </button>
                        {row.active && (
                          <button type="button" className="btn btn-sm" onClick={() => retire(row)}>
                            Retire
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {form && (
        <Modal
          title={editing ? `Edit ${editing.name}` : 'Add a payment method'}
          onClose={() => {
            setForm(null)
            setEditing(null)
          }}
        >
          <form onSubmit={save}>
            <Field label="Bank / method name" hint="What the player sees, e.g. mBoB (Bank of Bhutan)">
              <input
                className="input"
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </Field>

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Country code" hint="ISO-3166 alpha-2, or * for every country">
                <input
                  className="input"
                  value={form.country_code}
                  onChange={(e) =>
                    setForm({ ...form, country_code: e.target.value.toUpperCase() })
                  }
                />
              </Field>
              <Field label="Currency" hint="ISO-4217, or * for every currency">
                <input
                  className="input"
                  value={form.currency}
                  onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })}
                />
              </Field>
            </div>

            <Field
              label="Account / merchant ID"
              hint="The account money is collected into. Not a secret; it appears on the player's statement."
            >
              <input
                className="input"
                value={form.account_id}
                onChange={(e) => setForm({ ...form, account_id: e.target.value })}
              />
            </Field>

            <Field
              label="API endpoint"
              hint="Optional. Leave empty for a rail that settles out-of-band; https is required in production."
            >
              <input
                className="input"
                value={form.api_endpoint}
                onChange={(e) => setForm({ ...form, api_endpoint: e.target.value })}
              />
            </Field>

            <Field
              label="Credential environment variable"
              hint="The NAME of the variable holding the key - never the key itself. e.g. MBOB_API_KEY"
            >
              <input
                className="input"
                value={form.credential_env}
                onChange={(e) => setForm({ ...form, credential_env: e.target.value.toUpperCase() })}
                placeholder="MBOB_API_KEY"
              />
              <p className="text-xs text-muted" style={{ marginTop: 4 }}>
                The value is never stored in the database, never sent to this
                API and never logged. Set it in your host's environment.
              </p>
            </Field>

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Gateway" hint="Which adapter speaks to it">
                <select
                  className="input"
                  value={form.provider}
                  onChange={(e) => setForm({ ...form, provider: e.target.value })}
                >
                  {meta.providers.map((name) => (
                    <option key={name || 'default'} value={name}>
                      {PROVIDER_LABELS[name] ?? name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Cashier family" hint="How it is grouped for the player">
                <select
                  className="input"
                  value={form.method}
                  onChange={(e) => setForm({ ...form, method: e.target.value })}
                >
                  {meta.method_kinds.map((kind) => (
                    <option key={kind} value={kind}>
                      {kind}
                    </option>
                  ))}
                </select>
              </Field>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Min (minor units)" hint="0 = no limit from this row">
                <input
                  className="input"
                  type="number"
                  min="0"
                  value={form.min_amount_minor}
                  onChange={(e) => setForm({ ...form, min_amount_minor: e.target.value })}
                />
              </Field>
              <Field label="Max (minor units)">
                <input
                  className="input"
                  type="number"
                  min="0"
                  value={form.max_amount_minor}
                  onChange={(e) => setForm({ ...form, max_amount_minor: e.target.value })}
                />
              </Field>
              <Field label="Fee (basis points)" hint="25 = 0.25%">
                <input
                  className="input"
                  type="number"
                  min="0"
                  max="10000"
                  value={form.fee_bps}
                  onChange={(e) => setForm({ ...form, fee_bps: e.target.value })}
                />
              </Field>
            </div>

            <Field
              label="Priority"
              hint="Lower wins. A more specific market always beats a wildcard, whatever the priority."
            >
              <input
                className="input"
                type="number"
                min="0"
                value={form.priority}
                onChange={(e) => setForm({ ...form, priority: e.target.value })}
              />
            </Field>

            <div className="flex flex-wrap gap-4" style={{ margin: '12px 0' }}>
              <Check
                label="Accept deposits"
                checked={form.deposits_enabled}
                onChange={(v) => setForm({ ...form, deposits_enabled: v })}
              />
              <Check
                label="Pay out withdrawals"
                checked={form.withdrawals_enabled}
                onChange={(v) => setForm({ ...form, withdrawals_enabled: v })}
              />
              <Check
                label="Active"
                checked={form.active}
                onChange={(v) => setForm({ ...form, active: v })}
              />
            </div>

            <Field label="Internal notes" hint="For staff. Never shown to players.">
              <input
                className="input"
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
              />
            </Field>

            <div className="flex gap-2" style={{ marginTop: 16 }}>
              <button type="submit" className="btn btn-primary" disabled={saving}>
                {saving ? 'Saving…' : editing ? 'Save changes' : 'Add method'}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setForm(null)
                  setEditing(null)
                }}
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {explain && (
        <Modal title={`How ${explain.row.name} is chosen`} onClose={() => setExplain(null)}>
          <p className="text-sm text-muted">
            For {explain.data.country} / {explain.data.currency} ({explain.data.direction}
            {explain.data.amount_minor ? `, ${money(explain.data.amount_minor, explain.data.currency)}` : ''}),
            routing resolves in this order:
          </p>
          <ol style={{ marginTop: 12, paddingLeft: 18 }}>
            {explain.data.considered.map((row) => (
              <li key={row.id} style={{ marginBottom: 6 }}>
                <strong>{row.name}</strong>{' '}
                <Badge status={row.outcome === 'wins' ? 'succeeded' : 'none'}>{row.outcome}</Badge>
                <div className="text-xs text-muted">
                  {row.country_code}/{row.currency} · priority {row.priority}
                </div>
              </li>
            ))}
          </ol>
        </Modal>
      )}
    </div>
  )
}

function Metric({ label, value, hint, tone = 'default' }) {
  return (
    <div className="card" style={{ padding: 12 }}>
      <div className="text-xs text-muted">{label}</div>
      <div
        className={`text-2xl font-semibold${tone === 'warn' ? ' text-warn' : ''}`}
      >
        {value}
      </div>
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  )
}

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block', marginBottom: 12 }}>
      <span style={{ display: 'block', fontWeight: 600, marginBottom: 4 }}>{label}</span>
      {hint && (
        <span className="text-xs text-muted" style={{ display: 'block', marginBottom: 4 }}>
          {hint}
        </span>
      )}
      {children}
    </label>
  )
}

function Check({ label, checked, onChange }) {
  return (
    <label className="flex items-center gap-2">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  )
}
