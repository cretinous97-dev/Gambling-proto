/**
 * A transaction/payment status, as a chip.
 *
 * Two jobs, and the second is the one that gets skipped:
 *
 *  1. Colour - green is done, amber is waiting on someone, red is bad, grey is
 *     over-and-neutral. A player scanning their history should not have to read
 *     a word to know whether their money moved.
 *  2. Words - the label is translated, and it says who we are waiting *for*.
 *     "Awaiting payment" and "Under review" are both amber and mean completely
 *     different things to the person staring at them.
 */
import { useTranslation } from 'react-i18next'

const TONE = {
  succeeded: 'ok',
  paid: 'ok',
  approved: 'ok',
  posted: 'ok',
  verified: 'ok',
  win: 'ok',
  blackjack: 'ok',

  pending: 'warn',
  requires_action: 'warn',
  under_review: 'warn',
  requested: 'warn',
  push: 'muted',

  none: 'muted',
  cancelled: 'muted',

  failed: 'bad',
  rejected: 'bad',
  chargeback: 'bad',
  lose: 'bad',
}

const TONE_CLASS = {
  ok: 'bg-ok/15 text-ok border-ok/30',
  warn: 'bg-warn/15 text-warn border-warn/30',
  bad: 'bg-bad/15 text-bad border-bad/30',
  muted: 'bg-white/5 text-muted border-line',
}

export default function StatusChip({ status, label, className = '' }) {
  const { t } = useTranslation()
  const tone = TONE[status] || 'muted'
  const text = label || t(`status.${status}`, { defaultValue: status })

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium whitespace-nowrap ${TONE_CLASS[tone]} ${className}`}
    >
      <span
        className={`size-1.5 rounded-full bg-current ${tone === 'warn' ? 'animate-pulse' : ''}`}
        aria-hidden="true"
      />
      {text}
    </span>
  )
}
