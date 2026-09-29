import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import Layout from './components/Layout.jsx'
import { StoreProvider, useStore } from './lib/store.jsx'
import './styles.css'

import Home from './pages/Home.jsx'
import Login from './pages/Login.jsx'
import Register from './pages/Register.jsx'
import Wallet from './pages/Wallet.jsx'
import Checkout from './pages/Checkout.jsx'
import Account from './pages/Account.jsx'
import History from './pages/History.jsx'
import Leaderboard from './pages/Leaderboard.jsx'
import Promotions from './pages/Promotions.jsx'
import Legal from './pages/Legal.jsx'
import GameRoom from './pages/GameRoom.jsx'
import Crash from './pages/Crash.jsx'
import Admin from './pages/Admin.jsx'
import NotFound from './pages/NotFound.jsx'

function RequireAuth({ children }) {
  const { isAuthed, loading } = useStore()
  const location = useLocation()
  if (loading) return null
  if (!isAuthed) return <Navigate to="/login" state={{ from: location }} replace />
  return children
}

function RequireAdmin({ children }) {
  const { user, loading } = useStore()
  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'admin') return <Navigate to="/" replace />
  return children
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <StoreProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Home />} />
            <Route path="/login" element={<Login />} />
            <Route path="/register" element={<Register />} />
            <Route path="/wallet" element={<RequireAuth><Wallet /></RequireAuth>} />
            <Route path="/checkout/:depositId" element={<RequireAuth><Checkout /></RequireAuth>} />
            <Route path="/account" element={<RequireAuth><Account /></RequireAuth>} />
            <Route path="/history" element={<RequireAuth><History /></RequireAuth>} />
            <Route path="/leaderboard" element={<Leaderboard />} />
            <Route path="/promotions" element={<Promotions />} />
            <Route path="/legal/:doc" element={<Legal />} />
            <Route path="/crash" element={<Crash />} />
            <Route path="/game/:slug" element={<GameRoom />} />
            <Route path="/admin/*" element={<RequireAdmin><Admin /></RequireAdmin>} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </StoreProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
