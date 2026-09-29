/**
 * Global auth + wallet store. Deliberately tiny (Context + useReducer) - the
 * money lives on the server, this only mirrors the last known state for
 * rendering, and every mutation refetches from the API.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer } from 'react'
import { api, tokens } from './api.js'

const StoreContext = createContext(null)

const initialState = {
  user: null,
  wallet: null,
  config: null,
  loading: true,
  bootError: null,
  toast: null,
}

function reducer(state, action) {
  switch (action.type) {
    case 'boot:start':
      return { ...state, loading: true, bootError: null }
    case 'boot:done':
      return { ...state, loading: false, user: action.user, wallet: action.wallet }
    case 'boot:fail':
      return { ...state, loading: false, bootError: action.error }
    case 'config':
      return { ...state, config: action.config }
    case 'wallet':
      return { ...state, wallet: action.wallet }
    case 'user':
      return { ...state, user: action.user }
    case 'logout':
      return { ...state, user: null, wallet: null }
    case 'toast':
      return { ...state, toast: action.toast }
    default:
      return state
  }
}

export function StoreProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState)

  const refreshWallet = useCallback(async () => {
    if (!tokens.access) return
    try {
      const summary = await api.wallet()
      dispatch({ type: 'wallet', wallet: summary.balances })
    } catch {
      /* transient - the next action will retry */
    }
  }, [])

  const refreshUser = useCallback(async () => {
    if (!tokens.access) {
      dispatch({ type: 'boot:done', user: null, wallet: null })
      return
    }
    try {
      const me = await api.me()
      dispatch({ type: 'boot:done', user: me, wallet: me.balances })
    } catch {
      tokens.clear()
      dispatch({ type: 'boot:done', user: null, wallet: null })
    }
  }, [])

  const boot = useCallback(async () => {
    dispatch({ type: 'boot:start' })
    try {
      const config = await api.config()
      dispatch({ type: 'config', config })
    } catch (err) {
      dispatch({ type: 'boot:fail', error: err.message })
    }
    await refreshUser()
  }, [refreshUser])

  useEffect(() => {
    boot()
  }, [boot])

  const login = useCallback(
    async (email, password) => {
      const data = await api.login({ email, password })
      tokens.set(data)
      const me = await api.me()
      dispatch({ type: 'boot:done', user: me, wallet: me.balances })
      return me
    },
    [],
  )

  const register = useCallback(async (payload) => {
    const data = await api.register(payload)
    tokens.set(data)
    const me = await api.me()
    dispatch({ type: 'boot:done', user: me, wallet: me.balances })
    return me
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      /* logging out locally is what matters */
    }
    tokens.clear()
    dispatch({ type: 'logout' })
  }, [])

  const toast = useCallback((message, kind = 'info') => {
    dispatch({ type: 'toast', toast: { message, kind, id: Date.now() } })
    setTimeout(() => dispatch({ type: 'toast', toast: null }), 5200)
  }, [])

  const value = useMemo(
    () => ({
      ...state,
      isAuthed: Boolean(state.user),
      isAdmin: state.user?.role === 'admin',
      login,
      register,
      logout,
      refreshWallet,
      refreshUser,
      toast,
    }),
    [state, login, register, logout, refreshWallet, refreshUser, toast],
  )

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>
}

export function useStore() {
  const ctx = useContext(StoreContext)
  if (!ctx) throw new Error('useStore must be used inside <StoreProvider>')
  return ctx
}
