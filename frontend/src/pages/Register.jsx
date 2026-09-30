import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useStore } from '../lib/store.jsx'
import { Alert, Card } from '../components/ui.jsx'
import { countryOptions } from '../lib/countries.js'
import { fmtCents } from '../lib/format.js'

export default function Register() {
  const { t, i18n } = useTranslation()
  const { register, toast, config } = useStore()
  const navigate = useNavigate()
  const [form, setForm] = useState({
    email: '', username: '', password: '', date_of_birth: '', country: 'BT',
    phone: '', accepts_terms: false, bonus_code: '',
  })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // Every country, named in the player's language, sorted by that name.
  // The operator's accepted markets are a server-side policy, so the form
  // offers the world and the API is the thing that says no - a form that
  // quietly hides countries would be a second, stale copy of that policy.
  const countries = useMemo(
    () => countryOptions(i18n.language, [form.country, config?.region?.country]),
    [i18n.language, form.country, config?.region?.country],
  )

  const set = (key) => (e) =>
    setForm((f) => ({ ...f, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await register({ ...form, bonus_code: form.bonus_code || undefined })
      toast('Account created', 'success')
      navigate('/wallet', { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const blocked = config?.jurisdiction_blocklist || []

  return (
    <div className="page narrow">
      <Card title="Create your account">
        {error && <Alert kind="error">{error}</Alert>}

        {config?.bonuses?.first_deposit_pct > 0 && (
          <Alert kind="ok">
            {config.bonuses.first_deposit_pct}% first-deposit bonus up to{' '}
            {fmtCents(config.bonuses.first_deposit_cap)}, {config.bonuses.first_deposit_wager_x}x playthrough.
          </Alert>
        )}

        <form onSubmit={submit}>
          <div className="field">
            <label>{t('auth.email')}</label>
            <input type="email" value={form.email} onChange={set('email')} required autoComplete="email" />
          </div>

          <div className="field">
            <label>{t('auth.username')}</label>
            <input value={form.username} onChange={set('username')} required minLength={3} maxLength={32}
              placeholder="letters, digits, _ and -" />
          </div>

          <div className="field">
            <label>{t('auth.password')}</label>
            <input type="password" value={form.password} onChange={set('password')} required minLength={8}
              autoComplete="new-password" />
            <span className="tiny muted">At least 8 characters. Use a password manager.</span>
          </div>

          <div className="row" style={{ gap: 12 }}>
            <div className="field" style={{ flex: 1 }}>
              <label>{t('auth.date_of_birth')}</label>
              <input type="date" value={form.date_of_birth} onChange={set('date_of_birth')} required />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label>{t('auth.country')}</label>
              <select value={form.country} onChange={set('country')}>
                {countries.map((country) => (
                  <option key={country.code} value={country.code}>
                    {country.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="field">
            <label>Bonus code ({t('common.optional')})</label>
            <input value={form.bonus_code} onChange={(e) => setForm((f) => ({ ...f, bonus_code: e.target.value.toUpperCase() }))} />
          </div>

          <div className="field">
            <label className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
              <input type="checkbox" checked={form.accepts_terms} onChange={set('accepts_terms')} required />
              <span className="small">
                I am 18 or older, I accept the <Link to="/legal/terms">Terms</Link> and{' '}
                <Link to="/legal/privacy">Privacy Policy</Link>, and I understand the{' '}
                <Link to="/legal/responsible-gambling">responsible gambling</Link> tools available to me.
              </span>
            </label>
          </div>

          <button className="btn btn-primary btn-block" disabled={busy || !form.accepts_terms}>
            {busy ? <span className="spinner" /> : t('auth.signup')}
          </button>
        </form>

        <p className="small muted" style={{ marginTop: 14 }}>
          {t('auth.have_account')}{' '}
          <Link to="/login" style={{ color: 'var(--accent)' }}>
            {t('auth.signin')}
          </Link>
        </p>

        <p className="tiny muted" style={{ marginTop: 10 }}>
          Registration is not available in: {blocked.join(', ') || '—'}. Gambling laws vary by
          jurisdiction; it is your responsibility to comply with local law.
        </p>
      </Card>
    </div>
  )
}
