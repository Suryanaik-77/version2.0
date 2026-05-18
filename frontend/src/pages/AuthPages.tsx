/**
 * AuthPages.tsx — Login, Register, ForgotPassword
 */
import React, { useState, FormEvent } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '@/stores/auth'
import { api } from '@/lib/api'

// ── Shared shell ───────────────────────────────────────────────────────────────
function AuthShell({ children, title, subtitle }: {
  children: React.ReactNode; title: string; subtitle?: string
}) {
  return (
    <div style={{
      minHeight: '100dvh', background: 'var(--bg-canvas)',
      display: 'grid', gridTemplateColumns: '1fr 1fr',
    }}>
      {/* Left: brand panel */}
      <div style={{
        background: 'var(--text-0)',
        display: 'flex', flexDirection: 'column',
        justifyContent: 'space-between', padding: '48px 56px',
      }}>
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{
            width: 30, height: 30, borderRadius: 8, background: 'var(--accent)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
              <path d="M2.5 6.5H10.5M6.5 2.5V10.5" stroke="white" strokeWidth="2" strokeLinecap="round"/>
            </svg>
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: '#fff', letterSpacing: '0.05em' }}>
            VLSI INTERVIEW
          </span>
        </div>

        {/* Center copy */}
        <div>
          <h1 style={{
            fontFamily: 'var(--font-display)', fontSize: 40, color: '#fff',
            lineHeight: 1.1, marginBottom: 16,
          }}>
            Technical<br />
            <em style={{ color: 'rgba(255,255,255,0.5)' }}>interviews</em><br />
            engineered.
          </h1>
          <p style={{ fontSize: 14, color: 'rgba(255,255,255,0.45)', lineHeight: 1.7, maxWidth: 340 }}>
            Adaptive AI evaluation for Analog Layout, Physical Design, and Design Verification professionals.
          </p>
        </div>

        {/* Bottom tags */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {['Analog Layout', 'Physical Design', 'Design Verification'].map(d => (
            <span key={d} style={{
              fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.08em',
              textTransform: 'uppercase', color: 'rgba(255,255,255,0.3)',
              border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: 'var(--r-full)', padding: '4px 10px',
            }}>
              {d}
            </span>
          ))}
        </div>
      </div>

      {/* Right: form */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '48px 56px', background: 'var(--bg-0)',
      }}>
        <div style={{ width: '100%', maxWidth: 380 }}>
          <div style={{ marginBottom: 36 }}>
            <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 28, color: 'var(--text-0)', marginBottom: 6 }}>
              {title}
            </h2>
            {subtitle && (
              <p style={{ fontSize: 13, color: 'var(--text-2)' }}>{subtitle}</p>
            )}
          </div>
          {children}
        </div>
      </div>
    </div>
  )
}

// ── Shared field ───────────────────────────────────────────────────────────────
function Field({ label, type = 'text', value, onChange, placeholder, error, autoFocus, autoComplete }: {
  label: string; type?: string; value: string; onChange: (v: string) => void;
  placeholder?: string; error?: string; autoFocus?: boolean; autoComplete?: string
}) {
  const [focused, setFocused] = useState(false)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-1)', letterSpacing: '0.01em' }}>
        {label}
      </label>
      <input
        type={type} value={value} placeholder={placeholder}
        autoFocus={autoFocus} autoComplete={autoComplete}
        onChange={e => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        style={{
          width: '100%', padding: '10px 13px',
          fontFamily: 'var(--font-body)', fontSize: 13, color: 'var(--text-0)',
          background: 'var(--bg-0)',
          border: `1px solid ${error ? 'var(--red)' : focused ? 'var(--accent)' : 'var(--border-2)'}`,
          borderRadius: 'var(--r-md)', outline: 'none',
          transition: 'border-color var(--dur-fast)',
          boxShadow: focused ? 'var(--shadow-focus)' : undefined,
        }}
      />
      {error && <span style={{ fontSize: 11, color: 'var(--red)' }}>{error}</span>}
    </div>
  )
}

// ── Submit button ──────────────────────────────────────────────────────────────
function SubmitBtn({ loading, children }: { loading: boolean; children: string }) {
  return (
    <button
      type="submit"
      disabled={loading}
      style={{
        width: '100%', background: loading ? 'var(--text-3)' : 'var(--text-0)',
        color: '#fff', border: 'none', borderRadius: 'var(--r-md)',
        padding: '11px 0', fontSize: 13, fontFamily: 'var(--font-body)',
        fontWeight: 500, cursor: loading ? 'not-allowed' : 'pointer',
        transition: 'background var(--dur-std)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
      }}
    >
      {loading && (
        <span style={{
          width: 14, height: 14, border: '1.5px solid rgba(255,255,255,0.3)',
          borderTopColor: '#fff', borderRadius: '50%',
          display: 'inline-block', animation: 'spin 0.65s linear infinite',
        }} />
      )}
      {loading ? 'Please wait…' : children}
    </button>
  )
}

// ── Error banner ───────────────────────────────────────────────────────────────
function ErrorBanner({ msg }: { msg: string }) {
  return (
    <div style={{
      background: 'var(--red-bg)', border: '1px solid var(--red-border)',
      borderRadius: 'var(--r-md)', padding: '10px 14px',
      fontSize: 12, color: 'var(--red)', lineHeight: 1.5,
    }}>
      {msg}
    </div>
  )
}

