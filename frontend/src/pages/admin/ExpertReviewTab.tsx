/**
 * ExpertReviewTab.tsx — Per-turn expert review with 3-section form.
 * Matches monolith: Section A (read-only AI eval), Section B (expert correction),
 * Section C (verdict). Left panel = question queue with progress.
 */
import React, { useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { reviewerApi } from '@/lib/api'
import { Button, Card, Badge, MonoLabel, EmptyState } from '@/components/ui'
import { toast } from '@/hooks/useToast'

const DIMS = ['Technical Accuracy', 'Depth & Completeness', 'Clarity', 'Level Calibration']
const FLAGS = [
  { id: 'missed', l: 'Missed concept', w: false },
  { id: 'overscored', l: 'Score too high', w: true },
  { id: 'underscored', l: 'Score too low', w: false },
  { id: 'halluc', l: 'Hallucination', w: false },
  { id: 'vague', l: 'Vague feedback', w: true },
  { id: 'level', l: 'Level mismatch', w: true },
]
const BEH = [
  { k: 'reas', l: 'Reasoning', o: [{ v: 'clear', c: 'ok', t: 'Clear' }, { v: 'weak', c: 'warn', t: 'Weak' }, { v: 'flawed', c: 'err', t: 'Flawed' }] },
  { k: 'feed', l: 'Feedback', o: [{ v: 'actionable', c: 'ok', t: 'Actionable' }, { v: 'partial', c: 'warn', t: 'Partial' }, { v: 'vague', c: 'err', t: 'Vague' }] },
  { k: 'cal', l: 'Calibration', o: [{ v: 'appropriate', c: 'ok', t: 'Appropriate' }, { v: 'lenient', c: 'err', t: 'Too lenient' }, { v: 'harsh', c: 'warn', t: 'Too harsh' }] },
]
const VERDICTS: Record<string, { grade: string; label: string; desc: string; color: string; bg: string }> = {
  excellent:  { grade: 'A+', label: 'Excellent', desc: 'Accurate', color: '#16A34A', bg: 'rgba(22,163,74,0.08)' },
  good:       { grade: 'A',  label: 'Good',      desc: 'Reliable', color: '#2563EB', bg: 'rgba(37,99,235,0.08)' },
  acceptable: { grade: 'B',  label: 'Acceptable', desc: 'Adequate', color: '#7C3AED', bg: 'rgba(124,58,237,0.08)' },
  poor:       { grade: 'C',  label: 'Poor',      desc: 'Errors',   color: '#D97706', bg: 'rgba(217,119,6,0.08)' },
  unusable:   { grade: 'F',  label: 'Unusable',  desc: 'Unreliable', color: '#DC2626', bg: 'rgba(220,38,38,0.08)' },
}

type RevState = {
  score: number | null
  dims: string[]
  flags: Set<string>
  beh: Record<string, string>
  verdict: string
  feedback: string
  status: 'pending' | 'saved' | 'skipped'
}

function newRevState(): RevState {
  return { score: null, dims: Array(4).fill(''), flags: new Set(), beh: { reas: '', feed: '', cal: '' }, verdict: '', feedback: '', status: 'pending' }
}

function scColor(v: number): string {
  return v >= 8 ? '#16A34A' : v >= 6 ? '#2563EB' : v >= 4 ? '#D97706' : '#DC2626'
}

export default function ExpertReviewTab() {
  const qc = useQueryClient()
  const [selectedSession, setSelectedSession] = useState('')
  const [curIdx, setCurIdx] = useState(0)
  const [revStates, setRevStates] = useState<Record<number, RevState>>({})
  const [secOpen, setSecOpen] = useState({ A: true, B: true, C: true })

  // Review queue
  const { data: queue, isLoading: queueLoading } = useQuery({
    queryKey: ['review-queue'],
    queryFn: () => reviewerApi.queue({ status: 'pending', limit: 30 }).then(r => r.data),
    staleTime: 30_000,
  })

  // Session transcript
  const { data: transcript } = useQuery({
    queryKey: ['review-transcript', selectedSession],
    queryFn: () => reviewerApi.transcript(selectedSession).then(r => r.data),
    enabled: !!selectedSession,
  })

  const { data: existingReviews } = useQuery({
    queryKey: ['review-existing', selectedSession],
    queryFn: () => reviewerApi.sessionReviews(selectedSession).then(r => r.data),
    enabled: !!selectedSession,
  })

  const turns = (transcript?.turns || []).filter((t: any) => t.question && t.answer)
  const reviewed = new Set((existingReviews?.reviews || []).map((r: any) => r.turn_number))

  const getState = useCallback((i: number): RevState => {
    if (!revStates[i]) {
      const s = newRevState()
      const turn = turns[i]
      if (turn?.avg_score != null) s.score = turn.avg_score
      return s
    }
    return revStates[i]
  }, [revStates, turns])

  const updateState = useCallback((i: number, update: Partial<RevState>) => {
    setRevStates(prev => ({ ...prev, [i]: { ...getState(i), ...update } }))
  }, [getState])

  const checkBDone = (r: RevState) => r.score !== null && r.dims.every(v => v !== '') && r.beh.reas && r.beh.feed && r.beh.cal

  const submit = useMutation({
    mutationFn: () => {
      const r = getState(curIdx)
      const turn = turns[curIdx]
      return reviewerApi.submitReview({
        session_id: selectedSession,
        question_turn: turn?.turn_number ?? curIdx,
        ai_score: turn?.avg_score ?? 5,
        human_score: r.score ?? turn?.avg_score ?? 5,
        dimension_assessments: DIMS.map((d, i) => ({ dimension: d, assessment: r.dims[i] || 'not_set' })),
        error_flags: [...r.flags],
        concept_corrections: [],
        behavior_ratings: { reasoning_quality: r.beh.reas, feedback_quality: r.beh.feed, calibration: r.beh.cal },
        verdict: r.verdict,
        overall_feedback: r.feedback,
      })
    },
    onSuccess: () => {
      updateState(curIdx, { status: 'saved' })
      toast.success('Review saved')
      qc.invalidateQueries({ queryKey: ['review-existing', selectedSession] })
    },
    onError: () => toast.error('Failed to save review'),
  })

  const saveAndNext = async () => { await submit.mutateAsync(); if (curIdx < turns.length - 1) setCurIdx(curIdx + 1) }
  const skip = () => { updateState(curIdx, { status: 'skipped' }); if (curIdx < turns.length - 1) setCurIdx(curIdx + 1) }
  const openSession = (sid: string) => { setSelectedSession(sid); setCurIdx(0); setRevStates({}) }

  if (!selectedSession) {
    // Session list
    return (
      <div>
        <MonoLabel style={{ display: 'block', marginBottom: 14 }}>Completed sessions — select to review</MonoLabel>
        <Card style={{ padding: 0, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ background: 'var(--bg-1)', borderBottom: '1px solid var(--border-0)' }}>
                {['Session', 'Domain', 'Score', 'Turns', 'Status'].map(h => (
                  <th key={h} style={{ padding: '10px 14px', textAlign: 'left' as const, fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase' as const, color: 'var(--text-3)', fontWeight: 400 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {queueLoading ? (
                <tr><td colSpan={5} style={{ padding: 24, textAlign: 'center' as const, color: 'var(--text-3)' }}>Loading...</td></tr>
              ) : !queue?.queue?.length ? (
                <tr><td colSpan={5} style={{ padding: 24, textAlign: 'center' as const, color: 'var(--text-3)' }}>No completed sessions yet.</td></tr>
              ) : queue.queue.map((s: any) => (
                <tr key={s.session_id} onClick={() => openSession(s.session_id)} style={{ cursor: 'pointer', borderBottom: '1px solid var(--border-0)' }}>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>{s.session_id?.slice(0, 8)}</td>
                  <td style={{ padding: '10px 14px' }}>
                    <span style={{ padding: '2px 8px', borderRadius: 4, background: 'rgba(37,99,235,0.08)', color: '#2563EB', fontSize: 10, fontWeight: 500 }}>
                      {s.domain?.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontWeight: 500, color: scColor(s.overall_score || 5) }}>{s.overall_score?.toFixed(1) ?? '—'}</td>
                  <td style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', color: 'var(--text-2)' }}>{s.total_turns}</td>
                  <td style={{ padding: '10px 14px' }}><Badge variant="orange">Pending</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    )
  }

  const r = getState(curIdx)
  const h = turns[curIdx] || {}
  const aiScore = h.avg_score ?? 5
  const myScore = r.score ?? aiScore
  const delta = r.score !== null ? r.score - aiScore : 0
  const bDone = checkBDone(r)
  const cDone = !!r.verdict
  const doneCount = Object.values(revStates).filter(s => s?.status === 'saved').length

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 130px)', overflow: 'hidden' }}>
      {/* LEFT: Question queue */}
      <div style={{ width: 200, flexShrink: 0, borderRight: '1px solid var(--border-0)', display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--bg-1)' }}>
        <div style={{ padding: '13px 13px 9px', borderBottom: '1px solid var(--border-0)', flexShrink: 0 }}>
          <button onClick={() => setSelectedSession('')} style={{ background: 'none', border: 'none', fontSize: 11, color: 'var(--accent)', cursor: 'pointer', padding: 0, marginBottom: 8, fontFamily: 'inherit' }}>← Back to list</button>
          <div style={{ fontSize: 10, fontWeight: 500, textTransform: 'uppercase' as const, letterSpacing: '0.7px', color: 'var(--text-3)', marginBottom: 7 }}>Review queue</div>
          <div style={{ height: 3, background: 'var(--border-0)', borderRadius: 2, overflow: 'hidden', marginBottom: 4 }}>
            <div style={{ height: '100%', background: 'var(--accent)', borderRadius: 2, transition: 'width 0.3s', width: `${(doneCount / Math.max(turns.length, 1)) * 100}%` }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-3)' }}>
            <span>{doneCount} reviewed</span>
            <span>{turns.length} questions</span>
          </div>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {turns.map((t: any, i: number) => {
            const s = revStates[i]
            const saved = s?.status === 'saved'
            const dotColor = saved ? 'var(--green, #22c55e)' : i === curIdx ? 'var(--accent)' : 'var(--border-2)'
            return (
              <div key={i} onClick={() => setCurIdx(i)} style={{
                padding: '10px 12px', borderBottom: '1px solid var(--border-0)', cursor: 'pointer',
                background: i === curIdx ? 'var(--bg-0)' : 'transparent',
                borderLeft: i === curIdx ? '2px solid var(--accent)' : '2px solid transparent',
                opacity: saved ? 0.6 : 1,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 3 }}>
                  <span style={{ fontSize: 11, fontWeight: 500 }}>T{t.turn_number}</span>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: dotColor }} />
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-3)' }}>{t.mode || '—'}</div>
                <div style={{ fontSize: 10, color: 'var(--text-4)', marginTop: 2 }}>AI: {t.avg_score?.toFixed(1) || '—'}/10</div>
              </div>
            )
          })}
        </div>
      </div>

      {/* CENTER: Review form */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ flex: 1, overflowY: 'auto', background: 'var(--bg-0)', padding: '18px 22px 40px' }}>
          {!turns.length ? <EmptyState title="No turns" body="No technical turns to review." /> : (
            <div style={{ maxWidth: 680 }}>
              {/* Nav pips */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14, gap: 10 }}>
                <button onClick={() => setCurIdx(Math.max(0, curIdx - 1))} disabled={curIdx === 0} style={navBtn}>← Prev</button>
                <div style={{ display: 'flex', gap: 4, flex: 1, justifyContent: 'center' }}>
                  {turns.map((_: any, i: number) => (
                    <div key={i} style={{ height: 4, borderRadius: 2, flex: 1, maxWidth: 28, background: revStates[i]?.status === 'saved' ? 'var(--green, #22c55e)' : i === curIdx ? 'var(--border-2)' : 'var(--border-0)' }} />
                  ))}
                </div>
                <button onClick={() => setCurIdx(Math.min(turns.length - 1, curIdx + 1))} disabled={curIdx >= turns.length - 1} style={navBtn}>Next →</button>
              </div>

              {/* SECTION A: AI Evaluation Summary (read-only) */}
              <Section id="A" title="AI evaluation summary" status="Read only" done={true}
                open={secOpen.A} onToggle={() => setSecOpen(s => ({ ...s, A: !s.A }))}>
                <div style={{ padding: 4, background: 'var(--bg-1)', borderRadius: 8, marginBottom: 10, fontSize: 11, color: 'var(--text-3)', display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                  <span style={{ flexShrink: 0, marginTop: 1 }}>ℹ</span>
                  <span>You are evaluating the AI — not the candidate. Your corrections become training data.</span>
                </div>
                <QABox label={`Question · ${h.mode || ''}`} text={h.question || '—'} bold />
                <QABox label="Candidate answer" text={h.answer || '[no answer]'} />
                <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', marginTop: 10 }}>
                  <div style={{ textAlign: 'center' as const, padding: '9px 12px', background: 'var(--bg-1)', border: '1px solid var(--border-0)', borderRadius: 8, flexShrink: 0 }}>
                    <div style={{ fontSize: 26, fontWeight: 400, letterSpacing: -1.5, fontFamily: 'var(--font-mono)', color: scColor(aiScore) }}>{aiScore.toFixed(1)}</div>
                    <div style={{ fontSize: 9, color: 'var(--text-3)' }}>/10</div>
                  </div>
                  <div style={{ flex: 1, fontSize: 11, color: 'var(--text-2)', lineHeight: 1.6 }}>
                    {h.eval_scores && (
                      <div style={{ padding: '6px 8px', background: 'var(--bg-1)', borderRadius: 6, marginBottom: 6 }}>
                        {Object.entries(h.eval_scores).map(([k, v]: [string, any]) => (
                          <span key={k} style={{ marginRight: 10 }}><strong>{k}:</strong> {v}</span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </Section>

              {/* SECTION B: Expert Correction (required) */}
              <Section id="B" title="Expert correction" status={bDone ? '✓ Done' : 'Required'}
                done={bDone} statusColor={bDone ? '#16A34A' : '#DC2626'}
                open={secOpen.B} onToggle={() => setSecOpen(s => ({ ...s, B: !s.B }))}>
                {/* Score slider */}
                <SubSec label="Your score">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '10px 12px', background: 'var(--bg-1)', border: '1px solid var(--border-0)', borderRadius: 8 }}>
                    <span style={{ fontSize: 11, color: 'var(--text-3)', flexShrink: 0 }}>AI: {aiScore.toFixed(1)}</span>
                    <div style={{ flex: 1, padding: '7px 0' }}>
                      <input type="range" min={0} max={10} step={0.5} value={myScore}
                        onChange={e => updateState(curIdx, { score: Number(e.target.value) })}
                        style={{ width: '100%', accentColor: scColor(myScore) }} />
                    </div>
                    <span style={{ fontSize: 18, fontWeight: 500, letterSpacing: -1, fontFamily: 'var(--font-mono)', minWidth: 36, textAlign: 'center' as const, color: scColor(myScore) }}>{myScore.toFixed(1)}</span>
                    {r.score !== null && delta !== 0 && (
                      <span style={{ fontSize: 11, fontWeight: 500, padding: '3px 8px', borderRadius: 5, fontFamily: 'var(--font-mono)', background: delta < 0 ? 'rgba(220,38,38,0.08)' : 'rgba(22,163,74,0.08)', color: delta < 0 ? '#DC2626' : '#16A34A' }}>
                        {delta > 0 ? '+' : ''}{delta.toFixed(1)}
                      </span>
                    )}
                  </div>
                </SubSec>

                {/* Dimensions */}
                <SubSec label="Dimension assessment">
                  {DIMS.map((dim, i) => (
                    <div key={dim} style={{ border: '1px solid var(--border-0)', borderRadius: 7, padding: 9, marginBottom: 6 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 12, fontWeight: 500, minWidth: 140 }}>{dim}</span>
                        <div style={{ display: 'flex', border: '1px solid var(--border-2)', borderRadius: 5, overflow: 'hidden' }}>
                          {[{ v: 'ok', l: '✓ Correct', c: 'ok' }, { v: 'hi', l: 'Too high', c: 'warn' }, { v: 'lo', l: 'Too low', c: 'err' }].map(opt => (
                            <button key={opt.v} onClick={() => {
                              const d = [...r.dims]; d[i] = opt.v; updateState(curIdx, { dims: d })
                            }} style={{
                              padding: '5px 12px', fontSize: 11, fontWeight: 500, border: 'none', cursor: 'pointer',
                              borderRight: '1px solid var(--border-2)', fontFamily: 'inherit',
                              background: r.dims[i] === opt.v
                                ? opt.c === 'ok' ? 'rgba(22,163,74,0.1)' : opt.c === 'warn' ? 'rgba(217,119,6,0.1)' : 'rgba(220,38,38,0.1)'
                                : 'var(--bg-0)',
                              color: r.dims[i] === opt.v
                                ? opt.c === 'ok' ? '#16A34A' : opt.c === 'warn' ? '#D97706' : '#DC2626'
                                : 'var(--text-3)',
                            }}>{opt.l}</button>
                          ))}
                        </div>
                      </div>
                    </div>
                  ))}
                </SubSec>

                {/* Error flags */}
                <SubSec label="Error flags">
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginBottom: 4 }}>
                    {FLAGS.map(f => {
                      const on = r.flags.has(f.id)
                      return (
                        <div key={f.id} onClick={() => {
                          const next = new Set(r.flags); next.has(f.id) ? next.delete(f.id) : next.add(f.id)
                          updateState(curIdx, { flags: next })
                        }} style={{
                          padding: '6px 11px', border: `1px solid ${on ? (f.w ? '#D97706' : '#DC2626') : 'var(--border-2)'}`,
                          borderRadius: 20, fontSize: 11, fontWeight: 500, cursor: 'pointer',
                          background: on ? (f.w ? 'rgba(217,119,6,0.08)' : 'rgba(220,38,38,0.08)') : 'var(--bg-0)',
                          color: on ? (f.w ? '#D97706' : '#DC2626') : 'var(--text-2)',
                          display: 'flex', alignItems: 'center', gap: 5, userSelect: 'none' as const,
                        }}>
                          <span style={{ width: 6, height: 6, borderRadius: '50%', background: on ? (f.w ? '#D97706' : '#DC2626') : 'var(--border-2)' }} />
                          {f.l}
                        </div>
                      )
                    })}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text-4)', marginTop: 4 }}>No flags = AI evaluation was accurate</div>
                </SubSec>

                {/* AI behavior */}
                <SubSec label="AI behavior" noBorder>
                  {BEH.map(row => (
                    <div key={row.k} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                      <span style={{ fontSize: 12, color: 'var(--text-2)', width: 95, flexShrink: 0 }}>{row.l}</span>
                      <div style={{ display: 'flex', border: '1px solid var(--border-2)', borderRadius: 6, overflow: 'hidden' }}>
                        {row.o.map(o => (
                          <button key={o.v} onClick={() => updateState(curIdx, { beh: { ...r.beh, [row.k]: o.v } })}
                            style={{
                              padding: '5px 12px', fontSize: 11, fontWeight: 500, border: 'none', cursor: 'pointer',
                              borderRight: '1px solid var(--border-2)', fontFamily: 'inherit',
                              background: r.beh[row.k] === o.v
                                ? o.c === 'ok' ? 'rgba(22,163,74,0.1)' : o.c === 'warn' ? 'rgba(217,119,6,0.1)' : 'rgba(220,38,38,0.1)'
                                : 'var(--bg-0)',
                              color: r.beh[row.k] === o.v
                                ? o.c === 'ok' ? '#16A34A' : o.c === 'warn' ? '#D97706' : '#DC2626'
                                : 'var(--text-3)',
                            }}>{o.t}</button>
                        ))}
                      </div>
                    </div>
                  ))}
                </SubSec>
              </Section>

              {/* SECTION C: Overall Verdict (required) */}
              <Section id="C" title="Overall AI quality verdict" status={cDone ? '✓ Done' : 'Required'}
                done={cDone} statusColor={cDone ? '#16A34A' : '#DC2626'}
                open={secOpen.C} onToggle={() => setSecOpen(s => ({ ...s, C: !s.C }))}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 12 }}>One tap — how well did the AI evaluate this answer overall?</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: 7 }}>
                  {Object.entries(VERDICTS).map(([k, v]) => (
                    <div key={k} onClick={() => updateState(curIdx, { verdict: k })} style={{
                      padding: '10px 6px', border: `1px solid ${r.verdict === k ? v.color : 'var(--border-0)'}`,
                      borderRadius: 9, cursor: 'pointer', textAlign: 'center' as const, fontSize: 12, fontWeight: 500,
                      background: r.verdict === k ? v.bg : 'var(--bg-0)', color: r.verdict === k ? v.color : 'var(--text-2)',
                      transition: 'all 0.12s', userSelect: 'none' as const,
                    }}>
                      <div style={{
                        width: 26, height: 26, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                        margin: '0 auto 6px', fontSize: 12, fontWeight: 600,
                        border: `2px solid ${r.verdict === k ? v.color : 'var(--border-2)'}`,
                        background: r.verdict === k ? v.bg : 'var(--bg-1)',
                      }}>{v.grade}</div>
                      {v.label}
                      <div style={{ fontSize: 9, fontWeight: 400, opacity: 0.65, marginTop: 3, lineHeight: 1.3 }}>{v.desc}</div>
                    </div>
                  ))}
                </div>
                <div style={{ marginTop: 13, paddingTop: 13, borderTop: '1px solid var(--border-0)' }}>
                  <div style={{ fontSize: 10, fontWeight: 500, textTransform: 'uppercase' as const, letterSpacing: '0.7px', color: 'var(--text-3)', marginBottom: 7 }}>
                    Overall feedback <span style={{ fontWeight: 400, textTransform: 'none' as const, letterSpacing: 0, fontSize: 11, color: 'var(--text-4)' }}>— optional</span>
                  </div>
                  <textarea value={r.feedback} onChange={e => updateState(curIdx, { feedback: e.target.value })}
                    placeholder="Any overall observations about this AI evaluation…"
                    style={{ width: '100%', minHeight: 70, resize: 'vertical', padding: '9px 11px', border: '1px solid var(--border-2)', borderRadius: 8, fontSize: 12, color: 'var(--text-0)', fontFamily: 'inherit', outline: 'none', lineHeight: 1.55, background: 'var(--bg-0)' }} />
                </div>
              </Section>
            </div>
          )}
        </div>

        {/* Action bar */}
        <div style={{ borderTop: '1px solid var(--border-0)', padding: '11px 22px', display: 'flex', alignItems: 'center', gap: 8, background: 'var(--bg-0)', flexShrink: 0 }}>
          <div style={{ flex: 1, fontSize: 11, color: bDone && cDone ? 'var(--green, #22c55e)' : 'var(--text-3)' }}>
            {bDone && cDone ? 'Ready to save' : `Complete section${!bDone ? ' B' : ''}${!bDone && !cDone ? ' and' : ''}${!cDone ? ' C' : ''} to save`}
          </div>
          <button onClick={skip} style={skipBtn}>Skip</button>
          <Button variant="secondary" size="sm" onClick={() => submit.mutate()} loading={submit.isPending} disabled={!bDone || !cDone}>Save</Button>
          <Button variant="primary" size="sm" onClick={saveAndNext} loading={submit.isPending} disabled={!bDone || !cDone}>Save & next →</Button>
        </div>
      </div>
    </div>
  )
}

