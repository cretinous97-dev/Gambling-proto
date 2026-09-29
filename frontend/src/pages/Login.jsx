import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useStore } from '../lib/store.jsx'
import { Alert, Card } from '../components/ui.jsx'

export default function Login() {
  const { login, toast, config } = useStore()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await login(email, password)
      toast('Signed in', 'success')
      navigate(location.state?.from?.pathname || '/', { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page narrow">
      <Card title="Log in">
        {error && <Alert kind="error">{error}</Alert>}
        <form onSubmit={submit}>
          <div className="field">
            <label>Email</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              autoComplete="email" required />
          </div>
          <div className="field">
            <label>Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password" required />
          </div>
          <button className="btn btn-primary btn-block" disabled={busy}>
            {busy ? <span className="spinner" /> : 'Log in'}
          </button>
        </form>
        <p className="small muted" style={{ marginTop: 14 }}>
          No account yet? <Link to="/register" style={{ color: 'var(--accent)' }}>Create one</Link>.
        </p>
        {config?.environment === 'development' && (
          <div className="alert alert-info tiny" style={{ marginTop: 12 }}>
            Development build. The seeded admin account is <span className="mono">{'admin@casino.example.com'}</span> —
            change ADMIN_PASSWORD before deploying.
          </div>
        )}
      </Card>
    </div>
  )
}
