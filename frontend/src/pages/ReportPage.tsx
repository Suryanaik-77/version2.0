import React from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { sessionApi } from '@/lib/api'
import { Card, Badge, Skeleton, ScoreBar, MonoLabel, ModeTag, Divider, PageContainer } from '@/components/ui'
import { format } from 'date-fns'

const HIRING_CONFIG = {
  strong:   { label: 'Strong Hire',  bg: '#F0FDF4', border: '#BBF7D0', color: '#15803D', text: 'Recommendation: proceed to offer stage.' },
  moderate: { label: 'Moderate',     bg: '#FFFBEB', border: '#FDE68A', color: '#B45309', text: 'Some gaps identified. Additional evaluation recommended.' },
  weak:     { label: 'Weak',         bg: '#FFF7ED', border: '#FED7AA', color: '#C2410C', text: 'Significant gaps. Not recommended without further development.' },
  no:       { label: 'No Hire',      bg: '#FEF2F2', border: '#FECACA', color: '#B91C1C', text: 'Insufficient technical depth for this role.' },
}

export default function ReportPage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['session-report', sessionId],
    queryFn: () => sessionApi.report(sessionId!).then(r => r.data),
    enabled: !!sessionId,
    staleTime: 60_000,
    retry: 1,
  })

  if (isLoading) return <ReportSkeleton />
  if (isError || !data) return (
    <PageContainer>
      <button onClick={() => navigate('/dashboard')} style={backBtnStyle}>← Dashboard</button>
      <Card style={{ textAlign: 'center', padding: '60px 32px', marginTop: 32 }}>
        <p style={{ fontFamily: 'var(--font-display)', fontSize: 20, marginBottom: 8 }}>Report unavailable</p>
        <p style={{ fontSize: 13, color: 'var(--text-2)' }}>
          This session may still be processing or the report has not been generated yet.
        </p>
      </Card>
    </PageContainer>
  )

  const { session, report, turns } = data
  const hiring = HIRING_CONFIG[report?.hiring_signal as keyof typeof HIRING_CONFIG]
  const started = session.started_at ? format(new Date(session.started_at), 'MMM d, yyyy · h:mm a') : '—'
  const duration = session.duration_secs ? `${Math.round(session.duration_secs / 60)} min` : '—'

  return (
    <PageContainer>
      {/* Back */}
      <button onClick={() => navigate('/dashboard')} style={backBtnStyle}>← Dashboard</button>

      {/* Header */}
      <div style={{ margin: '20px 0 32px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 32 }}>
        <div>
          <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 32, color: 'var(--text-0)', marginBottom: 8 }}>
            Interview Report
          </h1>
          <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
            <MonoLabel style={{ color: 'var(--text-1)' }}>
              {session.domain?.replace(/_/g, ' ')}
            </MonoLabel>
            <span style={{ color: 'var(--text-4)', fontSize: 10 }}>·</span>
            <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{started}</span>
            <span style={{ color: 'var(--text-4)', fontSize: 10 }}>·</span>
            <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{duration}</span>
            <span style={{ color: 'var(--text-4)', fontSize: 10 }}>·</span>
            <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{session.total_turns} turns</span>
          </div>
        </div>

        {hiring && (
          <div style={{
            padding: '14px 20px', borderRadius: 'var(--r-lg)',
            background: hiring.bg, border: `1px solid ${hiring.border}`,
            textAlign: 'center', flexShrink: 0, minWidth: 160,
          }}>
            <MonoLabel style={{ color: hiring.color, display: 'block', marginBottom: 4 }}>Hiring signal</MonoLabel>
            <p style={{ fontSize: 17, fontWeight: 600, color: hiring.color, fontFamily: 'var(--font-display)' }}>
              {hiring.label}
            </p>
          </div>
        )}
      </div>

      {/* Two-column layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 290px', gap: 20, alignItems: 'start' }}>

        {/* Left: summary + transcript */}
        <div>
          {/* Summary card */}
          {(report?.strength_summary || report?.weakness_summary) && (
            <Card style={{ marginBottom: 16 }}>
              <MonoLabel style={{ display: 'block', marginBottom: 16 }}>Evaluation summary</MonoLabel>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
                {report.strength_summary && (
                  <div>
                    <p style={{ fontSize: 10, fontFamily: 'var(--font-mono)', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--green)', marginBottom: 8 }}>
                      Strengths
                    </p>
                    <p style={{ fontSize: 13, color: 'var(--text-1)', lineHeight: 1.7 }}>{report.strength_summary}</p>
                  </div>
                )}
                {report.weakness_summary && (
                  <div>
                    <p style={{ fontSize: 10, fontFamily: 'var(--font-mono)', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--yellow)', marginBottom: 8 }}>
                      Areas to develop
                    </p>
                    <p style={{ fontSize: 13, color: 'var(--text-1)', lineHeight: 1.7 }}>{report.weakness_summary}</p>
                  </div>
                )}
              </div>
              {hiring?.text && (
                <>
                  <Divider style={{ margin: '16px 0' }} />
                  <p style={{ fontSize: 12, color: 'var(--text-2)', fontStyle: 'italic' }}>{hiring.text}</p>
                </>
              )}
            </Card>
          )}

          {/* Transcript */}
          <MonoLabel style={{ display: 'block', marginBottom: 12 }}>Full transcript</MonoLabel>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {turns?.length ? turns.map((turn: any) => (
              <TurnCard key={turn.turn_number} turn={turn} />
            )) : (
              <Card style={{ textAlign: 'center', padding: '40px' }}>
                <p style={{ color: 'var(--text-3)', fontSize: 13 }}>No turns recorded for this session.</p>
              </Card>
            )}
          </div>
        </div>

        {/* Right: scores sidebar */}
        <div style={{ position: 'sticky', top: 24 }}>
          <Card style={{ marginBottom: 14 }}>
            <MonoLabel style={{ display: 'block', marginBottom: 20 }}>Score breakdown</MonoLabel>

            {report?.overall_score != null && (
              <>
                <div style={{ textAlign: 'center', marginBottom: 20 }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 44, color: 'var(--text-0)', fontWeight: 300, lineHeight: 1 }}>
                    {Number(report.overall_score).toFixed(1)}
                  </span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: 'var(--text-3)' }}> / 10</span>
                  <p style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 6 }}>Overall score</p>
                </div>
                <Divider style={{ marginBottom: 20 }} />
              </>
            )}

            {report?.dimension_scores && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {Object.entries(report.dimension_scores)
                  .filter(([, v]) => v != null)
                  .map(([k, v]) => (
                    <ScoreBar
                      key={k}
                      label={k.charAt(0).toUpperCase() + k.slice(1)}
                      score={Number(v)}
                    />
                  ))}
              </div>
            )}
          </Card>

          <button
            onClick={() => navigate('/dashboard')}
            style={{
              width: '100%', background: 'var(--bg-0)', color: 'var(--text-1)',
              border: '1px solid var(--border-2)', borderRadius: 'var(--r-md)',
              padding: '9px 0', fontSize: 12, fontFamily: 'var(--font-body)',
              cursor: 'pointer', fontWeight: 500,
            }}
          >
            Back to dashboard
          </button>
        </div>
      </div>
    </PageContainer>
  )
}

