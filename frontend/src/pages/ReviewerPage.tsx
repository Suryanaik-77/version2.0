import React, { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { reviewerApi } from '@/lib/api'
import { Button, Card, Badge, Skeleton, ScoreBar, MonoLabel, ModeTag, EmptyState, Divider } from '@/components/ui'
import { toast } from '@/hooks/useToast'
import { formatDistanceToNow } from 'date-fns'

export default function ReviewerPage() {
  const { sessionId } = useParams<{ sessionId?: string }>()
  const navigate = useNavigate()
  const [activeSessionId, setActiveSessionId] = useState<string | null>(sessionId || null)
  const [tab, setTab] = useState<'transcript' | 'integrity' | 'notes'>('transcript')

  const { data: queue, isLoading: queueLoading } = useQuery({
    queryKey: ['reviewer-queue'],
    queryFn: () => reviewerApi.queue().then(r => r.data?.queue || []),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  const { data: transcript, isLoading: txLoading } = useQuery({
    queryKey: ['reviewer-transcript', activeSessionId],
    queryFn: () => reviewerApi.transcript(activeSessionId!).then(r => r.data),
    enabled: !!activeSessionId,
    staleTime: 60_000,
  })

  const { data: integrity } = useQuery({
    queryKey: ['reviewer-integrity', activeSessionId],
    queryFn: () => reviewerApi.integrity(activeSessionId!).then(r => r.data),
    enabled: !!activeSessionId && tab === 'integrity',
    staleTime: 120_000,
  })

  const selectSession = (id: string) => {
    setActiveSessionId(id)
    setTab('transcript')
    navigate(`/reviewer/session/${id}`, { replace: true })
  }

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: 'var(--bg-1)' }}>

      {/* Queue sidebar */}
      <aside style={{
        width: 320, flexShrink: 0,
        background: 'var(--bg-0)', borderRight: '1px solid var(--border-0)',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <div style={{ padding: '20px 20px 14px', borderBottom: '1px solid var(--border-0)' }}>
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 18, color: 'var(--text-0)', marginBottom: 4 }}>Review Queue</h2>
          <MonoLabel>{queue?.length ?? '—'} sessions pending</MonoLabel>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {queueLoading ? (
            <div style={{ padding: 16 }}>
              {[1,2,3,4].map(i => <Skeleton key={i} h={72} style={{ marginBottom: 8, borderRadius: 8 }} />)}
            </div>
          ) : !queue?.length ? (
            <EmptyState icon="✓" title="Queue clear" body="All sessions have been reviewed." />
          ) : (
            queue.map((s: any) => (
              <QueueItem
                key={s.session_id}
                session={s}
                isActive={s.session_id === activeSessionId}
                onClick={() => selectSession(s.session_id)}
              />
            ))
          )}
        </div>
      </aside>

      {/* Main review area */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {!activeSessionId ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <EmptyState icon="📋" title="Select a session" body="Choose a session from the queue to begin review." />
          </div>
        ) : (
          <>
            {/* Tab bar */}
            <div style={{
              background: 'var(--bg-0)', borderBottom: '1px solid var(--border-0)',
              display: 'flex', alignItems: 'center', padding: '0 24px', height: 48,
            }}>
              {(['transcript', 'integrity', 'notes'] as const).map(t => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    height: 48, padding: '0 16px', fontSize: 13,
                    color: tab === t ? 'var(--accent-dim)' : 'var(--text-2)',
                    borderBottom: `2px solid ${tab === t ? 'var(--accent)' : 'transparent'}`,
                    fontFamily: 'var(--font-body)',
                    transition: 'all var(--dur-std) var(--ease-std)',
                  }}
                >
                  {t.charAt(0).toUpperCase() + t.slice(1)}
                </button>
              ))}
              <div style={{ marginLeft: 'auto' }}>
                <ApproveControls sessionId={activeSessionId} transcript={transcript} />
              </div>
            </div>

            {/* Tab content */}
            <div style={{ flex: 1, overflowY: 'auto', padding: 28 }}>
              {tab === 'transcript' && (
                txLoading
                  ? <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                      {[1,2,3].map(i => <Skeleton key={i} h={120} style={{ borderRadius: 12 }} />)}
                    </div>
                  : <TranscriptView transcript={transcript} />
              )}
              {tab === 'integrity' && <IntegrityView integrity={integrity} sessionId={activeSessionId} />}
              {tab === 'notes' && <NotesView sessionId={activeSessionId} transcript={transcript} />}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Queue item ─────────────────────────────────────────────────────────────────

function QueueItem({ session, isActive, onClick }: { session: any; isActive: boolean; onClick: () => void }) {
  const domain = session.domain?.replace(/_/g, ' ')
  const when = session.ended_at ? formatDistanceToNow(new Date(session.ended_at), { addSuffix: true }) : ''
  return (
    <div
      onClick={onClick}
      style={{
        padding: '14px 20px', cursor: 'pointer',
        background: isActive ? 'var(--accent-8)' : 'transparent',
        borderLeft: `2px solid ${isActive ? 'var(--accent)' : 'transparent'}`,
        borderBottom: '1px solid var(--border-0)',
        transition: 'all var(--dur-std) var(--ease-std)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-1)' }}>{domain}</span>
        {session.overall_score != null && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-3)', marginLeft: 'auto' }}>
            {Number(session.overall_score).toFixed(1)}
          </span>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <Badge variant={session.review_status === 'pending' ? 'gray' : 'green'}>
          {session.review_status}
        </Badge>
        <span style={{ fontSize: 11, color: 'var(--text-3)', marginLeft: 'auto' }}>{when}</span>
      </div>
    </div>
  )
}

// ── Transcript view ─────────────────────────────────────────────────────────────

function TranscriptView({ transcript }: { transcript: any }) {
  if (!transcript) return null
  const { turns, report } = transcript
  return (
    <div style={{ maxWidth: 800 }}>
      {report && (
        <Card style={{ marginBottom: 20 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 220px', gap: 24 }}>
            <div>
              {report.strength_summary && (
                <div style={{ marginBottom: 12 }}>
                  <MonoLabel style={{ color: 'var(--green)' }}>Strengths</MonoLabel>
                  <p style={{ fontSize: 13, color: 'var(--text-1)', lineHeight: 1.65, marginTop: 6 }}>{report.strength_summary}</p>
                </div>
              )}
              {report.weakness_summary && (
                <div>
                  <MonoLabel style={{ color: 'var(--yellow)' }}>Gaps identified</MonoLabel>
                  <p style={{ fontSize: 13, color: 'var(--text-1)', lineHeight: 1.65, marginTop: 6 }}>{report.weakness_summary}</p>
                </div>
              )}
            </div>
            {report.dimension_scores && (
              <div>
                <MonoLabel style={{ display: 'block', marginBottom: 14 }}>Dimensions</MonoLabel>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {Object.entries(report.dimension_scores).filter(([, v]) => v != null).map(([k, v]) => (
                    <ScoreBar key={k} label={k.charAt(0).toUpperCase() + k.slice(1)} score={Number(v)} />
                  ))}
                </div>
              </div>
            )}
          </div>
        </Card>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {turns?.map((turn: any) => (
          <div key={turn.turn_number} style={{
            background: 'var(--bg-0)', border: '1px solid var(--border-1)',
            borderRadius: 'var(--r-lg)', padding: '18px 22px',
          }}>
            <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center' }}>
              <MonoLabel>Turn {turn.turn_number}</MonoLabel>
              <ModeTag mode={turn.mode || 'PROBING'} />
              {turn.avg_score != null && (
                <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-3)' }}>
                  {Number(turn.avg_score).toFixed(1)}/10
                </span>
              )}
            </div>
            <p style={{ fontSize: 10, fontFamily: 'var(--font-mono)', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--accent-dim)', marginBottom: 6 }}>Q</p>
            <p style={{ fontSize: 14, color: 'var(--text-0)', lineHeight: 1.65, marginBottom: 14 }}>{turn.question}</p>
            <p style={{ fontSize: 10, fontFamily: 'var(--font-mono)', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-3)', marginBottom: 6 }}>A</p>
            <p style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.7 }}>{turn.answer}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Integrity view ──────────────────────────────────────────────────────────────

function IntegrityView({ integrity, sessionId }: { integrity: any; sessionId: string }) {
  if (!integrity) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200 }}>
      <Skeleton w={200} h={14} />
    </div>
  )
  if (integrity.no_data) return (
    <EmptyState icon="🔍" title="No integrity data" body="This session has no anti-cheat events recorded." />
  )

  const score = integrity.integrity_score ?? 100
  const scoreColor = score >= 80 ? 'var(--green)' : score >= 60 ? 'var(--yellow)' : 'var(--red)'

  return (
    <div style={{ maxWidth: 700 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 24 }}>
        <Card>
          <MonoLabel style={{ display: 'block', marginBottom: 12 }}>Integrity Score</MonoLabel>
          <div style={{ textAlign: 'center' }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 40, color: scoreColor }}>{score}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 16, color: 'var(--text-3)' }}>/100</span>
            <p style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 4 }}>
              Confidence: {integrity.confidence || 'low'}
            </p>
          </div>
        </Card>

        <Card>
          <MonoLabel style={{ display: 'block', marginBottom: 14 }}>Behavioral signals</MonoLabel>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <SignalRow label="Tab switches" value={integrity.summary?.tab_switches} warn={2} />
            <SignalRow label="Clipboard events" value={integrity.summary?.clipboard_events} warn={1} />
            <SignalRow label="Focus losses" value={integrity.summary?.focus_losses} warn={3} />
            <SignalRow label="DevTools detected" value={integrity.summary?.devtools_detected ? 'Yes' : 'No'} warn={-1} flagString="Yes" />
          </div>
        </Card>
      </div>

      {integrity.ai_signals && (
        <Card style={{ marginBottom: 16 }}>
          <MonoLabel style={{ display: 'block', marginBottom: 14 }}>AI-assistance signals</MonoLabel>
          <div style={{ display: 'flex', gap: 24 }}>
            <div>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-3)', marginBottom: 4 }}>PATTERN SCORE</p>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: 18, color: integrity.ai_signals.pattern_score > 0.6 ? 'var(--yellow)' : 'var(--text-1)' }}>
                {integrity.ai_signals.pattern_score != null ? `${(integrity.ai_signals.pattern_score * 100).toFixed(0)}%` : '—'}
              </p>
              <p style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 3 }}>AI-like phrasing probability</p>
            </div>
            <div>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-3)', marginBottom: 4 }}>VOCAB DIVERSITY</p>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: 18, color: 'var(--text-1)' }}>
                {integrity.ai_signals.vocabulary_diversity != null ? `${(integrity.ai_signals.vocabulary_diversity * 100).toFixed(0)}%` : '—'}
              </p>
              <p style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 3 }}>Type-token ratio</p>
            </div>
            <div>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-3)', marginBottom: 4 }}>SPEED ANOMALY</p>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: 18, color: integrity.ai_signals.answer_speed_anomaly ? 'var(--yellow)' : 'var(--green)' }}>
                {integrity.ai_signals.answer_speed_anomaly ? 'Detected' : 'Normal'}
              </p>
              <p style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 3 }}>Response timing</p>
            </div>
          </div>
        </Card>
      )}

      <Card>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <MonoLabel>Reviewer verdict</MonoLabel>
          {integrity.verdict && <Badge variant={integrity.verdict === 'clean' ? 'green' : integrity.verdict === 'suspicious' ? 'red' : 'gray'}>{integrity.verdict}</Badge>}
        </div>
        <p style={{ fontSize: 12, color: 'var(--text-3)' }}>
          These signals are probabilistic indicators only. Reviewer judgment is required before any action.
        </p>
      </Card>
    </div>
  )
}