// ── Sub-components ──────────────────────────────────────────────────────────

function Section({ id, title, status, done, statusColor, open, onToggle, children }: {
  id: string; title: string; status: string; done: boolean; statusColor?: string; open: boolean; onToggle: () => void; children: React.ReactNode
}) {
  return (
    <div style={{ border: `1px solid ${done ? 'rgba(22,163,74,0.3)' : 'var(--border-0)'}`, borderRadius: 12, marginBottom: 11, overflow: 'hidden', transition: 'border-color 0.15s' }}>
      <div onClick={onToggle} style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '12px 15px',
        background: 'var(--bg-1)', cursor: 'pointer', userSelect: 'none' as const,
      }}>
        <div style={{
          width: 22, height: 22, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 10, fontWeight: 500, flexShrink: 0,
          border: `1.5px solid ${done ? '#16A34A' : 'var(--border-2)'}`,
          background: done ? '#16A34A' : 'transparent', color: done ? '#fff' : 'var(--text-2)',
        }}>{id}</div>
        <div style={{ fontSize: 13, fontWeight: 500, flex: 1 }}>{title}</div>
        <span style={{ fontSize: 10, fontWeight: 500, color: statusColor || 'var(--text-3)' }}>{status}</span>
        <span style={{ fontSize: 10, color: 'var(--text-3)' }}>{open ? '▲' : '▼'}</span>
      </div>
      {open && <div style={{ padding: 16, background: 'var(--bg-0)' }}>{children}</div>}
    </div>
  )
}

