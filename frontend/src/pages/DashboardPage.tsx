import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { sessionApi } from '@/lib/api'
import { useAuth } from '@/stores/auth'
import { Card, Badge, Skeleton, EmptyState, MonoLabel, PageContainer, SectionHeader, StatCard } from '@/components/ui'
import { toast } from '@/hooks/useToast'
import { formatDistanceToNow, format } from 'date-fns'

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
  const [selectedDomain, setSelectedDomain] = useState<Domain>('ANALOG_LAYOUT')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [resumeText, setResumeText] = useState('')
  const [resumeFile, setResumeFile] = useState<File | null>(null)
  const [resumeLoading, setResumeLoading] = useState(false)

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

  const create = useMutation({
    mutationFn: ({ domain, resume }: { domain: Domain; resume: string }) =>
      sessionApi.create(domain, resume),
    onSuccess: res => {
      const sid = res.data?.session_id || res.data?.id
      if (sid) { qc.invalidateQueries({ queryKey: ['sessions'] }); navigate(`/interview/${sid}`) }
    },
    onError: () => toast.error('Could not create session. Please try again.'),
  })

  const handleResumeFile = async (file: File) => {
    setResumeFile(file)
    setResumeLoading(true)
    try {
      const text = await file.text()
      setResumeText(text)
    } catch {
      toast.error('Could not read file')
    }
    setResumeLoading(false)
  }

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
          Upload your resume and select a domain. The interviewer will personalize questions based on your skills and projects.
        </p>

        {/* Resume upload */}
        <div style={{ marginBottom: 20 }}>
          <label style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
            padding: '20px', border: '2px dashed var(--border-2)', borderRadius: 'var(--r-lg)',
            cursor: 'pointer', background: resumeText ? 'var(--green-bg)' : 'var(--bg-1)',
            borderColor: resumeText ? 'var(--green-border)' : 'var(--border-2)',
            transition: 'all var(--dur-std)',
          }}>
            <input
              type="file"
              accept=".txt,.pdf,.doc,.docx"
              style={{ display: 'none' }}
              onChange={e => e.target.files?.[0] && handleResumeFile(e.target.files[0])}
            />
            {resumeLoading ? (
              <span style={{ fontSize: 13, color: 'var(--text-2)' }}>Reading file...</span>
            ) : resumeText ? (
              <span style={{ fontSize: 13, color: 'var(--green)' }}>
                {resumeFile?.name || 'Resume uploaded'} ({Math.round(resumeText.length / 1024)}KB)
              </span>
            ) : (
              <span style={{ fontSize: 13, color: 'var(--text-3)' }}>
                Drop resume here or click to upload (.txt, .pdf)
              </span>
            )}
          </label>
          {!resumeText && (
            <p style={{ fontSize: 11, color: 'var(--text-4)', marginTop: 6, textAlign: 'center' }}>
              Resume helps the interviewer ask relevant questions about your experience
            </p>
          )}
        </div>

        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {/* Domain picker */}
          <div style={{ position: 'relative', flex: 1 }}>
            <button
              onClick={() => setPickerOpen(v => !v)}
              style={{
                display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                background: 'var(--bg-1)', border: '1px solid var(--border-2)',
                borderRadius: 'var(--r-md)', padding: '9px 14px',
                fontSize: 13, color: 'var(--text-1)', cursor: 'pointer',
                fontFamily: 'var(--font-body)',
                transition: 'border-color var(--dur-fast)',
              }}
            >
              <span style={{
                width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                background: DOMAIN_META[selectedDomain].color,
              }} />
              {DOMAIN_META[selectedDomain].label}
              <span style={{ marginLeft: 'auto', color: 'var(--text-3)', fontSize: 10 }}>▾</span>
            </button>

            {pickerOpen && (
              <div style={{
                position: 'absolute', top: 'calc(100% + 6px)', left: 0, right: 0,
                background: 'var(--bg-0)', border: '1px solid var(--border-1)',
                borderRadius: 'var(--r-lg)', boxShadow: 'var(--shadow-md)', zIndex: 50,
                overflow: 'hidden',
              }}>
                {(Object.keys(DOMAIN_META) as Domain[]).map(d => (
                  <button
                    key={d}
                    onClick={() => { setSelectedDomain(d); setPickerOpen(false) }}
                    style={{
                      display: 'block', width: '100%', textAlign: 'left',
                      padding: '11px 16px',
                      background: d === selectedDomain ? 'var(--bg-2)' : 'transparent',
                      border: 'none', cursor: 'pointer', fontFamily: 'var(--font-body)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <span style={{ width: 7, height: 7, borderRadius: '50%', background: DOMAIN_META[d].color, flexShrink: 0 }} />
                      <div>
                        <p style={{ fontSize: 13, color: 'var(--text-0)', fontWeight: d === selectedDomain ? 500 : 400 }}>{DOMAIN_META[d].label}</p>
                        <p style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 1 }}>{DOMAIN_META[d].description}</p>
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          <button
            disabled={create.isPending || !resumeText}
            onClick={() => create.mutate({ domain: selectedDomain, resume: resumeText })}
            style={{
              display: 'flex', alignItems: 'center', gap: 7,
              background: (create.isPending || !resumeText) ? 'var(--text-3)' : 'var(--accent)',
              color: '#fff', border: 'none', borderRadius: 'var(--r-md)',
              padding: '9px 24px', fontSize: 13, fontFamily: 'var(--font-body)',
              fontWeight: 500, cursor: (create.isPending || !resumeText) ? 'not-allowed' : 'pointer',
              flexShrink: 0,
            }}
          >
            {create.isPending ? (
              <span style={{ width: 13, height: 13, border: '1.5px solid rgba(255,255,255,0.4)', borderTopColor: '#fff', borderRadius: '50%', display: 'inline-block', animation: 'spin 0.65s linear infinite' }} />
            ) : null}
            {resumeText ? 'Begin Interview' : 'Upload Resume First'}
          </button>
        </div>
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