// ── LOGIN ──────────────────────────────────────────────────────────────────────
export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as any)?.from?.pathname || '/dashboard'

  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!email || !password) { setError('Please fill in all fields.'); return }
    setLoading(true); setError('')
    try {
      await login(email, password)
      navigate(from, { replace: true })
    } catch (err: any) {
      const status = err?.response?.status
      setError(status === 401 ? 'Invalid email or password.' : 'Sign in failed. Try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthShell title="Welcome back" subtitle="Sign in to your account">
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        {error && <ErrorBanner msg={error} />}
        <Field label="Email address" type="email" value={email} onChange={setEmail}
          placeholder="you@example.com" autoFocus autoComplete="email" />
        <Field label="Password" type="password" value={password} onChange={setPassword}
          placeholder="Your password" autoComplete="current-password" />
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: -8 }}>
          <Link to="/forgot-password" style={{ fontSize: 12, color: 'var(--text-3)', textDecoration: 'none' }}>
            Forgot password?
          </Link>
        </div>
        <SubmitBtn loading={loading}>Sign in</SubmitBtn>
        <p style={{ textAlign: 'center', fontSize: 12, color: 'var(--text-3)' }}>
          No account yet?{' '}
          <Link to="/register" style={{ color: 'var(--accent-dim)', textDecoration: 'none', fontWeight: 500 }}>
            Create one
          </Link>
        </p>
      </form>
    </AuthShell>
  )
}

// ── REGISTER ───────────────────────────────────────────────────────────────────
export function RegisterPage() {
  const { login } = useAuth()
  const navigate = useNavigate()

  const [fullName, setFullName] = useState('')
  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [errors, setErrors]     = useState<Record<string, string>>({})
  const [loading, setLoading]   = useState(false)
  const [serverError, setServerError] = useState('')

  function validate() {
    const e: Record<string, string> = {}
    if (!fullName.trim()) e.fullName = 'Name is required'
    if (!email.includes('@')) e.email = 'Enter a valid email'
    if (password.length < 8) e.password = 'Minimum 8 characters'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!validate()) return
    setLoading(true); setServerError('')
    try {
      await api.post('/auth/register', { email, password, full_name: fullName })
      await login(email, password)
      navigate('/dashboard', { replace: true })
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      setServerError(detail === 'Email already registered'
        ? 'That email is already in use. Sign in instead?'
        : 'Registration failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthShell title="Create account" subtitle="Start evaluating VLSI engineers today">
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {serverError && <ErrorBanner msg={serverError} />}
        <Field label="Full name" value={fullName} onChange={setFullName}
          placeholder="Jane Smith" autoFocus error={errors.fullName} autoComplete="name" />
        <Field label="Email address" type="email" value={email} onChange={setEmail}
          placeholder="you@company.com" error={errors.email} autoComplete="email" />
        <Field label="Password" type="password" value={password} onChange={setPassword}
          placeholder="8+ characters" error={errors.password} autoComplete="new-password" />
        <div style={{ marginTop: 4 }}>
          <SubmitBtn loading={loading}>Create account</SubmitBtn>
        </div>
        <p style={{ textAlign: 'center', fontSize: 12, color: 'var(--text-3)' }}>
          Already have an account?{' '}
          <Link to="/login" style={{ color: 'var(--accent-dim)', textDecoration: 'none', fontWeight: 500 }}>
            Sign in
          </Link>
        </p>
      </form>
    </AuthShell>
  )
}

// ── FORGOT PASSWORD ────────────────────────────────────────────────────────────
export function ForgotPasswordPage() {
  const [email, setEmail]   = useState('')
  const [sent, setSent]     = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError]   = useState('')

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!email.includes('@')) { setError('Enter a valid email address.'); return }
    setLoading(true); setError('')
    try {
      // api is statically imported above
      await api.post('/auth/forgot-password', { email })
      setSent(true)
    } catch {
      setError('Something went wrong. Try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthShell title="Reset password" subtitle="We'll send a reset link to your email">
      {sent ? (
        <div style={{
          background: 'var(--green-bg)', border: '1px solid var(--green-border)',
          borderRadius: 'var(--r-lg)', padding: '24px', textAlign: 'center',
        }}>
          <p style={{ fontSize: 22, marginBottom: 8 }}>✉</p>
          <p style={{ fontFamily: 'var(--font-display)', fontSize: 18, color: 'var(--text-0)', marginBottom: 8 }}>
            Check your inbox
          </p>
          <p style={{ fontSize: 13, color: 'var(--text-2)' }}>
            If an account exists for <strong>{email}</strong>, you'll receive a reset link shortly.
          </p>
        </div>
      ) : (
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {error && <ErrorBanner msg={error} />}
          <Field label="Email address" type="email" value={email} onChange={setEmail}
            placeholder="you@example.com" autoFocus autoComplete="email" />
          <SubmitBtn loading={loading}>Send reset link</SubmitBtn>
          <p style={{ textAlign: 'center', fontSize: 12, color: 'var(--text-3)' }}>
            <Link to="/login" style={{ color: 'var(--accent-dim)', textDecoration: 'none' }}>
              ← Back to sign in
            </Link>
          </p>
        </form>
      )}
    </AuthShell>
  )
}