function SubSec({ label, children, noBorder }: { label: string; children: React.ReactNode; noBorder?: boolean }) {
  return (
    <div style={{ marginBottom: noBorder ? 0 : 13, paddingBottom: noBorder ? 0 : 13, borderBottom: noBorder ? 'none' : '1px solid var(--border-0)' }}>
      <div style={{ fontSize: 10, fontWeight: 500, textTransform: 'uppercase' as const, letterSpacing: '0.7px', color: 'var(--text-3)', marginBottom: 9 }}>{label}</div>
      {children}
    </div>
  )
}

function QABox({ label, text, bold }: { label: string; text: string; bold?: boolean }) {
  return (
    <div style={{ background: 'var(--bg-1)', borderRadius: 8, padding: '10px 12px', marginBottom: 8 }}>
      <div style={{ fontSize: 9, fontWeight: 500, textTransform: 'uppercase' as const, letterSpacing: '0.5px', color: 'var(--text-3)', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 12, lineHeight: 1.6, fontWeight: bold ? 500 : 400, color: bold ? 'var(--text-0)' : 'var(--text-2)' }}>{text}</div>
    </div>
  )
}

const navBtn: React.CSSProperties = {
  padding: '5px 12px', border: '1px solid var(--border-2)', borderRadius: 6,
  background: 'var(--bg-0)', fontSize: 11, cursor: 'pointer', fontFamily: 'inherit',
  color: 'var(--text-2)',
}

const skipBtn: React.CSSProperties = {
  padding: '5px 12px', border: '1px solid var(--border-2)', borderRadius: 6,
  background: 'var(--bg-0)', fontSize: 11, cursor: 'pointer', fontFamily: 'inherit',
  color: 'var(--text-3)',
}
