import React from 'react'
import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '@/stores/auth'

interface NavItem { path: string; label: string; exact?: boolean; icon?: string }

const CANDIDATE_NAV: NavItem[] = [
  { path: '/dashboard', label: 'Dashboard', exact: true },
]
const REVIEWER_NAV: NavItem[] = [
  { path: '/reviewer', label: 'Review Queue' },
]
const ADMIN_NAV: NavItem[] = [
  { path: '/admin',    label: 'Operations' },
  { path: '/reviewer', label: 'Review Queue' },
]

const ROLE_BADGE: Record<string, { label: string; color: string }> = {
  admin:    { label: 'Admin',    color: 'var(--accent-dim)' },
  reviewer: { label: 'Reviewer', color: 'var(--blue)' },
  candidate:{ label: 'Candidate', color: 'var(--green)' },
}

export default function AppLayout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const navItems = user?.role === 'admin'
    ? ADMIN_NAV : user?.role === 'reviewer'
    ? REVIEWER_NAV : CANDIDATE_NAV

  const roleInfo = ROLE_BADGE[user?.role || 'candidate']
  const initials = (user?.full_name || user?.email || 'U')
    .split(' ').map((p: string) => p[0]).join('').slice(0, 2).toUpperCase()

  return (
    <div style={{ display: 'flex', minHeight: '100dvh', background: 'var(--bg-canvas)' }}>

      {/* ── Sidebar ── */}
      <aside style={{
        width: 'var(--sidebar-w)', flexShrink: 0,
        background: 'var(--bg-0)', borderRight: '1px solid var(--border-0)',
        display: 'flex', flexDirection: 'column',
        position: 'sticky', top: 0, height: '100dvh', overflow: 'hidden',
      }}>
        {/* Brand */}
        <div style={{
          padding: '20px 20px 16px',
          borderBottom: '1px solid var(--border-0)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <span style={{
              width: 26, height: 26, borderRadius: 7, background: 'var(--accent)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
            }}>
              <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
                <path d="M2 5.5H9M5.5 2V9" stroke="white" strokeWidth="1.8" strokeLinecap="round"/>
              </svg>
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-0)', letterSpacing: '0.06em', fontWeight: 500 }}>
              VLSI
            </span>
            <span style={{
              marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 8,
              letterSpacing: '0.08em', textTransform: 'uppercase',
              color: roleInfo.color,
              background: roleInfo.color + '14',
              border: `1px solid ${roleInfo.color}30`,
              borderRadius: 'var(--r-full)', padding: '2px 7px',
            }}>
              {roleInfo.label}
            </span>
          </div>
        </div>

        {/* Navigation */}
        <nav style={{ padding: '10px 8px', flex: 1 }}>
          {navItems.map(item => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.exact}
              style={({ isActive }) => ({
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '8px 12px', marginBottom: 2,
                borderRadius: 'var(--r-md)', textDecoration: 'none',
                fontSize: 13, fontWeight: isActive ? 500 : 400,
                color: isActive ? 'var(--text-0)' : 'var(--text-2)',
                background: isActive ? 'var(--bg-2)' : 'transparent',
                transition: 'all var(--dur-fast)',
                borderLeft: `2px solid ${isActive ? 'var(--accent)' : 'transparent'}`,
              })}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* Footer */}
        <div style={{ borderTop: '1px solid var(--border-0)', padding: '14px 16px' }}>
          {user?.role === 'candidate' && (
            <button
              onClick={() => navigate('/dashboard')}
              style={{
                display: 'flex', width: '100%', alignItems: 'center', justifyContent: 'center',
                background: 'var(--accent)', color: '#fff', border: 'none',
                borderRadius: 'var(--r-md)', padding: '9px 0', fontSize: 12,
                fontFamily: 'var(--font-body)', fontWeight: 500, cursor: 'pointer',
                marginBottom: 12,
              }}
            >
              + New Interview
            </button>
          )}

          {/* User row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <div style={{
              width: 28, height: 28, borderRadius: '50%', background: 'var(--bg-3)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
              fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-2)', fontWeight: 500,
            }}>
              {initials}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <p style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {user?.full_name || 'User'}
              </p>
              <p style={{ fontSize: 10, color: 'var(--text-4)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)' }}>
                {user?.email}
              </p>
            </div>
            <button
              onClick={() => logout()}
              title="Sign out"
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                color: 'var(--text-4)', fontSize: 14, padding: 4, borderRadius: 4,
                display: 'flex', alignItems: 'center',
                transition: 'color var(--dur-fast)',
              }}
              onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-2)')}
              onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-4)')}
            >
              →
            </button>
          </div>
        </div>
      </aside>

      {/* ── Main ── */}
      <main style={{ flex: 1, minWidth: 0, overflowY: 'auto', overflowX: 'hidden' }}>
        <Outlet />
      </main>
    </div>
  )
}