function TurnCard({ turn }: { turn: any }) {
  const avg = turn.avg_score
  return (
    <div style={{
      background: 'var(--bg-0)', border: '1px solid var(--border-1)',
      borderRadius: 'var(--r-lg)', padding: '18px 22px',
      boxShadow: 'var(--shadow-xs)',
    }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center' }}>
        <MonoLabel>Turn {turn.turn_number}</MonoLabel>
        {turn.mode && <ModeTag mode={turn.mode} />}
        {avg != null && (
          <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-3)' }}>
            {Number(avg).toFixed(1)}/10
          </span>
        )}
      </div>

      <p style={{ fontSize: 10, fontFamily: 'var(--font-mono)', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--accent-dim)', marginBottom: 7 }}>
        Interviewer
      </p>
      <p style={{ fontSize: 14, color: 'var(--text-0)', lineHeight: 1.7, marginBottom: 16 }}>
        {turn.question || '—'}
      </p>

      <p style={{ fontSize: 10, fontFamily: 'var(--font-mono)', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-3)', marginBottom: 7 }}>
        Candidate
      </p>
      <p style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.7 }}>
        {turn.answer || <em style={{ color: 'var(--text-4)' }}>No response recorded</em>}
      </p>
    </div>
  )
}

const backBtnStyle: React.CSSProperties = {
  background: 'none', border: 'none', cursor: 'pointer',
  color: 'var(--text-3)', fontSize: 13, padding: 0,
  display: 'flex', alignItems: 'center', gap: 4,
  transition: 'color var(--dur-fast)', fontFamily: 'var(--font-body)',
}

function ReportSkeleton() {
  return (
    <PageContainer>
      <Skeleton w={80} h={14} style={{ marginBottom: 32 }} />
      <Skeleton w={280} h={32} style={{ marginBottom: 12 }} />
      <Skeleton w={320} h={14} style={{ marginBottom: 40 }} />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 290px', gap: 20 }}>
        <div>
          <Skeleton h={140} style={{ marginBottom: 12, borderRadius: 12 }} />
          {[1,2,3].map(i => <Skeleton key={i} h={160} style={{ marginBottom: 8, borderRadius: 12 }} />)}
        </div>
        <Skeleton h={320} style={{ borderRadius: 12 }} />
      </div>
    </PageContainer>
  )
}
