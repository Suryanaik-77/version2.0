import React from 'react'
import { useNavigate } from 'react-router-dom'

export default function NotFound() {
  const navigate = useNavigate()
  return (
    <div style={{
      minHeight: '100dvh', background: 'var(--bg-canvas)',
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', gap: 12, padding: 32, textAlign: 'center',
    }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-4)' }}>
        404
      </span>
      <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 32, color: 'var(--text-0)' }}>
        Page not found
      </h1>
      <p style={{ fontSize: 14, color: 'var(--text-2)', maxWidth: 340, lineHeight: 1.65 }}>
        The page you're looking for doesn't exist or you may not have access.
      </p>
      <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
        <button
          onClick={() => navigate(-1)}
          style={{ background: 'none', border: '1px solid var(--border-2)', color: 'var(--text-1)', borderRadius: 'var(--r-md)', padding: '8px 18px', fontSize: 13, cursor: 'pointer', fontFamily: 'var(--font-body)' }}
        >
          Go back
        </button>
        <button
          onClick={() => navigate('/dashboard')}
          style={{ background: 'var(--text-0)', border: 'none', color: '#fff', borderRadius: 'var(--r-md)', padding: '8px 18px', fontSize: 13, cursor: 'pointer', fontFamily: 'var(--font-body)', fontWeight: 500 }}
        >
          Dashboard
        </button>
      </div>
    </div>
  )
}
