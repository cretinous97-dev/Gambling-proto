import { useEffect, useState } from 'react'
import { fmtCents, statusClass, toAmountString } from '../lib/format.js'

export function Card({ title, children, className = '', actions = null, ...rest }) {
  return (
    <div className={`card ${className}`} {...rest}>
      {(title || actions) && (
        <div className="row between" style={{ marginBottom: 12 }}>
          {title && <h3 className="card-title" style={{ margin: 0 }}>{title}</h3>}
          {actions}
        </div>
      )}
      {children}
    </div>
  )
}

export function Stat({ label, value, hint = null }) {
  return (
    <div className="stat">
      <span className="value">{value}</span>
      <span className="label">{label}</span>
      {hint && <span className="tiny muted">{hint}</span>}
    </div>
  )
}

export function Badge({ status, children }) {
  return <span className={statusClass(status)}>{children ?? status}</span>
}

export function Alert({ kind = 'info', children }) {
  if (!children) return null
  return <div className={`alert alert-${kind}`}>{children}</div>
}

/**
 * Stake input. Shows what the player typed, emits a canonical decimal string
 * ("12.34"). Quick-bet chips halve/double within the configured limits, and the
 * server re-validates everything - this is convenience, not enforcement.
 */
export function StakeInput({ value, onChange, min = 10, max = 200000, label = 'Bet amount', disabled }) {
  const minMajor = (min / 100).toFixed(2)
  const maxMajor = (max / 100).toFixed(2)
  const current = Math.round(Number(value || 0) * 100)

  const bump = (factor) => {
    const next = Math.max(min, Math.min(max, Math.round((current || min) * factor)))
    onChange(toAmountString((next / 100).toFixed(2)))
  }

  return (
    <div className="field">
      <label>{label}</label>
      <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
        <input
          inputMode="decimal"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(toAmountString(e.target.value))}
          placeholder={minMajor}
        />
        <button type="button" className="btn btn-sm" disabled={disabled} onClick={() => bump(0.5)}>½</button>
        <button type="button" className="btn btn-sm" disabled={disabled} onClick={() => bump(2)}>2×</button>
        <button type="button" className="btn btn-sm" disabled={disabled} onClick={() => onChange(maxMajor)}>Max</button>
      </div>
      <span className="tiny muted">Min {minMajor} · Max {maxMajor}</span>
    </div>
  )
}

export function Loading({ label = 'Loading…' }) {
  return (
    <div className="row" style={{ padding: 22, justifyContent: 'center' }}>
      <span className="spinner" />
      <span className="muted">{label}</span>
    </div>
  )
}

export function Modal({ title, onClose, children, footer = null }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose?.()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row between" style={{ marginBottom: 14 }}>
          <h3 className="card-title" style={{ margin: 0 }}>{title}</h3>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>Close</button>
        </div>
        {children}
        {footer && <div className="row" style={{ marginTop: 16, justifyContent: 'flex-end' }}>{footer}</div>}
      </div>
    </div>
  )
}

export function Tabs({ tabs, active, onChange }) {
  return (
    <div className="tabs">
      {tabs.map((t) => (
        <button key={t.id} className={active === t.id ? 'active' : ''} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  )
}

export function Empty({ children }) {
  return <p className="muted small" style={{ padding: '14px 0' }}>{children}</p>
}

export function Amount({ cents, currency = 'USD', className = '' }) {
  return <span className={className}>{fmtCents(cents, { currency })}</span>
}

/** Generic async-action button that never double-submits. */
export function ActionButton({ onClick, children, className = 'btn btn-primary', disabled, confirm = null }) {
  const [busy, setBusy] = useState(false)
  const [armed, setArmed] = useState(false)

  const run = async () => {
    if (confirm && !armed) {
      setArmed(true)
      setTimeout(() => setArmed(false), 4000)
      return
    }
    setBusy(true)
    try {
      await onClick()
    } finally {
      setBusy(false)
      setArmed(false)
    }
  }

  return (
    <button className={className} disabled={disabled || busy} onClick={run}>
      {busy ? <span className="spinner" /> : armed ? 'Confirm?' : children}
    </button>
  )
}

export function Copyable({ value, label = null }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard blocked - the value is visible anyway */
    }
  }
  return (
    <span className="row" style={{ gap: 8 }}>
      <span className="mono tiny" style={{ wordBreak: 'break-all' }}>{value}</span>
      <button className="btn btn-sm btn-ghost" onClick={copy}>{copied ? 'Copied' : label || 'Copy'}</button>
    </span>
  )
}

export function ProviderBanner({ provider }) {
  if (!provider) return null
  if (provider.mode === 'simulation') {
    return (
      <div className="demo-banner">
        DEMO / SANDBOX MODE — payments are simulated. No real money is accepted or paid out.
        Set PAYMENT_PROVIDER + provider keys on the server to go live.
      </div>
    )
  }
  return null
}
