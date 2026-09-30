import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Trans, useTranslation } from 'react-i18next'
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
      <Card title={t('auth.create_title')}>
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
              placeholder={t('auth.username_hint')} />
          </div>

          <div className="field">
            <label>{t('auth.password')}</label>
            <input type="password" value={form.password} onChange={set('password')} required minLength={8}
              autoComplete="new-password" />
            <span className="tiny muted">{t('auth.password_hint')}</span>
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
                {/* Three links live inside this sentence, and their position
                    differs by language ("acepto los Términos y la Política de
                    privacidad" vs "I accept the Terms and the Privacy Policy").
                    Splitting it into concatenated fragments would read as broken
                    grammar in any language that reorders them, so the sentence
                    stays whole in the locale file and the links are interpolated
                    into it.

                    The indices are positions in `components` below, NOT in the
                    JSX - react-i18next numbers JSX children, and a `{' '}` is a
                    child, so the numbers shift the moment someone reformats the
                    line. The link labels are {{placeholders}}, which the i18n
                    checker already verifies are present in all nine locales. */}
                <Trans
                  i18nKey="auth.terms_consent"
                  components={[
                    <Link to="/legal/terms" />,
                    <Link to="/legal/privacy" />,
                    <Link to="/legal/responsible-gambling" />,
                  ]}
                  values={{
                    terms: t('legal.terms'),
                    privacy: t('legal.privacy'),
                    rg: t('account.responsible'),
                  }}
                />
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
