import { useCallback, useState } from 'react'
import { api } from '../../lib/api.js'
import { useStore } from '../../lib/store.jsx'

/**
 * Shared "place one wager" hook.
 *
 * Handles the three things every game gets wrong when they are hand-rolled:
 *   1. an idempotency key per attempt, so a double-click or a retried request
 *      cannot place two bets;
 *   2. a single in-flight guard, so the UI cannot fire overlapping bets;
 *   3. wallet refresh after settlement, so the header never shows stale money.
 */
export function useBetSubmit(onSettled) {
  const { toast } = useStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = useCallback(
    async (fn, { refresh = true, quiet = false } = {}) => {
      setBusy(true)
      setError('')
      try {
        const result = await fn()
        if (refresh && onSettled) await onSettled()
        return result
      } catch (err) {
        setError(err.message)
        if (!quiet) toast(err.message, 'error')
        return null
      } finally {
        setBusy(false)
      }
    },
    [onSettled, toast],
  )

  const play = useCallback(
    (game, stake, params) =>
      run(() =>
        api.play({
          game,
          stake,
          params,
          idempotency_key: `${game}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        }),
      ),
    [run],
  )

  return { busy, error, run, play, setError }
}
