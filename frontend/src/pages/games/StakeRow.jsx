import { StakeInput } from '../../components/ui.jsx'

/** Stake + primary action row shared by every instant game. */
export default function StakeRow({ stake, setStake, min, max, onPlay, busy, label = 'Bet', disabled, children }) {
  return (
    <div className="bet-bar">
      <StakeInput value={stake} onChange={setStake} min={min} max={max} disabled={busy} />
      {children}
      <button className="btn btn-primary" onClick={onPlay} disabled={busy || disabled || !stake}>
        {busy ? <span className="spinner" /> : label}
      </button>
    </div>
  )
}