function SignalRow({ label, value, warn, flagString }: { label: string; value: any; warn: number; flagString?: string }) {
  const isWarn = warn === -1 ? value === flagString : typeof value === 'number' && value >= warn
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <span style={{ fontSize: 13, color: 'var(--text-2)' }}>{label}</span>
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 12,
        color: isWarn ? 'var(--yellow)' : 'var(--text-1)',
        fontWeight: isWarn ? 500 : 400,
      }}>
        {String(value ?? '—')}
      </span>
    </div>
  )
}

// ── Notes view ─────────────────────────────────────────────────────────────────

function NotesView({ sessionId, transcript }: { sessionId: string; transcript: any }) {
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const [noteType, setNoteType] = useState<'general' | 'flag' | 'correction' | 'commendation'>('general')

  const addNote = useMutation({
    mutationFn: () => reviewerApi.addNote(sessionId, { note_text: text, note_type: noteType }),
    onSuccess: () => {
      setText('')
      qc.invalidateQueries({ queryKey: ['reviewer-transcript', sessionId] })
      toast.success('Note added')
    },
    onError: () => toast.error('Failed to save note'),
  })

  const existingNotes = transcript?.notes || []

  return (
    <div style={{ maxWidth: 700 }}>
      <Card style={{ marginBottom: 20 }}>
        <MonoLabel style={{ display: 'block', marginBottom: 14 }}>Add note</MonoLabel>
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder="Write a note for this session..."
          style={{
            width: '100%', minHeight: 80, background: 'var(--bg-1)',
            border: '1px solid var(--border-2)', borderRadius: 'var(--r-md)',
            padding: '10px 14px', fontFamily: 'var(--font-body)', fontSize: 13,
            color: 'var(--text-0)', resize: 'vertical', outline: 'none',
            marginBottom: 12, boxSizing: 'border-box',
          }}
        />
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <select
            value={noteType}
            onChange={e => setNoteType(e.target.value as any)}
            style={{
              background: 'var(--bg-0)', border: '1px solid var(--border-2)',
              borderRadius: 'var(--r-md)', padding: '7px 12px',
              fontSize: 12, color: 'var(--text-1)', fontFamily: 'var(--font-mono)',
              cursor: 'pointer', outline: 'none',
            }}
          >
            <option value="general">General</option>
            <option value="flag">Flag</option>
            <option value="correction">Correction</option>
            <option value="commendation">Commendation</option>
          </select>
          <Button
            variant="primary" size="sm"
            loading={addNote.isPending}
            disabled={!text.trim()}
            onClick={() => addNote.mutate()}
          >
            Save note
          </Button>
        </div>
      </Card>

      {existingNotes.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {existingNotes.map((note: any) => (
            <Card key={note.id} style={{ padding: '14px 18px' }}>
              <div style={{ display: 'flex', gap: 10, marginBottom: 8 }}>
                <Badge variant={note.note_type === 'flag' ? 'red' : note.note_type === 'commendation' ? 'green' : 'gray'}>
                  {note.note_type}
                </Badge>
                {note.turn_number && <MonoLabel>Turn {note.turn_number}</MonoLabel>}
              </div>
              <p style={{ fontSize: 13, color: 'var(--text-1)', lineHeight: 1.65 }}>{note.note_text}</p>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Approve controls ────────────────────────────────────────────────────────────

function ApproveControls({ sessionId, transcript }: { sessionId: string; transcript: any }) {
  const qc = useQueryClient()
  const reviewStatus = transcript?.report?.review_status

  const approve = useMutation({
    mutationFn: () => reviewerApi.approve(sessionId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['reviewer-queue'] }); toast.success('Session approved') },
    onError: () => toast.error('Failed to approve'),
  })

  const flag = useMutation({
    mutationFn: () => reviewerApi.flag(sessionId, { flag_type: 'quality', reason: 'Flagged for follow-up review' }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['reviewer-queue'] }); toast.warning('Session flagged') },
    onError: () => toast.error('Failed to flag'),
  })

  if (reviewStatus === 'approved') {
    return <Badge variant="green" dot>Approved</Badge>
  }

  return (
    <div style={{ display: 'flex', gap: 8 }}>
      <Button variant="ghost" size="sm" loading={flag.isPending} onClick={() => flag.mutate()}>Flag</Button>
      <Button variant="primary" size="sm" loading={approve.isPending} onClick={() => approve.mutate()}>Approve</Button>
    </div>
  )
}
