import React, { useEffect } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '@/stores/auth'
import { ToastContainer } from '@/components/ui'
import { Spinner } from '@/components/ui'
import ErrorBoundary from '@/components/ErrorBoundary'

// Layouts
import AppLayout from '@/layouts/AppLayout'

// Pages
import LandingPage from '@/pages/LandingPage'
import { LoginPage, RegisterPage, ForgotPasswordPage } from '@/pages/AuthPages'
import InterviewPage from '@/pages/InterviewPage'
import DashboardPage from '@/pages/DashboardPage'
import ReportPage from '@/pages/ReportPage'
import ReviewerPage from '@/pages/ReviewerPage'
import AdminPage from '@/pages/AdminPage'
import NotFound from '@/pages/NotFound'

// ── Guards ─────────────────────────────────────────────────────────────────────

function RequireAuth({ children, role }: { children: React.ReactNode; role?: 'candidate' | 'reviewer' | 'admin' }) {
  const { user, isInitialized } = useAuth()
  const location = useLocation()
  if (!isInitialized) return <AppLoading />
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />
  if (role === 'admin'    && user.role !== 'admin') return <Navigate to="/dashboard" replace />
  if (role === 'reviewer' && user.role !== 'reviewer' && user.role !== 'admin') return <Navigate to="/dashboard" replace />
  return <>{children}</>
}

function RequireGuest({ children }: { children: React.ReactNode }) {
  const { user, isInitialized } = useAuth()
  if (!isInitialized) return <AppLoading />
  if (user) return <Navigate to="/dashboard" replace />
  return <>{children}</>
}

// ── Loading ────────────────────────────────────────────────────────────────────

function AppLoading() {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'var(--bg-0)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 }}>
        <div style={{
          width: 32, height: 32, borderRadius: 9, background: 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
            <path d="M2.5 6.5H10.5M6.5 2.5V10.5" stroke="white" strokeWidth="2" strokeLinecap="round"/>
          </svg>
        </div>
        <Spinner size={18} />
      </div>
    </div>
  )
}

// ── Root ───────────────────────────────────────────────────────────────────────

export default function App() {
  const { initialize, isInitialized } = useAuth()

  useEffect(() => { initialize() }, [])
  if (!isInitialized) return <AppLoading />

  return (
    <>
      <ErrorBoundary>
        <Routes>
          {/* Landing */}
          <Route path="/" element={<LandingPage />} />

          {/* Auth */}
          <Route path="/login"           element={<RequireGuest><LoginPage /></RequireGuest>} />
          <Route path="/register"        element={<RequireGuest><RegisterPage /></RequireGuest>} />
          <Route path="/forgot-password" element={<RequireGuest><ForgotPasswordPage /></RequireGuest>} />

          {/* Interview — full-screen */}
          <Route path="/interview/:sessionId" element={
            <RequireAuth role="candidate">
              <ErrorBoundary>
                <InterviewPage />
              </ErrorBoundary>
            </RequireAuth>
          } />

          {/* Candidate */}
          <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/report/:sessionId" element={<ReportPage />} />
          </Route>

          {/* Reviewer */}
          <Route element={<RequireAuth role="reviewer"><AppLayout /></RequireAuth>}>
            <Route path="/reviewer" element={<ReviewerPage />} />
            <Route path="/reviewer/session/:sessionId" element={<ReviewerPage />} />
          </Route>

          {/* Admin */}
          <Route element={<RequireAuth role="admin"><AppLayout /></RequireAuth>}>
            <Route path="/admin" element={<AdminPage />} />
          </Route>

          <Route path="*" element={<NotFound />} />
        </Routes>
      </ErrorBoundary>
      <ToastContainer />
    </>
  )
}
