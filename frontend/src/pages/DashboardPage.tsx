import React from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { sessionApi } from '@/lib/api'
import { useAuth } from '@/stores/auth'
import { Card, Badge, Skeleton, EmptyState, MonoLabel, PageContainer, SectionHeader, StatCard } from '@/components/ui'
import { formatDistanceToNow } from 'date-fns'

type Domain = 'ANALOG_LAYOUT' | 'PHYSICAL_DESIGN' | 'DESIGN_VERIFICATION'

const DOMAIN_META: Record<Domain, { label: string; description: string; color: string }> = {
  ANALOG_LAYOUT:       { label: 'Analog Layout',      description: 'Device matching, OTA layout, DRC/LVS',        color: 'var(--accent)' },
  PHYSICAL_DESIGN:     { label: 'Physical Design',     description: 'Floorplan, CTS, timing closure, IR drop',     color: 'var(--blue)' },
  DESIGN_VERIFICATION: { label: 'Design Verification', description: 'UVM, coverage closure, formal verification',  color: 'var(--green)' },
}

const HIRING_CONFIG = {
  strong:   { label: 'Strong hire', color: 'var(--green)', bg: 'var(--green-bg)', border: 'var(--green-border)' },
  moderate: { label: 'Moderate',    color: 'var(--yellow)', bg: 'var(--yellow-bg)', border: 'var(--yellow-border)' },
  weak:     { label: 'Weak',        color: 'var(--accent-dim)', bg: 'var(--accent-8)', border: 'var(--accent-15)' },
  no:       { label: 'No hire',     color: 'var(--red)', bg: 'var(--red-bg)', border: 'var(--red-border)' },
}

export default function DashboardPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['sessions'],
    queryFn: () => sessionApi.list().then(r => r.data),
    staleTime: 30_000,
  })

  const sessions = data?.sessions || []
  const completed = sessions.filter((s: any) => s.status === 'completed')
  const avgScore = completed.length
    ? (completed.reduce((a: number, s: any) => a + (s.avg_score || 0), 0) / completed.length).toFixed(1)
    : '—'

  const firstName = user?.full_name?.split(' ')[0] || 'there'

  return (
    <PageContainer>
      {/* ── Greeting ── */}
      <div style={{ marginBottom: 36 }}>
        <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 30, color: 'var(--text-0)', marginBottom: 4 }}>
          Good to see you, {firstName}
        </h1>
        <p style={{ fontSize: 13, color: 'var(--text-2)' }}>
          Run an interview or review your previous sessions below.
        </p>
      </div>

      {/* ── Stats row ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 32 }}>
        <StatCard label="Total sessions" value={sessions.length} />
        <StatCard label="Completed" value={completed.length} />
        <StatCard label="Avg score" value={avgScore} unit="/10" />
      </div>

      {/* ── Start interview CTA ── */}
      <Card style={{ marginBottom: 32, padding: '28px 32px' }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 22, color: 'var(--text-0)', marginBottom: 6 }}>
          Start a new interview
        </h2>
        <p style={{ fontSize: 13, color: 'var(--text-2)', marginBottom: 20, maxWidth: 520 }}>
          Upload your resume, preview the parsed details, and start a personalized technical interview.
        </p>

        <button
          onClick={() => navigate('/interview')}
          style={{
            display: 'flex', alignItems: 'center', gap: 8,
            background: 'var(--accent)', color: '#fff', border: 'none',
            borderRadius: 'var(--r-md)', padding: '11px 28px',
            fontSize: 14, fontFamily: 'var(--font-body)', fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M7 1v12M1 7h12" stroke="white" strokeWidth="2" strokeLinecap="round"/>
          </svg>
          New Interview
        </button>
      </Card>

      {/* ── Session history ── */}
      <SectionHeader
        title="Previous sessions"
        subtitle={isLoading ? undefined : `${sessions.length} total`}
      />

      {isLoading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {[1,2,3].map(i => <Skeleton key={i} h={64} style={{ borderRadius: 12 }} />)}
        </div>
      ) : !sessions.length ? (
        <Card>
          <EmptyState
            icon="🎯"
            title="No sessions yet"
            body="Your completed interview sessions will appear here with scores and hiring signals."
          />
        </Card>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {sessions.map((s: any) => <SessionRow key={s.id} session={s} />)}
        </div>
      )}
    </PageContainer>
  )
}

function SessionRow({ session }: { session: any }) {
  const navigate = useNavigate()
  const d = DOMAIN_META[session.domain as Domain] || { label: session.domain, color: 'var(--text-3)' }
  const h = HIRING_CONFIG[session.report?.hiring_signal as keyof typeof HIRING_CONFIG]
  const when = session.ended_at
    ? formatDistanceToNow(new Date(session.ended_at), { addSuffix: true })
    : 'Active'
  const canClick = session.status === 'completed'

  return (
    <div
      onClick={() => canClick && navigate(`/report/${session.id}`)}
      role={canClick ? 'button' : undefined}
      tabIndex={canClick ? 0 : undefined}
      onKeyDown={e => e.key === 'Enter' && canClick && navigate(`/report/${session.id}`)}
      style={{
        display: 'flex', alignItems: 'center', gap: 14,
        padding: '13px 18px',
        background: 'var(--bg-0)', border: '1px solid var(--border-1)',
        borderRadius: 'var(--r-lg)', cursor: canClick ? 'pointer' : 'default',
        transition: 'border-color var(--dur-fast)',
        boxShadow: 'var(--shadow-xs)',
      }}
      onMouseEnter={e => canClick && (e.currentTarget.style.borderColor = 'var(--border-3)')}
      onMouseLeave={e => (e.currentTarget.style.borderColor = 'var(--border-1)')}
    >
      {/* Status dot */}
      <span style={{
        width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
        background: session.status === 'active' ? 'var(--green)' : session.status === 'completed' ? 'var(--text-4)' : 'var(--text-4)',
        animation: session.status === 'active' ? 'pulse 1.5s infinite' : undefined,
      }} />

      {/* Domain */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, flex: '0 0 200px' }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: d.color, flexShrink: 0 }} />
        <span style={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 400 }}>{d.label}</span>
      </div>

      {/* Status */}
      <Badge variant={session.status === 'active' ? 'green' : 'gray'} dot={session.status === 'active'}>
        {session.status}
      </Badge>

      <div style={{ flex: 1 }} />

      {/* Turns */}
      {session.total_turns != null && (
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-3)' }}>
          {session.total_turns} turn{session.total_turns !== 1 ? 's' : ''}
        </span>
      )}

      {/* Score */}
      {session.avg_score != null && (
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-0)', minWidth: 42, textAlign: 'right', fontWeight: 500 }}>
          {Number(session.avg_score).toFixed(1)}<span style={{ color: 'var(--text-4)' }}>/10</span>
        </span>
      )}

      {/* Hiring signal */}
      {h && (
        <span style={{
          fontSize: 10, fontFamily: 'var(--font-mono)', letterSpacing: '0.06em', textTransform: 'uppercase',
          color: h.color, background: h.bg, border: `1px solid ${h.border}`,
          padding: '3px 9px', borderRadius: 'var(--r-full)', flexShrink: 0,
        }}>
          {h.label}
        </span>
      )}

      {/* When */}
      <span style={{ fontSize: 11, color: 'var(--text-4)', minWidth: 110, textAlign: 'right' }}>
        {when}
      </span>

      {canClick && (
        <span style={{ color: 'var(--text-4)', fontSize: 13 }}>→</span>
      )}
    </div>
  )
}
